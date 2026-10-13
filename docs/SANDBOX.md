# SANDBOX — T-Invest песочница

## Подключение

```bash
export TINVEST_TOKEN=...        # токен только из окружения
# необязательно: export TRADINGAGENTS_TINVEST_TOKEN=... (альтернативное имя)
```

- Base URL песочницы: `https://sandbox-invest-public-api.tinkoff.ru/rest/`
  (production base в коде есть, но мутации из GUI/CLI в production невозможны).
- `tinvest.mode` в settings: `disabled | readonly | sandbox | live_readonly`.
  Значение `live_trading` **отклоняется** и отображается как `disabled` (§6.5).
- Проверка: вкладка Песочница или `GET /api/tinvest/status`.

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
| Снимок состояния | `~/.tradingagents/tinvest/state.json` |
| Выводы по сделкам (§11) | `~/.tradingagents/tinvest/lessons.jsonl` |
| Кэш MOEX ISS/новостей | `~/.tradingagents/cache/` |

## Ограничения (честно)

- Без `TINVEST_TOKEN` все маршруты заявок возвращают 400 с текстом про токен —
  ничего не имитируется.
- Автоматических исполнений нет: превью → риск → подтверждение пользователя.
- Программный стоп-лосс не реализован как «гарантированный выход»;
  native стоп-заявки в песочнице API не предоставляет.
- Populate/закрытие счёта показывают фактический ответ API; при отказе — ошибка.
