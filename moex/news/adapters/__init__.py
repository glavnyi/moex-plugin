"""News adapters."""

from .base import AdapterError, BaseAdapter
from .rss import RssAdapter, parse_feed

__all__ = ["AdapterError", "BaseAdapter", "RssAdapter", "parse_feed"]
