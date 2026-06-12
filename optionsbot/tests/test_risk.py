from datetime import date, datetime, timezone

from bot.models import OptionLeg, RiskDecision, Signal
from bot.risk.manager import OpenRisk, RiskState

NOW = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)


def make_signal(qty=10, max_loss=400.0, underlying="SPY", legs=None):
    legs = legs or (
        OptionLeg(symbol="SPY260717P00540000", side="sell"),
        OptionLeg(symbol="SPY260717P00535000", side="buy"),
    )
    return Signal(strategy="t", underlying=underlying, legs=legs, qty=qty,
                  limit_price=-1.0, max_loss_per_contract=max_loss,
                  expiry=date(2026, 7, 17))


def review(rm, signal, equity=100_000, open_risk=(), age=0.0):
    return rm.review(signal, equity=equity, open_risk=list(open_risk),
                     quote_age_seconds=age, now=NOW)


def test_sizes_down_to_per_trade_budget(risk_manager):
    # 1.5% of 100k = $1500 budget; $400 max loss -> 3 contracts
    d = review(risk_manager, make_signal(qty=10, max_loss=400.0))
    assert d.approved and d.qty == 3
    assert any("shrunk" in r for r in d.reasons)


def test_rejects_when_one_contract_exceeds_budget(risk_manager):
    d = review(risk_manager, make_signal(qty=1, max_loss=2000.0))
    assert not d.approved and d.qty == 0


def test_rejects_naked_short_options(risk_manager):
    naked = make_signal(legs=(OptionLeg(symbol="SPY260717P00540000", side="sell"),))
    d = review(risk_manager, naked)
    assert not d.approved
    assert any("naked" in r for r in d.reasons)
    # short put "covered" by a long CALL is still naked on the put side
    mismatched = make_signal(legs=(
        OptionLeg(symbol="SPY260717P00540000", side="sell"),
        OptionLeg(symbol="SPY260717C00560000", side="buy"),
    ))
    assert not review(risk_manager, mismatched).approved


def test_max_concurrent_positions(risk_manager):
    crowd = [OpenRisk("X", 100.0)] * 5
    d = review(risk_manager, make_signal(qty=1), open_risk=crowd)
    assert not d.approved
    assert any("concurrent" in r for r in d.reasons)


def test_per_underlying_exposure_cap(risk_manager):
    # cap = 3% of 100k = $3000; existing SPY risk 2800 -> no room for a $400 contract
    d = review(risk_manager, make_signal(qty=1, max_loss=400.0),
               open_risk=[OpenRisk("SPY", 2800.0)])
    assert not d.approved
    # other underlyings don't count against SPY's cap
    d = review(risk_manager, make_signal(qty=1, max_loss=400.0),
               open_risk=[OpenRisk("QQQ", 2800.0)])
    assert d.approved


def test_staleness_circuit_breaker(risk_manager):
    d = review(risk_manager, make_signal(qty=1), age=61.0)
    assert not d.approved
    assert any("stale" in r for r in d.reasons)


def test_daily_loss_kill_switch_halts_and_persists(risk_config, tmp_path):
    from bot.risk.manager import RiskManager

    state = RiskState(tmp_path / "s")
    rm = RiskManager(risk_config, state)
    assert rm.check_daily_loss(100_000, now=NOW) is False  # records day start
    assert rm.check_daily_loss(96_000, now=NOW) is False   # -4%: under the 5% limit
    assert rm.check_daily_loss(94_999, now=NOW) is True    # -5%+: fires

    # all subsequent reviews are rejected, even from a NEW manager (disk state)
    rm2 = RiskManager(risk_config, RiskState(tmp_path / "s"))
    d = review(rm2, make_signal(qty=1))
    assert not d.approved
    assert any("halt" in r for r in d.reasons)

    # the halt expires the next day
    next_day = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
    assert state.halted_reason(next_day.date()) is None


def test_decision_tokens_cannot_be_forged(risk_manager):
    signal = make_signal(qty=1)
    real = review(risk_manager, signal)
    assert risk_manager.verify(real)
    forged = RiskDecision(signal_id=signal.id, approved=True, qty=100,
                          reasons=("looks fine",), token="deadbeef")
    assert not risk_manager.verify(forged)
    # tampering with qty on a real decision also fails
    tampered = RiskDecision(signal_id=real.signal_id, approved=True,
                            qty=real.qty + 5, reasons=real.reasons, token=real.token)
    assert not risk_manager.verify(tampered)
