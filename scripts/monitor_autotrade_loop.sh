#!/usr/bin/env bash
# Hourly bug sweep without systemd (fallback). Preferred setup is the timer
# in deploy/systemd/tradingagents-monitor.timer — it survives reboots and
# reports missed runs via Persistent=true.
#
#   nohup setsid bash scripts/monitor_autotrade_loop.sh >/dev/null 2>&1 &
set -u

here="$(cd "$(dirname "$0")" && pwd)"
interval="${TRADINGAGENTS_MONITOR_INTERVAL:-3600}"

while true; do
  bash "$here/monitor_autotrade_run.sh" >/dev/null 2>&1 || true
  sleep "$interval"
done
