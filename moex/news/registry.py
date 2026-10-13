"""Registry of news sources with probe-verified availability (§7.1, §7.2).

Every entry records the real endpoint that was probed live on
2026-10-13 from this environment. Sources with ``available=False`` are
kept on purpose: the GUI shows them with an exact reason instead of
pretending they are integrated (spec §7.2).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDef:
    id: str
    name: str
    kind: str  # rss | moex-rss | tinvest-mcp
    url: str
    category: str  # exchange | regulator | ministry | statistics | media | broker
    available: bool = True
    requires_token: bool = False
    enabled_by_default: bool = True
    reason: str = ""
    notes: str = ""


MOEX_NEWS_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

SOURCE_REGISTRY: list[SourceDef] = [
    # --- MOEX official (§7.1 items 1-6) --------------------------------
    *[SourceDef(
        id=f"moex-{cat}",
        name=f"MOEX news cat={cat}",
        kind="moex-rss",
        url=f"https://moex.com/export/news.aspx?cat={cat}",
        category="exchange",
        notes=name,
    ) for cat, name in (
        (100, "все новости"),
        (101, "главные новости"),
        (102, "результаты торгов"),
        (104, "листинг"),
        (122, "параметры риска"),
        (300, "события (последний пункт 2024-07 — feed не обновляется)"),
    )],
    # --- Regulators (§7.1 items 7-10) ----------------------------------
    SourceDef("cbr-press", "Банк России — пресс-релизы", "rss",
              "https://www.cbr.ru/rss/RssPress", "regulator"),
    SourceDef("cbr-news", "Банк России — новости", "rss",
              "https://www.cbr.ru/rss/RssNews", "regulator"),
    SourceDef("cbr-events", "Банк России — события", "rss",
              "https://www.cbr.ru/rss/eventrss", "regulator"),
    SourceDef("cbr-regions", "Банк России — региональные новости", "rss",
              "https://www.cbr.ru/rss/nregimr2", "regulator"),
    SourceDef("cbr-currency", "Банк России — курс валют", "rss",
              "https://www.cbr.ru/rss/RssCurrency", "regulator"),
    SourceDef("minfin", "Министерство финансов РФ", "rss",
              "https://minfin.gov.ru/ru/rss/", "ministry", available=False,
              reason="HTTP 503 от WAF на все проверенные пути (2026-10-13), "
                     "бесплатный RSS/API недоступен автоматизации",
              enabled_by_default=False),
    SourceDef("rosstat", "Росстат", "rss", "https://rosstat.gov.ru/", "statistics",
              available=False,
              reason="Публичный RSS/API не обнаружен; сайт HTML "
                     "(требуется российский корневой CA, сертификаты под "
                     "Russian Trusted Sub CA)",
              enabled_by_default=False),
    SourceDef("e-disclosure", "Раскрытие информации (e-disclosure.ru)", "rss",
              "https://e-disclosure.ru/News/", "regulator", available=False,
              reason="Страницы защищены JS-обфускацией, RSS/API нет "
                     "(2026-10-13); публичные раскрытия читаются вручную",
              enabled_by_default=False),
    # --- Business media (§7.1 items 11-20) -----------------------------
    SourceDef("interfax", "Интерфакс", "rss",
              "https://interfax.ru/rss.asp", "media"),
    SourceDef("rbc", "РБК", "rss", "https://www.rbc.ru/rss/news", "media",
              available=False,
              reason="HTTP 401 на все RSS/страницы без браузерной сессии "
                     "(антибот, 2026-10-13)",
              enabled_by_default=False),
    SourceDef("rbc-invest", "РБК Инвестиции", "rss", "https://www.rbc.ru/rss/",
              "media", available=False,
              reason="HTTP 401 (антибот) как у rbc.ru (2026-10-13)",
              enabled_by_default=False),
    SourceDef("vedomosti", "Ведомости", "rss",
              "https://www.vedomosti.ru/rss/news", "media"),
    SourceDef("akm", "AK&M", "rss", "https://www.akm.ru/rss/", "media",
              available=False,
              reason="HTTP 403 от WAF; RSS/HTML без обхода капчи недоступны "
                     "(2026-10-13)",
              enabled_by_default=False),
    SourceDef("bcs-express", "БКС Экспресс", "rss",
              "https://bcs-express.ru/news/rss", "broker", available=False,
              reason="Вместо RSS отдаётся HTML challenge-страница "
                     "(2026-10-13); публичного фида нет",
              enabled_by_default=False),
    SourceDef("smart-lab", "Smart-Lab", "rss", "https://smart-lab.ru/blog/rss.xml",
              "media", available=False,
              reason="Соединение не устанавливается (таймаут сетевого уровня, "
                     "2026-10-13) — фид существует, но недоступен из этой сети",
              enabled_by_default=False),
    SourceDef("investing-ru", "Investing.com (рус.)", "rss",
              "https://ru.investing.com/rss/news.rss", "media"),
    SourceDef("tass", "ТАСС", "rss", "https://tass.ru/rss/v2.xml", "media"),
    SourceDef("ria", "РИА Новости", "rss",
              "https://ria.ru/export/rss2/archive/index.xml", "media",
              notes="общий архивный фид; рубриковые пути /rss2/<cat> не работают"),
    # --- Brokers (§7.1 items 21-23) ------------------------------------
    SourceDef("finam", "Финам (публикации брокера)", "rss",
              "https://www.finam.ru/rss/", "broker", available=False,
              reason="HTTP 403 (2026-10-13) — бесплатный фид закрыт WAF",
              enabled_by_default=False),
    SourceDef("tinvest-news", "T-Invest (новости эмитентов и рынка)", "tinvest-mcp",
              "https://invest-public-api.tbank.ru/mcp", "broker",
              available=True, requires_token=True, enabled_by_default=True,
              notes="доступен при настроенном API-токене; инструменты news "
                    "подтверждены документацией developer.tbank.ru/invest/mcp"),
]

# Short browser UA for media hosts that reject the default bot UA.
RSS_UA = MOEX_NEWS_UA

DEFAULT_SYNC_SECONDS = 900  # 15 min


def registry_by_id() -> dict[str, SourceDef]:
    return {s.id: s for s in SOURCE_REGISTRY}


def default_enabled_ids() -> list[str]:
    return [s.id for s in SOURCE_REGISTRY if s.available and s.enabled_by_default]
