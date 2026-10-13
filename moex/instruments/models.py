"""Value objects for MOEX instruments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Market(str, Enum):
    """MOEX market (instrument class) identifiers used by this project."""

    SHARES = "shares"
    BONDS = "bonds"
    FUNDS = "funds"
    FUTURES = "forts"
    CURRENCY = "selt"

    @classmethod
    def coerce(cls, value: Any) -> "Market | None":
        if value is None or value == "":
            return None
        text = str(value).strip().lower()
        aliases = {
            "share": cls.SHARES,
            "stock": cls.SHARES,
            "equity": cls.SHARES,
            "акции": cls.SHARES,
            "bond": cls.BONDS,
            "облигации": cls.BONDS,
            "fund": cls.FUNDS,
            "etf": cls.FUNDS,
            "фонды": cls.FUNDS,
            "future": cls.FUTURES,
            "futures": cls.FUTURES,
            "фьючерсы": cls.FUTURES,
            "currency": cls.CURRENCY,
            "currencies": cls.CURRENCY,
            "валюта": cls.CURRENCY,
        }
        if text in {m.value for m in cls}:
            return cls(text)
        return aliases.get(text)


class InstrumentType(str, Enum):
    SHARE = "share"
    PREFERRED_SHARE = "preferred_share"
    BOND = "bond"
    ETF = "etf"
    FUND = "fund"
    FUTURE = "future"
    CURRENCY = "currency"
    INDEX = "index"
    OTHER = "other"


# ISO-4217 fix: MOEX uses SUR for the ruble.
CURRENCY_ALIASES = {"SUR": "RUB", "RUR": "RUB"}


def iso_currency(code: Any) -> str | None:
    if not code:
        return None
    text = str(code).strip().upper()
    return CURRENCY_ALIASES.get(text, text)


@dataclass(frozen=True)
class Instrument:
    """A single MOEX security, resolved to one trading board."""

    secid: str
    board_id: str | None
    engine: str
    market: str
    short_name: str
    full_name: str
    isin: str | None = None
    instrument_type: str = InstrumentType.SHARE.value
    currency: str | None = None
    status: str | None = None
    trading_status: str | None = None
    lot_size: int | None = None
    face_value: float | None = None
    decimals: int | None = None
    list_level: int | None = None
    issue_size: int | None = None
    prev_close: float | None = None
    is_traded: bool = False
    updated_at: str | None = None
    source: str = "moex-iss"
    raw: dict = field(default_factory=dict, compare=False)

    @property
    def display(self) -> str:
        return f"{self.secid} ({self.short_name})"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["instrument_type"] = self.instrument_type
        return data


_TYPE_BY_TYPE_CODE = {
    "common_share": InstrumentType.SHARE.value,
    "preferred_share": InstrumentType.PREFERRED_SHARE.value,
    "gov_bond": InstrumentType.BOND.value,
    "corporate_bond": InstrumentType.BOND.value,
    "exchange_bond": InstrumentType.BOND.value,
    "ofz_tech": InstrumentType.BOND.value,
    "muni_bond": InstrumentType.BOND.value,
    "etf": InstrumentType.ETF.value,
    "fund": InstrumentType.FUND.value,
    "open_fund": InstrumentType.FUND.value,
    "future": InstrumentType.FUTURE.value,
    "currency": InstrumentType.CURRENCY.value,
    "currency_pair": InstrumentType.CURRENCY.value,
    "index": InstrumentType.INDEX.value,
    "stock_index": InstrumentType.INDEX.value,
}

_TYPE_BY_GROUP = {
    "stock_shares": InstrumentType.SHARE.value,
    "stock_bonds": InstrumentType.BOND.value,
    "stock_etf": InstrumentType.ETF.value,
    "stock_fund": InstrumentType.FUND.value,
    "stock_index": InstrumentType.INDEX.value,
    "futures_forts": InstrumentType.FUTURE.value,
    "currency_selt": InstrumentType.CURRENCY.value,
}


def classify_type(type_code: Any, group: Any, market: Any) -> str:
    if type_code and str(type_code) in _TYPE_BY_TYPE_CODE:
        return _TYPE_BY_TYPE_CODE[str(type_code)]
    if group and str(group) in _TYPE_BY_GROUP:
        return _TYPE_BY_GROUP[str(group)]
    market_text = str(market or "").lower()
    if market_text == "shares":
        return InstrumentType.SHARE.value
    if market_text == "bonds":
        return InstrumentType.BOND.value
    if market_text in ("funds",):
        return InstrumentType.FUND.value
    if market_text == "forts":
        return InstrumentType.FUTURE.value
    if market_text == "selt":
        return InstrumentType.CURRENCY.value
    return InstrumentType.OTHER.value
