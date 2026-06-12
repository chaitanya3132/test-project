"""Configuration loading.

All knobs live in config.yaml. Credentials come from environment variables
ONLY (never the config file):

    APCA_API_KEY_ID / APCA_API_SECRET_KEY   Alpaca keys
    ANTHROPIC_API_KEY                       research_llm
    MONITOR_WEBHOOK_URL                     alerts (optional)
    BOT_ALLOW_LIVE=yes                      extra interlock for live mode
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config.yaml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_trade_pct: float = 1.5
    max_daily_loss_pct: float = 5.0
    defined_risk_only: bool = True
    max_concurrent_positions: int = 5
    max_exposure_per_underlying_pct: float = 3.0
    max_quote_staleness_seconds: int = 60


@dataclass(frozen=True)
class Config:
    mode: str
    risk: RiskConfig
    strategy: dict[str, Any]
    schedule: dict[str, Any]
    research: dict[str, Any]
    monitoring: dict[str, Any]
    options_feed: str
    state_dir: Path
    db_path: Path
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"


def load_config(path: Path | None = None, base_dir: Path | None = None) -> Config:
    cfg_path = path or DEFAULT_CONFIG_PATH
    with open(cfg_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    mode = str(raw.get("mode", "paper")).lower()
    if mode not in ("paper", "live"):
        raise ConfigError(f"mode must be 'paper' or 'live', got {mode!r}")
    if mode == "live" and os.environ.get("BOT_ALLOW_LIVE", "").lower() != "yes":
        raise ConfigError(
            "config requests live mode but BOT_ALLOW_LIVE=yes is not set in the "
            "environment. This double-opt-in prevents accidental live trading."
        )

    root = base_dir or cfg_path.parent
    paths = raw.get("paths", {})
    state_dir = root / paths.get("state_dir", "state")
    db_path = root / paths.get("db_path", "data/market.db")
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    risk_raw = raw.get("risk", {})
    risk = RiskConfig(
        max_risk_per_trade_pct=float(risk_raw.get("max_risk_per_trade_pct", 1.5)),
        max_daily_loss_pct=float(risk_raw.get("max_daily_loss_pct", 5.0)),
        defined_risk_only=bool(risk_raw.get("defined_risk_only", True)),
        max_concurrent_positions=int(risk_raw.get("max_concurrent_positions", 5)),
        max_exposure_per_underlying_pct=float(
            risk_raw.get("max_exposure_per_underlying_pct", 3.0)
        ),
        max_quote_staleness_seconds=int(risk_raw.get("max_quote_staleness_seconds", 60)),
    )

    return Config(
        mode=mode,
        risk=risk,
        strategy=raw.get("strategy", {}),
        schedule=raw.get("schedule", {}),
        research=raw.get("research", {}),
        monitoring=raw.get("monitoring", {}),
        options_feed=str(raw.get("broker", {}).get("options_feed", "indicative")),
        state_dir=state_dir,
        db_path=db_path,
        raw=raw,
    )


def alpaca_credentials() -> tuple[str, str]:
    """Read Alpaca keys from the environment; raise if missing."""
    key_id = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not key_id or not secret:
        raise ConfigError(
            "APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set in the environment."
        )
    return key_id, secret
