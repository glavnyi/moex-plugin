"""Sandbox dashboard aggregation (spec §5 panel).

One endpoint feeds the panel: account balance (total / available / blocked),
current positions, closed deals with realized PnL, and PnL rolled up over
day / week / month / all-time windows.

PnL windows are anchored on the *deal close* time and use the Moscow
calendar day (MOEX session day), so "today's P&L" means today's trading
day for this market. Week/month are rolling 7/30-day windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .deals import _load_state, _parse_dt, _resolve_ticker, detect_closed_deals

_MSK = timezone(timedelta(hours=3))

# window key -> rolling width in days (None = today only, "all" = everything)
_WINDOWS: dict[str, int | None] = {
    "day": None,
    "week": 7,
    "month": 30,
    "all": None,
}


def _msk(value: str | None) -> datetime | None:
    dt = _parse_dt(value or "")
    return dt.astimezone(_MSK) if dt else None


def pnl_windows(deals: list[dict], *, now: datetime | None = None) -> dict:
    """Roll realized PnL over day / week / month / all-time windows.

    ``day`` = deals closed on the current Moscow calendar day; ``week`` /
    ``month`` = rolling 7- / 30-day lookback; ``all`` = every deal passed
    in. Each window carries its own deal count so win-rate style stats can
    be derived downstream without re-scanning.
    """
    now = (now or datetime.now(_MSK)).astimezone(_MSK)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    starts = {"day": day_start, "week": week_start, "month": month_start}
    out = {k: {"pnl": 0.0, "deals": 0} for k in _WINDOWS}
    for deal in deals:
        closed = _msk(deal.get("closed_at"))
        if not closed:
            continue
        pnl = float(deal.get("pnl") or 0.0)
        for key, start in starts.items():
            if closed >= start:
                out[key]["pnl"] += pnl
                out[key]["deals"] += 1
        out["all"]["pnl"] += pnl
        out["all"]["deals"] += 1
    for bucket in out.values():
        bucket["pnl"] = round(bucket["pnl"], 2)
    out["as_of"] = now.strftime("%Y-%m-%d %H:%M MSK")
    out["windows"] = {
        "day": "сегодня (MSK)",
        "week": "7 дней",
        "month": "30 дней",
        "all": "всё время",
    }
    return out


def build_dashboard(client, account_id: str, *, ops_limit: int = 500) -> dict:
    """Assemble every dashboard block from live broker data + operations."""
    from tinvest.operations import list_operations
    from tinvest.portfolio import portfolio_context

    pf = portfolio_context(client, account_id)

    # --- balance: total vs available (cash minus margin-blocked) ---
    cash_rows = pf.get("cash_by_currency") or []
    available = sum(float(r.get("amount") or 0.0) - float(r.get("blocked") or 0.0)
                    for r in cash_rows)
    blocked = sum(float(r.get("blocked") or 0.0) for r in cash_rows)
    balance = {
        "total": pf.get("total_value"),
        "available": round(available, 2),
        "blocked": round(blocked, 2),
        "currency": pf.get("currency") or "rub",
        "cash_by_currency": cash_rows,
        "synced_at": pf.get("fetched_at"),
    }

    # --- positions (currency rows are cash, shown as balance instead) ---
    positions = [p for p in (pf.get("positions") or [])
                 if str(p.get("type") or "").lower() != "currency"]
    unrealized = round(sum(float(p.get("unrealized_pnl") or 0.0)
                           for p in positions), 2)

    # --- closed deals + realized PnL windows ---
    ops = list_operations(client, account_id, limit=ops_limit)
    deals = detect_closed_deals(ops)
    state = _load_state()
    cache = state.get("figi_tickers") or {}
    deal_rows = []
    commissions_total = 0.0
    for op in ops:
        if "комисси" in str(op.get("type") or "").lower():
            commissions_total += float(op.get("payment") or 0.0)
    for d in sorted(deals, key=lambda x: str(x.get("closed_at") or ""),
                    reverse=True):
        deal_rows.append({
            "ticker": _resolve_ticker(client, d.get("figi") or "", cache),
            "figi": d.get("figi"),
            "opened_at": d.get("opened_at"),
            "closed_at": d.get("closed_at"),
            "buy_payment": d.get("buy_payment"),
            "sell_payment": d.get("sell_payment"),
            "commission": d.get("commission"),
            "pnl": d.get("pnl"),
        })

    return {
        "account_id": account_id,
        "sandbox": bool(getattr(client, "sandbox", True)),
        "balance": balance,
        "positions": positions,
        "positions_count": len(positions),
        "unrealized_pnl": unrealized,
        "pnl": pnl_windows(deals),
        "deals": deal_rows,
        "deals_count": len(deal_rows),
        "commissions_total": round(commissions_total, 2),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
