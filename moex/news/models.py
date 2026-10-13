"""Normalized news model (spec §7.3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class NewsItem:
    id: str
    source_id: str
    source_name: str
    title: str
    summary: str = ""
    url: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = field(default_factory=utcnow)
    tickers: list[str] = field(default_factory=list)
    issuers: list[str] = field(default_factory=list)
    event_type: str = ""
    sentiment: str | None = None
    sentiment_confidence: float | None = None
    origin: str = ""
    is_duplicate: bool = False
    duplicate_of: str = ""
    available: bool = True

    @property
    def published_iso(self) -> str:
        if not self.published_at:
            return ""
        dt = self.published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["published_at"] = self.published_iso
        data["fetched_at"] = self.fetched_at.strftime("%Y-%m-%d %H:%M UTC")
        return data


@dataclass
class SourceStatus:
    """Live fetch outcome for one registered source (§7.5 GUI statuses)."""

    source_id: str
    name: str
    status: str  # ok | error | unavailable | disabled | skipped
    detail: str = ""
    items: int = 0
    last_success: datetime | None = None
    checked_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "items": self.items,
            "last_success": self.last_success.strftime("%Y-%m-%d %H:%M UTC")
            if self.last_success
            else None,
            "checked_at": self.checked_at.strftime("%Y-%m-%d %H:%M UTC"),
        }
