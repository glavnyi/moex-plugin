"""Stop orders (spec §6.4) — native conditional orders of T-Invest API.

Refused on production in this version. Native stop orders exist in the
API (StopOrdersService); a gap-through stop is never guaranteed to fill
at the stop price — callers must display that caveat (§6.4).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .client import TInvestClient, TInvestError, _quotation
from .orders import new_client_order_id

_DIRECTIONS = {"buy": "STOP_ORDER_DIRECTION_BUY",
               "sell": "STOP_ORDER_DIRECTION_SELL"}
_TYPES = {"stop_loss": "STOP_ORDER_TYPE_STOP_LOSS",
          "take_profit": "STOP_ORDER_TYPE_TAKE_PROFIT"}


def post_stop_order(
    client: TInvestClient,
    *,
    account_id: str,
    figi: str,
    direction: str,
    quantity_lots: int,
    stop_price: float,
    order_type: str = "stop_loss",
    limit_price: float | None = None,
    client_order_id: str | None = None,
) -> dict:
    if not client.sandbox:
        raise TInvestError("post_stop_order is sandbox-only in this version")
    d = direction.lower()
    t = (order_type or "stop_loss").lower()
    if d not in _DIRECTIONS or t not in _TYPES:
        raise TInvestError(f"invalid stop order params: {direction!r}/{order_type!r}")
    cid = client_order_id or new_client_order_id()
    payload = {
        "accountId": account_id,
        "figi": figi,
        "direction": _DIRECTIONS[d],
        "quantity": int(quantity_lots),
        "stopPrice": _quotation(float(stop_price)),
        "stopOrderType": _TYPES[t],
        "clientId": cid,
    }
    if limit_price is not None:
        payload["limitPrice"] = _quotation(float(limit_price))
    # Official OpenAPI: stop orders live on StopOrdersService for both
    # environments (no sandbox-specific stop methods exist).
    data = client.call("StopOrdersService", "PostStopOrder", payload)
    stop_id = data.get("stopOrderId") or ""
    return {
        "stop_order_id": stop_id,
        "client_order_id": cid,
        "figi": figi,
        "direction": d,
        "type": t,
        "stop_price": stop_price,
        "quantity_lots": int(quantity_lots),
        "sandbox": True,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "caveat": "программная/нативная стоп-заявка не гарантирует исполнение "
                  "по цене стопа при гэпе или остановке торгов (spec §6.4)",
    }


def list_stop_orders(client: TInvestClient, account_id: str) -> list[dict]:
    data = client.call("StopOrdersService", "GetStopOrders",
                       {"accountId": account_id})
    return data.get("stopOrders") or []


def cancel_stop_order(client: TInvestClient, account_id: str,
                      stop_order_id: str) -> dict:
    client.call("StopOrdersService", "CancelStopOrder",
                {"accountId": account_id, "stopOrderId": stop_order_id})
    return {"stop_order_id": stop_order_id, "status": "CANCEL_REQUESTED"}
