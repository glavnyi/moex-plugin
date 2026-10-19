"""Score MOEX outcomes against a MOEX index instead of Yahoo's SPY.

Upstream settlement resolves the benchmark from ``benchmark_map``, so a
suffix-less ticker falls through to ``""`` -> SPY, and fetches closes through
yfinance. yfinance serves no bars for Russian symbols: ``SBER``,
``SBER.MICEX``, ``IMOEX.MICEX``, ``MOEX.MICEX`` and ``IMOEX`` all return zero
rows while ``SPY`` returns data. ``_fetch_returns`` therefore yields
``(None, None, None)``, every MOEX memory entry stays ``pending`` for good,
and ``get_past_context`` — which drops pending entries — hands the Portfolio
Manager an empty lessons line.

This module wraps the two methods that settlement runs through and delegates
to the original whenever the run is not a MOEX run, the same refusal the MOEX
vendors use, so non-MOEX configs keep their original behaviour.

Both series come from MOEX ISS in roubles on the same trading calendar, so
there is no FX leg: the index is valued at its last close on or before each
of the security's own sessions, and a window that has not traded yet leaves
the entry pending exactly as upstream does.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)

_INSTALL_FLAGS: dict[str, bool] = {}
_ORIGINALS: dict[str, Callable] = {}

# Fallback when moex.settings carries no evaluation section (fresh install).
DEFAULT_BENCHMARK = "IMOEX"

# The index board first (IMOEX lives there); a non-index benchmark falls back
# to whatever board ISS resolves for the symbol.
_BENCHMARK_BOARDS = ("SNDX", None)


def is_moex(config: dict | None) -> bool:
    """True for a MOEX run — the guard the vendors and the settlement use."""
    return str((config or {}).get("market") or "").upper() == "MOEX"


def benchmark_for(config: dict | None = None) -> str:
    """Index alpha is measured against for a MOEX run.

    ``config["moex_benchmark"]`` wins when set; otherwise the value comes from
    ``moex.settings.evaluation.benchmark`` (IMOEX by default). Both hooks route
    through here, so the reflection label and the return series agree.
    """
    explicit = str((config or {}).get("moex_benchmark") or "").strip()
    if explicit:
        return explicit
    try:
        from moex.config import load_settings

        name = str((load_settings().get("evaluation") or {}).get("benchmark") or "")
    except Exception as exc:  # noqa: BLE001 — unreadable settings must not stop a run
        logger.warning("Benchmark setting unreadable (%s) — falling back to %s",
                       exc, DEFAULT_BENCHMARK)
        name = ""
    return name.strip() or DEFAULT_BENCHMARK


def _closes(frame) -> pd.Series | None:
    """``Close`` series indexed by calendar day; empty input -> None.

    ISS stamps a daily bar at midnight of its own trading day, so two series
    meet on their dates, not their instants. A missing or non-positive close
    is no price at all, not a bar to score on.
    """
    if frame is None or len(frame) == 0:
        return None
    out = frame[["Date", "Close"]].copy()
    out["Date"] = out["Date"].astype("datetime64[ns]").dt.normalize()
    out["Close"] = out["Close"].astype(float)
    out = out[out["Close"] > 0]
    if out.empty:
        return None
    series = out.set_index("Date")["Close"]
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series if len(series) else None


def _benchmark_closes(symbol: str, start: str, end: str) -> pd.Series | None:
    from moex.market_data import get_candles

    for board in _BENCHMARK_BOARDS:
        try:
            series = _closes(get_candles(symbol, start, end, interval="1d", board=board))
        except Exception:  # noqa: BLE001 — try the next way to name the board
            series = None
        if series is not None:
            return series
    return None


def fetch_returns(
    config: dict | None,
    ticker: str,
    trade_date: str,
    holding_days: int = 5,
    benchmark: str = "",
) -> tuple[float | None, float | None, int | None]:
    """Raw and alpha return for ``ticker`` over ``holding_days`` trading days.

    Returns ``(raw_return, alpha_return, holding_days)``, or
    ``(None, None, None)`` while the window has not traded or one side has no
    prices — the entry stays pending and is retried on the next run, matching
    upstream's refusal to score a partial window.
    """
    from moex.market_data import get_candles

    name = str(benchmark or "").strip() or benchmark_for(config)
    board = (config or {}).get("board") or None
    try:
        start = datetime.strptime(str(trade_date), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None, None, None
    # holding_days counts trading days, so ask for the calendar span they
    # occupy (about 7 for every 5) plus a week for holidays.
    end = (start + timedelta(days=round(holding_days * 7 / 5) + 7)).strftime("%Y-%m-%d")
    bench_start = (start - timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        stock = _closes(get_candles(str(ticker).upper(), str(trade_date), end,
                                    interval="1d", board=board))
        bench = _benchmark_closes(name, bench_start, end)
    except Exception as exc:  # noqa: BLE001 — an unreachable ISS leaves the entry pending
        logger.warning("MOEX prices for %s on %s unavailable: %s", ticker, trade_date, exc)
        return None, None, None
    if stock is None or bench is None or len(stock) <= holding_days:
        return None, None, None

    entry, exit_ = stock.index[0], stock.index[holding_days]
    # The window is the security's own sessions; the index is valued at its
    # last close by each end, so both returns span the same dates even when
    # their calendars differ. It must have traded through the exit.
    if bench.index[0] > entry or bench.index[-1] < exit_:
        return None, None, None

    bench_entry, bench_exit = bench.asof(entry), bench.asof(exit_)
    if pd.isna(bench_entry) or pd.isna(bench_exit) or bench_entry == 0:
        return None, None, None
    raw = float((stock.iloc[holding_days] - stock.iloc[0]) / stock.iloc[0])
    bench_ret = float((bench_exit - bench_entry) / bench_entry)
    return raw, raw - bench_ret, holding_days


def install() -> dict[str, Any]:
    """Wrap settlement on the importable copy of tradingagents (idempotent).

    Returns which hooks were attached, so a caller can report a copy the
    project does not ship instead of silently leaving settlement unwired.
    """
    return {"gui": _install_gui()}


def _install_gui() -> bool:
    """Wrap ``TradingAgentsGraph._resolve_benchmark`` / ``._fetch_returns``."""
    if _INSTALL_FLAGS.get("gui"):
        return True
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
    except ImportError as exc:  # GUI copy may be absent (CLI-only install)
        logger.info("MOEX settlement not installed: %s", exc)
        return False

    original_benchmark = getattr(TradingAgentsGraph, "_resolve_benchmark", None)
    original_returns = getattr(TradingAgentsGraph, "_fetch_returns", None)
    if original_benchmark is None or original_returns is None:
        logger.info("MOEX settlement not installed: this tradingagents copy has "
                    "no settlement hooks to wrap")
        return False

    def _resolve_benchmark(self, ticker: str) -> str:
        if is_moex(self.config):
            return benchmark_for(self.config)
        return original_benchmark(self, ticker)

    def _fetch_returns(self, ticker: str, trade_date: str, holding_days: int = 5,
                       benchmark: str = "SPY"):
        if is_moex(self.config):
            # _resolve_benchmark already returned this run's index; ignore the
            # upstream default so a MOEX run can never score against SPY.
            return fetch_returns(self.config, ticker, trade_date, holding_days)
        return original_returns(self, ticker, trade_date, holding_days, benchmark)

    _ORIGINALS["gui_benchmark"] = original_benchmark
    _ORIGINALS["gui_returns"] = original_returns
    TradingAgentsGraph._resolve_benchmark = _resolve_benchmark
    TradingAgentsGraph._fetch_returns = _fetch_returns
    _INSTALL_FLAGS["gui"] = True
    return True


def installed() -> bool:
    """Whether the settlement hooks are in place."""
    return bool(_INSTALL_FLAGS.get("gui"))


def uninstall() -> None:
    """Detach the wrappers — tests only; the original methods come back."""
    if not _INSTALL_FLAGS.pop("gui", None):
        return
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    TradingAgentsGraph._resolve_benchmark = _ORIGINALS.pop("gui_benchmark")
    TradingAgentsGraph._fetch_returns = _ORIGINALS.pop("gui_returns")


__all__ = [
    "DEFAULT_BENCHMARK",
    "benchmark_for",
    "fetch_returns",
    "install",
    "installed",
    "is_moex",
    "uninstall",
]
