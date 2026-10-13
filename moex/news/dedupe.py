"""Cross-source deduplication (§7.3): URL, normalized title, similarity."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import NewsItem

_UTM = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "from", "rss", "referrer"}
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

_SIMILARITY_THRESHOLD = 0.90


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _UTM
    ]
    clean = parts._replace(
        netloc=parts.netloc.lower(),
        path=parts.path.rstrip("/") or "/",
        query=urlencode(query),
        fragment="",
    )
    return urlunparse(clean)


def normalize_title(title: str) -> str:
    text = (title or "").casefold().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def item_id(source_id: str, url: str, title: str) -> str:
    basis = canonical_url(url) or normalize_title(title)
    raw = f"{source_id}|{basis}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _is_duplicate(a: NewsItem, b: NewsItem) -> bool:
    if a.url and b.url and canonical_url(a.url) == canonical_url(b.url):
        return True
    ta, tb = normalize_title(a.title), normalize_title(b.title)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    # Short shared titles must match exactly; longer ones may differ slightly.
    return (
        SequenceMatcher(None, ta, tb).ratio() >= _SIMILARITY_THRESHOLD
        and min(len(ta), len(tb)) >= 40
    )


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Mark later duplicates; keep the earliest publication of a story."""
    kept: list[NewsItem] = []
    for item in sorted(
        items,
        key=lambda x: (x.published_at is None, x.published_at or x.fetched_at),
    ):
        winner = None
        for prior in kept:
            if prior.source_id == item.source_id and prior.title == item.title:
                winner = prior
                break
            if _is_duplicate(prior, item):
                winner = prior
                break
        if winner is None:
            kept.append(item)
        else:
            item.is_duplicate = True
            item.duplicate_of = winner.id
            kept.append(item)
    return kept


def unique(items: list[NewsItem]) -> list[NewsItem]:
    return [i for i in items if not i.is_duplicate]
