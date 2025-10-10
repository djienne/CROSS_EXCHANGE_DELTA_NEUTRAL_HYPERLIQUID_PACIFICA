# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a **delta-neutral funding rate arbitrage bot** that automatically captures funding rate spreads between Hyperliquid and Pacifica exchanges. The bot opens simultaneous long/short positions on both exchanges to collect funding payments while remaining market-neutral.

## Architecture

### Core Components

**Main Bot (`hyperliquid_pacifica_hedge.py`)**
- State machine with states: IDLE, ANALYZING, OPENING, HOLDING, CLOSING, WAITING, ERROR, SHUTDOWN
- Persistent state management via `bot_state_hl_pacifica.json`
- Configuration loaded from `bot_config.json`
- Main loop: analyze funding rates → open best position → hold for duration → close → wait → repeat

**Exchange Connectors**
- `hyperliquid_connector.py`: Wrapper around Hyperliquid SDK with rate limiting and error handling
- `pacifica_client.py`: Custom client for Pacifica DEX using Solana keypairs

**State Persistence**
- `StateManager` class handles JSON state file with atomic writes (temp file + os.replace)
- Tracks current position, cycle number (persistent across restarts), completed cycles, and cumulative stats
- Tracks `initial_capital` (total equity at first run) for long-term PnL calculation
- State recovery on startup by scanning both exchanges for existing positions

### Key Mechanisms

**Leverage System**
- Both exchanges MUST use identical leverage for delta-neutral safety
- Final leverage = `min(config_leverage, hl_max, pacifica_max, 20)` (20x hard cap enforced)
- Leverage is set on BOTH exchanges before opening any positions (lines 615-632)
- Warnings logged when leverage is reduced due to limits (lines 603-611)
- 20x hard cap at line 595: `MAX_ALLOWED_LEVERAGE = 20`

**Position Sizing**
- `base_capital_allocation` in config is BASE CAPITAL (not leveraged position size)
- 2% safety buffer applied automatically: `safe_base_capital = base_capital_allocation × 0.98`
- Position notional = `safe_base_capital × final_leverage` (lines 640-644)
- Further reduced if insufficient margin available (95% of max available)
- Example: $100 base at 3x leverage → $98 × 3 = $294 position on each exchange

**Stop-Loss Calculation** (lines 274-308)
- Dynamic based on leverage to trigger at ~60% capital loss, leaving 40% buffer before liquidation
- Formula: `max(2.0, 60.0 / leverage)` for leverage > 5
- Examples:
  - 1x: -50%, 3x: -20%, 5x: -12%, 10x: -6%, 15x: -4%, 20x: -3%
- **Trigger based on worst leg PnL** (not total PnL) for better risk protection (lines 886-913)
- When triggered: Both positions closed immediately via market orders, PnL calculated, bot enters WAITING state
- Uses same closing logic as `emergency_close.py` (tested and verified)
- Checked during monitoring phase every `check_interval_seconds` (default 60s)

**Symbol Filtering** (lines 479-499)
- At startup, bot filters `symbols_to_monitor` to only those available on BOTH exchanges
- Logs removed symbols and exits if no common symbols remain
- Prevents attempting to trade unsupported pairs

**Quantity Synchronization** (lines 674-703)
- Uses coarser (larger) step size between both exchanges for quantity rounding
- Ensures identical quantity on both sides for true delta-neutral hedge
- Rounds DOWN using Decimal arithmetic to avoid rejection

**Funding Rate Decision** (lines 216-256)
- Fetches funding rates from both exchanges (hourly percentages)
- Converts to APR: `rate × 24 × 365 × 100`
- Goes LONG on exchange with lower funding rate, SHORT on higher
- Net APR = absolute difference between the two rates

**Funding Rates Table Display** (lines 258-323)
- `display_funding_rates_table()` shows formatted comparison of all funding rates
- Displays at bot startup (after state recovery, lines 700-709) and before opening position (line 775)
- Table includes: Symbol, Hyperliquid APR, Pacifica APR, Net Spread, Strategy
- Color-coded: Green for opportunities above threshold, gray for below
- Sorted by net APR descending, shows best opportunity in summary

**State Recovery** (lines 371-450)
- On startup, scans all configured symbols for existing positions
- Validates delta-neutral (sizes approximately opposite and equal within 5%)
- Recovers to HOLDING state if valid single position found
- Sets ERROR state if multiple positions or imbalanced positions detected

**Cycle Tracking**
- `current_cycle_number` increments on each position open (line 726)
- Persistent across restarts (stored in state file)
- Displayed in status output during monitoring (line 799)

## Configuration

