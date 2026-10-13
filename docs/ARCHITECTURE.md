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
│   └── analytics/        # proposal из отчёта, workflow: context→preview→place→track→lessons
├── tinvest/              # T-Invest API (всегда sandbox=True)
│   ├── client.py         # REST JSON, SANDBOX/PROD base, cert bundle, токен только env
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
│   ├── gui_routes.py     # 25 REST-маршрутов GUI (ленивые импорты, без секретов)
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
   │    environment must be SANDBOX → свежий check_proposal → post_order
   │    (тот же client_order_id → брокер не дублирует)
   └─ state = map_order_state(REST enum)      # sent/accepted/partial/filled/...
track    GET  /api/tinvest/order_state        # polled в GUI
cancel   POST /api/tinvest/order/cancel
sync     POST /api/tinvest/sync               # снимок + сверка журнала
```

Отклонения до отправки: подтверждение не передано (409), не-песочница,
нарушение риска, недостаток средств, лимит заявок, шорт-продажа без позиции.

## Соглашения

- Секреты: только env `TINVEST_TOKEN` / `TRADINGAGENTS_TINVEST_TOKEN`;
  settings.json, Git, ответы API и журналы — без токенов.
- Все обращения к брокеру: `sandbox=True`; `tinvest.mode=live_trading`
  отклоняется и отображается как `disabled`.
- Сетевые сбои источников новостей изолированы per-source (§7.5).
- Секции GUI расширяются, не заменяя: патчи = +2 кнопки навигации, +2 панели,
  +1 строка в карточке Analyze, +1 `<script>`.
