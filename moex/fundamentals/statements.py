"""Statement-style queries for MOEX issuers.

Free machine-readable statements do not exist for MOEX issuers:
MOEX ISS publishes no financial statements and e-disclosure.ru blocks
automated access (403 anti-bot, checked 2026-10-13). These functions
therefore return an explicit "not available" report in the same
string shape upstream returns, instead of fabricating numbers.
"""

from __future__ import annotations

from datetime import datetime

_KINDS = {
    "balance_sheet": "balance sheet",
    "cashflow": "cashflow",
    "income_statement": "income statement",
}


def _report(ticker: str, kind: str, freq: str) -> str:
    label = _KINDS.get(kind, kind)
    header = (
        f"# {label.capitalize()} data for {ticker.upper()} ({freq})\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    body = (
        f"No {label} data found for symbol '{ticker.upper()}': "
        "MOEX free interfaces (ISS) do not provide financial statements, and "
        "e-disclosure.ru disallows automated collection. Obtain filings from "
        "issuer IR pages or e-disclosure.ru manually. "
        "Statement metrics are therefore intentionally absent, not estimated."
    )
    return header + "\n" + body + "\n"


def balance_sheet_report(ticker: str, freq: str = "annual") -> str:
    return _report(ticker, "balance_sheet", freq)


def cashflow_report(ticker: str, freq: str = "annual") -> str:
    return _report(ticker, "cashflow", freq)


def income_statement_report(ticker: str, freq: str = "annual") -> str:
    return _report(ticker, "income_statement", freq)
