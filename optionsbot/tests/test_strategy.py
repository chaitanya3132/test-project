from datetime import datetime, timezone

from bot.models import ResearchView
from bot.strategy import BullPutSpread, MarketSnapshot, build_strategies

from conftest import NOW, put_quote


def make_chain():
    """Strikes 525-545 with deltas; spot 560; ~35 DTE from NOW."""
    quotes = []
    deltas = {545: -0.38, 540: -0.30, 535: -0.24, 530: -0.19, 525: -0.15}
    prices = {545: 6.0, 540: 4.8, 535: 3.8, 530: 3.0, 525: 2.4}
    for strike, delta in deltas.items():
        mid = prices[strike]
        quotes.append(put_quote("SPY", strike, mid - 0.05, mid + 0.05, delta))
    return tuple(quotes)


def snapshot(research=None, open_underlyings=(), chain=None):
    return MarketSnapshot(
        ts=NOW,
        underlying_prices={"SPY": 560.0},
        chains={"SPY": chain if chain is not None else make_chain()},
        research=research,
        open_position_underlyings=tuple(open_underlyings),
    )


def strat(**kw):
    return BullPutSpread(underlying="SPY", dte_min=25, dte_max=45,
                         short_delta=0.30, width=5.0, min_credit_frac=0.15, **kw)


def test_picks_short_at_target_delta_and_width_below():
    signals = strat().generate(snapshot())
    assert len(signals) == 1
    s = signals[0]
    sell = next(l for l in s.legs if l.side == "sell")
    buy = next(l for l in s.legs if l.side == "buy")
    assert "00540000" in sell.symbol  # 0.30 delta strike
    assert "00535000" in buy.symbol   # width=5 below
    credit = 4.8 - 3.8
    assert s.limit_price == -credit   # negative = credit
    assert s.max_loss_per_contract == (5.0 - credit) * 100


def test_requires_minimum_credit():
    signals = strat().generate(snapshot())
    assert signals  # 1.00 credit on a 5 wide passes at 15%
    picky = BullPutSpread(underlying="SPY", dte_min=25, dte_max=45,
                          short_delta=0.30, width=5.0, min_credit_frac=0.5)
    assert picky.generate(snapshot()) == []  # needs 2.50 credit; only 1.00 available


def test_bear_regime_vetoes_entry():
    bear = ResearchView(regime="bear", flagged_events=(), confidence=0.9,
                        generated_at=datetime.now(timezone.utc))
    assert strat().generate(snapshot(research=bear)) == []
    # low-confidence bear does NOT veto
    meh = ResearchView(regime="bear", flagged_events=(), confidence=0.3,
                       generated_at=datetime.now(timezone.utc))
    assert strat().generate(snapshot(research=meh))
    # bull never vetoes
    bull = ResearchView(regime="bull", flagged_events=(), confidence=0.95,
                        generated_at=datetime.now(timezone.utc))
    assert strat().generate(snapshot(research=bull))


def test_no_stacking_on_same_underlying():
    assert strat().generate(snapshot(open_underlyings=("SPY",))) == []


def test_empty_chain_or_missing_price_is_safe():
    assert strat().generate(snapshot(chain=())) == []
    snap = MarketSnapshot(ts=NOW, underlying_prices={}, chains={})
    assert strat().generate(snap) == []


def test_build_strategies_from_config():
    strategies = build_strategies({"bull_put_spread": {"underlying": "QQQ", "width": 10}})
    assert len(strategies) == 1
    assert strategies[0].underlying == "QQQ"
    assert strategies[0].width == 10
