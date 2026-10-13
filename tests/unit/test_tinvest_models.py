from __future__ import annotations

from tinvest.client import quotation_to_float
from tinvest.models import Account, Money, Order, account_balance_view


def test_quotation_to_float_units_nano():
    assert quotation_to_float({"units": "12", "nano": 500_000_000}) == 12.5


def test_quotation_to_float_negative():
    assert quotation_to_float({"units": "-3", "nano": -250_000_000}) == -3.25


def test_quotation_to_float_none_safe():
    assert quotation_to_float(None) is None
    assert quotation_to_float({}) == 0.0  # no units → zero, never a crash


def test_money_from_api():
    m = Money.from_api({"currency": "rub", "units": "100", "nano": 0})
    assert m is not None
    assert m.amount == 100.0
    assert m.currency == "rub"


def test_account_from_api():
    a = Account.from_api({
        "accountId": "acc-1", "name": "Sandbox", "status": "open",
        "openedDate": "2026-10-01T00:00:00Z", "type": "TYPE_ACCOUNT",
        "cashBalance": {"currency": "rub", "units": "1000", "nano": 0},
    })
    assert a.id == "acc-1"
    assert a.name == "Sandbox"
    assert a.opened.startswith("2026-10-01")
    assert a.cash_currency == "rub"


def test_order_state_transitions():
    o = Order.from_api({
        "orderId": "o-1", "direction": "direction_buy",
        "lotsRequested": "10", "lotsExecuted": "4",
        "executionReportStatus": "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
        "figi": "SBER",
    })
    assert o.is_partial
    assert not o.is_terminal


def test_order_terminal_filled():
    o = Order.from_api({
        "orderId": "o-2", "direction": "direction_sell",
        "lotsRequested": "5", "lotsExecuted": "5",
        "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
        "figi": "GAZP",
    })
    assert o.is_terminal
    assert not o.is_partial


def test_account_balance_view():
    view = account_balance_view({
        "totalPortfolio": {"units": "200000", "nano": 0},
        "totalCurrencies": {"units": "50000", "nano": 0},
        "totalShares": {"units": "150000", "nano": 0},
    }, "acc-1")
    assert view["account_id"] == "acc-1"
    assert view["total"] == 200000.0
    assert view["currencies"] == 50000.0
    assert view["shares"] == 150000.0
    assert view["synced_at"]


def test_map_order_state_rest_enum():
    from moex.analytics.workflow import map_order_state

    assert map_order_state("EXECUTION_REPORT_STATUS_NEW") == "accepted"
    assert map_order_state("EXECUTION_REPORT_STATUS_FILL") == "filled"
    assert map_order_state("EXECUTION_REPORT_STATUS_PARTIALLYFILL") == "partial"
    assert map_order_state("EXECUTION_REPORT_STATUS_CANCELLED") == "cancelled"
    assert map_order_state("EXECUTION_REPORT_STATUS_REJECTED") == "rejected"
    # legacy aliases
    assert map_order_state("NEW") == "accepted"
    assert map_order_state("WEIRD") == "unknown"
    assert map_order_state("") == "unknown"
