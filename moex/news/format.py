"""Render aggregated news for the analyst (upstream-compatible shapes)."""

from __future__ import annotations

from datetime import datetime

from .models import NewsItem


def _block(item: NewsItem) -> str:
    stamp = item.published_iso or "date unknown"
    line = f"### {item.title} (source: {item.source_name}, {stamp})\n"
    if item.tickers:
        line += f"Instruments: {', '.join(item.tickers)}\n"
    if item.summary:
        line += f"{item.summary}\n"
    if item.url:
        line += f"Link: {item.url}\n"
    if item.is_duplicate:
        line += f"Note: duplicate of {item.duplicate_of}\n"
    return line + "\n"


def news_report(
    items: list[NewsItem],
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    linked: bool | None = None,
    include_duplicates: bool = False,
) -> str:
    """Ticker news string shaped like upstream ``get_news_yfinance``.

    ``linked=True``  — items were matched against an instrument: only its
    matches are shown (empty → honest "No news found").
    ``linked=False`` — corpus was fetched without linkage: show everything.
    ``linked=None``  — infer from the data (any item carries tickers).
    """
    pool = items if include_duplicates else [i for i in items if not i.is_duplicate]
    if linked is None:
        linked = any(i.tickers for i in pool)
    if linked:
        matched = [i for i in pool
                   if ticker.upper() in {t.upper() for t in i.tickers}]
    else:
        matched = pool
    if not matched:
        return f"No news found for {ticker} between {start_date} and {end_date}"
    body = "".join(_block(i) for i in matched)
    return f"## {ticker} News, from {start_date} to {end_date}:\n\n{body}"


def global_news_report(
    items: list[NewsItem],
    curr_date: str,
    look_back_days: int,
    *,
    include_duplicates: bool = False,
) -> str:
    """Macro/global news shaped like upstream ``get_global_news_yfinance``."""
    pool = items if include_duplicates else [i for i in items if not i.is_duplicate]
    if not pool:
        return f"No global news found for the last {look_back_days} days up to {curr_date}"
    body = "".join(_block(i) for i in pool)
    start = (datetime.strptime(curr_date, "%Y-%m-%d").toordinal() - int(look_back_days))
    start_date = datetime.fromordinal(start).strftime("%Y-%m-%d")
    return f"## Global News, from {start_date} to {curr_date}:\n\n{body}"


def statuses_report(statuses) -> str:
    """Human-readable source health block (used by GUI + diagnostics)."""
    lines = ["# News source statuses"]
    for st in statuses:
        detail = f" — {st.detail}" if st.detail else ""
        lines.append(f"- {st.source_id}: {st.status} ({st.items} items){detail}")
    return "\n".join(lines)
