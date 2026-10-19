"""MOEX fundamentals: issuer data, corporate actions, derived metrics.

Free MOEX ISS reference data is used as the primary source. Where a
metric is not provided by a free interface (financial statements,
dividend history), the report says so explicitly instead of inventing
values — see docs/CONFIGURATION.md «Известные ограничения».
"""

from __future__ import annotations

from datetime import datetime

from ..http import MoexHttpError, get_json
from ..instruments.catalog import resolve_instrument
from ..market_data.indicators import load_ohlcv
from ..market_data.iss import get_quote

ISS = "https://iss.moex.com/iss"
_TTL_META = 3600.0


def _rows(block: dict | None) -> list[dict]:
    if not block:
        return []
    columns = block.get("columns") or []
    return [dict(zip(columns, row)) for row in block.get("data") or []]


def get_dividends(secid: str, *, limit: int = 20) -> dict:
    """Dividend history.

    MOEX ISS exposes no free dividends method (probed 2026-10: every
    documented variant answers with the plain security card). When a
    T-Invest token is configured we use the official
    ``InstrumentsService/GetDividends`` method; otherwise the caller gets
    an explicit ``available: false`` record.
    """
    try:
        from tinvest.client import TInvestClient  # type: ignore

        client = TInvestClient.from_settings()
        if client.enabled():
            instrument = resolve_instrument(secid)
            resp = client.call(
                "tinkoff.public.invest.api.contract.v1.InstrumentsService",
                "GetDividends",
                {"figi": "", "instrumentId": _instrument_id(instrument), "limit": int(limit)},
            )
            payments = []
            for item in resp.get("dividends") or []:
                payments.append(
                    {
                        "pay_date": (item.get("payDate") or "")[:10],
                        "record_date": (item.get("recordDate") or "")[:10],
                        "declared_date": (item.get("declaredDate") or "")[:10],
                        "amount": _quotation(item.get("yieldValue") or item.get("payout") or {}),
                        "currency": (item.get("yieldCurrency") or ""),
                        "source": "t-invest-api",
                    }
                )
            return {"secid": secid, "available": True, "items": payments,
                    "source": "t-invest-api"}
    except Exception:  # noqa: BLE001 — dividends are optional enrichment
        pass
    return {
        "secid": secid,
        "available": False,
        "items": [],
        "source": "moex-iss",
        "reason": (
            "Free MOEX ISS provides no dividend-history method; "
            "connect a T-Invest API token to load dividends."
        ),
    }


def get_coupons(secid: str, *, limit: int = 40) -> dict:
    """Bond coupon + amortization schedule (free MOEX ISS bondization)."""
    url = f"{ISS}/securities/{secid.upper()}/bondization.json"
    try:
        data = get_json(url, {"iss.meta": "off"}, ttl=_TTL_META)
    except MoexHttpError as exc:
        if exc.status == 404:
            return {"secid": secid, "available": False, "reason": "no bondization data"}
        raise
    coupons = [
        {
            "coupon_date": row.get("coupondate"),
            "record_date": row.get("recorddate"),
            "value": row.get("value"),
            "value_pct": row.get("valueprc"),
            "face_value": row.get("facevalue"),
            "currency": row.get("faceunit"),
        }
        for row in _rows(data.get("coupons"))[: int(limit)]
    ]
    amortizations = [
        {
            "date": row.get("amortdate"),
            "face_value": row.get("facevalue"),
            "initial_face_value": row.get("initialfacevalue"),
            "value": row.get("value"),
            "currency": row.get("faceunit"),
        }
        for row in _rows(data.get("amortizations"))
    ]
    available = bool(coupons or amortizations)
    return {
        "secid": secid,
        "available": available,
        "coupons": coupons,
        "amortizations": amortizations,
        "source": "moex-iss",
        **({} if available else {"reason": "no coupon schedule published"}),
    }


def _instrument_id(instrument) -> str:
    return instrument.isin or instrument.secid


def _quotation(value: dict) -> float | None:
    if not isinstance(value, dict):
        return None
    units = value.get("units")
    nano = value.get("nano")
    if units is None and nano is None:
        return None
    return float(units or 0) + float(nano or 0) / 1e9


