"""RiskManager — the only gate between strategy signals and the broker.

Every order must carry a RiskDecision token minted by this class; the
ExecutionEngine verifies the HMAC before routing, so no other module can
fabricate an approval. Limits enforced (from config):

  * max risk per trade (sizes down, rejects at zero)
  * max daily loss kill switch (flatten + halt for the calendar day)
  * defined-risk structures only (every short leg must be paired long)
  * max concurrent positions
  * max exposure per underlying
  * data-staleness circuit breaker

Halt state and day-start equity persist to disk so they survive restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import RiskConfig
from ..models import RiskDecision, Signal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenRisk:
    """Risk already on the books, supplied by the execution state."""

    underlying: str
    max_loss_dollars: float


class RiskState:
    """Persistent halt flag + day-start equity (JSON files under state_dir)."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._halt_file = self.state_dir / "halt.json"
        self._equity_file = self.state_dir / "day_equity.json"

    def halted_reason(self, today: date) -> str | None:
        if not self._halt_file.exists():
            return None
        try:
            d = json.loads(self._halt_file.read_text())
        except (json.JSONDecodeError, OSError):
            return "halt file unreadable (failing safe)"
        if d.get("permanent"):
            return str(d.get("reason", "halted"))
        if d.get("date") == today.isoformat():
            return str(d.get("reason", "halted"))
        return None  # stale daily halt from a previous day

    def set_halt(self, today: date, reason: str, permanent: bool = False) -> None:
        self._halt_file.write_text(json.dumps(
            {"date": today.isoformat(), "reason": reason, "permanent": permanent}
        ))

    def clear_halt(self) -> None:
        self._halt_file.unlink(missing_ok=True)

    def day_start_equity(self, today: date, current_equity: float) -> float:
        """Return today's opening equity, recording it on first call of the day."""
        if self._equity_file.exists():
            try:
                d = json.loads(self._equity_file.read_text())
                if d.get("date") == today.isoformat():
                    return float(d["equity"])
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                pass
        self._equity_file.write_text(json.dumps(
            {"date": today.isoformat(), "equity": current_equity}
        ))
        return current_equity


class RiskManager:
    def __init__(self, config: RiskConfig, state: RiskState) -> None:
        self.config = config
        self.state = state
        self._secret = secrets.token_bytes(32)  # per-process; decisions aren't transferable

    # ----- approval tokens -----

    def _sign(self, signal_id: str, qty: int) -> str:
        msg = f"{signal_id}:{qty}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def verify(self, decision: RiskDecision) -> bool:
        """True iff this decision was minted by this RiskManager instance."""
        if not decision.approved:
            return False
        expected = self._sign(decision.signal_id, decision.qty)
        return hmac.compare_digest(expected, decision.token)

    # ----- kill switch -----

    def check_daily_loss(self, current_equity: float,
                         now: datetime | None = None) -> bool:
        """Returns True when the daily-loss kill switch fires. Sets the halt
        flag; the caller (execution engine / scheduler) must flatten."""
        now = now or datetime.now(timezone.utc)
        today = now.date()
        start = self.state.day_start_equity(today, current_equity)
        if start <= 0:
            return False
        drawdown_pct = (start - current_equity) / start * 100
        if drawdown_pct >= self.config.max_daily_loss_pct:
            reason = (f"daily loss kill switch: equity {current_equity:.2f} is "
                      f"{drawdown_pct:.2f}% below day start {start:.2f} "
                      f"(limit {self.config.max_daily_loss_pct}%)")
            log.error(reason)
            self.state.set_halt(today, reason)
            return True
        return False

    # ----- pre-trade review -----

    def review(
        self,
        signal: Signal,
        equity: float,
        open_risk: list[OpenRisk],
        quote_age_seconds: float,
        now: datetime | None = None,
    ) -> RiskDecision:
        now = now or datetime.now(timezone.utc)
        today = now.date()
        reasons: list[str] = []

        def reject(reason: str) -> RiskDecision:
            log.warning("REJECT %s: %s", signal.id[:8], reason)
            return RiskDecision(signal_id=signal.id, approved=False, qty=0,
                                reasons=tuple(reasons + [reason]))

        halt = self.state.halted_reason(today)
        if halt:
            return reject(f"trading halted: {halt}")

        if quote_age_seconds > self.config.max_quote_staleness_seconds:
            return reject(
                f"stale data circuit breaker: quotes are {quote_age_seconds:.0f}s old "
                f"(limit {self.config.max_quote_staleness_seconds}s)"
            )

        if self.config.defined_risk_only and not self._is_defined_risk(signal):
            return reject("defined-risk only: structure has a naked short option")

        if not math.isfinite(signal.max_loss_per_contract) or signal.max_loss_per_contract <= 0:
            return reject("max loss per contract must be a positive finite number")

        if len(open_risk) >= self.config.max_concurrent_positions:
            return reject(
                f"max concurrent positions reached "
                f"({len(open_risk)}/{self.config.max_concurrent_positions})"
            )

        # Size to the per-trade risk budget; shrink rather than reject when possible.
        budget = equity * self.config.max_risk_per_trade_pct / 100
        qty = min(signal.qty, int(budget // signal.max_loss_per_contract))
        if qty < 1:
            return reject(
                f"per-trade risk budget ${budget:.2f} cannot cover one contract "
                f"(max loss ${signal.max_loss_per_contract:.2f})"
            )
        if qty < signal.qty:
            reasons.append(f"qty shrunk {signal.qty} -> {qty} to fit "
                           f"{self.config.max_risk_per_trade_pct}% per-trade budget")

        # Per-underlying exposure cap, counting the new trade.
        underlying_cap = equity * self.config.max_exposure_per_underlying_pct / 100
        existing = sum(r.max_loss_dollars for r in open_risk
                       if r.underlying == signal.underlying)
        proposed = existing + qty * signal.max_loss_per_contract
        if proposed > underlying_cap:
            fit = int((underlying_cap - existing) // signal.max_loss_per_contract)
            if fit < 1:
                return reject(
                    f"underlying exposure cap: {signal.underlying} already carries "
                    f"${existing:.2f} risk of ${underlying_cap:.2f} allowed"
                )
            reasons.append(f"qty shrunk {qty} -> {fit} to fit per-underlying cap")
            qty = fit

        reasons.append("approved")
        return RiskDecision(
            signal_id=signal.id, approved=True, qty=qty,
            reasons=tuple(reasons), token=self._sign(signal.id, qty),
        )

    @staticmethod
    def _is_defined_risk(signal: Signal) -> bool:
        """Every short contract must be covered by a long contract of the same
        option type (vertical/condor style pairing). OCC symbols encode the
        type at position -9 ('C'/'P')."""
        shorts: dict[str, int] = {}
        longs: dict[str, int] = {}
        for leg in signal.legs:
            opt_type = leg.symbol[-9].upper() if len(leg.symbol) >= 9 else "?"
            bucket = shorts if leg.side == "sell" else longs
            bucket[opt_type] = bucket.get(opt_type, 0) + leg.ratio
        return all(longs.get(t, 0) >= n for t, n in shorts.items())
