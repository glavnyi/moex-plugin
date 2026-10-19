"""Resolve MOEX tickers to T-Invest instrument identifiers (figi/uid).

T-Invest order endpoints reject bare MOEX SECIDs ("SBER"): the broker
expects its own ``figi`` (code 50022 "Instrument not found" for secid
payloads — live-verified). MOEX ISS and T-Invest use independent id
spaces, so every order placement must translate SECID → T-Invest figi
via ``InstrumentsService/FindInstrument``.
"""

from __future__ import annotations

from .client import TInvestClient, TInvestError

_SHARE_KIND = "INSTRUMENT_TYPE_SHARE"


def find_instrument_ref(
    client: TInvestClient,
    ticker: str,
    *,
    isin: str | None = None,
    instrument_kind: str = _SHARE_KIND,
) -> dict:
    """Return ``{"figi", "uid", "ticker", "isin"}`` for a MOEX SECID.

    Preference: exact ticker match, then ISIN match when provided (both
    SBER and its duplicate listing share an ISIN — first exact match
    wins). Raises :class:`TInvestError` when the broker has no such
    instrument.
    """
    secid = str(ticker or "").strip().upper()
    if not secid:
        raise TInvestError("ticker required")
    data = client.call(
        "InstrumentsService",
        "FindInstrument",
        {"query": secid, "instrumentKind": instrument_kind},
    )
    items = list(data.get("instruments") or [])
    # Only figi with apiTradeAvailableFlag=True are accepted by
    # PostSandboxOrder; all TCS*-prefixed duplicates are view-only
    # ("Instrument forbidden for trading by API", 30052 — live-verified;
    # tradeable SBER is BBG004730N88).
    tradeable = [
        i for i in items if i.get("apiTradeAvailableFlag")
    ] or items
    exact = [
        i for i in tradeable
        if str(i.get("ticker") or "").strip().upper() == secid
    ]
    pool = exact or tradeable
    if isin:
        want = str(isin).strip().upper()
        by_isin = [
            i for i in pool
            if str(i.get("isin") or "").strip().upper() == want
        ]
        if by_isin:
            pool = by_isin
    if not pool:
        raise TInvestError(f"T-Invest has no instrument for '{secid}'")
    it = pool[0]
    figi = it.get("figi")
    if not figi:
        raise TInvestError(f"T-Invest returned no figi for '{secid}'")
    return {
        "figi": figi,
        "uid": it.get("uid") or "",
        "ticker": it.get("ticker") or secid,
        "isin": it.get("isin") or "",
    }
