"""MOEX news: registry, adapters, dedup, aggregation, reporting."""

from .aggregator import AggregatedNews, fetch_all
from .dedupe import dedupe, unique
from .format import global_news_report, news_report, statuses_report
from .linking import link_items, match_item
from .models import NewsItem, SourceStatus
from .registry import SOURCE_REGISTRY, SourceDef, registry_by_id

__all__ = [
    "AggregatedNews",
    "NewsItem",
    "SOURCE_REGISTRY",
    "SourceDef",
    "SourceStatus",
    "dedupe",
    "fetch_all",
    "global_news_report",
    "link_items",
    "match_item",
    "news_report",
    "registry_by_id",
    "statuses_report",
    "unique",
]
