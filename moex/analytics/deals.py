"""Closed-deal detection from T-Invest operations (spec §11).

The sandbox journal keeps every fill as separate operations (buy / sell /
commission) with payment amounts but WITHOUT quantity (sandbox quirk —
live-verified: quantity/quantityLots come back null). Closed deals are
therefore reconstructed by FIFO pairing of buy payments against sell
payments per figi, and each completed round-trip becomes a lesson via
``record_lesson`` so future analyst runs see realized outcomes.

Idempotency: consumed operation ids are persisted in
``~/.tradingagents/tinvest/lessons_seen.json`` — re-syncs never record the
same deal twice.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BUY_MARK = "покупка"
_SELL_MARK = "продажа"
_COMMISSION_MARK = "комисси"
# Sandbox commissions land within the same second as the fill; give a small
# window so overlapping deals don't steal each other's fees.
_COMMISSION_SLACK = timedelta(seconds=5)


def _home_file(name: str) -> Path:
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    path = Path(home) / "tinvest" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def detect_closed_deals(ops: list[dict]) -> list[dict]:
    """FIFO-match buy payments against sell payments per figi.

    Returns one dict per matched sell::

        {"figi", "opened_at", "closed_at", "buy_payment", "sell_payment",
         "pnl", "commission", "trade_op_ids", "commission_op_ids"}

    ``pnl`` includes matched commissions (sell + buys + fees, payments are
    already signed: buy negative, sell positive). Partial sell-vs-buy
    matches produce one deal per matched slice; leftover buy lots stay in
    the book for the next sell.
    """
    trades = [o for o in ops
              if o.get("status") == "OPERATION_STATE_EXECUTED"
              and o.get("figi")
              and (o.get("payment") or 0) != 0
              and str(o.get("type") or "").lower().count("комисси") == 0
              and (_BUY_MARK in str(o.get("type") or "").lower()
                   or _SELL_MARK in str(o.get("type") or "").lower())]
    trades.sort(key=lambda o: str(o.get("date") or ""))

    commissions = [o for o in ops
                   if o.get("status") == "OPERATION_STATE_EXECUTED"
                   and _COMMISSION_MARK in str(o.get("type") or "").lower()
                   and (o.get("payment") or 0) != 0]
    used_commissions: set[str] = set()

    open_lots: dict[str, list[dict]] = {}   # figi -> FIFO of buy slices
    deals: list[dict] = []

    def _attach_commissions(deal: dict) -> None:
        opened = _parse_dt(deal["opened_at"])
        closed = _parse_dt(deal["closed_at"])
        if not opened or not closed:
            return
        lo, hi = opened - _COMMISSION_SLACK, closed + _COMMISSION_SLACK
        total = 0.0
        ids: list[str] = []
        for c in commissions:
            cid = str(c.get("operation_id") or "")
            if not cid or cid in used_commissions:
                continue
            when = _parse_dt(c.get("date"))
            if when and lo <= when <= hi:
                used_commissions.add(cid)
                total += float(c.get("payment") or 0.0)
                ids.append(cid)
        deal["commission"] = round(total, 4)
        deal["commission_op_ids"] = ids
        deal["pnl"] = round(float(deal["buy_payment"]) + float(deal["sell_payment"])
                            + total, 4)

    for op in trades:
        figi = str(op.get("figi"))
        payment = float(op.get("payment") or 0.0)
        is_buy = _BUY_MARK in str(op.get("type") or "").lower()
        book = open_lots.setdefault(figi, [])
        if is_buy:
            book.append({
                "payment": payment,
                "remaining": abs(payment),
                "op_id": str(op.get("operation_id") or ""),
                "date": str(op.get("date") or ""),
            })
            continue
        # Sell: consume buy lots FIFO until the sell payment is covered.
        remaining_sell = abs(payment)
        matched_buys: list[dict] = []
        matched_buy_sum = 0.0
        while book and remaining_sell > 0.005:
            lot = book[0]
            take = min(lot["remaining"], remaining_sell)
            lot["remaining"] = round(lot["remaining"] - take, 4)
            remaining_sell = round(remaining_sell - take, 4)
            matched_buy_sum = round(matched_buy_sum + take, 4)
            matched_buys.append({"op_id": lot["op_id"], "amount": take,
                                 "date": lot["date"]})
            if lot["remaining"] <= 0.005:
                book.pop(0)
        if not matched_buys:
            continue  # sell without prior buy in window — nothing to pair
        sell_id = str(op.get("operation_id") or "")
        trade_ids = [b["op_id"] for b in matched_buys if b["op_id"]]
        if sell_id:
            trade_ids.append(sell_id)
        deal = {
            "figi": figi,
            "opened_at": matched_buys[0]["date"],
            "closed_at": str(op.get("date") or ""),
            "buy_payment": -round(matched_buy_sum, 4),
            "sell_payment": round(payment, 4),
            "pnl": None,
            "commission": 0.0,
            "trade_op_ids": trade_ids,
            "commission_op_ids": [],
            "buy_slices": matched_buys,
        }
        _attach_commissions(deal)
        deals.append(deal)
    return deals


def _load_state() -> dict:
    path = _home_file("lessons_seen.json")
    if not path.exists():
        return {"seen_op_ids": [], "figi_tickers": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen_op_ids": [], "figi_tickers": {}}
    state.setdefault("seen_op_ids", [])
    state.setdefault("figi_tickers", {})
    return state


def _save_state(state: dict) -> None:
    path = _home_file("lessons_seen.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def _resolve_ticker(client, figi: str, cache: dict) -> str:
    if figi in cache:
        return str(cache[figi])
    try:
        data = client.call("InstrumentsService", "GetInstrumentBy",
                           {"id": figi, "idType": "INSTRUMENT_ID_TYPE_FIGI"})
        ticker = str((data.get("instrument") or {}).get("ticker") or "")
    except Exception:  # noqa: BLE001 — ticker is cosmetic, figi is enough
        ticker = ""
    if ticker:
        cache[figi] = ticker
    return ticker or figi


def sync_lessons_from_operations(client, account_id: str, *,
                                 limit: int = 300) -> dict:
    """Detect newly closed sandbox deals and record one lesson each (§11)."""
    from .workflow import record_lesson

    from tinvest.operations import list_operations

    ops = list_operations(client, account_id, limit=limit)
    deals = detect_closed_deals(ops)
    state = _load_state()
    seen: set[str] = set(state.get("seen_op_ids") or [])
    cache = state.get("figi_tickers") or {}

    recorded: list[dict] = []
    for deal in deals:
        involved = set(deal["trade_op_ids"]) | set(deal["commission_op_ids"])
        if not involved or involved <= seen:
            continue
        ticker = _resolve_ticker(client, deal["figi"], cache)
        lesson = record_lesson(
            proposal={"ticker": ticker, "side": "sell",
                      "figi": deal["figi"]},
            decision_context={"source": "sandbox-operations",
                              "auto_detected": True,
                              "opened_at": deal["opened_at"]},
            execution={"closed_at": deal["closed_at"],
                       "buy_payment": deal["buy_payment"],
                       "sell_payment": deal["sell_payment"],
                       "commission": deal.get("commission", 0.0)},
            realized_pnl=deal["pnl"],
            explanation=(f"{ticker}: round-trip {deal['opened_at']} → "
                         f"{deal['closed_at']}, realized P&L "
                         f"{deal['pnl']:.2f} RUB "
                         f"(fees {deal.get('commission', 0.0):.2f})"),
        )
        recorded.append(lesson)
        seen |= involved

    state["seen_op_ids"] = sorted(seen)
    state["figi_tickers"] = cache
    _save_state(state)
    return {"recorded": len(recorded),
            "lessons": [l["id"] for l in recorded],
            "deals_seen": len(deals)}
