"""Persistent settings for the MOEX extension.

One JSON document at ``~/.tradingagents/moex/settings.json`` holds every
new setting this project adds: news source toggles, risk limits, sandbox
sync cadence and learning switches.  It is deliberately separate from the
GUI's ``ui_state.json`` so upstream configurations open unchanged and our
settings can never corrupt them.

Secrets (T-Invest tokens) are *never* stored here — see
``tinvest.credentials``.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    # News: per-source switches and cadence (moex/news/registry.py owns ids).
    "news": {
        "enabled": True,
        "sync_interval_minutes": 30,
        "sync_seconds": 1800,                 # GUI-facing cadence (§7.5)
        "sources": {},           # {source_id: {"enabled": bool, "priority": int}}
        "max_items_per_source": 50,
        "max_age_days": 14,
    },
    # Risk: enforced before every sandbox order (moex/risk/checks.py).
    # Key names must match moex.risk.DEFAULT_RISK — effective_limits()
    # only honours those keys.
    "risk": {
        "max_order_amount": 500000.0,         # руб. на одну заявку
        "max_position_pct": 30.0,             # % капитала в одной позиции
        "max_issuer_pct": 30.0,               # % капитала на одного эмитента
        "max_open_orders": 5,
        "allowed_instruments": [],            # [] = все разрешены
        "allow_new_buys": True,
        "allow_selling_positions": True,
        "max_risk_per_trade_pct": 5.0,        # % капитала на сделку
        "daily_loss_limit": None,             # руб. или None
        "require_confirmation": True,
        "automation_sandbox_only": True,      # автоматизация только песочницы
    },
    # Sandbox state sync.
    "sandbox": {
        "auto_sync_seconds": 60,
        "mark": "SANDBOX",                     # shown in the GUI
    },
    # Trading-process switches.
    "trading": {
        "require_order_confirmation": True,
        "sandbox_auto_confirm": False,         # never applies to production
        "use_learned_insights": True,
    },
    # T-Invest connection mode: disabled | readonly | sandbox |
    # live_readonly | live_trading (live_trading is refused in this build).
    "tinvest": {
        "mode": "sandbox",
        "active_account": None,                # sandbox account id chosen in the GUI
        "sync_seconds": 60,                    # portfolio auto-sync cadence (§5.7)
        "base_url": None,                      # None = mode-appropriate default
        "mcp_url": None,                       # None = mode-appropriate default
        "verify_ca_fallback": True,
    },
}


def settings_path() -> Path:
    base = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(base) / "moex" / "settings.json"


def load_settings() -> dict[str, Any]:
    """Return settings merged over defaults. Missing/corrupt file → defaults."""
    path = settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    merged = deepcopy(DEFAULT_SETTINGS)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Atomically persist settings and return what was written."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return settings


def update_settings(section: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge ``updates`` into one top-level section and persist."""
    settings = load_settings()
    current = settings.get(section)
    if isinstance(current, dict):
        current.update(updates or {})
    else:
        settings[section] = updates
    return save_settings(settings)


def home_dir() -> Path:
    base = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(base)
