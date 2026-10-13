from __future__ import annotations

import pytest

from moex.news.adapters.base import AdapterError
from moex.news.adapters.rss import parse_feed

FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>MOEX</title>
<item>
  <title>Итоги торгов 13.10.2026</title>
  <link>https://moex.com/n1?utm_source=rss</link>
  <description>&lt;p&gt;Индекс МосБиржи вырос на 1,2%&lt;/p&gt;</description>
  <pubDate>Tue, 13 Oct 2026 10:30:00 +0300</pubDate>
</item>
<item>
  <title>Вторая новость</title>
  <link>https://moex.com/n2</link>
  <description>Коротко</description>
  <pubDate>2026-10-12T09:00:00+03:00</pubDate>
</item>
</channel></rss>
""".encode("utf-8")


def test_parse_feed_items_and_times():
    items = parse_feed(FEED, "moex-all", "MOEX все новости")
    assert len(items) == 2
    first = items[0]
    assert first.title == "Итоги торгов 13.10.2026"
    assert first.source_id == "moex-all"
    assert first.url.startswith("https://moex.com/n1")
    # normalized to naive UTC (adapter contract)
    assert first.published_at is not None
    assert first.published_at.tzinfo is None
    # 10:30 MSK == 07:30 UTC
    assert first.published_at.hour == 7 and first.published_at.minute == 30
    # html stripped from summary
    assert "<p>" not in first.summary


def test_parse_feed_invalid_xml_raises():
    with pytest.raises(AdapterError):
        parse_feed(b"<html anti-bot page", "x", "X")


def test_parse_feed_iso_date_supported():
    items = parse_feed(FEED, "x", "X")
    assert items[1].published_at is not None
    assert items[1].published_at.day == 12
