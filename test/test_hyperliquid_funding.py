import sys
import os
# Add parent directory to path to allow imports from parent folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


import json
from dotenv import load_dotenv
from hyperliquid_connector import HyperliquidConnector, setup_logger
from typing import List, Dict, Any

def get_funding_data(connector: HyperliquidConnector, symbols_to_monitor: List[str]) -> List[Dict[str, Any]]:
    """
    Fetches, processes, and sorts PREDICTED/NEXT funding rate data for a list of symbols.

    Uses forward-looking funding rates that will be applied in the next funding period,
    not historical rates that were already applied.

    Args:
        connector: An initialized HyperliquidConnector instance.
        symbols_to_monitor: A list of symbols to get funding data for.

    Returns:
        A sorted list of dictionaries, each containing symbol, hourly_rate, apr, and next_funding_time.
        Example structure:
        [
            {
                "symbol": "BTC",
                "hourly_rate": 0.0000125,
                "apr": 10.95,
                "next_funding_time": 1733958000000
            },
            ...
        ]
    """
    all_funding_rates = connector.get_predicted_funding_rates()

    if not all_funding_rates:
        raise RuntimeError("Could not retrieve any predicted funding rates.")

    results = []
    for symbol in symbols_to_monitor:
        if symbol in all_funding_rates:
            rate_data = all_funding_rates[symbol]
            hourly_rate = rate_data["funding_rate"]
            next_funding_time = rate_data["next_funding_time"]
            # APR = hourly_rate * 24 hours/day * 365 days/year * 100
            apr = hourly_rate * 24 * 365 * 100
            results.append({
                "symbol": symbol,
                "hourly_rate": hourly_rate,
                "apr": apr,
                "next_funding_time": next_funding_time
            })
        else:
            # Place symbols with no data at the bottom when sorting
            results.append({
                "symbol": symbol,
                "hourly_rate": None,
                "apr": -float('inf'),
                "next_funding_time": None
            })

    # Sort by APR, descending
    sorted_results = sorted(results, key=lambda x: x["apr"], reverse=True)
    return sorted_results

def main():
    logger = setup_logger('FundingFeeTest', 'test_hyperliquid_funding.log')
    logger.info("Funding fee test script started.")

    try:
        # --- Load Config File ---
        try:
            with open('bot_config.json', 'r') as f:
                config = json.load(f)
            symbols_to_monitor = config.get("symbols_to_monitor", [])
            if not symbols_to_monitor:
                logger.error("'symbols_to_monitor' is empty or not found in bot_config.json")
                return
            logger.info(f"Loaded {len(symbols_to_monitor)} symbols to monitor from config.")
        except FileNotFoundError:
            logger.error("bot_config.json not found. Aborting.")
            return
        except json.JSONDecodeError:
            logger.error("bot_config.json is not a valid JSON file. Aborting.")
            return

        # --- Load Environment and Initialize Connector ---
        load_dotenv()
        wallet_address = os.environ.get("HL_WALLET")
        private_key = os.environ.get("HL_PRIVATE_KEY")
        if not wallet_address or not private_key:
            logger.error("Missing HL_WALLET or HL_PRIVATE_KEY in .env file. Aborting.")
            return

        connector = HyperliquidConnector(wallet_address, private_key)

        # --- Get Predicted Funding Rates ---
        logger.info("=== Fetching Predicted/Next Funding Rates ===")
        funding_data = get_funding_data(connector, symbols_to_monitor)
        
        print(f"\nExplicitly returned data:\n{json.dumps(funding_data, indent=2)}")

        # --- Display Results for Monitored Symbols ---
        print("\n--- Funding Rate Report (Ranked by APR) ---")
        print(f"{'Symbol':<10} | {'Hourly Rate':>15} | {'Est. APR':>12}")
        print("|"*44)

        for res in funding_data:
            if res["hourly_rate"] is not None:
                print(f"{res['symbol']:<10} | {res['hourly_rate']:>15.8f} | {res['apr']:>11.2f}%")
            else:
                print(f"{res['symbol']:<10} | {'Not Found':>15} | {'N/A':>12}")

    except Exception as e:
        logger.exception("An unexpected error occurred in the funding fee test script.")
    finally:
        logger.info("Funding fee test script finished.")

if __name__ == "__main__":
    main()
