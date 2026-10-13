"""Link news items to MOEX instruments (high-precision matching, §7.4).

Only precision terms are used to avoid false positives:
* SECID and ISIN (word-boundary, case-insensitive);
* short name words (brand words like «Сбербанк»), never the full legal
  name — words like «России»/«Банк» would match every regulator notice.
"""

from __future__ import annotations

import re

_WORD = r"(?<![0-9A-Za-zА-Яа-яЁё]){}(?![0-9A-Za-zА-Яа-яЁё])"

_STOP_WORDS = {
    "ПАО", "АО", "ЗАО", "ОАО", "ООО", "акции", "акция", "акционерный",
    "компания", "группа", "банк", "фонд", "облигации", "России", "Россия",
    "Москва", "плюс", "для", "международная", "промышленная", "финансовая",
}


def _precision_terms(instrument) -> list[str]:
    terms: list[str] = []
    secid = str(getattr(instrument, "secid", "") or "")
    isin = str(getattr(instrument, "isin", "") or "")
    short = str(getattr(instrument, "short_name", "") or "")
    if secid:
        terms.append(secid)
    if isin:
        terms.append(isin)
    for word in re.split(r"[\s«»()\-\–—\.]+", short):
        if (
            word
            and len(word) >= 4
            and word.casefold() not in {s.casefold() for s in _STOP_WORDS}
            and not word.isdigit()
        ):
            terms.append(word)
    return terms


def _patterns(instrument) -> list[re.Pattern]:
    pats: list[re.Pattern] = []
    seen: set[str] = set()
    for term in _precision_terms(instrument):
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        pats.append(re.compile(_WORD.format(re.escape(term)), re.IGNORECASE))
    return pats


def match_item(item_text: str, instrument) -> tuple[bool, list[str]]:
    """Return (matched, matched_terms) for an instrument against item text."""
    if not item_text:
        return False, []
    hits: list[str] = []
    for pat in _patterns(instrument):
        m = pat.search(item_text)
        if m:
            hits.append(m.group(0))
    return bool(hits), hits


def link_items(items, instrument) -> int:
    """Attach tickers/issuers on items; return how many matched."""
    linked = 0
    for item in items:
        text = f"{item.title}\n{item.summary}"
        ok, hits = match_item(text, instrument)
        if ok:
            linked += 1
            secid = str(getattr(instrument, "secid", "") or "")
            if secid and secid not in item.tickers:
                item.tickers.append(secid)
            for hit in hits:
                if hit not in item.issuers:
                    item.issuers.append(hit)
    return linked
