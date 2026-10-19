"""Analysis → proposal → risk → confirmation → order → lessons
(spec §6.2, §11).

Two callers reach ``place_confirmed_order``: the GUI, whose ``confirm=True``
follows an explicit user click; and the auto-trading cycle
(``integration/autotrade_service.py``), which supplies the "отдельное
разрешение" §6.2 asks for by passing its own gate chain first. In both cases
the risk check re-runs against fresh portfolio state immediately before the
send — there is no path to an order that skips it.
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
        # Point-in-time: лента обрезается по дате ячейки (как
        # snapshot_news_window в gui_adapter) — иначе историческая ячейка
        # увидела бы свежие новости: без until фид отдаёт последние дни.
        agg = fetch_all(instrument=instrument, days=news_days, limit=60,
                        until=_news_until(curr_date))
        ctx["news_report"] = news_report(
            agg.items, instrument.secid,
            _shift_days(curr_date, news_days), curr_date, linked=True,
        )
        ctx["news_statuses"] = [s.to_dict() for s in agg.statuses]
    if portfolio_ctx:
        ctx["portfolio"] = portfolio_ctx
    return ctx


def _news_until(curr_date: str):
    """Обрезка ленты: конец дня ячейки, но не позже «сейчас»."""
    from datetime import timedelta

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return min(now, end_dt + timedelta(days=1) - timedelta(seconds=1))


def _shift_days(date_str: str, days: int) -> str:
    from datetime import timedelta

    base = datetime.strptime(date_str, "%Y-%m-%d")
    return (base - timedelta(days=int(days))).strftime("%Y-%m-%d")


def _price_hint(portfolio_ctx: dict | None, ticker, limit_price):
    """Best known price for a proposal: its own limit, then the caller's
    quote, then the current price of the position already held.

    ``portfolio_context()`` carries no top-level ``last_price`` (only the GUI
    preview adds it), so without this chain an auto-trade market order had no
    price at all and its amount stayed unknown.
    """
    if limit_price:
        return limit_price
    ctx = portfolio_ctx or {}
    if ctx.get("last_price"):
        return ctx["last_price"]
    want = str(ticker or "").upper()
    for p in ctx.get("positions") or []:
        if str(p.get("ticker") or "").upper() != want:
            continue
        if str(p.get("type") or "").lower() == "currency":
            continue
        if p.get("current_price"):
            return p["current_price"]
    return None


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
    price = _price_hint(portfolio_ctx, prop.get("ticker"), prop.get("limit_price"))
    if prop["side"] == "buy" and not prop.get("lots"):
        if price:
            sizing = size_position("buy", float(price), portfolio_ctx, settings,
                                   lot_size=lot_size)
            prop["lots"] = sizing["lots"]
    if prop["side"] == "sell" and not prop.get("lots"):
        # Default a bare SELL to the full held position (shorts are
        # forbidden by the risk engine anyway) — otherwise lots stay None
        # and the broker call crashes on int(None) (live UI bug).
        held_qty = None
        for p in (portfolio_ctx or {}).get("positions") or []:
            same = str(p.get("ticker") or "").upper() == str(prop.get("ticker") or "").upper()
            not_cash = str(p.get("type") or "").lower() != "currency"
            if same and not_cash and p.get("quantity_lots") is not None:
                held_qty = float(p["quantity_lots"])
                break
        if held_qty:
            prop["lots"] = max(1, int(held_qty))
    if prop["side"] in ("buy", "sell") and prop.get("lots") and price:
        # Always recompute: the proposal's own estimate knows nothing about
        # lot size, so for a lot≠1 instrument (MOEX bonds trade in 100s) the
        # stale figure understated exposure by that whole factor and let an
        # order past every money limit in §6.3.
        prop["estimated_amount"] = round(
            int(prop["lots"]) * float(price) * int(lot_size or 1), 2)
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
    open_order_count: int | None = None,
) -> dict:
    """Place an order — with a fresh risk check + confirm=True.

    Venue comes from the §5.1 connection mode: a ``SANDBOX`` preview needs a
    sandbox client, a ``PRODUCTION`` preview needs ``live_trading``. A
    read-only mode can never get here, because ``post_order`` refuses first.

    ``open_order_count`` is part of that fresh check (§6.3): both callers
    read the open orders right before calling, but the value was previously
    dropped, so ``max_open_orders`` was enforced only by the preview — the
    authoritative gate ran with 0. When it is omitted the count is re-read
    here; when that read fails the order is refused rather than placed
    unchecked.
    """
    from tinvest.orders import post_order  # root package, absolute import

    if not confirm:
        return {"placed": False, "reason": "confirmation required (§6.2 step 9)"}
    want_env = preview.get("environment")
    have_env = getattr(client, "environment", "SANDBOX")
    if want_env != have_env:
        return {"placed": False, "reason":
                f"preview environment {want_env} does not match client {have_env}"}
    if open_order_count is None:
        try:
            from tinvest.orders import list_orders

            open_order_count = len(list_orders(client, account_id))
        except Exception as exc:  # noqa: BLE001 — fail closed, report why
            return {"placed": False, "reason":
                    f"не удалось перечитать открытые заявки: {exc}"}
    prop = preview.get("proposal") or {}
    risk = check_proposal(prop, portfolio_ctx, settings,
                          open_order_count=int(open_order_count))
    if not risk.allowed:
        return {"placed": False, "reason": "risk check failed on fresh state",
                "violations": risk.violations}
    if prop.get("order_type") == "limit" and not prop.get("limit_price"):
        return {"placed": False, "reason": "limit order requires price"}
    result = post_order(
        client,
        account_id=account_id,
        figi=prop.get("figi") or _figi_for(client, prop.get("ticker"),
                                           isin=(preview.get("instrument")
                                                 or {}).get("isin")),
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


def _figi_for(client, ticker: str | None, isin: str | None = None) -> str:
    if not ticker:
        raise ValueError("ticker required")
    from tinvest.instruments import find_instrument_ref

    return find_instrument_ref(client, ticker, isin=isin)["figi"]


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
