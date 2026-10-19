"""Офлайн-тесты бэктест-раннера и point-in-time окна новостей снапшота.

Сетка/скоринг — чистые функции scripts/backtest_moex; окно новостей —
integration.gui_adapter.snapshot_news_window. Ни один тест не ходит в сеть.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from integration.gui_adapter import snapshot_news_window
from scripts.backtest_moex import (
    grid_dates,
    load_env_file,
    load_run_meta,
    parse_percent,
    render_summary,
    summarize,
)


def _entry(date, ticker="SBER", rating="Buy", pending=False, alpha="+1.0%",
           decision="DECISION text", reflection=""):
    return {"date": date, "ticker": ticker, "rating": rating, "pending": pending,
            "alpha": alpha, "raw": None, "holding": "5d",
            "decision": decision, "reflection": reflection}


# --- сетка ---


def test_grid_three_years_monthly_is_37_dates():
    dates = grid_dates("2023-10-18", "2026-10-17", 30)
    assert len(dates) == 37  # ~3 года с шагом 30 дней
    assert dates[0] == "2023-10-18"
    assert dates[-1] == "2026-10-02"  # следующий шаг (+30д) ушёл бы за end
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


def test_grid_rejects_inverted_bounds_and_zero_step():
    with pytest.raises(ValueError):
        grid_dates("2026-01-02", "2026-01-01", 30)
    with pytest.raises(ValueError):
        grid_dates("2026-01-01", "2026-01-02", 0)


def test_grid_single_day_when_start_equals_end():
    assert grid_dates("2024-05-05", "2024-05-05", 7) == ["2024-05-05"]


# --- парсинг и агрегация ---


def test_parse_percent_accepts_written_outcomes_only():
    assert parse_percent("+3.2%") == pytest.approx(0.032)
    assert parse_percent("-1.5%") == pytest.approx(-0.015)
    assert parse_percent("0.0%") == 0.0
    assert parse_percent(None) is None
    assert parse_percent("n/a") is None
    assert parse_percent("") is None


def test_summarize_scores_directional_ratings_against_alpha():
    entries = [
        _entry("2024-01-31", rating="Buy", alpha="+1.2%"),        # верно
        _entry("2024-02-29", rating="Buy", alpha="-0.5%"),        # неверно
        _entry("2024-03-31", rating="Sell", alpha="-2.0%"),       # верно
        _entry("2024-04-30", rating="Hold", alpha="+3.0%"),       # без направления
        _entry("2024-05-31", pending=True, alpha=None),           # окно не закрыто
        _entry("2024-06-30", pending=False, alpha="not-a-number"),  # alpha не распознана
    ]
    summary = summarize(entries)
    assert summary["resolved"] == 4
    assert summary["pending"] == 1
    assert summary["unscored"] == 1  # settled, но исход не читается — не выдумываем
    assert summary["by_rating"]["Buy"]["count"] == 2
    assert summary["by_rating"]["Buy"]["hit_rate"] == pytest.approx(0.5)
    assert summary["by_rating"]["Buy"]["mean_alpha"] == pytest.approx(0.0035)
    assert summary["by_rating"]["Sell"]["hit_rate"] == 1.0
    assert summary["by_rating"]["Hold"]["hit_rate"] is None  # Hold ничего не утверждает


def test_render_summary_states_benchmark_and_pending_caveat():
    entries = [_entry("2024-01-31", alpha="+1.2%"),
               _entry("2024-02-29", pending=True, alpha=None)]
    summary = summarize(entries)
    text = render_summary(summary, tickers=["SBER"], grid="2023…2026 шаг 30д")
    assert "IMOEX" in text
    assert "Pending" in text
    assert "SBER" in text


def test_render_summary_reports_unscored_separately():
    entries = [_entry("2024-01-31", alpha="+1.2%"),
               _entry("2024-02-29", pending=False, alpha="garbage")]
    text = render_summary(summarize(entries), tickers=["SBER"], grid="…")
    assert "не оценено" in text
    assert "garbage" not in text  # мусорная alpha не попадает в агрегаты


def test_load_run_meta_roundtrip_and_tolerates_junk(tmp_path):
    import json

    meta_file = tmp_path / "run_meta.json"
    # Нет файла — пустой dict (старые прогоны без meta работают как раньше).
    assert load_run_meta(meta_file) == {}
    # Порча — тоже пустой dict, не исключение.
    meta_file.write_text("{broken", encoding="utf-8")
    assert load_run_meta(meta_file) == {}
    # Валидный meta — читается.
    meta_file.write_text(json.dumps({"tickers": ["SBER"], "start": "2023-10-18",
                                     "end": "2026-10-17", "every": 30}),
                         encoding="utf-8")
    assert load_run_meta(meta_file)["start"] == "2023-10-18"


# --- point-in-time окно новостей снапшота ---


def test_live_window_keeps_legacy_eight_days_up_to_now():
    until, days = snapshot_news_window("2026-09-20", "2026-10-17", live=True)
    assert days == 8
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now - until).total_seconds()) < 5  # живой путь не изменился


def test_historical_window_clamps_to_cell_day():
    until, days = snapshot_news_window("2023-09-21", "2023-11-15", live=False)
    assert until == datetime(2023, 11, 15, 23, 59, 59)  # конец даты ячейки
    assert days == (until - datetime(2023, 9, 21)).days + 1  # весь заявленный диапазон
    assert days == 56


def test_historical_window_never_reads_past_now():
    future = (datetime.now() + timedelta(days=1)).date().isoformat()
    until, _ = snapshot_news_window(future, future, live=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert until <= now + timedelta(seconds=1)  # as_of из будущего не утащит будущее


# --- окружение ---


def test_load_env_file_sets_missing_and_keeps_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# комментарий\n"
        "BT_NEW_KEY=value1\n"
        "OPENROUTER_API_KEY=from-file\n"
        "MALFORMED LINE WITHOUT EQUALS\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
    monkeypatch.delenv("BT_NEW_KEY", raising=False)
    load_env_file(env_file)
    import os
    assert os.environ["BT_NEW_KEY"] == "value1"
    assert os.environ["OPENROUTER_API_KEY"] == "from-shell"  # приоритет среды


def test_load_env_file_absent_file_is_noop(tmp_path):
    load_env_file(tmp_path / "missing.env")  # не должен упасть
