#!/usr/bin/env python3
"""
hyperliquid_pacifica_hedge.py
-----------------------------
Automated delta-neutral funding rate arbitrage bot for Hyperliquid and Pacifica.

This bot continuously:
1. Analyzes funding rates across multiple symbols on Hyperliquid and Pacifica.
2. Opens a delta-neutral position (long on one, short on the other) to capture the funding rate spread.
3. Holds the position for a configurable duration (e.g., 8 hours) to collect funding payments.
4. Closes the position.
5. Waits for a brief period and repeats the cycle.

Features:
- Persistent state management to survive restarts.
- Automatic recovery from crashes by checking on-chain positions.
- PnL tracking and health monitoring during the holding period.
- Graceful shutdown handling.
"""

import asyncio
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, UTC
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict

from dotenv import load_dotenv

# Import exchange connectors
from hyperliquid_connector import HyperliquidConnector
from pacifica_client import PacificaClient

# ANSI color codes for console output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    GRAY = '\033[90m'

class BalanceFetchError(Exception):
    """Raised when balance retrieval fails."""
    pass

# ==================== Logging Setup ====================

os.makedirs('logs', exist_ok=True)
file_handler = logging.FileHandler('logs/hyperliquid_pacifica_hedge.log', mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))

# Use UTF-8 encoding for console output to support emojis
import io
console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
console_handler = logging.StreamHandler(console_stream)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler], force=True)
logger = logging.getLogger(__name__)

# Silence noisy third-party loggers
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

# ==================== State Management ====================

class BotState:
    """State machine for the hedge bot."""
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    OPENING = "OPENING"
    HOLDING = "HOLDING"
    CLOSING = "CLOSING"
    WAITING = "WAITING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"

@dataclass
class BotConfig:
    """Bot configuration parameters."""
    symbols_to_monitor: List[str]
    leverage: int = 3
    base_capital_allocation: float = 100.0
    hold_duration_hours: float = 8.0
    wait_between_cycles_minutes: float = 5.0
    check_interval_seconds: int = 60
    min_net_apr_threshold: float = 5.0

    @staticmethod
    def load_from_file(config_file: str) -> 'BotConfig':
        """Load configuration from JSON file."""
        try:
            with open(config_file, 'r') as f:
                data = json.load(f)
            
            # Filter out comment keys before initializing
            data = {k: v for k, v in data.items() if not k.startswith('comment_')}

            # Provide defaults for any missing fields
            defaults = {
                'symbols_to_monitor': ["BTC", "ETH", "SOL"],
                'leverage': 3,
                'base_capital_allocation': 100.0,
                'hold_duration_hours': 8.0,
                'wait_between_cycles_minutes': 5.0,
                'check_interval_seconds': 60,
                'min_net_apr_threshold': 5.0,
            }
            # Remove old keys if they exist
            data.pop('stop_loss_percent', None)
            data.pop('enable_stop_loss', None)
            # Handle rename of notional_per_position to base_capital_allocation
            if 'notional_per_position' in data:
                data['base_capital_allocation'] = data.pop('notional_per_position')
            for key, default_value in defaults.items():
                if key not in data:
                    data[key] = default_value
            return BotConfig(**data)
        except FileNotFoundError:
            logger.warning(f"Config file {config_file} not found, using defaults.")
            return BotConfig(symbols_to_monitor=["BTC", "ETH", "SOL"])
        except Exception as e:
            logger.error(f"Error loading config: {e}, using defaults.")
            return BotConfig(symbols_to_monitor=["BTC", "ETH", "SOL"])

