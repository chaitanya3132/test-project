from datetime import date, datetime, timedelta, timezone

from bot.data import Store
from bot.models import NewsItem

from conftest import put_quote


def make_store(tmp_path):
    return Store(tmp_path / "test.db")


def test_bars_roundtrip_and_ordering(tmp_path):
    store = make_store(tmp_path)
    store.upsert_bar("SPY", date(2026, 6, 11), 1, 2, 0.5, 1.5, 100)
    store.upsert_bar("SPY", date(2026, 6, 10), 1, 2, 0.5, 1.2, 100)
    bars = store.get_bars("SPY")
    assert [b[0] for b in bars] == [date(2026, 6, 10), date(2026, 6, 11)]
    assert bars[1][4] == 1.5


def test_bar_upsert_overwrites_same_day(tmp_path):
    store = make_store(tmp_path)
    store.upsert_bar("SPY", date(2026, 6, 11), 1, 1, 1, 1.0, 0)
    store.upsert_bar("SPY", date(2026, 6, 11), 1, 1, 1, 2.0, 0)
    assert store.get_bars("SPY")[0][4] == 2.0


def test_option_snapshots_persist(tmp_path):
    store = make_store(tmp_path)
    store.insert_option_snapshots([
        put_quote("SPY", 540, 5.0, 5.2, -0.3),
        put_quote("SPY", 535, 4.0, 4.2, -0.25),
    ])
    assert store.count_option_snapshots("SPY") == 2


def test_news_dedupes_on_id(tmp_path):
    store = make_store(tmp_path)
    now = datetime.now(timezone.utc)
    item = NewsItem(id="n1", headline="h", summary="s", symbols=("SPY",),
                    source="x", ts=now)
    assert store.insert_news([item]) == 1
    assert store.insert_news([item]) == 0
    found = store.get_news_since(now - timedelta(hours=1))
    assert len(found) == 1 and found[0].symbols == ("SPY",)
