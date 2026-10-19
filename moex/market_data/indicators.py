"""Technical indicators for MOEX instruments.

The output format mirrors upstream ``get_stock_stats_indicators_window``
so the Market Analyst sees byte-comparable structure regardless of market.
Indicator maths comes from the same ``stockstats`` library the US path
uses, computed over MOEX OHLCV only.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from stockstats import wrap

from .iss import get_candles

logger = logging.getLogger(__name__)

# Same descriptions upstream feeds the Market Analyst — kept verbatim so
# report text stays identical across markets.
INDICATOR_DOCS: dict[str, str] = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers and divergence as signals of trend changes. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot trend shifts early. "
        "Tips: Can be volatile; complement with additional filters in low-volatility markets."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts and reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Signals potential oversold conditions and breakdown zones. "
        "Tips: Use alongside volume analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes and combine with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}

_CACHE_YEARS = 5
# The cache file is named after *today*, so it is written on the first call
# of the day and then reused until midnight. Written before the open — live
# bug: 08:11 on Saturday 2026-10-17, i.e. before the 09:50 ДСВД session —
# every RSI/MACD/SMA the analyst saw all day lacked the current bar and the
# report claimed "N/A: Not a trading day (weekend or holiday)" for a day the
# exchange was trading and had a candle for. Same order as the autotrade
# tick (300 s), so a cycle reuses the cache but a session never stays stale.
_CACHE_TTL_SECONDS = 600


def _cache_dir() -> Path:
    try:  # prefer the framework cache dir when running inside a graph
        from tradingagents.dataflows.config import get_config  # type: ignore

        base = get_config().get("data_cache_dir")
        if base:
            return Path(base)
    except Exception:  # pragma: no cover - standalone use
        pass
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(home) / "cache"


def _safe(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(symbol))[:64]


def _cache_fresh(path: Path) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < _CACHE_TTL_SECONDS
    except OSError:
        return False


def load_ohlcv(symbol: str, curr_date: str, *, market=None, board=None) -> pd.DataFrame:
    """MOEX OHLCV up to ``curr_date`` (inclusive), cached per symbol.

    Rows after ``curr_date`` are dropped so backtests and historical runs
    never see future prices. A cache older than ``_CACHE_TTL_SECONDS`` is
    refreshed; if the refresh fails, the stale bars are served instead of
    raising — a slightly old close beats no answer at all.
    """
    curr_ts = pd.to_datetime(curr_date)
    today = pd.Timestamp.today().normalize()
    start = curr_ts - pd.DateOffset(years=_CACHE_YEARS)
    if start > today:
        start = today - pd.DateOffset(years=_CACHE_YEARS)
    start_str = start.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_safe(symbol)}-MOEX-data-{start_str}-{end_str}.csv"

    cached = pd.DataFrame()
    if cache_file.exists():
        try:
            cached = pd.read_csv(cache_file, on_bad_lines="skip", encoding="utf-8")
        except (OSError, ValueError):
            cached = pd.DataFrame()

    data = cached if (not cached.empty and _cache_fresh(cache_file)) else pd.DataFrame()

    if data.empty:
        try:
            fetched = get_candles(symbol, start_str, end_str, interval="1d",
                                  market=market, board=board)
        except Exception as exc:
            if cached.empty:
                raise
            logger.warning("candle refresh failed for %s (%s); serving cached bars",
                           symbol, exc)
            fetched = pd.DataFrame()
        if fetched.empty:
            if cached.empty:
                raise ValueError(
                    f"No MOEX candle history for '{symbol}' between {start_str} and {end_str}"
                )
            data = cached
        else:
            data = fetched
            data.to_csv(cache_file, index=False, encoding="utf-8")

    data = data.copy()
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])
    price_cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()
    # Point-in-time: never expose future rows.
    data = data[data["Date"] <= curr_ts]
    return data.sort_values("Date").reset_index(drop=True)


def indicator_values(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    *,
    market=None,
    board=None,
) -> str:
    """Formatted indicator report, upstream-compatible in structure."""
    ind = str(indicator or "").strip().lower()
    if ind not in INDICATOR_DOCS:
        raise ValueError(
            f"Indicator {ind} is not supported. Please choose from: {list(INDICATOR_DOCS)}"
        )

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - timedelta(days=int(look_back_days))

    data = load_ohlcv(symbol, curr_date, market=market, board=board)
    if data.empty:
        raise ValueError(f"No MOEX data for '{symbol}' up to {curr_date}")

    frame = wrap(data.copy())
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.strftime("%Y-%m-%d")
    frame[ind]  # noqa: B018 — trigger stockstats computation

    values: dict[str, str] = {}
    for _, row in frame.iterrows():
        value = row[ind]
        values[str(row["Date"])] = "N/A" if pd.isna(value) else str(value)

    lines: list[str] = []
    cursor = curr_dt
    while cursor >= before:
        key = cursor.strftime("%Y-%m-%d")
        lines.append(f"{key}: {values.get(key, 'N/A: Not a trading day (weekend or holiday)')}")
        cursor -= timedelta(days=1)

    return (
        f"## {ind} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + INDICATOR_DOCS.get(ind, "No description available.")
    )
