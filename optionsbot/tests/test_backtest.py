import math
from datetime import date

import pytest

from bot.backtest import Backtester
from bot.backtest.pricing import bs_delta, bs_price, realized_vol
from bot.backtest.sample_data import generate_bars, seed_store
from bot.data import Store
from bot.strategy import BullPutSpread


def test_bs_price_basics():
    # deep ITM put ~ intrinsic; far OTM put ~ 0
    assert bs_price(100, 150, 0.1, 0.2, "put") == pytest.approx(50, rel=0.02)
    assert bs_price(100, 50, 0.1, 0.2, "put") < 0.01
    # put-call parity: C - P = S - K*exp(-rT)
    c = bs_price(100, 100, 0.5, 0.25, "call")
    p = bs_price(100, 100, 0.5, 0.25, "put")
    assert c - p == pytest.approx(100 - 100 * math.exp(-0.04 * 0.5), abs=1e-6)
    # expiry collapses to intrinsic
    assert bs_price(100, 105, 0.0, 0.25, "put") == 5.0


def test_bs_delta_signs_and_range():
    d = bs_delta(100, 95, 0.1, 0.25, "put")
    assert -1.0 < d < 0.0
    assert bs_delta(100, 95, 0.1, 0.25, "call") == pytest.approx(d + 1, abs=1e-9)


def test_realized_vol_reasonable():
    flat = [100.0] * 30
    assert realized_vol(flat) == pytest.approx(0.05)  # floored
    noisy = [100 * (1.01 if i % 2 else 0.99) ** i for i in range(30)]
    assert realized_vol(noisy) > 0.05


def test_sample_data_is_deterministic():
    a = generate_bars(seed=7)
    b = generate_bars(seed=7)
    assert a == b
    assert len(a) == 500
    assert all(low <= o and low <= c <= high for _, o, high, low, c, _ in a)


def test_backtest_runs_with_live_risk_rules(tmp_path, risk_config):
    store = Store(tmp_path / "bt.db")
    seed_store(store, "SPY")
    strategy = BullPutSpread(underlying="SPY", dte_min=25, dte_max=45,
                             short_delta=0.30, width=5.0, min_credit_frac=0.10)
    report = Backtester(store, strategy, risk_config,
                        initial_equity=100_000.0,
                        state_dir=tmp_path / "risk").run()

    m = report.metrics
    assert m["trades"] > 5                      # the strategy actually traded
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["avg_win"] >= 0 >= m["avg_loss"]
    assert 0.0 <= m["max_drawdown"] < 1.0
    assert len(report.equity_curve) > 400

    # per-trade risk rule held: no single closed trade lost more than the
    # 1.5% budget (plus slippage tolerance)
    budget = 100_000 * 0.015
    assert all(t.pnl >= -(budget * 1.1) for t in report.trades)

    # report renders
    text = str(report)
    assert "win rate" in text and "max drawdown" in text


def test_backtest_requires_data(tmp_path, risk_config):
    store = Store(tmp_path / "empty.db")
    strategy = BullPutSpread(underlying="SPY")
    with pytest.raises(ValueError, match="seed-data"):
        Backtester(store, strategy, risk_config, state_dir=tmp_path / "r").run()
