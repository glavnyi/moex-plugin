"""Point-in-time каналы промпта: «уроки» §11 и лента новостей.

Регрессия к найденной смеси: enrich_run_config подмешивал живой
lessons.jsonl (записи 2026 года) в ячейки бэктеста 2023 года, а
build_analysis_context фетчил новость без until — свежая лента в
историческом контексте. Оба канала обязаны уважать дату ячейки.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import integration.config_adapter as ca
import integration.moex_settlement as settlement
import moex.analytics.workflow as workflow


def _frag():
    return {"tool_vendors": {}, "data_vendors": {}, "board": "TQBR",
            "ticker": "SBER"}


def _wire(monkeypatch, lessons_text="LESSONS"):
    """Офлайн-обвязка enrich_run_config: без сети и без GUI-вендоров."""
    monkeypatch.setattr(ca, "register_moex_vendors", lambda: None)
    monkeypatch.setattr(ca, "moex_vendor_config", lambda t, b: _frag())
    monkeypatch.setattr(settlement, "install", lambda: {"gui": True})
    monkeypatch.setattr("integration.gui_adapter.build_moex_snapshot",
                        lambda *a, **k: "SNAPSHOT")
    monkeypatch.setattr("moex.analytics.lessons_block", lambda *a, **k: lessons_text)


# --- _lessons_wanted -------------------------------------------------------

def test_lessons_wanted_defaults_to_on():
    assert ca._lessons_wanted({}) is True
    assert ca._lessons_wanted({"moex_lessons": True}) is True


def test_lessons_wanted_honors_off_switches():
    for off in (False, 0, "false", "0", "off", "OFF ", "no"):
        assert ca._lessons_wanted({"moex_lessons": off}) is False, off


# --- enrich_run_config: гейт уроков ---------------------------------------

def test_backtest_flag_keeps_lessons_out_of_the_snapshot(monkeypatch):
    _wire(monkeypatch)
    merged = ca.enrich_run_config(
        {"market": "MOEX", "ticker": "SBER", "moex_lessons": False},
        {"market": "MOEX", "ticker": "SBER"})
    assert merged["moex_data_snapshot"] == "SNAPSHOT"  # без уроков


def test_live_runs_still_get_lessons(monkeypatch):
    _wire(monkeypatch)
    merged = ca.enrich_run_config(
        {"market": "MOEX", "ticker": "SBER"},
        {"market": "MOEX", "ticker": "SBER"})
    assert merged["moex_data_snapshot"].startswith("LESSONS")


# --- _news_until: обрезка ленты по дате ячейки -----------------------------

def test_news_until_cuts_a_historical_cell_at_its_own_day():
    until = workflow._news_until("2023-10-18")
    assert until == datetime(2023, 10, 18, 23, 59, 59)
    assert until < datetime.now(timezone.utc).replace(tzinfo=None)


def test_news_until_never_exceeds_now_for_today_and_future():
    today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    until_today = workflow._news_until(today)
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # после вызова
    assert until_today <= now
    assert until_today > now - timedelta(days=1)
    # Дата «из будущего» не даёт фильтру в будущее.
    until_future = workflow._news_until("2099-01-01")
    assert until_future <= datetime.now(timezone.utc).replace(tzinfo=None)
