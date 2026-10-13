"""Analysis → proposal → risk → confirmation → sandbox order → lessons
(spec §6.2, §11). Nothing here auto-executes: every placement requires
``confirm=True`` which the GUI only sends after an explicit user click.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..fundamentals import fundamentals_report
from ..instruments import resolve_instrument
from ..market_data import get_candles, get_quote, indicator_values, stock_data_report
from ..news import fetch_all, news_report
from ..risk import check_proposal, effective_limits, size_position
from .proposal import TradeProposal, extract_proposal

_STATUS_MAP = {
    # REST enum values (investAPI v1 OrderExecutionReportStatus) — verified
    # against src/docs/swagger-ui/openapi.yaml.
    "EXECUTION_REPORT_STATUS_NEW": "accepted",
    "EXECUTION_REPORT_STATUS_FILL": "filled",
    "EXECUTION_REPORT_STATUS_PARTIALLYFILL": "partial",
    "EXECUTION_REPORT_STATUS_CANCELLED": "cancelled",
    "EXECUTION_REPORT_STATUS_REJECTED": "rejected",
    # Short/legacy aliases seen in older API replies.
    "NEW": "accepted",
    "FILL": "filled",
    "FILLED": "filled",
    "PARTIALLYFILL": "partial",
    "PARTIALLY_EXECUTED": "partial",
    "PENDING_NEW": "sent",
    "BORDERED": "sent",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
    "REJECTED": "rejected",
    "REPLACED": "accepted",
}


def map_order_state(api_status: str) -> str:
    return _STATUS_MAP.get((api_status or "").upper(), "unknown")


def build_analysis_context(
    ticker: str,
    curr_date: str,
    *,
    lookback_days: int = 30,
    portfolio_ctx: dict | None = None,
    include_news: bool = True,
    news_days: int = 7,
) -> dict:
    """Everything the graph/analyst needs for one MOEX run (§4, §6.1)."""
    instrument = resolve_instrument(ticker)
    ctx: dict = {
        "market": "MOEX",
        "ticker": instrument.secid,
        "instrument": instrument.__dict__ if hasattr(instrument, "__dict__") else {},
        "curr_date": curr_date,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    quote = get_quote(instrument, board=instrument.board_id)
    ctx["quote"] = quote
    candles = get_candles(instrument.secid, "2000-01-01" if lookback_days > 250
                          else _shift_days(curr_date, lookback_days),
                          curr_date, interval="1d", board=instrument.board_id)
    ctx["ohlcv_rows"] = len(candles)
    ctx["stock_data_report"] = stock_data_report(
        instrument.secid, _shift_days(curr_date, min(lookback_days, 120)),
        curr_date, board=instrument.board_id,
    )
    try:
        ctx["indicators"] = {
            ind: indicator_values(instrument.secid, ind, curr_date, 30,
                                  board=instrument.board_id)
            for ind in ("rsi", "macd", "close_50_sma")
        }
    except Exception as exc:  # noqa: BLE001 — indicators enrich, never block
        ctx["indicators_error"] = str(exc)
    try:
        ctx["fundamentals_report"] = fundamentals_report(instrument.secid, curr_date,
                                                         board=instrument.board_id)
    except Exception as exc:  # noqa: BLE001
        ctx["fundamentals_error"] = str(exc)
    if include_news:
        agg = fetch_all(instrument=instrument, days=news_days, limit=60)
        ctx["news_report"] = news_report(
            agg.items, instrument.secid,
            _shift_days(curr_date, news_days), curr_date, linked=True,
        )
        ctx["news_statuses"] = [s.to_dict() for s in agg.statuses]
    if portfolio_ctx:
        ctx["portfolio"] = portfolio_ctx
    return ctx


def _shift_days(date_str: str, days: int) -> str:
    from datetime import timedelta

    base = datetime.strptime(date_str, "%Y-%m-%d")
    return (base - timedelta(days=int(days))).strftime("%Y-%m-%d")


def prepare_order(
    proposal: TradeProposal | dict,
    portfolio_ctx: dict,
    settings: dict | None,
    *,
    open_order_count: int = 0,
    lot_size: int = 1,
) -> dict:
    """Pre-trade preview (§5.5): side, lots, est. amount + risk verdict."""
    prop = proposal.to_dict() if isinstance(proposal, TradeProposal) else dict(proposal)
    price = prop.get("limit_price") or (portfolio_ctx.get("last_price")
                                        if portfolio_ctx else None)
    if prop["side"] == "buy" and not prop.get("lots"):
        if price:
            sizing = size_position("buy", float(price), portfolio_ctx, settings,
                                   lot_size=lot_size)
            prop["lots"] = sizing["lots"]
            prop["estimated_amount"] = round(sizing["lots"] * float(price), 2)
    if prop["side"] == "buy" and prop.get("lots") and price and not prop.get("estimated_amount"):
        prop["estimated_amount"] = round(prop["lots"] * float(price), 2)
    risk = check_proposal(prop, portfolio_ctx, settings,
                          open_order_count=open_order_count)
    limits = effective_limits(settings)
    return {
        "proposal": prop,
        "risk": risk.to_dict(),
        "requires_confirmation": bool(limits["require_confirmation"]),
        "environment": "SANDBOX" if portfolio_ctx.get("sandbox") else "PRODUCTION",
        "cash": portfolio_ctx.get("cash"),
        "portfolio_total": portfolio_ctx.get("total_value"),
        "preview_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def place_confirmed_order(
    client,
    account_id: str,
    preview: dict,
    *,
    confirm: bool,
    portfolio_ctx: dict,
    settings: dict | None,
) -> dict:
    """Place a sandbox order — only with fresh risk check + confirm=True."""
    from tinvest.orders import post_order  # root package, absolute import

    if not confirm:
        return {"placed": False, "reason": "confirmation required (§6.2 step 9)"}
    if preview.get("environment") != "SANDBOX":
        return {"placed": False, "reason": "only SANDBOX previews can be placed"}
    prop = preview.get("proposal") or {}
    risk = check_proposal(prop, portfolio_ctx, settings)
    if not risk.allowed:
        return {"placed": False, "reason": "risk check failed on fresh state",
                "violations": risk.violations}
    if prop.get("order_type") == "limit" and not prop.get("limit_price"):
        return {"placed": False, "reason": "limit order requires price"}
    result = post_order(
        client,
        account_id=account_id,
        figi=prop.get("figi") or _figi_for(prop.get("ticker")),
        direction=prop["side"],
        quantity_lots=int(prop["lots"]),
        order_type=prop.get("order_type", "market"),
        price=prop.get("limit_price"),
        client_order_id=preview.get("client_order_id"),
    )
    result["placed"] = True
    result["preview_id"] = preview.get("preview_id")
    result["state"] = map_order_state(result.get("status", ""))
    return result


def _figi_for(ticker: str | None) -> str:
    if not ticker:
        raise ValueError("ticker required")
    return resolve_instrument(ticker).secid


def track_order(client, account_id: str, order_id: str) -> dict:
    from tinvest.orders import get_order_state

    order = get_order_state(client, account_id, order_id)
    return {
        "order_id": order.order_id,
        "api_status": order.status,
        "state": map_order_state(order.status),
        "lots_requested": order.lots_requested,
        "lots_executed": order.lots_executed,
        "partial": order.is_partial,
        "terminal": order.is_terminal,
        "message": order.message,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# --- §11 lessons from closed trades ---------------------------------

def _lessons_path() -> Path:
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(home) / "tinvest" / "lessons.jsonl"


def record_lesson(
    *,
    proposal: dict,
    decision_context: dict,
    execution: dict,
    realized_pnl: float | None,
    explanation: str,
    enabled: bool = True,
) -> dict:
    lesson = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "proposal": proposal,
        "decision_context": decision_context,  # news/indicators at decision time
        "execution": execution,
        "realized_pnl": realized_pnl,
        "explanation": explanation,
        "enabled": enabled,
        "used_in_future_runs": False,
    }
    path = _lessons_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(lesson, ensure_ascii=False) + "\n")
    return lesson


def load_lessons(*, enabled_only: bool = True) -> list[dict]:
    path = _lessons_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if enabled_only and not row.get("enabled", True):
            continue
        out.append(row)
    return out


def set_lesson_enabled(lesson_id: str, enabled: bool) -> bool:
    return _rewrite_lesson(lesson_id, {"enabled": bool(enabled)})


def delete_lesson(lesson_id: str) -> bool:
    path = _lessons_path()
    if not path.exists():
        return False
    rows = []
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id") == lesson_id:
            found = True
            continue
        rows.append(row)
    if found:
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return found


def _rewrite_lesson(lesson_id: str, patch: dict) -> bool:
    path = _lessons_path()
    if not path.exists():
        return False
    rows, found = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id") == lesson_id:
            row.update(patch)
            found = True
        rows.append(row)
    if found:
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return found


def lessons_block(max_items: int = 10) -> str:
    """Lessons injected into future analyst context (§11 step 8)."""
    lessons = [x for x in load_lessons() if x.get("used_in_future_runs") is not True]
    if not lessons:
        return ""
    lines = ["# Lessons from past closed trades (informational, not rules)"]
    for row in lessons[-max_items:]:
        lines.append(
            f"- [{row['id']}] {row.get('proposal', {}).get('side', '?')} "
            f"{row.get('proposal', {}).get('ticker', '?')}: "
            f"pnl={row.get('realized_pnl')}; {str(row.get('explanation', ''))[:300]}"
        )
    return "\n".join(lines)
