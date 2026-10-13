# TradingAgents-MOEX

Полноценное расширение [TradingAgents](https://github.com/tauricresearch/tradingagents) v0.6.0 и
[TradingAgents-GUI](https://github.com/TheLocalLab/TradingAgents-GUI) для московской биржи:
анализ российских бумаг, интегрированные новости и полный цикл работы с виртуальным счётом
T-Invest (песочница). **Реальная торговля в этой версии отключена.**

## Что добавлено (без потери оригинала)

| Область | Статус |
|---|---|
| Выбор рынка MOEX в GUI и CLI (`--market MOEX`) | да |
| Рыночные данные MOEX ISS: свечи, котировки, стакан, индикаторы | да |
| Фундаментал MOEX + купоны + дивиденды (T-Invest, при токене) | да |
| 26 новостных источников с дедупликацией и линковкой к тикерам | да, из них 16 проверены доступными (см. docs/NEWS_SOURCES.md) |
| T-Invest Sandbox: счета, баланс, позиции, заявки, отмена, операции, синхронизация | да (нужен `TINVEST_TOKEN`) |
| Портфельный контекст в аналитическом графе (§6.1) | да |
| Лимиты риска перед каждой заявкой + повторная проверка на свежем состоянии | да |
| Выводы по закрытым сделкам (§11) | да |
| Вкладки GUI: MOEX и Песочница | да, оригинальные вкладки не тронуты |
| FAST/SMART, провайдеры, отчёты, чат, бэктесты, чекпоинты upstream | сохранены без изменений |

## Установка

```bash
python scripts/install.py            # патчи + пакет + проверки
python upstream/TradingAgents-GUI/gui/app.py   # запуск GUI
```

Откат: `python scripts/uninstall.py` (исходники upstream восстанавливаются,
`~/.tradingagents/` с настройками/отчётами не трогается).

Подробности: [docs/INSTALLATION.md](docs/INSTALLATION.md),
[docs/UPDATES.md](docs/UPDATES.md), [docs/SECURITY.md](docs/SECURITY.md).

## Быстрый старт с песочницей

```bash
export TINVEST_TOKEN=...      # только окружение; в файлы и Git не пишется
python upstream/TradingAgents-GUI/gui/app.py
```

Вкладка **Песочница** → «Создать счёт» → «Пополнить» → вкладка **MOEX** (поиск бумаги) →
вкладка **Анализ** (Market = MOEX, тикер) → после отчёта подтверждение заявки вручную.

## Структура

- `moex/` — инструменты, рыночные данные, фундаментал, новости, риск, аналитика
- `tinvest/` — клиент T-Invest API, счета, портфель, заявки, операции, сверка, MCP
- `integration/` — мост к TradingAgents/GUI (регистрация MOEX-вендоров, маршруты, JS)
- `patches/` — воспроизводимые патчи обоих upstream + `manifest.yaml`
- `scripts/` — install / uninstall / apply_patches / check_compatibility / update_upstream
- `tests/` — unit, integration, regression, compatibility (71 тест)

## Проверка

```bash
python scripts/check_compatibility.py   # SHA, патчи, импорты
pytest tests/ -q                        # 71 passed
```
