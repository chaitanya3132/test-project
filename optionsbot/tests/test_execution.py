from datetime import date

from bot.execution import ExecutionEngine, PositionStateStore
from bot.models import Account, OptionLeg, Signal
from bot.monitoring import AlertManager


class FakeBroker:
    """In-memory broker double tracking orders and flatten calls."""

    def __init__(self, equity=100_000.0):
        self.equity = equity
        self.orders = {}
        self.flattened = False
        self.canceled_all = False
        self._next = 0

    def get_account(self):
        return Account(equity=self.equity, cash=self.equity, buying_power=self.equity)

    def submit_mleg_order(self, legs, qty, limit_price):
        self._next += 1
        oid = f"ord-{self._next}"
        self.orders[oid] = {"id": oid, "status": "accepted", "filled_qty": "0"}
        return self.orders[oid]

    def get_order(self, order_id):
        return self.orders[order_id]

    def list_open_orders(self):
        return [o for o in self.orders.values() if o["status"] == "accepted"]

    def cancel_all_orders(self):
        self.canceled_all = True

    def close_all_positions(self):
        self.flattened = True


def make_signal(qty=1, max_loss=400.0):
    return Signal(
        strategy="t", underlying="SPY",
        legs=(OptionLeg(symbol="SPY260717P00540000", side="sell"),
              OptionLeg(symbol="SPY260717P00535000", side="buy")),
        qty=qty, limit_price=-1.0, max_loss_per_contract=max_loss,
        expiry=date(2026, 7, 17),
    )


def make_engine(risk_manager, tmp_path, broker=None):
    broker = broker or FakeBroker()
    state = PositionStateStore(tmp_path / "positions.db")
    alerts = AlertManager()
    return ExecutionEngine(broker, risk_manager, state, alerts), broker, state, alerts


def test_approved_signal_is_routed_and_persisted(risk_manager, tmp_path):
    engine, broker, state, alerts = make_engine(risk_manager, tmp_path)
    order = engine.submit(make_signal(), quote_age_seconds=0.0)
    assert order is not None and order.order_id == "ord-1"
    assert state.open_orders()[0].underlying == "SPY"
    assert any(a["event"] == "order_submitted" for a in alerts.sent)


def test_rejected_signal_never_reaches_broker(risk_manager, tmp_path):
    engine, broker, state, alerts = make_engine(risk_manager, tmp_path)
    order = engine.submit(make_signal(), quote_age_seconds=999.0)  # stale data
    assert order is None
    assert broker.orders == {}
    assert any(a["event"] == "risk_reject" for a in alerts.sent)


def test_fill_polling_updates_state_and_alerts(risk_manager, tmp_path):
    engine, broker, state, alerts = make_engine(risk_manager, tmp_path)
    order = engine.submit(make_signal(), quote_age_seconds=0.0)
    broker.orders[order.order_id].update(status="filled", filled_qty="1",
                                         filled_avg_price="-1.02")
    assert engine.poll_fills() == 1
    assert state.open_orders() == []
    assert any(a["event"] == "fill" for a in alerts.sent)


def test_state_survives_restart_and_reconciles(risk_manager, tmp_path):
    engine, broker, state, _ = make_engine(risk_manager, tmp_path)
    order = engine.submit(make_signal(), quote_age_seconds=0.0)
    state.close()

    # "restart": new state store on the same file; broker canceled the order meanwhile
    broker.orders[order.order_id]["status"] = "canceled"
    state2 = PositionStateStore(tmp_path / "positions.db")
    assert state2.open_orders()  # persisted across the crash
    engine2 = ExecutionEngine(broker, risk_manager, state2, AlertManager())
    engine2.reconcile()
    assert state2.open_orders() == []


def test_kill_switch_flattens_everything(risk_manager, tmp_path):
    broker = FakeBroker(equity=100_000.0)
    engine, broker, state, alerts = make_engine(risk_manager, tmp_path, broker)
    engine.submit(make_signal(), quote_age_seconds=0.0)

    assert engine.enforce_kill_switch() is False  # records day start
    broker.equity = 90_000.0                      # -10% intraday
    assert engine.enforce_kill_switch() is True
    assert broker.flattened and broker.canceled_all
    assert state.live_orders() == []
    assert any(a["event"] == "flatten" for a in alerts.sent)
    # and no new orders are accepted afterwards
    assert engine.submit(make_signal(), quote_age_seconds=0.0) is None


def test_open_risk_feeds_the_risk_manager(risk_manager, tmp_path):
    engine, broker, state, _ = make_engine(risk_manager, tmp_path)
    engine.submit(make_signal(qty=3, max_loss=400.0), quote_age_seconds=0.0)
    risk = state.open_risk()
    assert len(risk) == 1
    assert risk[0].underlying == "SPY"
    assert risk[0].max_loss_dollars == 3 * 400.0
