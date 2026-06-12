"""Momentum/trend screener over the US stock & ETF universe.

The scoring is deliberately simple and transparent:

  * 3-month and 6-month total return (momentum)
  * price vs 50-day and 200-day moving averages (trend)
  * distance below the 52-week high (room/risk balance)
  * a volatility penalty (annualized stdev of daily returns)

Each factor is converted to a cross-sectional percentile rank and combined
into a 0-100 composite. This surfaces candidates for YOUR research — it is
not a buy signal.
"""

import numpy as np
import pandas as pd

TRADING_DAYS_3M = 63
TRADING_DAYS_6M = 126

WEIGHTS = {
    "ret_3m": 0.30,
    "ret_6m": 0.25,
    "trend": 0.25,
    "near_high": 0.10,
    "low_vol": 0.10,
}


def compute_metrics(closes: pd.DataFrame, volumes: pd.DataFrame) -> pd.DataFrame:
    """Per-ticker metrics from ~1y of daily closes/volumes."""
    rows = []
    for t in closes.columns:
        px = closes[t].dropna()
        if len(px) < TRADING_DAYS_6M + 10:
            continue
        last = px.iloc[-1]
        ret_3m = last / px.iloc[-TRADING_DAYS_3M] - 1
        ret_6m = last / px.iloc[-TRADING_DAYS_6M] - 1
        sma50 = px.rolling(50).mean().iloc[-1]
        sma200 = px.rolling(200).mean().iloc[-1] if len(px) >= 200 else np.nan
        high_52w = px.max()
        daily = px.pct_change().dropna()
        vol_ann = daily.std() * np.sqrt(252)
        vols = volumes[t].dropna() if t in volumes else pd.Series(dtype=float)
        dollar_vol = (vols.tail(20) * px.tail(20)).mean() if len(vols) >= 20 else np.nan
        rows.append({
            "ticker": t,
            "price": last,
            "ret_3m": ret_3m,
            "ret_6m": ret_6m,
            "above_sma50": last / sma50 - 1 if sma50 else np.nan,
            "above_sma200": last / sma200 - 1 if not np.isnan(sma200) else np.nan,
            "pct_off_high": last / high_52w - 1,
            "vol_ann": vol_ann,
            "dollar_vol": dollar_vol,
        })
    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def apply_filters(m: pd.DataFrame, min_price: float, min_dollar_volume: float) -> pd.DataFrame:
    if m.empty:
        return m
    keep = (m["price"] >= min_price) & (m["dollar_vol"].fillna(0) >= min_dollar_volume)
    return m[keep]


def score(m: pd.DataFrame) -> pd.DataFrame:
    """Add a 0-100 composite score column based on cross-sectional ranks."""
    if m.empty:
        return m
    s = m.copy()
    pct = lambda col: col.rank(pct=True)  # noqa: E731
    trend = (s["above_sma50"].fillna(0) + s["above_sma200"].fillna(0)) / 2
    factors = {
        "ret_3m": pct(s["ret_3m"]),
        "ret_6m": pct(s["ret_6m"]),
        "trend": pct(trend),
        # closer to its 52w high ranks higher (pct_off_high is <= 0)
        "near_high": pct(s["pct_off_high"]),
        # lower volatility ranks higher
        "low_vol": pct(-s["vol_ann"]),
    }
    composite = sum(WEIGHTS[k] * v for k, v in factors.items())
    s["score"] = (composite * 100).round(1)
    return s.sort_values("score", ascending=False)


def run_screen(closes: pd.DataFrame, volumes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    m = compute_metrics(closes, volumes)
    m = apply_filters(
        m,
        min_price=float(cfg["universe"]["min_price"]),
        min_dollar_volume=float(cfg["universe"]["min_dollar_volume"]),
    )
    return score(m)
