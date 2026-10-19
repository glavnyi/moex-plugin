"""Bug classification for the hourly auto-trading monitor.

The monitor's whole value is that it does not cry wolf: a retry that
recovered, an analysis still inside its budget, or a cycle skipped because
the engine is off are *not* bugs. These tests pin that distinction.
"""

from __future__ import annotations

import json

import pytest

import scripts.monitor_autotrade as mon


@pytest.fixture()
def quiet(monkeypatch):
    """Replace every external reader with a controlled double."""
    monkeypatch.setattr(mon, "collect_health",
                        lambda: {"ok": True, "result": "RESULT: ALL OK",
                                 "failures": [], "warnings": []})
    monkeypatch.setattr(mon, "collect_autotrade_journal",
                        lambda since: [])
    monkeypatch.setattr(mon, "collect_service_log", lambda minutes: [])
    monkeypatch.setattr(mon, "collect_bad_runs", lambda since: [])
    return monkeypatch


def _status(**over):
    base = {
        "ok": True,
        "settings": {"enabled": True},
        "environment": "sandbox",
        "loop_started": True,
        "running": False,
        "orders_today": 0,
        "decisions_today": {},
        "last_error": None,
        "gates": {"ok": True, "blocked_by": None, "reason": "все условия выполнены",
                  "gates": []},
    }
    base.update(over)
    return base


def test_healthy_engine_reports_no_bugs(quiet):
    quiet.setattr(mon, "_http", lambda path: _status())
    report = mon.build_report(60)
    assert report["ok"] is True and report["bugs"] == []
    assert report["counts"]["bugs"] == 0


def test_engine_off_is_not_a_bug(quiet):
    quiet.setattr(mon, "_http",
                  lambda path: _status(settings={"enabled": False},
                                       gates={"blocked_by": "enabled",
                                              "reason": "выкл"}))
    report = mon.build_report(60)
    assert report["ok"] is True


@pytest.mark.parametrize("blocked", ["analysis_idle", "market_open"])
def test_busy_engine_is_not_a_bug(quiet, blocked):
    quiet.setattr(mon, "_http",
                  lambda path: _status(gates={"blocked_by": blocked,
                                              "reason": "занят"}))
    report = mon.build_report(60)
    assert report["ok"] is True
    assert blocked in report["infos"][0]


def test_a_failing_gate_is_a_bug(quiet):
    quiet.setattr(mon, "_http",
                  lambda path: _status(gates={"blocked_by": "orders_budget",
                                              "reason": "лимит заявок"}))
    report = mon.build_report(60)
    assert report["ok"] is False
    assert any("orders_budget" in b for b in report["bugs"])


def test_dead_status_endpoint_is_a_bug(quiet):
    quiet.setattr(mon, "_http", lambda path: {"_error": "ConnectionRefused"})
    report = mon.build_report(60)
    assert report["ok"] is False
    assert any("status API" in b for b in report["bugs"])


def test_journal_cycle_error_is_a_bug(quiet):
    quiet.setattr(mon, "_http", lambda path: _status())
    quiet.setattr(mon, "collect_autotrade_journal",
                  lambda since: [{"event": "cycle_error", "ticker": "SBER",
                                  "error": "анализ не уложился"}])
    report = mon.build_report(60)
    assert report["ok"] is False
    assert any("cycle_error" in b for b in report["bugs"])


def test_retrying_provider_hiccup_is_a_warning_not_a_bug(quiet):
    quiet.setattr(mon, "_http", lambda path: _status())
    line = ("2026-10-16T21:06:00 transient OpenRouter/provider error "
            "({'message': 'A Timeout Occurred', 'code': 504}) — retrying in 3.4s")
    quiet.setattr(mon, "collect_service_log",
                  lambda minutes: [{"level": "warn", "line": line}])
    report = mon.build_report(60)
    assert report["ok"] is True
    assert report["counts"]["log_warnings"] == 1
    assert any("retrying in 3.4s" in w for w in report["warnings"])


def test_real_service_error_still_counts_as_a_bug(quiet):
    quiet.setattr(mon, "_http", lambda path: _status())
    quiet.setattr(mon, "collect_service_log",
                  lambda minutes: [{"level": "error",
                                    "line": "Traceback (most recent call last)"}])
    report = mon.build_report(60)
    assert report["ok"] is False


def test_pattern_priority_prefers_recovered_hiccup():
    """WARN_PATTERNS are checked first, so 'retrying' wins over the 5xx rule."""
    line = "code: 504 — retrying in 3.4s"
    matched = [sev for pat, sev in mon.WARN_PATTERNS if pat.search(line)]
    assert matched and matched[0] == "warn"
    assert not any(pat.search(line) for pat, _ in mon.BUG_PATTERNS)


def test_bad_runs_skips_a_run_still_inside_its_budget(monkeypatch, tmp_path):
    import time

    now = time.time()
    runs = {
        "current_id": "a",
        "runs": {
            "fresh": {"id": "fresh", "status": "running", "ticker": "SBER",
                      "date": "2026-10-16", "started_at": now - 120,
                      "ended_at": 0, "error": None,
                      "stats": {"elapsed_s": 60}},
            "stale": {"id": "stale", "status": "running", "ticker": "SBER",
                      "date": "2026-10-16", "started_at": now - 99999,
                      "ended_at": 0, "error": None,
                      "stats": {"elapsed_s": 99999}},
            "failed": {"id": "failed", "status": "error", "ticker": "SBER",
                       "date": "2026-10-16", "started_at": now - 600,
                       "ended_at": now - 300, "error": "boom",
                       "stats": {"elapsed_s": 300}},
            "done": {"id": "done", "status": "completed", "ticker": "SBER",
                     "date": "2026-10-16", "started_at": now - 600,
                     "ended_at": now - 300, "error": None,
                     "stats": {"elapsed_s": 300}},
        },
    }
    path = tmp_path / ".tradingagents" / "runs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs), encoding="utf-8")
    monkeypatch.setattr(mon.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(mon, "STALE_RUN_SECONDS", 3600)
    # Wide window: the budget check, not the time filter, has to do the work.
    bad = mon.collect_bad_runs(10**7)
    ids = {r["run_id"] for r in bad}
    assert "fresh" not in ids and "done" not in ids
    assert ids == {"stale", "failed"}


def test_bad_runs_tolerates_null_stats(monkeypatch, tmp_path):
    import time

    now = time.time()
    runs = {
        "runs": {
            # Прогон, остановленный на старте, бывает записан без stats.
            "killed_early": {"id": "killed_early", "status": "error",
                             "ticker": "GAZP", "date": "2026-10-17",
                             "started_at": now - 600, "ended_at": now - 500,
                             "error": "stopped", "stats": None},
        },
    }
    path = tmp_path / ".tradingagents" / "runs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs), encoding="utf-8")
    monkeypatch.setattr(mon.Path, "home", staticmethod(lambda: tmp_path))
    bad = mon.collect_bad_runs(10**7)
    assert [r["run_id"] for r in bad] == ["killed_early"]
    assert bad[0]["llm_calls"] is None  # null-stats не роняет монитор
