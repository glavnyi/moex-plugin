# PROGRESS (internal state log — TradingAgents-MOEX)

Updated: 2026-10-13 (session 3). Nothing here is user documentation; see `docs/`.

## Done (live-verified)
- **Repo/base**: `pyproject.toml` (name `tradingagents-moex`, includes
  `moex* tinvest* integration* scripts*`), `.gitignore`. Upstream pinned:
  tradingagents `1394a3f72aa4393e1a98f51b382434c4b4c2d972` (v0.6.0),
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
   both standalone v0.6.0 `dataflows/vendors/router.py` AND GUI vendored
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
