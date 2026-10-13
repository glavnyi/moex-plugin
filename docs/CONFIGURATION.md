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
  "tinvest":{ "mode": "sandbox", "active_account": null, "sync_seconds": 60 }
}
```

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