class StateManager:
    """Manages bot state persistence and recovery."""
    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self.state = {
            "version": "1.0",
            "state": BotState.IDLE,
            "current_cycle_number": 0,
            "current_position": None,
            "completed_cycles": [],
            "cumulative_stats": {
                "total_cycles": 0,
                "successful_cycles": 0,
                "failed_cycles": 0,
                "total_realized_pnl": 0.0,
                "last_error": None,
                "last_error_at": None
            },
            "initial_capital": None,
            "last_updated": datetime.now(UTC).isoformat()
        }

    def load(self) -> bool:
        if not os.path.exists(self.state_file):
            logger.info(f"No state file found at {self.state_file}, starting fresh.")
            return False
        try:
            with open(self.state_file, 'r') as f:
                loaded_state = json.load(f)
            self.state.update(loaded_state)
            # Ensure backward compatibility for cycle number
            if "current_cycle_number" not in self.state:
                self.state["current_cycle_number"] = 0
            logger.info(f"Loaded state from {self.state_file}. Current state: {self.state['state']}")
            return True
        except Exception as e:
            logger.warning(f"Could not load state file: {e}. Starting fresh.")
            return False

    def save(self):
        self.state["last_updated"] = datetime.now(UTC).isoformat()
        try:
            temp_file = self.state_file + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(self.state, f, indent=2)
            os.replace(temp_file, self.state_file)
            logger.debug(f"Saved state to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def set_state(self, new_state: str):
        logger.info(f"State transition: {self.state['state']} -> {new_state}")
        self.state["state"] = new_state
        self.save()

    def get_state(self) -> str:
        return self.state["state"]

# ==================== Balance & Position Helpers ====================

def get_hyperliquid_balance(client: HyperliquidConnector) -> Tuple[float, float]:
    """Get Hyperliquid total and available USD balance."""
    try:
        total, available = client.get_balance()
        return total, available
    except Exception as e:
        logger.error(f"Error fetching Hyperliquid balance: {e}", exc_info=True)
        raise BalanceFetchError(f"Hyperliquid balance fetch failed: {e}") from e

def get_pacifica_balance(client: PacificaClient) -> Tuple[float, float]:
    """Get Pacifica total and available USD balance."""
    try:
        total = client.get_equity()
        available = client.get_available_balance()
        return total, available
    except Exception as e:
        logger.error(f"Error fetching Pacifica balance: {e}", exc_info=True)
        raise BalanceFetchError(f"Pacifica balance fetch failed: {e}") from e

async def fetch_funding_rates(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, symbols: List[str]) -> List[Dict]:
    """Fetch and compare funding rates for a list of symbols."""
    try:
        hl_rates = hl_client.get_funding_rates()
    except Exception as e:
        logger.error(f"Could not fetch Hyperliquid funding rates: {e}")
        hl_rates = {}

    results = []
    for symbol in symbols:
        try:
            pacifica_rate = pacifica_client.get_funding_rate(symbol)
            hl_rate = hl_rates.get(symbol)

            if hl_rate is None:
                logger.info(f"No funding rate for {symbol} on Hyperliquid.")
                continue

            # Rates are hourly percentages, convert to APR
            hl_apr = hl_rate * 24 * 365 * 100
            pacifica_apr = pacifica_rate * 24 * 365 * 100

            # Positive net APR means shorting the higher APR exchange is profitable
            net_apr = abs(hl_apr - pacifica_apr)
            
            long_exch = "Hyperliquid" if hl_apr < pacifica_apr else "Pacifica"
            short_exch = "Pacifica" if long_exch == "Hyperliquid" else "Hyperliquid"

            results.append({
                "symbol": symbol,
                "hl_apr": hl_apr,
                "pacifica_apr": pacifica_apr,
                "net_apr": net_apr,
                "long_exch": long_exch,
                "short_exch": short_exch,
                "available": True
            })
        except Exception as e:
            logger.warning(f"Could not process funding for {symbol}: {e}")

    return results

async def get_position_pnl(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, symbol: str) -> Dict:
    """Get unrealized PnL from both exchanges."""
    pnl_data = {
        "hyperliquid_unrealized_pnl": 0.0,
        "pacifica_unrealized_pnl": 0.0,
        "total_unrealized_pnl": 0.0,
    }
    try:
        hl_pos = hl_client.get_position(symbol)
        if hl_pos:
            pnl_data["hyperliquid_unrealized_pnl"] = hl_pos.get("unrealized_pnl", 0.0)
    except Exception as e:
        logger.error(f"Error fetching Hyperliquid PnL: {e}")

    try:
        pacifica_pos = await pacifica_client.get_position(symbol)
        if pacifica_pos:
            pnl_data["pacifica_unrealized_pnl"] = pacifica_pos.get("unrealized_pnl", 0.0)
    except Exception as e:
        logger.error(f"Error fetching Pacifica PnL: {e}")

    pnl_data["total_unrealized_pnl"] = pnl_data["hyperliquid_unrealized_pnl"] + pnl_data["pacifica_unrealized_pnl"]
    return pnl_data

def calculate_dynamic_stop_loss(leverage: int) -> float:
    """
    Calculate dynamic stop-loss percentage based on leverage.
    Higher leverage = tighter stop-loss to protect capital.

    The stop-loss is set to trigger at approximately 50-60% of the capital at risk,
    leaving margin before liquidation (which typically happens at 100% loss of margin).

    Args:
        leverage: The leverage multiplier used for the position

    Returns:
        Stop-loss percentage (e.g., 15.0 means -15% loss triggers stop)

    Examples:
        1x leverage: -50% (lose 50% of capital)
        3x leverage: -25% (lose 75% of capital)
        5x leverage: -12% (lose 60% of capital)
        10x leverage: -6% (lose 60% of capital)
        20x leverage: -3% (lose 60% of capital)
    """
    if leverage <= 1:
        return 50.0  # -50% at 1x leverage
    elif leverage == 2:
        return 30.0  # -30% at 2x (lose 60% of capital)
    elif leverage == 3:
        return 20.0  # -20% at 3x (lose 60% of capital)
    elif leverage == 4:
        return 15.0  # -15% at 4x (lose 60% of capital)
    elif leverage == 5:
        return 12.0  # -12% at 5x (lose 60% of capital)
    else:
        # For leverage > 5: Target ~60% capital loss before stop
        # Formula: 60% / leverage, with minimum of 2%
        return max(2.0, 60.0 / leverage)

def check_stop_loss(pnl_data: Dict, notional: float, stop_loss_percent: float) -> Tuple[bool, str]:
    """
    Check if stop-loss should be triggered based on unrealized PnL.

    Args:
        pnl_data: Dictionary containing unrealized PnL data
        notional: The notional size of the position in USD
        stop_loss_percent: The stop-loss threshold percentage

    Returns:
        Tuple of (triggered: bool, reason: str)
    """
    total_unrealized_pnl = pnl_data.get("total_unrealized_pnl", 0.0)

    # Calculate loss percentage relative to notional
    if notional <= 0:
        return False, ""

    loss_percent = (total_unrealized_pnl / notional) * 100

    # Trigger if loss exceeds threshold (negative percentage)
    if loss_percent <= -stop_loss_percent:
        return True, f"Loss of {loss_percent:.2f}% exceeds stop-loss threshold of -{stop_loss_percent:.2f}%"

    return False, ""

# ==================== State Recovery ====================

async def scan_symbols_for_positions(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, symbols: List[str]) -> List[dict]:
    """Scan multiple symbols for open positions on both exchanges."""
    positions_found = []

    logger.info(f"Scanning {len(symbols)} symbols for existing positions: {', '.join(symbols)}")

    for i, symbol in enumerate(symbols, 1):
        try:
            logger.debug(f"[{i}/{len(symbols)}] Checking {symbol}...")
            hl_pos = hl_client.get_position(symbol)
            pacifica_pos = await pacifica_client.get_position(symbol)

            hl_size = hl_pos['qty'] if hl_pos else 0.0
            pacifica_size = pacifica_pos['qty'] if pacifica_pos else 0.0

            if hl_size != 0 or pacifica_size != 0:
                logger.info(f"[FOUND] Position on {symbol}: HL={hl_size}, Pacifica={pacifica_size}")
                positions_found.append({
                    "symbol": symbol,
                    "hyperliquid_size": hl_size,
                    "pacifica_size": pacifica_size,
                })
            else:
                logger.debug(f"[{i}/{len(symbols)}] {symbol}: No positions")
        except Exception as e:
            logger.debug(f"[{i}/{len(symbols)}] Could not check {symbol}: {e}")

    if positions_found:
        logger.info(f"{Colors.GREEN}Position scan complete: {len(positions_found)} position(s) found{Colors.RESET}")
    else:
        logger.info(f"Position scan complete: No existing positions found")
    return positions_found

async def recover_state(state_mgr: StateManager, hl_client: HyperliquidConnector, pacifica_client: PacificaClient, config: BotConfig) -> bool:
    """Recover bot state by checking actual positions on exchanges."""
    logger.info(f"{Colors.YELLOW}Performing state recovery...{Colors.RESET}")
    state = state_mgr.get_state()

    if state in [BotState.OPENING, BotState.CLOSING]:
        logger.error(f"{Colors.RED}Bot was last in {state} state. Manual intervention required.{Colors.RESET}")
        return False

    try:
        positions_found = await scan_symbols_for_positions(hl_client, pacifica_client, config.symbols_to_monitor)

        if not positions_found:
            logger.info(f"{Colors.GREEN}No existing positions found. Resetting to IDLE.{Colors.RESET}")
            state_mgr.state["current_position"] = None
            state_mgr.set_state(BotState.IDLE)
            return True

        if len(positions_found) > 1:
            logger.error(f"{Colors.RED}Multiple positions found on {len(positions_found)} symbols! Manual cleanup required.{Colors.RESET}")
            state_mgr.set_state(BotState.ERROR)
            return False

        # Single position found, attempt recovery
        pos_info = positions_found[0]
        symbol = pos_info["symbol"]
        hl_size = pos_info["hyperliquid_size"]
        pacifica_size = pos_info["pacifica_size"]

        # Check if it's an orphan leg (only one exchange has a position)
        is_orphan_hl = (hl_size != 0 and pacifica_size == 0)
        is_orphan_pacifica = (pacifica_size != 0 and hl_size == 0)

        if is_orphan_hl or is_orphan_pacifica:
            orphan_exchange = "Hyperliquid" if is_orphan_hl else "Pacifica"
            orphan_size = hl_size if is_orphan_hl else pacifica_size
            logger.warning(f"{Colors.YELLOW}⚠️ Detected orphan leg on {orphan_exchange} for {symbol}: {orphan_size:+.4f}{Colors.RESET}")
            logger.info(f"{Colors.CYAN}🔧 Attempting to close orphan position...{Colors.RESET}")

            try:
                # Close the orphan leg
                if is_orphan_hl:
                    logger.info(f"Closing orphan Hyperliquid position for {symbol}...")
                    hl_result = hl_client.market_close(symbol)
                    if hl_result is None:
                        raise RuntimeError("Failed to close orphan Hyperliquid position")
                    logger.info(f"{Colors.GREEN}✅ Orphan Hyperliquid position closed{Colors.RESET}")
                else:  # Orphan on Pacifica
                    close_qty = abs(pacifica_size)
                    close_side = 'sell' if pacifica_size > 0 else 'buy'
                    logger.info(f"Closing orphan Pacifica position for {symbol}: {close_qty:.4f} {close_side}...")
                    pacifica_result = pacifica_client.place_market_order(symbol, side=close_side, quantity=close_qty, reduce_only=True)
                    if pacifica_result is None:
                        raise RuntimeError("Failed to close orphan Pacifica position")
                    logger.info(f"{Colors.GREEN}✅ Orphan Pacifica position closed{Colors.RESET}")

                # Reset state to IDLE after successful cleanup
                logger.info(f"{Colors.GREEN}✅ Orphan leg cleaned up successfully. Resetting to IDLE.{Colors.RESET}")
                state_mgr.state["current_position"] = None
                state_mgr.set_state(BotState.IDLE)
                return True

            except Exception as e:
                logger.error(f"{Colors.RED}Failed to close orphan leg: {e}. Manual intervention required.{Colors.RESET}")
                state_mgr.set_state(BotState.ERROR)
                return False

        # Check if it's a valid delta-neutral position (approximately opposite and equal)
        if abs(hl_size + pacifica_size) > (abs(max(abs(hl_size), abs(pacifica_size))) * 0.05): # Allow 5% imbalance
            logger.error(f"{Colors.RED}Position sizes for {symbol} are not delta-neutral! HL: {hl_size}, Pacifica: {pacifica_size}. Manual cleanup required.{Colors.RESET}")
            state_mgr.set_state(BotState.ERROR)
            return False
        
        logger.info(f"{Colors.GREEN}Detected delta-neutral position on {symbol}. Recovering state...{Colors.RESET}")
        
        long_exchange = "Hyperliquid" if hl_size > 0 else "Pacifica"
        short_exchange = "Pacifica" if long_exchange == "Hyperliquid" else "Hyperliquid"
        
        # If state was already HOLDING, keep original open time. Otherwise, set to now.
        opened_at_str = (state_mgr.state.get("current_position") or {}).get("opened_at")
        if state == BotState.HOLDING and opened_at_str:
            # Ensure timezone-aware datetime
            opened_at = datetime.fromisoformat(opened_at_str.replace('Z', '+00:00'))
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=UTC)
        else:
            opened_at = datetime.now(UTC)

        target_close_at = opened_at + timedelta(hours=config.hold_duration_hours)

        # Estimate notional value with error handling
        mid_price_str = hl_client.get_mid_price(symbol)
        if mid_price_str:
            estimated_notional = abs(hl_size) * float(mid_price_str)
        else:
            logger.warning(f"Could not get mid price for {symbol}, estimating notional as 100")
            estimated_notional = 100.0

        # Get actual leverage
        actual_leverage = hl_client.get_leverage(symbol)

        state_mgr.state["current_position"] = {
            "symbol": symbol,
            "opened_at": opened_at.isoformat(),
            "target_close_at": target_close_at.isoformat(),
            "long_exchange": long_exchange,
            "short_exchange": short_exchange,
            "notional": estimated_notional,
            "leverage": actual_leverage
        }
        state_mgr.set_state(BotState.HOLDING)
        logger.info(f"{Colors.GREEN}Position recovered successfully. Now in HOLDING state.{Colors.RESET}")
        return True

    except Exception as e:
        logger.error(f"Error during state recovery: {e}", exc_info=True)
        state_mgr.set_state(BotState.ERROR)
        return False

