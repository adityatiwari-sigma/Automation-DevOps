"""
test_config_loader.py — unit tests for config_loader.py

Covers:
  - Loading a valid config file
  - Error handling for missing config
  - Dot-notation get() accessor with various edge cases
  - Cache invalidation behaviour
"""

import os
import sys
import json
import pytest
import importlib
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Helpers ──────────────────────────────────────────────────

def _fresh_loader(config_path: str):
    """Return a config_loader module instance pointing at a specific file."""
    import importlib
    import config_loader as cl
    # Save and override the path, then reset the cache
    original_path = cl._CONFIG_PATH
    original_cfg  = cl._CONFIG
    cl._CONFIG_PATH = config_path
    cl._CONFIG      = None
    yield cl
    cl._CONFIG_PATH = original_path
    cl._CONFIG      = original_cfg


# ════════════════════════════════════════════════════════════
# LOADING
# ════════════════════════════════════════════════════════════

class TestConfigLoading:

    def test_load_returns_dict(self, tmp_config):
        """load() must return a Python dict."""
        import config_loader as cl
        # tmp_config fixture writes TEST_CONFIG → temp file; we point loader at it
        original, cl._CONFIG = cl._CONFIG, None
        original_path, cl._CONFIG_PATH = cl._CONFIG_PATH, tmp_config
        try:
            result = cl.load()
            assert isinstance(result, dict)
        finally:
            cl._CONFIG      = original
            cl._CONFIG_PATH = original_path

    def test_load_missing_file_raises(self, tmp_path):
        """load() must raise FileNotFoundError when config.json does not exist."""
        import config_loader as cl
        orig_path, orig_cfg = cl._CONFIG_PATH, cl._CONFIG
        cl._CONFIG_PATH = str(tmp_path / "does_not_exist.json")
        cl._CONFIG = None
        try:
            with pytest.raises(FileNotFoundError, match="config.json not found"):
                cl.load()
        finally:
            cl._CONFIG_PATH = orig_path
            cl._CONFIG      = orig_cfg

    def test_load_is_cached(self):
        """Second call to load() must return the same dict object (cached)."""
        import config_loader as cl
        first  = cl.load()
        second = cl.load()
        assert first is second

    def test_load_parses_all_top_level_keys(self, tmp_config):
        """Loaded config must contain all seven top-level sections."""
        import config_loader as cl
        orig_path, orig_cfg = cl._CONFIG_PATH, cl._CONFIG
        cl._CONFIG_PATH, cl._CONFIG = tmp_config, None
        try:
            cfg = cl.load()
            for key in ["network", "ports", "grafana", "email", "ssh", "redis", "webhook", "platform"]:
                assert key in cfg, f"Missing top-level key: {key}"
        finally:
            cl._CONFIG_PATH = orig_path
            cl._CONFIG      = orig_cfg


# ════════════════════════════════════════════════════════════
# DOT-NOTATION GET
# ════════════════════════════════════════════════════════════

class TestGetAccessor:

    def test_get_top_level_key(self):
        """get('network') must return the network sub-dict."""
        import config_loader as cl
        result = cl.get("network")
        assert isinstance(result, dict)
        assert "local_ip" in result

    def test_get_nested_key(self):
        """get('network.local_ip') must return '127.0.0.1' from TEST_CONFIG."""
        import config_loader as cl
        result = cl.get("network.local_ip")
        assert result == "127.0.0.1"

    def test_get_deeply_nested_key(self):
        """get('email.recipients.p1') must return the P1 email address."""
        import config_loader as cl
        result = cl.get("email.recipients.p1")
        assert result == "p1@example.com"

    def test_get_missing_key_returns_none(self):
        """get() with a non-existent key must return None by default."""
        import config_loader as cl
        result = cl.get("network.nonexistent_field")
        assert result is None

    def test_get_missing_key_returns_custom_default(self):
        """get() with a non-existent key and a default must return the default."""
        import config_loader as cl
        result = cl.get("network.nonexistent_field", default="fallback")
        assert result == "fallback"

    def test_get_missing_top_level_key_returns_default(self):
        """get() for a completely absent top-level key must return the default."""
        import config_loader as cl
        result = cl.get("does_not_exist", default=42)
        assert result == 42

    def test_get_integer_value(self):
        """get() must correctly return integer values."""
        import config_loader as cl
        result = cl.get("ports.webhook")
        assert result == 5051
        assert isinstance(result, int)

    def test_get_boolean_value(self):
        """get() must correctly return boolean values."""
        import config_loader as cl
        result = cl.get("webhook.auto_remediate")
        assert result is True

    def test_get_empty_string_value(self):
        """get() must return empty string when that is the configured value."""
        import config_loader as cl
        result = cl.get("slack.webhook_url")
        assert result == ""

    def test_get_intermediate_missing_returns_default(self):
        """get() must return default when an intermediate key is absent."""
        import config_loader as cl
        result = cl.get("network.missing.deep.key", default="safe")
        assert result == "safe"
