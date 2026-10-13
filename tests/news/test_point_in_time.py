"""Point-in-time news contour (§10 / §14.4): a historical run must only see
items published at or before the cutoff, one broken source must never block
the others, and unavailable sources must not masquerade as empty successes.

All source responses are recorded/stubbed — these tests never hit the
network, so they stay stable (§14.4)."""

from __future__ import annotations

from datetime import datetime

from moex.news import aggregator as agg
from moex.news.adapters import AdapterError
from moex.news.models import NewsItem
from moex.news.registry import SOURCE_REGISTRY, SourceDef


def _item(n: int, published: datetime | None) -> NewsItem:
    return NewsItem(
        id=f"rec:{n}",
        source_id="ok",
        source_name="recorded",
        title=f"Заголовок {n}",
        summary="",
        url=f"https://example.com/{n}",
        published_at=published,
    )


def _source(id_: str) -> SourceDef:
    return SourceDef(
        id=id_, name=id_.upper(), kind="rss",
        url=f"https://example.com/{id_}.xml",
        category="exchange", available=True,
    )


def test_until_cutoff_drops_future_items(monkeypatch):
    cutoff = datetime(2026, 10, 10, 12, 0, 0)
    past = _item(1, datetime(2026, 10, 10, 9, 0, 0))
    future = _item(2, datetime(2026, 10, 11, 9, 0, 0))  # after the cutoff
    no_date = _item(3, None)

    monkeypatch.setattr(agg, "SOURCE_REGISTRY", [_source("ok")])
    monkeypatch.setattr(agg, "_enabled_ids", lambda: {"ok"})
    monkeypatch.setattr(
        agg, "_fetch_one",
        lambda src, **kw: [past, future, no_date],
    )

    res = agg.fetch_all(until=cutoff, days=2, limit=50)
    ids = {i.id for i in res.items}
    assert "rec:1" in ids            # published before cutoff — kept
    assert "rec:3" in ids            # no date — kept (cannot prove it is future)
    assert "rec:2" not in ids        # future leak — dropped (§10 no lookahead)
    assert {s.source_id for s in res.statuses} == {"ok"}


def test_one_broken_source_does_not_block_the_rest(monkeypatch):
    cutoff = datetime(2026, 10, 10, 12, 0, 0)
    good, bad = _source("good"), _source("bad")
    monkeypatch.setattr(agg, "SOURCE_REGISTRY", [good, bad])
    monkeypatch.setattr(agg, "_enabled_ids", lambda: {"good", "bad"})

    def fake(src, **kw):
        if src.id == "bad":
            raise AdapterError("HTTP 503 from source")
        return [_item(1, datetime(2026, 10, 10, 8, 0, 0))]

    monkeypatch.setattr(agg, "_fetch_one", fake)
    res = agg.fetch_all(until=cutoff, days=1, limit=50)

    st = {s.source_id: s for s in res.statuses}
    assert st["good"].status == "ok" and st["good"].items == 1
    assert st["bad"].status == "error" and "503" in st["bad"].detail
    assert len(res.items) == 1  # the healthy source still delivered


def test_disabled_and_unavailable_sources_are_reported_not_hidden(monkeypatch):
    cutoff = datetime(2026, 10, 10, 12, 0, 0)
    off = _source("off")
    gone = SourceDef(
        id="gone", name="GONE", kind="rss", url="https://example.com/gone.xml",
        category="media", available=False,
    )
    monkeypatch.setattr(agg, "SOURCE_REGISTRY", [off, gone])
    monkeypatch.setattr(agg, "_enabled_ids", lambda: set())  # nothing enabled

    res = agg.fetch_all(until=cutoff, days=1, limit=50)
    st = {s.source_id: s for s in res.statuses}
    assert st["off"].status == "disabled"
    assert st["gone"].status == "unavailable"  # not a fake empty "ok"
    assert res.items == []


def test_registry_covers_the_required_moex_categories():
    # §7.1 — the six MOEX export categories are all registered and live.
    ids = {s.id for s in SOURCE_REGISTRY}
    assert {"moex-100", "moex-101", "moex-102",
            "moex-104", "moex-122", "moex-300"} <= ids
    assert len(SOURCE_REGISTRY) == 26
