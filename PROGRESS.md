# PROGRESS (internal state log — TradingAgents-MOEX)

Updated: 2026-10-19 (session 6). Nothing here is user documentation; see `docs/`.

## Done (live-verified)
- **Скринер автоторговли: автоотбор инструментов TQBR** (2026-10-19,
  сессия 6): ручной ввод тикеров больше не требуется.
  - `moex/screener.py`: `fetch_universe` — весь борд одной страницей ISS
    (510 тикеров TQBR: LAST/BID/SPREAD/TRADINGSTATUS/VALTODAY, TTL-кэш
    120 с) и чистый `select_candidates` (фильтры `not_trading/no_price/
    cheap/illiquid/wide_spread/low_list/excluded`, очередь `deferred`
    для «решение принято/позиция открыта», ранжирование оборот↓/спред↑,
    топ-N по `max_candidates_per_day`). Каждый отказ журналируется с
    именем причины — GUI показывает разбивку.
  - `integration/autotrade_service.py`: цикл берёт worklist =
    watchlist (первым, ручной режим сохранён) + один кандидат скринера
    за цикл (анализ ~20 мин, следующий цикл возьмёт следующего по
    очереди). Бюджет LLM (`max_candidates_per_day`, по умолчанию 3)
    считается по журналу `screener_candidate` — переживает перезапуск.
    События журнала: `screener_scan` (разбивка), `screener_candidate`,
    `screener_error` (monitor: warn).
  - **Учёт заявок** (`moex/autotrade.py`): поле `kind` в журнале.
    Лимит `max_orders_per_day` расходуют только `open/increase/retry`
    с `placed!=false`; `close/protective` бесплатны (позиция никогда не
    остаётся без защиты), `placed=false` вообще не заявка (анализ тикера
    не тратит бюджет). `order_budget()` → used/limit/left по московскому
    дню, переживает перезапуск (журнал).
  - **Дубли**: до отправки — активная заявка по тикеру у брокера
    матчится по `client_order_id` из журнала (cid = строгий UUID,
    T-Invest код 30028 запрещает префиксы — live-verified ранее);
    после сетевого сбоя `_send_with_recovery`: маркет не переотправляется
    вовсе (fail-closed), лимитная — только после повторной проверки
    GetOrders, `TInvestError` = определённый отказ без повторов.
  - **GUI** (вкладка «Авто»): карточка «Скринер» — пороги, стоп-лист,
    KPI (заявок сегодня X/Y, осталось, просканировано, прошло фильтров,
    анализов N/M, отклонено сигналов), разбивка отказов, кандидаты,
    таблица заявок за день со статусами, кнопка предпросмотра
    (`GET /api/autotrade/screener` — без LLM и заявок).
    `index.html` — upstream-файл: патч 0001 GUI перегенерирован,
    `check_compatibility` зелёный (pin=774a0fa, applied).
  - Документация: `docs/CONFIGURATION.md` — секция `screener` и правила
    учёта заявок (§3).
  - Live-смок: скан реального ISS 510→154 прошли фильтры (топ GAZP/
    ROSN/SBER по обороту), `status()`/`screener_preview()` в процессе —
    бюджет 1/3 по реальному журналу, легаси-заявка SBER 19.10 считается.
  - **Сьюты: 309 passed** (было 279; +`test_screener.py` и 13 тестов
    сервиса: виды заявок, бюджет, гейт, цикл с кандидатом, 5 путей
    `_send_with_recovery`, дедуп по активной заявке, UUID-cid).
    Рестарт GUI 23:55 применит всё разом.
  - **Операционно (15:10)**: bt_3y упёрся в дневную бесплатную квоту
    OpenRouter (1000 req/day, remaining 0 с 14:09 MSK, reset 03:00 MSK
    20.10): ячейки с 3-й падают по 429. Прогон остановлен — 2 успешные
    ячейки (2023-10-18, 2023-11-17) сохранены в `trading_memory.md`
    (FAIL-ячейки резюмируются), докатка запланирована таймером
    `tradingagents-bt-resume` на **03:10 MSK 20.10** той же командой
    `--run-id bt_3y`. Квота делится с GUI: скринеру завтра достанется
    3 финансируемых анализа (лимит `max_candidates_per_day=3`).
  - Скринер **включён** в настройках (`screener.enabled=true`) — цикл
    daemon сразу перешёл на автоотбор; до сброса квоты попытки анализа
    кандидатов падают по 429 (cycle_error в журнале — ожидаемо и
    безвредно: бюджет анализов 3/день не даёт молотить дальше).
  - **Политика расписания (по решению владельца, 19.10)**: дневная
    квота LLM (OpenRouter free, 1000 req/day) в **торговые дни** целиком
    принадлежит автоторговле; бэктесты — **только в сб/вс**. Таймер
    докатки `tradingagents-bt-resume` (вт 03:10) отменён; вместо него
    постоянный `tradingagents-bt-weekend` (Sat+Sun 03:10 MSK, повтор
    каждую неделю) дёргает `scripts/run_backtest_weekend.sh` — обвязка с
    `flock -n` на `bt_3y/run.lock` (воскресный запуск молча пропускается,
    если субботний ещё идёт). Бэктест возобновляет по журналу `--run-id`:
    каждые выходные прибавляется ~50-70 ячеек из квоты, до завершения
    111 ячеек хватит 2-3 уикендов. В будни цикл авторежима гейтом
    `market_open` не пускает скринер до06:50 (утр. сессия), так что LLM
    в рабочие дни тратится только на3 анализа кандидатов + ручные
    запуски.
