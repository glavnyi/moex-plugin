# UPSTREAM_MAP

## Закреплённые версии (pinned)

| Проект | Commit | Версия | Статус |
|---|---|---|---|
| tauricresearch/tradingagents | `e8f5503e9b86d5c2710b0c76b1f686703968c9df` | v0.7.0 | supported |
| TheLocalLab/TradingAgents-GUI | `774a0fa2aa3ee576d993ab2389e661a44ac9c37b` | — | supported |

Источник истины: `patches/manifest.yaml` + `integration/version_check.py::PINNED`.
Любой другой SHA → `untested` (скрипты предупреждают, `--force` для попытки).

## Какой «tradingagents» где лежит

- **GUI вендорит собственную копию** `upstream/TradingAgents-GUI/tradingagents`
  (editable-install ставит именно её — `import tradingagents` указывает туда).
  Точки интеграции: `dataflows/interface.py` (`VENDOR_METHODS`, `route_to_vendor`,
  `TOOLS_CATEGORIES`), `dataflows/config.py` (`set_config`/`get_config`).
- **Standalone v0.7.0**: `upstream/tradingagents/tradingagents`, тот же механизм в
  `dataflows/router.py` + `dataflows/config.py`.
- Копии различаются (GUI-версия 0.2.5+gui.2 против v0.7.0), поэтому адаптеры
  динамически определяют, какая копия импортирована.

## Точки интеграции

| Что | GUI-копия | Standalone |
|---|---|---|
| Регистрация MOEX-вендоров | `integration/gui_adapter.register_moex_vendors()` → `interface.VENDOR_METHODS[m]["moex"]` | `integration/core_adapter.install()` → `router.VENDOR_METHODS` |
| Передача конфига графу | `TradingAgentsGraph(config=...)` → `set_config` (trading_graph.py:73) | то же |
| Обогащение конфига | патч `gui/run_manager._build_config` → `enrich_run_config` | патч `cli/run.run_analysis` → `enrich_run_config` |
| Маршруты REST/JS | патч `gui/app.py` → `integration.gui_routes.register_routes(app)` | — |
| Новые вкладки GUI | патч `gui/templates/index.html` + `/api/moex_ext.js` (= `integration/ext.js`) | — |
| CLI-флаги | — | патч `cli/main.py` (`--market`, `--board`) |

## Как работают вендоры (сохранено)

`route_to_vendor(method, *args)` читает `config["tool_vendors"][method]`
(fallback: `config["data_vendors"][category]`), ищет реализацию в
`VENDOR_METHODS[method][vendor]`. Мы добавляем **только** запись `"moex"` —
`yfinance`/`alpha_vantage` остаются на месте; для не-MOEX запусков ветка
`market != MOEX` не выполняется вообще, конфиг возвращается идентичным.

Ошибки MOEX-вендора не «улетают» в yfinance: выбор вендора детерминирован
по конфигу; отсутствие MOEX-ключа в конфиге — ValueError с внятным текстом.

## Порядок данных для MOEX-рана

1. `params.market == "MOEX"` → `enrich_run_config` кладёт `tool_vendors` (9 методов),
   `data_vendors` (4 категории), `market/ticker/board`.
2. `TradingAgentsGraph` вызывает `set_config(config)`.
3. Инструменты зовут `route_to_vendor(...)` → `moex_*` impl → `moex/*` модули.
4. Все ответы содержат источник и время (`fetched_at`, `source: moex-iss`).
