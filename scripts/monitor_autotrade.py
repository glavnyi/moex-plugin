#!/usr/bin/env python3
"""Hourly bug sweep for the auto-trading stack.

Answers one question: *did anything go wrong since the last sweep?*  It
reads only local evidence — the auto-trading journal, the GUI service log,
``runs.json`` and the live status endpoints — so it never spends an LLM
call or touches the broker just to produce a report.

Exit codes:  0 = no bugs in the window · 1 = bugs found · 2 = monitor itself
could not run.  Run it repeatedly (see ``monitor_autotrade_loop.sh``); the
``--since`` window is what makes consecutive runs non-overlapping in
practice — each sweep reports the last N minutes.

Usage::

    python scripts/monitor_autotrade.py                 # last 60 min
    python scripts/monitor_autotrade.py --minutes 30
    python scripts/monitor_autotrade.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GUI_BASE = os.environ.get("TRADINGAGENTS_GUI_URL", "http://127.0.0.1:5000")
SERVICE = "tradingagents-gui.service"

# Journal events that are always a bug or a refusal worth looking at.
BAD_EVENTS = {
    "cycle_error": "error",
    "loop_error": "error",
    "risk_blocked": "warn",
    "cycle_blocked": "warn",
    "screener_error": "warn",
}
# A run that is merely still going is not a bug — only one that outlived its
# own budget is (the cycle stops it at autotrade.run_timeout_seconds).
STALE_RUN_SECONDS = int(os.environ.get(
    "TRADINGAGENTS_MONITOR_STALE_RUN_S", "3600"))
# Gates the engine is *expected* to report when the world is busy/closed.
NORMAL_BLOCKED = {"enabled", "analysis_idle", "market_open"}
# Service-log lines that mean the stack struggled, even if it recovered.
BUG_PATTERNS = [
    (re.compile(r"Traceback \(most recent call last\)"), "error"),
    (re.compile(r"Exception|CRITICAL|Unhandled"), "error"),
    (re.compile(r"GET /api/autotrade 5\d\d|POST /api/autotrade 5\d\d"), "error"),
    (re.compile(r"MOEX extension routes not loaded"), "error"),
    (re.compile(r"OpenAIError|OpenAIConnectionError|ValueError|TypeError"),
     "error"),
    (re.compile(r"\b(5\d\d)\b.*[Ee]rror"), "error"),
]
# Checked first: a hiccup the retry ladder handled is not a failure.
WARN_PATTERNS = [
    (re.compile(r"retrying in \d"), "warn"),
    (re.compile(r"transient .*provider error", re.I), "warn"),
    (re.compile(r"did not fit|context length", re.I), "warn"),
    (re.compile(r"не уложился в \d+ с"), "warn"),
]


def _since_epoch(minutes: int) -> float:
    return time.time() - minutes * 60


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


def _http(path: str, timeout: float = 15.0):
    try:
        with urllib.request.urlopen(GUI_BASE + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — a dead GUI is itself a bug
        return {"_error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# collectors
# --------------------------------------------------------------------------

def collect_autotrade_journal(since: float) -> list[dict]:
    from moex.autotrade import journal_entries

    rows = []
    for row in journal_entries(limit=2000):
        try:
            ts = datetime.fromisoformat(str(row.get("ts"))).timestamp()
        except (TypeError, ValueError):
            continue
        if ts >= since:
            rows.append(row)
    return rows


def collect_service_log(since_minutes: int) -> list[dict]:
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", SERVICE,
             f"--since=-{since_minutes} min", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return [{"level": "warn", "line": f"journalctl недоступен: {exc}"}]
    hits: list[dict] = []
    for line in out.splitlines():
        level = None
        # Recovered hiccups outrank the generic patterns below.
        for pat, sev in WARN_PATTERNS:
            if pat.search(line):
                level = sev
                break
        if level is None:
            for pat, sev in BUG_PATTERNS:
                if pat.search(line):
                    level = sev
                    break
        if level:
            hits.append({"level": level, "line": line.strip()})
    return hits


def collect_bad_runs(since: float) -> list[dict]:
    path = Path.home() / ".tradingagents" / "runs.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    bad = []
    for run_id, run in (data.get("runs") or {}).items():
        started = float(run.get("started_at") or 0)
        ended = float(run.get("ended_at") or 0) or started
        if max(started, ended) < since:
            continue
        status = run.get("status")
        if status == "completed":
            continue
        stats = run.get("stats") or {}  # null-безопасно: остановленный
        # на старте прогон может быть записан без stats.
        elapsed = float(stats.get("elapsed_s") or 0)
        if status in ("running", "queued") and elapsed < STALE_RUN_SECONDS:
            continue  # still working within its budget
        bad.append({
            "run_id": run_id, "status": status,
            "ticker": run.get("ticker"), "date": run.get("date"),
            "started": _fmt_ts(started) if started else "",
            "ended": _fmt_ts(ended) if ended else "",
            "error": (run.get("error") or "")[:300],
            "llm_calls": stats.get("llm_calls"),
            "elapsed_s": round(float(stats.get("elapsed_s") or 0)),
        })
    return bad


def collect_health() -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "health_check.py")],
        capture_output=True, text=True, timeout=150, check=False,
        cwd=str(ROOT),
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    failures = [ln for ln in lines if ln.startswith("[FAIL]")]
    warnings = [ln for ln in lines if ln.startswith("[WARN]")]
    return {
        "ok": proc.returncode == 0 and not failures,
        "result": lines[-1] if lines else "",
        "failures": failures,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build_report(minutes: int) -> dict:
    since = _since_epoch(minutes)

    status = _http("/api/autotrade")
    status_broken = "_error" in status
    health = collect_health()
    journal = collect_autotrade_journal(since)
    log_hits = collect_service_log(minutes)
    bad_runs = collect_bad_runs(since)

    journal_bugs = [r for r in journal
                    if BAD_EVENTS.get(r.get("event")) == "error"]
    journal_warns = [r for r in journal
                     if BAD_EVENTS.get(r.get("event")) == "warn"]
    log_errors = [h for h in log_hits if h["level"] == "error"]
    log_warns = [h for h in log_hits if h["level"] == "warn"]

    gates = (status.get("gates") or {}) if not status_broken else {}
    engine = {
        "enabled": (status.get("settings") or {}).get("enabled"),
        "environment": status.get("environment"),
        "loop_started": status.get("loop_started"),
        "running": status.get("running"),
        "blocked_by": gates.get("blocked_by"),
        "reason": gates.get("reason"),
        "orders_today": status.get("orders_today"),
        "decisions_today": sorted((status.get("decisions_today") or {}).keys()),
        "last_error": status.get("last_error"),
    }

    bugs: list[str] = []
    infos: list[str] = []
    if status_broken:
        bugs.append("status API недоступен: " + status["_error"])
    if not engine["loop_started"]:
        bugs.append("daemon авторежима не запущен")
    if engine["enabled"] and engine["blocked_by"] not in (None, "enabled"):
        msg = (f"гейты блокируют цикл: {engine['blocked_by']} — "
               f"{engine['reason']}")
        # Busy/closed market is the engine working as designed.
        (infos if engine["blocked_by"] in NORMAL_BLOCKED else bugs).append(msg)
    if not health["ok"]:
        bugs.extend(health["failures"] or ["health_check: FAIL"])
    bugs.extend(f"журнал: {r.get('event')} {r.get('ticker') or ''} "
                f"{r.get('error') or r.get('reason') or ''}".strip()
                for r in journal_bugs)
    bugs.extend(f"лог GUI: {h['line'][:200]}" for h in log_errors)
    bugs.extend(f"запуск {r['run_id'][:8]} {r['status']} {r['ticker']}: "
                f"{r['error'] or 'без решения'}".strip()
                for r in bad_runs)

    warnings = [f"журнал: {r.get('event')} {r.get('ticker') or ''} "
                f"{r.get('reason') or ''}".strip() for r in journal_warns]
    warnings.extend(f"лог GUI: {h['line'][:200]}" for h in log_warns)
    warnings.extend(health["warnings"])

    return {
        "window_minutes": minutes,
        "at": datetime.now(timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %Z"),
        "engine": engine,
        "health": health,
        "counts": {
            "bugs": len(bugs), "warnings": len(warnings),
            "journal_rows": len(journal),
            "journal_errors": len(journal_bugs),
            "log_errors": len(log_errors), "log_warnings": len(log_warns),
            "bad_runs": len(bad_runs),
        },
        "bugs": bugs,
        "warnings": warnings,
        "infos": infos,
        "bad_runs": bad_runs,
        "ok": not bugs,
    }


def render(report: dict) -> str:
    e = report["engine"]
    c = report["counts"]
    lines = [
        "=" * 72,
        f"[{report['at']}] монитор авторежима — окно {report['window_minutes']} мин",
        f"движок: enabled={e['enabled']} env={e['environment']} "
        f"loop={e['loop_started']} running={e['running']} "
        f"blocked_by={e['blocked_by']}",
        f"        заявок сегодня={e['orders_today']} "
        f"решений={e['decisions_today'] or '—'} "
        f"last_error={e['last_error'] or '—'}",
        f"health: {report['health']['result'] or '—'}",
        f"итог: BUGS={c['bugs']} WARNINGS={c['warnings']} "
        f"(журнал={c['journal_rows']}, log_err={c['log_errors']}, "
        f"bad_runs={c['bad_runs']})",
    ]
    if report["bugs"]:
        lines.append("— БАГИ —")
        lines += [f"  ✗ {b}" for b in report["bugs"]]
    if report["warnings"]:
        lines.append("— предупреждения —")
        lines += [f"  ! {w}" for w in report["warnings"][:20]]
    if report.get("infos"):
        lines.append("— норма (движок занят/рынок) —")
        lines += [f"  · {i}" for i in report["infos"]]
    if not report["bugs"] and not report["warnings"]:
        lines.append("  багов и предупреждений за окно не найдено")
    lines.append(f"РЕЗУЛЬТАТ: {'BUGS FOUND' if report['bugs'] else 'CLEAN'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=int, default=60,
                    help="ширина окна наблюдения (по умолчанию 60)")
    ap.add_argument("--json", action="store_true",
                    help="машинный вывод вместо текстового отчёта")
    args = ap.parse_args(argv)

    try:
        report = build_report(max(1, args.minutes))
    except Exception as exc:  # noqa: BLE001 — the monitor must not die silently
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[FAIL] монитор сам упал: {type(exc).__name__}: {exc}")
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2)
          if args.json else render(report))
    return 1 if report["bugs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
