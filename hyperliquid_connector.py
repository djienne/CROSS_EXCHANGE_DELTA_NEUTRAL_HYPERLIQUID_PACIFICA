
import os
import time
import logging
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
import json
import requests
from typing import Optional

import eth_account
from dotenv import load_dotenv

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from hyperliquid.utils.error import ClientError

# --- UTILITY FUNCTIONS ---

def setup_logger(name, log_file, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # File Handler
    fh = logging.FileHandler(log_file, mode='w')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console Handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger

# --- RATE LIMITING DECORATOR ---

def rate_limited(max_retries=5, initial_delay=1, backoff_factor=2):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # --- Throttle before the call ---
            current_time = time.time()
            elapsed = current_time - self.last_call_time
            if elapsed < self.throttle_delay:
                sleep_time = self.throttle_delay - elapsed
                self.logger.debug(f"Throttling: sleeping for {sleep_time:.2f}s")
                time.sleep(sleep_time)
            
            # --- Execute with retry logic ---
            retries = 0
            delay = initial_delay
            while retries < max_retries:
                try:
                    self.last_call_time = time.time()
                    return func(self, *args, **kwargs)
                except ClientError as e:
                    if e.code == 429: # Rate limit error
                        retries += 1
                        if retries >= max_retries:
                            self.logger.error(f"Rate limit error on {func.__name__} after {max_retries} retries. Giving up.")
                            raise
                        self.logger.warning(f"Rate limit error on {func.__name__}. Retrying in {delay}s... ({retries}/{max_retries})")
                        time.sleep(delay)
                        delay *= backoff_factor
                    else:
                        # Re-raise other client errors immediately
                        self.logger.error(f"ClientError on {func.__name__}: {e}")
                        raise
                except Exception as e:
                    self.logger.exception(f"An unexpected error occurred in {func.__name__}")
                    raise
            return None # Should not be reached
        return wrapper
    return decorator

# --- CONNECTOR CLASS ---

class HyperliquidConnector:
    def __init__(self, wallet_address, private_key=None, api_url=constants.MAINNET_API_URL):
        self.logger = setup_logger('HyperliquidConnector', 'connector_debug.log')
        self.logger.info(f"Initializing connector for wallet: {wallet_address}")

        self.wallet_address = wallet_address
        self.account = None
        self.exchange = None

        if private_key:
            self.account = eth_account.Account.from_key(private_key)
            # The Exchange class does not take skip_ws, it's handled by the underlying Info class.
            # We will close the websocket connection manually on shutdown.
            self.exchange = Exchange(self.account, api_url, account_address=wallet_address)
            self.info = self.exchange.info
        else:
            self.info = Info(api_url, skip_ws=True)

        self.last_call_time = 0
        self.throttle_delay = 0.2  # 5 calls per second

        self.logger.info("Fetching initial metadata...")
        self.meta = self._get_meta()
        self.coin_to_meta = {asset["name"]: asset for asset in self.meta["universe"]}
        self.logger.info("Connector initialized successfully.")

    @rate_limited()
    def _get_meta(self):
        return self.info.meta()

    @rate_limited()
    def get_mid_price(self, coin):
        return self.info.all_mids().get(coin)

    @rate_limited()
    def update_leverage(self, coin, target_leverage, is_cross_margin=True):
        if not self.exchange:
            self.logger.error("A private key is required to update leverage.")
            return False
        self.logger.info(f"--- Adjusting leverage for {coin} ---")
        try:
            asset_meta = self.coin_to_meta[coin]
            max_leverage = asset_meta["maxLeverage"]
            self.logger.info(f"Max leverage for {coin} is {max_leverage}x.")

            if target_leverage > max_leverage:
                self.logger.warning(f"Requested leverage {target_leverage}x is above max. Using {max_leverage}x instead.")
                target_leverage = max_leverage
            
            self.logger.info(f"Setting {coin} leverage to {target_leverage}x ({'Cross' if is_cross_margin else 'Isolated'})...")
            update_result = self.exchange.update_leverage(target_leverage, coin, is_cross_margin)
            
            if update_result["status"] == "ok":
                self.logger.info(f"Successfully sent leverage update request for {coin}.")
                return True
            else:
                self.logger.error(f"Failed to update leverage for {coin}. Response: {update_result}")
                return False
        except KeyError:
            self.logger.error(f"Could not find metadata for {coin}. Cannot update leverage.")
            return False

    @rate_limited()
    def market_open(self, coin, is_buy, notional_size_usd=None, size=None):
        if not self.exchange:
            self.logger.error("A private key is required to open a market order.")
            return None
        
        if size is None and notional_size_usd is None:
            raise ValueError("Either notional_size_usd or size must be provided.")
        if size is not None and notional_size_usd is not None:
            self.logger.warning("Both size and notional_size_usd provided to market_open. Preferring size.")

        self.logger.info(f"--- Opening market order for {coin} ---")
        try:
            asset_meta = self.coin_to_meta[coin]
            step_size = Decimal(str(10**-asset_meta["szDecimals"]))
            
            if size is not None:
                rounded_size = float(size)
            else:
                mid_price_str = self.get_mid_price(coin)
                if not mid_price_str:
                    self.logger.error(f"Could not get mid-price for {coin}. Aborting market open.")
                    return None

                mid_price = Decimal(mid_price_str)
                unrounded_size = Decimal(str(notional_size_usd)) / mid_price
                rounded_size = float(unrounded_size.quantize(step_size, rounding=ROUND_HALF_UP))

            self.logger.info(f"Attempting to market {'buy' if is_buy else 'sell'} {rounded_size} {coin}...")
            order_result = self.exchange.market_open(coin, is_buy, rounded_size, None, 0.05)
            
            if order_result["status"] == "ok":
                for status in order_result["response"]["data"]["statuses"]:
                    if "filled" in status:
                        fill_data = status["filled"]
                        self.logger.info(f'  -> SUCCESS: Filled {fill_data["totalSz"]} @{fill_data["avgPx"]}')
                        return fill_data
                    else:
                        self.logger.error(f'  -> ERROR on open: {status.get("error", "Unknown error")}')
            else:
                self.logger.error(f"  -> FAILED to place order. Full response: {order_result}")
            return None

        except KeyError:
            self.logger.error(f"Could not find metadata or mid-price for {coin}. Aborting market open.")
            return None

    def get_step_size(self, coin: str) -> Optional[Decimal]:
        """Gets the quantity step size for a given coin."""
        try:
            asset_meta = self.coin_to_meta[coin]
            return Decimal(str(10**-asset_meta["szDecimals"]))
        except KeyError:
            self.logger.error(f"Could not find metadata for {coin} to get step size.")
            return None

    def close(self):
        """Closes the websocket connection managed by the exchange object."""
        if self.exchange and hasattr(self.exchange, 'ws_manager') and self.exchange.ws_manager:
            try:
                self.logger.info("Closing Hyperliquid WebSocket connection...")
                self.exchange.ws_manager.end()
                self.logger.info("Hyperliquid WebSocket connection closed.")
            except Exception as e:
                self.logger.error(f"Error while closing Hyperliquid WebSocket: {e}")


    @rate_limited()
    def get_funding_rates(self):
        """
        Get current/historical funding rates (last applied).

        DEPRECATED: For arbitrage bots, use get_predicted_funding_rates() instead
        to get forward-looking rates that will be applied in the next funding period.
        """
        self.logger.debug("Fetching meta and asset contexts for funding rates...")
        meta_and_ctxs = self.info.meta_and_asset_ctxs()

        if not meta_and_ctxs or len(meta_and_ctxs) < 2:
            self.logger.error("Received invalid data from meta_and_asset_ctxs endpoint.")
            return {}

        universe = meta_and_ctxs[0].get("universe", [])
        asset_ctxs = meta_and_ctxs[1]

        if len(universe) != len(asset_ctxs):
            self.logger.warning("Mismatch between universe and asset_ctxs length. Funding data may be incomplete.")

        funding_rates = {}
        for i, asset_info in enumerate(universe):
            try:
                asset_name = asset_info["name"]
                funding_rate = asset_ctxs[i]["funding"]
                funding_rates[asset_name] = float(funding_rate)
            except (IndexError, KeyError) as e:
                self.logger.warning(f"Could not process funding for asset at index {i}. Error: {e}")

        self.logger.debug(f"Successfully processed {len(funding_rates)} funding rates.")
        return funding_rates

    @rate_limited()
    def get_predicted_funding_rates(self):
        """
        Get predicted/next funding rates (forward-looking).

        Returns a dict with asset names as keys and dicts containing:
        - 'funding_rate': The predicted funding rate for the next period
        - 'next_funding_time': Timestamp (ms) when this rate will be applied

        This should be used for arbitrage bots instead of get_funding_rates()
        because it shows what you will earn/pay during the next funding period.
        """
        self.logger.debug("Fetching predicted funding rates...")
        payload = {"type": "predictedFundings"}

        try:
            response = requests.post(f"{self.info.base_url}/info", json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.error(f"Failed to fetch predicted funding rates: {e}")
            return {}

        # Parse response: [[asset, [[venue, {fundingRate, nextFundingTime}]]]]
        predicted_rates = {}
        for asset_data in data:
            try:
                asset_name = asset_data[0]
                venues = asset_data[1]

                # Find HlPerp venue (Hyperliquid perpetuals)
                for venue_data in venues:
                    venue_name = venue_data[0]
                    if venue_name == "HlPerp":
                        venue_info = venue_data[1]
                        predicted_rates[asset_name] = {
                            "funding_rate": float(venue_info["fundingRate"]),
                            "next_funding_time": venue_info["nextFundingTime"]
                        }
                        break
            except (IndexError, KeyError, TypeError) as e:
                self.logger.warning(f"Could not process predicted funding for {asset_data}: {e}")

        self.logger.debug(f"Successfully processed {len(predicted_rates)} predicted funding rates.")
        return predicted_rates

    def get_user_state(self):
        """Fetches the user's state, including positions and margin summary."""
        payload = {"type": "clearinghouseState", "user": self.wallet_address}
        response = requests.post(f"{self.info.base_url}/info", json=payload)
        response.raise_for_status()
        user_state = response.json()
        self.logger.debug("--- Full user_state response from API ---")
        self.logger.debug(json.dumps(user_state, indent=2))
        self.logger.debug("------------------------------------------")
        return user_state

    def get_balance(self):
        """Gets the total and available margin in USD."""
        user_state = self.get_user_state()
        margin_summary = user_state.get("marginSummary", {})
        total_margin = float(margin_summary.get("accountValue", 0))
        available_margin = total_margin - float(margin_summary.get("totalMarginUsed", 0))
        return total_margin, available_margin

    def get_position(self, coin):
        """Gets position details for a specific coin."""
        user_state = self.get_user_state()
        asset_positions = user_state.get("assetPositions", [])
        for position in asset_positions:
            if position and position.get("position") and position["position"]["coin"].upper() == coin.upper():
                pos_info = position["position"]
                return {
                    "qty": float(pos_info["szi"]),
                    "entry_price": float(pos_info["entryPx"]),
                    "unrealized_pnl": float(pos_info["unrealizedPnl"]),
                    "notional": float(pos_info["positionValue"]),
                }
        return None

    def get_leverage(self, coin):
        """Gets the current leverage for a specific coin."""
        user_state = self.get_user_state()
        self.logger.debug(f"--- Full user_state for get_leverage ---\n{json.dumps(user_state, indent=2)}")

        # First check if there's an active position with leverage info
        asset_positions = user_state.get("assetPositions", [])
        for position in asset_positions:
            if position and position.get("position") and position["position"]["coin"].upper() == coin.upper():
                # Leverage is part of the position details in the user state
                return float(position["position"].get("leverage", {}).get("value", 1))

        # If no position exists, check for leverage mode settings (isolated leverage)
        # Hyperliquid stores leverage settings even without positions
        if "assetPositions" in user_state:
            for asset_data in asset_positions:
                if asset_data and asset_data.get("position", {}).get("coin", "").upper() == coin.upper():
                    leverage_info = asset_data.get("position", {}).get("leverage", {})
                    if leverage_info:
                        return float(leverage_info.get("value", 1))

        # Try to get from clearinghouse state if available
        # Note: Without an open position, leverage defaults to 1x in Hyperliquid
        # The leverage setting only applies when opening a new position
        self.logger.debug(f"No active position for {coin}. Leverage will be applied when position opens.")
        return 1.0  # Default leverage when no position exists

    @rate_limited()
    def market_close(self, coin):
        if not self.exchange:
            self.logger.error("A private key is required to close a market order.")
            return None
        self.logger.info(f"--- Closing market position for {coin} ---")
        close_result = self.exchange.market_close(coin)
        if close_result and close_result["status"] == "ok":
            for status in close_result["response"]["data"]["statuses"]:
                if "filled" in status:
                    fill_data = status["filled"]
                    self.logger.info(f'  -> SUCCESS: Closed {fill_data["totalSz"]} @{fill_data["avgPx"]}')
                    return fill_data
                else:
                    self.logger.warning(f'  -> INFO/ERROR on close: {status.get("error", "Unknown error")}')
        elif close_result:
            self.logger.error(f"  -> FAILED to close position. Response: {close_result}")
        else:
            self.logger.info(f"  -> INFO: No open position found for {coin} to close.")
        return None
