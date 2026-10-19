"""Dashboard PnL window aggregation (§5 panel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from moex.analytics.dashboard import pnl_windows

_MSK = timezone(timedelta(hours=3))


def _deal(closed_at: str, pnl: float) -> dict:
    return {"closed_at": closed_at, "pnl": pnl}


def test_windows_split_by_close_time():
    # Fixed "now": 2026-10-16 12:00 MSK → today started 2026-10-16 00:00 MSK.
    now = datetime(2026, 10, 16, 12, 0, tzinfo=_MSK)
    deals = [
        _deal("2026-10-16T09:30:00", 10.0),    # today
        _deal("2026-10-16T02:00:00", -5.0),    # today (early MSK)
        _deal("2026-10-12T10:00:00", 3.0),     # 4 days ago → week+month+all
        _deal("2026-09-20T10:00:00", -2.0),    # 26 days ago → month+all only
        _deal("2026-08-01T10:00:00", 100.0),   # all only
    ]
    out = pnl_windows(deals, now=now)

    assert out["day"]["pnl"] == 5.0
    assert out["day"]["deals"] == 2
    assert out["week"]["pnl"] == 8.0          # 10 - 5 + 3
    assert out["week"]["deals"] == 3
    assert out["month"]["pnl"] == 6.0         # + (-2)
    assert out["month"]["deals"] == 4
    assert out["all"]["pnl"] == 106.0
    assert out["all"]["deals"] == 5


def test_utc_close_times_convert_to_msk_boundary():
    # 2026-10-15T22:00 UTC == 2026-10-16 01:00 MSK → counts as *today*.
    now = datetime(2026, 10, 16, 12, 0, tzinfo=_MSK)
    deals = [_deal("2026-10-15T22:00:00", 7.0)]
    out = pnl_windows(deals, now=now)
    assert out["day"]["pnl"] == 7.0
    assert out["day"]["deals"] == 1


def test_unparsable_close_time_is_skipped():
    out = pnl_windows([_deal("", 5.0), _deal("not-a-date", 5.0)])
    assert out["all"]["deals"] == 0
    assert out["all"]["pnl"] == 0.0


def test_empty_input():
    out = pnl_windows([])
    for key in ("day", "week", "month", "all"):
        assert out[key] == {"pnl": 0.0, "deals": 0}
    assert out["windows"]["day"] == "сегодня (MSK)"


def test_pnl_rounded_to_kopecks():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=_MSK)
    deals = [_deal("2026-10-16T10:00:00", 1.0059)]
    out = pnl_windows(deals, now=now)
    assert out["all"]["pnl"] == 1.01