**bot_config.json**
- `symbols_to_monitor`: List of symbols (automatically filtered to those on both exchanges)
- `leverage`: Target leverage (auto-reduced if exceeds exchange limits or 20x hard cap)
- `base_capital_allocation`: Base capital in USD (actual position = base × leverage × 0.98 safety buffer)
- `hold_duration_hours`: How long to hold position (default 12h)
- `min_net_apr_threshold`: Minimum net APR % to open position (default 5%)
- `check_interval_seconds`: Health check frequency during HOLDING (default 60s)
- `wait_between_cycles_minutes`: Wait after closing before next cycle (default 5min)

**Parameter Name Migration**
- Old configs with `notional_per_position` automatically migrate to `base_capital_allocation` (lines 119-121)

**Environment Variables** (`.env`)
- `HL_WALLET`: Hyperliquid wallet address
- `HL_PRIVATE_KEY`: Hyperliquid private key
- `SOL_WALLET`: Solana wallet address for Pacifica
- `API_PUBLIC`: Pacifica API public key
- `API_PRIVATE`: Pacifica API private key (base58 encoded)

## Commands

### Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot (standard)
python hyperliquid_pacifica_hedge.py

# With custom config/state files
python hyperliquid_pacifica_hedge.py --config-file custom_config.json --state-file custom_state.json
```

### Funding Rates Checker

```bash
# View funding rates for symbols in bot_config.json
python show_funding_rates.py

# Check specific symbols
python show_funding_rates.py --symbols BTC ETH SOL

# Use custom config file
python show_funding_rates.py --config my_config.json

# Set custom threshold for highlighting (default: 5.0%)
python show_funding_rates.py --threshold 10.0
```

The `show_funding_rates.py` script:
- Standalone utility to check funding rates without running the bot
- Displays formatted table with real-time rates from both exchanges
- Calculates net APR spread for each symbol
- Color-coded: Green for opportunities above threshold
- Sorted by best opportunities first
- Filters to symbols available on both exchanges

### Emergency Position Closer

```bash
# Interactive mode - scans symbols from bot_config.json, shows positions, asks confirmation
python emergency_close.py

# Close specific symbol only
python emergency_close.py --symbol BTC

# Close all without confirmation
python emergency_close.py --force

# Preview without executing
python emergency_close.py --dry-run

# Use custom config file
python emergency_close.py --config custom_config.json
```

The `emergency_close.py` script:
- Scans only symbols listed in `bot_config.json` (not all available symbols)
- Displays all open positions with side, quantity, and unrealized PnL
- Requires typing 'YES' to confirm before closing (unless `--force`)
- Provides colored output for easy readability
- Reports success/failure for each position closed

### Testing

Tests are in `test/` folder and work when run from either project root or test directory:

```bash
# Run all tests
pytest test/

# Run specific test file
python test/test_hyperliquid_balance.py
python test/test_pacifica_leverage.py

# Run from test directory
cd test && python test_hyperliquid_positions.py
```

Test files include `sys.path.insert()` to import from parent directory.

### Docker Deployment

```bash
# Build the image
docker-compose build

# Build and start
docker-compose up -d

# View logs
docker-compose logs -f hedge-bot

# Stop
docker-compose down

