"""MOEX ISS instrument catalogue.

All lookups are explicit about board/market and never fall back to a
different market: a failed MOEX lookup raises rather than returning a
foreign-market substitute.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..http import MoexHttpError, get_json
from .models import Instrument, InstrumentType, Market, classify_type, iso_currency

logger = logging.getLogger(__name__)

ISS = "https://iss.moex.com/iss"

# Preferred trading boards per market, best first.
BOARD_PRIORITY: dict[str, tuple[str, ...]] = {
    Market.SHARES.value: ("TQBR", "TQTD", "TQIF", "SMAL", "TQPI"),
    Market.BONDS.value: ("TQOB", "TQCB"),
    Market.FUNDS.value: ("TQTF", "TQIF"),
    Market.FUTURES.value: ("FQBR",),
    Market.CURRENCY.value: ("CETS",),
}

DEFAULT_BOARD = {
    Market.SHARES.value: "TQBR",
    Market.BONDS.value: "TQOB",
    Market.FUNDS.value: "TQTF",
    Market.FUTURES.value: "FQBR",
    Market.CURRENCY.value: "CETS",
}

ENGINE_FOR = {market: "stock" for market in (
    Market.SHARES.value, Market.BONDS.value, Market.FUNDS.value, Market.FUTURES.value
)}
ENGINE_FOR[Market.CURRENCY.value] = "currency"

# Board securities/marketdata
_TTL_META = 3600.0
_TTL_BOARD = 600.0
_TTL_QUOTE = 15.0


class InstrumentNotFound(LookupError):
    """The instrument does not exist on MOEX (or is not on the requested market)."""


def _rows(block: dict | None) -> list[dict]:
    if not block:
        return []
    columns = block.get("columns") or []
    return [dict(zip(columns, row)) for row in block.get("data") or []]


def default_board_for(market: str) -> str:
    return DEFAULT_BOARD.get(str(market).lower(), "TQBR")


def _pick_board(
    boards: list[dict],
    *,
    market: str | None = None,
    board: str | None = None,
) -> dict | None:
    if not boards:
        return None
    if board:
        for entry in boards:
            if str(entry.get("boardid", "")).upper() == board.upper():
                return entry
        return None
    candidates = list(boards)
    if market:
        wanted = str(market).lower()
        filtered = [b for b in candidates if str(b.get("market", "")).lower() == wanted]
        if filtered:
            candidates = filtered
        else:
            return None
    traded = [b for b in candidates if b.get("is_traded") in (1, "1", True)]
    pool = traded or candidates

    def rank(entry: dict) -> tuple:
        market_name = str(entry.get("market", "")).lower()
        priority = BOARD_PRIORITY.get(market_name, ())
        try:
            idx = priority.index(str(entry.get("boardid", "")).upper())
        except ValueError:
            idx = len(priority)
        return (0 if entry in traded else 1, idx)

    return sorted(pool, key=rank)[0]


def _fetch_security_cards(secid: str) -> tuple[dict, list[dict]]:
    url = f"{ISS}/securities/{secid.upper()}.json"
    data = get_json(url, {"iss.meta": "off"}, ttl=_TTL_META)
    description = {row.get("name"): row.get("value") for row in _rows(data.get("description"))}
    boards = _rows(data.get("boards"))
    if not description and not boards:
        # ISS answers 200 with empty blocks for unknown SECIDs.
        raise InstrumentNotFound(f"MOEX has no security '{secid}'")
    return description, boards


def _fetch_board_row(engine: str, market: str, board: str, secid: str) -> dict | None:
    url = f"{ISS}/engines/{engine}/markets/{market}/boards/{board}/securities/{secid.upper()}.json"
    data = get_json(
        url,
        {"iss.meta": "off", "iss.only": "securities,marketdata"},
        ttl=_TTL_QUOTE,
    )
    securities = _rows(data.get("securities"))
    if not securities:
        return None
    row = dict(securities[0])
    marketdata = _rows(data.get("marketdata"))
    if marketdata:
        row["_marketdata"] = marketdata[0]
    return row


def resolve_instrument(
    symbol: str,
    *,
    market: str | None = None,
    board: str | None = None,
) -> Instrument:
    """Resolve ``symbol`` (SECID) to a single MOEX instrument.

    Raises :class:`InstrumentNotFound` when the security is unknown or not
    available on the requested market — the caller must not treat this as
    "try another market/vendor".
    """
    secid = str(symbol or "").strip().upper()
    if not secid:
        raise InstrumentNotFound("empty ticker")
    secid = secid.split(".")[0] if secid.endswith(".MOEX") else secid

    try:
        description, boards = _fetch_security_cards(secid)
    except MoexHttpError as exc:
        if exc.status == 404:
            raise InstrumentNotFound(f"MOEX has no security '{secid}'") from exc
        raise

    chosen = _pick_board(boards, market=market, board=board)
    if chosen is None:
        if market:
            raise InstrumentNotFound(
                f"'{secid}' exists on MOEX but not on market '{market}'"
            )
        chosen = _pick_board(boards)

    if chosen is None:
        # Metadata without any board — still return a card (e.g. delisted).
        return _build_instrument(secid, description, {}, None, None, None, market)

    engine = str(chosen.get("engine") or "stock")
    market_name = str(chosen.get("market") or market or "")
    board_id = str(chosen.get("boardid") or "")
    row = _fetch_board_row(engine, market_name, board_id, secid)
    if row is None:
        raise InstrumentNotFound(
            f"'{secid}' is listed on {market_name}/{board_id} but returned no data"
        )
    return _build_instrument(secid, description, row, engine, market_name, board_id, market)


def _build_instrument(
    secid: str,
    description: dict,
    row: dict,
    engine: str | None,
    market_name: str | None,
    board_id: str | None,
    requested_market: str | None,
) -> Instrument:
    marketdata = row.get("_marketdata") or {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = marketdata.get("SYSTIME") or now
    group = description.get("GROUP") or row.get("MARKETCODE")
    market = (market_name or Market.coerce(requested_market) or Market.SHARES.value)
    if not isinstance(market, str):
        market = market.value
    prev_close = row.get("PREVPRICE") or marketdata.get("PREVPRICE")
    return Instrument(
        secid=str(row.get("SECID") or description.get("SECID") or secid),
        board_id=board_id or row.get("BOARDID"),
        engine=engine or "stock",
        market=str(market),
        short_name=str(row.get("SHORTNAME") or description.get("SHORTNAME") or ""),
        full_name=str(row.get("SECNAME") or description.get("NAME") or ""),
        isin=_first(row.get("ISIN"), description.get("ISIN")),
        instrument_type=classify_type(
            description.get("TYPE") or description.get("GROUP"),
            group,
            market,
        ),
        currency=iso_currency(row.get("CURRENCYID") or description.get("FACEUNIT")),
        status=row.get("STATUS"),
        trading_status=marketdata.get("TRADINGSTATUS"),
        lot_size=_as_int(row.get("LOTSIZE")),
        face_value=_as_float(row.get("FACEVALUE")),
        decimals=_as_int(row.get("DECIMALS")),
        list_level=_as_int(row.get("LISTLEVEL") or description.get("LISTLEVEL")),
        issue_size=_as_int(row.get("ISSUESIZE") or description.get("ISSUESIZE")),
        prev_close=_as_float(prev_close),
        is_traded=bool(int(row.get("STATUS") == "A" if row.get("STATUS") else 0)),
        updated_at=str(updated),
        source="moex-iss",
        raw={"description": description, "board": row},
    )


def search_instruments(query: str, *, limit: int = 20) -> list[dict]:
    """Full-text search across the MOEX securities directory."""
    text = str(query or "").strip()
    if not text:
        return []
    fetch = max(int(limit) * 4, 40)
    data = get_json(
        f"{ISS}/securities.json",
        {
            "iss.meta": "off",
            "q": text,
            "limit": fetch,
            "securities.columns": (
                "secid,shortname,name,isin,type,group,primary_boardid,is_traded,"
                "emitent_title"
            ),
        },
        ttl=_TTL_META,
    )
    results = []
    for row in _rows(data.get("securities")):
        results.append(
            {
                "secid": row.get("secid"),
                "shortname": row.get("shortname"),
                "name": row.get("name"),
                "isin": row.get("isin"),
                "type": row.get("type"),
                "group": row.get("group"),
                "primary_board": row.get("primary_boardid"),
                "is_traded": bool(row.get("is_traded")),
                "emitent": row.get("emitent_title"),
                "source": "moex-iss",
            }
        )
    # Exact ticker/ISIN/name matches first, then traded, then the rest.
    needle = text.casefold()
    results.sort(
        key=lambda r: (
            0 if str(r.get("secid", "")).upper() == text.upper() else 1,
            0 if str(r.get("isin", "") or "").upper() == text.upper() else 1,
            0
            if str(r.get("shortname", "") or "").casefold() == needle
            or str(r.get("name", "") or "").casefold() == needle
            else 1,
            0 if r.get("is_traded") else 1,
        ),
    )
    return results[: int(limit)]


def list_board(
    market: str = Market.SHARES.value,
    *,
    board: str | None = None,
    max_rows: int | None = None,
) -> list[Instrument]:
    """Full listing of one board with current trading status.

    Paginates through the ISS board listing (100..500 rows per page).
    """
    market_name = str(market or Market.SHARES.value).lower()
    board_id = (board or default_board_for(market_name)).upper()
    engine = ENGINE_FOR.get(market_name, "stock")
    url = f"{ISS}/engines/{engine}/markets/{market_name}/boards/{board_id}/securities.json"

    instruments: list[Instrument] = []
    start = 0
    for _page in range(50):  # hard safety cap on pagination
        data = get_json(
            url,
            {
                "iss.meta": "off",
                "iss.only": "securities,marketdata",
                "start": start,
            },
            ttl=_TTL_BOARD,
        )
        security_rows = _rows(data.get("securities"))
        if not security_rows:
            break
        marketdata = {
            row.get("SECID"): row for row in _rows(data.get("marketdata"))
        }
        for row in security_rows:
            secid = row.get("SECID")
            enriched = dict(row)
            if secid in marketdata:
                enriched["_marketdata"] = marketdata[secid]
            instruments.append(
                _build_instrument(
                    str(secid), {}, enriched, engine, market_name, board_id, market_name
                )
            )
            if max_rows and len(instruments) >= max_rows:
                return instruments
        start += len(security_rows)
    return instruments


def _first(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
