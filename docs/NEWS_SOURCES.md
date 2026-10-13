# NEWS_SOURCES

Реестр: `moex/news/registry.py` (26 источников). Проверка доступности —
`probe_all()`, последний живой прогон: **2026-10-13**.
Статусы в GUI: вкладка MOEX → «Новости и источники».

## Доступны (16)

| id | Источник | Тип | Как получаем |
|---|---|---|---|
| moex-100 | MOEX — все новости | RSS | moex.com/export/news.aspx?cat=100 |
| moex-101 | MOEX — главные новости | RSS | cat=101 |
| moex-102 | MOEX — результаты торгов | RSS | cat=102 |
| moex-104 | MOEX — листинг | RSS | cat=104 |
| moex-122 | MOEX — параметры риска | RSS | cat=122 |
| moex-300 | MOEX — события | RSS | cat=300 |
| cbr-press | Банк России — пресс-релизы | RSS | cbr.ru/feeds/press_daily |
| cbr-news | Банк России — новости | RSS | cbr.ru/feeds/mict_news |
| cbr-events | Банк России — события | RSS | cbr.ru/feeds/mict_events |
| cbr-regions | Банк России — региональная сеть | RSS | cbr.ru/feeds/mict_regions |
| cbr-currency | Банк России — курсы | RSS | cbr.ru/feeds/daily_req.xml |
| interfax | Интерфакс | RSS | if24.rss.ifax.ru |
| vedomosti | Ведомости | RSS | vedomosti.ru/rss |
| investing-ru | Investing.com (рус.) | RSS | ru.investing.com/rss/news.rss |
| tass | ТАСС | RSS | tass.ru/rss |
| ria | РИА Новости (архив) | RSS | ria.ru/export/rss |
| tinvest-news | T-Invest MCP (новости) | MCP | нужен TINVEST_TOKEN; ищет инструменты с «news» в имени |

У каждого доступного: заголовок, текст (до 700 символов), ссылка, время
(pubDate/ISO → UTC), тикерная линковка. Требований к ключам нет, кроме
tinvest-news.

## Недоступны (10) — точные причины (§7.2)

| id | Причина (проверено 2026-10-13) |
|---|---|
| minfin | HTTP 503 от WAF на все проверенные пути; бесплатного RSS/API нет |
| rosstat | Публичный RSS/API не обнаружен; сайт HTML, требует российский корневой CA |
| e-disclosure | Страницы защищены JS-обфускацией; RSS/API нет |
| rbc | HTTP 401 (антибот) на RSS и страницы без браузерной сессии |
| rbc-invest | HTTP 401 (антибот) |
| akm | HTTP 403 от WAF; капча |
| bcs-express | Вместо RSS — HTML challenge-страница; публичного фида нет |
| smart-lab | Таймаут сетевого уровня (фид существует, но недоступен автоматически) |
| finam | HTTP 403 — бесплатный фид закрыт WAF |
| ria (основной канал) | Используется архивный экспорт ria.ru/export/rss как рабочий канал |

Не обходили платный доступ, CAPTCHA и антибот — источник честно помечен
`available=false` с текстом причины и не участвует в выдаче.

## Модель и обработка

- `NewsItem`: id, source_id/name, title, summary, url, published_at (UTC),
  fetched_at, tickers, issuers, event_type, sentiment(+confidence), origin,
  is_duplicate/duplicate_of, available.
- Дедупликация: canonical URL (utm/rss/from/referrer отбрасываются),
  normalize title (ё→е, пунктуация), SequenceMatcher ≥ 0.90 при длине ≥ 40.
  Дубликаты помечаются, `unique()` отбирает победителей.
- Линковка: SECID/ISIN/short_name с word-boundary; полные названия
  («Сбербанк», «Газпром») + эмитентские термины.
- Агрегатор: сбой одного источника не влияет на остальные; point-in-time
  фильтр `published <= until` для исторических прогонов.
- Внешний текст считается недоверенным: исполняющихся инструкций в нём нет,
  размер ограничен, HTML вычищается.
