from __future__ import annotations

from pathlib import Path

from moex.config import DEFAULT_SETTINGS, load_settings, save_settings, update_settings
from moex.market_data.indicators import INDICATOR_DOCS, indicator_values
from moex.market_data.formatters import stock_data_report


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    update_settings("risk", {"max_order_amount": 42.0})
    s = load_settings()
    assert s["risk"]["max_order_amount"] == 42.0
    # untouched sections keep defaults
    assert s["news"]["sync_seconds"] == DEFAULT_SETTINGS["news"]["sync_seconds"]
    path = tmp_path / "moex" / "settings.json"
    assert path.exists()


def test_settings_corrupt_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    path = tmp_path / "moex" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    s = load_settings()
    assert s["risk"]["max_order_amount"] == DEFAULT_SETTINGS["risk"]["max_order_amount"]


def test_save_settings_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    s = load_settings()
    s["sandbox"]["mark"] = "SANDBOX"
    save_settings(s)
    assert load_settings()["sandbox"]["mark"] == "SANDBOX"
    leftovers = list((tmp_path / "moex").glob(".settings-*"))
    assert not leftovers


def test_indicator_docs_cover_market_analyst_menu():
    # Exact list the upstream market analyst prompt offers to the model.
    offered = {
        "close_50_sma", "close_200_sma", "close_10_ema",
        "macd", "macds", "macdh", "rsi",
        "boll", "boll_ub", "boll_lb", "atr", "vwma",
    }
    assert offered <= set(INDICATOR_DOCS)


def test_stock_data_report_format(monkeypatch):
    import pandas as pd

    from moex.market_data import formatters

    df = pd.DataFrame({
        "Date": ["2026-10-09", "2026-10-10"],
        "Open": [100.0, 101.0],
        "High": [102.0, 103.0],
        "Low": [99.0, 100.0],
        "Close": [101.0, 102.0],
        "Volume": [1000, 1100],
    })
    monkeypatch.setattr(formatters, "get_candles",
                        lambda *a, **k: df.copy())
    monkeypatch.setattr(formatters, "get_quote",
                        lambda *a, **k: {"last": 102.0, "currency": "RUB"})
    out = formatters.stock_data_report("SBER", "2026-10-01", "2026-10-10")
    assert "# Stock data for SBER from 2026-10-01 to 2026-10-10" in out
    assert "# Total records:" in out
    assert "# Data retrieved on:" in out
    assert "2026-10-10" in out


def test_indicator_values_rejects_unknown():
    try:
        indicator_values("SBER", "not_a_real_indicator", "2026-10-10", 30)
    except ValueError as exc:
        assert "not supported" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown indicator")
