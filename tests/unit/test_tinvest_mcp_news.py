"""tinvest-news MCP: issuer-scoped calls (instrumentId) — regression.

Live bug: the adapter always fetched the MARKET-WIDE wire, which flooded
the corpus (80/80 slots) and starved ticker linking → every preview said
"No news found" even for SBER. The real tool ``invest_get_news`` accepts
``instrumentId`` (ticker) and must receive it when the instrument is known.
"""

from __future__ import annotations

import json


def _fake_client_factory(captured):
    from tinvest import mcp_adapter as ma

    class FakeClient:
        def __init__(self, url, token, **kw):
            pass

        def list_tools(self):
            return [{
                "name": "invest_get_news",
                "description": "Get news",
                "inputSchema": {"properties": {
                    "instrumentId": {"type": "string"},
                    "limit": {"type": "integer"},
                }},
            }]

        def call_tool(self, name, args):
            captured["name"] = name
            captured["args"] = dict(args)
            payload = json.dumps([{
                "title": "Sberbank bond news",
                "summary": "Issuer update",
                "url": "https://example.invalid/1",
                "publishedAt": "2026-10-14T10:00:00Z",
            }])
            return {"content": [{"type": "text", "text": payload}]}

    return FakeClient


def test_fetch_market_news_passes_instrument_id(monkeypatch):
    from tinvest import mcp_adapter as ma
    from tinvest.mcp_adapter import SourceDef

    captured: dict = {}
    monkeypatch.setattr(ma, "McpClient", _fake_client_factory(captured))
    monkeypatch.setattr(ma, "_token", lambda: "test-token")
    src = SourceDef(id="tinvest-news", name="T-Invest", kind="tinvest-mcp",
                    url="https://example.invalid", category="broker")
    inst = type("I", (), {"secid": "SBER", "isin": "RU0009029540"})()

    items = ma.fetch_market_news(since=None, until=None, limit=10,
                                 source=src, instrument=inst)

    assert captured["args"].get("instrumentId") == "SBER"
    assert captured["args"].get("limit") == 10
    assert items and items[0].title == "Sberbank bond news"


def test_fetch_market_news_without_instrument_stays_market_wide(monkeypatch):
    from tinvest import mcp_adapter as ma
    from tinvest.mcp_adapter import SourceDef

    captured: dict = {}
    monkeypatch.setattr(ma, "McpClient", _fake_client_factory(captured))
    monkeypatch.setattr(ma, "_token", lambda: "test-token")
    src = SourceDef(id="tinvest-news", name="T-Invest", kind="tinvest-mcp",
                    url="https://example.invalid", category="broker")

    ma.fetch_market_news(since=None, until=None, limit=10, source=src)

    assert "instrumentId" not in captured["args"]


def _tinvest_rows_factory(captured, rows):
    from tinvest import mcp_adapter as ma

    class FakeClient:
        def __init__(self, url, token, **kw):
            pass

        def list_tools(self):
            return [{"name": "invest_get_news", "description": "Get news",
                     "inputSchema": {"properties": {
                         "instrumentId": {"type": "string"},
                         "limit": {"type": "integer"}}}}]

        def call_tool(self, name, args):
            captured["args"] = dict(args)
            return {"content": [{"type": "text", "text": json.dumps(
                {"hasNext": False, "items": rows})}]}

    return FakeClient


def test_tinvest_ts_date_is_parsed_and_filters_apply(monkeypatch):
    """Поле ``ts`` (RFC3339, наносекунды) — реальная дата T-Invest новостей.

    Раньше парсер знал только publishedAt/date/time: published_at=None
    ОБХОДИЛ фильтры since/until, и свежая лента утекала в исторические
    ячейки бэктеста (контентная утечка будущего).
    """
    from datetime import datetime
    from tinvest import mcp_adapter as ma
    from tinvest.mcp_adapter import SourceDef

    captured: dict = {}
    rows = [{
        "title": "Market wire item", "ts": "2026-10-19T07:35:55.301228372Z",
        "content": "Полный текст новости из content…",
        "source": "interfax", "id": "1",
    }]
    monkeypatch.setattr(ma, "McpClient", _tinvest_rows_factory(captured, rows))
    monkeypatch.setattr(ma, "_token", lambda: "test-token")
    src = SourceDef(id="tinvest-news", name="T-Invest", kind="tinvest-mcp",
                    url="https://example.invalid", category="broker")

    items = ma.fetch_market_news(since=None, until=None, limit=10, source=src)
    assert items[0].published_at == datetime(2026, 10, 19, 7, 35, 55, 301228)
    assert items[0].summary.startswith("Полный текст новости")

    # until=18.10 — свежая (19.10) новость отсекается, как и в бэктесте.
    keep = ma.fetch_market_news(
        since=None, until=datetime(2026, 10, 18, 23, 59), limit=10, source=src)
    assert keep == []
