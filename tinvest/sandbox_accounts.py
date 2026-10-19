"""Sandbox account management (list/create/close/pay-in) — spec §5.2, §5.4."""

from __future__ import annotations

from .client import TInvestClient, TInvestError
from .models import Account, Money, quotation_to_float


def require_sandbox(client: TInvestClient) -> TInvestClient:
    """Account open/close/pay-in are virtual-money operations (spec §5.2, §5.4).

    The production API exposes no equivalent, so these stay sandbox-only
    regardless of mode — a live account is opened at the broker, not here.
    """
    if not client.sandbox:
        raise TInvestError(
            "account open/close/pay-in is a sandbox (virtual money) operation; "
            f"tinvest.mode={client.mode!r} targets {client.environment}"
        )
    return client


def list_accounts(client: TInvestClient) -> list[Account]:
    if client.sandbox:
        rows = client.get_sandbox_accounts()
    else:
        rows = client.get_accounts()
    return [Account.from_api(r) for r in rows]


def create_account(client: TInvestClient, currency: str = "rub") -> Account:
    require_sandbox(client)
    return Account.from_api(client.open_sandbox_account(currency))


def close_account(client: TInvestClient, account_id: str) -> None:
    require_sandbox(client)
    client.close_sandbox_account(account_id)


def pay_in(client: TInvestClient, account_id: str, amount: float,
           currency: str = "rub") -> dict:
    """Top up virtual money. Result includes API's money amount."""
    require_sandbox(client)
    if amount <= 0:
        raise TInvestError("amount must be positive")
    payload = client.sandbox_pay_in(account_id, amount, currency)
    # SandboxPayInResponse: { balance: MoneyValue } (openapi v1.4); keep
    # legacy "money" as a fallback for older gateways.
    money = payload.get("balance") or payload.get("money") or payload
    return {
        "account_id": account_id,
        "amount": quotation_to_float(money) if isinstance(money, dict) else amount,
        "currency": (money.get("currency") if isinstance(money, dict) else currency) or currency,
        "operation": "sandbox-pay-in",
        "result": "success",
    }


def account_overview(client: TInvestClient, account_id: str) -> dict:
    """Balance + available/blocked split when the API provides it."""
    accounts = {a.id: a for a in list_accounts(client)}
    account = accounts.get(account_id)
    portfolio = client.call(
        "SandboxService" if client.sandbox else "OperationsService",
        "GetSandboxPortfolio" if client.sandbox else "GetPortfolio",
        {"accountId": account_id},
    )
    withdraw = {}
    try:
        withdraw = client.call("SandboxService", "GetSandboxWithdrawLimits",
                               {"accountId": account_id})
    except TInvestError:
        pass
    return {
        "account": account.__dict__ if account else {"id": account_id, "status": "unknown"},
        "portfolio": portfolio,
        "available_withdraw": quotation_to_float(withdraw.get("availableForWithdraw")),
        "blocked": quotation_to_float(withdraw.get("blocked")),
        "source": "t-invest-api",
        "sandbox": client.sandbox,
    }