- **«Уроки» §11 не подмешиваются в бэктест** (2026-10-19, сессия 5): аудит
  `config_adapter.enrich_run_config` показал, что `lessons_block()`
  (глобальный `~/.tradingagents/tinvest/lessons.jsonl`, 8 живых записей
  SBER за 14–15.10.2026) подмешивался в промпт КАЖДОЙ ячейки бэктеста —
  знание будущего для ячеек 2023 года (требование: не подмешивать).
  Теперь явный гейт `_lessons_wanted(params)`: `moex_lessons=False`
  (bool/строка) закрывает канал; дефолт не меняется (живые запуски видят
  уроки как раньше). Раннер бэктеста всегда передаёт False.
  Заодно `workflow.build_analysis_context` (публичный API) фетчил новость
  БЕЗ `until` — свежая лента в историческом контексте любого будущего
  вызова; теперь `_news_until(curr_date)` режет по дате ячейки.
  8 тестов `tests/unit/test_pointintime_channels.py` (гейт включая
  строковые формы, обе ветки enrich_run_config, границы `_news_until`).
  bt_3y перезапущен в третий раз (13:15, прежний прогон 5 ячеек
  сохранён как `bt_3y_lessons_mix`) — все ячейки чистые.
- **Капы новостных tool-репортов + честные итоги бэктеста** (2026-10-19,
  сессия 5): (1) `integration/gui_adapter._cap_report` — get_news ограничен
  12K, get_global_news 8K символов, обрезка строго на границе «###» с
  пометкой «показано X из Y, пропущено Z»: после фикса content-сниппетов
  T-Invest худший случай репорта вырос до ~42 КБ (контекст-бомба для
  free-модели). 4 теста `tests/unit/test_news_tool_caps.py`.
  (2) `scripts/backtest_moex.py`: мёртвый счётчик `unscored` реально считает
  settled-записи с нераспознанной alpha (раньше ложно попадали в pending);
  при старте пишется `run_meta.json` — `--summary`/`summary.md` описывает
  сетку САМОГО прогона, а не текущие CLI-аргументы (была ложь «шаг от
  2023-10-19» при реальной сетке от 2023-10-18). 3 теста.
  (3) `scripts/monitor_autotrade.py`: null-безопасный `stats` в
  `collect_bad_runs` (прогон с `"stats": null` ронял монитор в exit-2).
  1 тест. (4) `moex/market_data/iss.py`: докстринг `get_candles` уточнён —
  Date нормализуется к дню, интрадей-интервалы схлопываются (латентно:
  никто интрадей не запрашивает, все вызовы 1d).
  Итог сьюты: **279 passed**. Операционно: рестарт GUI запланирован на
  23:55 сегодня (systemd-run-таймер `tradingagents-gui-restart-night`,
  отмена: `systemctl --user stop tradingagents-gui-restart-night.timer`) —
  применит в GUI-процессе (стартовал 07:35, до правок) http.py
  cache_put-хардинг (GUI и бэктест делят кэш-каталог!), капы новостей и
  mcp_adapter-фиксы.
