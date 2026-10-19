"""Dataclasses mirroring T-Invest API payloads (only fields we surface)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .client import quotation_to_float


def _iso(ts) -> str:
    if not ts:
        return ""
    return str(ts)[:19].replace("T", " ")


@dataclass
class Account:
    id: str
    name: str
    type: str
    status: str
    opened: str = ""
    cash_currency: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "Account":
        return cls(
            id=data.get("id") or data.get("accountId") or "",
            name=data.get("name") or "",
            type=data.get("type") or "",
            status=data.get("status") or "",
            opened=_iso(data.get("openedDate")),
            cash_currency=(data.get("cashBalance") or {}).get("currency") or "",
        )


@dataclass
class Money:
    amount: float
    currency: str

    @classmethod
    def from_api(cls, data: dict | None) -> "Money | None":
        if not data:
            return None
        value = quotation_to_float(data)
        if value is None:
            return None
        return cls(round(value, 6), data.get("currency") or "")


@dataclass
class Position:
    figi: str = ""
    instrument_type: str = ""
    ticker: str = ""
    name: str = ""
    quantity: float = 0.0
    lot_size: int = 1
    avg_price: Money | None = None
    current_price: Money | None = None
    value: Money | None = None
    nkd: Money | None = None
    expected_yield: Money | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Position":
        block = data.get("instrumentStatus") or data
        qty = block.get("quantityLots")
        if qty is None:
            qty = block.get("quantity") or 0
        return cls(
            figi=block.get("figi") or "",
            instrument_type=block.get("instrumentType") or "",
            ticker=block.get("ticker") or block.get("figi") or "",
            name=block.get("name") or "",
            quantity=quotation_to_float(qty) or 0.0,
            lot_size=int(block.get("lot") or block.get("lotSize") or 1),
            avg_price=Money.from_api(block.get("averagePositionPriceFifo")
                                     or block.get("averagePositionPrice")),
            current_price=Money.from_api(block.get("currentPrice")),
            value=Money.from_api(block.get("marketValue")),
            nkd=Money.from_api(block.get("currentNkd")),
            expected_yield=Money.from_api(block.get("expectedYield")),
        )


@dataclass
class PortfolioState:
    account_id: str
    total_shares: Money | None = None
    total_bonds: Money | None = None
    total_etf: Money | None = None
    total_currencies: Money | None = None
    total_futures: Money | None = None
    total_portfolio: Money | None = None
    positions: list[Position] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "t-invest-api"

    @classmethod
    def from_api(cls, account_id: str, data: dict) -> "PortfolioState":
        positions = [Position.from_api(p) for p in data.get("positions") or []]
        return cls(
            account_id=account_id,
            total_shares=Money.from_api(data.get("totalShares")),
            total_bonds=Money.from_api(data.get("totalBonds")),
            total_etf=Money.from_api(data.get("totalEtf")),
            total_currencies=Money.from_api(data.get("totalCurrencies")),
            total_futures=Money.from_api(data.get("totalFutures")),
            total_portfolio=Money.from_api(data.get("totalPortfolio")),
            positions=positions,
        )


@dataclass
class Order:
    order_id: str
    client_order_id: str
    figi: str = ""
    direction: str = ""
    order_type: str = ""
    status: str = ""
    lots_requested: int = 0
    lots_executed: int = 0
    price: Money | None = None
    executed_price: Money | None = None
    message: str = ""
    created_at: str = ""

    @classmethod
    def from_api(cls, data: dict) -> "Order":
        executed = data.get("executedPrice") or data.get("averagePositionPrice")
        # Sandbox v1 GetOrders often omits `price` for resting LIMIT orders
        # and only echoes the limit price under executedPrice (live-verified)
        # — mirror it so callers can show the working price.
        price = data.get("price") or (
            executed if "LIMIT" in str(data.get("orderType") or "") else None
        )
        return cls(
            order_id=data.get("orderId") or "",
            client_order_id=data.get("clientId") or "",
            figi=data.get("figi") or "",
            direction=data.get("direction") or "",
            order_type=data.get("orderType") or "",
            status=data.get("executionReportStatus") or "",
            lots_requested=int(data.get("lotsRequested") or 0),
            lots_executed=int(data.get("lotsExecuted") or 0),
            price=Money.from_api(price),
            executed_price=Money.from_api(executed),
            message=data.get("message") or "",
            created_at=_iso(data.get("createdAt")),
        )

    @property
    def is_terminal(self) -> bool:
        # REST enum names + short aliases (v1 OrderExecutionReportStatus).
        status = (self.status or "").upper()
        return status in {
            "FILL", "FILLED", "CANCELLED", "CANCELED", "REJECTED",
            "EXECUTION_REPORT_STATUS_FILL",
            "EXECUTION_REPORT_STATUS_CANCELLED",
            "EXECUTION_REPORT_STATUS_REJECTED",
        }

    @property
    def is_partial(self) -> bool:
        return 0 < self.lots_executed < self.lots_requested


def account_balance_view(portfolio: dict, account_id: str) -> dict:
    """Money breakdown straight from GetSandboxPortfolio/GetPortfolio."""
    return {
        "account_id": account_id,
        "total": quotation_to_float(portfolio.get("totalPortfolio")),
        "shares": quotation_to_float(portfolio.get("totalShares")),
        "bonds": quotation_to_float(portfolio.get("totalBonds")),
        "etf": quotation_to_float(portfolio.get("totalEtf")),
        "currencies": quotation_to_float(portfolio.get("totalCurrencies")),
        "futures": quotation_to_float(portfolio.get("totalFutures")),
        "source": "t-invest-api",
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
