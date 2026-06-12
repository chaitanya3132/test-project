"""Deterministic synthetic daily bars for backtest demos and tests.

GBM with regime shifts (bull / bear / chop blocks) so the strategy sees
varied conditions. Seeded — same data every run.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from ..data.store import Store

REGIMES = [
    # (trading days, annual drift, annual vol)
    (120, 0.12, 0.13),   # bull
    (60, -0.25, 0.28),   # bear
    (90, 0.02, 0.16),    # chop
    (120, 0.15, 0.14),   # bull
    (60, -0.10, 0.22),   # pullback
    (50, 0.05, 0.15),    # drift
]


def generate_bars(symbol: str = "SPY", start: date = date(2024, 1, 2),
                  start_price: float = 470.0, seed: int = 7,
                  ) -> list[tuple[date, float, float, float, float, int]]:
    rng = random.Random(seed)
    rows: list[tuple[date, float, float, float, float, int]] = []
    d = start
    price = start_price
    for days, drift, vol in REGIMES:
        mu_d = drift / 252
        sd_d = vol / math.sqrt(252)
        for _ in range(days):
            while d.weekday() >= 5:
                d += timedelta(days=1)
            ret = rng.gauss(mu_d, sd_d)
            open_ = price
            close = price * math.exp(ret)
            high = max(open_, close) * (1 + abs(rng.gauss(0, sd_d / 3)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, sd_d / 3)))
            rows.append((d, round(open_, 2), round(high, 2), round(low, 2),
                         round(close, 2), rng.randint(40, 90) * 10**6))
            price = close
            d += timedelta(days=1)
    return rows


def seed_store(store: Store, symbol: str = "SPY", seed: int = 7) -> int:
    rows = generate_bars(symbol=symbol, seed=seed)
    store.upsert_bars(symbol, rows)
    return len(rows)
