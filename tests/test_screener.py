import numpy as np
import pandas as pd

from investor import screener


def make_history(days=300):
    """Synthetic closes/volumes: a steady uptrend, downtrend, and flat line."""
    idx = pd.bdate_range(end="2026-06-11", periods=days)
    up = 100 * (1.002 ** np.arange(days))
    down = 100 * (0.998 ** np.arange(days))
    flat = 100 + 0.5 * np.sin(np.arange(days) / 5)  # wiggle so stdev > 0
    closes = pd.DataFrame({"UP": up, "DOWN": down, "FLAT": flat}, index=idx)
    volumes = pd.DataFrame(1_000_000, index=idx, columns=closes.columns)
    return closes, volumes


def test_metrics_computed_for_all_tickers():
    closes, volumes = make_history()
    m = screener.compute_metrics(closes, volumes)
    assert set(m.index) == {"UP", "DOWN", "FLAT"}
    assert m.loc["UP", "ret_6m"] > m.loc["DOWN", "ret_6m"]


def test_uptrend_outscores_downtrend():
    closes, volumes = make_history()
    scored = screener.score(screener.compute_metrics(closes, volumes))
    assert scored.loc["UP", "score"] > scored.loc["DOWN", "score"]
    assert scored.index[0] == "UP"
    assert (scored["score"] >= 0).all() and (scored["score"] <= 100).all()


def test_filters_drop_cheap_and_illiquid():
    closes, volumes = make_history()
    m = screener.compute_metrics(closes, volumes)
    filtered = screener.apply_filters(m, min_price=5.0, min_dollar_volume=10**12)
    assert filtered.empty  # nothing trades a trillion dollars a day
    kept = screener.apply_filters(m, min_price=5.0, min_dollar_volume=1.0)
    assert len(kept) == 3


def test_short_history_skipped():
    closes, volumes = make_history(days=50)
    m = screener.compute_metrics(closes, volumes)
    assert m.empty
