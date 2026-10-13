"""News adapter protocol."""

from __future__ import annotations

from ..models import NewsItem
from ..registry import SourceDef


class AdapterError(RuntimeError):
    """A source failed to fetch/parse — surfaced as a per-source status."""


class BaseAdapter:
    def __init__(self, source: SourceDef):
        self.source = source

    def fetch(self, *, since=None, until=None, limit: int = 50) -> list[NewsItem]:
        raise NotImplementedError
