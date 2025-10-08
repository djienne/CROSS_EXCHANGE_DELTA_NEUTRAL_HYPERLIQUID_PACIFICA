import sys
import os
# Add parent directory to path to allow imports from parent folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

"""
Test script to get the current BTC position from Hyperliquid.
"""
from dotenv import load_dotenv
from hyperliquid_connector import HyperliquidConnector
from typing import Dict, Any, Optional, Tuple

def get_hl_position(client: HyperliquidConnector, symbol: str) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Fetches and returns the current position and quantity for a given symbol from Hyperliquid.
    
    Args:
        client: An initialized HyperliquidConnector instance.
        symbol: The symbol to fetch the position for (e.g., "BTC").
        
    Returns:
        A tuple containing the position details dictionary (or None) and the quantity.
        Example structure:
        (
            {
                "qty": 0.001,
                "entry_price": 65000.0,
                "unrealized_pnl": 15.0,
                "notional": 65.0
            },
            0.001
        )
    """
    print(f"Fetching {symbol} position from Hyperliquid...")
    try:
        position = client.get_position(symbol)
        if position:
            return position, position.get("qty", 0.0)
        return None, 0.0
    except Exception as e:
        print(f"Error fetching {symbol} position: {e}")
        raise

def main():
    """
    Main function to get and display the BTC position from Hyperliquid.
    """
    # Load environment variables from .env file
    load_dotenv()

    hl_wallet = os.getenv("HL_WALLET")
    hl_private_key = os.getenv("HL_PRIVATE_KEY")

    if not all([hl_wallet, hl_private_key]):
        print("Error: Missing required environment variables: HL_WALLET, HL_PRIVATE_KEY")
        return

    # Initialize HyperliquidConnector
    try:
        client = HyperliquidConnector(
            wallet_address=hl_wallet,
            private_key=hl_private_key
        )
    except Exception as e:
        print(f"Error initializing HyperliquidConnector: {e}")
        return

    # Get BTC position
    try:
        symbol_to_test = "BTC"
        position_data, position_qty = get_hl_position(client, symbol_to_test)

        print(f"\n{symbol_to_test} Position: {position_data}")

        # Assert that a position exists and has a non-zero quantity
        assert position_data is not None, f"No position found for {symbol_to_test}."
        
        print(f"Explicit Quantity from return: {position_qty}")
        assert position_qty != 0, f"Position for {symbol_to_test} has zero quantity."
        
        print(f"\nAssertion passed: {symbol_to_test} position exists and is not zero.")

    except Exception:
        # Error is already printed in the get_hl_position function
        return

if __name__ == "__main__":
    main()
