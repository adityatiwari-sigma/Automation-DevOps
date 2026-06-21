"""Central config reader — all code imports this instead of reading .env or hardcoding values."""

import json
import os

_CONFIG = None
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load() -> dict:
    global _CONFIG
    if _CONFIG is None:
        if not os.path.exists(_CONFIG_PATH):
            raise FileNotFoundError(
                f"config.json not found at {_CONFIG_PATH}. "
                "Copy config.example.json to config.json and fill in your values."
            )
        with open(_CONFIG_PATH) as f:
            _CONFIG = json.load(f)
    return _CONFIG


def get(key_path: str, default=None):
    """Read a value using dot notation.  e.g.  get('email.from_address')"""
    cfg = load()
    val = cfg
    for part in key_path.split("."):
        if not isinstance(val, dict):
            return default
        val = val.get(part)
        if val is None:
            return default
    return val
