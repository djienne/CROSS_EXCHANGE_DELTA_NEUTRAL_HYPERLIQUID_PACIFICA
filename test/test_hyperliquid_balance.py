"""
Test script to get the available balance from Hyperliquid.
"""
import sys
import os
# Add parent directory to path to allow imports from parent folder
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
from hyperliquid_connector import HyperliquidConnector
from typing import Tuple

def get_hl_balance(client: HyperliquidConnector) -> Tuple[float, float]:
    """
    Fetches and returns the total and available balance from Hyperliquid.

    Args:
        client: An initialized HyperliquidConnector instance.

    Returns:
        A tuple containing the total balance and available balance.
        Example structure: (1500.75, 1250.50)
    """
    print("Fetching Hyperliquid balance...")
    try:
        total_balance, available_balance = client.get_balance()
        return total_balance, available_balance
    except Exception as e:
        print(f"Error fetching available balance: {e}")
        raise

def main():
    """
    Main function to get and display the available balance.
    """
    # Load environment variables from .env file
    load_dotenv()

    hl_wallet = os.getenv("HL_WALLET")

    if not hl_wallet:
        print("Error: Missing required environment variable: HL_WALLET")
        return

    # Initialize HyperliquidConnector
    try:
        client = HyperliquidConnector(
            wallet_address=hl_wallet
        )
    except Exception as e:
        print(f"Error initializing HyperliquidConnector: {e}")
        return

    # Get available balance
    try:
        total_balance_data, available_balance_data = get_hl_balance(client)
        print(f"\nTotal Balance: ${total_balance_data:.2f}")
        print(f"Available Balance: ${available_balance_data:.2f}")
        print(f"Explicit Total Balance: {total_balance_data}")
        print(f"Explicit Available Balance: {available_balance_data}")
    except Exception:
        # Error is already printed in the get_hl_balance function
        return

if __name__ == "__main__":
    main()

