"""Trade orders in the sandbox — spec §5.5, §5.6.

Idempotency: every placement gets a client-generated ``client_order_id``
(reused on retry), is journaled to disk *before* the network call, and
re-sends after a timeout reuse the same id — the broker dedupes. Production
accounts are rejected outright.
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
    return f"moex-{uuid.uuid4().hex}"


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
    if not client.sandbox:
        raise TInvestError("post_order is sandbox-only in this version")
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
        "sandbox": True,
        "account_id": account_id,
        "figi": figi,
        "direction": direction_key,
        "order_type": type_key,
        "quantity_lots": int(quantity_lots),
        "price": price,
        "client_order_id": cid,
    })

    try:
        data = client.call("SandboxService", "PostSandboxOrder", payload)
    except TInvestError as exc:
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
        "sandbox": True,
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
    if not client.sandbox:
        raise TInvestError("cancel_order is sandbox-only in this version")
    _journal({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "cancel_order.attempt",
        "account_id": account_id,
        "order_id": order_id,
    })
    client.call("SandboxService", "CancelSandboxOrder",
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
