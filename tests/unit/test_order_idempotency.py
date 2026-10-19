"""Spec §5.6: re-sending a client_order_id must not create a second order."""

from __future__ import annotations

import tinvest.orders as orders
from tinvest.client import TInvestError


class _FakeClient:
    sandbox = True
    mode = "sandbox"
    environment = "SANDBOX"
    can_mutate = True

    def __init__(self):
        self.posts = 0

    def require_mutation(self, what):
        if not self.can_mutate:
            raise TInvestError(f"{what} refused: read-only mode")

    def call(self, service, method, params):
        if method == "PostSandboxOrder":
            self.posts += 1
            # Second send of the same client id → broker dedup w/o report.
            raise TInvestError(
                'HTTP 400 from PostSandboxOrder: {"code":3,'
                '"message":"The order is a duplicate, but the order report '
                'was not found","description":"30057"}'
            )
        if method == "GetSandboxOrderState":
            # Sandbox can 404 on a dedup; the harness treats this as the
            # "report not found" path by raising below via caller's try.
            raise TInvestError("HTTP 404 from GetSandboxOrderState")
        if method == "GetSandboxOrders":
            return {"orders": [{"orderId": "real-open-oid",
                                "clientId": "same-cid",
                                "executionReportStatus":
                                "EXECUTION_REPORT_STATUS_NEW",
                                "lotsRequested": 1, "lotsExecuted": 0}]}
        raise AssertionError(f"unexpected {method}")


def test_duplicate_client_order_id_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(orders, "_journal_path",
                        lambda: tmp_path / "journal.jsonl")
    # Sandbox GET-state by client id 404s on a dedup (live-verified) — the
    # code must then recover the real resting order id from the open book.
    monkeypatch.setattr(orders, "get_order_state",
                        lambda c, a, oid: (_ for _ in ()).throw(
                            TInvestError("HTTP 404")))
    c = _FakeClient()
    r = orders.post_order(c, account_id="a", figi="F", direction="buy",
                          quantity_lots=1, order_type="limit", price=1.0,
                          client_order_id="same-cid")
    # Deduped, not an error; caller gets a success carrying the REAL order id.
    assert r["deduped"] is True
    assert r["order_id"] == "real-open-oid"
    assert r["lots_executed"] == 0
    # Journal recorded a result, never an error → no retry storm.
    txt = (tmp_path / "journal.jsonl").read_text()
    assert "post_order.result" in txt
    assert "real-open-oid" in txt
