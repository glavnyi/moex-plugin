# CONFIGURATION

## Файлы

| Файл | Назначение | Секреты |
|---|---|---|
| `~/.tradingagents/moex/settings.json` | настройки расширения | никогда |
| GUI `.env` (upstream, `gui/.env`) | ключи LLM-провайдеров (upstream-механизм, сохранён) | да, как и раньше |
| окружение `TINVEST_TOKEN` | токен T-Invest | единственный способ |

Старые конфигурации GUI открываются без изменений: патчи только добавляют
маршруты/вкладки; `ui_state.json` и `.env` не модифицируются.

## Секции settings.json

```jsonc
{
  "news":   { "enabled": true, "sync_seconds": 1800, "sources": {"tass": {"enabled": false}} },
  "risk":   { "max_order_amount": 500000, "max_position_pct": 30,
              "max_issuer_pct": 30, "max_open_orders": 5,
              "allowed_instruments": [], "allow_new_buys": true,
              "allow_selling_positions": true, "max_risk_per_trade_pct": 5,
              "daily_loss_limit": null, "require_confirmation": true,
              "automation_sandbox_only": true },
  "sandbox":{ "auto_sync_seconds": 60, "mark": "SANDBOX" },
  "trading":{ "require_order_confirmation": true, "sandbox_auto_confirm": false,
              "use_learned_insights": true },
  "tinvest":{ "mode": "sandbox", "active_account": null, "sync_seconds": 60 },
  "autotrade":{ "enabled": false, "environment": "sandbox",
                "live_confirmed": false, "kill_switch": false,
                "tick_seconds": 300, "watchlist": ["SBER"],
                "max_orders_per_day": 3, "max_open_positions": 3,
                "skip_if_holding": true, "only_when_market_open": true,
                "analysts": ["market","social","news","fundamentals"],
                "research_depth": 3, "language": "English",
                "run_timeout_seconds": 2700 },
  "screener":{ "enabled": false, "board": "TQBR",
               "min_turnover_rub": 1000000, "max_spread_pct": 1.5,
               "min_price": 1.0, "list_levels": [1, 2],
               "max_candidates_per_day": 3, "exclude": [] }
}
```

### `autotrade.run_timeout_seconds` и бюджет ретраев LLM

Анализ не укладывается в бюджет, если **суммарный** бюджет ретраев одного
тяжёлого вызова превышает бюджет самого прогона. Два независимых параметра:

| Переменная окружения | Default | Значение |
|---|---|---|
| `TA_LLM_TIMEOUT` | 300 | таймаут одного вызова LLM |
| `TA_LLM_MAX_RETRIES` | 5 | число SDK-ретраев (6 попыток) |

Худший случай на один вызов ≈ `(MAX_RETRIES + 1) * TIMEOUT` + backoff.
При дефолтах это `6 * 300 ≈ 1850 с` — больше, чем весь бюджет
`run_timeout_seconds`, поэтому один засветившийся `504` от провайдера
валит весь прогон (в журнале это цепочка `attempt 1/6 … 5/6` с интервалом
≈ 5 минут).

Замеренные задержки `openrouter/free`: 2-5 с на короткие промпты,
23-42 с на промпт полного отчёта. Поэтому в системе принят
`TA_LLM_TIMEOUT=120` (≈3× худшего здорового вызова) и
`TA_LLM_MAX_RETRIES=2` (3 попытки → худший случай ≈ 370 с),
а `run_timeout_seconds` поднят до 2700 с (полный прогон 1050-1250 с +
запас на пару упавших вызовов).

Задаются в `~/.config/systemd/user/tradingagents-gui.service.d/override.conf`
(`Environment=...`) и применяются после
`systemctl --user daemon-reload && systemctl --user restart tradingagents-gui`.

### `tinvest.mode` — режим подключения (§5.1)

`disabled | readonly | sandbox | live_readonly | live_trading`.
Меняется во вкладке **Авто** или через `POST /api/tinvest/mode`;
значение `live_trading` принимается только с `confirm_live: true`.
Режим определяет и площадку (`sandbox-invest-public-api` /
`invest-public-api`), и право на запись.

