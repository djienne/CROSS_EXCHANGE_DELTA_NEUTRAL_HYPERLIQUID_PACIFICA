#!/usr/bin/env python3
"""
show_funding_rates.py
---------------------
Standalone script to display PREDICTED/NEXT funding rates comparison between Hyperliquid and Pacifica.

Shows forward-looking funding rates that will be applied in the next funding period,
not historical rates that were already applied. This is critical for arbitrage decision-making.

Usage:
    python show_funding_rates.py
    python show_funding_rates.py --symbols BTC ETH SOL
    python show_funding_rates.py --config bot_config.json
"""

import asyncio
import argparse
import json
import os
import sys
from typing import List, Dict
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

async def fetch_funding_rates(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, symbols: List[str]) -> List[Dict]:
    """
    Fetch and compare PREDICTED/NEXT funding rates for a list of symbols.

    Uses forward-looking funding rates that will be applied in the next funding period,
    not historical rates that were already applied.
    """
    try:
        hl_rates = hl_client.get_predicted_funding_rates()
    except Exception as e:
        print(f"{Colors.RED}Error fetching Hyperliquid predicted funding rates: {e}{Colors.RESET}")
        hl_rates = {}

    results = []
    for symbol in symbols:
        try:
            # Pacifica's get_funding_rate now returns next_funding_rate (predicted)
            pacifica_rate = pacifica_client.get_funding_rate(symbol)

            # hl_rates now returns a dict with 'funding_rate' and 'next_funding_time'
            hl_rate_data = hl_rates.get(symbol)

            if hl_rate_data is None:
                print(f"{Colors.YELLOW}No predicted funding rate for {symbol} on Hyperliquid.{Colors.RESET}")
                continue

            hl_rate = hl_rate_data["funding_rate"]

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
                "available": True,
                "next_funding_time": hl_rate_data["next_funding_time"]
            })
        except Exception as e:
            print(f"{Colors.YELLOW}Could not process funding for {symbol}: {e}{Colors.RESET}")

    return results

def display_funding_rates_table(opportunities: List[Dict], min_threshold: float = 0.0):
    """Display funding rates comparison in a formatted table."""
    if not opportunities:
        print(f"{Colors.YELLOW}No funding rate data available to display{Colors.RESET}")
        return

    # Build table header
    table_lines = []
    table_lines.append(f"\n{Colors.BOLD}{Colors.CYAN}{'='*95}{Colors.RESET}")
    table_lines.append(f"{Colors.BOLD}💸 Funding Rates Comparison (APR %){Colors.RESET}")
    table_lines.append(f"{Colors.CYAN}{'='*95}{Colors.RESET}")

    # Column headers
    header = f"{Colors.BOLD}{'Symbol':<10} {'Hyperliquid':>12} {'Pacifica':>12} {'Net Spread':>12}  {'Strategy':<35}{Colors.RESET}"
    table_lines.append(header)
    table_lines.append(f"{Colors.GRAY}{'-'*95}{Colors.RESET}")

    # Sort by net APR descending
    sorted_opps = sorted(opportunities, key=lambda x: x["net_apr"], reverse=True)

    for opp in sorted_opps:
        symbol = opp["symbol"]
        hl_apr = opp["hl_apr"]
        pac_apr = opp["pacifica_apr"]
        net_apr = opp["net_apr"]
        long_exch = opp["long_exch"]
        short_exch = opp["short_exch"]

        # Color coding
        hl_color = Colors.GREEN if hl_apr >= 0 else Colors.RED
        pac_color = Colors.GREEN if pac_apr >= 0 else Colors.RED

        # Net APR color: green if above threshold, yellow otherwise
        if net_apr >= min_threshold:
            net_color = Colors.GREEN
            symbol_color = Colors.YELLOW
        else:
            net_color = Colors.GRAY
            symbol_color = Colors.GRAY

        # Strategy description
        strategy = f"LONG {long_exch[:2]}, SHORT {short_exch[:2]}"

        # Format row
        row = (f"{symbol_color}{symbol:<10}{Colors.RESET} "
               f"{hl_color}{hl_apr:>11.2f}%{Colors.RESET} "
               f"{pac_color}{pac_apr:>11.2f}%{Colors.RESET} "
               f"{net_color}{net_apr:>11.2f}%{Colors.RESET}  "
               f"{Colors.GRAY}{strategy:<35}{Colors.RESET}")
        table_lines.append(row)

    table_lines.append(f"{Colors.CYAN}{'='*95}{Colors.RESET}")

    # Summary
    best_opp = sorted_opps[0]
    if best_opp["net_apr"] >= min_threshold:
        summary = (f"{Colors.GREEN}✓ Best opportunity: {best_opp['symbol']} "
                  f"({best_opp['net_apr']:.2f}% spread){Colors.RESET}")
    else:
        summary = (f"{Colors.YELLOW}⚠ No opportunities above {min_threshold:.1f}% threshold. "
                  f"Best: {best_opp['symbol']} ({best_opp['net_apr']:.2f}%){Colors.RESET}")
    table_lines.append(summary)
    table_lines.append(f"{Colors.CYAN}{'='*95}{Colors.RESET}\n")

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
    parser = argparse.ArgumentParser(description="Display funding rates comparison between Hyperliquid and Pacifica")
    parser.add_argument("--symbols", type=str, nargs='+', help="List of symbols to check (e.g., BTC ETH SOL)")
    parser.add_argument("--config", type=str, default="bot_config.json", help="Path to bot config file (default: bot_config.json)")
    parser.add_argument("--threshold", type=float, default=5.0, help="Minimum net APR threshold for highlighting (default: 5.0)")
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

    # Fetch and display funding rates
    print(f"{Colors.CYAN}Fetching funding rates for {len(filtered_symbols)} symbols...{Colors.RESET}")
    opportunities = await fetch_funding_rates(hl_client, pacifica_client, filtered_symbols)

    if opportunities:
        display_funding_rates_table(opportunities, args.threshold)
    else:
        print(f"{Colors.RED}No funding rate data available.{Colors.RESET}")

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
        sys.exit(1)
