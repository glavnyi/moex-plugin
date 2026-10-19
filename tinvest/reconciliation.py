"""State synchronisation between local journal and broker (spec §5.7)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .client import TInvestClient, TInvestError
from .operations import list_operations
from .orders import journal_entries, list_orders
from .portfolio import balance_view, get_portfolio
from .stop_orders import list_stop_orders


def _state_path() -> Path:
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(home) / "tinvest" / "state.json"


# Journal statuses that mean "the order is still resting at the broker".
# Anything else (FILLED/CANCELLED/REJECTED) has correctly disappeared from
# the broker's open-order listing and must never be flagged.
_OPEN_STATUSES = (
    "EXECUTION_REPORT_STATUS_NEW",
    "EXECUTION_REPORT_STATUS_PARTIALLY_EXECUTED",
)

# Journal results older than this are settled by definition: this system's
# resting orders live minutes, and a fill/cancel is never journaled again,
# so a stale "NEW" says nothing about today's broker state.
_MISMATCH_WINDOW_HOURS = 48


def _journal_mismatches(attempts: list[dict], broker_ids: set[str], *,
                        now: datetime | None = None,
                        window_hours: int = _MISMATCH_WINDOW_HOURS) -> list[dict]:
    """Journal orders the broker no longer lists — only ones still expected open.

    A filled or cancelled order correctly vanishes from the open-order
    listing, so the whole journal history must not be compared against it
    (live noise: every settled order of past days read as a mismatch).
    Only results whose status is still NEW/PARTIALLY_EXECUTED count, and only
    inside ``window_hours``. An EMPTY broker listing is not a reason to skip
    the check: a journal that believes an order rests while the broker shows
    nothing is exactly the drift this looks for.
    """
    now = now or datetime.now(timezone.utc)
    flagged: list[dict] = []
    for e in attempts:
        order_id = e.get("order_id")
        if not order_id or order_id in broker_ids:
            continue
        if str(e.get("status") or "") not in _OPEN_STATUSES:
            continue
        try:
            stamped = datetime.fromisoformat(str(e.get("ts")))
        except (TypeError, ValueError):
            continue  # no readable timestamp → no honest age → no flag
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        if now - stamped > timedelta(hours=window_hours):
            continue
        flagged.append({"client_order_id": e.get("client_order_id"),
                        "journal_order_id": order_id,
                        "reason": "journal order absent from broker listing"})
    return flagged


def sync(client: TInvestClient, account_id: str) -> dict:
    """Refresh balance, positions, orders, operations; persist snapshot."""
    errors: list[str] = []
    snapshot: dict = {
        "account_id": account_id,
        "sandbox": client.sandbox,
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "t-invest-api",
    }
    try:
        snapshot["balance"] = balance_view(client, account_id)
    except TInvestError as exc:
        errors.append(f"balance: {exc}")
    try:
        state = get_portfolio(client, account_id)
        snapshot["portfolio"] = {
            "total": state.total_portfolio.amount if state.total_portfolio else None,
            "currency": state.total_portfolio.currency if state.total_portfolio else "",
            "position_count": len(state.positions),
            "positions": [
                {"ticker": p.ticker, "quantity_lots": p.quantity,
                 "avg_price": p.avg_price.amount if p.avg_price else None}
                for p in state.positions
            ],
        }
    except TInvestError as exc:
        errors.append(f"portfolio: {exc}")
    try:
        orders = list_orders(client, account_id)
        snapshot["open_orders"] = [
            {"order_id": o.order_id, "figi": o.figi, "status": o.status,
             "lots": f"{o.lots_executed}/{o.lots_requested}"}
            for o in orders
        ]
    except TInvestError as exc:
        errors.append(f"orders: {exc}")
        orders = []
    try:
        snapshot["operations"] = list_operations(client, account_id, limit=50)
    except TInvestError as exc:
        errors.append(f"operations: {exc}")
    try:
        # Stop orders are invisible to list_orders — without this the
        # snapshot pretends there are no resting stops (live bug: after
        # placing a stop-loss the sync state.json showed none).
        snapshot["stop_orders"] = [
            {"stop_order_id": s.get("stopOrderId"),
             "figi": s.get("figi"),
             "direction": ("sell" if "SELL" in str(s.get("direction"))
                           else ("buy" if "BUY" in str(s.get("direction"))
                                 else s.get("direction"))),
             "status": str(s.get("status") or "").replace(
                 "STOP_ORDER_STATUS_", "").lower()}
            for s in list_stop_orders(client, account_id)
        ]
    except TInvestError as exc:
        errors.append(f"stop_orders: {exc}")

    # Local journal vs broker reality (spec §5.6/5.7): only orders still
    # expected open can be "absent" — settled ones left the listing rightly.
    attempts = [e for e in journal_entries()
                if e.get("event") == "post_order.result"]
    mismatches = _journal_mismatches(attempts, {o.order_id for o in orders})
    snapshot["journal_entries"] = len(attempts)
    snapshot["mismatches"] = mismatches
    snapshot["errors"] = errors
    snapshot["stale"] = bool(errors)

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return snapshot


def load_last_state() -> dict | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
