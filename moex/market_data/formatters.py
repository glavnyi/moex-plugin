"""Human/LLM-readable renderings of MOEX data (upstream-compatible)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .iss import get_candles, get_quote


def stock_data_report(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    market: str | None = None,
    board: str | None = None,
) -> str:
    """OHLCV CSV report matching the upstream ``get_stock_data`` shape."""
    data = get_candles(symbol, start_date, end_date, interval="1d",
                       market=market, board=board)
    if data.empty:
        raise ValueError(
            f"No MOEX data found for symbol '{symbol}' between {start_date} and {end_date}"
        )

    out = data.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col].astype(float).round(2)
    out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")

    quote = None
    try:
        quote = get_quote(symbol, market=market, board=board)
    except Exception:  # noqa: BLE001 — quote is supplementary, never fatal
        pass

    csv_string = out.to_csv(index=False)
    header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(out)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    if quote:
        header += (
            f"# Source: MOEX ISS, board {quote.get('board_id')}, "
            f"currency {quote.get('currency') or 'SUR'}, "
            f"quote time {quote.get('quote_time') or 'n/a'}\n"
        )
    else:
        header += "# Source: MOEX ISS\n"
    header += "\n"
    return header + csv_string
