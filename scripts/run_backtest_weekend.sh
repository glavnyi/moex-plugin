#!/usr/bin/env bash
# Выходной прогон бэктеста bt_3y (только сб/вс по таймеру
# tradingagents-bt-weekend): в торговые дни дневная квота LLM целиком
# уходит автоторговле — см. PROGRESS, политика 2026-10-19.
#
# flock -n не даёт второму запуску (воскресному, если субботний ещё
# идёт) стартовать параллельно с тем же --run-id; проигравший молча
# выходит. Resume идёт по журналу run-а: несобранные ячейки (включая
# FAIL после 429) запускаются заново, добитые — пропускаются.
set -u
LOCK="$HOME/.tradingagents/backtest/bt_3y/run.lock"
LOG=/tmp/backtest_bt_3y.log

cd "/home/boris/TRADINGAGENTS MOEX" || exit 1
set -a; . /home/boris/.config/tradingagents.env; set +a

exec flock -n "$LOCK" .venv/bin/python scripts/backtest_moex.py \
    --tickers SBER,GAZP,LKOH --start 2023-10-18 --end 2026-10-17 \
    --every 30 --run-id bt_3y >> "$LOG" 2>&1
