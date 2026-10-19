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
    # The window is [start_date, end_date], not a fixed 7 days: the old
    # `(until - end_dt).days + 7` never read start_date, so a caller asking
    # for a month of history silently got one week while the report header
    # claimed the full range.
    start_dt = datetime.strptime(str(start_date), "%Y-%m-%d")
    days = max(1, (until - start_dt).days + 1)
    agg = fetch_all(instrument=instrument, days=days, limit=80, until=until)
    return _cap_report(news_report(
        agg.items, instrument.secid, str(start_date), str(end_date), linked=True
    ), _NEWS_TOOL_MAX_CHARS)


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
    return _cap_report(
        global_news_report(agg.items, str(curr_date), days),
        _GLOBAL_NEWS_TOOL_MAX_CHARS,
    )


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


_NEWS_TOOL_MAX_CHARS = 12_000
_GLOBAL_NEWS_TOOL_MAX_CHARS = 8_000


def _cap_report(report: str, max_chars: int) -> str:
    """Bound a news tool's text at a whole-block boundary, honestly.

    T-Invest items now carry full ``content`` snippets (up to 700 chars
    each), so an 60-80-item report can reach tens of KB — too heavy for a
    free-model context. The corpus stays newest-first, so cutting at the
    last ``### `` keeps complete items and the note says how many were
    dropped instead of silently vanishing.
    """
    if len(report) <= max_chars:
        return report
    kept = report[:max_chars]
    cut = kept.rfind("### ")
    if cut > 0:
        kept = kept[:cut]
    total = report.count("### ")
    shown = kept.count("### ")
    return kept + (
        f"\n… (обрезано по лимиту {max_chars} символов: показано "
        f"{shown} из {total} новостей, пропущено {total - shown}; "
        f"корпус отсортирован от свежих к старым)\n"
    )


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


_SNAPSHOT_MAX_CHARS = 24_000


def snapshot_news_window(start: str, as_of: str, *, live: bool) -> tuple[datetime, int]:
    """``(until, days)`` cutoffs for the snapshot's news block.

    Point-in-time honesty: a historical cell must never read news published
    after its own date. ``news_report`` does not filter dates itself — the
    window has to be cut at fetch time, the same way the ``get_news`` vendor
    does it. ``until`` therefore clamps to the end of ``as_of`` (never past
    now), and ``days`` spans the whole window the report header claims.
    ``live=True`` keeps the legacy last-8-days-up-to-now window for
    snapshots baked without an as_of, so live runs are unchanged.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if live:
        return now, 8
    end_dt = datetime.fromisoformat(as_of) + timedelta(days=1) - timedelta(seconds=1)
    until = min(now, end_dt)
    start_dt = datetime.fromisoformat(start)
    return until, max(1, (until - start_dt).days + 1)


def build_moex_snapshot(ticker: str, board: str = "", as_of: str | None = None) -> str:
    """Real-data snapshot injected into analyst system prompts (MOEX runs).

    Free/edge LLMs often never emit tool calls, which previously made the
    analysts invent plausible-looking but fake prices (verified live: report
    said close 285.76 while ISS showed 286.43). This pre-fetch guarantees
    that live MOEX data is present in the prompt regardless of the model's
    tool-calling support. Best-effort: a failed section is marked
    ``[unavailable]`` instead of aborting the run.
    """
    from datetime import date

    from moex.fundamentals import fundamentals_report
    from moex.instruments import InstrumentNotFound, resolve_instrument
    from moex.market_data import indicator_values, stock_data_report
    from moex.news import fetch_all, news_report

    live = as_of is None
    as_of = as_of or date.today().isoformat()
    end = date.fromisoformat(as_of)
    start = (end - timedelta(days=65)).isoformat()
    brd = str(board or "").upper() or None
    parts: list[str] = []

    def _add(title: str, fn, *args, max_chars: int = 2500, **kwargs) -> None:
        try:
            text = fn(*args, **kwargs)
            if text and str(text).strip():
                parts.append(f"### {title}\n{str(text).strip()[:max_chars]}")
        except Exception as exc:  # noqa: BLE001 — snapshot is best-effort
            parts.append(f"### {title}\n[unavailable: {exc}]")

    _add("Daily price history (OHLCV)", stock_data_report,
         ticker, start, as_of, board=brd, max_chars=5000)
    for ind in ("close_50_sma", "close_200_sma", "rsi", "macd"):
        _add(f"Indicator: {ind}", indicator_values,
             ticker, ind, as_of, 30, board=brd, max_chars=1500)
    _add("Fundamentals", fundamentals_report, ticker, as_of, board=brd,
         max_chars=3000)

    def _news() -> str:
        instrument = resolve_instrument(str(ticker))
        until, days = snapshot_news_window(start, as_of, live=live)
        agg = fetch_all(instrument=instrument, days=days, limit=40, until=until)
        return news_report(agg.items, instrument.secid, start, as_of, linked=True)

    _add("Recent issuer & market news", _news, max_chars=3500)

    header = (
        f"## Pre-fetched real MOEX data ({str(ticker).upper()}"
        f"/{brd or 'TQBR'}, as of {as_of})\n"
        + _market_status_block() + "\n"
        "Every figure below comes from MOEX ISS / official sources. Use these"
        " numbers; NEVER invent prices, dates or indicator values that are not"
        " present here or returned by your tools.\n"
    )
    text = header + "\n\n".join(parts)
    return text[:_SNAPSHOT_MAX_CHARS]


def _market_status_block() -> str:
    """One-line session banner: live ISS status, not a hardcoded clock.

    MOEX trades TQBR in the main session (10:00–18:40 MSK) and, for many
    instruments, the evening session (19:05–23:50 MSK) — so the banner asks
    ISS (TRADINGSTATUS) instead of assuming a schedule.
    """
    try:
        from moex.market_data import get_market_status

        st = get_market_status() or {}
        if st.get("trading"):
            return ("SESSION: MOEX (TQBR) is TRADING right now — today's price"
                    " values below are PROVISIONAL intraday figures and will"
                    " keep changing until the session close.")
        return ("SESSION: MOEX (TQBR) is not trading — figures below are from"
                " the last completed session; today's candle may be absent or"
                " not final yet.")
    except Exception as exc:  # noqa: BLE001 — banner is best-effort
        return f"SESSION: MOEX trading-status unavailable ({exc})."


def is_moex_config(config: dict) -> bool:
    return str((config or {}).get("market") or "").upper() == "MOEX"
