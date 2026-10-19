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


def _sse_message(raw: bytes) -> dict | None:
    """First complete JSON-RPC message among the SSE frames received so far.

    SSE frames end with a blank line, and only *complete* frames are parsed,
    so a chunk boundary (or the spec §13 size bound) landing mid-JSON can
    never yield a truncated message. Multiple ``data:`` lines in one frame
    are joined with a newline per the SSE spec.
    """
    text = raw.replace(b"\r\n", b"\n").decode("utf-8", "ignore")
    frames = text.split("\n\n")
    for frame in frames[:-1]:  # the last element may still be incomplete
        data_lines = [ln[5:].lstrip() for ln in frame.splitlines()
                      if ln.startswith("data:")]
        if not data_lines:
            continue
        try:
            message = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and "id" in message:
            return message
    return None


class McpClient:
    def __init__(self, url: str, token: str, *, sandbox: bool = True,
                 timeout: float = 20.0):
        if not token:
            raise McpError("TINVEST_TOKEN is not set — T-Invest MCP requires a token")
        self.url = url
        self.sandbox = sandbox
        self._session = requests.Session()
        # T-Bank endpoints use the Russian trusted CA chain which is absent
        # from the system store — verify with our combined bundle.
        from .client import cert_bundle
        bundle = cert_bundle()
        if bundle:
            self._session.verify = bundle
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
            message = None
            for chunk in resp.iter_content(8192):
                raw += chunk
                message = _sse_message(raw)
                if message is not None:
                    break
                if len(raw) > MAX_CONTENT:
                    # Spec §13 bound: a message bigger than the cap cannot be
                    # delivered whole — say so distinctly (callers retry with
                    # fewer items) instead of parsing a stump of JSON.
                    raise McpError(
                        f"MCP SSE response exceeds the {MAX_CONTENT}-byte bound"
                    )
            if message is None:
                # The server closed the stream: dispatch the trailing frame.
                message = _sse_message(raw + b"\n\n")
            if message is None:
                body = raw.decode("utf-8", "ignore")
                raise McpError(f"no JSON-RPC message in SSE stream: {body[:200]!r}")
            return message
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
        # T-Invest news rows carry their time in ``ts`` (RFC3339, possibly
        # nanosecond precision); RSS-shaped rows use publishedAt/date/time.
        # An unparsed date would bypass the since/until filters and leak
        # today's wire into historical cells — so ``ts`` must be honoured.
        published_raw = (row.get("publishedAt") or row.get("date")
                         or row.get("time") or row.get("ts") or "")
        published = None
        if isinstance(published_raw, (int, float)) and published_raw:
            try:
                published = datetime.fromtimestamp(
                    float(published_raw) / (1000.0 if published_raw > 1e12 else 1.0),
                    tz=timezone.utc).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError):
                published = None
        elif isinstance(published_raw, str) and published_raw:
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
            summary=str(row.get("summary") or row.get("text")
                        or row.get("content") or "")[:700],
            url=url,
            published_at=published,
            fetched_at=now,
            origin=str(row.get("source") or source.name),
        ))
    return items


def fetch_market_news(*, since: datetime | None, until: datetime | None,
                      limit: int = 30, source: SourceDef | None = None,
                      instrument=None) -> list[NewsItem]:
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
        base: dict = {}
        limit_key = next((k for k in ("limit", "count", "pageSize")
                          if k in schema), None)
        if limit_key is not None:
            base[limit_key] = int(limit)
        # Issuer-scoped call when we know the instrument: the real tool
        # (invest_get_news) accepts ``instrumentId`` = ticker and then
        # returns that issuer's news instead of the market-wide wire,
        # which used to flood the corpus and starve ticker linking.
        inst_keys: list[str] = []
        if instrument is not None:
            secid = str(getattr(instrument, "secid", "") or "").strip()
            isin = str(getattr(instrument, "isin", "") or "").strip()
            for k in schema:
                kl = k.lower()
                if kl in ("instrumentid", "instrument", "ticker", "tickers",
                          "secid", "figi"):
                    base[k] = secid
                    inst_keys.append(k)
                elif kl == "isin" and isin:
                    base[k] = isin
                    inst_keys.append(k)
        if required and any(k not in base for k in required):
            # unknown required params → try as-is anyway, honest failure below
            attempts.append(f"{name}: required params {required} unknown, trying empty")
        # Preferred order: issuer-scoped first, market-wide as fallback.
        variants: list[dict] = []
        if inst_keys:
            variants.append(dict(base))
            variants.append({k: v for k, v in base.items() if k not in inst_keys})
        else:
            variants.append(dict(base))
        for args in variants:
            attempt_limit = int(limit)
            while True:
                call_args = dict(args)
                if limit_key is not None:
                    call_args[limit_key] = attempt_limit
                try:
                    result = client.call_tool(name, call_args)
                except McpError as exc:
                    # A full-text news payload can exceed the transport bound
                    # (live: 80 items > the 400 KB SSE cap). Ask for fewer
                    # items instead of losing the whole source — the §13
                    # download bound stays intact.
                    if ("exceeds" in str(exc) and limit_key is not None
                            and attempt_limit > 10):
                        attempts.append(
                            f"{name}: payload too large at limit={attempt_limit}, "
                            f"retrying with {max(10, attempt_limit // 2)}")
                        attempt_limit = max(10, attempt_limit // 2)
                        continue
                    attempts.append(f"{name}({list(args)}): {exc}")
                    break
                items = _to_items(_tool_text(result), source, limit)
                if since:
                    items = [i for i in items
                             if i.published_at is None or i.published_at >= since]
                if until:
                    items = [i for i in items
                             if i.published_at is None or i.published_at <= until]
                return items[:limit]
    raise AdapterError(
        "tinvest-news: all news tool calls failed — " + " | ".join(attempts[:4])
    )
