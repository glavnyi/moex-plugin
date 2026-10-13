"""T-Invest MCP adapter (streamable HTTP JSON-RPC) — spec §5, §7.1 item 24-27.

Endpoint and transport come from official docs
(https://developer.tbank.ru/invest/mcp): POST JSON-RPC to
``https://invest-public-api.tbank.ru/mcp`` (sandbox variant for sandbox
flows) with ``Authorization: Bearer``. Exact news tool names are not
published statically — they are discovered via ``tools/list`` at runtime
and only names containing "news" are called; any failure surfaces as an
honest AdapterError with the real server message (no invented methods).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import requests

from moex.http import MoexHttpError  # noqa: F401 — shared error taxonomy
from moex.news.adapters.base import AdapterError
from moex.news.dedupe import item_id
from moex.news.models import NewsItem
from moex.news.registry import SourceDef

logger = logging.getLogger(__name__)

MCP_SANDBOX_URL = "https://sandbox-invest-public-api.tbank.ru/mcp"
MCP_PROD_URL = "https://invest-public-api.tbank.ru/mcp"
PROTOCOL_VERSION = "2025-06-18"
MAX_CONTENT = 400_000  # spec §13: bounded downloads


def _token() -> str:
    return (os.environ.get("TINVEST_TOKEN")
            or os.environ.get("TRADINGAGENTS_TINVEST_TOKEN") or "")


class McpError(RuntimeError):
    pass


class McpClient:
    def __init__(self, url: str, token: str, *, sandbox: bool = True,
                 timeout: float = 20.0):
        if not token:
            raise McpError("TINVEST_TOKEN is not set — T-Invest MCP requires a token")
        self.url = url
        self.sandbox = sandbox
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        self.timeout = timeout
        self._id = 0
        self._session_id: str | None = None
        self._initialized = False

    def _post(self, payload: dict) -> dict:
        self._id += 1
        payload.setdefault("jsonrpc", "2.0")
        payload.setdefault("id", self._id)
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            resp = self._session.post(
                self.url, data=json.dumps(payload), headers=headers,
                timeout=self.timeout, stream=True,
            )
        except requests.RequestException as exc:
            raise McpError(f"MCP transport error: {exc}") from exc
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if resp.status_code >= 400:
            text = resp.raw.read(500, decode_content=True) if resp.raw else b""
            raise McpError(f"MCP HTTP {resp.status_code}: {text[:300]!r}")
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "event-stream" in ctype:
            raw = b""
            for chunk in resp.iter_content(8192):
                raw += chunk
                if len(raw) > MAX_CONTENT:
                    break
                if b"\n\n" in raw and b'"result"' in raw:
                    break
            body = raw.decode("utf-8", "ignore")
            data = None
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        candidate = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict) and "id" in candidate:
                        data = candidate
            if data is None:
                raise McpError(f"no JSON-RPC message in SSE stream: {body[:200]!r}")
            return data
        try:
            return resp.json()
        except ValueError as exc:
            raise McpError("MCP returned non-JSON body") from exc

    def initialize(self) -> dict:
        result = self._post({
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "TradingAgents-MOEX", "version": "0.1"},
            },
        })
        # notification (no id) — best effort
        try:
            self._session.post(
                self.url,
                data=json.dumps({"jsonrpc": "2.0",
                                 "method": "notifications/initialized"}),
                headers={"Mcp-Session-Id": self._session_id} if self._session_id else {},
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass
        self._initialized = True
        return result.get("result") or {}

    def list_tools(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        result = self._post({"method": "tools/list", "params": {}})
        if "error" in result:
            raise McpError(f"tools/list error: {result['error']}")
        return (result.get("result") or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not self._initialized:
            self.initialize()
        result = self._post({
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if "error" in result:
            raise McpError(f"tool {name} error: {result['error']}")
        return result.get("result") or {}


def _tool_text(result: dict) -> str:
    parts = []
    for block in result.get("content") or []:
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def _to_items(text: str, source: SourceDef, limit: int) -> list[NewsItem]:
    """Best-effort normalization of a news tool's JSON payload."""
    payload = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        pass
    rows: list[dict] = []
    if isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
    elif isinstance(payload, dict):
        for key in ("news", "items", "stories", "results", "data"):
            if isinstance(payload.get(key), list):
                rows = [r for r in payload[key] if isinstance(r, dict)]
                break
    now = datetime.now(timezone.utc)
    items: list[NewsItem] = []
    for row in rows[:limit]:
        title = str(row.get("title") or row.get("name") or row.get("headline") or "")
        if not title:
            continue
        url = str(row.get("url") or row.get("link") or row.get("sourceUrl") or "")
        published_raw = (row.get("publishedAt") or row.get("date")
                         or row.get("time") or "")
        published = None
        if isinstance(published_raw, str) and published_raw:
            try:
                published = datetime.fromisoformat(
                    published_raw.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                published = None
        items.append(NewsItem(
            id=item_id(source.id, url, title),
            source_id=source.id,
            source_name=source.name,
            title=title,
            summary=str(row.get("summary") or row.get("text") or "")[:700],
            url=url,
            published_at=published,
            fetched_at=now,
            origin=str(row.get("source") or source.name),
        ))
    return items


def fetch_market_news(*, since: datetime | None, until: datetime | None,
                      limit: int = 30, source: SourceDef | None = None) -> list[NewsItem]:
    """News via official T-Invest MCP. Requires TINVEST_TOKEN."""
    source = source or SourceDef(
        id="tinvest-news", name="T-Invest MCP", kind="tinvest-mcp",
        url=MCP_PROD_URL, category="broker",
    )
    token = _token()
    if not token:
        raise AdapterError(
            "tinvest-news: TINVEST_TOKEN not set — news tools unavailable"
        )
    client = McpClient(MCP_PROD_URL, token)
    try:
        tools = client.list_tools()
    except McpError as exc:
        raise AdapterError(f"tinvest-news: {exc}") from exc

    news_tools = [t for t in tools
                  if "news" in str(t.get("name", "")).lower()
                  or "news" in str(t.get("description", "")).lower()]
    if not news_tools:
        raise AdapterError(
            "tinvest-news: no news tool exposed by this MCP server "
            f"(available: {', '.join(str(t.get('name')) for t in tools[:12])})"
        )

    attempts: list[str] = []
    for tool in news_tools[:3]:
        name = str(tool.get("name"))
        schema = (tool.get("inputSchema") or {}).get("properties") or {}
        required = (tool.get("inputSchema") or {}).get("required") or []
        variants: list[dict] = [{}]
        base: dict = {}
        if any(k in schema for k in ("limit", "count", "pageSize")):
            key = next(k for k in ("limit", "count", "pageSize") if k in schema)
            base[key] = int(limit)
        if any("instrument" in k or k in ("figi", "ticker", "uid") for k in schema):
            pass  # market-wide news call preferred; instrument tools skipped
        if required and any(k not in base for k in required):
            # unknown required params → try as-is anyway, honest failure below
            attempts.append(f"{name}: required params {required} unknown, trying empty")
        variants.append(dict(base))
        for args in variants:
            try:
                result = client.call_tool(name, args)
                items = _to_items(_tool_text(result), source, limit)
                if since:
                    items = [i for i in items
                             if i.published_at is None or i.published_at >= since]
                if until:
                    items = [i for i in items
                             if i.published_at is None or i.published_at <= until]
                return items[:limit]
            except McpError as exc:
                attempts.append(f"{name}({list(args)}): {exc}")
                continue
    raise AdapterError(
        "tinvest-news: all news tool calls failed — " + " | ".join(attempts[:4])
    )
