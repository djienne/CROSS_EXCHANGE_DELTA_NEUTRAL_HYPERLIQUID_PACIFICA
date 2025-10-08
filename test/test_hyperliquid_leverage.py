import sys
import os
# Add parent directory to path to allow imports from parent folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


from dotenv import load_dotenv
from hyperliquid_connector import HyperliquidConnector, setup_logger

def main():
    logger = setup_logger('LeverageTest', 'test_leverage.log')
    logger.info("Leverage test script started.")

    try:
        # --- Load Environment and Initialize Connector ---
        load_dotenv()
        wallet_address = os.environ.get("HL_WALLET")
        private_key = os.environ.get("HL_PRIVATE_KEY")
        if not wallet_address or not private_key:
            logger.error("Missing walletAddress or privateKey in .env file. Aborting.")
            return

        connector = HyperliquidConnector(wallet_address, private_key)

        # --- Define Test Logic ---
        coins_to_test = ["ETH", "BTC", "DOGE"]
        target_leverage = 3

        # --- Test 1: Get Current Leverage (Before Any Position) ---
        logger.info("=== Test 1: Getting Current Leverage (No Position) ===")
        for coin in coins_to_test:
            try:
                current_leverage = connector.get_leverage(coin)
                logger.info(f"{coin}: Current leverage = {current_leverage}x")
            except Exception as e:
                logger.error(f"Failed to get leverage for {coin}: {e}")

        # --- Test 2: Adjust Leverage ---
        logger.info("\n=== Test 2: Adjusting Leverage ===")
        for coin in coins_to_test:
            try:
                # The connector's update_leverage method contains all the logic
                # to check max leverage and use it if the target is too high.
                success = connector.update_leverage(coin, target_leverage=target_leverage, is_cross_margin=False)
                if success:
                    logger.info(f"{coin}: Leverage update request sent successfully")
                else:
                    logger.warning(f"{coin}: Leverage update failed")
            except Exception as e:
                logger.error(f"Error updating leverage for {coin}: {e}")

        # --- Test 3: Verify Leverage After Update (Still No Position) ---
        logger.info("\n=== Test 3: Verifying Leverage After Update (No Position) ===")
        for coin in coins_to_test:
            try:
                current_leverage = connector.get_leverage(coin)
                logger.info(f"{coin}: Leverage after update = {current_leverage}x")
                if current_leverage == target_leverage:
                    logger.info(f"{coin}: [OK] Leverage matches target")
                else:
                    logger.warning(f"{coin}: [WARNING] Leverage ({current_leverage}x) does not match target ({target_leverage}x) - This is expected without an open position")
            except Exception as e:
                logger.error(f"Failed to verify leverage for {coin}: {e}")

    except Exception as e:
        logger.exception("An unexpected error occurred in the leverage test script.")
    finally:
        logger.info("Leverage test script finished.")

if __name__ == "__main__":
    main()
