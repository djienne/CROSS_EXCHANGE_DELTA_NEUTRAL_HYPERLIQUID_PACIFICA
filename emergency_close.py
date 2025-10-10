#!/usr/bin/env python3
"""
emergency_close.py
------------------
Emergency script to close all open positions on Hyperliquid and Pacifica exchanges.

Usage:
    python emergency_close.py                         # Scan symbols from bot_config.json, ask confirmation
    python emergency_close.py --symbol BTC            # Close specific symbol only
    python emergency_close.py --force                 # Close all without confirmation
    python emergency_close.py --dry-run               # Show what would be closed without executing
    python emergency_close.py --config custom.json   # Use custom config file
"""

import os
import sys
import argparse
import asyncio
import json
from dotenv import load_dotenv
from hyperliquid_connector import HyperliquidConnector
from pacifica_client import PacificaClient

# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'

def print_header():
    print(f"\n{Colors.BOLD}{Colors.RED}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.RED}  EMERGENCY POSITION CLOSER{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.RED}{'='*60}{Colors.RESET}\n")

def print_position(exchange: str, symbol: str, qty: float, unrealized_pnl: float = None):
    """Print position details."""
    side = "LONG" if qty > 0 else "SHORT"
    side_color = Colors.GREEN if qty > 0 else Colors.RED

    pnl_str = ""
    if unrealized_pnl is not None:
        pnl_color = Colors.GREEN if unrealized_pnl >= 0 else Colors.RED
        pnl_str = f" | PnL: {pnl_color}${unrealized_pnl:+.2f}{Colors.RESET}"

    print(f"  {Colors.CYAN}{exchange:12}{Colors.RESET} | {symbol:8} | {side_color}{side:5}{Colors.RESET} | Qty: {abs(qty):.4f}{pnl_str}")

def load_config_symbols(config_file: str = "bot_config.json") -> list:
    """Load symbols from bot config file."""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        return config.get('symbols_to_monitor', [])
    except Exception as e:
        print(f"{Colors.YELLOW}Warning: Could not load symbols from {config_file}: {e}{Colors.RESET}")
        return []

async def scan_positions(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, symbols: list = None):
    """Scan both exchanges for open positions."""
    positions = []

    if symbols is None:
        # Load symbols from config file
        symbols = load_config_symbols()
        if not symbols:
            print(f"{Colors.RED}Error: No symbols to scan. Check bot_config.json{Colors.RESET}")
            return []

    print(f"{Colors.YELLOW}Scanning {len(symbols)} symbols from config for open positions...{Colors.RESET}\n")

    for symbol in symbols:
        # Check Hyperliquid
        try:
            hl_pos = hl_client.get_position(symbol)
            if hl_pos and hl_pos.get('qty', 0) != 0:
                positions.append({
                    'exchange': 'Hyperliquid',
                    'symbol': symbol,
                    'qty': hl_pos['qty'],
                    'unrealized_pnl': hl_pos.get('unrealized_pnl', 0.0),
                    'position_obj': hl_pos
                })
        except Exception as e:
            pass  # Ignore errors for symbols that don't exist

        # Check Pacifica
        try:
            pacifica_pos = await pacifica_client.get_position(symbol)
            if pacifica_pos and pacifica_pos.get('qty', 0) != 0:
                positions.append({
                    'exchange': 'Pacifica',
                    'symbol': symbol,
                    'qty': pacifica_pos['qty'],
                    'unrealized_pnl': pacifica_pos.get('unrealized_pnl', 0.0),
                    'position_obj': pacifica_pos
                })
        except Exception as e:
            pass  # Ignore errors for symbols that don't exist

    return positions

async def close_position(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, position: dict, dry_run: bool = False):
    """Close a single position."""
    exchange = position['exchange']
    symbol = position['symbol']
    qty = position['qty']

    if dry_run:
        print(f"  {Colors.YELLOW}[DRY-RUN]{Colors.RESET} Would close {exchange} {symbol} position (qty: {qty:.4f})")
        return True

    try:
        if exchange == 'Hyperliquid':
            result = hl_client.market_close(symbol)
            if result:
                print(f"  {Colors.GREEN}✓{Colors.RESET} Closed {exchange} {symbol} position")
                return True
            else:
                print(f"  {Colors.RED}✗{Colors.RESET} Failed to close {exchange} {symbol} position")
                return False

        elif exchange == 'Pacifica':
            close_qty = abs(qty)
            close_side = 'sell' if qty > 0 else 'buy'
            result = pacifica_client.place_market_order(symbol, side=close_side, quantity=close_qty, reduce_only=True)
            if result:
                print(f"  {Colors.GREEN}✓{Colors.RESET} Closed {exchange} {symbol} position")
                return True
            else:
                print(f"  {Colors.RED}✗{Colors.RESET} Failed to close {exchange} {symbol} position")
                return False

    except Exception as e:
        print(f"  {Colors.RED}✗{Colors.RESET} Error closing {exchange} {symbol}: {e}")
        return False

