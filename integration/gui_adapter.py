"""Register MOEX vendors into the GUI's vendored TradingAgents copy.

The GUI imports its own ``tradingagents`` package (0.2.5+gui.2) whose
``dataflows.interface`` dispatches every data tool through
``route_to_vendor(method, *args)`` with ``VENDOR_METHODS[method][vendor]``.

This module adds a ``moex`` vendor implementation for every method and —
via :func:`moex_vendor_config` — points the run config at it when the
user selects market MOEX. Other markets keep their original chain:
their configs never contain our vendor keys, so nothing changes (§2,
§4.1 "no accidental cross-market fallback": our impls raise ValueError
if invoked while the active config is not MOEX, and route_to_vendor only
swallows AlphaVantageRateLimitError — the error reaches the analyst).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_REGISTERED = False

MOEX_METHODS = (
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
    "get_insider_transactions",
)

MOEX_CATEGORIES = (
    "core_stock_apis",
    "technical_indicators",
    "fundamental_data",
    "news_data",
)


def _require_moex(method: str) -> tuple[str, str | None]:
    """Return (ticker, board) for the active run; raise if not a MOEX run."""
    from tradingagents.dataflows.config import get_config  # vendored copy

    cfg = get_config() or {}
    market = str(cfg.get("market") or "").upper()
    if market != "MOEX":
        raise ValueError(
            f"MOEX vendor for '{method}' invoked while active market is "
            f"{market or 'unset'} — refusing to mix markets"
        )
    ticker = str(cfg.get("ticker") or cfg.get("symbol") or "").strip().upper()
    board = cfg.get("board")
    return ticker, (str(board) if board else None)


def moex_stock_data(symbol, start_date, end_date) -> str:
    from moex.market_data import stock_data_report

    _, board = _require_moex("get_stock_data")
    return stock_data_report(str(symbol), str(start_date), str(end_date), board=board)


def moex_indicators(symbol, indicator, curr_date, look_back_days=30) -> str:
    from moex.market_data import indicator_values

    _, board = _require_moex("get_indicators")
    return indicator_values(
        str(symbol), str(indicator), str(curr_date), int(look_back_days or 30),
        board=board,
    )


def moex_fundamentals(ticker, curr_date) -> str:
    from moex.fundamentals import fundamentals_report

    _, board = _require_moex("get_fundamentals")
    return fundamentals_report(str(ticker), str(curr_date), board=board)


def moex_balance_sheet(ticker, freq, curr_date) -> str:
    from moex.fundamentals import balance_sheet_report

    _require_moex("get_balance_sheet")
    return balance_sheet_report(str(ticker), str(freq))


def moex_cashflow(ticker, freq, curr_date) -> str:
    from moex.fundamentals import cashflow_report

    _require_moex("get_cashflow")
    return cashflow_report(str(ticker), str(freq))


def moex_income_statement(ticker, freq, curr_date) -> str:
    from moex.fundamentals import income_statement_report

    _require_moex("get_income_statement")
    return income_statement_report(str(ticker), str(freq))


def moex_news(ticker, start_date, end_date) -> str:
    from moex.instruments import InstrumentNotFound, resolve_instrument
    from moex.news import fetch_all, news_report

    _require_moex("get_news")
    end_dt = datetime.strptime(str(end_date), "%Y-%m-%d")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    until = min(now, end_dt + timedelta(days=1) - timedelta(seconds=1))
    try:
        instrument = resolve_instrument(str(ticker))
    except InstrumentNotFound as exc:
        raise ValueError(str(exc)) from exc
    days = max(1, (until - end_dt).days + 7)
    agg = fetch_all(instrument=instrument, days=days, limit=80, until=until)
    return news_report(
        agg.items, instrument.secid, str(start_date), str(end_date), linked=True
    )


def moex_global_news(curr_date, look_back_days=None, limit=None) -> str:
    from moex.news import fetch_all, global_news_report

    _require_moex("get_global_news")
    days = int(look_back_days or 7)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end_dt = datetime.strptime(str(curr_date), "%Y-%m-%d")
    until = min(now, end_dt + timedelta(days=1) - timedelta(seconds=1))
    agg = fetch_all(
        days=days, limit=int(limit or 60), until=until,
        categories={"exchange", "regulator", "media"},
    )
    return global_news_report(agg.items, str(curr_date), days)


def moex_insider_transactions(ticker) -> str:
    _require_moex("get_insider_transactions")
    return (
        f"No insider transaction data for '{ticker}': MOEX free interfaces "
        "provide no insider-report feed (spec §7.2 — documented limitation)."
    )


_IMPLS = {
    "get_stock_data": moex_stock_data,
    "get_indicators": moex_indicators,
    "get_fundamentals": moex_fundamentals,
    "get_balance_sheet": moex_balance_sheet,
    "get_cashflow": moex_cashflow,
    "get_income_statement": moex_income_statement,
    "get_news": moex_news,
    "get_global_news": moex_global_news,
    "get_insider_transactions": moex_insider_transactions,
}


def register_moex_vendors() -> list[str]:
    """Idempotently add the ``moex`` vendor to the GUI's VENDOR_METHODS."""
    global _REGISTERED
    from tradingagents.dataflows import interface as itf  # vendored copy

    added = []
    for method, fn in _IMPLS.items():
        table = itf.VENDOR_METHODS.setdefault(method, {})
        if "moex" not in table:
            table["moex"] = fn
            added.append(method)
    if "moex" not in getattr(itf, "VENDOR_LIST", []):
        itf.VENDOR_LIST.append("moex")
    _REGISTERED = True
    return added


def moex_vendor_config(ticker: str, board: str | None = None,
                       market: str = "MOEX") -> dict:
    """Config fragment merged into a MOEX run (used by run_manager patch)."""
    return {
        "market": market,
        "ticker": str(ticker).upper(),
        "board": str(board).upper() if board else "",
        "tool_vendors": {m: "moex" for m in MOEX_METHODS},
        "data_vendors": {c: "moex" for c in MOEX_CATEGORIES},
    }


def is_moex_config(config: dict) -> bool:
    return str((config or {}).get("market") or "").upper() == "MOEX"
