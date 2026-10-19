#!/usr/bin/env bash
# One bug sweep, appended to ~/.tradingagents/autotrade_monitor.log.
# Exits with the monitor's own code (0 clean · 1 bugs · 2 monitor broke).
# Invoked by the systemd timer (deploy/systemd/) and by the fallback loop.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
home="${TRADINGAGENTS_HOME:-$HOME/.tradingagents}"
log="$home/autotrade_monitor.log"
minutes="${TRADINGAGENTS_MONITOR_MINUTES:-65}"

py="$here/../.venv/bin/python"
[ -x "$py" ] || py="$(command -v python3)"

mkdir -p "$home"
{
  echo "========================================================================"
  echo "sweep $(date -Is) window=${minutes}m"
} >>"$log"

"$py" "$here/monitor_autotrade.py" --minutes "$minutes" >>"$log" 2>&1
rc=$?
echo "[sweep] done rc=$rc $(date -Is)" >>"$log"
exit "$rc"
