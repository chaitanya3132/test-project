"""Portfolio tracker: P/L per position, benchmark comparison, risk flags."""

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

from .config import ROOT

log = logging.getLogger(__name__)

PORTFOLIO_CSV = ROOT / "portfolio.csv"


def load_positions(path: Path | None = None) -> pd.DataFrame:
    p = path or PORTFOLIO_CSV
    df = pd.read_csv(p, comment="#")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["shares"] = df["shares"].astype(float)
    df["cost_basis"] = df["cost_basis"].astype(float)
    df["buy_date"] = pd.to_datetime(df["buy_date"]).dt.date
    return df


def benchmark_return_since(bench_closes: pd.Series, since: datetime.date) -> float | None:
    """Benchmark total return from `since` (or first date after) to now."""
    px = bench_closes.dropna()
    if px.empty:
        return None
    idx = px.index.tz_localize(None) if px.index.tz is not None else px.index
    mask = idx.date >= since
    if not mask.any():
        return None
    start = px[mask].iloc[0]
    return px.iloc[-1] / start - 1


def analyze(positions: pd.DataFrame, prices: dict[str, float],
            bench_closes: pd.Series, cfg: dict) -> dict:
    """Compute per-position and portfolio-level stats plus risk flags."""
    rows = []
    for _, pos in positions.iterrows():
        t = pos["ticker"]
        price = prices.get(t)
        if price is None:
            log.warning("no price for %s; skipping", t)
            continue
        value = price * pos["shares"]
        cost = pos["cost_basis"] * pos["shares"]
        ret = price / pos["cost_basis"] - 1
        bench_ret = benchmark_return_since(bench_closes, pos["buy_date"])
        rows.append({
            "ticker": t,
            "shares": pos["shares"],
            "cost_basis": pos["cost_basis"],
            "price": price,
            "value": value,
            "pl_dollars": value - cost,
            "pl_pct": ret * 100,
            "bench_pct": bench_ret * 100 if bench_ret is not None else None,
            "vs_bench_pct": (ret - bench_ret) * 100 if bench_ret is not None else None,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"positions": df, "total_value": 0, "total_pl": 0, "flags": []}

    total_value = df["value"].sum()
    df["alloc_pct"] = df["value"] / total_value * 100

    flags = []
    max_pos = float(cfg["portfolio"]["max_position_pct"])
    drawdown = float(cfg["portfolio"]["drawdown_review_pct"])
    for _, r in df.iterrows():
        if r["alloc_pct"] > max_pos:
            flags.append(
                f"CONCENTRATION: {r['ticker']} is {r['alloc_pct']:.1f}% of the portfolio "
                f"(limit {max_pos:.0f}%) — consider trimming to reduce single-stock risk."
            )
        if r["pl_pct"] < -drawdown:
            flags.append(
                f"DRAWDOWN: {r['ticker']} is down {abs(r['pl_pct']):.1f}% from cost — "
                f"review the thesis; decide deliberately whether to hold, add, or exit."
            )
        if r["vs_bench_pct"] is not None and r["vs_bench_pct"] < -15:
            flags.append(
                f"LAGGARD: {r['ticker']} has underperformed {cfg['portfolio']['benchmark']} "
                f"by {abs(r['vs_bench_pct']):.1f}% since purchase."
            )

    return {
        "positions": df.sort_values("value", ascending=False),
        "total_value": total_value,
        "total_pl": df["pl_dollars"].sum(),
        "flags": flags,
    }


def run(cfg: dict) -> dict:
    positions = load_positions()
    tickers = positions["ticker"].unique().tolist()
    bench = str(cfg["portfolio"]["benchmark"])
    hist = yf.download(tickers + [bench], period="1y", interval="1d",
                       group_by="ticker", auto_adjust=True, progress=False)
    prices: dict[str, float] = {}
    for t in tickers:
        try:
            series = hist[t]["Close"] if isinstance(hist.columns, pd.MultiIndex) else hist["Close"]
            prices[t] = float(series.dropna().iloc[-1])
        except (KeyError, IndexError):
            continue
    bench_closes = hist[bench]["Close"] if isinstance(hist.columns, pd.MultiIndex) else hist["Close"]
    return analyze(positions, prices, bench_closes, cfg)
