from datetime import date

import pandas as pd

from investor import portfolio

CFG = {
    "portfolio": {"benchmark": "SPY", "max_position_pct": 25, "drawdown_review_pct": 20},
}


def make_inputs():
    positions = pd.DataFrame({
        "ticker": ["BIG", "LOSER", "OK"],
        "shares": [10.0, 5.0, 2.0],
        "cost_basis": [100.0, 100.0, 100.0],
        "buy_date": [date(2026, 1, 5)] * 3,
    })
    prices = {"BIG": 150.0, "LOSER": 70.0, "OK": 105.0}
    idx = pd.bdate_range("2026-01-05", "2026-06-11")
    bench = pd.Series([100.0 + 0.05 * i for i in range(len(idx))], index=idx)  # gentle uptrend
    return positions, prices, bench


def test_totals_and_allocations():
    positions, prices, bench = make_inputs()
    result = portfolio.analyze(positions, prices, bench, CFG)
    df = result["positions"]
    assert result["total_value"] == 150 * 10 + 70 * 5 + 105 * 2
    assert abs(df["alloc_pct"].sum() - 100) < 1e-6


def test_concentration_and_drawdown_flags():
    positions, prices, bench = make_inputs()
    flags = portfolio.analyze(positions, prices, bench, CFG)["flags"]
    assert any("CONCENTRATION: BIG" in f for f in flags)
    assert any("DRAWDOWN: LOSER" in f for f in flags)
    assert not any("OK is" in f and "CONCENTRATION" in f for f in flags)


def test_missing_price_skipped():
    positions, prices, bench = make_inputs()
    del prices["OK"]
    result = portfolio.analyze(positions, prices, bench, CFG)
    assert set(result["positions"]["ticker"]) == {"BIG", "LOSER"}
