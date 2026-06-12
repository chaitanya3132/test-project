from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.scheduler import in_trade_window, is_us_market_hours
from bot.scheduler.supervisor import compute_backoff, supervise

ET = ZoneInfo("America/New_York")


def et(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def test_market_hours_weekday_gate():
    assert is_us_market_hours(et(2026, 6, 12, 10, 0))      # Friday 10:00
    assert not is_us_market_hours(et(2026, 6, 13, 10, 0))  # Saturday
    assert not is_us_market_hours(et(2026, 6, 12, 9, 29))  # pre-open
    assert is_us_market_hours(et(2026, 6, 12, 9, 30))
    assert not is_us_market_hours(et(2026, 6, 12, 16, 0))  # at the close


def test_trade_window_is_inside_market_hours():
    assert in_trade_window(et(2026, 6, 12, 10, 0), "10:00", "15:30")
    assert not in_trade_window(et(2026, 6, 12, 9, 45), "10:00", "15:30")
    assert not in_trade_window(et(2026, 6, 12, 15, 30), "10:00", "15:30")
    assert not in_trade_window(et(2026, 6, 14, 12, 0), "10:00", "15:30")  # Sunday


def test_backoff_grows_and_caps():
    assert compute_backoff(1) == 5
    assert compute_backoff(2) == 10
    assert compute_backoff(3) == 20
    assert compute_backoff(20) == 300  # capped


def test_supervisor_restarts_on_crash_until_clean_exit():
    codes = iter([1, 1, 0])
    runs = []

    def fake_run(cmd):
        runs.append(cmd)
        return next(codes)

    sleeps = []
    code = supervise(cmd=("noop",), run=fake_run, sleep=sleeps.append)
    assert code == 0
    assert len(runs) == 3
    assert sleeps == [5.0, 10.0]


def test_supervisor_gives_up_after_max_restarts():
    code = supervise(cmd=("noop",), run=lambda c: 2,
                     sleep=lambda s: None, max_restarts=3)
    assert code == 2
