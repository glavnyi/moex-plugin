from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from moex.news.dedupe import canonical_url, dedupe, normalize_title, unique
from moex.news.models import NewsItem

# Recorded shape of moex.instruments.resolve_instrument("SBER") — the
# matching logic under test only reads these attributes, so no live ISS
# call is needed (stable unit tests, spec §14.4).
_SBER = SimpleNamespace(
    secid="SBER", isin="RU0009029540", short_name="Сбербанк",
    name="ПАО Сбербанк, акции", instrument_type="common_share",
)


def _item(source: str, url: str, title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        id=f"{source}:{url}",
        source_id=source,
        source_name=source,
        title=title,
        summary=summary,
        url=url,
        published_at=datetime(2026, 10, 13, 10, 0, 0),
    )


def test_canonical_url_strips_tracking():
    a = canonical_url("https://example.com/news/1?utm_source=x&utm_medium=y")
    b = canonical_url("https://example.com/news/1")
    assert a == b


def test_normalize_title_unifies_yo():
    assert normalize_title("Россия идёт вперёд") == normalize_title("Россия идет вперед")


def test_dedupe_by_canonical_url():
    items = [
        _item("a", "https://ex.com/n/1?utm_source=x", "Заголовок один"),
        _item("b", "https://ex.com/n/1", "Совсем другой заголовок"),
        _item("c", "https://ex.com/n/2", "Вторая новость"),
    ]
    out = dedupe(items)
    assert len(out) == 3  # all items stay, duplicates are flagged
    assert sum(1 for i in out if i.is_duplicate) == 1
    assert len(unique(out)) == 2


def test_dedupe_by_similar_title():
    base = "Сбербанк увеличил чистую прибыль по МСФО за квартал"
    items = [
        _item("a", "https://ex.com/a", base),
        _item("b", "https://ex.com/b", base + " на 12%"),
        _item("c", "https://ex.com/c", "Совсем другая тема про нефть"),
    ]
    out = dedupe(items)
    assert len(unique(out)) == 2
    assert any(i.is_duplicate for i in out)


def test_linking_matches_secid_word_boundary():
    from moex.news.linking import match_item

    hit, terms = match_item("Акции SBER подорожали сегодня", _SBER)
    assert hit and "SBER" in [t.upper() for t in terms]


def test_linking_matches_short_name_brand_word():
    from moex.news.linking import match_item

    hit, terms = match_item("Сбербанк увеличил прибыль", _SBER)
    assert hit and any("Сбербанк" in t for t in terms)


def test_linking_does_not_match_country_name():
    from moex.news.linking import match_item

    hit, _ = match_item("Экономика России выросла в квартале", _SBER)
    assert not hit
