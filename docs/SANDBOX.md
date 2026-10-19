# SANDBOX — T-Invest песочница

## Подключение

```bash
export TINVEST_TOKEN=...        # токен только из окружения
# необязательно: export TRADINGAGENTS_TINVEST_TOKEN=... (альтернативное имя)
```

- Base URL песочницы: `https://sandbox-invest-public-api.tinkoff.ru/rest/`,
  боевой: `https://invest-public-api.tinkoff.ru/rest/`. Какой применяется —
  целиком определяется режимом §5.1.
- `tinvest.mode` в settings и во вкладке **Авто**: пять режимов §5.1.

  | Режим | Площадка | Заявки |
  |---|---|---|
  | `disabled` | — (клиент не создаётся) | — |
  | `readonly` | боевая | запрещены |
  | `sandbox` | песочница | разрешены |
  | `live_readonly` | боевая | запрещены |
  | `live_trading` | боевая | разрешены, настоящие деньги |

  Переключение на `live_trading` принимается только вместе с
  `confirm_live: true` (маршрут `POST /api/tinvest/mode`), иначе 409.
- Проверка: вкладка **Авто** (сверху) или `GET /api/tinvest/status` —
  в ответе `mode`, `modes`, `environment`, `can_read`, `can_mutate`,
  `production_trading`.

## Возможности (подтверждены по openapi v1.4, 72 операции)

| Действие | Метод | GUI |
|---|---|---|
| Список виртуальных счетов | SandboxService/GetSandboxAccounts | ✓ |
| Создать счёт | SandboxService/OpenSandboxAccount | ✓ |
| Закрыть счёт | SandboxService/CloseSandboxAccount | ✓ |
| Пополнить | SandboxService/SandboxPayIn | ✓ (явное подтверждение) |
| Баланс/портфель | GetSandboxPortfolio / GetSandboxPositions | ✓ |
| Заявки | PostSandboxOrder / GetSandboxOrders / GetSandboxOrderState / CancelSandboxOrder | ✓ |
| Операции | GetSandboxOperations | ✓ |
| Стоп-заявки | StopOrdersService (production-only, sandbox-стопов API не даёт) | честно: нет |

Статусы заявок — enum `OrderExecutionReportStatus` (REST JSON):
`EXECUTION_REPORT_STATUS_NEW|FILL|PARTIALLYFILL|CANCELLED|REJECTED` →
GUI-состояния `accepted|filled|partial|cancelled|rejected|sent|unknown`
(`moex/analytics/workflow.map_order_state`).

Сервис выбирается по режиму, а не захардкожен: песочница —
`SandboxService/PostSandboxOrder/...`, боевые режимы —
`OrdersService/PostOrder/...`. Мутация в режимах `readonly` и
`live_readonly` блокируется до отправки (`TInvestClient.require_mutation`).

## Авторежим (§6.2–6.3, вкладка «Авто»)

По расписанию: гейты → анализ (тот же `RunManager`) → `extract_proposal` →
риск на свежем состоянии → заявка → журнал → синк/уроки.

- Настройки — секция `autotrade` (`enabled`, `environment`, `watchlist`,
  `tick_seconds`, `max_orders_per_day`, `max_open_positions`,
  `skip_if_holding`, `only_when_market_open`, `live_confirmed`,
  `kill_switch`, `analysts`, `research_depth`, `language`,
  `run_timeout_seconds`).
- **Выключен по умолчанию во всех окружениях** и не включается сам.
- Для `environment: live` нужны три отдельных разрешения:
  `tinvest.mode=live_trading` **и** `autotrade.live_confirmed`
  **и** снятый `risk.automation_sandbox_only`.
- Kill switch (`autotrade.kill_switch`) останавливает циклы, не меняя
  `enabled`; снятие записывается в журнал отдельной строкой.
- Лимиты: не более `max_orders_per_day` заявок и не более
  `max_open_positions` позиций на московские сутки; одно решение на тикер
  в день (дедуп по журналу, `environment` учитывается).
- Ручной запуск (`POST /api/autotrade/run`) обходит **только** гейт
  `enabled` (`{"force": true}`); остальные действуют.
