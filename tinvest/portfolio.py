"""Portfolio and positions — spec §5.3, §6.1."""

from __future__ import annotations

from datetime import datetime, timezone

from .client import TInvestClient, TInvestError, quotation_to_float
from .models import PortfolioState, account_balance_view


def _portfolio_service_call(client: TInvestClient, account_id: str) -> dict:
    if client.sandbox:
        return client.call("SandboxService", "GetSandboxPortfolio",
                           {"accountId": account_id})
    return client.call("OperationsService", "GetPortfolio",
                       {"accountId": account_id})


def get_portfolio(client: TInvestClient, account_id: str) -> PortfolioState:
    data = _portfolio_service_call(client, account_id)
    return PortfolioState.from_api(account_id, data)


def get_positions(client: TInvestClient, account_id: str) -> dict:
    """Money + securities split (spec §5.3).

    Same contract on both venues — ``GetSandboxPositions`` in the sandbox,
    ``OperationsService/GetPositions`` on production.
    """
    if client.sandbox:
        return client.call("SandboxService", "GetSandboxPositions",
                           {"accountId": account_id})
    return client.call("OperationsService", "GetPositions",
                       {"accountId": account_id})


def balance_view(client: TInvestClient, account_id: str) -> dict:
    data = _portfolio_service_call(client, account_id)
    view = account_balance_view(data, account_id)
    try:
        positions = get_positions(client, account_id)
        money = positions.get("money") or []
        if money:
            view["cash_by_currency"] = [
                {
                    "currency": m.get("currency"),
                    "amount": quotation_to_float(m),
                    "blocked": quotation_to_float(
                        next((b for b in (positions.get("blocked") or [])
                              if b.get("currency") == m.get("currency")), None)
                    ) or 0.0,
                }
                for m in money
            ]
    except TInvestError:
        pass
    view["synced_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return view


def portfolio_context(client: TInvestClient, account_id: str) -> dict:
    """Compact dict fed into the analyst graph (spec §6.1) with timestamps."""
    state = get_portfolio(client, account_id)
    view = balance_view(client, account_id)
    # Sandbox GetSandboxPortfolio returns totalPortfolio/totalCurrencies as
    # null (live-verified): cash then arrives as a RUB000UTSTOM currency
    # *position*, which balance_view already folds into cash_by_currency.
    # Fall back to that sum so the risk engine sees real money instead of
    # treating cash as 0 and blocking every buy ("недостаточно средств"
    # at 1M RUB balance — live bug).
    cash = view.get("currencies")
    if not cash:
        cash = sum(
            float(c.get("amount") or 0.0)
            for c in (view.get("cash_by_currency") or [])
        ) or None
    positions_out = []
    for p in state.positions:
        cur = p.current_price.amount if p.current_price else None
        qty = float(p.quantity or 0.0)
        avg = p.avg_price.amount if p.avg_price else None
        # Sandbox portfolio omits the "value" block (live-verified) —
        # derive it from quantity × current price.
        val = p.value.amount if p.value else (
            (cur * qty) if (cur is not None and qty) else None
        )
        pnl = p.expected_yield.amount if p.expected_yield else (
            ((cur - avg) * qty)
            if (cur is not None and avg is not None and qty)
            else None
        )
        positions_out.append({
            "ticker": p.ticker,
            "figi": p.figi,
            "type": p.instrument_type,
            "quantity_lots": p.quantity,
            "avg_price": avg,
            "current_price": cur,
            "value": val,
            "unrealized_pnl": pnl,
            "currency": (p.current_price.currency if p.current_price else ""),
        })
    total = state.total_portfolio.amount if state.total_portfolio else None
    if not total:
        # Cash + non-currency position value (currency positions ARE cash).
        pos_value = sum(
            float(p["value"] or 0.0)
            for p in positions_out
            if str(p.get("type") or "").lower() != "currency"
        )
        total = (float(cash or 0.0) + pos_value) or None
    return {
        "account_id": account_id,
        "mode": client.mode,
        "environment": client.environment,
        "sandbox": client.sandbox,
        "cash": cash,
        "cash_by_currency": view.get("cash_by_currency"),
        "total_value": total,
        "currency": state.total_portfolio.currency if state.total_portfolio else "rub",
        "positions": positions_out,
        "fetched_at": state.fetched_at.strftime("%Y-%m-%d %H:%M UTC"),
        "source": state.source,
    }
