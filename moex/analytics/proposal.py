"""Trade proposal extracted from an analyst report (spec §6.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_VERDICT = re.compile(
    r"\b(BUY|SELL|HOLD|ПОКУПКА|ПРОДАЖА|ДЕРЖАТЬ|КУПИТЬ|ПРОДАТЬ)\b",
    re.IGNORECASE,
)
_LOTS = re.compile(r"(\d+)\s*(?:лот|lots?)", re.IGNORECASE)
_PRICE = re.compile(
    r"(?:лимитн\w*\s*цен\w*|price|по цене)[^\d]{0,12}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_BUY = {"BUY", "ПОКУПКА", "КУПИТЬ"}
_SELL = {"SELL", "ПРОДАЖА", "ПРОДАТЬ"}


@dataclass
class TradeProposal:
    side: str            # buy | sell | hold
    ticker: str
    lots: int = 0
    order_type: str = "limit"
    limit_price: float | None = None
    rationale: str = ""
    report_ref: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )

    @property
    def estimated_amount(self) -> float | None:
        if self.lots and self.limit_price:
            return round(self.lots * self.limit_price, 2)
        return None

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "ticker": self.ticker,
            "lots": self.lots,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "estimated_amount": self.estimated_amount,
            "rationale": self.rationale,
            "report_ref": self.report_ref,
            "created_at": self.created_at,
            "requires_confirmation": True,
        }


def normalize_side(raw: str) -> str:
    token = (raw or "").upper()
    if token in _BUY:
        return "buy"
    if token in _SELL:
        return "sell"
    return "hold"


def extract_proposal(report_text: str, ticker: str, *,
                     default_lots: int = 1) -> TradeProposal:
    """Parse BUY/SELL/HOLD + lots/price hints from the analyst output.

    The verdict is never executed automatically — the proposal only
    feeds the risk check and the user confirmation dialog (§6.2).
    """
    text = report_text or ""
    verdict = "HOLD"
    for m in _VERDICT.finditer(text):
        verdict = m.group(1)
        break
    side = normalize_side(verdict)
    lots = default_lots
    m_lots = _LOTS.search(text)
    if m_lots:
        lots = max(1, int(m_lots.group(1)))
    price = None
    m_price = _PRICE.search(text)
    if m_price:
        try:
            price = float(m_price.group(1).replace(",", "."))
        except ValueError:
            price = None
    # Grab the last verdict sentence as rationale context.
    rationale = ""
    idx = text.upper().find(verdict.upper())
    if idx >= 0:
        start = max(0, text.rfind("\n", 0, idx) + 1)
        end = text.find("\n", idx)
        rationale = text[start:end if end > 0 else len(text)][:500].strip()

    return TradeProposal(
        side=side,
        ticker=ticker.upper(),
        lots=lots if side != "hold" else 0,
        order_type="limit" if price else "market",
        limit_price=price,
        rationale=rationale,
    )
