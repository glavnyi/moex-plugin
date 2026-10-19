"""Closed-deal detector (§11): FIFO buy/sell pairing from operations."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from moex.analytics.deals import detect_closed_deals  # noqa: E402


def _trade(op_id, type_, payment, date, figi="F1"):
    return {
        "operation_id": op_id,
        "type": type_,
        "status": "OPERATION_STATE_EXECUTED",
        "payment": payment,
        "figi": figi,
        "date": date,
    }


def _fee(op_id, payment, date):
    return {
        "operation_id": op_id,
        "type": "Удержание комиссии за операцию",
        "status": "OPERATION_STATE_EXECUTED",
        "payment": payment,
        "figi": "",
        "date": date,
    }


def test_simple_round_trip_produces_one_deal():
    ops = [
        _trade("b1", "Покупка ценных бумаг", -1000.0, "2026-10-15T10:00:00"),
        _fee("c1", -0.5, "2026-10-15T10:00:00"),
        _trade("s1", "Продажа ценных бумаг", 1100.0, "2026-10-15T11:00:00"),
        _fee("c2", -0.5, "2026-10-15T11:00:00"),
    ]
    deals = detect_closed_deals(ops)
    assert len(deals) == 1
    d = deals[0]
    assert d["buy_payment"] == -1000.0
    assert d["sell_payment"] == 1100.0
    assert d["pnl"] == 99.0  # 100 gross - 1.0 fees
    assert set(d["trade_op_ids"]) == {"b1", "s1"}
    assert set(d["commission_op_ids"]) == {"c1", "c2"}
    assert d["opened_at"] == "2026-10-15T10:00:00"
    assert d["closed_at"] == "2026-10-15T11:00:00"


def test_partial_sell_keeps_leftover_buy_lot():
    ops = [
        _trade("b1", "Покупка ценных бумаг", -2000.0, "2026-10-15T10:00:00"),
        _trade("s1", "Продажа ценных бумаг", 900.0, "2026-10-15T11:00:00"),
    ]
    deals = detect_closed_deals(ops)
    assert len(deals) == 1
    assert deals[0]["buy_payment"] == -900.0
    assert deals[0]["pnl"] == 0.0  # no fees attached in window
    # The residual 1100 buy lot is still in the FIFO book:
    ops2 = ops + [_trade("s2", "Продажа ценных бумаг", 1100.0,
                         "2026-10-15T12:00:00")]
    deals2 = detect_closed_deals(ops2)
    assert len(deals2) == 2
    assert deals2[1]["buy_payment"] == -1100.0


def test_sell_without_buy_is_ignored():
    ops = [_trade("s1", "Продажа ценных бумаг", 500.0, "2026-10-15T10:00:00")]
    assert detect_closed_deals(ops) == []


def test_fees_outside_window_not_attached():
    ops = [
        _trade("b1", "Покупка ценных бумаг", -1000.0, "2026-10-15T10:00:00"),
        _fee("far", -5.0, "2026-10-15T08:00:00"),   # before the buy
        _trade("s1", "Продажа ценных бумаг", 1050.0, "2026-10-15T11:00:00"),
    ]
    deals = detect_closed_deals(ops)
    assert deals[0]["pnl"] == 50.0
    assert deals[0]["commission_op_ids"] == []


def test_multi_figi_books_are_independent():
    ops = [
        _trade("b1", "Покупка ценных бумаг", -100.0, "2026-10-15T10:00:00",
               figi="FA"),
        _trade("b2", "Покупка ценных бумаг", -200.0, "2026-10-15T10:01:00",
               figi="FB"),
        _trade("s1", "Продажа ценных бумаг", 120.0, "2026-10-15T11:00:00",
               figi="FA"),
    ]
    deals = detect_closed_deals(ops)
    assert len(deals) == 1
    assert deals[0]["figi"] == "FA"
    assert deals[0]["pnl"] == 20.0


def test_commission_shared_across_overlapping_deals_not_double_counted():
    ops = [
        _trade("b1", "Покупка ценных бумаг", -1000.0, "2026-10-15T10:00:00"),
        _trade("s1", "Продажа ценных бумаг", 1010.0, "2026-10-15T10:30:00"),
        _trade("b2", "Покупка ценных бумаг", -500.0, "2026-10-15T10:30:00"),
        _fee("c1", -1.0, "2026-10-15T10:30:00"),
        _trade("s2", "Продажа ценных бумаг", 520.0, "2026-10-15T11:00:00"),
    ]
    deals = detect_closed_deals(ops)
    assert len(deals) == 2
    # The single fee lands in deal 1's window (10:00–10:30) and is consumed;
    # deal 2 gets nothing rather than stealing it back.
    assert deals[0]["commission_op_ids"] == ["c1"]
    assert deals[1]["commission_op_ids"] == []
    assert deals[0]["pnl"] == 9.0
    assert deals[1]["pnl"] == 20.0