def fundamentals_report(ticker: str, curr_date: str, *, market=None, board=None) -> str:
    """Fundamentals report in the upstream ``get_fundamentals`` format."""
    instrument = resolve_instrument(ticker, market=market, board=board)

    lines: list[str] = []
    lines.append(f"Name: {instrument.full_name or instrument.short_name}")
    lines.append(f"Short name: {instrument.short_name}")
    lines.append(f"SECID: {instrument.secid}")
    if instrument.isin:
        lines.append(f"ISIN: {instrument.isin}")
    lines.append(f"Instrument type: {instrument.instrument_type}")
    lines.append(f"Exchange / board: MOEX {instrument.market}/{instrument.board_id}")
    lines.append(f"Currency: {instrument.currency or 'n/a'}")
    lines.append(f"Trading status: {instrument.status or 'n/a'}")
    if instrument.lot_size:
        lines.append(f"Lot size: {instrument.lot_size}")
    if instrument.face_value:
        lines.append(f"Face value: {instrument.face_value}")
    if instrument.issue_size:
        lines.append(f"Issue size: {instrument.issue_size}")

    # Point-in-time honesty: a historical cell must never see today's price.
    # A live run keeps the intraday quote; a past date gets its "last" from
    # the same as-of candles the range statistics use below, so the snapshot
    # header's date and the price basis always agree.
    today = datetime.now().strftime("%Y-%m-%d")
    live = str(curr_date)[:10] >= today
    quote = None
    if live:
        try:
            quote = get_quote(instrument, market=market, board=board)
        except Exception:  # noqa: BLE001
            quote = None

    hist = None
    hist_error = None
    try:
        hist = load_ohlcv(instrument.secid, curr_date, market=market, board=board)
        if hist is not None and hist.empty:
            hist = None
    except Exception as exc:  # noqa: BLE001
        hist_error = exc

    last = (quote or {}).get("last")
    if last is None and hist is not None:
        last = float(hist["Close"].iloc[-1])  # close on or before curr_date
    if last is None and live:
        last = instrument.prev_close
    if instrument.issue_size and last:
        basis = "last price" if quote else "last close on or before curr_date"
        lines.append(
            f"Market cap (computed): {float(instrument.issue_size) * float(last):,.0f} "
            f"{instrument.currency or ''} (issue size x {basis})"
        )
    if quote:
        lines.append(f"Last price: {quote.get('last')}")
        lines.append(f"Previous close: {quote.get('prev_close')}")
        lines.append(f"Quote time: {quote.get('quote_time')} (source: {quote.get('source')})")
    elif hist is not None:
        asof = hist["Date"].iloc[-1]
        asof_str = (asof.strftime("%Y-%m-%d")
                    if hasattr(asof, "strftime") else str(asof)[:10])
        lines.append(f"Last close (as of {asof_str}): {float(hist['Close'].iloc[-1])}")
        if len(hist) > 1:
            lines.append(f"Previous close: {float(hist['Close'].iloc[-2])}")
        lines.append(
            "Quote time: n/a — historical date; prices above are point-in-time "
            "MOEX ISS closes, never today's quote"
        )
    else:
        lines.append(
            "Last price: unavailable (no MOEX candles on or before this date"
            + (f": {hist_error})" if hist_error is not None else ")")
        )

    # Derived range stats (computed from MOEX candles, never from the future).
    if hist is not None:
        window = hist.tail(252)
        lines.append(
            f"52-week high (computed): {float(window['High'].max()):.2f}"
        )
        lines.append(
            f"52-week low (computed): {float(window['Low'].min()):.2f}"
        )
        lines.append(
            f"Average daily volume, 30d (computed): "
            f"{float(window['Volume'].tail(30).mean()):,.0f}"
        )
    elif hist_error is not None:
        lines.append(f"Price statistics: unavailable ({hist_error})")

    if instrument.instrument_type == "bond":
        coupons = get_coupons(instrument.secid, limit=5)
        if coupons.get("available") and coupons.get("coupons"):
            nxt = coupons["coupons"][0]
            lines.append(
                f"Next coupon date: {nxt.get('coupon_date')} "
                f"({nxt.get('value_pct')}% of face, {nxt.get('currency')})"
            )
            lines.append(f"Coupon schedule entries available: {len(coupons['coupons'])}")

    dividends = get_dividends(instrument.secid, limit=5)
    if dividends.get("available"):
        lines.append(f"Dividend payments available: {len(dividends.get('items', []))}")
    else:
        lines.append(
            "Dividend history: not available from free sources (MOEX ISS publishes "
            "no free dividends method; connect T-Invest API for this data)"
        )
    lines.append(
        "Financial statements (balance sheet / cashflow / income): not available "
        "from free machine-readable sources for MOEX issuers; use issuer disclosures "
        "(e-disclosure.ru, company IR sites) manually"
    )

    header = f"# Company Fundamentals for {instrument.secid}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += "# Source: MOEX ISS (reference + market data)\n\n"
    return header + "\n".join(lines)
