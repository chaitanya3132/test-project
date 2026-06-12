"""ExecutionEngine — the only module that talks to the broker's order API.

Design guarantees:
  * `submit()` is the single entry point for new orders, and it calls the
    RiskManager itself. There is no code path that places an order without a
    verified, HMAC-signed RiskDecision.
  * Order/position state is persisted to SQLite before and after broker
    calls, and `reconcile()` re-syncs from the broker on startup, so a crash
    can't orphan state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..broker_client import AlpacaClient, BrokerError
from ..models import OrderState, Signal
from ..monitoring.alerts import AlertManager
from ..risk.manager import RiskManager
from .state import PositionStateStore

log = logging.getLogger(__name__)


class ExecutionEngine:
    def __init__(self, broker: AlpacaClient, risk: RiskManager,
                 state: PositionStateStore, alerts: AlertManager) -> None:
        self.broker = broker
        self.risk = risk
        self.state = state
        self.alerts = alerts

    def submit(self, signal: Signal, quote_age_seconds: float) -> OrderState | None:
        """Review a signal with the RiskManager and route it if approved."""
        account = self.broker.get_account()
        decision = self.risk.review(
            signal,
            equity=account.equity,
            open_risk=self.state.open_risk(),
            quote_age_seconds=quote_age_seconds,
        )
        if not decision.approved or not self.risk.verify(decision):
            self.alerts.notify(
                "risk_reject",
                f"{signal.strategy} {signal.underlying}: {'; '.join(decision.reasons)}",
                level="warning",
            )
            return None

        try:
            resp = self.broker.submit_mleg_order(
                signal.legs, qty=decision.qty, limit_price=signal.limit_price
            )
        except BrokerError as exc:
            self.alerts.notify("order_error", f"order for {signal.underlying} failed: {exc}",
                               level="error")
            return None

        order = OrderState(
            order_id=str(resp["id"]),
            signal_id=signal.id,
            strategy=signal.strategy,
            underlying=signal.underlying,
            status=str(resp.get("status", "accepted")),
            qty=decision.qty,
            filled_qty=int(float(resp.get("filled_qty") or 0)),
            limit_price=signal.limit_price,
            max_loss_per_contract=signal.max_loss_per_contract,
            created_at=datetime.now(timezone.utc),
        )
        self.state.record_order(order)
        self.alerts.notify(
            "order_submitted",
            f"{signal.strategy}: {signal.rationale} x{decision.qty} "
            f"(order {order.order_id})",
        )
        return order

    def poll_fills(self) -> int:
        """Refresh status of open orders; alert on every fill. Returns fills."""
        fills = 0
        for order in self.state.open_orders():
            try:
                d = self.broker.get_order(order.order_id)
            except BrokerError as exc:
                log.warning("could not poll order %s: %s", order.order_id, exc)
                continue
            status = str(d.get("status", order.status))
            filled = int(float(d.get("filled_qty") or 0))
            if status != order.status or filled != order.filled_qty:
                self.state.update_status(order.order_id, status, filled)
            if status == "filled" and order.status != "filled":
                fills += 1
                self.alerts.notify(
                    "fill",
                    f"FILLED {order.strategy} {order.underlying} x{filled} "
                    f"@ {d.get('filled_avg_price', order.limit_price)}",
                )
        return fills

    def reconcile(self) -> None:
        """On restart: sync local open orders against the broker's view."""
        try:
            broker_open = {str(o["id"]) for o in self.broker.list_open_orders()}
        except BrokerError as exc:
            self.alerts.notify("error", f"reconcile failed: {exc}", level="error")
            return
        for order in self.state.open_orders():
            if order.order_id not in broker_open:
                try:
                    d = self.broker.get_order(order.order_id)
                    self.state.update_status(
                        order.order_id,
                        str(d.get("status", "canceled")),
                        int(float(d.get("filled_qty") or 0)),
                    )
                except BrokerError:
                    self.state.update_status(order.order_id, "canceled", order.filled_qty)
        log.info("reconciled state against broker (%d broker-open orders)", len(broker_open))

    def enforce_kill_switch(self) -> bool:
        """Check the daily-loss limit; flatten and halt if breached."""
        account = self.broker.get_account()
        if self.risk.check_daily_loss(account.equity):
            self.flatten_all("daily loss kill switch")
            return True
        return False

    def flatten_all(self, reason: str) -> None:
        """Cancel everything and liquidate all positions."""
        self.alerts.notify("flatten", f"FLATTENING ALL POSITIONS: {reason}", level="critical")
        try:
            self.broker.cancel_all_orders()
            self.broker.close_all_positions()
        except BrokerError as exc:
            self.alerts.notify("error", f"flatten encountered an error: {exc}", level="critical")
            raise
        self.state.mark_all_closed(reason)