### `autotrade` — авторежим

Выключен по умолчанию; переключатели `enabled`, `environment`,
`live_confirmed`, `kill_switch` **не выводятся друг из друга** — включить
авторежим можно, ничего не меняя в рисках и режиме, и он всё равно
остановится на гейте `environment`. Полный список гейтов и семантика
констант — `moex/autotrade.py::evaluate_gates` (покрыто тестами
`tests/unit/test_modes_and_autotrade.py`).

### `screener` — автоматический отбор тикеров

Когда `screener.enabled=true`, ручной ввод тикеров не требуется: каждый
цикл скринер сканирует **весь** борд (одна страница ISS, TTL-кэш 120 с),
отсеивает неподходящее и отдаёт в TradingAgents не больше
`max_candidates_per_day` кандидатов в день (бюджет LLM считается по
журналу и переживает перезапуск). Ручной `watchlist` обрабатывается
**первым** — скринер добавляет кандидатов только после него.

Фильтры (`moex/screener.py`, каждый отказ журналируется с именем
причины): `not_trading` (TRADINGSTATUS ≠ T), `no_price`, `cheap`
(цена < `min_price`), `illiquid` (оборот дня < `min_turnover_rub`),
`wide_spread` ((ASK−BID)/LAST > `max_spread_pct`), `low_list` (уровень
листинга вне `list_levels`), `excluded` (стоп-лист). Тикер с решением
сегодня или открытой позицией — не отказ, а очередь (`deferred`).
Ранжирование: оборот ↓, затем спред ↑. Просмотр отбора без LLM и
заявок — `GET /api/autotrade/screener` и кнопка в вкладке «Авто».

Дневной лимит заявок (`max_orders_per_day`) — что считается заявкой
(цель §3), правило зафиксировано в `moex/autotrade.py`:

* **расходуют лимит**: `kind=open` (новая позиция), `kind=increase`
  (добор), `kind=retry` (повторная отправка после сетевого сбоя) —
  одна экономическая заявка;
* **не расходуют**: `kind=close` (закрытие), `kind=protective`
  (стоп-лосс/тейк-профит) — открытая позиция никогда не остаётся без
  защиты из-за исчерпанного бюджета;
* **не считаются заявкой вообще**: строка журнала `order` с
  `placed=false` (отказ риска, сетевой сбой) — анализ тикера бюджет
  не тратит.

Дубли исключены дважды: до отправки проверяются активные заявки
брокера по `client_order_id` из журнала (§4), а после сетевого сбоя
маркет-заявка не переотправляется вовсе (исход неизвестен —
fail-closed), лимитная — только после повторной проверки статуса у
брокера (`integration/autotrade_service.py::_send_with_recovery`).

`sandbox_auto_confirm` живёт в секции `trading` и применяется **только**
при `tinvest.mode=sandbox`; на боевом счёте ключ не читается.

Ключи `risk` обязаны совпадать с `moex.risk.DEFAULT_RISK` —
`effective_limits()` принимает только их (покрыто тестом
`test_settings_defaults_use_effective_key_names`).

Переопределение каталога данных: `TRADINGAGENTS_HOME=/path`
(используется тестами для изоляции).

## Конфиг запуска (MOEX)

`enrich_run_config(params, config)` добавляет к оригинальному конфигу:

```jsonc
{
  "market": "MOEX", "ticker": "SBER", "board": "TQBR",
  "tool_vendors": { "get_stock_data": "moex", "...9 методов": "moex" },
  "data_vendors": { "core_stock_apis": "moex", "technical_indicators": "moex",
                    "fundamental_data": "moex", "news_data": "moex" }
}
```

Для `market != MOEX` функция возвращает входной конфиг без изменений
(регрессия: `test_non_moex_config_is_byte_identical`).

## FAST / SMART

Сохранена оригинальная двухуровневая система TradingAgents
(`quick_think_llm` / `deep_think_llm`, provider, endpoint, key, env-переменные).
Категории FAST/SMART не привязаны к вендору: это настройки GUI/CLI upstream,
расширение их не заменяет. Токены провайдеров — по-прежнему через
upstream `.env`/`TRADINGAGENTS_*` env.
