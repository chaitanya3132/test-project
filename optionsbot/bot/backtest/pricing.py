"""Black-Scholes pricing used to synthesize option chains for backtests."""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, vol: float,
             option_type: str, rate: float = 0.04) -> float:
    """European option price. Degenerate inputs collapse to intrinsic value."""
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if t_years <= 0 or vol <= 0:
        return intrinsic
    d1 = (math.log(spot / strike) + (rate + vol ** 2 / 2) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    return strike * math.exp(-rate * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(spot: float, strike: float, t_years: float, vol: float,
             option_type: str, rate: float = 0.04) -> float:
    if t_years <= 0 or vol <= 0:
        if option_type == "call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1 = (math.log(spot / strike) + (rate + vol ** 2 / 2) * t_years) / (vol * math.sqrt(t_years))
    return _norm_cdf(d1) if option_type == "call" else _norm_cdf(d1) - 1.0


def realized_vol(closes: list[float], window: int = 21) -> float:
    """Annualized realized volatility from trailing daily closes."""
    tail = closes[-(window + 1):]
    if len(tail) < 3:
        return 0.2
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    return max(0.05, math.sqrt(var * 252))
