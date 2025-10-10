#!/usr/bin/env python3
"""
show_volumes.py
---------------
Standalone script to display 24h trading volume comparison between Hyperliquid and Pacifica.

Usage:
    python show_volumes.py
    python show_volumes.py --symbols BTC ETH SOL
    python show_volumes.py --config bot_config.json
"""

import asyncio
import argparse
import json
import os
import sys
import requests
from typing import List, Dict
from dotenv import load_dotenv
from pathlib import Path

# Add parent directory to path for SDK imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from pacifica_sdk.common.constants import REST_URL

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

def get_hyperliquid_volumes(hl_client: HyperliquidConnector) -> Dict[str, float]:
    """Fetch 24h volumes from Hyperliquid."""
    try:
        # Get meta and asset contexts which includes volume data
        meta_and_ctxs = hl_client.info.meta_and_asset_ctxs()

        if not meta_and_ctxs or len(meta_and_ctxs) < 2:
            print(f"{Colors.RED}Error: Invalid data from Hyperliquid meta_and_asset_ctxs endpoint{Colors.RESET}")
            return {}

        universe = meta_and_ctxs[0].get("universe", [])
        asset_ctxs = meta_and_ctxs[1]

        volumes = {}
        for i, asset_info in enumerate(universe):
            try:
                symbol = asset_info["name"]
                # Volume is in the dayNtlVlm field (daily notional volume in USD)
                volume_usd = float(asset_ctxs[i].get("dayNtlVlm", 0))
                volumes[symbol] = volume_usd
            except (IndexError, KeyError) as e:
                print(f"{Colors.YELLOW}Warning: Could not get volume for asset at index {i}: {e}{Colors.RESET}")

        return volumes
    except Exception as e:
        print(f"{Colors.RED}Error fetching Hyperliquid volumes: {e}{Colors.RESET}")
        return {}

