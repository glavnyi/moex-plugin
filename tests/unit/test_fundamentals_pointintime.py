"""Fundamentals: историческая ячейка не должна видеть сегодняшнюю цену.

В бэктесте снапшот зовёт fundamentals_report(ticker, as_of) — раньше отчёт
брал живую котировку (get_quote) независимо от даты, и аналитик про 2023 год
получал цену 2026 года. Теперь для прошлых дат «last» берётся из as-of
свечей, а интрадей-котировка запрашивается только для «сегодня».
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd

import moex.fundamentals.issuer as issuer

_PAST = "2024-01-15"
_HIST_CLOSE = 286.43
_LIVE_QUOTE = 500.0
_ISSUE_SIZE = 10_000_000_000


def _instrument():
    return SimpleNamespace(
        secid="SBER", short_name="Сбербанк", full_name="ПАО Сбербанк",
        isin="US8068571047", instrument_type="stock", board_id="TQBR",
        market="shares", currency="RUB", status="A", lot_size=10, face_value=None,
        issue_size=_ISSUE_SIZE, prev_close=999.0,
    )


def _hist() -> pd.DataFrame:
    dates = pd.date_range("2023-11-01", _PAST, freq="B")
    closes = [_HIST_CLOSE - 0.01 * i for i in range(len(dates))][::-1]
    return pd.DataFrame({
        "Date": dates,
        "Open": closes, "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes], "Close": closes,
        "Volume": [1_000_000] * len(dates),
    })


def _wire(monkeypatch, *, quote_calls: list):
    monkeypatch.setattr(issuer, "resolve_instrument", lambda *a, **k: _instrument())
    monkeypatch.setattr(issuer, "get_dividends",
                        lambda *a, **k: {"available": False, "items": []})

    def _quote(*a, **k):
        quote_calls.append(k)
        return {"last": _LIVE_QUOTE, "prev_close": 499.0,
                "quote_time": "2026-10-19 10:00", "source": "tinvest"}

    monkeypatch.setattr(issuer, "get_quote", _quote)
    monkeypatch.setattr(issuer, "load_ohlcv", lambda *a, **k: _hist())


def test_historical_cell_never_sees_todays_quote(monkeypatch):
    calls: list = []
    _wire(monkeypatch, quote_calls=calls)
    text = issuer.fundamentals_report("SBER", _PAST)
    assert calls == [], "историческая ячейка не должна спрашивать живую котировку"
    assert f"{_LIVE_QUOTE}" not in text
    assert f"Last close (as of {_PAST}): {_HIST_CLOSE}" in text
    assert "point-in-time" in text


def test_historical_market_cap_uses_point_in_time_close(monkeypatch):
    _wire(monkeypatch, quote_calls=[])
    text = issuer.fundamentals_report("SBER", _PAST)
    expected = f"{_ISSUE_SIZE * _HIST_CLOSE:,.0f}"
    assert expected in text
    assert "issue size x last close on or before curr_date" in text
    assert f"{_ISSUE_SIZE * _LIVE_QUOTE:,.0f}" not in text


def test_live_run_keeps_the_intraday_quote(monkeypatch):
    calls: list = []
    _wire(monkeypatch, quote_calls=calls)
    today = date.today().isoformat()
    text = issuer.fundamentals_report("SBER", today)
    assert len(calls) == 1
    assert f"Last price: {_LIVE_QUOTE}" in text
    assert "issue size x last price" in text


def test_future_date_is_treated_as_live(monkeypatch):
    # curr_date позже сегодня (например, «день следующей сессии») — это
    # текущий рынок, а не история: котировка допустима.
    calls: list = []
    _wire(monkeypatch, quote_calls=calls)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    issuer.fundamentals_report("SBER", tomorrow)
    assert len(calls) == 1


def test_missing_history_reports_unavailable_not_a_price(monkeypatch):
    calls: list = []
    _wire(monkeypatch, quote_calls=calls)

    def _no_hist(*a, **k):
        raise ValueError("No MOEX candle history")

    monkeypatch.setattr(issuer, "load_ohlcv", _no_hist)
    text = issuer.fundamentals_report("SBER", _PAST)
    assert calls == []
    assert "Last price: unavailable" in text
    assert "Price statistics: unavailable" in text
    assert "Market cap" not in text
