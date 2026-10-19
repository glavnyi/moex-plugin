"""Market-aware sentiment prompt (regression: MOEX runs got the US prompt).

Live bug: on a MOEX run the sentiment analyst was told "News headlines —
Yahoo Finance" (the news block actually came from MOEX) and was instructed
to read a StockTwits Bullish/Bearish ratio and Reddit engagement, while both
social fetchers always returned 403/empty for MOEX tickers. The model was
pushed to invent retail-sentiment data it never had.

No network: only the prompt assembler and the run config are touched.
"""

from __future__ import annotations

import pytest

from tradingagents.agents.analysts.sentiment_analyst import (
    _active_market,
    _build_system_message,
)

_KW = {
    "ticker": "SBER",
    "start_date": "2026-10-10",
    "end_date": "2026-10-17",
    "news_block": "NEWS",
    "stocktwits_block": "STOCKTWITS",
    "reddit_block": "REDDIT",
}

_BLOCK_TAGS = (
    "<start_of_news>", "<end_of_news>",
    "<start_of_stocktwits>", "<end_of_stocktwits>",
    "<start_of_reddit>", "<end_of_reddit>",
)


def test_us_prompt_keeps_the_upstream_wording():
    msg = _build_system_message(**_KW)          # market defaults to ""

    assert "### News headlines — Yahoo Finance, past 7 days" in msg
    assert "**Read the StockTwits Bullish/Bearish ratio" in msg
    assert "r/wallstreetbets, r/stocks, r/investing (past 7 days)" in msg
    for tag in _BLOCK_TAGS:
        assert tag in msg


def test_moex_prompt_stops_asking_for_us_social_data():
    msg = _build_system_message(**_KW, market="MOEX")

    assert "Yahoo Finance" not in msg
    assert "### News headlines — MOEX (ISS and exchange RSS feeds), past 7 days" in msg
    assert msg.count("not applicable to MOEX-quoted instruments") == 2
    assert "**Read the StockTwits Bullish/Bearish ratio" not in msg
    assert "Do not read, estimate or invent a StockTwits Bullish/Bearish ratio" in msg
    assert "social sources are out of scope for MOEX" in msg
    # the pre-fetched blocks themselves must survive the rewrite
    for tag in _BLOCK_TAGS:
        assert tag in msg
    for payload in ("NEWS", "STOCKTWITS", "REDDIT"):
        assert payload in msg
    # still a report with an ordered output section
    assert "## Output" in msg and "Produce a sentiment report covering, in order:" in msg


def test_market_is_read_from_the_run_config():
    from tradingagents.dataflows.config import get_config, set_config

    original = get_config()
    try:
        cfg = get_config()
        cfg.pop("market", None)
        set_config(cfg)
        assert _active_market() == ""

        cfg = get_config()
        cfg["market"] = "MOEX"
        set_config(cfg)
        assert _active_market() == "MOEX"
    finally:
        set_config(original)


@pytest.mark.parametrize("market", ["MOEX", "moex", "Moex"])
def test_moex_matching_is_case_insensitive(market):
    msg = _build_system_message(**_KW, market=market)
    assert "Yahoo Finance" not in msg
