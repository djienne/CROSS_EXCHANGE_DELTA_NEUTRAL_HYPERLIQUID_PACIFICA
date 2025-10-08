# Docker Deployment Guide

This guide explains how to run the Hyperliquid-Pacifica arbitrage bot using Docker.

## Prerequisites

- Docker installed ([Get Docker](https://docs.docker.com/get-docker/))
- Docker Compose installed (usually comes with Docker Desktop)
- `.env` file with your API credentials (see `.env.example`)

## Quick Start

1. **Clone/copy the repository** to your target machine

2. **Create your `.env` file** from the example:
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

3. **Build and run** the bot:
   ```bash
   docker-compose up -d
   ```

The bot will start running in the background (detached mode).

## Docker Commands

### Start the bot
```bash
docker-compose up -d
```

### View logs (live)
```bash
docker-compose logs -f hedge-bot
```

### View logs (last 100 lines)
```bash
docker-compose logs --tail=100 hedge-bot
```

### Stop the bot
```bash
docker-compose down
```

### Restart the bot
```bash
docker-compose restart
```

### Rebuild after code changes
```bash
docker-compose up -d --build
```

### Check bot status
```bash
docker-compose ps
```

## Persistent Data

The entire project directory is mounted as a volume (`./:/app/`), which means:

- All files are accessible inside the container
- Changes to files on the host are immediately reflected in the container
- Data persists across container restarts

Key files that persist:
- `./logs/` - Bot log files (including `hyperliquid_pacifica_hedge.log` and `connector_debug.log`)
- `./bot_state_hl_pacifica.json` - Bot state (positions, cycles, etc.)
- `./bot_config.json` - Configuration (can be edited while running)
- `./emergency_close.py` - Emergency position closer (accessible via `docker-compose exec`)

## Configuration Changes

To change bot configuration:

1. Edit `bot_config.json` on the host machine
2. Restart the container:
   ```bash
   docker-compose restart
   ```

## Environment Variables

All credentials are loaded from `.env` file:
- `HL_WALLET` - Hyperliquid wallet address
- `HL_PRIVATE_KEY` - Hyperliquid private key
- `SOL_WALLET` - Pacifica/Solana wallet address
- `API_PUBLIC` - Pacifica API public key
- `API_PRIVATE` - Pacifica API private key

## Accessing Logs

### View log file directly
```bash
# On host machine
tail -f logs/hyperliquid_pacifica_hedge.log

# Or connector debug log
tail -f connector_debug.log
```

### View Docker container logs
```bash
docker-compose logs -f hedge-bot
```

## Emergency Position Closer

If you need to close positions immediately (e.g., bot malfunction, market conditions), use the emergency closer:

### Interactive mode (recommended)
```bash
docker-compose exec hedge-bot python emergency_close.py
```

This will:
- Scan all symbols from `bot_config.json`
- Display open positions with PnL
- Ask for confirmation before closing

### Force close without confirmation
```bash
docker-compose exec hedge-bot python emergency_close.py --force
```

### Preview positions without closing
```bash
docker-compose exec hedge-bot python emergency_close.py --dry-run
```

### Close specific symbol only
```bash
docker-compose exec hedge-bot python emergency_close.py --symbol BTC
```

**Note:** After emergency closing, you may need to manually reset the bot state to IDLE in `bot_state_hl_pacifica.json`

## Running Tests

To run tests inside the Docker container:

```bash
# Run all tests
docker-compose exec hedge-bot python -m pytest test/

# Run specific test
docker-compose exec hedge-bot python test/test_hyperliquid_balance.py
```

## Monitoring

Check if the bot is running:
```bash
docker-compose ps
```

Expected output:
```
NAME                       STATUS
hyperliquid-pacifica-bot   Up 5 minutes (healthy)
```

## Troubleshooting

### Bot immediately exits
Check logs for errors:
```bash
docker-compose logs hedge-bot
```

Common issues:
- Missing or invalid `.env` file
- Invalid API credentials
- Insufficient balance on exchanges
- No common symbols between exchanges (check logs for filtering messages)

### Positions stuck open or bot in ERROR state
1. Check current positions:
   ```bash
   docker-compose exec hedge-bot python emergency_close.py --dry-run
   ```
2. Close positions if needed:
   ```bash
   docker-compose exec hedge-bot python emergency_close.py
   ```
3. Reset bot state to IDLE in `bot_state_hl_pacifica.json`
4. Restart the container:
   ```bash
   docker-compose restart
   ```

### Container won't start
```bash
# Check for syntax errors in docker-compose.yml
docker-compose config

# Rebuild from scratch
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### View state file
```bash
cat bot_state_hl_pacifica.json
```

### View connector debug log
```bash
tail -f connector_debug.log
```

### Reset bot state
```bash
# Stop the bot
docker-compose down

# Delete state file
rm bot_state_hl_pacifica.json

# Restart
docker-compose up -d
```

## Deployment on Remote Server

### Copy files to server
```bash
# Using rsync (recommended - excludes logs and .env)
rsync -avz --exclude 'logs/' --exclude '.env' --exclude '__pycache__' \
  ./ user@server:/path/to/bot/

# Or using scp (copies everything)
scp -r ./ user@server:/path/to/bot/
```

### SSH into server and run
```bash
ssh user@server
cd /path/to/bot
cp .env.example .env
# Edit .env with your credentials
docker-compose up -d
```

**Note:** All scripts (including `emergency_close.py` and tests) will be available on the server since the entire directory is copied.

## Security Notes

1. **Never commit `.env` file** to version control
2. Keep your `.env` file permissions restricted:
   ```bash
   chmod 600 .env
   ```
3. Consider using Docker secrets for production deployments
4. Run container as non-root user (add `user: "1000:1000"` to docker-compose.yml)

## Resource Usage

The container is lightweight:
- Memory: ~100-200 MB
- CPU: Minimal (mostly idle waiting for funding periods)
- Disk: <100 MB for image + logs

## Updating

To update to a new version:

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose down
docker-compose up -d --build
```

## Multi-Instance Deployment

To run multiple bots with different configs:

1. Copy the directory for each instance
2. Create separate `docker-compose.yml` files with different container names
3. Use different state files via environment variables

Example:
```yaml
services:
  hedge-bot-btc:
    container_name: hedge-bot-btc
    volumes:
      - ./bot_state_btc.json:/app/bot_state_hl_pacifica.json
      - ./bot_config_btc.json:/app/bot_config.json
```
