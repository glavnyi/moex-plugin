"""Trade proposal extracted from an analyst report (spec §6.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_VERDICT = re.compile(
    r"\b(BUY|SELL|HOLD|ПОКУПКА|ПРОДАЖА|ДЕРЖАТЬ|КУПИТЬ|ПРОДАТЬ"
    r"|Overweight|Underweight|Neutral|Accumulate|Reduce)\b",
    re.IGNORECASE,
)
_LOTS = re.compile(r"(\d+)\s*(?:лот|lots?)", re.IGNORECASE)
_PRICE = re.compile(
    r"(?:лимитн\w*\s*цен\w*|limit\s*price|entry\s*(?:price|level|at)|по цене)"
    r"[^\d]{0,12}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# Stop-loss hint: "stop-loss at ~164.05", "стоп 160", "stop near 275".
# Number may sit several words after the keyword ("stop-loss just below the
# lower Bollinger band at ~164.05") — allow up to 60 non-digit chars but
# never cross a sentence boundary (.) or newline.
_STOP = re.compile(
    r"(?:stop[\s-]?loss|stop\s+(?:at|near|to|of)|стоп[\s-]?\w*)"
    r"[^.\n\d]{0,60}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# The actionable verdict lives in the Portfolio Manager Decision block —
# earlier sections quote each other's BUY/HOLD calls during the debate.
_FINAL_SECTION = re.compile(
    r"(?:Portfolio\s+Manager\s+Decision|Final\s+(?:Trade\s+)?Decision"
    r"|Investment\s+Decision|Итогов\w*\s+решени\w*)",
    re.IGNORECASE,
)

_BUY = {"BUY", "ПОКУПКА", "КУПИТЬ", "OVERWEIGHT", "ACCUMULATE"}
_SELL = {"SELL", "ПРОДАЖА", "ПРОДАТЬ", "UNDERWEIGHT", "REDUCE"}
# Neutral ratings → hold: HOLD, ДЕРЖАТЬ, NEUTRAL (default branch).


@dataclass
class TradeProposal:
    side: str            # buy | sell | hold
    ticker: str
    lots: int = 0
    order_type: str = "limit"
    limit_price: float | None = None
    stop_price: float | None = None
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
            "stop_price": self.stop_price,
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


def _decision_slice(text: str) -> str:
    """Text window that actually contains the final verdict.

    Complete reports are long multi-agent debates where EVERY role's call
    (BUY from the aggressive, HOLD from the neutral…) is quoted verbatim.
    Scanning the whole markdown picks up the first quoted verdict instead
    of the real one (live bug: a real SBER Overweight report extracted as
    HOLD from a mid-debate sentence with a garbage limit price). Prefer
    the last final-decision heading; fall back to the full text for
    short/legacy reports.
    """
    match = None
    for match in _FINAL_SECTION.finditer(text):
        pass
    if match:
        return text[match.start():]
    return text


def extract_proposal(report_text: str, ticker: str, *,
                     default_lots: int = 1) -> TradeProposal:
    """Parse BUY/SELL/HOLD + lots/price hints from the analyst output.

    Ratings vocabulary (Overweight/Underweight/Neutral) maps onto
    buy/sell/hold. Extraction itself decides nothing: the proposal is a
    *candidate* that still has to clear ``moex.risk.check_proposal`` on
    fresh account state, and it is either confirmed by the user in the GUI
    or authorised by the auto-trading gate chain before anything is sent
    (§6.2).
    """
    text = _decision_slice(report_text or "")
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
    stop = None
    m_stop = _STOP.search(text)
    if m_stop:
        try:
            stop = float(m_stop.group(1).replace(",", "."))
        except ValueError:
            stop = None
    # Sanity: a stop far from the entry is a parsing artefact, not a real
    # level (e.g. "stop-loss at 164.05" next to a 172.90 price is plausible,
    # but a 10x gap means the wrong number got matched).
    ref = price
    if ref and stop and not (0.5 * ref <= stop <= 1.5 * ref):
        stop = None
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
        stop_price=stop,
        rationale=rationale,
    )
