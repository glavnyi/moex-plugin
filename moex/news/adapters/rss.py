"""Generic RSS/Atom adapter (stdlib XML only, no feedparser dependency)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ...http import MoexHttpError, get_bytes
from ..dedupe import item_id
from ..models import NewsItem
from ..registry import RSS_UA
from .base import AdapterError, BaseAdapter

logger = logging.getLogger(__name__)

_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_ATOM = "{http://www.w3.org/2005/Atom}"

_FETCH_TTL = 600.0  # seconds


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt:
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=None)
    except ValueError:
        return None


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return " ".join(el.text.split())


def _strip_html(raw: str, limit: int = 700) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = " ".join(text.split())
    return text[:limit]


def parse_feed(body: bytes | str, source_id: str, source_name: str) -> list[NewsItem]:
    """Parse RSS 2.0 / Atom into normalized items. Raises AdapterError.

    Bytes are passed to ElementTree so the XML declaration picks the
    right encoding (Vedomosti et al. declare utf-8 while some HTTP
    headers claim windows-1251).
    """
    if isinstance(body, str):
        body = body.lstrip("﻿").encode("utf-8")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise AdapterError(f"invalid XML from {source_id}: {exc}") from exc

    tag = root.tag.lower()
    ns = _ATOM if tag.startswith("{") else ""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items: list[NewsItem] = []

    nodes = root.findall(f".//{ns}item") or root.findall(f".//{ns}entry")
    for node in nodes:
        title = _text(node.find(f"{ns}title"))
        if not title:
            continue
        link = ""
        link_el = node.find(f"{ns}link")
        if link_el is not None:
            link = (link_el.get("href") or link_el.text or "").strip()
        if not link:
            guid = node.find(f"{ns}guid")
            if guid is not None and guid.text and guid.text.startswith("http"):
                link = guid.text.strip()
        summary = _text(node.find(f"{ns}description")) or _text(
            node.find(f"{ns}summary")
        )
        content = _text(node.find(f"{_CONTENT}encoded"))
        if content and len(content) > len(summary):
            summary = content
        pub_raw = (
            _text(node.find(f"{ns}pubDate"))
            or _text(node.find(f"{ns}published"))
            or _text(node.find(f"{ns}updated"))
            or _text(node.find(f"{_DC}date"))
        )
        published = _parse_date(pub_raw)
        item = NewsItem(
            id=item_id(source_id, link, title),
            source_id=source_id,
            source_name=source_name,
            title=title,
            summary=_strip_html(summary),
            url=link,
            published_at=published,
            fetched_at=datetime.now(timezone.utc),
            origin=source_name,
        )
        items.append(item)
    return items


class RssAdapter(BaseAdapter):
    def fetch(self, *, since=None, until=None, limit: int = 50) -> list[NewsItem]:
        src = self.source
        try:
            body = get_bytes(
                src.url,
                ttl=_FETCH_TTL,
                accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                user_agent=RSS_UA,
            )
        except MoexHttpError as exc:
            raise AdapterError(f"{src.id}: {exc}") from exc

        try:
            items = parse_feed(body, src.id, src.name)
        except AdapterError:
            # Transient anti-bot/HTML response may have been cached — drop
            # it and refetch once from the network before giving up.
            from ...http import cache_invalidate

            cache_invalidate(src.url)
            body = get_bytes(
                src.url,
                ttl=0.0,
                accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                user_agent=RSS_UA,
            )
            items = parse_feed(body, src.id, src.name)
        out = []
        for item in items:
            if item.published_at:
                if since and item.published_at < since:
                    continue
                if until and item.published_at > until:
                    continue
            out.append(item)
            if len(out) >= limit:
                break
        return out
