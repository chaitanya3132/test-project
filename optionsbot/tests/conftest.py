import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import RiskConfig  # noqa: E402
from bot.models import OptionContract, OptionQuote, occ_symbol  # noqa: E402
from bot.risk.manager import RiskManager, RiskState  # noqa: E402


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.delenv("MONITOR_WEBHOOK_URL", raising=False)


@pytest.fixture
def risk_config():
    return RiskConfig(
        max_risk_per_trade_pct=1.5,
        max_daily_loss_pct=5.0,
        defined_risk_only=True,
        max_concurrent_positions=5,
        max_exposure_per_underlying_pct=3.0,
        max_quote_staleness_seconds=60,
    )


@pytest.fixture
def risk_manager(risk_config, tmp_path):
    return RiskManager(risk_config, RiskState(tmp_path / "state"))


NOW = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 7, 17)


def put_quote(underlying: str, strike: float, bid: float, ask: float,
              delta: float | None, expiry: date = EXPIRY,
              ts: datetime = NOW) -> OptionQuote:
    return OptionQuote(
        contract=OptionContract(
            symbol=occ_symbol(underlying, expiry, "put", strike),
            underlying=underlying, expiry=expiry, strike=strike, option_type="put",
        ),
        bid=bid, ask=ask, delta=delta, iv=0.2, ts=ts,
    )