- Журнал: `~/.tradingagents/tinvest/autotrade_journal.jsonl`.
- В тестах поток глушится `TRADINGAGENTS_AUTOTRADE=0`.

### Мониторинг багов (почасовой)

Таймер `tradingagents-monitor.timer` (unit-файлы в `deploy/systemd/`)
раз в час запускает `scripts/monitor_autotrade_run.sh`, который сводит
в один отчёт:

- статус движка (`/api/autotrade`) и health-check (`scripts/health_check.py`);
- журнал авторежима за окно (`--minutes`, по умолчанию 65);
- строки `journalctl --user -u tradingagents-gui` с маркерами ошибок;
- битые запуски из `~/.tradingagents/runs.json` (ошибочные, или «running»
  дольше `TRADINGAGENTS_MONITOR_STALE_RUN_S`, по умолчанию 1 ч).

Классификация: **BUG** (нужно чинить), **WARN** (восстановилось само:
`retrying in`, обработанные 4xx-источники), **норма** (движок занят
`analysis_idle`/`analysis_running`, рынок закрыт). Возврат: `0` чисто,
`1` найдены баги, `2` сломался сам монитор. Отчёт пишется в
`~/.tradingagents/autotrade_monitor.log`; вручную —
`python scripts/monitor_autotrade.py --minutes 65 [--json]`.

Без systemd: `nohup setsid bash scripts/monitor_autotrade_loop.sh >/dev/null 2>&1 &`
(интервал `TRADINGAGENTS_MONITOR_INTERVAL`, по умолчанию 3600 с).

Известные внешние помехи, которые монитор относит к WARN, а не к багам:
таймауты провайдера LLM (`OpenRouter`/nemotron, `504 ... — retrying`),
блокировки источников новостей (Reddit/StockTwits 403), сетевые сбои
до `*.tinkoff.ru` и `www.cbr.ru` (обычно WARP/канал; проверяется
`curl --interface wlp2s0`).

## Идемпотентность (§5.6)

- Каждая заявка получает `client_order_id = "ta-" + preview_id`;
  повтор с тем же id после тайм-аута не создаёт вторую заявку.
- Журнал: `~/.tradingagents/tinvest/orders_journal.jsonl`
  (attempt/result/error/cancel; без секретов).
- При неопределённом результате — сначала статус (`GetSandboxOrders`/`GetOrderState`),
  потом повтор; новый id никогда не генерируется автоматически.

## Синхронизация (§5.7)

`POST /api/tinvest/sync` → снимок в `~/.tradingagents/tinvest/state.json`:
баланс, позиции, открытые заявки, история операций, время получения,
`stale`-флаги при ошибке. Ручной запуск из GUI; авто-период —
`tinvest.sync_seconds` (по умолчанию 60).

## Где живут данные

| Что | Путь |
|---|---|
| Настройки (лимиты, новости, счёт) | `~/.tradingagents/moex/settings.json` |
| Журнал заявок | `~/.tradingagents/tinvest/orders_journal.jsonl` |
| Журнал авторежима | `~/.tradingagents/tinvest/autotrade_journal.jsonl` |
| Снимок состояния | `~/.tradingagents/tinvest/state.json` |
| Выводы по сделкам (§11) | `~/.tradingagents/tinvest/lessons.jsonl` |
| Отчёты почасового монитора | `~/.tradingagents/autotrade_monitor.log` |
| Кэш MOEX ISS/новостей | `~/.tradingagents/cache/` |

## Ограничения (честно)

- Без `TINVEST_TOKEN` все маршруты заявок возвращают 400 с текстом про токен —
  ничего не имитируется.
- Ручные заявки: превью → риск → подтверждение пользователя.
  Подтверждение может быть снято **только** `trading.sandbox_auto_confirm`
  и **только** при `tinvest.mode=sandbox`; на боевом счёте этот ключ не
  читается. Авторежим подтверждается цепочкой гейтов, а не этим ключом.
- Программный стоп-лосс не реализован как «гарантированный выход»;
  native стоп-заявки в песочнице API не предоставляет (в боевых режимах —
  `StopOrdersService`).
- Populate/закрытие счёта показывают фактический ответ API; при отказе — ошибка.
