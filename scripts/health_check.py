#!/usr/bin/env python3
"""One-shot health check for the TradingAgents GUI + T-Invest sandbox.

Prints a compact report; exit code 1 if any hard check fails.
Run inside the hourly auto-trading sweep (scripts/monitor_autotrade_run.sh)
and safe to invoke by hand at any time.
"""
import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5000"
failures = []


def sh(cmd: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"ERR: {exc}"


def api(path: str, timeout: int = 20):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            return json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}


# 1. service active
state = sh("systemctl --user is-active tradingagents-gui")
ok_svc = state == "active"
print(f"[{'OK' if ok_svc else 'FAIL'}] service: {state}")
if not ok_svc:
    failures.append("service down")

# 2. HTTP alive
st = api("/api/tinvest/status")
if st.get("_error"):
    print(f"[FAIL] http: {st['_error']}")
    failures.append("http down")
else:
    a = st.get("auto_sync") or {}
    print(f"[OK] http: mode={st.get('mode')} auto_sync "
          f"last={a.get('last_sync')} err={'none' if not a.get('last_error') else a['last_error'][:120]}")
    if a.get("last_error"):
        failures.append(f"auto_sync error: {str(a['last_error'])[:100]}")

# 3. analysis status
run = api("/api/status")
if not run.get("_error"):
    print(f"[OK] analysis: running={run.get('running')}")
else:
    print(f"[FAIL] analysis status: {run['_error']}")
    failures.append("analysis status unreachable")

# 4. journal problems since 16 min ago
# Match failure markers, not every line that contains the word "error":
# a handled upstream data-source 4xx or a retried provider hiccup is not a
# broken service — those are counted as warnings by
# scripts/monitor_autotrade.py instead.
j = sh("journalctl --user -u tradingagents-gui --since '-16 min' --no-pager "
       "| grep -iE 'Traceback|CRITICAL|Unhandled|OpenAIError|"
       "OpenAIConnectionError|extension routes not loaded|Exception|"
       " [45][0-9]{2} .*[Ee]rror' "
       "| grep -viE 'transient .*retrying|retrying in|network error|"
       "HTTP Error 4[0-9]{3}|GET /api|auto-sync|ConnectTimeout' "
       "| tail -3")
print(f"[{'WARN' if j else 'OK'}] journal: "
      f"{j[:300] if j else 'clean'}")
if j and "ConnectTimeout" not in j and "network error" not in j:
    failures.append("journal errors present")

# 5. state.json freshness
fresh = sh("python3 -c \""
           "import json,os,time; p=os.path.expanduser('~/.tradingagents/tinvest/state.json');"
           "d=json.load(open(p)); age=int(time.time()-os.path.getmtime(p));"
           "print(f'stale={d.get(\\\"stale\\\")} age={age}s errors={len(d.get(\\\"errors\\\") or [])}')\"")
print(f"[{'WARN' if 'stale=True' in fresh else 'OK'}] state: {fresh}")
if "stale=True" in fresh:
    failures.append("reconciliation state stale")

# 6. lessons + journal size
lessons = sh("wc -l < ~/.tradingagents/tinvest/lessons.jsonl 2>/dev/null || echo 0")
disk = sh("df -h / | awk 'NR==2{print $4\" free (\"$5\" used)\"}'")
print(f"[OK] lessons={lessons.strip()} | disk: {disk}")

# 7. run status last error
# Only a *recent* failure is news; a months-old errored run is history.
runs = api("/api/runs")
if not runs.get("_error"):
    rs = runs.get("runs") or []
    active = [r for r in rs if r.get("status") == "running"]
    fresh = [r for r in rs
             if r.get("error") and (r.get("started_at") or 0) >= time.time() - 86400]
    print(f"[OK] runs: total={len(rs)} running={len(active)} "
          f"errored_24h={len(fresh)}")
    if fresh:
        last = fresh[0]
        print(f"[WARN] last error run: {last.get('ticker')} "
              f"{str(last.get('error'))[:100]}")

print("RESULT:", "FAIL: " + "; ".join(failures) if failures else "ALL OK")
sys.exit(1 if failures else 0)