async def main():
    parser = argparse.ArgumentParser(description="Emergency position closer for Hyperliquid and Pacifica")
    parser.add_argument('--symbol', type=str, help='Close specific symbol only (e.g., BTC)')
    parser.add_argument('--force', action='store_true', help='Close all positions without confirmation')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be closed without executing')
    parser.add_argument('--config', type=str, default='bot_config.json', help='Config file to load symbols from (default: bot_config.json)')
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()
    hl_wallet = os.getenv("HL_WALLET")
    hl_private_key = os.getenv("HL_PRIVATE_KEY")
    sol_wallet = os.getenv("SOL_WALLET")
    api_public = os.getenv("API_PUBLIC")
    api_private = os.getenv("API_PRIVATE")

    if not all([hl_wallet, hl_private_key, sol_wallet, api_public, api_private]):
        print(f"{Colors.RED}Error: Missing environment variables. Check your .env file.{Colors.RESET}")
        sys.exit(1)

    print_header()

    if args.dry_run:
        print(f"{Colors.YELLOW}DRY-RUN MODE: No positions will be closed{Colors.RESET}\n")

    # Initialize clients
    print("Connecting to exchanges...")
    hl_client = HyperliquidConnector(hl_wallet, hl_private_key)
    pacifica_client = PacificaClient(sol_wallet, api_public, api_private)
    print(f"{Colors.GREEN}Connected.{Colors.RESET}\n")

    # Scan for positions
    if args.symbol:
        # Scan specific symbol
        symbols_to_scan = [args.symbol]
    else:
        # Load symbols from config file
        symbols_to_scan = load_config_symbols(args.config)
        if not symbols_to_scan:
            print(f"{Colors.RED}Error: No symbols found in {args.config}{Colors.RESET}\n")
            hl_client.close()
            sys.exit(1)

    positions = await scan_positions(hl_client, pacifica_client, symbols_to_scan)

    if not positions:
        print(f"{Colors.GREEN}No open positions found.{Colors.RESET}\n")
        hl_client.close()
        return

    # Display found positions
    print(f"{Colors.BOLD}Found {len(positions)} open position(s):{Colors.RESET}\n")
    total_pnl = 0.0
    for pos in positions:
        print_position(pos['exchange'], pos['symbol'], pos['qty'], pos.get('unrealized_pnl'))
        total_pnl += pos.get('unrealized_pnl', 0.0)

    pnl_color = Colors.GREEN if total_pnl >= 0 else Colors.RED
    print(f"\n{Colors.BOLD}Total Unrealized PnL: {pnl_color}${total_pnl:+.2f}{Colors.RESET}\n")

    # Confirmation
    if not args.force and not args.dry_run:
        print(f"{Colors.YELLOW}{Colors.BOLD}WARNING: This will close all positions listed above.{Colors.RESET}")
        input(f"Press {Colors.BOLD}ENTER{Colors.RESET} to confirm (or Ctrl+C to cancel): ")
        print()

    # Close positions
    if args.dry_run:
        print(f"{Colors.YELLOW}DRY-RUN: Showing what would be closed:{Colors.RESET}\n")
    else:
        print(f"{Colors.BOLD}Closing positions...{Colors.RESET}\n")

    success_count = 0
    for pos in positions:
        result = await close_position(hl_client, pacifica_client, pos, dry_run=args.dry_run)
        if result:
            success_count += 1

    # Summary
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    if args.dry_run:
        print(f"{Colors.YELLOW}DRY-RUN COMPLETE: {success_count}/{len(positions)} positions would be closed{Colors.RESET}")
    else:
        if success_count == len(positions):
            print(f"{Colors.GREEN}SUCCESS: All {success_count} position(s) closed{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}PARTIAL: {success_count}/{len(positions)} position(s) closed{Colors.RESET}")
            print(f"{Colors.RED}Some positions failed to close. Check errors above.{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")

    # Cleanup
    hl_client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Operation cancelled by user.{Colors.RESET}\n")
        sys.exit(0)