def get_pacifica_volumes(symbols: List[str]) -> Dict[str, float]:
    """Fetch 24h volumes from Pacifica using kline data.

    Pacifica doesn't provide direct 24h volume, but we can calculate it from kline data.
    Volume is calculated by summing hourly candles and converting to USD.
    """
    import time

    volumes = {}

    # Calculate start time (24 hours ago in milliseconds)
    start_time = int((time.time() - 86400) * 1000)

    for symbol in symbols:
        try:
            url = f"{REST_URL}/kline"
            params = {
                "symbol": symbol,
                "interval": "1h",
                "start_time": start_time
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                print(f"{Colors.YELLOW}Warning: Failed to get kline data for {symbol}{Colors.RESET}")
                volumes[symbol] = 0
                continue

            candles = data.get("data", [])

            # Calculate total volume in USD
            # Volume is in base currency, need to convert to USD using average price
            total_volume_usd = 0
            for candle in candles:
                volume_base = float(candle.get("v", 0))  # Volume in base currency (e.g., BTC)
                open_price = float(candle.get("o", 0))
                close_price = float(candle.get("c", 0))
                avg_price = (open_price + close_price) / 2  # Average price for the candle

                volume_usd = volume_base * avg_price
                total_volume_usd += volume_usd

            volumes[symbol] = total_volume_usd

        except Exception as e:
            print(f"{Colors.YELLOW}Warning: Could not get volume for {symbol}: {e}{Colors.RESET}")
            volumes[symbol] = 0

    return volumes

def format_volume(volume: float, allow_na: bool = False) -> str:
    """Format volume in a human-readable way."""
    if volume is None:
        return "N/A" if allow_na else "$0.00"
    if volume >= 1_000_000_000:
        return f"${volume/1_000_000_000:.2f}B"
    elif volume >= 1_000_000:
        return f"${volume/1_000_000:.2f}M"
    elif volume >= 1_000:
        return f"${volume/1_000:.2f}K"
    else:
        return f"${volume:.2f}"

def display_volumes_table(volumes: List[Dict]):
    """Display 24h volumes comparison in a formatted table."""
    if not volumes:
        print(f"{Colors.YELLOW}No volume data available to display{Colors.RESET}")
        return

    # Build table header
    table_lines = []
    table_lines.append(f"\n{Colors.BOLD}{Colors.CYAN}{'='*105}{Colors.RESET}")
    table_lines.append(f"{Colors.BOLD}📊 24h Trading Volume Comparison{Colors.RESET}")
    table_lines.append(f"{Colors.CYAN}{'='*105}{Colors.RESET}")

    # Column headers
    header = f"{Colors.BOLD}{'Symbol':<10} {'Hyperliquid':>18} {'Pacifica':>18} {'Total Volume':>18}  {'HL Share':>10}{Colors.RESET}"
    table_lines.append(header)
    table_lines.append(f"{Colors.GRAY}{'-'*105}{Colors.RESET}")

    # Sort by total volume descending
    sorted_vols = sorted(volumes, key=lambda x: x["total_volume"], reverse=True)

    total_hl = 0
    total_pac = 0

    for vol in sorted_vols:
        symbol = vol["symbol"]
        hl_vol = vol["hl_volume"]
        pac_vol = vol["pac_volume"]
        total_vol = vol["total_volume"]

        # Calculate market share
        if total_vol > 0:
            hl_share = (hl_vol / total_vol) * 100
        else:
            hl_share = 0

        # Color coding based on volume
        if total_vol >= 1_000_000:
            symbol_color = Colors.GREEN
        elif total_vol >= 100_000:
            symbol_color = Colors.YELLOW
        else:
            symbol_color = Colors.GRAY

        # Format volumes
        hl_vol_str = format_volume(hl_vol)
        pac_vol_str = format_volume(pac_vol)
        total_vol_str = format_volume(total_vol)

        # Format row
        row = (f"{symbol_color}{symbol:<10}{Colors.RESET} "
               f"{Colors.CYAN}{hl_vol_str:>18}{Colors.RESET} "
               f"{Colors.MAGENTA}{pac_vol_str:>18}{Colors.RESET} "
               f"{Colors.BOLD}{total_vol_str:>18}{Colors.RESET}  "
               f"{Colors.GRAY}{hl_share:>9.1f}%{Colors.RESET}")
        table_lines.append(row)

        total_hl += hl_vol
        total_pac += pac_vol

    table_lines.append(f"{Colors.CYAN}{'='*105}{Colors.RESET}")

    # Summary totals
    total_combined = total_hl + total_pac
    hl_share_total = (total_hl / total_combined * 100) if total_combined > 0 else 0

    summary = (f"{Colors.BOLD}Total Volume: {format_volume(total_combined)} "
              f"(Hyperliquid: {format_volume(total_hl)}, Pacifica: {format_volume(total_pac)}){Colors.RESET}")
    table_lines.append(summary)

    share_info = (f"{Colors.GRAY}Market Share: Hyperliquid {hl_share_total:.1f}%, "
                 f"Pacifica {100-hl_share_total:.1f}%{Colors.RESET}")
    table_lines.append(share_info)

    table_lines.append(f"{Colors.CYAN}{'='*105}{Colors.RESET}\n")

    # Print the table
    print("\n".join(table_lines))

def load_config_symbols(config_file: str) -> List[str]:
    """Load symbols from bot config file."""
    try:
        with open(config_file, 'r') as f:
            data = json.load(f)
        return data.get('symbols_to_monitor', ["BTC", "ETH", "SOL"])
    except FileNotFoundError:
        print(f"{Colors.YELLOW}Config file {config_file} not found, using defaults.{Colors.RESET}")
        return ["BTC", "ETH", "SOL"]
    except Exception as e:
        print(f"{Colors.RED}Error loading config: {e}, using defaults.{Colors.RESET}")
        return ["BTC", "ETH", "SOL"]

async def main():
    parser = argparse.ArgumentParser(description="Display 24h trading volume comparison between Hyperliquid and Pacifica")
    parser.add_argument("--symbols", type=str, nargs='+', help="List of symbols to check (e.g., BTC ETH SOL)")
    parser.add_argument("--config", type=str, default="bot_config.json", help="Path to bot config file (default: bot_config.json)")
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    hl_wallet = os.getenv("HL_WALLET")
    hl_private_key = os.getenv("HL_PRIVATE_KEY")
    sol_wallet = os.getenv("SOL_WALLET")
    api_public = os.getenv("API_PUBLIC")
    api_private = os.getenv("API_PRIVATE")

    if not all([hl_wallet, hl_private_key, sol_wallet, api_public, api_private]):
        print(f"{Colors.RED}Error: Missing required environment variables. Please check your .env file.{Colors.RESET}")
        sys.exit(1)

    print(f"{Colors.CYAN}Initializing exchange clients...{Colors.RESET}")

    # Initialize clients
    hl_client = HyperliquidConnector(hl_wallet, hl_private_key)
    pacifica_client = PacificaClient(sol_wallet, api_public, api_private)

    # Determine which symbols to check
    if args.symbols:
        symbols = args.symbols
        print(f"{Colors.CYAN}Using symbols from command line: {', '.join(symbols)}{Colors.RESET}")
    else:
        symbols = load_config_symbols(args.config)
        print(f"{Colors.CYAN}Using symbols from {args.config}: {', '.join(symbols)}{Colors.RESET}")

    # Filter symbols to only those available on both exchanges
    hl_symbols = set(hl_client.coin_to_meta.keys())
    pacifica_symbols = set(pacifica_client._market_info.keys())
    common_symbols = hl_symbols.intersection(pacifica_symbols)

    filtered_symbols = [s for s in symbols if s in common_symbols]

    if len(filtered_symbols) < len(symbols):
        removed = set(symbols) - set(filtered_symbols)
        print(f"{Colors.YELLOW}Warning: Removed symbols not available on both exchanges: {', '.join(removed)}{Colors.RESET}")

    if not filtered_symbols:
        print(f"{Colors.RED}Error: No common symbols found between exchanges.{Colors.RESET}")
        sys.exit(1)

    # Fetch volumes
    print(f"{Colors.CYAN}Fetching 24h volumes for {len(filtered_symbols)} symbols...{Colors.RESET}")

    hl_volumes = get_hyperliquid_volumes(hl_client)
    pac_volumes = get_pacifica_volumes(filtered_symbols)

    # Combine data
    volume_data = []
    for symbol in filtered_symbols:
        hl_vol = hl_volumes.get(symbol, 0)
        pac_vol = pac_volumes.get(symbol, 0) if pac_volumes else 0
        total_vol = hl_vol + pac_vol

        volume_data.append({
            "symbol": symbol,
            "hl_volume": hl_vol,
            "pac_volume": pac_vol,
            "total_volume": total_vol
        })

    if volume_data:
        display_volumes_table(volume_data)
    else:
        print(f"{Colors.RED}No volume data available.{Colors.RESET}")

    # Cleanup
    if hl_client:
        hl_client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user.{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}Error: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
