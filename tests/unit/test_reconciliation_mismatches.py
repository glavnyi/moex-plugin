"""Расхождения журнала и брокера: флагуются только реально открытые заявки.

Старая логика сравнивала ВСЮ историю orders_journal с текущим листингом
открытых заявок: каждый давно исполненный/отменённый ордер читался как
mismatch, а пустой листинг глушил проверку (fail-open) — то есть критический
случай «журнал считает заявку открытой, брокер не показывает ничего»
не детектировался вовсе. Здесь — чистая функция _journal_mismatches.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import tinvest.reconciliation as rec

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def _attempt(order_id="ord-1", status="EXECUTION_REPORT_STATUS_NEW",
             ts=None, client_order_id="cli-1"):
    if ts is None:
        ts = _NOW - timedelta(hours=1)
    stamped = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return {"event": "post_order.result", "order_id": order_id,
            "client_order_id": client_order_id, "status": status,
            "ts": stamped, "lots_executed": 0}


def test_settled_history_is_never_flagged():
    # Исполненная и отменённая заявки исчезли из листинга — это норма.
    attempts = [
        _attempt("old-fill", status="EXECUTION_REPORT_STATUS_EXECUTED",
                 ts=_NOW - timedelta(hours=30)),
        _attempt("old-cancel", status="EXECUTION_REPORT_STATUS_CANCELLED",
                 ts=_NOW - timedelta(hours=40)),
    ]
    assert rec._journal_mismatches(attempts, {"other-open"}, now=_NOW) == []


def test_recent_open_order_missing_from_broker_is_flagged():
    out = rec._journal_mismatches([_attempt("resting")], {"other-open"}, now=_NOW)
    assert out == [{"client_order_id": "cli-1", "journal_order_id": "resting",
                    "reason": "journal order absent from broker listing"}]


def test_order_the_broker_does_list_is_fine():
    assert rec._journal_mismatches([_attempt("listed")], {"listed"}, now=_NOW) == []


def test_empty_broker_listing_still_flags_a_journal_open_order():
    # Ключевой кейс: журнал верит в открытую заявку, брокер не показывает
    # ничего — раньше это глушилось условием `if broker_ids and ...`.
    out = rec._journal_mismatches([_attempt("ghost")], set(), now=_NOW)
    assert [m["journal_order_id"] for m in out] == ["ghost"]


def test_stale_new_result_is_settled_by_definition():
    out = rec._journal_mismatches(
        [_attempt("ancient", ts=_NOW - timedelta(hours=72))],
        set(), now=_NOW)
    assert out == []


def test_naive_timestamp_is_treated_as_utc():
    naive = (_NOW - timedelta(hours=1)).replace(tzinfo=None)
    out = rec._journal_mismatches([_attempt("naive", ts=naive)], set(), now=_NOW)
    assert [m["journal_order_id"] for m in out] == ["naive"]


def test_unreadable_timestamp_is_not_flagged():
    out = rec._journal_mismatches([_attempt("garbage", ts="not-a-date")],
                                  set(), now=_NOW)
    assert out == []


def test_sync_writes_mismatches_into_the_snapshot(monkeypatch):
    monkeypatch.setattr(rec, "balance_view",
                        lambda c, a: {"cash_by_currency": []})
    monkeypatch.setattr(rec, "get_portfolio",
                        lambda c, a: SimpleNamespace(
                            total_portfolio=None, positions=[]))
    monkeypatch.setattr(rec, "list_orders", lambda c, a: [])
    monkeypatch.setattr(rec, "list_operations", lambda c, a, limit=None: [])
    monkeypatch.setattr(rec, "list_stop_orders", lambda c, a: [])
    monkeypatch.setattr(rec, "journal_entries", lambda: [
        _attempt("ghost", ts=datetime.now(timezone.utc) - timedelta(minutes=5)),
        _attempt("settled", status="EXECUTION_REPORT_STATUS_EXECUTED",
                 ts=datetime.now(timezone.utc) - timedelta(minutes=5)),
    ])
    tmp = rec._state_path()
    monkeypatch.setattr(rec, "_state_path",
                        lambda: tmp.parent / "state_test_mismatches.json")
    snap = rec.sync(SimpleNamespace(sandbox=True), "acct")
    assert [m["journal_order_id"] for m in snap["mismatches"]] == ["ghost"]
    assert snap["stale"] is False
