import sys
import os
# Add parent directory to path to allow imports from parent folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


import time
from dotenv import load_dotenv
from hyperliquid_connector import HyperliquidConnector, setup_logger

def main():
    logger = setup_logger('MarketOrderTest', 'test_market_order.log')
    logger.info("Market order test script started.")

    try:
        # --- Load Environment and Initialize Connector ---
        load_dotenv()
        wallet_address = os.environ.get("HL_WALLET")
        private_key = os.environ.get("HL_PRIVATE_KEY")
        if not wallet_address or not private_key:
            logger.error("Missing HL_WALLET or HL_PRIVATE_KEY in .env file. Aborting.")
            return
        
        connector = HyperliquidConnector(wallet_address, private_key)

        # --- Define Trading Logic ---
        coins_to_trade = ["ETH", "PAXG"]
        notional_per_trade = 20.0

        # --- Open Positions ---
        logger.info("=== Opening Market Positions ===")
        for coin in coins_to_trade:
            connector.market_open(coin, is_buy=True, notional_size_usd=notional_per_trade)

        # --- Wait ---
        wait_time = 5
        logger.info(f"\n=== Waiting for {wait_time} seconds ===")
        time.sleep(wait_time)

        # --- Close Positions ---
        logger.info("\n=== Closing Market Positions ===")
        for coin in coins_to_trade:
            connector.market_close(coin)

    except Exception as e:
        logger.exception("An unexpected error occurred in the market order test script.")
    finally:
        logger.info("Market order test script finished.")

if __name__ == "__main__":
    main()