- **Фундаменталы: point-in-time цена** (2026-10-19): `fundamentals_report`
  для исторической ячейки подставлял ЖИВУЮ котировку (`get_quote`) —
  аналитик про 2023 год видел цену/рыночную капитализацию 2026 года прямо
  в снапшоте бэктеста. Теперь дата `curr_date` >= сегодня → интрадей-котировка
  как раньше; прошлая дата → «last» из as-of свечей (`load_ohlcv`), явная
  пометка point-in-time. 5 тестов `tests/unit/test_fundamentals_pointintime.py`.
- **T-Invest новости: SSE oversize + даты** (2026-10-19): две живые проблемы
  MCP-адаптера. (1) Новостная лента с полными текстами переваливала 400 КБ
  bound (§13) — поток рвался посередине JSON, парсер падал, ячейки шли БЕЗ
  T-Invest новостей (9/31 в прогоне 18.10). Теперь фреймовый SSE-разбор
  (`_sse_message`, только целые кадры), distinct-ошибка «exceeds the bound»
  и повтор с halves limit (80→40→20→10) — источник больше не теряется.
  (2) Дата T-Invest-новостей живёт в поле `ts` (RFC3339, наносекунды), а
  парсер знал только publishedAt/date/time → published_at=None ОБХОДИЛ
  фильтры since/until, и свежая лента утекала в исторические ячейки
  (контентная утечка будущего). `ts` парсится (+ unix-ms fallback), берётся
  `content` как summary[:700]. Живая проверка: 20/20 items с датой,
  until=now−3d отсекает всё свежее (0 просачиваний). 13 тестов.
  Бэктест `bt_3y` перезапущен дважды за эти фиксы (оба прежних прогона
  сохранены: `bt_3y_pre_fundamentals_fix`, `bt_3y_leaky_news`).
- **Reconciliation: честные mismatches** (2026-10-19): сравнение журнала
  `orders_journal` с листингом открытых заявок флагало **всю историю**
  исполненных/отменённых ордеров, как только существовал хотя бы одна
  открытая заявка, а пустой листинг глушил проверку (fail-open — критический
  «журнал верит в открытую заявку, брокер пуст» не детектировался вовсе).
  Теперь `tinvest.reconciliation._journal_mismatches`: флагаются только
  заявки со статусом NEW/PARTIALLY_EXECUTED и свежие (окно 48 ч), пустой
  листинг — тоже повод. 8 тестов `tests/unit/test_reconciliation_mismatches.py`.
  Проверено вживую на sandbox-вызове: ошибки T-Invest приходят HTTP 404 без
  ключа `payload` — обе ветки детекции ошибок в `TInvestClient.call`
  корректны (отдельный аудит: pay_in со знаковой суммой защищён,
  настройки новостей читаются на каждый fetch, update_settings — shallow-merge).
  Операционный урок того же дня: перезапуск GUI убил идущий анализ (утренняя
  сессия MOEX 06:50–09:59 открыта) — рестарты делать только вне сессий,
  иначе монитор честно покажет баг-прогон «server restart».
- **Бэктест MOEX** (2026-10-18): `scripts/backtest_moex.py` — полный граф
  по сетке (тикер × дата) с конфигом через `gui.run_manager._build_config`
  (паритет с живым циклом: те же модели/depth, `TA_LLM_*`), point-in-time
  снапшот `as_of=дата ячейки`, settlement vs IMOEX через
  `integration/moex_settlement`, свой `memory_log_path` (живая память не
  трогается), возобновление по run-id, `--plan/--summary/--max-cells`,
  лимит ячейки `--cell-timeout 2700` (SIGALRM). Скоринг по resolved-записям
  (hit rate по рейтингу, средний alpha) в `summary.md`. 11 офлайн-тестов
  `tests/unit/test_backtest_moex.py`; итог сьюты 242 passed.
  **Попутно закрыта утечка будущего**: снапшотная секция новостей брала
  `until=now` и `news_report` не фильтрует даты — в ячейку 2024 года ушли бы
  новости 2026-го; теперь `integration/gui_adapter.snapshot_news_window`
  (живой путь без `as_of` побайтно прежний: 8 дней до now). Живо проверено:
  снапшот `as_of=2024-01-15` — свечи кончаются на 01-15, дат позже as_of в
  новостях 0.
