"""Fetch depth, output cap and window (regression: empty ticker news).

Live bug behind this file: ``per_source_limit = limit // registry_size + 10``
handed every source ~13 headlines for a 7-day request — a couple of hours —
so issuer news older than that never reached ``link_items()`` and every MOEX
ticker query came back as "No news found for SBER between 2026-10-10 and
2026-10-17". ``moex_news`` additionally ignored ``start_date`` when computing
that window, and the cross-source dedupe cost ~50 s (O(n^2) SequenceMatcher)
on the now-fuller corpus.

No network: every source is stubbed (spec §14.4).
"""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

from moex.news import aggregator as agg
from moex.news.dedupe import dedupe
from moex.news.models import NewsItem
from moex.news.registry import SourceDef

# Recorded shape of moex.instruments.resolve_instrument("SBER") — only
# attributes read by match_item/link_items, so no live ISS call is needed.
_SBER = SimpleNamespace(
    secid="SBER", isin="RU0009029540", short_name="Сбербанк",
    name="ПАО Сбербанк, акции", instrument_type="common_share",
)

_UNTIL = datetime(2026, 10, 17, 23, 59, 59)


def _source(id_: str = "ok") -> SourceDef:
    return SourceDef(
        id=id_, name=id_.upper(), kind="rss",
        url=f"https://example.com/{id_}.xml",
        category="exchange", available=True,
    )


def _item(id_: str, title: str, published: datetime, url: str | None = None,
          source: str = "ok") -> NewsItem:
    return NewsItem(
        id=id_, source_id=source, source_name=source,
        title=title, url=url if url is not None else f"https://ex.com/{id_}",
        published_at=published,
    )


def _stub_sources(monkeypatch, fetch) -> None:
    monkeypatch.setattr(agg, "SOURCE_REGISTRY", [_source()])
    monkeypatch.setattr(agg, "_enabled_ids", lambda: {"ok"})
    monkeypatch.setattr(agg, "_fetch_one", fetch)


def test_per_source_depth_follows_the_window_not_the_budget(monkeypatch):
    """A tiny output budget must not starve the per-source fetch depth."""
    requested: list[int] = []
    _stub_sources(monkeypatch,
                  lambda src, **kw: requested.append(kw["limit"]) or [])

    agg.fetch_all(days=7, limit=10, until=_UNTIL)
    agg.fetch_all(days=1, limit=10, until=_UNTIL)

    assert requested[0] == 175   # 7 * 25 — not 10 // 1 + 10 == 20
    assert requested[1] == 100   # floor: a full day of a high-volume wire


def test_per_source_depth_never_exceeds_the_tinvest_ceiling(monkeypatch):
    """The T-Invest MCP rejects limit >= 200 (180 works, 200 errors)."""
    requested: list[int] = []
    _stub_sources(monkeypatch,
                  lambda src, **kw: requested.append(kw["limit"]) or [])

    agg.fetch_all(days=30, limit=1000, until=_UNTIL)

    assert requested == [180]


def test_linked_items_survive_the_output_cap(monkeypatch):
    """Issuer news older than the newest headlines must not be dropped."""
    older = datetime(2026, 10, 11, 9, 0)
    newer = datetime(2026, 10, 17, 9, 0)
    linked_old = _item(
        "sber-old",
        "Сбербанк утвердил выплату купона по облигациям серии БО-01",
        older,
    )
    filler = [
        _item(f"fill-{n}",
              f"Цены на нефть изменились на мировом рынке сегодня номер {n}",
              newer)
        for n in range(6)
    ]
    _stub_sources(monkeypatch, lambda src, **kw: [linked_old, *filler])

    res = agg.fetch_all(instrument=_SBER, days=7, limit=2, until=_UNTIL)

    ids = [i.id for i in res.items]
    assert len(res.items) == 2          # the cap still applies
    assert "sber-old" in ids            # the instrument's own item kept


def test_without_an_instrument_the_cap_keeps_the_newest(monkeypatch):
    old = _item("old", "Совсем старая заметка о торгах на бирже", 
                datetime(2026, 10, 10, 9, 0))
    fresh = [
        _item(f"new-{n}", f"Свежая заметка о торгах номер {n} на бирже сегодня",
              datetime(2026, 10, 17, 9, n))
        for n in range(6)
    ]
    _stub_sources(monkeypatch, lambda src, **kw: [old, *fresh])

    res = agg.fetch_all(days=7, limit=3, until=_UNTIL)

    assert [i.id for i in res.items] == ["new-5", "new-4", "new-3"]


def test_moex_news_window_follows_start_date(monkeypatch):
    """start_date drives the fetch window — the old code read only end_date."""
    import moex.news as news_pkg
    from integration import gui_adapter as ga
    from tradingagents.dataflows import config as cfgmod

    monkeypatch.setattr(cfgmod, "_config",
                        {"market": "MOEX", "ticker": "SBER", "board": "TQBR"})
    monkeypatch.setattr("moex.instruments.resolve_instrument", lambda t: _SBER)

    seen: dict = {}

    def fake_fetch_all(**kw):
        seen.update(kw)
        return SimpleNamespace(items=[])

    monkeypatch.setattr(news_pkg, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(news_pkg, "news_report",
                        lambda *a, **k: "NO NEWS")

    ga.moex_news("SBER", "2026-10-10", "2026-10-17")
    short = seen["days"]
    ga.moex_news("SBER", "2026-09-17", "2026-10-17")
    long_ = seen["days"]

    assert short >= 7
    # 2026-09-17 → 2026-10-17 is 30 days wider than 2026-10-10 → 2026-10-17.
    assert long_ - short >= 22


def test_dedupe_scales_to_a_full_week_of_headlines():
    """~930 items used to take ~50 s (every pair ran SequenceMatcher)."""
    import random

    rng = random.Random(20261017)
    words = ["акции", "рынок", "банк", "дивиденды", "прибыль", "выручка",
             "ставка", "инфляция", "нефть", "газ", "экспорт", "курс", "рубль",
             "облигации", "доходность", "капитал", "инвесторы", "отчётность",
             "эмитент", "ликвидность", "сессия", "индекс", "валюта", "регулятор"]
    items = [
        _item(f"n-{n}",
              " ".join(rng.sample(words, 7)) + f" за номер {n}",
              datetime(2026, 10, 17, 8, n % 60),
              url=f"https://ex.com/{n}")
        for n in range(900)
    ]
    # 30 cross-source reprints of an already-seen story (same canonical URL).
    for n in range(30):
        items.append(
            _item(f"dup-{n}", "Совсем другая тема про рынок капитала сегодня",
                  datetime(2026, 10, 17, 7, 0),
                  url=f"https://ex.com/{n}?utm_source=mirror",
                  source="mirror")
        )

    started = time.perf_counter()
    out = dedupe(items)
    elapsed = time.perf_counter() - started

    assert len(out) == 930                       # dedupe never drops rows
    assert sum(1 for i in out if i.is_duplicate) >= 30
    assert elapsed < 8.0, f"dedupe took {elapsed:.1f}s on 930 items"