# ==================== Main Bot Logic ====================

class RotationBot:
    def __init__(self, state_file: str, config_file: str):
        self.state_mgr = StateManager(state_file)
        self.config_file = config_file  # Store config file path for reloading
        self.config = BotConfig.load_from_file(config_file)
        self.shutdown_requested = False
        
        load_dotenv()
        self.hl_wallet = os.getenv("HL_WALLET")
        self.hl_private_key = os.getenv("HL_PRIVATE_KEY")
        self.sol_wallet = os.getenv("SOL_WALLET")
        self.api_public = os.getenv("API_PUBLIC")
        self.api_private = os.getenv("API_PRIVATE")

        if not all([self.hl_wallet, self.hl_private_key, self.sol_wallet, self.api_public, self.api_private]):
            logger.error("Missing required environment variables. Please check your .env file.")
            sys.exit(1)

        self.hl_client = HyperliquidConnector(self.hl_wallet, self.hl_private_key)
        self.pacifica_client = PacificaClient(self.sol_wallet, self.api_public, self.api_private)

        self._filter_tradable_symbols()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _filter_tradable_symbols(self):
        """Filters the config's symbols to only include those present on both exchanges."""
        logger.info("Filtering symbols to find those tradable on both exchanges...")
        hl_symbols = set(self.hl_client.coin_to_meta.keys())
        pacifica_symbols = set(self.pacifica_client._market_info.keys())

        common_symbols = hl_symbols.intersection(pacifica_symbols)
        
        original_symbols = self.config.symbols_to_monitor
        filtered_symbols = [s for s in original_symbols if s in common_symbols]

        if len(filtered_symbols) < len(original_symbols):
            removed_symbols = set(original_symbols) - set(filtered_symbols)
            logger.warning(f"Removed symbols not available on both exchanges: {', '.join(removed_symbols)}")

        if not filtered_symbols:
            logger.error("No common symbols found between Hyperliquid and Pacifica from the configured list. The bot cannot proceed.")
            sys.exit(1)

        logger.info(f"Final list of monitored symbols: {', '.join(filtered_symbols)}")
        self.config.symbols_to_monitor = filtered_symbols

    def _signal_handler(self, signum, frame):
        logger.info(f"\n{Colors.YELLOW}🛑 Shutdown signal received. Stopping gracefully...{Colors.RESET}")
        self.shutdown_requested = True

    def reload_config(self):
        """Reload configuration from file and re-filter symbols. Only call when no position is open."""
        try:
            # Safety check: ensure no position is open
            if self.state_mgr.state.get("current_position") is not None:
                logger.warning(f"{Colors.YELLOW}⚠️ Config reload attempted while position is open. Skipping reload for safety.{Colors.RESET}")
                return False

            logger.info(f"{Colors.CYAN}🔄 Reloading configuration from {self.config_file}...{Colors.RESET}")
            old_config = self.config
            new_config = BotConfig.load_from_file(self.config_file)

            # Check if any important parameters changed
            changes = []
            if new_config.leverage != old_config.leverage:
                changes.append(f"leverage: {old_config.leverage}x → {new_config.leverage}x")
            if new_config.base_capital_allocation != old_config.base_capital_allocation:
                changes.append(f"base_capital: ${old_config.base_capital_allocation:.2f} → ${new_config.base_capital_allocation:.2f}")
            if new_config.hold_duration_hours != old_config.hold_duration_hours:
                changes.append(f"hold_duration: {old_config.hold_duration_hours}h → {new_config.hold_duration_hours}h")
            if new_config.min_net_apr_threshold != old_config.min_net_apr_threshold:
                changes.append(f"min_apr_threshold: {old_config.min_net_apr_threshold}% → {new_config.min_net_apr_threshold}%")
            if new_config.wait_between_cycles_minutes != old_config.wait_between_cycles_minutes:
                changes.append(f"wait_time: {old_config.wait_between_cycles_minutes}min → {new_config.wait_between_cycles_minutes}min")
            if new_config.check_interval_seconds != old_config.check_interval_seconds:
                changes.append(f"check_interval: {old_config.check_interval_seconds}s → {new_config.check_interval_seconds}s")
            if set(new_config.symbols_to_monitor) != set(old_config.symbols_to_monitor):
                changes.append(f"symbols: {old_config.symbols_to_monitor} → {new_config.symbols_to_monitor}")

            # Apply new config
            self.config = new_config

            # Re-filter symbols to ensure they're available on both exchanges
            self._filter_tradable_symbols()

            if changes:
                logger.info(f"{Colors.GREEN}✅ Config reloaded successfully. Changes detected:{Colors.RESET}")
                for change in changes:
                    logger.info(f"   • {change}")
            else:
                logger.info(f"{Colors.GREEN}✅ Config reloaded (no changes detected){Colors.RESET}")

            return True
        except Exception as e:
            logger.error(f"{Colors.RED}Failed to reload config: {e}. Keeping existing configuration.{Colors.RESET}")
            return False

    async def _responsive_sleep(self, duration_seconds: int):
        """Sleeps for a duration in 1-second intervals, checking for shutdown."""
        for _ in range(int(duration_seconds)):
            if self.shutdown_requested:
                logger.info("Sleep interrupted by shutdown signal.")
                break
            await asyncio.sleep(1)

    async def run(self):
        logger.info(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")
        logger.info("  🤖 Automated Delta-Neutral Bot for Hyperliquid & Pacifica")
        logger.info(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")

        self.state_mgr.load()

        # Initialize base capital if missing
        if self.state_mgr.state.get("initial_capital") is None:
            try:
                logger.info(f"{Colors.YELLOW}💰 Fetching initial capital for long-term PnL tracking...{Colors.RESET}")
                hl_total, _ = get_hyperliquid_balance(self.hl_client)
                pa_total, _ = get_pacifica_balance(self.pacifica_client)
                initial_capital = hl_total + pa_total
                self.state_mgr.state["initial_capital"] = initial_capital
                self.state_mgr.save()
                logger.info(f"{Colors.GREEN}✅ Initial capital set to: ${initial_capital:.2f}{Colors.RESET}")
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch initial capital: {e}. Will retry later.")
        else:
            logger.info(f"💰 Initial capital: ${self.state_mgr.state['initial_capital']:.2f}")

        if not await recover_state(self.state_mgr, self.hl_client, self.pacifica_client, self.config):
            logger.error(f"{Colors.RED}State recovery failed. Exiting.{Colors.RESET}")
            return

        try:
            while not self.shutdown_requested:
                try:
                    state = self.state_mgr.get_state()
                    if state == BotState.IDLE:
                        await self.start_new_cycle()
                    elif state == BotState.HOLDING:
                        await self.monitor_position()
                    elif state == BotState.WAITING:
                        wait_seconds = self.config.wait_between_cycles_minutes * 60
                        logger.info(f"Waiting for {self.config.wait_between_cycles_minutes} minutes...")
                        await self._responsive_sleep(wait_seconds)
                        if not self.shutdown_requested:
                            self.state_mgr.set_state(BotState.IDLE)
                    elif state == BotState.ERROR:
                        logger.error("Bot is in ERROR state. Waiting 5 minutes before attempting recovery.")
                        await self._responsive_sleep(300)
                        if not self.shutdown_requested:
                            if not await recover_state(self.state_mgr, self.hl_client, self.pacifica_client, self.config):
                                 logger.error(f"{Colors.RED}Recovery failed again. Manual intervention required.{Colors.RESET}")
                                 break
                    else:
                        # Fallback sleep for any unexpected state
                        await self._responsive_sleep(self.config.check_interval_seconds)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception(f"An unexpected error occurred in the main loop: {e}")
                    self.state_mgr.set_state(BotState.ERROR)
                    await self._responsive_sleep(60)
        finally:
            logger.info("Bot shutting down. Performing cleanup...")
            if self.hl_client:
                self.hl_client.close()
            logger.info("Cleanup complete. Goodbye.")

    async def start_new_cycle(self):
        self.state_mgr.set_state(BotState.ANALYZING)

        # Reload config to pick up any changes made while bot was running
        self.reload_config()

        logger.info("🔍 Analyzing funding rates for opportunities...")

        try:
            # Check balances first
            hl_total, hl_avail = get_hyperliquid_balance(self.hl_client)
            pa_total, pa_avail = get_pacifica_balance(self.pacifica_client)
            logger.info(f"Balances | HL: ${hl_total:.2f} (Avail: ${hl_avail:.2f}) | Pacifica: ${pa_total:.2f} (Avail: ${pa_avail:.2f})")

            # Check for minimum balance
            if hl_avail < 20 or pa_avail < 20:
                logger.warning(f"Insufficient balance to start new cycle. HL: ${hl_avail:.2f}, Pacifica: ${pa_avail:.2f}. Required: $20 on each.")
                self.state_mgr.set_state(BotState.WAITING)
                return

            # Find best funding rate opportunity
            opportunities = await fetch_funding_rates(self.hl_client, self.pacifica_client, self.config.symbols_to_monitor)
            if not opportunities:
                logger.warning("No funding rate opportunities found.")
                self.state_mgr.set_state(BotState.WAITING)
                return

            opportunities.sort(key=lambda x: x["net_apr"], reverse=True)
            best_opportunity = opportunities[0]

            if best_opportunity["net_apr"] < self.config.min_net_apr_threshold:
                logger.info(f"Best opportunity ({best_opportunity['symbol']}: {best_opportunity['net_apr']:.2f}%) is below threshold.")
                self.state_mgr.set_state(BotState.WAITING)
                return
            
            logger.info(f"🎯 Found best opportunity: {best_opportunity['symbol']} with {best_opportunity['net_apr']:.2f}% APR.")

            symbol = best_opportunity['symbol']

            # Determine and set the safe, final leverage
            MAX_ALLOWED_LEVERAGE = 20  # Hard cap for safety
            hl_max_leverage = self.hl_client.coin_to_meta[symbol]['maxLeverage']
            pacifica_max_leverage = self.pacifica_client.get_max_leverage(symbol)

            # Calculate target leverage (minimum of config, exchange limits, and hard cap)
            final_leverage = min(self.config.leverage, hl_max_leverage, pacifica_max_leverage, MAX_ALLOWED_LEVERAGE)

            # Log leverage information
            if final_leverage < self.config.leverage:
                reasons = []
                if self.config.leverage > MAX_ALLOWED_LEVERAGE:
                    reasons.append(f"hard cap: {MAX_ALLOWED_LEVERAGE}x")
                if self.config.leverage > hl_max_leverage:
                    reasons.append(f"HL max: {hl_max_leverage}x")
                if self.config.leverage > pacifica_max_leverage:
                    reasons.append(f"Pacifica max: {pacifica_max_leverage}x")
                logger.warning(f"⚠️ Leverage adjusted to {final_leverage}x due to limits ({', '.join(reasons)}). Config: {self.config.leverage}x.")
            else:
                logger.info(f"⚡ Using leverage: {final_leverage}x for both exchanges (HL max: {hl_max_leverage}x, Pacifica max: {pacifica_max_leverage}x, Hard cap: {MAX_ALLOWED_LEVERAGE}x)")

            # Set leverage on BOTH exchanges
            logger.info(f"⚙️ Setting leverage to {final_leverage}x on both exchanges...")

            # Set leverage on Hyperliquid (isolated margin)
            hl_leverage_success = self.hl_client.update_leverage(symbol, final_leverage, is_cross_margin=False)
            if not hl_leverage_success:
                logger.error(f"{Colors.RED}Failed to set leverage on Hyperliquid for {symbol}. Aborting.{Colors.RESET}")
                self.state_mgr.set_state(BotState.WAITING)
                return

            # Set leverage on Pacifica
            pacifica_leverage_success = self.pacifica_client.set_leverage(symbol, final_leverage)
            if not pacifica_leverage_success:
                logger.error(f"{Colors.RED}Failed to set leverage on Pacifica for {symbol}. Aborting.{Colors.RESET}")
                self.state_mgr.set_state(BotState.WAITING)
                return

            logger.info(f"{Colors.GREEN}✅ Successfully set leverage to {final_leverage}x on both exchanges.{Colors.RESET}")

            # Brief pause to ensure both exchanges have processed the leverage update
            await asyncio.sleep(2)

            # Calculate target position size: base capital * leverage
            # Apply 2% safety buffer to base capital allocation
            safe_base_capital = self.config.base_capital_allocation * 0.98
            target_position_notional = safe_base_capital * final_leverage
            logger.info(f"Target position size: ${target_position_notional:.2f} (base capital: ${self.config.base_capital_allocation:.2f} with 2% buffer = ${safe_base_capital:.2f} x {final_leverage}x leverage)")

            # Calculate max position size based on available margin
            max_pos_hl = hl_avail * final_leverage
            max_pos_pa = pa_avail * final_leverage
            max_available_notional = min(max_pos_hl, max_pos_pa)

            if max_available_notional < 10.0: # Minimum position size in USD
                logger.warning("Insufficient available capital on one or both exchanges for a minimum position.")
                self.state_mgr.set_state(BotState.WAITING)
                return

            # Determine final notional size, respecting target and available capital
            # Use 95% of available for a safety buffer
            actual_notional = min(target_position_notional, max_available_notional * 0.95)

            if actual_notional < target_position_notional:
                logger.warning(f"Position size reduced to ${actual_notional:.2f} due to available margin (max available: ${max_available_notional:.2f}).")
            else:
                logger.info(f"Position size will be ${actual_notional:.2f}.")

            await self.open_position(best_opportunity, actual_notional)
        except BalanceFetchError as e:
            logger.error(f"Could not fetch balances to start cycle: {e}")
            self.state_mgr.set_state(BotState.WAITING)
        except Exception as e:
            logger.exception(f"Error during analysis phase: {e}")
            self.state_mgr.set_state(BotState.ERROR)

    async def open_position(self, opportunity: Dict, notional: float):
        self.state_mgr.set_state(BotState.OPENING)
        symbol = opportunity["symbol"]
        
        try:
            # 1. Get step sizes from both exchanges
            hl_step_size = self.hl_client.get_step_size(symbol)
            pacifica_lot_size = Decimal(str(self.pacifica_client.get_lot_size(symbol)))

            if not hl_step_size:
                raise ValueError(f"Could not get step size for {symbol} from Hyperliquid.")

            # 2. Determine the coarser (larger) step size for rounding
            coarser_step_size = max(hl_step_size, pacifica_lot_size)
            logger.info(f"Step sizes | HL: {hl_step_size}, Pacifica: {pacifica_lot_size}. Using coarser: {coarser_step_size}")

            # 3. Calculate and round the final quantity
            price = await self.pacifica_client.get_mark_price(symbol)
            if price <= 0:
                logger.warning(f"Could not get a valid price for {symbol}. Skipping trade for this cycle.")
                self.state_mgr.set_state(BotState.WAITING)
                return
            
            unrounded_quantity = Decimal(str(notional)) / Decimal(str(price))
            
            # Round down to the coarser precision to ensure both exchanges accept the order
            quantizer = Decimal('1')
            rounded_quantity_decimal = (unrounded_quantity / coarser_step_size).quantize(quantizer, rounding=ROUND_DOWN) * coarser_step_size
            
            final_quantity = float(rounded_quantity_decimal)
            
            if final_quantity <= 0:
                raise ValueError(f"Calculated quantity is zero after rounding. Notional ${notional} is too small for {symbol}'s tick sizes.")

            logger.info(f"Calculated final quantity for both exchanges: {final_quantity:.8f} {symbol}")

            # 4. Execute orders with the same final quantity
            # Note: These are synchronous calls, so we execute them sequentially
            if opportunity["long_exch"] == "Hyperliquid":
                logger.info(f"Opening LONG on Hyperliquid and SHORT on Pacifica.")
                hl_result = self.hl_client.market_open(symbol, is_buy=True, size=final_quantity)
                pacifica_result = self.pacifica_client.place_market_order(symbol, side='sell', quantity=final_quantity)
            else: # Long on Pacifica, Short on HL
                logger.info(f"Opening LONG on Pacifica and SHORT on Hyperliquid.")
                pacifica_result = self.pacifica_client.place_market_order(symbol, side='buy', quantity=final_quantity)
                hl_result = self.hl_client.market_open(symbol, is_buy=False, size=final_quantity)

            # Check if both orders succeeded
            if hl_result is None or pacifica_result is None:
                raise RuntimeError(f"Failed to open one or both legs: HL={hl_result}, Pacifica={pacifica_result}")

            logger.info(f"{Colors.GREEN}✅ Successfully opened delta-neutral position for {symbol}.{Colors.RESET}")

            # Get the actual leverage used for this position
            actual_leverage = self.hl_client.get_leverage(symbol)

            # Increment cycle number
            self.state_mgr.state["current_cycle_number"] += 1

            opened_at = datetime.now(UTC)
            self.state_mgr.state["current_position"] = {
                "symbol": symbol, "opened_at": opened_at.isoformat(),
                "target_close_at": (opened_at + timedelta(hours=self.config.hold_duration_hours)).isoformat(),
                "long_exchange": opportunity["long_exch"], "short_exchange": opportunity["short_exch"],
                "notional": notional, "leverage": actual_leverage,
                "entry_balance_hl": get_hyperliquid_balance(self.hl_client)[0],
                "entry_balance_pacifica": get_pacifica_balance(self.pacifica_client)[0]
            }
            self.state_mgr.set_state(BotState.HOLDING)
        except Exception as e:
            logger.exception(f"Failed to open position for {symbol}. Attempting to close any open legs.")
            await self.close_position(is_emergency=True)
            self.state_mgr.set_state(BotState.ERROR)

    async def monitor_position(self):
        pos = self.state_mgr.state["current_position"]
        if not pos: self.state_mgr.set_state(BotState.IDLE); return

        symbol = pos["symbol"]

        # Ensure timezone-aware datetime for comparison
        # Handle both 'Z' suffix and '+00:00' suffix
        target_close_str = pos['target_close_at']
        if target_close_str.endswith('Z'):
            target_close_str = target_close_str[:-1] + '+00:00'
        target_close_dt = datetime.fromisoformat(target_close_str)
        if target_close_dt.tzinfo is None:
            target_close_dt = target_close_dt.replace(tzinfo=UTC)
        if datetime.now(UTC) >= target_close_dt:
            logger.info(f"Hold duration for {symbol} has ended. Closing position.")
            await self.close_position()
            return

        # Get detailed position and account info
        try:
            # Get positions from both exchanges
            hl_pos = self.hl_client.get_position(symbol)
            pacifica_pos = await self.pacifica_client.get_position(symbol)

            # Get balances
            hl_total, hl_avail = get_hyperliquid_balance(self.hl_client)
            pa_total, pa_avail = get_pacifica_balance(self.pacifica_client)

            # Get funding rates
            try:
                hl_rates = self.hl_client.get_funding_rates()
                hl_funding = hl_rates.get(symbol, 0.0) * 24 * 365 * 100  # Convert to APR
            except:
                hl_funding = 0.0

            try:
                pa_funding = self.pacifica_client.get_funding_rate(symbol) * 24 * 365 * 100  # Convert to APR
            except:
                pa_funding = 0.0

            # Calculate PnL
            hl_pnl = hl_pos.get('unrealized_pnl', 0.0) if hl_pos else 0.0
            pa_pnl = pacifica_pos.get('unrealized_pnl', 0.0) if pacifica_pos else 0.0
            total_pnl = hl_pnl + pa_pnl

            # Get position sizes
            hl_size = hl_pos.get('qty', 0.0) if hl_pos else 0.0
            pa_size = pacifica_pos.get('qty', 0.0) if pacifica_pos else 0.0

            # Get leverage (use stored leverage from position state)
            hl_leverage = pos.get('leverage', self.config.leverage)
            pa_leverage = pos.get('leverage', self.config.leverage)

            # Display status
            pnl_color = Colors.GREEN if total_pnl >= 0 else Colors.RED
            cycle_number = self.state_mgr.state.get("current_cycle_number", 0)

            # Parse opened_at time
            opened_at_str = pos.get('opened_at', '')
            if opened_at_str.endswith('Z'):
                opened_at_str = opened_at_str[:-1] + '+00:00'
            opened_at_dt = datetime.fromisoformat(opened_at_str) if opened_at_str else None

            # Build status message as single string
            status_lines = []
            status_lines.append(f"\n{Colors.BOLD}{Colors.CYAN}{'='*70}{Colors.RESET}")
            status_lines.append(f"{Colors.BOLD}📊 Position Status: {Colors.YELLOW}{symbol}{Colors.RESET} {Colors.GRAY}(Cycle #{cycle_number}){Colors.RESET}")
            status_lines.append(f"{Colors.CYAN}{'='*70}{Colors.RESET}")

            # Show timing information
            if opened_at_dt:
                status_lines.append(f"\n{Colors.BOLD}{Colors.BLUE}⏰ Timing:{Colors.RESET}")
                status_lines.append(f"  {Colors.GRAY}Opened:{Colors.RESET}      {Colors.CYAN}{opened_at_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}{Colors.RESET}")
                status_lines.append(f"  {Colors.GRAY}Target Close:{Colors.RESET} {Colors.CYAN}{target_close_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}{Colors.RESET}")
                time_remaining = target_close_dt - datetime.now(UTC)
                hours_remaining = time_remaining.total_seconds() / 3600
                time_color = Colors.GREEN if hours_remaining > 6 else (Colors.YELLOW if hours_remaining > 2 else Colors.RED)
                status_lines.append(f"  {Colors.GRAY}Time Left:{Colors.RESET}    {time_color}{hours_remaining:.1f} hours{Colors.RESET}")

            status_lines.append(f"\n{Colors.BOLD}{Colors.MAGENTA}📈 Position Sizes:{Colors.RESET}")
            hl_size_color = Colors.GREEN if hl_size > 0 else Colors.RED
            pa_size_color = Colors.GREEN if pa_size > 0 else Colors.RED
            status_lines.append(f"  {Colors.CYAN}Hyperliquid:{Colors.RESET} {hl_size_color}{hl_size:+.4f}{Colors.RESET} {Colors.YELLOW}{symbol}{Colors.RESET}")
            status_lines.append(f"  {Colors.CYAN}Pacifica:{Colors.RESET}    {pa_size_color}{pa_size:+.4f}{Colors.RESET} {Colors.YELLOW}{symbol}{Colors.RESET}")
            status_lines.append(f"  {Colors.BOLD}Notional:{Colors.RESET}     {Colors.YELLOW}${pos['notional']:.2f}{Colors.RESET} {Colors.GRAY}(per exchange){Colors.RESET}")

            status_lines.append(f"\n{Colors.BOLD}{Colors.BLUE}💰 Account Balances:{Colors.RESET}")
            status_lines.append(f"  {Colors.CYAN}Hyperliquid:{Colors.RESET} {Colors.GREEN}${hl_total:.2f}{Colors.RESET} {Colors.GRAY}(Available: ${hl_avail:.2f}){Colors.RESET}")
            status_lines.append(f"  {Colors.CYAN}Pacifica:{Colors.RESET}    {Colors.GREEN}${pa_total:.2f}{Colors.RESET} {Colors.GRAY}(Available: ${pa_avail:.2f}){Colors.RESET}")
            current_equity = hl_total + pa_total
            total_equity_color = Colors.GREEN if current_equity > 200 else Colors.YELLOW
            status_lines.append(f"  {Colors.BOLD}Total Equity:{Colors.RESET} {total_equity_color}${current_equity:.2f}{Colors.RESET}")

            # Display long-term PnL if initial capital is available
            initial_capital = self.state_mgr.state.get("initial_capital")
            if initial_capital is not None:
                long_term_pnl = current_equity - initial_capital
                long_term_pnl_pct = (long_term_pnl / initial_capital * 100) if initial_capital > 0 else 0
                lt_pnl_color = Colors.GREEN if long_term_pnl >= 0 else Colors.RED
                status_lines.append(f"  {Colors.BOLD}Total PnL:{Colors.RESET}    {lt_pnl_color}${long_term_pnl:+.2f} ({long_term_pnl_pct:+.2f}%){Colors.RESET} {Colors.GRAY}(since start){Colors.RESET}")

            status_lines.append(f"\n{Colors.BOLD}{Colors.YELLOW}⚡ Leverage:{Colors.RESET}")
            lev_color = Colors.GREEN if hl_leverage <= 5 else (Colors.YELLOW if hl_leverage <= 10 else Colors.RED)
            status_lines.append(f"  {Colors.CYAN}Hyperliquid:{Colors.RESET} {lev_color}{hl_leverage:.1f}x{Colors.RESET}")
            status_lines.append(f"  {Colors.CYAN}Pacifica:{Colors.RESET}    {lev_color}{pa_leverage:.1f}x{Colors.RESET}")

            status_lines.append(f"\n{Colors.BOLD}{Colors.MAGENTA}💸 Funding Rates (APR):{Colors.RESET}")
            hl_color = Colors.GREEN if hl_funding >= 0 else Colors.RED
            pa_color = Colors.GREEN if pa_funding >= 0 else Colors.RED
            status_lines.append(f"  {Colors.CYAN}Hyperliquid:{Colors.RESET} {hl_color}{hl_funding:+.2f}%{Colors.RESET}")
            status_lines.append(f"  {Colors.CYAN}Pacifica:{Colors.RESET}    {pa_color}{pa_funding:+.2f}%{Colors.RESET}")
            spread_color = Colors.GREEN if abs(hl_funding - pa_funding) > 30 else Colors.YELLOW
            status_lines.append(f"  {Colors.BOLD}Net Spread:{Colors.RESET}  {spread_color}{abs(hl_funding - pa_funding):.2f}%{Colors.RESET}")

            status_lines.append(f"\n{Colors.BOLD}{Colors.GREEN}💵 Unrealized PnL:{Colors.RESET}")
            hl_pnl_color = Colors.GREEN if hl_pnl >= 0 else Colors.RED
            pa_pnl_color = Colors.GREEN if pa_pnl >= 0 else Colors.RED
            status_lines.append(f"  {Colors.CYAN}Hyperliquid:{Colors.RESET} {hl_pnl_color}${hl_pnl:+.2f}{Colors.RESET}")
            status_lines.append(f"  {Colors.CYAN}Pacifica:{Colors.RESET}    {pa_pnl_color}${pa_pnl:+.2f}{Colors.RESET}")
            status_lines.append(f"  {Colors.BOLD}Total PnL:{Colors.RESET}   {pnl_color}${total_pnl:+.2f}{Colors.RESET}")

            # Determine worst leg (for stop-loss calculation)
            worst_leg = "Hyperliquid" if hl_pnl < pa_pnl else "Pacifica"
            worst_pnl = min(hl_pnl, pa_pnl)

            # Stop-loss check and display (based on worst leg)
            pnl_data = {"total_unrealized_pnl": worst_pnl}  # Use worst leg PnL for stop-loss
            stop_loss_percent = pos.get("stop_loss_percent")
            if stop_loss_percent is None:
                stop_loss_percent = calculate_dynamic_stop_loss(pos.get("leverage", self.config.leverage))

            # Calculate stop-loss trigger level
            stop_loss_trigger = -1 * (pos["notional"] * stop_loss_percent / 100)
            pnl_percent = (total_pnl / pos["notional"]) * 100 if pos["notional"] > 0 else 0
            worst_leg_pnl_percent = (worst_pnl / pos["notional"]) * 100 if pos["notional"] > 0 else 0

            status_lines.append(f"\n{Colors.BOLD}{Colors.RED}🛡️ Risk Management:{Colors.RESET}")
            status_lines.append(f"  {Colors.GRAY}Stop-Loss:{Colors.RESET}   {Colors.RED}-{stop_loss_percent:.1f}%{Colors.RESET} {Colors.GRAY}(${stop_loss_trigger:.2f}){Colors.RESET}")
            pnl_pct_color = Colors.GREEN if pnl_percent >= 0 else Colors.RED
            status_lines.append(f"  {Colors.GRAY}Total PnL:{Colors.RESET}    {pnl_pct_color}{pnl_percent:+.2f}%{Colors.RESET} {Colors.GRAY}(${total_pnl:+.2f}){Colors.RESET}")
            status_lines.append(f"  {Colors.GRAY}HL PnL:{Colors.RESET}      {hl_pnl_color}${hl_pnl:+.2f}{Colors.RESET}")
            status_lines.append(f"  {Colors.GRAY}PA PnL:{Colors.RESET}      {pa_pnl_color}${pa_pnl:+.2f}{Colors.RESET}")
            worst_leg_color = Colors.CYAN
            worst_pnl_color = Colors.GREEN if worst_pnl >= 0 else Colors.RED
            worst_leg_pct_color = Colors.GREEN if worst_leg_pnl_percent >= 0 else Colors.RED
            status_lines.append(f"  {Colors.GRAY}Worst Leg:{Colors.RESET}   {worst_leg_color}{worst_leg}{Colors.RESET} ({worst_pnl_color}${worst_pnl:+.2f}{Colors.RESET}, {worst_leg_pct_color}{worst_leg_pnl_percent:+.2f}%{Colors.RESET})")

            # Distance to stop-loss (based on worst leg)
            distance_to_sl = worst_pnl - stop_loss_trigger
            if distance_to_sl < 0:
                sl_color = Colors.RED
                status_lines.append(f"  {Colors.RED}{Colors.BOLD}⚠ STOP-LOSS BREACH: ${distance_to_sl:+.2f}{Colors.RESET}")
            else:
                sl_pct = (distance_to_sl / abs(stop_loss_trigger)) * 100 if stop_loss_trigger != 0 else 100
                if sl_pct < 20:
                    sl_color = Colors.RED
                    status_lines.append(f"  {Colors.GRAY}Distance to SL:{Colors.RESET} {sl_color}${distance_to_sl:.2f} ({sl_pct:.1f}%){Colors.RESET}")
                elif sl_pct < 50:
                    sl_color = Colors.YELLOW
                    status_lines.append(f"  {Colors.GRAY}Distance to SL:{Colors.RESET} {sl_color}${distance_to_sl:.2f} ({sl_pct:.1f}%){Colors.RESET}")
                else:
                    status_lines.append(f"  {Colors.GRAY}Distance to SL:{Colors.RESET} {Colors.GREEN}${distance_to_sl:.2f} ({sl_pct:.1f}%){Colors.RESET}")

            status_lines.append(f"{Colors.CYAN}{'='*70}{Colors.RESET}\n")

            # Log as single message
            logger.info("\n".join(status_lines))

            triggered, reason = check_stop_loss(pnl_data, pos["notional"], stop_loss_percent)
            if triggered:
                logger.warning(f"{Colors.RED}🚨 Stop-loss triggered! {reason}{Colors.RESET}")
                await self.close_position()
                return

        except Exception as e:
            logger.error(f"Error gathering position info: {e}")
            # Fallback to basic monitoring
            pnl_data = await get_position_pnl(self.hl_client, self.pacifica_client, symbol)
            pnl_color = Colors.GREEN if pnl_data['total_unrealized_pnl'] >= 0 else Colors.RED
            logger.info(f"Monitoring {symbol}. Total PnL: {pnl_color}${pnl_data['total_unrealized_pnl']:.2f}{Colors.RESET}")

        logger.info(f"Next health check in {self.config.check_interval_seconds} seconds.")
        await self._responsive_sleep(self.config.check_interval_seconds)

    async def close_position(self, is_emergency: bool = False, max_retries: int = 3):
        if not is_emergency: self.state_mgr.set_state(BotState.CLOSING)
        pos = self.state_mgr.state["current_position"]
        if not pos: logger.warning("Close called but no position in state."); return
        symbol = pos["symbol"]

        try:
            hl_pos = self.hl_client.get_position(symbol)
            pacifica_pos = await self.pacifica_client.get_position(symbol)

            # Close positions with retry logic for each leg
            hl_closed = False
            pacifica_closed = False

            # Try to close Hyperliquid position with retries
            if hl_pos and hl_pos['qty'] != 0:
                for attempt in range(max_retries):
                    logger.info(f"Closing Hyperliquid position for {symbol} (attempt {attempt + 1}/{max_retries})...")
                    try:
                        hl_result = self.hl_client.market_close(symbol)
                        if hl_result is not None:
                            hl_closed = True
                            logger.info(f"{Colors.GREEN}✅ Hyperliquid position closed{Colors.RESET}")
                            break
                        else:
                            logger.warning(f"Hyperliquid close returned None on attempt {attempt + 1}")
                    except Exception as e:
                        logger.error(f"Hyperliquid close attempt {attempt + 1} failed: {e}")
                    if not hl_closed and attempt < max_retries - 1:
                        await asyncio.sleep(2)  # Wait before retry
            else:
                hl_closed = True  # No position to close
                logger.debug(f"No Hyperliquid position to close for {symbol}")

            # Try to close Pacifica position with retries
            if pacifica_pos and pacifica_pos['qty'] != 0:
                close_qty = abs(pacifica_pos['qty'])
                close_side = 'sell' if pacifica_pos['qty'] > 0 else 'buy'
                for attempt in range(max_retries):
                    logger.info(f"Closing Pacifica position for {symbol}: {close_qty:.4f} {close_side} (attempt {attempt + 1}/{max_retries})...")
                    try:
                        pacifica_result = self.pacifica_client.place_market_order(symbol, side=close_side, quantity=close_qty, reduce_only=True)
                        if pacifica_result is not None:
                            pacifica_closed = True
                            logger.info(f"{Colors.GREEN}✅ Pacifica position closed{Colors.RESET}")
                            break
                        else:
                            logger.warning(f"Pacifica close returned None on attempt {attempt + 1}")
                    except Exception as e:
                        logger.error(f"Pacifica close attempt {attempt + 1} failed: {e}")
                    if not pacifica_closed and attempt < max_retries - 1:
                        await asyncio.sleep(2)  # Wait before retry
                        # Re-check position in case it was filled
                        pacifica_pos = await self.pacifica_client.get_position(symbol)
                        if not pacifica_pos or pacifica_pos['qty'] == 0:
                            pacifica_closed = True
                            logger.info(f"{Colors.GREEN}✅ Pacifica position confirmed closed on re-check{Colors.RESET}")
                            break
                        close_qty = abs(pacifica_pos['qty'])
            else:
                pacifica_closed = True  # No position to close
                logger.debug(f"No Pacifica position to close for {symbol}")

            # Report results
            if hl_closed and pacifica_closed:
                logger.info(f"{Colors.GREEN}✅ Successfully closed all positions for {symbol}.{Colors.RESET}")
            elif hl_closed:
                logger.error(f"{Colors.RED}⚠️ PARTIAL CLOSE: Hyperliquid closed, but Pacifica position remains for {symbol}!{Colors.RESET}")
                logger.error(f"{Colors.RED}   Please manually close Pacifica position or restart bot for recovery.{Colors.RESET}")
                raise RuntimeError(f"Failed to close Pacifica position for {symbol} after {max_retries} attempts")
            elif pacifica_closed:
                logger.error(f"{Colors.RED}⚠️ PARTIAL CLOSE: Pacifica closed, but Hyperliquid position remains for {symbol}!{Colors.RESET}")
                logger.error(f"{Colors.RED}   Please manually close Hyperliquid position or restart bot for recovery.{Colors.RESET}")
                raise RuntimeError(f"Failed to close Hyperliquid position for {symbol} after {max_retries} attempts")
            else:
                raise RuntimeError(f"Failed to close both positions for {symbol} after {max_retries} attempts")

            if not is_emergency:
                # PnL Calculation
                try:
                    hl_balance_after = get_hyperliquid_balance(self.hl_client)[0]
                    pacifica_balance_after = get_pacifica_balance(self.pacifica_client)[0]
                    pnl_hl = hl_balance_after - pos.get('entry_balance_hl', hl_balance_after)
                    pnl_pacifica = pacifica_balance_after - pos.get('entry_balance_pacifica', pacifica_balance_after)
                    total_pnl = pnl_hl + pnl_pacifica

                    logger.info(f"💵 Cycle PnL | Hyperliquid: ${pnl_hl:+.2f} | Pacifica: ${pnl_pacifica:+.2f} | Total: ${total_pnl:+.2f}")

                    stats = self.state_mgr.state["cumulative_stats"]
                    stats["total_cycles"] += 1
                    stats["successful_cycles"] += 1
                    stats["total_realized_pnl"] += total_pnl
                except Exception as e:
                    logger.warning(f"Could not calculate PnL: {e}")

            self.state_mgr.state["current_position"] = None
            self.state_mgr.set_state(BotState.WAITING)
        except Exception as e:
            logger.exception(f"Failed to close position for {symbol}: {e}")
            self.state_mgr.set_state(BotState.ERROR)

async def main():
    parser = argparse.ArgumentParser(description="Delta-Neutral Funding Rate Bot for Hyperliquid and Pacifica")
    parser.add_argument("--state-file", type=str, default="bot_state_hl_pacifica.json", help="Path to the state file.")
    parser.add_argument("--config-file", type=str, default="bot_config.json", help="Path to the configuration file.")
    args = parser.parse_args()

    bot = RotationBot(state_file=args.state_file, config_file=args.config_file)
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
