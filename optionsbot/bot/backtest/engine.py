"""Backtest harness.

Replays stored daily bars, synthesizes option chains with Black-Scholes from
trailing realized volatility, and runs the SAME strategy objects through the
SAME RiskManager rules used live (per-trade budget, exposure caps, daily-loss
kill switch). Reports win rate, average win/loss, max drawdown, and Sharpe.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from ..config import RiskConfig
from ..data.store import Store
from ..models import (
    OPTION_MULTIPLIER,
    OptionContract,
    OptionQuote,
    Signal,
    occ_symbol,
)
from ..risk.manager import OpenRisk, RiskManager, RiskState
from ..strategy.base import MarketSnapshot
from ..strategy.vertical_spread import BullPutSpread
from .pricing import bs_delta, bs_price, realized_vol

WARMUP_DAYS = 30


@dataclass
class OpenTrade:
    underlying: str
    short_strike: float
    long_strike: float
    expiry: date
    qty: int
    entry_credit: float  # per share
    entry_date: date
    max_loss_per_contract: float
    profit_target_frac: float
    stop_mult: float


@dataclass
class ClosedTrade:
    underlying: str
    entry_date: date
    exit_date: date
    qty: int
    pnl: float  # dollars
    reason: str


@dataclass
class BacktestReport:
    trades: list[ClosedTrade]
    equity_curve: list[tuple[date, float]]
    initial_equity: float
    kill_switch_days: int = 0
    rejected_signals: int = 0
    metrics: dict[str, float] = field(default_factory=dict)

    def compute(self) -> "BacktestReport":
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        n = len(self.trades)
        eq = [v for _, v in self.equity_curve]
        peak, max_dd = eq[0] if eq else 0.0, 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)
        rets = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq)) if eq[i - 1] > 0]
        sharpe = 0.0
        if len(rets) > 2:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            std = var ** 0.5
            if std > 0:
                sharpe = mean / std * (252 ** 0.5)
        self.metrics = {
            "trades": float(n),
            "win_rate": (len(wins) / n) if n else 0.0,
            "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
            "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
            "total_pnl": sum(t.pnl for t in self.trades),
            "final_equity": eq[-1] if eq else self.initial_equity,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
        }
        return self

    def __str__(self) -> str:
        m = self.metrics or self.compute().metrics
        lines = [
            "=" * 56,
            "BACKTEST REPORT",
            "=" * 56,
            f"period            : {self.equity_curve[0][0]} -> {self.equity_curve[-1][0]}"
            if self.equity_curve else "period            : (no days)",
            f"initial equity    : ${self.initial_equity:,.2f}",
            f"final equity      : ${m['final_equity']:,.2f}",
            f"total P&L         : ${m['total_pnl']:,.2f}",
            f"trades closed     : {int(m['trades'])}",
            f"win rate          : {m['win_rate'] * 100:.1f}%",
            f"avg win           : ${m['avg_win']:,.2f}",
            f"avg loss          : ${m['avg_loss']:,.2f}",
            f"max drawdown      : {m['max_drawdown'] * 100:.2f}%",
            f"sharpe (daily ann): {m['sharpe']:.2f}",
            f"kill-switch days  : {self.kill_switch_days}",
            f"risk rejections   : {self.rejected_signals}",
            "=" * 56,
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(self, store: Store, strategy: BullPutSpread,
                 risk_config: RiskConfig, initial_equity: float = 100_000.0,
                 slippage_per_spread: float = 0.02,
                 state_dir: Path | None = None) -> None:
        self.store = store
        self.strategy = strategy
        self.risk_config = risk_config
        self.initial_equity = initial_equity
        self.slippage = slippage_per_spread
        risk_state_dir = state_dir or Path(tempfile.mkdtemp(prefix="backtest_risk_"))
        self.risk = RiskManager(risk_config, RiskState(risk_state_dir))

    # ----- synthetic chain -----

    def _make_chain(self, underlying: str, day: date, spot: float,
                    vol: float, ts: datetime) -> tuple[OptionQuote, ...]:
        target_dte = (self.strategy.dte_min + self.strategy.dte_max) // 2
        expiry = day + timedelta(days=target_dte)
        t_years = target_dte / 365.0
        quotes = []
        lo, hi = int(spot * 0.80), int(spot * 1.02)
        for strike in range(lo, hi + 1):
            mid = bs_price(spot, strike, t_years, vol, "put")
            if mid < 0.03:
                continue
            half = max(0.02, mid * 0.02)
            quotes.append(OptionQuote(
                contract=OptionContract(
                    symbol=occ_symbol(underlying, expiry, "put", strike),
                    underlying=underlying, expiry=expiry,
                    strike=float(strike), option_type="put",
                ),
                bid=round(mid - half, 2),
                ask=round(mid + half, 2),
                delta=bs_delta(spot, strike, t_years, vol, "put"),
                iv=vol,
                ts=ts,
            ))
        return tuple(quotes)

    # ----- trade valuation -----

    def _spread_value(self, trade: OpenTrade, day: date, spot: float, vol: float) -> float:
        """Cost per share to close the spread today."""
        t_years = max(0.0, (trade.expiry - day).days / 365.0)
        short_val = bs_price(spot, trade.short_strike, t_years, vol, "put")
        long_val = bs_price(spot, trade.long_strike, t_years, vol, "put")
        return short_val - long_val

    def _check_exit(self, trade: OpenTrade, day: date, value: float) -> str | None:
        if day >= trade.expiry:
            return "expiry"
        if value <= trade.entry_credit * (1 - trade.profit_target_frac):
            return "profit_target"
        if value - trade.entry_credit >= trade.stop_mult * trade.entry_credit:
            return "stop_loss"
        return None

    # ----- main loop -----

    def run(self) -> BacktestReport:
        bars = self.store.get_bars(self.strategy.underlying)
        if len(bars) <= WARMUP_DAYS:
            raise ValueError(
                f"need more than {WARMUP_DAYS} bars for {self.strategy.underlying}; "
                f"have {len(bars)} (run seed-data first)"
            )
        closes = [b[4] for b in bars]
        days = [b[0] for b in bars]

        equity = self.initial_equity
        realized = 0.0
        open_trades: list[OpenTrade] = []
        closed: list[ClosedTrade] = []
        curve: list[tuple[date, float]] = []
        kill_days = 0
        rejections = 0

        for i in range(WARMUP_DAYS, len(bars)):
            day, spot = days[i], closes[i]
            ts = datetime.combine(day, time(15, 0), tzinfo=timezone.utc)
            vol = realized_vol(closes[: i + 1])

            # record day-start equity for the kill switch
            self.risk.state.day_start_equity(day, equity)

            # manage open trades
            still_open: list[OpenTrade] = []
            for trade in open_trades:
                value = self._spread_value(trade, day, spot, vol)
                reason = self._check_exit(trade, day, value)
                if reason:
                    exit_value = (max(trade.short_strike - spot, 0)
                                  - max(trade.long_strike - spot, 0)
                                  if reason == "expiry" else value + self.slippage)
                    pnl = (trade.entry_credit - exit_value) * OPTION_MULTIPLIER * trade.qty
                    realized += pnl
                    closed.append(ClosedTrade(
                        underlying=trade.underlying, entry_date=trade.entry_date,
                        exit_date=day, qty=trade.qty, pnl=pnl, reason=reason,
                    ))
                else:
                    still_open.append(trade)
            open_trades = still_open

            # mark-to-market equity
            unrealized = sum(
                (t.entry_credit - self._spread_value(t, day, spot, vol))
                * OPTION_MULTIPLIER * t.qty
                for t in open_trades
            )
            equity = self.initial_equity + realized + unrealized

            # the SAME daily-loss kill switch as live trading
            if self.risk.check_daily_loss(equity, now=ts):
                kill_days += 1
                for trade in open_trades:
                    value = self._spread_value(trade, day, spot, vol) + self.slippage
                    pnl = (trade.entry_credit - value) * OPTION_MULTIPLIER * trade.qty
                    realized += pnl
                    closed.append(ClosedTrade(
                        underlying=trade.underlying, entry_date=trade.entry_date,
                        exit_date=day, qty=trade.qty, pnl=pnl, reason="kill_switch",
                    ))
                open_trades = []
                equity = self.initial_equity + realized
                curve.append((day, equity))
                continue

            # generate + risk-review new entries
            chain = self._make_chain(self.strategy.underlying, day, spot, vol, ts)
            snapshot = MarketSnapshot(
                ts=ts,
                underlying_prices={self.strategy.underlying: spot},
                chains={self.strategy.underlying: chain},
                research=None,
                open_position_underlyings=tuple(t.underlying for t in open_trades),
            )
            open_risk = [OpenRisk(t.underlying, t.max_loss_per_contract * t.qty)
                         for t in open_trades]
            for signal in self.strategy.generate(snapshot):
                decision = self.risk.review(
                    signal, equity=equity, open_risk=open_risk,
                    quote_age_seconds=0.0, now=ts,
                )
                if not decision.approved:
                    rejections += 1
                    continue
                credit = -signal.limit_price - self.slippage
                if credit <= 0:
                    continue
                open_trades.append(self._to_trade(signal, decision.qty, credit, day))

            curve.append((day, equity))

        return BacktestReport(
            trades=closed, equity_curve=curve, initial_equity=self.initial_equity,
            kill_switch_days=kill_days, rejected_signals=rejections,
        ).compute()

    def _to_trade(self, signal: Signal, qty: int, credit: float, day: date) -> OpenTrade:
        strikes = sorted(int(leg.symbol[-8:]) / 1000.0 for leg in signal.legs)
        return OpenTrade(
            underlying=signal.underlying,
            short_strike=strikes[1],
            long_strike=strikes[0],
            expiry=signal.expiry,
            qty=qty,
            entry_credit=credit,
            entry_date=day,
            max_loss_per_contract=signal.max_loss_per_contract,
            profit_target_frac=self.strategy.profit_target_frac,
            stop_mult=self.strategy.stop_loss_credit_mult,
        )
