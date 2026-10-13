"""Aggregate news from every registered source with per-source statuses (§7).

Guarantees (spec §7.3-7.5):
* one broken source never blocks the others — each fetch is isolated;
* point-in-time: items published after ``until`` are dropped;
* deduplication across sources with duplicate flags;
* ticker linking via the MOEX instrument catalogue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import load_settings
from .adapters import AdapterError, RssAdapter
from .dedupe import dedupe, unique
from .linking import link_items
from .models import NewsItem, SourceStatus
from .registry import SOURCE_REGISTRY, SourceDef

logger = logging.getLogger(__name__)


@dataclass
class AggregatedNews:
    items: list[NewsItem] = field(default_factory=list)
    statuses: list[SourceStatus] = field(default_factory=list)

    @property
    def unique_items(self) -> list[NewsItem]:
        return unique(self.items)


def _enabled_ids() -> set[str]:
    settings = load_settings()
    news_cfg = settings.get("news") or {}
    sources_cfg = news_cfg.get("sources") or {}
    enabled: set[str] = set()
    for src in SOURCE_REGISTRY:
        if not src.available and src.id not in sources_cfg:
            continue
        flag = sources_cfg.get(src.id, src.enabled_by_default)
        if isinstance(flag, dict):  # {"enabled": bool, "priority": int}
            flag = flag.get("enabled", True)
        if flag:
            enabled.add(src.id)
    return enabled


def _source_status(src: SourceDef, *, status: str, detail: str = "",
                   items: int = 0) -> SourceStatus:
    return SourceStatus(
        source_id=src.id, name=src.name, status=status,
        detail=detail or (src.reason if status in {"unavailable", "disabled"} else ""),
        items=items,
    )


def _fetch_one(src: SourceDef, *, since: datetime, until: datetime,
               limit: int) -> list[NewsItem]:
    if src.kind in {"rss", "moex-rss"}:
        return RssAdapter(src).fetch(since=since, until=until, limit=limit)
    if src.kind == "tinvest-mcp":
        from tinvest.mcp_adapter import fetch_market_news  # lazy: optional

        return fetch_market_news(since=since, until=until, limit=limit, source=src)
    raise AdapterError(f"{src.id}: unknown adapter kind {src.kind!r}")


def fetch_all(
    *,
    instrument=None,
    days: int = 7,
    limit: int = 150,
    until: datetime | None = None,
    categories: set[str] | None = None,
) -> AggregatedNews:
    """Fetch every enabled source. ``until`` = point-in-time cutoff."""
    until = until or datetime.now(timezone.utc).replace(tzinfo=None)
    since = until - timedelta(days=int(days))
    enabled = _enabled_ids()

    statuses: list[SourceStatus] = []
    items: list[NewsItem] = []
    per_source_limit = max(10, int(limit // max(1, len(SOURCE_REGISTRY))) + 10)

    for src in SOURCE_REGISTRY:
        if categories and src.category not in categories:
            continue
        if not src.available and src.id not in enabled:
            statuses.append(_source_status(src, status="unavailable"))
            continue
        if src.id not in enabled:
            statuses.append(_source_status(src, status="disabled"))
            continue
        try:
            fetched = _fetch_one(
                src, since=since, until=until, limit=per_source_limit
            )
            items.extend(fetched)
            statuses.append(_source_status(src, status="ok", items=len(fetched)))
        except AdapterError as exc:
            logger.warning("news source %s failed: %s", src.id, exc)
            statuses.append(_source_status(src, status="error", detail=str(exc)))
        except Exception as exc:  # noqa: BLE001 — isolate unexpected failures
            logger.exception("news source %s crashed", src.id)
            statuses.append(
                _source_status(src, status="error", detail=f"{type(exc).__name__}: {exc}")
            )

    if instrument is not None:
        link_items(items, instrument)

    items = [i for i in items if i.published_at is None or i.published_at <= until]
    deduped = dedupe(items)
    deduped.sort(
        key=lambda x: (x.published_at is None, x.published_at or x.fetched_at),
        reverse=True,
    )
    return AggregatedNews(items=deduped[: int(limit)], statuses=statuses)