- **Upstream v0.7.0** (2026-10-18): standalone `tradingagents` перешёл с
  v0.6.0 (`1394a3f`) на v0.7.0 (`e8f5503`, 11 коммитов: ChatGPT-plan
  sign-in provider, CLI update-check, fix(memory) scoring от первой
  закрытия после даты). Наш патч `0001-moex-market-cli.patch` применился
  чисто; апстрим-тесты 1361 passed + наши 231 passed. GUI-репо тега
  v0.7.0 не имеет — осталось на `774a0fa`. Заодно починены три дефекта
  `scripts/update_upstream.py`/`apply_patches.py`: (1) `--ref` больше не
  пытается чекаутить один тег во всех репо — чужой проект пропускается с
  пометкой `[skip]` и остаётся на пине; (2) при ошибке чекаута рабочая
  копия восстанавливается на пин и патчи пере-применяются (раньше оставалась
  голая после reset+clean); (3) оверрайды ключей приведены к ключам
  `projects` манифеста — `--gui-root` реально работает (раньше молча
  игнорировался).
- **Repo/base**: `pyproject.toml` (name `tradingagents-moex`, includes
  `moex* tinvest* integration* scripts*`), `.gitignore`. Upstream pinned:
  tradingagents `e8f5503e9b86d5c2710b0c76b1f686703968c9df` (v0.7.0),
  TradingAgents-GUI `774a0fa2aa3ee576d993ab2389e661a44ac9c37b`. Venv `.venv`
  with GUI deps + pytest. GUI vendors its own `tradingagents` copy (0.2.5+gui.2).
- **moex.http**: retry session, MoexHttpError, JSON+bytes cache
  (`~/.tradingagents/cache/moex`), `get_json/get_text/get_bytes`
  (user_agent param for media UA).
- **moex.config**: settings store `~/.tradingagents/moex/settings.json`
  (sections news/risk/sandbox/trading/tinvest; no tokens stored).
- **moex.instruments**: resolve/search/list_board/InstrumentNotFound;
  Board priority, iso currency (SUR→RUB); search exact-name ranking fixed.
- **moex.market_data**: `iss.py` (candles/quote/orderbook/trading status/
  market status, interval map), `indicators.py` (load_ohlcv cached+point-in-time,
  stockstats report matching upstream format incl. exact INDICATOR_DOCS),
  `formatters.py` (stock_data_report CSV with `# Stock data for …` header).
  Live smoke passed: SBER resolve/quote/candles/RSI/fundamentals report.
- **moex.fundamentals**: issuer.py (fundamentals_report, get_coupons via
  ISS bondization, get_dividends via T-Invest GetDividends if token else
  honest unavailable), statements.py (honest "not available" reports —
  ISS has no statements, e-disclosure anti-bot).
- **moex.news**: registry (26 sources, probe-verified 2026-10-13:
  17 available: 6×MOEX RSS cats, 5×CBR, interfax, vedomosti, investing-ru,
  tass, ria-archive, tinvest-mcp [token], rest unavailable with exact
  reasons: minfin 503, rosstat no-RSS, e-disclosure JS, rbc 401, akm 403,
  bcs challenge, smartlab timeout, finam 403), adapters/rss.py (bytes→ET,
  RFC822+ISO dates normalized to UTC), dedupe (canonical URL + normalized
  title + 0.90 similarity), linking (SECID/ISIN/short-name word boundary —
  full_name removed after false positives), aggregator (per-source isolation,
  statuses, point-in-time `until`), format (upstream-shaped
  news_report/global_news_report/statuses_report). Live smoke passed.
- **tinvest**: `client.py` (REST JSON POST, sandbox/prod bases from official
  openapi.yaml, cert bundle = certifi + russian_trusted_root_ca.pem
  [Root+Sub CA, extracted live], token only from env TINVEST_TOKEN,
  TInvestError), `models.py` (Account/Position/PortfolioState/Order mappers),
  `sandbox_accounts.py` (list/create/close/pay-in/overview, production
  mutations refused), `portfolio.py` (portfolio_context for §6.1),
  `orders.py` (post/cancel/state/list/wait, client_order_id idempotency +
  journal `~/.tradingagents/tinvest/orders_journal.jsonl`, sandbox-only),
  `operations.py` (list+summary), `reconciliation.py` (sync→state.json,
  journal-vs-broker mismatches, stale flags), `stop_orders.py`
  (StopOrdersService — verified: NO sandbox-specific stop methods),
  `mcp_adapter.py` (streamable HTTP JSON-RPC, initialize/tools-list/tools-call,
  news tools discovered dynamically by name contains 'news'; requires token).
