"""Config loading. Paper mode is the only mode this code supports."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def load_config(path: Path | None = None) -> dict:
    cfg = json.loads((path or ROOT / "config.json").read_text())
    if cfg.get("mode") != "paper":
        # Live trading is deliberately not implemented. Enabling it needs a code
        # change reviewed with the account owner, not a config flip.
        raise RuntimeError("Only mode='paper' is supported.")
    return cfg


# Cloud environments reserve some names, so a key can also arrive under a TRADEBOT_ alias.
ALIASES = {"ANTHROPIC_API_KEY": "TRADEBOT_ANTHROPIC_KEY"}


def env(name: str) -> str | None:
    v = os.environ.get(name) or (os.environ.get(ALIASES[name]) if name in ALIASES else None)
    return v or None


def have_alpaca_keys() -> bool:
    return bool(env("ALPACA_API_KEY") and env("ALPACA_SECRET_KEY"))
