#!/usr/bin/env python3
"""
show_price_spreads.py
---------------------
Standalone script to display current price spreads between Hyperliquid and Pacifica.

Shows mid price on each exchange and the percentage spread between them.

Usage:
    python show_price_spreads.py
    python show_price_spreads.py --symbols BTC ETH SOL
    python show_price_spreads.py --config bot_config.json
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

async def fetch_price_spreads(hl_client: HyperliquidConnector, pacifica_client: PacificaClient, symbols: List[str]) -> List[Dict]:
    """Fetch and compare mid prices for a list of symbols."""
    results = []

    for symbol in symbols:
        try:
            # Get Hyperliquid mid price
            hl_price_str = hl_client.get_mid_price(symbol)
            if not hl_price_str:
                print(f"{Colors.YELLOW}No mid price for {symbol} on Hyperliquid.{Colors.RESET}")
                continue

            hl_price = float(hl_price_str)

            # Get Pacifica mark price
            pacifica_price = await pacifica_client.get_mark_price(symbol)
            if pacifica_price <= 0:
                print(f"{Colors.YELLOW}No mark price for {symbol} on Pacifica.{Colors.RESET}")
                continue

            # Calculate spread as percentage
            # Use Hyperliquid as reference price
            spread_pct = abs(hl_price - pacifica_price) / hl_price * 100

            # Determine which exchange is higher
            higher_exch = "Hyperliquid" if hl_price > pacifica_price else "Pacifica"
            lower_exch = "Pacifica" if higher_exch == "Hyperliquid" else "Hyperliquid"

            results.append({
                "symbol": symbol,
                "hl_price": hl_price,
                "pacifica_price": pacifica_price,
                "spread_pct": spread_pct,
                "higher_exch": higher_exch,
                "lower_exch": lower_exch,
            })
        except Exception as e:
            print(f"{Colors.YELLOW}Could not process prices for {symbol}: {e}{Colors.RESET}")

    return results

def display_spreads_table(spreads: List[Dict], max_spread: float = 1.0):
    """Display price spreads in a formatted table."""
    if not spreads:
        print(f"{Colors.YELLOW}No price data available to display{Colors.RESET}")
        return

    # Build table header
    table_lines = []
    table_lines.append(f"\n{Colors.BOLD}{Colors.CYAN}{'='*100}{Colors.RESET}")
    table_lines.append(f"{Colors.BOLD}Price Spreads Comparison{Colors.RESET}")
    table_lines.append(f"{Colors.CYAN}{'='*100}{Colors.RESET}")

    # Column headers
    header = f"{Colors.BOLD}{'Symbol':<10} {'Hyperliquid':>15} {'Pacifica':>15} {'Spread':>12}  {'Note':<30}{Colors.RESET}"
    table_lines.append(header)
    table_lines.append(f"{Colors.GRAY}{'-'*100}{Colors.RESET}")

    # Sort by spread descending
    sorted_spreads = sorted(spreads, key=lambda x: x["spread_pct"], reverse=True)

    for item in sorted_spreads:
        symbol = item["symbol"]
        hl_price = item["hl_price"]
        pac_price = item["pacifica_price"]
        spread_pct = item["spread_pct"]
        higher_exch = item["higher_exch"]

        # Color coding based on spread magnitude
        if spread_pct > max_spread:
            spread_color = Colors.RED
            symbol_color = Colors.YELLOW
            note = f"{higher_exch} +{spread_pct:.3f}% higher"
            note_color = Colors.RED
        elif spread_pct > max_spread / 2:
            spread_color = Colors.YELLOW
            symbol_color = Colors.YELLOW
            note = f"{higher_exch} +{spread_pct:.3f}% higher"
            note_color = Colors.YELLOW
        else:
            spread_color = Colors.GREEN
            symbol_color = Colors.GREEN
            note = "Good alignment"
            note_color = Colors.GREEN

        # Format prices with appropriate precision
        if hl_price >= 1000:
            hl_price_str = f"${hl_price:,.2f}"
            pac_price_str = f"${pac_price:,.2f}"
        elif hl_price >= 1:
            hl_price_str = f"${hl_price:.2f}"
            pac_price_str = f"${pac_price:.2f}"
        else:
            hl_price_str = f"${hl_price:.6f}"
            pac_price_str = f"${pac_price:.6f}"

        # Format row
        row = (f"{symbol_color}{symbol:<10}{Colors.RESET} "
               f"{Colors.CYAN}{hl_price_str:>15}{Colors.RESET} "
               f"{Colors.CYAN}{pac_price_str:>15}{Colors.RESET} "
               f"{spread_color}{spread_pct:>11.3f}%{Colors.RESET}  "
               f"{note_color}{note:<30}{Colors.RESET}")
        table_lines.append(row)

    table_lines.append(f"{Colors.CYAN}{'='*100}{Colors.RESET}")

    # Summary
    best_spread = sorted_spreads[-1]  # Lowest spread
    worst_spread = sorted_spreads[0]  # Highest spread
    avg_spread = sum(s["spread_pct"] for s in spreads) / len(spreads)

    summary_lines = []
    summary_lines.append(f"{Colors.BOLD}Summary:{Colors.RESET}")
    summary_lines.append(f"  Tightest spread: {Colors.GREEN}{best_spread['symbol']} ({best_spread['spread_pct']:.3f}%){Colors.RESET}")
    summary_lines.append(f"  Widest spread:   {Colors.RED}{worst_spread['symbol']} ({worst_spread['spread_pct']:.3f}%){Colors.RESET}")
    summary_lines.append(f"  Average spread:  {Colors.YELLOW}{avg_spread:.3f}%{Colors.RESET}")

    # Alert if any spreads are too high
    high_spreads = [s for s in spreads if s["spread_pct"] > max_spread]
    if high_spreads:
        summary_lines.append(f"\n{Colors.RED}WARNING: {len(high_spreads)} symbol(s) with spread > {max_spread}%:{Colors.RESET}")
        for s in high_spreads:
            summary_lines.append(f"  - {s['symbol']}: {s['spread_pct']:.3f}% ({s['higher_exch']} higher)")
    else:
        summary_lines.append(f"\n{Colors.GREEN}[OK] All spreads within {max_spread}% threshold{Colors.RESET}")

    table_lines.append("\n".join(summary_lines))
    table_lines.append(f"{Colors.CYAN}{'='*100}{Colors.RESET}\n")

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
    parser = argparse.ArgumentParser(description="Display price spreads between Hyperliquid and Pacifica")
    parser.add_argument("--symbols", type=str, nargs='+', help="List of symbols to check (e.g., BTC ETH SOL)")
    parser.add_argument("--config", type=str, default="bot_config.json", help="Path to bot config file (default: bot_config.json)")
    parser.add_argument("--max-spread", type=float, default=0.15, help="Maximum acceptable spread percentage for alerts (default: 0.15)")
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

    # Fetch and display price spreads
    print(f"{Colors.CYAN}Fetching prices for {len(filtered_symbols)} symbols...{Colors.RESET}")
    spreads = await fetch_price_spreads(hl_client, pacifica_client, filtered_symbols)

    if spreads:
        display_spreads_table(spreads, args.max_spread)
    else:
        print(f"{Colors.RED}No price data available.{Colors.RESET}")

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
