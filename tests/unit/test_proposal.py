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


def test_rating_vocabulary_maps_to_sides():
    assert normalize_side("Overweight") == "buy"
    assert normalize_side("Underweight") == "sell"
    assert normalize_side("Neutral") == "hold"
    assert normalize_side("Accumulate") == "buy"
    assert normalize_side("Reduce") == "sell"


DEBATE_REPORT = """\
## Analyst debate
Aggressive Analyst: I propose BUY 50 лотов immediately!
Neutral Analyst: HOLD with vigilance, watch 288. Price sitting 4.3% below.
Conservative: The BUY proposal exposes us; HOLD is safer.

### Portfolio Manager Decision
**Rating**: Underweight

Trim the position; entry at 270 would be the only re-entry of interest.
"""


def test_final_section_overrides_debate_verdicts():
    p = extract_proposal(DEBATE_REPORT, "SBER")
    # The debate quotes BUY first, but the PM block says Underweight.
    assert p.side == "sell"
    assert p.limit_price == 270.0
    assert p.order_type == "limit"


OVERWEIGHT_REPORT = """\
## Analyst debate
Aggressive: BUY 50 lots now. Price sitting high.
Neutral: HOLD.

### Portfolio Manager Decision
**Rating**: Overweight

Thesis: defined stop near 275 RUB to the Bollinger upper (~289.6 RUB),
risk-reward ~1.4:1 with moderate 2-3% sizing.
"""


def test_overweight_report_yields_market_buy():
    p = extract_proposal(OVERWEIGHT_REPORT, "SBER")
    assert p.side == "buy"
    # No explicit entry/limit price in the PM section — market order.
    assert p.limit_price is None
    assert p.order_type == "market"
    assert p.lots == 1


STOP_HINT_REPORT = """\
### Portfolio Manager Decision
**Rating**: Underweight

Trim the MTSS position by ~50% at current levels (~172.90 RUB).
Set a stop-loss just below the lower Bollinger band at ~164.05 RUB
to limit downside. Reassess in 3-6 months.
"""


def test_stop_loss_hint_is_extracted():
    p = extract_proposal(STOP_HINT_REPORT, "MTSS")
    assert p.side == "sell"
    assert p.stop_price == 164.05
    d = p.to_dict()
    assert d["stop_price"] == 164.05


def test_stop_near_existing_report_entry():
    # Existing OVERWEIGHT fixture mentions "stop near 275" — parser now
    # picks it up alongside the market-buy proposal.
    p = extract_proposal(OVERWEIGHT_REPORT, "SBER")
    assert p.side == "buy"
    assert p.stop_price == 275.0


def test_absurd_stop_gap_is_discarded():
    bad = """\
### Portfolio Manager Decision
**Rating**: Overweight
Entry at 270. Stop mentioned somewhere: 17.
"""
    p = extract_proposal(bad, "SBER")
    # 17 is 15x away from the 270 entry — parsing artefact, dropped.
    assert p.stop_price is None
