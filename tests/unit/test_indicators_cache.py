"""Indicator candle cache freshness (regression: stale bars all session).

Live bug: the cache file is named after *today*, so it is written on the
first call of the day — 08:11 on Saturday 2026-10-17, before the 09:50 ДСВД
session — and reused until midnight. Every RSI/MACD/SMA the analyst saw
lacked the current bar, and the report printed "N/A: Not a trading day
(weekend or holiday)" for a day the exchange was trading (SBER had a candle
for 2026-10-17).

No network: ``get_candles`` is stubbed (spec §14.4).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import pytest

from moex.market_data import indicators as ind

_CURR = "2026-10-17"


def _cache_path(tmp_path: Path) -> Path:
    today = pd.Timestamp.today().normalize()
    start = pd.to_datetime(_CURR) - pd.DateOffset(years=ind._CACHE_YEARS)
    if start > today:
        start = today - pd.DateOffset(years=ind._CACHE_YEARS)
    return (tmp_path
            / f"SBER-MOEX-data-{start.strftime('%Y-%m-%d')}"
              f"-{today.strftime('%Y-%m-%d')}.csv")


def _candles(last: str = _CURR) -> pd.DataFrame:
    dates = pd.date_range(end=last, periods=40, freq="D")
    return pd.DataFrame({
        "Date": dates.strftime("%Y-%m-%d"),
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5,
        "Volume": 1000, "Value": 100000.0,
    })


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ind, "_cache_dir", lambda: tmp_path)
    return _cache_path(tmp_path)


def test_stale_cache_is_refreshed(cache, monkeypatch):
    _candles("2026-10-16").to_csv(cache, index=False)
    _age(cache, ind._CACHE_TTL_SECONDS + 60)

    calls: list[tuple] = []
    monkeypatch.setattr(
        ind, "get_candles",
        lambda *a, **k: calls.append(a) or _candles(_CURR),
    )

    out = ind.load_ohlcv("SBER", _CURR)

    assert calls, "an out-of-date cache must be re-fetched"
    assert "2026-10-17" in set(out["Date"].astype(str))


def test_fresh_cache_is_reused(cache, monkeypatch):
    _candles("2026-10-17").to_csv(cache, index=False)   # mtime = now

    def boom(*a, **k):
        raise AssertionError("fresh cache must not trigger a fetch")

    monkeypatch.setattr(ind, "get_candles", boom)

    out = ind.load_ohlcv("SBER", _CURR)

    assert "2026-10-17" in set(out["Date"].astype(str))


def test_failed_refresh_serves_the_stale_bars(cache, monkeypatch):
    _candles("2026-10-16").to_csv(cache, index=False)
    _age(cache, ind._CACHE_TTL_SECONDS + 60)

    def boom(*a, **k):
        raise RuntimeError("ISS unreachable")

    monkeypatch.setattr(ind, "get_candles", boom)

    out = ind.load_ohlcv("SBER", _CURR)

    assert len(out) == 40            # cached bars, not an exception


def test_indicator_does_not_call_a_traded_day_a_non_trading_day(cache):
    _candles(_CURR).to_csv(cache, index=False)   # Saturday bar exists

    report = ind.indicator_values("SBER", "rsi", _CURR, 5)

    line = next(ln for ln in report.splitlines() if ln.startswith(f"{_CURR}:"))
    assert "Not a trading day" not in line
    assert line.split(":", 1)[1].strip().replace(".", "").isdigit()
