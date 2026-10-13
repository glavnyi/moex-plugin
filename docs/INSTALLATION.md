# INSTALLATION

## Требования

- Python 3.10+ (проверено на 3.14), git, сетевой доступ к GitHub/MOEX/T-Invest
- Окружение, где уже установлен GUI-upstream (Flask, его зависимости)

## Установка на клонированный репозиторий (как здесь)

```bash
python scripts/install.py                 # всё сразу
# или по шагам:
python scripts/apply_patches.py           # патчи + бэкапы в .backups/<ts>/
pip install -e .                          # пакет moex/tinvest/integration
python scripts/check_compatibility.py     # SHA + патчи + импорты
```

`install.py` последовательно:

1. сверяет HEAD обоих upstream с `patches/manifest.yaml`;
2. применяет патчи (идемпотентно; уже применённые — skip);
3. ставит расширение (`pip install -e`);
4. проверяет импорты и функциональный срез
   (fragment конфига, risk, реестр новостей);
5. печатает команду запуска и инструкцию отката.

## Установка на сторонние чекауты

```bash
python scripts/install.py \
  --tradingagents-root /path/to/tradingagents \
  --gui-root /path/to/TradingAgents-GUI \
  --venv /path/to/venv/bin/python
```

Патчи применяются только при совпадении pinned SHA (иначе warning и
отказ без `--force`).

## Запуск

```bash
export TINVEST_TOKEN=...          # опционально, для песочницы
python upstream/TradingAgents-GUI/gui/app.py
# или: cd upstream/TradingAgents-GUI && python -m gui.app
```

В браузере: прежние вкладки работают как раньше; новые — **MOEX** и
**Песочница**; в Analyze появился переключатель Market (US / MOEX).

CLI standalone: `tradingagents --market MOEX --ticker SBER ...`.

## Проверка установки

```bash
python scripts/check_compatibility.py   # exit 0 = supported
pytest tests/ -q                        # 71 passed
```

## Удаление

Смотрите [UPDATES.md](UPDATES.md#откат).
