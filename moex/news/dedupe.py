"""Cross-source deduplication (§7.3): URL, normalized title, similarity."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import NewsItem

_UTM = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "from", "rss", "referrer"}
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

_SIMILARITY_THRESHOLD = 0.90
# Titles shorter than this are matched exactly only (see _is_duplicate).
_MIN_FUZZY_LEN = 40
# ratio >= 0.90 forces |len(a) - len(b)| to stay within ~22%: with
# ratio = 2M/(la+lb) and M <= min(la, lb), the longer title cannot exceed
# 1.2222x the shorter one. Used to bucket candidates for the fuzzy pass.
_LEN_RATIO = 1.2222


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
    return _fuzzy_duplicate(ta, tb, Counter(ta), Counter(tb))


def _fuzzy_duplicate(ta: str, tb: str, ca: Counter, cb: Counter) -> bool:
    """SequenceMatcher pass, guarded by two cheap necessary conditions.

    `dedupe` compares every item against every earlier one, so the naive
    form cost 50 s on the ~900 headlines a 7-day fetch now yields. Both
    guards below only reject pairs that could never reach the threshold.
    """
    if min(len(ta), len(tb)) < _MIN_FUZZY_LEN:
        return False
    # Matching blocks never consume a character twice, so their size is at
    # most the character-multiset overlap — when2*overlap already falls
    # below threshold*(la+lb), ratio() cannot reach the threshold.
    if 2 * sum((ca & cb).values()) < _SIMILARITY_THRESHOLD * (len(ta) + len(tb)):
        return False
    return SequenceMatcher(None, ta, tb).ratio() >= _SIMILARITY_THRESHOLD


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Mark later duplicates; keep the earliest publication of a story.

    Semantics match the naive O(n^2) scan (first match in publication order
    wins), but exact URL/title hits go through hash lookups and the fuzzy
    pass only sees candidates whose normalized length is within the band
    that could still reach the similarity threshold.
    """
    kept: list[NewsItem] = []
    by_url: dict[str, NewsItem] = {}
    by_title: dict[str, NewsItem] = {}
    by_length: dict[int, list[tuple[NewsItem, str, Counter]]] = {}

    for item in sorted(
        items,
        key=lambda x: (x.published_at is None, x.published_at or x.fetched_at),
    ):
        url_key = canonical_url(item.url) if item.url else ""
        title_key = normalize_title(item.title)
        counter = Counter(title_key)

        winner = by_url.get(url_key) if url_key else None
        if winner is None and title_key:
            winner = by_title.get(title_key)
            if winner is None and len(title_key) >= _MIN_FUZZY_LEN:
                lo = max(_MIN_FUZZY_LEN, int(len(title_key) / _LEN_RATIO) - 1)
                hi = int(len(title_key) * _LEN_RATIO) + 1
                for length in range(lo, hi + 1):
                    for prior, prior_title, prior_counter in by_length.get(length, ()):
                        if _fuzzy_duplicate(title_key, prior_title,
                                            counter, prior_counter):
                            winner = prior
                            break
                    if winner is not None:
                        break

        kept.append(item)
        # Every kept item (duplicates included) stays findable, matching the
        # naive loop that scanned the whole list.
        if url_key:
            by_url.setdefault(url_key, item)
        if title_key:
            by_title.setdefault(title_key, item)
            if len(title_key) >= _MIN_FUZZY_LEN:
                by_length.setdefault(len(title_key), []).append(
                    (item, title_key, counter)
                )
        if winner is not None:
            item.is_duplicate = True
            item.duplicate_of = winner.id
    return kept


def unique(items: list[NewsItem]) -> list[NewsItem]:
    return [i for i in items if not i.is_duplicate]
