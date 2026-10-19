"""MOEX ISS market data: candles, quotes, order book, trading status.

Every function is board-aware and returns source timestamps so callers can
distinguish fresh quotes from end-of-day history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from ..http import MoexHttpError, get_json
from ..instruments.catalog import default_board_for, resolve_instrument
from ..instruments.models import Instrument, Market

ISS = "https://iss.moex.com/iss"

_TTL_QUOTE = 15.0
_TTL_HISTORY = 6 * 3600.0

# MOEX candle intervals (ISS `interval` codes).
INTERVALS = {
    "1m": 1, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60,
    "120m": 120, "240m": 240, "1d": 24, "W": 7, "M": 31,
}


def _rows(block: dict | None) -> list[dict]:
    if not block:
        return []
    columns = block.get("columns") or []
    return [dict(zip(columns, row)) for row in block.get("data") or []]


def _instrument(target: str | Instrument, *, market: str | None, board: str | None):
    if isinstance(target, Instrument):
        return target
    return resolve_instrument(target, market=market, board=board)


def _board_paths(instrument: Instrument) -> tuple[str, str, str]:
    engine = instrument.engine or "stock"
    market = instrument.market or Market.SHARES.value
    board = instrument.board_id or default_board_for(market)
    return engine, market, board


def get_candles(
    target: str | Instrument,
    start_date: str,
    end_date: str,
    *,
    interval: str | int = "1d",
    market: str | None = None,
    board: str | None = None,
) -> pd.DataFrame:
    """Historical candles as a DataFrame with a day-normalized ``Date``.

    ``start_date`` / ``end_date`` are inclusive YYYY-MM-DD strings. Only
    data published on or before ``end_date`` is returned (point-in-time
    correct: MOEX candles are finalized end-of-day and the request window
    is bounded). Daily intervals are the supported case: ``Date`` is
    normalized to the calendar day, so intraday intervals would collapse
    to one row per day — nobody in this codebase requests them.
    """
    instrument = _instrument(target, market=market, board=board)
    engine, market_name, board_id = _board_paths(instrument)
    interval_code = INTERVALS.get(str(interval), interval)
    url = (
        f"{ISS}/engines/{engine}/markets/{market_name}/boards/{board_id}"
        f"/securities/{instrument.secid}/candles.json"
    )

    frames: list[dict] = []
    start = 0
    for _page in range(60):
        data = get_json(
            url,
            {
                "iss.meta": "off",
                "interval": interval_code,
                "from": start_date,
                "till": end_date,
                "start": start,
            },
            ttl=_TTL_HISTORY,
        )
        rows = _rows(data.get("candles"))
        if not rows:
            break
        frames.extend(rows)
        if len(rows) < 100:
            break
        start += len(rows)

    if not frames:
        return pd.DataFrame(
            columns=["Date", "Open", "High", "Low", "Close", "Volume"]
        )

    df = pd.DataFrame(frames)
    df["Date"] = pd.to_datetime(df["begin"]).dt.normalize()
    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "value": "Value",
        }
    )
    keep = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume", "Value") if c in df.columns]
    df = df[keep]
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close"]).drop_duplicates(subset=["Date"], keep="last")
    df = df.sort_values("Date").reset_index(drop=True)
    # Never return data after the requested window end.
    df = df[df["Date"] <= pd.Timestamp(end_date)]
    return df


def get_quote(
    target: str | Instrument,
    *,
    market: str | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Current market data snapshot with its source timestamp."""
    instrument = _instrument(target, market=market, board=board)
    engine, market_name, board_id = _board_paths(instrument)
    url = (
        f"{ISS}/engines/{engine}/markets/{market_name}/boards/{board_id}"
        f"/securities/{instrument.secid}.json"
    )
    data = get_json(
        url,
        {"iss.meta": "off", "iss.only": "securities,marketdata"},
        ttl=_TTL_QUOTE,
    )
    security = (_rows(data.get("securities")) or [{}])[0]
    marketdata = (_rows(data.get("marketdata")) or [{}])[0]
    return {
        "secid": instrument.secid,
        "board_id": board_id,
        "short_name": security.get("SHORTNAME") or instrument.short_name,
        "last": marketdata.get("LAST"),
        "prev_close": security.get("PREVPRICE"),
        "open": marketdata.get("OPEN"),
        "high": marketdata.get("HIGH"),
        "low": marketdata.get("LOW"),
        "bid": marketdata.get("BID"),
        "offer": marketdata.get("OFFER"),
        "volume_today": marketdata.get("VOLTODAY"),
        "value_today": marketdata.get("VALTODAY"),
        "num_trades": marketdata.get("NUMTRADES"),
        "trading_status": marketdata.get("TRADINGSTATUS"),
        "security_status": security.get("STATUS"),
        "currency": security.get("CURRENCYID"),
        "updated_at": str(marketdata.get("SYSTIME") or ""),
        "quote_time": str(marketdata.get("UPDATETIME") or ""),
        "source": "moex-iss",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def get_orderbook(
    target: str | Instrument,
    *,
    depth: int = 20,
    market: str | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Order book (best bid/offer levels) as published by MOEX ISS."""
    instrument = _instrument(target, market=market, board=board)
    engine, market_name, board_id = _board_paths(instrument)
    url = (
        f"{ISS}/engines/{engine}/markets/{market_name}/boards/{board_id}"
        f"/securities/{instrument.secid}/orderbook.json"
    )
    data = get_json(url, {"iss.meta": "off", "depth": int(depth)}, ttl=_TTL_QUOTE)
    rows = _rows(data.get("orderbook"))
    bids, offers = [], []
    for row in rows:
        side = str(row.get("BOARDID") or "")
        entry = {"price": row.get("PRICE"), "quantity": row.get("QUANTITY")}
        # ISS orderbook rows carry BUYSELL='B'/'S'.
        if str(row.get("BUYSELL", "")).upper() == "B":
            bids.append(entry)
        elif str(row.get("BUYSELL", "")).upper() == "S":
            offers.append(entry)
    return {
        "secid": instrument.secid,
        "board_id": board_id,
        "bids": bids,
        "offers": offers,
        "updated_at": str((_rows(data.get("orderbook")) or [{}])[0].get("SYSTIME") or ""),
        "source": "moex-iss",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def get_trading_status(
    target: str | Instrument,
    *,
    market: str | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Trading status + session schedule for one instrument/board."""
    instrument = _instrument(target, market=market, board=board)
    engine, market_name, board_id = _board_paths(instrument)
    url = (
        f"{ISS}/engines/{engine}/markets/{market_name}/boards/{board_id}"
        f"/securities/{instrument.secid}.json"
    )
    data = get_json(
        url, {"iss.meta": "off", "iss.only": "marketdata,securities"}, ttl=_TTL_QUOTE
    )
    marketdata = (_rows(data.get("marketdata")) or [{}])[0]
    security = (_rows(data.get("securities")) or [{}])[0]
    return {
        "secid": instrument.secid,
        "board_id": board_id,
        "trading_status": marketdata.get("TRADINGSTATUS"),
        "security_status": security.get("STATUS"),
        "settle_date": security.get("SETTLEDATE"),
        "updated_at": str(marketdata.get("SYSTIME") or ""),
        "source": "moex-iss",
    }


def get_market_status() -> dict[str, Any]:
    """Whether the MOEX share market is currently trading (board TQBR)."""
    url = f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities.json"
    data = get_json(
        url, {"iss.meta": "off", "iss.only": "marketdata", "start": 0}, ttl=_TTL_QUOTE
    )
    rows = _rows(data.get("marketdata"))
    statuses = {str(r.get("TRADINGSTATUS")) for r in rows if r.get("TRADINGSTATUS")}
    return {
        "market": "shares",
        "board_id": "TQBR",
        "trading": "T" in statuses,
        "statuses": sorted(statuses),
        "source": "moex-iss",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
