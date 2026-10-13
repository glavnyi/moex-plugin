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
    """Money + securities split from SandboxService/GetSandboxPositions."""
    if not client.sandbox:
        raise TInvestError("GetSandboxPositions requires sandbox mode")
    data = client.call("SandboxService", "GetSandboxPositions",
                       {"accountId": account_id})
    return data


def balance_view(client: TInvestClient, account_id: str) -> dict:
    data = _portfolio_service_call(client, account_id)
    view = account_balance_view(data, account_id)
    try:
        positions = get_positions(client, account_id) if client.sandbox else {}
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
    return {
        "account_id": account_id,
        "sandbox": client.sandbox,
        "cash": view.get("currencies"),
        "cash_by_currency": view.get("cash_by_currency"),
        "total_value": state.total_portfolio.amount if state.total_portfolio else None,
        "currency": state.total_portfolio.currency if state.total_portfolio else "",
        "positions": [
            {
                "ticker": p.ticker,
                "figi": p.figi,
                "type": p.instrument_type,
                "quantity_lots": p.quantity,
                "avg_price": p.avg_price.amount if p.avg_price else None,
                "current_price": p.current_price.amount if p.current_price else None,
                "value": p.value.amount if p.value else None,
                "unrealized_pnl": p.expected_yield.amount if p.expected_yield else None,
                "currency": (p.current_price.currency if p.current_price else ""),
            }
            for p in state.positions
        ],
        "fetched_at": state.fetched_at.strftime("%Y-%m-%d %H:%M UTC"),
        "source": state.source,
    }
