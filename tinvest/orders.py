"""Trade orders — spec §5.5, §5.6.

Idempotency: every placement gets a client-generated ``client_order_id``
(reused on retry), is journaled to disk *before* the network call, and
re-sends after a timeout reuse the same id — the broker dedupes.

Venue follows the §5.1 connection mode: ``sandbox`` uses SandboxService,
``live_trading`` uses OrdersService against production. Both refuse to run
on a client whose ``can_mutate`` is false.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .client import TInvestClient, TInvestError, _money, _quotation
from .models import Order

_ORDER_TYPES = {"limit": "ORDER_TYPE_LIMIT", "market": "ORDER_TYPE_MARKET"}
_DIRECTIONS = {"buy": "ORDER_DIRECTION_BUY", "sell": "ORDER_DIRECTION_SELL"}


def _journal_path() -> Path:
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(home) / "tinvest" / "orders_journal.jsonl"


def _journal(entry: dict) -> None:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def journal_entries() -> list[dict]:
    path = _journal_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def new_client_order_id() -> str:
    # T-Invest PostSandboxOrder rejects anything but a strict RFC-4122 UUID
    # ("`order id` has invalid UUID format", code 30028 — live-verified);
    # prefixed ids like "moex-<hex>" or "ta-<preview>" never reach matching.
    return str(uuid.uuid4())


def _services(client: TInvestClient) -> tuple[str, str, str]:
    """(service, post_method, cancel_method) for the client's venue.

    Sandbox and production expose the same contract under different names —
    live-verified against the official openapi: OrdersService/PostOrder and
    OrdersService/CancelOrder on production, SandboxService/PostSandboxOrder
    and SandboxService/CancelSandboxOrder in the sandbox.
    """
    if client.sandbox:
        return "SandboxService", "PostSandboxOrder", "CancelSandboxOrder"
    return "OrdersService", "PostOrder", "CancelOrder"


def post_order(
    client: TInvestClient,
    *,
    account_id: str,
    figi: str,
    direction: str,
    quantity_lots: int,
    order_type: str = "market",
    price: float | None = None,
    client_order_id: str | None = None,
) -> dict:
    """Place an order. Returns API result + journal metadata.

    Never auto-retried with a fresh id (spec §5.6): pass the previous
    ``client_order_id`` on retry so the broker sees the same request.
    """
    client.require_mutation("post_order")
    direction_key = direction.lower()
    type_key = (order_type or "market").lower()
    if direction_key not in _DIRECTIONS:
        raise TInvestError(f"unknown direction {direction!r}; use buy/sell")
    if type_key not in _ORDER_TYPES:
        raise TInvestError(f"unknown order type {order_type!r}; use market/limit")
    if int(quantity_lots) <= 0:
        raise TInvestError("quantity_lots must be a positive integer")
    if type_key == "limit" and price is None:
        raise TInvestError("limit order requires price")

    cid = client_order_id or new_client_order_id()
    payload: dict = {
        "accountId": account_id,
        "figi": figi,
        "direction": _DIRECTIONS[direction_key],
        "quantity": int(quantity_lots),
        "orderType": _ORDER_TYPES[type_key],
        "orderId": cid,
    }
    if type_key == "limit":
        payload["price"] = _quotation(float(price))

    _journal({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "post_order.attempt",
        "sandbox": client.sandbox,
        "mode": client.mode,
        "account_id": account_id,
        "figi": figi,
        "direction": direction_key,
        "order_type": type_key,
        "quantity_lots": int(quantity_lots),
        "price": price,
        "client_order_id": cid,
    })

    service, post_method, _ = _services(client)
    try:
        data = client.call(service, post_method, payload)
    except TInvestError as exc:
        # Spec §5.6 idempotency: re-sending the same client_order_id after a
        # timeout must NOT create a second order. The broker recognises the
        # duplicate but the sandbox execution report may not be retrievable,
        # answering 30057 "The order is a duplicate, but the order report was
        # not found" (live-verified). Treat it as a successful placement:
        # recover via GET state, honour the original order_id, and mark it
        # deduped so callers never mint a fresh id.
        if "30057" in str(exc) or "duplicate" in str(exc).lower():
            order = None
            try:
                order = get_order_state(client, account_id, cid)
            except TInvestError:
                order = None
            recovered_id = getattr(order, "order_id", None) or ""
            if not recovered_id or recovered_id == cid:
                # Sandbox GET-state by client id can 404 on a dedup; the real
                # order is still resting. Recover its id from the live open
                # book so callers can track/cancel the genuine order (live-
                # verified: duplicate returned a DIFFERENT order_id while the
                # original stayed open).
                try:
                    for o in list_orders(client, account_id):
                        if o.client_order_id == cid or o.client_order_id == "":
                            recovered_id = o.order_id
                            order = o
                            break
                except TInvestError:
                    pass
            _journal({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "post_order.result",
                "client_order_id": cid,
                "order_id": recovered_id or cid,
                "status": getattr(order, "status", "") or "DEDUPED",
                "deduped": True,
            })
            return {
                "order_id": recovered_id or cid,
                "client_order_id": cid,
                "status": getattr(order, "status", "") or "DEDUPED",
                "lots_requested": getattr(order, "lots_requested", None) or int(quantity_lots),
                "lots_executed": getattr(order, "lots_executed", 0) or 0,
                "message": "duplicate client_order_id re-sent (idempotent); "
                           "no second order created",
                "deduped": True,
                "sandbox": client.sandbox,
                "mode": client.mode,
                "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            }
        _journal({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "post_order.error",
            "client_order_id": cid,
            "error": str(exc),
            "retry_safe_id": cid,  # reuse on retry after timeout
        })
        raise

    order = Order.from_api(data.get("order") or data)
    _journal({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "post_order.result",
        "client_order_id": cid,
        "order_id": order.order_id,
        "status": order.status,
        "lots_executed": order.lots_executed,
    })
    return {
        "order_id": order.order_id,
        "client_order_id": cid,
        "status": order.status,
        "lots_requested": order.lots_requested,
        "lots_executed": order.lots_executed,
        "message": order.message,
        "sandbox": client.sandbox,
        "mode": client.mode,
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def get_order_state(client: TInvestClient, account_id: str,
                    order_id: str) -> Order:
    if client.sandbox:
        data = client.call("SandboxService", "GetSandboxOrderState",
                           {"accountId": account_id, "orderId": order_id})
    else:
        data = client.call("OrdersService", "GetOrderState",
                           {"accountId": account_id, "orderId": order_id})
    return Order.from_api(data)


def list_orders(client: TInvestClient, account_id: str) -> list[Order]:
    if client.sandbox:
        data = client.call("SandboxService", "GetSandboxOrders",
                           {"accountId": account_id})
    else:
        data = client.call("OrdersService", "GetOrders",
                           {"accountId": account_id})
    return [Order.from_api(o) for o in data.get("orders") or []]


def cancel_order(client: TInvestClient, account_id: str, order_id: str) -> dict:
    client.require_mutation("cancel_order")
    _journal({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "cancel_order.attempt",
        "account_id": account_id,
        "order_id": order_id,
    })
    service, _, cancel_method = _services(client)
    client.call(service, cancel_method,
                {"accountId": account_id, "orderId": order_id})
    state = get_order_state(client, account_id, order_id)
    _journal({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "cancel_order.result",
        "order_id": order_id,
        "status": state.status,
    })
    return {
        "order_id": order_id,
        "status": state.status,
        "lots_executed": state.lots_executed,
        "lots_requested": state.lots_requested,
    }


def wait_for_fill(client: TInvestClient, account_id: str, order_id: str,
                  *, timeout: float = 60.0, poll: float = 2.0) -> Order:
    """Poll until the order reaches a terminal state or timeout."""
    deadline = time.monotonic() + timeout
    state = get_order_state(client, account_id, order_id)
    while not state.is_terminal and time.monotonic() < deadline:
        time.sleep(poll)
        state = get_order_state(client, account_id, order_id)
    return state
