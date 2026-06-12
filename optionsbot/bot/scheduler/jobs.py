"""Job scheduling: pre-market research, intraday trading windows (US market
hours only), end-of-day review."""

from __future__ import annotations

import logging
import time as time_mod
from datetime import datetime, time, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..broker_client import AlpacaClient
from ..config import Config
from ..data import Store
from ..data import ingest
from ..execution import ExecutionEngine
from ..models import OptionQuote
from ..monitoring import AlertManager
from ..research_llm import MorningAnalyst, load_research_view
from ..strategy import MarketSnapshot, Strategy

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def is_us_market_hours(now: datetime) -> bool:
    """Regular US session: weekdays 9:30-16:00 ET (holidays come from the
    broker clock at runtime; this is the static gate)."""
    local = now.astimezone(ET)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_CLOSE


def in_trade_window(now: datetime, start: str, end: str) -> bool:
    if not is_us_market_hours(now):
        return False
    local = now.astimezone(ET).time()
    return _parse_hhmm(start) <= local < _parse_hhmm(end)


class TradingDayScheduler:
    """Drives one process-lifetime of the bot: premarket -> intraday -> EOD."""

    def __init__(self, config: Config, broker: AlpacaClient, store: Store,
                 engine: ExecutionEngine, strategies: list[Strategy],
                 alerts: AlertManager,
                 clock: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] = time_mod.sleep) -> None:
        self.config = config
        self.broker = broker
        self.store = store
        self.engine = engine
        self.strategies = strategies
        self.alerts = alerts
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        s = config.schedule
        self.trade_start = str(s.get("trade_window_start", "10:00"))
        self.trade_end = str(s.get("trade_window_end", "15:30"))
        self.cycle_minutes = int(s.get("cycle_minutes", 15))

    # ----- jobs -----

    def run_premarket(self) -> None:
        """Ingest news and run the LLM analyst. Failures are non-fatal."""
        underlyings = sorted({s.underlying for s in self.strategies
                              if hasattr(s, "underlying")})
        try:
            ingest.ingest_news(self.broker, self.store, underlyings or ["SPY"])
        except Exception as exc:  # noqa: BLE001
            self.alerts.notify("error", f"premarket news ingest failed: {exc}", level="error")

        if self.config.research.get("enabled", True):
            try:
                analyst = MorningAnalyst(
                    model=str(self.config.research.get("model", "claude-fable-5")),
                    state_dir=self.config.state_dir,
                    max_news_items=int(self.config.research.get("max_news_items", 40)),
                )
                view = analyst.analyze(ingest.recent_news(self.store))
                if view:
                    self.alerts.notify(
                        "research",
                        f"regime={view.regime} confidence={view.confidence:.2f} "
                        f"events={list(view.flagged_events)[:5]}",
                    )
            except Exception as exc:  # noqa: BLE001
                self.alerts.notify("error", f"research failed (continuing without a "
                                            f"view): {exc}", level="error")

    def run_intraday_cycle(self) -> None:
        """One strategy pass: refresh data, generate, risk-review, execute."""
        # Kill switch first; nothing else runs if it fires.
        if self.engine.enforce_kill_switch():
            return
        self.engine.poll_fills()

        now = self.clock()
        research = load_research_view(self.config.state_dir)
        open_underlyings = tuple({o.underlying for o in self.engine.state.live_orders()})

        prices: dict[str, float] = {}
        chains: dict[str, tuple[OptionQuote, ...]] = {}
        newest_quote: datetime | None = None
        for strat in self.strategies:
            u = getattr(strat, "underlying", None)
            if not u or u in prices:
                continue
            try:
                prices[u] = ingest.ingest_underlying_quote(self.broker, self.store, u)
                chain = self.broker.get_option_chain(
                    u,
                    expiry_gte=now.date(),
                )
                self.store.insert_option_snapshots(chain)
                chains[u] = tuple(chain)
                for q in chain:
                    if newest_quote is None or q.ts > newest_quote:
                        newest_quote = q.ts
            except Exception as exc:  # noqa: BLE001
                self.alerts.notify("error", f"data refresh failed for {u}: {exc}",
                                   level="error")

        quote_age = ((now - newest_quote).total_seconds()
                     if newest_quote else float("inf"))
        snapshot = MarketSnapshot(
            ts=now, underlying_prices=prices, chains=chains,
            research=research, open_position_underlyings=open_underlyings,
        )
        for strat in self.strategies:
            for signal in strat.generate(snapshot):
                self.engine.submit(signal, quote_age_seconds=quote_age)

    def run_eod(self) -> None:
        self.engine.poll_fills()
        orders = self.engine.state.all_orders()
        live = [o for o in orders if o.status == "filled"]
        self.alerts.notify(
            "eod_review",
            f"EOD: {len(orders)} orders total, {len(live)} filled positions held; "
            f"see state DB for detail.",
        )

    # ----- main loop -----

    def run_forever(self) -> None:
        """Loop: premarket once per day, intraday on a cadence, EOD once."""
        self.engine.reconcile()
        did_premarket: str | None = None
        did_eod: str | None = None
        while True:
            now = self.clock()
            local = now.astimezone(ET)
            day = local.date().isoformat()

            if (local.weekday() < 5 and did_premarket != day
                    and local.time() >= _parse_hhmm(
                        str(self.config.schedule.get("premarket_time", "08:30")))
                    and local.time() < MARKET_OPEN):
                self.run_premarket()
                did_premarket = day

            if in_trade_window(now, self.trade_start, self.trade_end):
                try:
                    if self.broker.market_is_open():
                        self.run_intraday_cycle()
                except Exception as exc:  # noqa: BLE001
                    self.alerts.notify("error", f"intraday cycle error: {exc}",
                                       level="error")
                self.sleep(self.cycle_minutes * 60)
                continue

            if (local.weekday() < 5 and did_eod != day
                    and local.time() >= _parse_hhmm(
                        str(self.config.schedule.get("eod_time", "16:15")))):
                self.run_eod()
                did_eod = day

            self.sleep(60)