- All modules compile + import clean (`compileall OK`).

## Verified external facts (don't re-probe)
- ISS: securities/search/candles/history/board-listing/bondization;
  NO dividends method on /iss/securities/{id}/ (returns card), NO statements,
  /iss/issuers/* 404. Empty SECID response ≠ 404.
- MOEX news RSS redirects moex.com→www.moex.com (requests follows), feed
  can be >400KB (never truncate before parse); cat=102/300 currently empty.
- TLS: tbank & rosstat chains = Russian Trusted Sub CA → Root CA; bundle
  now contains both (4513B → combined-bundle.pem built at runtime).
- Official REST methods (openapi.yaml, 72 ops): SandboxService has NO stop
  methods; StopOrdersService used for both envs; no news methods at all →
  news only via MCP. Live probe without token: HTTP 401 code=16.
- MCP endpoints: prod `https://invest-public-api.tbank.ru/mcp`, sandbox
  `.../sandbox-.../mcp`, Bearer header (401 text: "Couldn't find BearerToken
  cookie"). Tool names NOT static — Glama shows unofficial server only.

## TODO (ordered)
1. `moex/risk/` (limits check pre-order, GUI settings) + `moex/analytics/`
   (trade proposal → risk → portfolio context integration, §6.2-6.3).
2. `moex/market_data` extra smoke: orderbook/trading_status/market_status.
3. `integration/` core_adapter (route MOEX into TradingAgents dataflows:
   both standalone v0.7.0 `dataflows/vendors/router.py` AND GUI vendored
   copy `dataflows/interface.py route_to_vendor/VENDOR_METHODS`),
   gui_adapter (settings/endpoints), config_adapter, version_check.
4. `patches/` — for GUI: `gui/app.py` routes (+news/tinvest/sandbox/ports/risk),
   `index.html` nav buttons + tab panels, new JS file(s), `run_manager.py`
   params["market"], providers/env wiring; for tradingagents: vendor route
   registrations. patches/manifest.yaml + scripts/{install,uninstall,
   apply_patches,check_compatibility,update_upstream}.py.
5. `tests/` unit (moex,tinvest,news dedupe/link), integration (mocked ISS+
   T-Invest, recorded fixtures in tests/fixtures), regression (upstream GUI
   routes/settings inventory), compatibility (patch application).
6. `docs/`: UPSTREAM_MAP, ARCHITECTURE, NEWS_SOURCES (copy registry table +
   probe dates), SANDBOX, CONFIGURATION, INSTALLATION, UPDATES, SECURITY.
7. GUI news settings section: enable/disable sources, statuses endpoint
   (registry + aggregator statuses), last-success persistence in settings.
8. Acceptance §14.6 end-to-end (needs TINVEST_TOKEN for live parts — mark
   `untested` otherwise).

## Key API notes for next session
- GetDividends payload: `{figi:"", instrumentId: <isin/secid>, limit}`.
- SandboxPayIn: `{accountId, amount:{units,nano,currency:"rub"}}`.
- PostSandboxOrder: `{accountId, figi, direction: ORDER_DIRECTION_*, quantity
  (LOTS), orderType: ORDER_TYPE_*, orderId: <client id>}`; price quotation
  for limit. States: NEW/EXECUTED/PARTIALLY_EXECUTED/CANCELLED/REJECTED.
- PortfolioContext for graph: `tinvest.portfolio.portfolio_context()`.
- News item → analyst: `moex.news.format.news_report(items, ticker, s, e,
  linked=True)`.

---

# FINAL STATUS (2026-10-13) — все планы выше выполнены

## Тесты и проверки (пройдены)
- `pytest tests/ -q` → **72 passed** (unit: risk/proposal/dedupe/rss/models/
  indicators; integration: gui_routes(16)/config_flow; regression:
  original_surface; compatibility: patches).
- `scripts/check_compatibility.py` → exit 0: оба upstream `supported`,
  pinned SHA совпадают, патчи `applied`, extension importable, 26 источников.
- `scripts/apply_patches.py` → идемпотентен (`already-applied`).
- Round-trip установки: `uninstall.py` → деревья pristine → `install.py` →
  патчи reapplied, rc=0.

## Приёмочный сквозной смоук (§14.6, без токена) — PASS
1. GUI boot: `/` 200 + вкладки `tab-moex`/`tab-sandbox` + `/api/moex_ext.js` 200.
2. `enrich_run_config` (board→TQBR, tool_vendors→moex) → `set_config` →
   `route_to_vendor`: stock report, RSI, global news (10835) — все из ISS/новостей.
3. non-MOEX config байт-в-байт нетронут.
4. Live котировка SBER: bid 287.18 SUR, board TQBR, fetched_at реальный.
5. Live MOEX RSS cat=101: 50 items (реальный заголовок).
6. Без токена: preview/status дают честные ошибки, mode=`disabled`,
   configured=`False` — ничего не имитируется.

## Что дописано в этой сессии
- `moex/config.DEFAULT_SETTINGS` выровнены с DEFAULT_RISK; news/tinvest секции.
- `_STATUS_MAP` и `Order.is_terminal` — реальный REST enum
  (`EXECUTION_REPORT_STATUS_*`), legacy-алиасы сохранены.
- `moex/http.cache_invalidate` + rss refetch-once против антибот-кэша.
- `gui_routes.py` (25 роутов) + `ext.js` (вкладки UI, fetch-hook /api/analyze).
- GUI патчи: app.py (регистрация роутов после CORS), run_manager.py
  (_build_config → enrich), index.html (+2 nav, +2 panel, +ext.js);
  standalone: cli/main.py (--market/--board), cli/run.py (install+enrich).
- `sandbox_auto_confirm` (§13 исключение) реально читается только в
  order-роуте, всегда через sandbox-guard; дефолт false; тест есть.
- `board` нормализуется `.upper()` в moex_vendor_config.
- scripts: apply/install/uninstall/check_compatibility/update_upstream.
- docs/: UPSTREAM_MAP, ARCHITECTURE, NEWS_SOURCES, SANDBOX, CONFIGURATION,
  INSTALLATION, UPDATES, SECURITY + README.md.

## Известные ограничения (для §17)
- Нет TINVEST_TOKEN → все живые sandbox-операции `untested` (мок-тесты есть:
  заказ, статусы, идемпотентность, сверка, синхронизация).
- 10 из 26 новостных источников недоступны (WAF/антибот/нет RSS) —
  точные причины в docs/NEWS_SOURCES.md, в GUI отображаются как error.
- Полный LLM-граф не прогнан (нет ключа провайдера) — обходится vendor-роутом.

## Session N+1 (2026-10-13, второй заход) — закрыт §14.1/§10/§11-тесты
- **§14.1 upstream-регрессия пройдена:**
  - standalone `upstream/tradingagents/tests`: **1262 passed**, 1 skipped
    (bedrock, нет langchain_aws), 1 deselected.
  - GUI `upstream/TradingAgents-GUI/tests`: **210 passed**, 1 skipped
    (DEEPSEEK_API_KEY).
  - 2 предварительных фейла CLI-тестов объяснены и исправлены патчем
    ожиданий: `--market/--board` добавляют ключи в flags dict
    (`tests/test_cli_commands.py`, `tests/test_cli_headless.py`) —
    документированное добавление в manifest notes; поведение не менялось.
- Патч standalone перегенерирован (4 файла), manifest обновлён, round-trip
  uninstall→pristine→install повторно проверен, state=applied/идемпотентно.
- **§10 point-in-time:** `tests/news/test_point_in_time.py` — until-фильтр
  дропает будущие новости, изоляция упавшего источника, disabled/unavailable
  статусы видны (не фейковый ok), реестр=26/MOEX-категории на месте.
- **§11 уроки:** `tests/unit/test_lessons.py` — запись/загрузка/персистентность,
  enable-фильтр, delete, lessons_block (informational, capped, used_in_future_runs).
- **Тесты герметизированы:** линковка больше не ходит в живой ISS
  (записанный shape инструмента, §14.4) — suite **81 passed, ~3s**, без сети.
- Итог: **81 (наши) + 1262 (standalone) + 210 (GUI) passed**.
