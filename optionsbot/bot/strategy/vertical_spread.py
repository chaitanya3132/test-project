"""Bull put credit spread — a defined-risk vertical.

Entry rules (all configurable, all deterministic):
  * expiry within [dte_min, dte_max], preferring the one closest to the middle
  * short put: |delta| closest to `short_delta` (fallback: ~5% OTM if no greeks)
  * long put: `width` dollars below the short strike
  * require net credit >= min_credit_frac * width
  * skip entirely if research regime == regime_veto with enough confidence
  * never stack a second position on the same underlying
"""

from __future__ import annotations

from datetime import date

from ..models import OPTION_MULTIPLIER, OptionLeg, OptionQuote, Signal
from .base import MarketSnapshot, Strategy


class BullPutSpread(Strategy):
    def __init__(
        self,
        underlying: str = "SPY",
        dte_min: int = 25,
        dte_max: int = 45,
        short_delta: float = 0.30,
        width: float = 5.0,
        min_credit_frac: float = 0.25,
        profit_target_frac: float = 0.50,
        stop_loss_credit_mult: float = 2.0,
        regime_veto: str = "bear",
        regime_confidence_min: float = 0.6,
    ) -> None:
        self.name = f"bull_put_spread:{underlying}"
        self.underlying = underlying
        self.dte_min = dte_min
        self.dte_max = dte_max
        self.short_delta = short_delta
        self.width = width
        self.min_credit_frac = min_credit_frac
        self.profit_target_frac = profit_target_frac
        self.stop_loss_credit_mult = stop_loss_credit_mult
        self.regime_veto = regime_veto
        self.regime_confidence_min = regime_confidence_min

    def generate(self, snapshot: MarketSnapshot) -> list[Signal]:
        if self.underlying in snapshot.open_position_underlyings:
            return []  # one position per underlying per strategy

        r = snapshot.research
        if (r is not None and r.regime == self.regime_veto
                and r.confidence >= self.regime_confidence_min):
            return []  # research veto: risk-reducing only

        spot = snapshot.underlying_prices.get(self.underlying)
        chain = snapshot.chains.get(self.underlying)
        if spot is None or not chain:
            return []

        today = snapshot.ts.date()
        puts = [q for q in chain
                if q.contract.option_type == "put"
                and self.dte_min <= (q.contract.expiry - today).days <= self.dte_max
                and q.bid > 0 and q.ask > q.bid]
        if not puts:
            return []

        expiry = self._pick_expiry(puts, today)
        exp_puts = {q.contract.strike: q for q in puts if q.contract.expiry == expiry}

        short_q = self._pick_short(exp_puts, spot)
        if short_q is None:
            return []
        long_strike = short_q.contract.strike - self.width
        long_q = exp_puts.get(long_strike)
        if long_q is None:
            return []

        credit = short_q.mid - long_q.mid
        if credit < self.min_credit_frac * self.width:
            return []

        credit = round(credit, 2)
        max_loss = (self.width - credit) * OPTION_MULTIPLIER
        if max_loss <= 0:
            return []

        return [Signal(
            strategy=self.name,
            underlying=self.underlying,
            legs=(
                OptionLeg(symbol=short_q.contract.symbol, side="sell"),
                OptionLeg(symbol=long_q.contract.symbol, side="buy"),
            ),
            qty=1,  # risk manager sizes up/down from here
            limit_price=-credit,  # negative = credit
            max_loss_per_contract=max_loss,
            expiry=expiry,
            rationale=(
                f"sell {short_q.contract.strike}P / buy {long_strike}P {expiry} "
                f"for {credit:.2f} credit (width {self.width})"
            ),
        )]

    def _pick_expiry(self, puts: list[OptionQuote], today: date) -> date:
        target = (self.dte_min + self.dte_max) / 2
        expiries = sorted({q.contract.expiry for q in puts})
        return min(expiries, key=lambda e: abs((e - today).days - target))

    def _pick_short(self, exp_puts: dict[float, OptionQuote], spot: float) -> OptionQuote | None:
        otm = [q for q in exp_puts.values() if q.contract.strike < spot]
        if not otm:
            return None
        with_delta = [q for q in otm if q.delta is not None]
        if with_delta:
            return min(with_delta, key=lambda q: abs(abs(q.delta or 0) - self.short_delta))
        # no greeks on the feed: fall back to ~5% out-of-the-money
        return min(otm, key=lambda q: abs(q.contract.strike - spot * 0.95))
