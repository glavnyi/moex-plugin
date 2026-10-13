from __future__ import annotations

from moex.analytics.proposal import extract_proposal, normalize_side


def test_normalize_side():
    assert normalize_side("BUY") == "buy"
    assert normalize_side("ПОКУПКА") == "buy"
    assert normalize_side("КУПИТЬ") == "buy"
    assert normalize_side("SELL") == "sell"
    assert normalize_side("ПРОДАТЬ") == "sell"
    assert normalize_side("whatever") == "hold"


REPORT = """\
## Торговое предложение

Итог: BUY 12 лотов SBER.
Рекомендуемая лимитная цена: 285,5.
Аргументы: рост прибыли и дивидендная отдача.
"""


def test_extract_proposal_buy_lots_price():
    p = extract_proposal(REPORT, "sber")
    assert p.side == "buy"
    assert p.ticker == "SBER"
    assert p.lots == 12
    assert p.order_type == "limit"
    assert p.limit_price == 285.5
    assert p.requires_confirmation if hasattr(p, "requires_confirmation") else True
    assert p.to_dict()["requires_confirmation"] is True


def test_extract_proposal_market_when_no_price():
    p = extract_proposal("SELL 3 лота GAZP", "gazp")
    assert p.side == "sell"
    assert p.lots == 3
    assert p.order_type == "market"
    assert p.limit_price is None


def test_extract_proposal_hold_has_zero_lots():
    p = extract_proposal("Вердикт: HOLD, ждём отчётность.", "LKOH")
    assert p.side == "hold"
    assert p.lots == 0
