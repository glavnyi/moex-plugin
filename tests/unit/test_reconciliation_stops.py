"""Stop orders must appear in the broker snapshot (spec §5.7)."""

from __future__ import annotations

from types import SimpleNamespace

import tinvest.reconciliation as rec


class _FakeClient:
    sandbox = True


def test_sync_includes_stop_orders(monkeypatch):
    monkeypatch.setattr(rec, "balance_view",
                        lambda c, a: {"cash_by_currency": []})
    monkeypatch.setattr(rec, "get_portfolio",
                        lambda c, a: SimpleNamespace(
                            total_portfolio=None, positions=[]))
    monkeypatch.setattr(rec, "list_orders", lambda c, a: [])
    monkeypatch.setattr(rec, "list_operations", lambda c, a, limit=None: [])
    monkeypatch.setattr(rec, "journal_entries", lambda: [])
    monkeypatch.setattr(rec, "list_stop_orders", lambda c, a: [
        {"stopOrderId": "abc", "figi": "BBG004730N88",
         "direction": "STOP_ORDER_DIRECTION_SELL",
         "status": "STOP_ORDER_STATUS_ACTIVE"},
    ])
    tmp = rec._state_path()
    monkeypatch.setattr(rec, "_state_path",
                        lambda: tmp.parent / "state_test_stops.json")
    snap = rec.sync(_FakeClient(), "acct")
    assert snap["stop_orders"] == [
        {"stop_order_id": "abc", "figi": "BBG004730N88",
         "direction": "sell", "status": "active"},
    ]
    assert snap["stale"] is False
