# Gemini CLI Project Summary

This document summarizes the work done on the `HL_test_API` project.

## Delta-Neutral Arbitrage Bot

A new, sophisticated delta-neutral funding rate arbitrage bot was created: `hyperliquid_pacifica_hedge.py`.

### Key Features:
- **Strategy**: Implements a delta-neutral funding rate arbitrage strategy between Hyperliquid and Pacifica exchanges.
- **Foundation**: The bot's architecture is based on the robust `cross_exchange_delta_neutral.py` script, adapting its state management, PnL tracking, and recovery logic.
- **Risk Management**: Several critical safety features were implemented to ensure stable and safe operation:
    - **Dynamic Stop-Loss**: A mandatory, adaptive stop-loss mechanism that automatically tightens the stop-loss percentage as leverage increases (e.g., -40% at 2x leverage, -25% at 3x, etc.). This is not configurable and is always active.
    - **Leverage Synchronization**: Before opening any position, the bot fetches the maximum allowed leverage from both exchanges and uses the minimum of the configured leverage and the exchange limits, ensuring it never exceeds the constraints of either platform.
    - **Capital-Aware Sizing**: The notional size of each position is determined by the available capital on the most constrained exchange, preventing failures due to insufficient funds.
    - **Coarse Tick Rounding**: To prevent position size mismatches, the trade quantity is calculated based on the coarser (larger) tick size of the two exchanges, ensuring the same rounded quantity is executed on both sides.

## Test Script Refactoring

The existing test scripts were significantly improved to be more modular and reusable:
- `test_pacifica_positions.py`
- `test_pacifica_balance.py`
- `test_hyperliquid_positions.py`
- `test_hyperliquid_balance.py`
- `test_hyperliquid_funding.py`

### Improvements:
- **Explicit Data Functions**: The core logic for fetching data (e.g., positions, balances, funding rates) was extracted into dedicated functions that return the data explicitly. This makes the data easily accessible for other scripts or future tests.
- **Code Clarity**: Added comments to the docstrings of these new functions to document the exact structure of the data they return, making them easier to use and understand.
