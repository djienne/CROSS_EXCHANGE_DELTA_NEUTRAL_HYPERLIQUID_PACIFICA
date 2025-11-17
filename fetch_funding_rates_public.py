#!/usr/bin/env python3
"""
Quick script to fetch predicted funding rates without authentication.
Uses only public API endpoints.
"""
import requests
import json
from typing import Dict

def fetch_hyperliquid_predicted_funding(symbols: list) -> Dict:
    """Fetch predicted funding rates from Hyperliquid (public API)."""
    try:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "predictedFundings"}

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Parse response: [[asset, [[venue, {fundingRate, nextFundingTime}]]]]
        predicted_rates = {}
        for asset_data in data:
            asset_name = asset_data[0]
            if asset_name not in symbols:
                continue

            venues = asset_data[1]

            # Find HlPerp venue (Hyperliquid perpetuals)
            for venue_data in venues:
                venue_name = venue_data[0]
                if venue_name == "HlPerp":
                    venue_info = venue_data[1]
                    predicted_rates[asset_name] = {
                        "funding_rate": float(venue_info["fundingRate"]),
                        "next_funding_time": venue_info["nextFundingTime"],
                        "apr": float(venue_info["fundingRate"]) * 24 * 365 * 100
                    }
                    break

        return predicted_rates
    except Exception as e:
        print(f"Error fetching Hyperliquid funding rates: {e}")
        return {}

def fetch_pacifica_funding(symbols: list) -> Dict:
    """Fetch funding rates from Pacifica (public API)."""
    try:
        url = "https://api.pacifica.fi/api/v1/info"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            raise ValueError("Pacifica API call was not successful")

        markets = data.get("data", [])
        funding_rates = {}

        for market in markets:
            symbol = market.get("symbol")
            if symbol not in symbols:
                continue

            # Use next_funding_rate (predicted), not funding_rate (historical)
            next_rate = float(market.get("next_funding_rate", 0.0))
            funding_rates[symbol] = {
                "funding_rate": next_rate,
                "apr": next_rate * 24 * 365 * 100
            }

        return funding_rates
    except Exception as e:
        print(f"Error fetching Pacifica funding rates: {e}")
        return {}

def main():
    symbols = ["BTC", "ETH", "SOL", "DOGE", "ONDO"]

    print("Fetching predicted funding rates from public APIs...\n")

    # Fetch from both exchanges
    hl_rates = fetch_hyperliquid_predicted_funding(symbols)
    pac_rates = fetch_pacifica_funding(symbols)

    # Display results
    print("=" * 100)
    print(f"{'Symbol':<10} {'Hyperliquid APR':>15} {'Pacifica APR':>15} {'Net Spread':>15} {'Strategy':<30}")
    print("-" * 100)

    results = []
    for symbol in symbols:
        hl_data = hl_rates.get(symbol)
        pac_data = pac_rates.get(symbol)

        if not hl_data or not pac_data:
            continue

        hl_apr = hl_data["apr"]
        pac_apr = pac_data["apr"]
        net_spread = abs(hl_apr - pac_apr)

        long_exch = "Hyperliquid" if hl_apr < pac_apr else "Pacifica"
        short_exch = "Pacifica" if long_exch == "Hyperliquid" else "Hyperliquid"
        strategy = f"LONG {long_exch[:2]}, SHORT {short_exch[:2]}"

        results.append({
            "symbol": symbol,
            "hl_apr": hl_apr,
            "pac_apr": pac_apr,
            "net_spread": net_spread,
            "strategy": strategy
        })

    # Sort by net spread descending
    results.sort(key=lambda x: x["net_spread"], reverse=True)

    for r in results:
        print(f"{r['symbol']:<10} {r['hl_apr']:>14.2f}% {r['pac_apr']:>14.2f}% "
              f"{r['net_spread']:>14.2f}% {r['strategy']:<30}")

    print("=" * 100)

    if results:
        best = results[0]
        print(f"\n✓ Best opportunity: {best['symbol']} ({best['net_spread']:.2f}% spread)")
        print(f"  Strategy: {best['strategy']}")

        # Show next funding time for best opportunity
        if best['symbol'] in hl_rates:
            next_time = hl_rates[best['symbol']]['next_funding_time']
            from datetime import datetime
            dt = datetime.fromtimestamp(next_time / 1000)
            print(f"  Next Hyperliquid funding: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    print("\nNote: Using PREDICTED/NEXT funding rates (forward-looking)")

if __name__ == "__main__":
    main()
