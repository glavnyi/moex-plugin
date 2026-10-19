"""T-Invest MCP SSE: живой баг обрезки длинных новостей.

Событийный поток news-инструментов весит > 400 КБ (полные тексты), старый
помеховый разбор рвал поток поspec §13 bound посередине JSON и парсил
пену: обе вариации инструмента падали, ячейка оставалась без T-Invest
новостей (9/31 в прогоне bt_3y от 18.10). Теперь: фреймовый разбор
(только целые SSE-кадры), distinct-ошибка про oversize и повтор с меньшим
limit.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import tinvest.mcp_adapter as ma


_MSG = {"jsonrpc": "2.0", "id": 3, "result": {"content": [], "ok": True}}


def _frame(payload: dict | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"event: message\ndata: {data}\n\n".encode()


# --- _sse_message ---------------------------------------------------------


def test_complete_frame_yields_the_message():
    assert ma._sse_message(_frame(_MSG)) == _MSG


def test_truncated_frame_yields_none():
    cut = _frame(_MSG)[:60]
    assert ma._sse_message(cut) is None


def test_multiline_data_is_joined():
    # Сервер шлёт pretty-printed JSON построчно: каждая строка — отдельная
    # data:-строка, SSE склеивает их через "\n" (спецификация).
    pretty = json.dumps(_MSG, indent=2)
    raw = ("event: message\n"
           + "".join(f"data: {ln}\n" for ln in pretty.splitlines())
           + "\n").encode()
    assert ma._sse_message(raw) == _MSG


def test_crlf_framing_is_understood():
    raw = ("event: message\r\ndata: " + json.dumps(_MSG) + "\r\n\r\n").encode()
    assert ma._sse_message(raw) == _MSG


def test_keepalive_before_the_message_is_skipped():
    raw = b": ping\n\n" + _frame(_MSG)
    assert ma._sse_message(raw) == _MSG


def test_final_frame_without_trailing_blank_line_is_dispatched():
    # Сервер закрыл поток без завершающего \n\n — кадр всё равно валиден.
    raw = b"event: message\ndata: " + json.dumps(_MSG).encode()
    assert ma._sse_message(raw + b"\n\n") == _MSG


# --- _post ----------------------------------------------------------------


class _FakeResp:
    def __init__(self, chunks, *, ctype="text/event-stream", status=200):
        self._chunks = list(chunks)
        self.status_code = status
        self.headers = {"Content-Type": ctype}

    def iter_content(self, _size):
        yield from self._chunks


def _client_with_stream(chunks) -> ma.McpClient:
    client = ma.McpClient("https://example.invalid/mcp", "test-token")
    fake = SimpleNamespace(post=lambda *a, **k: _FakeResp(chunks))
    client._session = fake
    return client


def test_post_parses_a_message_split_across_chunks():
    frame = _frame(_MSG)
    mid = len(frame) // 2
    client = _client_with_stream([frame[:mid], frame[mid:]])
    assert client._post({"method": "tools/list"}) == _MSG


def test_post_raises_distinct_error_past_the_bound():
    stump = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"t":"' + b"x" * ma.MAX_CONTENT
    client = _client_with_stream([stump])
    try:
        client._post({"method": "tools/list"})
    except ma.McpError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("ожидали McpError про превышение границы")


# --- fetch_market_news: адаптивный limit ----------------------------------


def _news_factory(captured, *, oversize_above: int):
    class FakeClient:
        def __init__(self, url, token, **kw):
            pass

        def list_tools(self):
            return [{"name": "invest_get_news", "description": "Get news",
                     "inputSchema": {"properties": {
                         "limit": {"type": "integer"}}}}]

        def call_tool(self, name, args):
            captured.append(int(args["limit"]))
            if args["limit"] > oversize_above:
                raise ma.McpError("MCP SSE response exceeds the 400000-byte bound")
            payload = json.dumps([{
                "title": "Market wire", "url": "https://example.invalid/1",
                "publishedAt": "2026-10-19T09:00:00Z"}])
            return {"content": [{"type": "text", "text": payload}]}

    return FakeClient


def test_oversize_payload_retries_with_fewer_items(monkeypatch):
    captured: list[int] = []
    monkeypatch.setattr(ma, "McpClient", _news_factory(captured, oversize_above=40))
    monkeypatch.setattr(ma, "_token", lambda: "test-token")

    items = ma.fetch_market_news(since=None, until=None, limit=80)

    assert captured == [80, 40]  # halved once, then the call succeeded
    assert items and items[0].title == "Market wire"


def test_oversize_at_the_floor_fails_honestly(monkeypatch):
    captured: list[int] = []
    monkeypatch.setattr(ma, "McpClient", _news_factory(captured, oversize_above=0))
    monkeypatch.setattr(ma, "_token", lambda: "test-token")

    try:
        ma.fetch_market_news(since=None, until=None, limit=80)
    except ma.AdapterError as exc:
        assert "all news tool calls failed" in str(exc)
        # 80 → 40 → 20 → 10 (пол) — и честный отказ, без бесконечного цикла
        assert captured == [80, 40, 20, 10]
    else:
        raise AssertionError("ожидали AdapterError после исчерпания попыток")
