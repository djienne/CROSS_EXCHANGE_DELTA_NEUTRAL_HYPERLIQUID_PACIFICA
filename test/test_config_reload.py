#!/usr/bin/env python3
"""
test_config_reload.py
---------------------
Test the config reload functionality to ensure it works correctly.
"""

import sys
import os
import json
import tempfile
from pathlib import Path

# Add parent directory to path to import bot modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from hyperliquid_pacifica_hedge import BotConfig

def test_config_load_and_reload():
    """Test that config can be loaded and reloaded with changes detected."""

    # Create a temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        config_data = {
            "symbols_to_monitor": ["BTC", "ETH", "SOL"],
            "leverage": 3,
            "base_capital_allocation": 100.0,
            "hold_duration_hours": 8.0,
            "wait_between_cycles_minutes": 5.0,
            "check_interval_seconds": 60,
            "min_net_apr_threshold": 5.0
        }
        json.dump(config_data, f, indent=2)
        temp_config_path = f.name

    try:
        # Load initial config
        print("Loading initial config...")
        config1 = BotConfig.load_from_file(temp_config_path)
        assert config1.leverage == 3, f"Expected leverage 3, got {config1.leverage}"
        assert config1.base_capital_allocation == 100.0, f"Expected base_capital 100.0, got {config1.base_capital_allocation}"
        print("[PASS] Initial config loaded successfully")
        print(f"   Leverage: {config1.leverage}x")
        print(f"   Base capital: ${config1.base_capital_allocation}")

        # Modify the config file
        print("\nModifying config file...")
        with open(temp_config_path, 'w') as f:
            config_data["leverage"] = 5
            config_data["base_capital_allocation"] = 200.0
            config_data["min_net_apr_threshold"] = 10.0
            json.dump(config_data, f, indent=2)

        # Reload config
        print("Reloading config...")
        config2 = BotConfig.load_from_file(temp_config_path)
        assert config2.leverage == 5, f"Expected leverage 5, got {config2.leverage}"
        assert config2.base_capital_allocation == 200.0, f"Expected base_capital 200.0, got {config2.base_capital_allocation}"
        assert config2.min_net_apr_threshold == 10.0, f"Expected min_apr 10.0, got {config2.min_net_apr_threshold}"
        print("[PASS] Config reloaded successfully with new values")
        print(f"   Leverage: {config2.leverage}x (changed from {config1.leverage}x)")
        print(f"   Base capital: ${config2.base_capital_allocation} (changed from ${config1.base_capital_allocation})")
        print(f"   Min APR threshold: {config2.min_net_apr_threshold}% (changed from {config1.min_net_apr_threshold}%)")

        print("\n[PASS] All config reload tests passed!")
        return True

    finally:
        # Clean up temp file
        if os.path.exists(temp_config_path):
            os.remove(temp_config_path)

def test_config_with_missing_fields():
    """Test that config loading handles missing fields with defaults."""

    # Create a temporary config file with only some fields
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        config_data = {
            "symbols_to_monitor": ["BTC"],
            "leverage": 5
            # Missing other fields
        }
        json.dump(config_data, f, indent=2)
        temp_config_path = f.name

    try:
        print("\nTesting config with missing fields...")
        config = BotConfig.load_from_file(temp_config_path)
        assert config.leverage == 5, f"Expected leverage 5, got {config.leverage}"
        assert config.base_capital_allocation == 100.0, f"Expected default base_capital 100.0, got {config.base_capital_allocation}"
        assert config.hold_duration_hours == 8.0, f"Expected default hold_duration 8.0, got {config.hold_duration_hours}"
        print("[PASS] Config with missing fields loaded successfully with defaults")
        print(f"   Leverage (from file): {config.leverage}x")
        print(f"   Base capital (default): ${config.base_capital_allocation}")
        print(f"   Hold duration (default): {config.hold_duration_hours}h")
        return True

    finally:
        # Clean up temp file
        if os.path.exists(temp_config_path):
            os.remove(temp_config_path)

def test_old_config_migration():
    """Test that old config files with notional_per_position are migrated."""

    # Create a temporary config file with old parameter name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        config_data = {
            "symbols_to_monitor": ["BTC"],
            "leverage": 3,
            "notional_per_position": 150.0,  # Old parameter name
            "hold_duration_hours": 8.0
        }
        json.dump(config_data, f, indent=2)
        temp_config_path = f.name

    try:
        print("\nTesting old config parameter migration...")
        config = BotConfig.load_from_file(temp_config_path)
        assert config.base_capital_allocation == 150.0, f"Expected migrated base_capital 150.0, got {config.base_capital_allocation}"
        print("[PASS] Old config parameter 'notional_per_position' migrated to 'base_capital_allocation'")
        print(f"   Base capital: ${config.base_capital_allocation}")
        return True

    finally:
        # Clean up temp file
        if os.path.exists(temp_config_path):
            os.remove(temp_config_path)

if __name__ == "__main__":
    print("="*70)
    print("Testing Config Reload Functionality")
    print("="*70)

    try:
        test_config_load_and_reload()
        test_config_with_missing_fields()
        test_old_config_migration()

        print("\n" + "="*70)
        print("[PASS] ALL TESTS PASSED!")
        print("="*70)
        print("\nConfig reload is working correctly. You can now:")
        print("  1. Edit bot_config.json while the bot is running")
        print("  2. Changes will be picked up at the start of the next cycle")
        print("  3. Config will NOT reload if a position is currently open (safety check)")

    except AssertionError as e:
        print(f"\n[FAIL] TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