# Rebuild after code changes
docker-compose build && docker-compose up -d
```

See `DOCKER.md` for comprehensive deployment guide including:
- Persistent volume mounts for logs, state, and config
- Environment variable injection from `.env`
- Automatic restart on failure
- Multi-instance deployment patterns

### Logs

- Main bot log: `logs/hyperliquid_pacifica_hedge.log` (resets on each script start, mode='w' at line 59)
- Connector log: `connector_debug.log`
- View state: `cat bot_state_hl_pacifica.json`
- Docker logs: `docker-compose logs -f hedge-bot`

## Critical Safety Constraints

1. **Leverage Synchronization**: Both exchanges MUST use same leverage. Bot enforces this at lines 615-632.
2. **20x Hard Cap**: Never exceeds 20x leverage regardless of config or exchange limits (line 595: `MAX_ALLOWED_LEVERAGE = 20`)
3. **2% Safety Buffer**: Base capital automatically reduced by 2% before leverage multiplication (line 642)
4. **Position Size Limits**: Auto-reduces if insufficient margin, uses 95% of available (lines 647-663)
5. **Delta-Neutral Validation**: State recovery checks positions are opposite and equal within 5% (line 401)
6. **Single Position Limit**: Bot only manages one position at a time. Multiple positions trigger ERROR state (lines 389-392)
7. **Stop-Loss Buffer**: Dynamic stop-loss leaves ~40% buffer before liquidation (lines 274-308). **Triggered by worst leg PnL** to protect against one-sided losses

## Status Display

When in HOLDING state, comprehensive color-coded status shown every check interval (single log message, not multiple lines with timestamps):

```
Position Status: ASTER (Cycle #1)

Timing:
  Opened:       2025-10-08 13:41:53 UTC
  Target Close: 2025-10-08 21:41:53 UTC
  Time Left:    8.0 hours

Position Sizes:
  Hyperliquid: +49.0000 ASTER
  Pacifica:    -49.0000 ASTER
  Notional:     $294.00 (per exchange)

Account Balances:
  Hyperliquid: $153.20 (Available: $120.00)
  Pacifica:    $65.18 (Available: $31.92)
  Total Equity: $218.38
  Total PnL:    $+18.38 (+9.18%) (since start)

Leverage:
  Hyperliquid: 3.0x
  Pacifica:    3.0x

Funding Rates (APR):
  Hyperliquid: +10.95%
  Pacifica:    +66.74%
  Net Spread:  55.79%

Unrealized PnL:
  Hyperliquid: $+0.33
  Pacifica:    $-0.24
  Total PnL:   $+0.09

Risk Management:
  Stop-Loss:   -20.0% ($-19.62)
  Total PnL:    +0.09% ($+0.09)
  HL PnL:      $+0.33
  PA PnL:      $-0.24
  Worst Leg:   Pacifica ($-0.24, -2.45%)
  Distance to SL: $19.38 (98.8%)
```

## Common Development Patterns

**Adding New Exchange Methods**
- Add to respective connector class (`HyperliquidConnector` or `PacificaClient`)
- Use `@rate_limited` decorator for Hyperliquid methods (lines 45-82 in connector)
- Handle errors gracefully and log with appropriate level

**Modifying State Machine**
- State transitions use `state_mgr.set_state()` which auto-saves (line 181-182)
- Always update state BEFORE async operations that might fail
- Use `state_mgr.save()` after modifying nested state data (line 168-177)

**Config Changes**
- Update `BotConfig` dataclass (lines 86-94)
- Update defaults dict (lines 107-115)
- Add migration logic if renaming fields (lines 116-121 show example)
- Update `bot_config.json` with comment explaining new field

**Precision Handling**
- Use Decimal for all quantity/price calculations to avoid floating-point errors
- Round quantities DOWN with `ROUND_DOWN` (line 696)
- Get step sizes from both exchanges and use coarser one (lines 675-683)

**Status Display Modifications**
- Status output is consolidated into a single log message (lines 811-891) to avoid timestamp on every line
- Use color codes from `Colors` class (lines 41-50) for visual clarity
- Dynamic coloring based on values (e.g., green for profit, red for loss, time remaining colors)

## Key Code Locations

- **Funding rates fetch function**: Lines 216-256
- **Funding rates table display**: Lines 258-323
- **Funding table at startup**: Lines 700-709
- **Funding table before position open**: Line 775
- **20x leverage hard cap**: Line 595
- **Initial capital tracking**: Lines 525-537 (fetched at startup if missing)
- **Long-term PnL display**: Lines 858-864
- **Leverage setting and validation**: Lines 594-632
- **2% safety buffer application**: Lines 640-644
- **Position sizing calculation**: Lines 640-664
- **Stop-loss formula**: Lines 349-383 (updated line numbers)
- **Stop-loss check and trigger**: Lines 933-937 (calls close_position if triggered)
- **Worst leg PnL calculation**: Lines 886-899
- **State recovery**: Lines 371-450
- **Symbol filtering**: Lines 479-499
- **Quantity synchronization**: Lines 674-703
- **Position opening**: Lines 669-741
- **Position monitoring**: Lines 743-940
- **Position closing**: Lines 949-1010 (identical logic to emergency_close.py)
- **Status display (consolidated)**: Lines 811-931
- **Risk management display**: Lines 901-926
- **Config parameter migration**: Lines 119-121

## Emergency Procedures

**Use emergency_close.py to close positions**:
```bash
python emergency_close.py  # Interactive with confirmation
python emergency_close.py --force  # No confirmation
```

**If bot crashes during OPENING/CLOSING**:
1. Run `python emergency_close.py --dry-run` to see open positions
2. Run `python emergency_close.py` to close them (requires 'YES' confirmation)
3. Reset state: Edit `bot_state_hl_pacifica.json` to set `"state": "IDLE"` and `"current_position": null`
4. Restart bot

**If ERROR state persists**:
- Bot retries recovery every 5 minutes
- Check logs for specific error
- Use `python emergency_close.py` to safely close positions
- May need manual position cleanup if delta-neutral constraint violated (>5% imbalance)

## Important Notes

- Log file resets on every script start (mode='w' at line 59)
- Cycle counter persists across restarts via state file
- Bot exits if no common symbols found between exchanges (lines 494-496)
- Unicode symbols (✓, ✗) replaced with text ([FOUND], etc.) to avoid Windows console errors
- All timestamps use UTC with proper timezone awareness (lines 749-756)
- PnL calculation compares entry balance to current balance after closing (lines 943-954)
- Pacifica DOES support leverage setting via API (`/api/v1/account/leverage` endpoint)
- Old config files with `notional_per_position` automatically upgrade to `base_capital_allocation`
