"""Risk checks before any sandbox order (spec §6.3).

Limits live in settings (`risk` section, GUI-editable, persisted by
moex.config). Every check is re-run against the *current* portfolio
context immediately before placement — never cached from analysis time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_RISK = {
    "max_order_amount": 500_000.0,        # руб. на одну заявку
    "max_position_pct": 30.0,             # % капитала в одной позиции
    "max_issuer_pct": 30.0,               # % капитала на одного эмитента
    "max_open_orders": 5,                 # одновременных открытых заявок
    "allowed_instruments": [],            # [] = все разрешены
    "allow_new_buys": True,
    "allow_selling_positions": True,
    "max_risk_per_trade_pct": 5.0,        # % капитала на сделку
    "daily_loss_limit": None,             # руб. или None (нет надёжного расчёта)
    "require_confirmation": True,         # явное подтверждение каждой заявки
    "automation_sandbox_only": True,      # автоматизация только для песочницы
}


@dataclass
class RiskResult:
    allowed: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sized: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "violations": self.violations,
            "warnings": self.warnings,
            "sized": self.sized,
        }


def effective_limits(settings: dict | None) -> dict:
    merged = dict(DEFAULT_RISK)
    risk = (settings or {}).get("risk") or {}
    for key, value in risk.items():
        if key in merged and value is not None:
            merged[key] = value
        elif key in DEFAULT_RISK:
            merged[key] = value
    return merged


def _portfolio_total(ctx: dict) -> float:
    total = ctx.get("total_value")
    if total:
        return float(total)
    # Fallback: cash + position values (authoritative fields only).
    cash = float(ctx.get("cash") or 0.0)
    pos = sum(float(p.get("value") or 0.0) for p in ctx.get("positions") or [])
    return cash + pos


def _position_value(ctx: dict, ticker: str) -> float:
    for p in ctx.get("positions") or []:
        if str(p.get("ticker", "")).upper() == str(ticker).upper():
            return float(p.get("value") or 0.0)
    return 0.0


def _daily_realized_pnl() -> float:
    """Realized PnL (RUB) for the current Moscow trading day.

    Source: closed-deal lessons (§11) — each carries ``realized_pnl`` for a
    completed round-trip. Summing today's entries gives the day's realized
    result. Lazy import avoids a circular dependency (analytics imports
    risk checks). Returns 0.0 when no lessons exist yet.
    """
    try:
        from ..analytics import load_lessons
    except Exception:  # noqa: BLE001 — analytics unavailable → no data
        return 0.0
    msk = timezone(timedelta(hours=3))
    today = datetime.now(msk).date()
    total = 0.0
    for lesson in load_lessons(enabled_only=False):
        pnl = lesson.get("realized_pnl")
        if pnl is None:
            continue
        created = lesson.get("created_at") or ""
        try:
            if datetime.fromisoformat(str(created)).astimezone(msk).date() == today:
                total += float(pnl)
        except (ValueError, TypeError):
            continue
    return round(total, 2)


def check_proposal(
    proposal: dict,
    portfolio_ctx: dict,
    settings: dict | None,
    *,
    open_order_count: int = 0,
) -> RiskResult:
    """Validate a trade proposal against risk limits + live account state.

    ``proposal``: {side: buy|sell|hold, ticker, lots, order_type, price,
    limit_price, estimated_amount}
    """
    limits = effective_limits(settings)
    result = RiskResult()
    side = str(proposal.get("side", "")).lower()
    ticker = str(proposal.get("ticker", "")).upper()
    amount = float(proposal.get("estimated_amount") or 0.0)
    lots = int(proposal.get("lots") or 0)

    if side in {"hold", "", "none"}:
        result.sized = dict(proposal)
        result.warnings.append("hold — бронирование не требуется")
        return result

    if amount <= 0:
        # §6.3 states every limit in money. A proposal with no price (a
        # market order whose quote is missing) yields amount 0, and `if amount
        # and …` below would then check *nothing* — a live bug: a market buy
        # came back allowed with zero limits evaluated. Unknown amount blocks.
        result.violations.append(
            "сумма заявки неизвестна (нет цены инструмента) — лимиты §6.3 "
            "проверить нельзя, заявка отклонена"
        )

    if portfolio_ctx.get("sandbox") is False:
        # §5.1: a production context is only tradeable when the connection
        # mode itself says so — and that mode had to be set with
        # confirm_live, so nothing here can widen it. `automation_sandbox_only`
        # is deliberately *not* consulted: it scopes automation (§13), not the
        # venue, and is enforced by the auto-trading gates.
        mode = str(((settings or {}).get("tinvest") or {}).get("mode") or "").lower()
        if mode != "live_trading":
            result.violations.append(
                f"боевой торговый контекст при tinvest.mode={mode or '(не задан)'} — "
                "для реальных заявок нужен режим live_trading (§5.1)"
            )

    allowed_list = [str(x).upper() for x in limits.get("allowed_instruments") or []]
    if allowed_list and ticker and ticker not in allowed_list:
        result.violations.append(
            f"{ticker} отсутствует в списке допустимых инструментов"
        )

    if side == "buy" and not limits["allow_new_buys"]:
        result.violations.append("новые покупки запрещены настройкой")
    if side == "sell" and not limits["allow_selling_positions"]:
        result.violations.append("продажи запрещены настройкой")
    if side == "sell" and portfolio_ctx is not None:
        held = any(
            str(p.get("ticker", "")).upper() == ticker
            for p in portfolio_ctx.get("positions") or []
        )
        if not held:
            result.violations.append(
                f"продажа {ticker}: позиция не найдена в портфеле (шорт запрещён)"
            )

    total = _portfolio_total(portfolio_ctx)
    cash = float(portfolio_ctx.get("cash") or 0.0)

    if open_order_count >= int(limits["max_open_orders"]):
        result.violations.append(
            f"превышен лимит открытых заявок ({open_order_count}/"
            f"{limits['max_open_orders']})"
        )

    if side == "buy":
        if amount and amount > float(limits["max_order_amount"]):
            result.violations.append(
                f"сумма заявки {amount:.0f} > максимума "
                f"{float(limits['max_order_amount']):.0f}"
            )
        if amount and amount > cash:
            result.violations.append(
                f"недостаточно средств: нужно {amount:.0f}, доступно {cash:.0f}"
            )
        if total and amount:
            existing = _position_value(portfolio_ctx, ticker)
            pct = (existing + amount) / total * 100.0
            if pct > float(limits["max_position_pct"]):
                result.violations.append(
                    f"позиция {ticker} составит {pct:.1f}% > лимита "
                    f"{limits['max_position_pct']}%"
                )
            if pct > float(limits["max_risk_per_trade_pct"]) + float(
                limits["max_position_pct"]
            ) * 0.5:
                result.warnings.append(
                    f"концентрация {ticker} {pct:.1f}% — высокая"
                )

    if side == "sell" and total:
        existing = _position_value(portfolio_ctx, ticker)
        if existing / total * 100.0 > float(limits["max_issuer_pct"]):
            result.warnings.append(
                f"доля эмитента {ticker} превышала "
                f"{limits['max_issuer_pct']}% — продажа снижает риск"
            )

    if limits.get("daily_loss_limit") is not None:
        realized = _daily_realized_pnl()
        limit = abs(float(limits["daily_loss_limit"]))
        if realized <= -limit:
            result.violations.append(
                f"дневной лимит потерь превышен: realized {realized:.2f} RUB "
                f"при лимите -{limit:.2f} (§6.3)"
            )
        else:
            result.warnings.append(
                f"дневной realized PnL: {realized:.2f} RUB "
                f"(лимит потерь -{limit:.2f})"
            )

    result.allowed = not result.violations
    result.sized = dict(proposal)
    return result


def size_position(
    side: str,
    price: float,
    portfolio_ctx: dict,
    settings: dict | None,
    *,
    lot_size: int = 1,
) -> dict:
    """Suggest lots under max_order_amount / position-pct constraints."""
    limits = effective_limits(settings)
    total = _portfolio_total(portfolio_ctx)
    cash = float(portfolio_ctx.get("cash") or 0.0)
    price = float(price or 0)
    if side != "buy" or price <= 0:
        return {"lots": 0, "reason": "sizing available only for buys with price"}
    budget = min(float(limits["max_order_amount"]), cash)
    if total:
        budget = min(budget, total * float(limits["max_position_pct"]) / 100.0)
    lots = int(budget // (price * max(1, lot_size)))
    return {
        "lots": max(0, lots),
        "budget": round(budget, 2),
        "lot_size": lot_size,
        "price": price,
        "assumptions": [
            "max_order_amount", "max_position_pct", "cash",
        ],
    }
