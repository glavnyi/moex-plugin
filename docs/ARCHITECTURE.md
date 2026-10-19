# ARCHITECTURE

```
TradingAgents-MOEX/
├── moex/                 # всё, что касается российского рынка
│   ├── http.py           # retry-сессия, кэш JSON/bytes, российские CA-фиксы
│   ├── config.py         # ~/.tradingagents/moex/settings.json (без секретов)
│   ├── instruments/      # resolve/search/list_board, ISIN, лоты, статусы
│   ├── market_data/      # ISS: свечи, котировки, стакан; индикаторы; форматтеры
│   ├── fundamentals/     # эмитент, отчётность, купоны, дивиденды
│   ├── news/             # registry(26) → adapters → dedupe → linking → aggregator
│   ├── risk/             # DEFAULT_RISK, check_proposal, size_position, effective_limits
│   ├── analytics/        # proposal из отчёта, workflow: context→preview→place→track→lessons
│   └── autotrade.py      # гейты авторежима + журнал: «можно ли запускать цикл»
├── tinvest/              # T-Invest API, пять режимов §5.1
│   ├── client.py         # REST JSON, режим→площадка, require_mutation, токен только env
│   ├── models.py         # Account/Money/Position/PortfolioState/Order (enum §5.5)
│   ├── sandbox_accounts.py, portfolio.py, orders.py, operations.py
│   ├── reconciliation.py # снимок состояния + журнал-vs-брокер, stale-флаги
│   ├── stop_orders.py    # native stop orders API существует только в production
│   ├── mcp_adapter.py    # T-Invest MCP: tools/list+call, новостные инструменты
│   └── certs/            # certifi + Russian Trusted Root(+Sub) CA
├── integration/          # мост к обоим upstream
│   ├── gui_adapter.py    # MOEX_METHODS, register_moex_vendors, moex_vendor_config
│   ├── core_adapter.py   # то же для standalone dataflows.router
│   ├── config_adapter.py # enrich_run_config: только для market=MOEX
│   ├── gui_routes.py     # 34 REST-маршрута GUI (ленивые импорты, без секретов)
│   ├── autotrade_service.py # цикл авторежима: гейты→анализ→риск→заявка→журнал
│   ├── ext.js            # UI новых вкладок + инъекция market в /api/analyze
│   └── version_check.py  # SHA-контроль upstream
├── patches/ + scripts/   # патчи, manifest, install/uninstall/compat/update
└── tests/                # unit, integration, regression, compatibility
```

## Поток заявки (§5.5–5.7)

```
preview  POST /api/tinvest/order/preview
   ├─ resolve_instrument + get_quote (свежая котировка, время)
   ├─ portfolio_context(client, account)      # баланс+позиции из API
   ├─ prepare_order(...)                      # превью + check_proposal(limits)
   └─ client_order_id = "ta-" + preview_id    # идемпотентность
confirm  POST /api/tinvest/order {preview, confirm:true}
   ├─ place_confirmed_order:
   │    preview.environment == client.environment
   │    → открытые заявки берутся от вызывающего (или перечитаются)
   │    → свежий check_proposal(limits, open_order_count) → post_order
   │    (тот же client_order_id → брокер не дубликует)
   └─ state = map_order_state(REST enum)      # sent/accepted/partial/filled/...
track    GET  /api/tinvest/order_state        # polled в GUI
cancel   POST /api/tinvest/order/cancel
sync     POST /api/tinvest/sync               # снимок + сверка журнала
```

Отклонения до отправки: подтверждение не передано (409), несовпадение
окружения превью и клиента, режим только для чтения, нарушение риска,
недостаток средств, лимит заявок, шорт-продажа без позиции, **неизвестная
сумма заявки** (без цены ни один денежный лимит §6.3 не проверить — заявка
отклоняется, а не проходит молча), непрочитанные открытые заявки.

Сумма считается `лоты × цена × lot_size` (`lot_size` берётся из карточки
MOEX-инструмента: облигации торгуются пачками по 100), цена — из лимитной
цены, затем из котировки (`last_price`, подставляют GUI и цикл авторежима),
затем из текущей цены уже имеющейся позиции.

## Соглашения

- Секреты: только env `TINVEST_TOKEN` / `TRADINGAGENTS_TINVEST_TOKEN`;
  settings.json, Git, ответы API и журналы — без токенов.
- Обращения к брокеру идут в площадку режима §5.1 (`client.sandbox`);
  `readonly`/`live_readonly` отказывают в мутациях через `require_mutation`.
  Выбор `live_trading` требует `confirm_live: true`.
- Сетевые сбои источников новостей изолированы per-source (§7.5).
- Секции GUI расширяются, не заменяя: патчи = +3 кнопки навигации (MOEX,
  Песочница, Авто), +3 панели, +1 строка в карточке Analyze, +1 `<script>`.
- **Единый `RunManager`.** GUI стартует как `python -m gui.app`, то есть
  модуль исполняется под именем `__main__`; любой `from gui.app import runs`
  создаёт *второй* модуль (второй Flask app и второй `RunManager`), и
  авторежим начинает крутить граф, который HTTP API не видит, а два
  менеджера дерутся за один `runs.json`. Из extension-кода объект берётся
  только через `integration.autotrade_service._gui_runs()`, который
  резолвит модуль, владеющий живым app (`__main__` → `gui.app`).
- **CA-бандл T-Invest.** `tinvest.client.cert_bundle()` собирает
  certifi + российские корни; перезапись только при изменении входа и
  только атомарно (`os.replace`) — конкурентное чтение пополам давало
  `SSLError [X509] PEM lib`.
- **Alpha по российским бумагам.** Upstream settlement берёт бенчмарк из
  `benchmark_map` (без суффикса → SPY) и котировки из yfinance, который по
  русским символам не отдаёт ни одного бара: запись MOEX навсегда оставалась
  `pending`, а `get_past_context` (фильтр по `pending`) отдавал Portfolio
  Manager пустую строку уроков. `integration.moex_settlement` оборачивает
  `_resolve_benchmark` / `_fetch_returns` и при `market == "MOEX"` считает
  доходность по MOEX ISS против `evaluation.benchmark` (IMOEX); другим
  рынкам делегирует оригиналу.
- **Уроки §11 в промпте.** `moex.analytics.lessons_block()` не имел ни одного
  production-вызова: закрытые сделки писались в `lessons.jsonl`, но в промпты
  не попадали. Блок вставляется первым в `moex_data_snapshot`
  (`integration.config_adapter`) — до собственной плашки снимка «every figure
  below comes from MOEX ISS», чтобы уроки не читались как данные ISS.
