"""Auto-trading engine: the deterministic gate in front of a scheduled cycle.

The original TradingAgents hands a Portfolio Manager decision to a
*simulated exchange* with no per-order click (README: "If approved, the
order will be sent to the simulated exchange and executed"). This module is
the authorising half of that contract for our project — it answers **may a
cycle run at all**, never **what to trade**. The trade still comes from the
analysis, and every proposal is re-checked by ``moex.risk.check_proposal``
against fresh portfolio state immediately before it is sent (§6.3).

Safety, stated as code (spec §1.1 item 14, §6.5, §13, §16):

* the engine is **off in every environment by default** and cannot turn
  itself on;
* a ``live`` cycle additionally needs ``tinvest.mode == "live_trading"``,
  ``autotrade.live_confirmed`` and ``risk.automation_sandbox_only == False``
  — three separately stored settings, none of which a sandbox choice implies;
* the kill switch stops the cycle while leaving ``enabled`` untouched, so
  resuming is a deliberate act;
* at most ``max_orders_per_day`` placements and one decision per ticker per
  Moscow calendar day.

Nothing here talks to a broker or to the LLM graph: the runtime facts it
judges are injected, which is what makes the gates testable. The only local
I/O is the audit journal, redirectable through ``TRADINGAGENTS_HOME``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))

# Environments an auto cycle can target.
ENVIRONMENTS = ("sandbox", "live")

# Journal events that count as "we already spent today's decision here".
_DECISION_EVENTS = frozenset({"decision", "order"})

# Виды заявок (поле ``kind`` в журнале). Дневной лимит «Максимум новых
# заявок в день» считает ТОЛЬКО новые позиции, доборы и повторные отправки
# (retry) — это одна экономическая заявка. Закрытия позиции и защитные
# ордера (стоп-лосс/тейк-профит) лимитом НЕ блокируются: позиция никогда
# не должна остаться без защиты из-за исчерпанного бюджета (цель §3).
# Старые записи без ``kind`` считаются (исторически они были новыми).
COUNTED_ORDER_KINDS = frozenset({None, "open", "increase", "retry"})
FREE_ORDER_KINDS = frozenset({"close", "protective"})


def order_counts_toward_limit(row: dict) -> bool:
    """True — запись журнала расходует дневной лимит.

    Считаются только фактически отправленные (``placed`` не False)
    новые/добырочные/retry-заявки: проанализировать тикер и получить отказ
    риска — не заявка, бюджет не расходуется.
    """
    if row.get("placed") is False:
        return False
    kind = row.get("kind")
    if kind is None:
        kind = "open" if row.get("event") == "order" else None
    return kind in COUNTED_ORDER_KINDS


# --------------------------------------------------------------------------
# Journal — one append-only file, the audit trail for every cycle
# --------------------------------------------------------------------------

def journal_path() -> Path:
    home = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(home) / "tinvest" / "autotrade_journal.jsonl"


def journal_append(entry: dict) -> dict:
    """Append one entry; returns it with a timestamp if it had none."""
    row = dict(entry)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def journal_entries(limit: int = 200) -> list[dict]:
    path = journal_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-int(limit):]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def today_msk(now: datetime | None = None) -> str:
    """The Moscow calendar day a cycle belongs to (trading day, §6.3)."""
    return (now or datetime.now(timezone.utc)).astimezone(MSK).strftime("%Y-%m-%d")


def decisions_today(environment: str, date: str | None = None) -> dict[str, dict]:
    """Latest decision per ticker for ``environment`` on ``date`` (MSK)."""
    day = date or today_msk()
    out: dict[str, dict] = {}
    for row in journal_entries(limit=2000):
        if row.get("event") not in _DECISION_EVENTS:
            continue
        if str(row.get("environment") or "") != environment:
            continue
        if str(row.get("date") or "") != day:
            continue
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            out[ticker] = row
    return out


def orders_today(environment: str, date: str | None = None) -> list[dict]:
    """Заявки, расходующие дневной лимит (см. ``order_counts_toward_limit``)."""
    day = date or today_msk()
    return [
        row for row in journal_entries(limit=2000)
        if row.get("event") == "order"
        and str(row.get("environment") or "") == environment
        and str(row.get("date") or "") == day
        and order_counts_toward_limit(row)
    ]


def order_budget(environment: str, max_orders: int, date: str | None = None) -> dict:
    """Бюджет новых заявок на московский день: used / limit / left."""
    used = len(orders_today(environment, date))
    limit = max(0, int(max_orders or 0))
    return {"used": used, "limit": limit,
            "left": max(0, limit - used) if limit else 0,
            "date": date or today_msk()}


def already_decided(ticker: str, environment: str, date: str | None = None) -> bool:
    return str(ticker).upper() in decisions_today(environment, date)


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    """One named precondition. ``ok=False`` blocks the whole cycle."""

    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def resolve_environment(settings: dict) -> str:
    """Which venue this configuration wants: ``sandbox`` or ``live``."""
    env = str(((settings.get("autotrade") or {}).get("environment")) or "sandbox")
    return env if env in ENVIRONMENTS else "sandbox"


def evaluate_gates(
    settings: dict,
    *,
    mode: str,
    client_ok: bool = True,
    client_can_mutate: bool = False,
    portfolio_ok: bool = True,
    market_open: bool = True,
    market_detail: str = "",
    analysis_running: bool = False,
    open_positions: int = 0,
    daily_realized_pnl: float | None = None,
    client_error: str = "",
    now: datetime | None = None,
) -> list[Gate]:
    """Ordered, fail-closed preconditions for one auto-trading cycle.

    Every fact except the settings themselves is injected by the caller, so
    the whole decision table is exercised by unit tests without a network,
    a broker or a running GUI.
    """
    cfg = dict(settings.get("autotrade") or {})
    risk = dict(settings.get("risk") or {})
    tinv = dict(settings.get("tinvest") or {})
    env = resolve_environment(settings)
    gates: list[Gate] = []

    def add(name: str, ok: bool, detail: str) -> None:
        gates.append(Gate(name, bool(ok), detail))

    add("enabled", bool(cfg.get("enabled")),
        "автоторговля включена" if cfg.get("enabled")
        else "выкл: autotrade.enabled = false")

    add("kill_switch", not bool(cfg.get("kill_switch")),
        "аварийный стоп снят" if not cfg.get("kill_switch")
        else "сработал аварийный стоп (kill_switch)")

    watchlist = [str(t).strip().upper() for t in (cfg.get("watchlist") or []) if str(t).strip()]
    screener_on = bool((settings.get("screener") or {}).get("enabled"))
    add("watchlist", bool(watchlist) or screener_on,
        f"тикеров в наборе: {len(watchlist)}" if watchlist
        else ("набор пуст — отбор по скринеру (TQBR)" if screener_on
              else "набор тикеров пуст"))

    # Venue match: the configured §5.1 mode must be the environment we want.
    mode_ok = (str(mode) == "sandbox") if env == "sandbox" else (str(mode) == "live_trading")
    add("environment", mode_ok,
        f"цель={env}, tinvest.mode={mode}" + ("" if mode_ok else
            f" — нужен mode={'sandbox' if env == 'sandbox' else 'live_trading'}"))

    if env == "live":
        # Three deliberate switches, none implied by sandbox configuration.
        add("live_confirmed", bool(cfg.get("live_confirmed")),
            "боевой режим подтверждён" if cfg.get("live_confirmed")
            else "autotrade.live_confirmed = false — требуется явное подтверждение боя")
        add("automation_scope", not bool(risk.get("automation_sandbox_only", True)),
            "risk.automation_sandbox_only снят" if not risk.get("automation_sandbox_only", True)
            else "risk.automation_sandbox_only = true — автоматизация ограничена песочницей")

    if client_ok and client_can_mutate:
        add("read_write", True, f"клиент {mode} может отправлять заявки")
    else:
        add("read_write", False,
            client_error or ("клиент не создан (нет токена?)" if not client_ok
                             else f"tinvest.mode={mode} — только чтение"))

    add("portfolio_readable", bool(portfolio_ok),
        "состояние счёта прочитано" if portfolio_ok
        else (client_error or "не удалось прочитать состояние счёта"))

    if cfg.get("only_when_market_open", True):
        add("market_open", bool(market_open),
            market_detail or ("торги MOEX идут" if market_open
                              else "рынок закрыт"))

    # Budgets are compared against the Moscow day so the cap cannot be reset
    # by a UTC boundary.
    day = today_msk(now)
    placed = orders_today(env, day)
    max_orders = int(cfg.get("max_orders_per_day") or 0)
    add("orders_budget", len(placed) < max_orders if max_orders > 0 else False,
        f"заявок сегодня {len(placed)} из {max_orders}"
        if max_orders > 0 else "max_orders_per_day <= 0 — лимит запрещает сделки")

    max_pos = int(cfg.get("max_open_positions") or 0)
    add("positions_budget", open_positions < max_pos if max_pos > 0 else False,
        f"открытых позиций {open_positions} из {max_pos}"
        if max_pos > 0 else "max_open_positions <= 0 — лимит запрещает новые позиции")

    add("analysis_idle", not analysis_running,
        "предыдущий анализ завершён" if not analysis_running
        else "анализ уже выполняется — цикл пропущен")

    # Daily loss stop (§6.3). Enforced again inside check_proposal; this gate
    # only saves burning an analysis we would refuse afterwards.
    limit = risk.get("daily_loss_limit")
    if limit is not None:
        realized = float(daily_realized_pnl or 0.0)
        breached = realized <= -abs(float(limit))
        add("daily_loss", not breached,
            f"realized {realized:.2f} RUB при лимите {-abs(float(limit)):.2f}"
            + (" — превышен" if breached else ""))

    return gates


def first_blocked(gates: list[Gate]) -> Gate | None:
    return next((g for g in gates if not g.ok), None)


def gates_summary(gates: list[Gate]) -> dict:
    blocked = first_blocked(gates)
    return {
        "ok": blocked is None,
        "blocked_by": blocked.name if blocked else None,
        "reason": blocked.detail if blocked else "все условия выполнены",
        "gates": [g.to_dict() for g in gates],
    }
