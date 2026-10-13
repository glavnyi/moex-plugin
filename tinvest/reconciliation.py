"""State synchronisation between local journal and broker (spec §5.7)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .client import TInvestClient, TInvestError
from .operations import list_operations
from .orders import journal_entries, list_orders
from .portfolio import balance_view, get_portfolio


def _state_path() -> Path:
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(home) / "tinvest" / "state.json"


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

    # Local journal vs broker reality (spec §5.6/5.7).
    attempts = [e for e in journal_entries()
                if e.get("event") == "post_order.result"]
    broker_ids = {o.order_id for o in orders}
    mismatches = [
        {"client_order_id": e.get("client_order_id"),
         "journal_order_id": e.get("order_id"),
         "reason": "journal order absent from broker listing"}
        for e in attempts
        if broker_ids and e.get("order_id") and e["order_id"] not in broker_ids
    ]
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
