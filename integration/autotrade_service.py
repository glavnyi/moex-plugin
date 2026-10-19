"""Scheduled auto-trading cycle: the executor behind ``moex.autotrade``.

A daemon thread alongside the §5.7 sync loop. One cycle:

    gates (moex.autotrade) ──▶ pick tickers not yet decided today
                             ──▶ launch the GUI's own analysis graph
                             ──▶ read the Portfolio Manager decision
                             ──▶ extract_proposal ──▶ prepare_order
                             ──▶ place_confirmed_order ──▶ journal

Reuse is the point: the same ``RunManager``, the same preview pipeline and
the same risk engine the manual button uses. An automatic order is never a
second code path with its own idea of the limits — the only difference is
who supplies ``confirm``, and that is exactly what the gate chain authorises
(spec §6.2 "отдельное разрешение", §6.3, §13).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from moex.autotrade import (
    Gate,
    decisions_today,
    evaluate_gates,
    first_blocked,
    gates_summary,
    journal_append,
    journal_entries,
    orders_today,
    resolve_environment,
    today_msk,
)

_state: dict = {
    "started": False,
    "thread": None,
    "cycle_in_flight": False,
    "last_cycle_at": None,
    "last_cycle": None,
    "last_error": None,
    "last_scan": None,
}

# How often the daemon wakes up when the engine is off — long enough that an
# idle system costs nothing, short enough that enabling it takes effect soon.
_IDLE_SLEEP = 60
_MIN_TICK = 60


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

def _settings() -> dict:
    from moex.config import load_settings

    return load_settings()


def update_settings(body: dict) -> dict:
    """Persist the auto-trading block. Returns the saved section."""
    from moex.config import load_settings as _load
    from moex.config import update_settings as _update

    was_killed = bool((_load().get("autotrade") or {}).get("kill_switch"))
    allowed = {
        "enabled", "environment", "live_confirmed", "kill_switch",
        "tick_seconds", "watchlist", "max_orders_per_day",
        "max_open_positions", "skip_if_holding", "analysts",
        "research_depth", "language", "run_timeout_seconds",
        "only_when_market_open",
    }
    updates: dict = {}
    for key in allowed:
        if key not in body:
            continue
        value = body[key]
        if key in {"enabled", "live_confirmed", "kill_switch", "skip_if_holding",
                   "only_when_market_open"}:
            updates[key] = bool(value)
        elif key == "environment":
            updates[key] = str(value).lower() if str(value).lower() in (
                "sandbox", "live") else "sandbox"
        elif key == "watchlist":
            updates[key] = [str(t).strip().upper() for t in (value or [])
                            if str(t).strip()]
        elif key == "analysts":
            updates[key] = [str(a).strip().lower() for a in (value or [])
                            if str(a).strip()]
        elif key == "language":
            updates[key] = str(value) or "English"
        else:
            try:
                updates[key] = max(1, int(value))
            except (TypeError, ValueError):
                continue
    saved = _update("autotrade", updates)
    if isinstance(body.get("screener"), dict):
        # Секция скринера живёт отдельно; effective_config дочищает типы.
        from moex.screener import effective_config

        _update("screener", effective_config({"screener": body["screener"]}))
    section = dict(saved.get("autotrade") or {})
    # Settings changed → the cached account/market facts may describe the
    # wrong world; drop them so the next status read re-fetches.
    _FACTS.update({"at": 0.0, "key": None, "facts": None})
    _state_scan(None)  # и последний скан описывал старые пороги
    # Switching the engine off is always allowed; releasing the kill switch
    # is a deliberate separate act and is recorded with who did it.
    if was_killed and updates.get("kill_switch") is False:
        journal_append({"event": "kill_switch_cleared", "by": "settings"})
    if updates.get("enabled") is True:
        journal_append({"event": "enabled",
                        "environment": section.get("environment"),
                        "live_confirmed": section.get("live_confirmed")})
    return section


# --------------------------------------------------------------------------
# facts the gates need
# --------------------------------------------------------------------------

def _market_open(cfg: dict) -> tuple[bool, str]:
    if not cfg.get("only_when_market_open", True):
        return True, "проверка рынка отключена"
    try:
        from moex.market_data import get_market_status

        status = get_market_status()
        trading = bool(status.get("trading"))
        return trading, ("торги идут" if trading else
                         f"статус {','.join(status.get('statuses') or []) or 'нет данных'}")
    except Exception as exc:  # noqa: BLE001 — fail closed, report why
        return False, f"не удалось прочитать статус рынка: {exc}"


def _client(mode: str):
    """(client, error) — never raises; gates judge the failure."""
    from tinvest.client import MODES, TInvestClient

    mode = str(mode or "").lower()
    if mode not in MODES or mode == "disabled":
        return None, f"tinvest.mode={mode or '(пусто)'} не подключается (§5.1)"
    client = TInvestClient.from_mode(mode)
    if client is None:
        return None, "TINVEST_TOKEN не задан — токен только из окружения"
    return client, ""


def _account(settings: dict) -> str:
    return str((settings.get("tinvest") or {}).get("active_account") or "")


def _portfolio(client, account_id: str):
    """(portfolio_ctx, error). Fresh account state for the gates and risk."""
    if not account_id:
        return None, "не выбран активный счёт"
    try:
        from tinvest.portfolio import portfolio_context

        return portfolio_context(client, account_id), ""
    except Exception as exc:  # noqa: BLE001 — surfaced as a failed gate
        return None, str(exc)


def _open_positions(ctx: dict | None) -> int:
    if not ctx:
        return 0
    count = 0
    for pos in ctx.get("positions") or []:
        if str(pos.get("type") or "").lower() == "currency":
            continue
        try:
            if float(pos.get("quantity_lots") or 0.0) > 0:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def _holding(ctx: dict | None, ticker: str) -> bool:
    if not ctx:
        return False
    for pos in ctx.get("positions") or []:
        if str(pos.get("type") or "").lower() == "currency":
            continue
        if str(pos.get("ticker") or "").upper() == ticker.upper():
            try:
                if float(pos.get("quantity_lots") or 0.0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _gui_runs():
    """The RunManager that belongs to the *served* Flask app.

    ``python -m gui.app`` executes the module as ``__main__``, so a plain
    ``from gui.app import runs`` would import a second copy of the module —
    a second Flask app and a second RunManager. The daemon would then drive
    a graph the HTTP API cannot see (and vice versa), and the two would
    fight over ``~/.tradingagents/runs.json``. Resolve through the module
    that actually owns the live app instead; ``__main__`` wins under ``-m``.

    Deliberately uncached: a lookup that ran too early would otherwise pin a
    phantom manager for the life of the process.
    """
    import sys

    for name in ("__main__", "gui.app"):
        mod = sys.modules.get(name)
        if (mod is not None and hasattr(mod, "runs") and hasattr(mod, "app")
                and hasattr(getattr(mod, "app", None), "import_name")):
            return mod.runs
    from gui.app import runs  # noqa: PLC0415 — GUI may be absent (CLI/tests)

    return runs


def _analysis_busy() -> bool:
    try:
        return _gui_runs().is_anything_running()
    except Exception:  # noqa: BLE001 — no GUI loaded → nothing is running
        return False


# --------------------------------------------------------------------------
# gate evaluation
# --------------------------------------------------------------------------

# Facts that cost a network round-trip (ISS market status, T-Invest
# portfolio). The status endpoint is polled by the GUI, so it may reuse a
# recent answer; a cycle always re-reads the world before spending money.
_FACTS: dict = {"at": 0.0, "key": None, "facts": None}
_FACTS_TTL = 30.0


def _gather_facts(settings: dict, cfg: dict, mode: str) -> dict:
    client, client_err = _client(mode)
    market_open, market_detail = _market_open(cfg)
    ctx, ctx_err = (_portfolio(client, _account(settings)) if client
                    else (None, client_err))
    return {
        "client": client,
        "client_ok": client is not None,
        "client_can_mutate": bool(client and client.can_mutate),
        "client_error": ctx_err or client_err,
        "market_open": market_open,
        "market_detail": market_detail,
        "portfolio_ok": ctx is not None,
        "open_positions": _open_positions(ctx),
        "ctx": ctx,
    }


def evaluate(settings: dict | None = None, *, fresh: bool = False) -> list[Gate]:
    """Full gate chain for the current process, with live facts attached.

    ``fresh=True`` skips the 30s fact cache — used by ``cycle()``, which must
    see the account exactly as it is before it sends an order.
    """
    settings = settings or _settings()
    cfg = dict(settings.get("autotrade") or {})
    mode = str((settings.get("tinvest") or {}).get("mode") or "").lower()

    key = (mode, _account(settings), bool(cfg.get("only_when_market_open", True)))
    now = time.time()
    if not fresh and _FACTS["facts"] is not None and _FACTS["key"] == key \
            and now - _FACTS["at"] < _FACTS_TTL:
        facts = _FACTS["facts"]
    else:
        facts = _gather_facts(settings, cfg, mode)
        _FACTS.update({"at": now, "key": key, "facts": facts})

    from moex.risk import daily_realized_pnl

    return evaluate_gates(
        settings,
        mode=mode,
        client_ok=facts["client_ok"],
        client_can_mutate=facts["client_can_mutate"],
        portfolio_ok=facts["portfolio_ok"],
        market_open=facts["market_open"],
        market_detail=facts["market_detail"],
        analysis_running=_analysis_busy(),
        open_positions=facts["open_positions"],
        daily_realized_pnl=daily_realized_pnl(),
        client_error=facts["client_error"],
        now=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------
# the analysis → order step
# --------------------------------------------------------------------------

def _analysis_params(ticker: str, cfg: dict) -> dict:
    """Parameters shaped like the Analyze form, so the run is indistinguishable
    from one started by hand (same vendors, same models, same depth)."""
    try:
        from gui.ui_state import UIStateStore

        ui = UIStateStore().get()
    except Exception:  # noqa: BLE001 — CLI/test context, fall back to defaults
        ui = {}

    def _opt(*keys, default=None):
        for key in keys:
            if ui.get(key) not in (None, ""):
                return ui[key]
        return default

    params = {
        "ticker": ticker.upper(),  # same shape as the form's own value
        "date": today_msk(),
        "market": "MOEX",
        "analysts": list(cfg.get("analysts") or ["market", "social",
                                                 "news", "fundamentals"]),
        "research_depth": int(cfg.get("research_depth") or 3),
        "output_language": cfg.get("language") or "English",
        "provider": _opt("provider", default="openrouter"),
        "quick_model": _opt("quick_model"),
        "deep_model": _opt("deep_model"),
        "report_brevity": "standard",
        "auto_trading": True,
        "triggered_by": "autotrade",
    }
    vendors = {
        "core_stock_apis": _opt("vendor_core"),
        "technical_indicators": _opt("vendor_technical"),
        "fundamental_data": _opt("vendor_fundamental"),
        "news_data": _opt("vendor_news"),
    }
    vendors = {k: v for k, v in vendors.items() if v}
    if vendors:
        params["data_vendors"] = vendors
    return params


def _run_analysis(ticker: str, cfg: dict) -> tuple[str, str]:
    """Start the graph and block until it finishes. Returns (run_id, report)."""
    runs = _gui_runs()

    run = runs.start(_analysis_params(ticker, cfg))
    deadline = time.time() + int(cfg.get("run_timeout_seconds") or 1800)
    while run.status in ("queued", "running") and time.time() < deadline:
        time.sleep(5)
    if run.status in ("queued", "running"):
        runs.stop(run.id)
        raise RuntimeError(f"анализ {ticker} не уложился в "
                           f"{cfg.get('run_timeout_seconds')} с — остановлен")
    if run.status != "completed":
        raise RuntimeError(f"анализ {ticker} завершился со статусом "
                           f"{run.status}: {run.error or 'нет деталей'}")
    report = (run.reports.get("final_trade_decision")
              or run.reports.get("trader_investment_plan")
              or "")
    if not report:
        report = _read_complete_report(ticker, run.date)
    if not report:
        raise RuntimeError(f"отчёт по {ticker} пуст — решение не прочитано")
    return run.id, report


def _read_complete_report(ticker: str, date: str) -> str:
    try:
        from pathlib import Path
        import os as _os

        home = _os.environ.get("TRADINGAGENTS_HOME") or str(
            Path.home() / ".tradingagents")
        path = Path(home) / "logs" / ticker.upper() / date / "reports" / "complete_report.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:  # noqa: BLE001 — absence is reported as an empty report
        return ""


def _inject_last_price(ctx: dict, ticker: str) -> None:
    """Attach a price so the §6.3 money limits have something to measure.

    ``portfolio_context()`` carries no top-level ``last_price`` — only the GUI
    preview sets it — so an auto-trade market proposal had no price, its
    ``estimated_amount`` stayed empty and ``check_proposal`` checked nothing.
    Failure is not fatal: with no price the risk engine now refuses the order.
    """
    try:
        from moex.market_data import get_quote

        quote = get_quote(ticker)
        last = quote.get("last") or quote.get("close")
        if last:
            ctx["last_price"] = float(last)
    except Exception:  # noqa: BLE001 — absence shows up as a risk violation
        pass


def _lot_size(ticker: str) -> int:
    """Lots per instrument (MOEX bonds trade in 100s) for amount maths."""
    try:
        from moex.instruments import resolve_instrument

        return int(resolve_instrument(ticker).lot_size or 1)
    except Exception:  # noqa: BLE001 — unknown lot → assume a single unit
        return 1


def _place_for(client, settings: dict, cfg: dict, ticker: str,
               report: str, run_id: str, env: str) -> dict:
    """Proposal → risk on fresh state → send. Returns a journal-shaped dict."""
    from moex.analytics import extract_proposal, place_confirmed_order, prepare_order
    from tinvest.orders import list_orders

    account_id = _account(settings)
    proposal = extract_proposal(report, ticker)
    decision_row = {
        "event": "decision",
        "date": today_msk(),
        "environment": env,
        "mode": client.mode,
        "ticker": ticker,
        "action": proposal.side,
        "lots": proposal.lots,
        "run_id": run_id,
        "order_type": proposal.order_type,
        "limit_price": proposal.limit_price,
    }
    journal_append(decision_row)

    if proposal.side == "hold":
        return {"ticker": ticker, "action": "hold", "placed": False,
                "note": "решение HOLD — заявка не формируется"}

    # Fresh state: the analysis took minutes, and §6.3 requires the check
    # to run against what the account looks like *now*.
    from tinvest.portfolio import portfolio_context

    ctx = portfolio_context(client, account_id)
    _inject_last_price(ctx, ticker)
    open_list = list_orders(client, account_id)
    open_orders = len(open_list)
    # §4 «защита от повторных покупок»: у брокера не должна уже висеть
    # активная ЗАЯВКА, отправленная нами по этому тикеру. Матчим по
    # client_order_id из журнала (cid обязан быть чистым UUID — T-Invest
    # отклоняет всё иное, код 30028), так что FIGI не нужен; активные
    # заявки живут не дольше сессии, окна журнала хватает.
    our_cids = {str(r.get("client_order_id") or "")
                for r in journal_entries(limit=200)
                if r.get("ticker") == ticker and r.get("client_order_id")}
    if any(str(o.client_order_id or "") in our_cids for o in open_list):
        journal_append({
            "event": "decision", "date": today_msk(), "environment": env,
            "mode": client.mode, "ticker": ticker, "action": "skip",
            "reason": "у брокера уже есть активная заявка по тикеру",
            "run_id": run_id,
        })
        return {"ticker": ticker, "action": proposal.side, "placed": False,
                "reason": "у брокера уже есть активная заявка по тикеру"}
    preview = prepare_order(proposal, ctx, settings,
                            open_order_count=open_orders,
                            lot_size=_lot_size(ticker))
    if not preview["risk"]["allowed"]:
        row = journal_append({
            "event": "risk_blocked",
            "date": today_msk(),
            "environment": env,
            "ticker": ticker,
            "violations": preview["risk"].get("violations") or [],
        })
        return {"ticker": ticker, "action": proposal.side, "placed": False,
                "reason": "риск-проверка отказала",
                "violations": preview["risk"].get("violations") or [],
                "journal": row}

    # Вид заявки для журнала и лимита: добор открытой позиции — increase
    # (расходует лимит как обычная заявка), всё остальное — open. Закрытия
    # и защитные ордера (kind=close/protective) лимитом не считаются.
    kind = "increase" if _holding(ctx, ticker) else "open"
    from tinvest.orders import new_client_order_id

    preview["client_order_id"] = new_client_order_id()
    result, recovered = _send_with_recovery(
        client, account_id, preview, portfolio_ctx=ctx, settings=settings,
        open_order_count=open_orders)
    journal_append({
        "event": "order",
        "date": today_msk(),
        "environment": env,
        "mode": client.mode,
        "ticker": ticker,
        "side": proposal.side,
        "kind": kind,
        "lots": proposal.lots,
        "placed": bool(result.get("placed")),
        "order_id": result.get("order_id"),
        "client_order_id": preview["client_order_id"],
        "status": result.get("state") or result.get("status"),
        "status_unknown": bool(result.get("status_unknown")) or None,
        "recovered": recovered or None,
        "reason": result.get("reason"),
        "violations": result.get("violations"),
        "run_id": run_id,
    })
    if result.get("placed"):
        # §5.7: refresh the snapshot and §11 lessons right away, so the next
        # cycle's gates see this order instead of the pre-order state.
        try:
            from moex.analytics import sync_lessons_from_operations
            from tinvest.reconciliation import sync

            sync(client, account_id)
            sync_lessons_from_operations(client, account_id)
        except Exception as exc:  # noqa: BLE001 — never fail a placed order
            _state["last_error"] = f"post-order sync: {exc}"
    return {"ticker": ticker, "action": proposal.side,
            "placed": bool(result.get("placed")),
            "order_id": result.get("order_id"),
            "state": result.get("state") or result.get("status"),
            "reason": result.get("reason")}


def _send_with_recovery(client, account_id: str, preview: dict, *,
                        portfolio_ctx: dict, settings: dict,
                        open_order_count: int, attempts: int = 2):
    """Отправка с защитой от дубликата при сетевом сбое (цель §3).

    Возвращает ``(result, recovered)``. Правила:

    * ``TInvestError`` — определённый отказ брокера: без повторов;
    * маркет-заявка при сетевом сбое — исход НЕИЗВЕСТЕН и повтор
      запрещён: мгновенное исполнение делает статус неотличимым от
      «не создана» (GetOrders показывает только открытые), а дубль
      покупки хуже пропущенной;
    * лимитная заявка при сбое — повтор разрешён, но ТОЛЬКО после
      проверки статуса предыдущей заявки у брокера по client_order_id
      (открытая лимитная в GetOrders видна). Найденная заявка
      восстанавливается (``recovered=True``) без повторной отправки;
    * если статус у брокера проверить не удалось — повтор запрещён
      (fail-closed): неизвестность не тратится в дубликат.
    """
    from moex.analytics import map_order_state, place_confirmed_order
    from tinvest.client import TInvestError
    from tinvest.orders import list_orders

    cid = str(preview.get("client_order_id") or "")
    is_market = str((preview.get("proposal") or {}).get("order_type")
                    or "market") == "market"
    last_exc: Exception | None = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            open_now = list_orders(client, account_id)
        except Exception as exc:  # noqa: BLE001 — статус не проверен → стоп
            return {"placed": False, "client_order_id": cid,
                    "status_unknown": True, "reason":
                        f"исход неизвестен, статус у брокера проверить не "
                        f"удалось ({exc}) — повторная отправка запрещена"}, False
        found = next((o for o in open_now if o.client_order_id == cid), None)
        if found is not None:
            return {"placed": True, "order_id": found.order_id,
                    "state": map_order_state(found.status),
                    "client_order_id": cid, "recovered": True}, True
        try:
            result = place_confirmed_order(
                client, account_id, preview, confirm=True,
                portfolio_ctx=portfolio_ctx, settings=settings,
                open_order_count=len(open_now) if attempt > 1
                else open_order_count,
            )
            result["client_order_id"] = cid
            return result, False
        except TInvestError as exc:
            return {"placed": False, "client_order_id": cid, "reason":
                    f"брокер отклонил заявку: {exc}"}, False
        except Exception as exc:  # noqa: BLE001 — сеть: исход неизвестен
            last_exc = exc
            if is_market:
                return {"placed": False, "client_order_id": cid,
                        "status_unknown": True, "reason":
                            f"сетевой сбой при маркет-заявке — исход "
                            f"неизвестен, повтор запрещён: {exc}"}, False
            # лимитная: следующая итерация сначала проверит GetOrders.
    return {"placed": False, "client_order_id": cid, "status_unknown": True,
            "reason": f"после {attempts} попыток исход неизвестен: {last_exc}"}, False


# --------------------------------------------------------------------------
# screener — automatic candidate selection (цель «Автоматический отбор»)
# --------------------------------------------------------------------------

def _held_tickers(ctx: dict | None) -> tuple:
    return tuple(
        str(p.get("ticker") or "").upper()
        for p in (ctx or {}).get("positions") or []
        if str(p.get("type") or "").lower() != "currency")


def _state_scan(scan: dict | None) -> None:
    _state["last_scan"] = scan


def _screener_funded_today() -> int:
    """Сколько анализов кандидатов оплачено сегодня (журнал → переживает
    перезапуск; одно «screener_candidate» = один запуск TradingAgents)."""
    from moex.autotrade import journal_entries, today_msk

    day = today_msk()
    return sum(1 for r in journal_entries(limit=2000)
               if r.get("event") == "screener_candidate"
               and str(r.get("date") or "") == day)


def _screener_candidates(settings: dict, cfg: dict, env: str,
                         ctx: dict | None) -> list[dict]:
    """Топ-кандидат скринера на этот цикл (0..1 элемента).

    Скан идёт по всей доске TQBR (TTL-кэш ISS), фильтры и ранжирование —
    чистая логика moex.screener. Бюджет LLM (max_candidates_per_day)
    считается по журналу, поэтому переживает перезапуск; дневной лимит
    заявок и «решение по тикеру уже принято» гасят цикл раньше.
    """
    from moex.autotrade import decisions_today, orders_today
    from moex.screener import effective_config, fetch_universe, select_candidates

    scfg = effective_config(settings)
    if not scfg.get("enabled"):
        return []
    max_orders = int(cfg.get("max_orders_per_day") or 0)
    if max_orders and len(orders_today(env)) >= max_orders:
        return []
    funded = _screener_funded_today()
    if funded >= int(scfg["max_candidates_per_day"]):
        _state_scan({"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                     "date": today_msk(),
                     "note": f"бюджет анализов исчерпан: {funded} из "
                             f"{scfg['max_candidates_per_day']} за день"})
        return []
    try:
        rows = fetch_universe(scfg["board"])
    except Exception as exc:  # noqa: BLE001 — скринер не должен убивать цикл
        _state["last_error"] = f"скринер: {exc}"
        journal_append({"event": "screener_error", "date": today_msk(),
                        "environment": env, "error": str(exc)})
        return []
    decided = tuple(decisions_today(env))
    held = _held_tickers(ctx)
    scan = select_candidates(rows, scfg, decided_today=decided, holding=held)
    scan["funded_today"] = funded
    scan["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scan["date"] = today_msk()
    _state_scan(scan)
    journal_append({
        "event": "screener_scan", "date": today_msk(), "environment": env,
        "scanned": scan["scanned"], "passed": scan["passed"],
        "rejected": scan["rejected"],
        "deferred": {k: len(v) for k, v in (scan.get("deferred") or {}).items()},
        "top": [c.get("secid") for c in scan["candidates"]],
        "funded_today": funded,
    })
    return list(scan["candidates"])


def _tickers_for_cycle(settings: dict, cfg: dict, env: str,
                       ctx: dict | None) -> list[tuple]:
    """[(ticker, source, meta)]: ручной вишлист первым (как раньше), затем
    — если скринер включён — один кандидат скринера.

    Один кандидат за цикл: анализ занимает десятки минут, цикл не должен
    блокироваться на часы; следующий цикл (tick_seconds) возьмёт следующего
    по очереди — отбор продолжается, пока не исчерпан лимит заявок или
    анализов за день либо не закроется сессия.
    """
    out = [(str(t).upper(), "watchlist", {})
           for t in (cfg.get("watchlist") or []) if str(t).strip()]
    try:
        candidates = _screener_candidates(settings, cfg, env, ctx)
    except Exception as exc:  # noqa: BLE001
        _state["last_error"] = f"скринер: {exc}"
        return out
    for row in candidates[:1]:
        out.append((str(row.get("secid") or "").upper(), "screener", row))
    return out


def screener_preview(settings: dict | None = None) -> dict:
    """Онлайн-предпросмотр отбора: кандидаты и разбивка отказов.

    Без LLM и без заявок — читает кэшированную вселенную борда и чистую
    логику moex.screener; решения/позиции — из живых фактов. То, что
    увидит следующий цикл.
    """
    settings = settings or _settings()
    cfg = dict(settings.get("autotrade") or {})
    env = resolve_environment(settings)
    mode = str((settings.get("tinvest") or {}).get("mode") or "").lower()
    from moex.screener import effective_config, fetch_universe, select_candidates

    scfg = effective_config(settings)
    facts = _gather_facts(settings, cfg, mode)
    rows = fetch_universe(scfg["board"])
    scan = select_candidates(rows, scfg,
                             decided_today=tuple(decisions_today(env)),
                             holding=_held_tickers(facts["ctx"]))
    scan["funded_today"] = _screener_funded_today()
    scan["queue_limit"] = scfg["max_candidates_per_day"]
    scan["enabled"] = bool(scfg.get("enabled"))
    scan["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scan["date"] = today_msk()
    return scan


# --------------------------------------------------------------------------
# one cycle
# --------------------------------------------------------------------------

def cycle(settings: dict | None = None, *, force: bool = False) -> dict:
    """Run one full cycle. Safe to call directly (manual trigger / tests).

    ``force=True`` drops exactly one gate — ``enabled`` — so an operator can
    rehearse a cycle with the master switch off. Everything else (kill
    switch, venue match, live confirmations, budgets, market) still applies:
    a manual click is not an override of the safety table.
    """
    if _state["cycle_in_flight"]:
        return {"ok": False, "skipped": "цикл уже выполняется"}
    _state["cycle_in_flight"] = True
    try:
        return _cycle_locked(settings, force=force)
    finally:
        _state["cycle_in_flight"] = False


def _cycle_locked(settings: dict | None = None, *, force: bool = False) -> dict:
    settings = settings or _settings()
    cfg = dict(settings.get("autotrade") or {})
    env = resolve_environment(settings)
    mode = str((settings.get("tinvest") or {}).get("mode") or "").lower()

    from moex.risk import daily_realized_pnl

    # Fresh facts, not the UI's cache: an order must be judged against the
    # account as it is *now* (§6.3).
    facts = _gather_facts(settings, cfg, mode)
    gates = evaluate_gates(
        settings,
        mode=mode,
        client_ok=facts["client_ok"],
        client_can_mutate=facts["client_can_mutate"],
        portfolio_ok=facts["portfolio_ok"],
        market_open=facts["market_open"],
        market_detail=facts["market_detail"],
        analysis_running=_analysis_busy(),
        open_positions=facts["open_positions"],
        daily_realized_pnl=daily_realized_pnl(),
        client_error=facts["client_error"],
        now=datetime.now(timezone.utc),
    )
    if force:
        gates = [g for g in gates if g.name != "enabled"]
    summary = gates_summary(gates)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    blocked = first_blocked(gates)
    if blocked:
        record = {"ok": False, "at": started, "environment": env,
                  "blocked_by": blocked.name, "reason": blocked.detail,
                  "gates": summary["gates"], "results": []}
        _state["last_cycle"] = record
        _state["last_cycle_at"] = started
        journal_append({"event": "cycle_blocked", "date": today_msk(),
                        "environment": env, "gate": blocked.name,
                        "reason": blocked.detail})
        return record

    client = facts["client"]
    ctx = facts["ctx"]
    account_id = _account(settings)
    max_orders = int(cfg.get("max_orders_per_day") or 0)
    results: list[dict] = []

    for ticker, source, meta in _tickers_for_cycle(settings, cfg, env, ctx):
        if len(orders_today(env)) >= max_orders:
            results.append({"ticker": ticker, "skipped": "дневной лимит заявок"})
            break
        if ticker in decisions_today(env):
            results.append({"ticker": ticker, "skipped": "по этому тикеру уже "
                                                          "принято решение сегодня"})
            continue
        if cfg.get("skip_if_holding", True) and _holding(ctx, ticker):
            journal_append({"event": "decision", "date": today_msk(),
                            "environment": env, "ticker": ticker,
                            "action": "skip", "reason": "позиция уже открыта"})
            results.append({"ticker": ticker, "skipped": "позиция уже открыта"})
            continue
        if _analysis_busy():
            results.append({"ticker": ticker, "skipped": "анализ уже выполняется"})
            break
        if source == "screener":
            journal_append({
                "event": "screener_candidate", "date": today_msk(),
                "environment": env, "ticker": ticker,
                "turnover_rub": meta.get("turnover_rub"),
                "spread_pct": meta.get("spread_pct"),
                "last": meta.get("last"),
                "funded_today": _screener_funded_today() + 1,
            })
        try:
            run_id, report = _run_analysis(ticker, cfg)
            results.append(_place_for(client, settings, cfg, ticker, report,
                                      run_id, env))
            # A placement changes the book; re-read it for the next ticker.
            ctx, _ = _portfolio(client, account_id)
        except Exception as exc:  # noqa: BLE001 — one ticker must not kill the cycle
            _state["last_error"] = str(exc)
            journal_append({"event": "cycle_error", "date": today_msk(),
                            "environment": env, "ticker": ticker,
                            "error": str(exc)})
            results.append({"ticker": ticker, "error": str(exc)})

    record = {
        "ok": not any(r.get("error") for r in results),
        "at": started,
        "environment": env,
        "gates": summary["gates"],
        "results": results,
        "orders_today": len(orders_today(env)),
    }
    _state["last_cycle"] = record
    _state["last_cycle_at"] = started
    journal_append({"event": "cycle_done", "date": today_msk(),
                    "environment": env, "results": results})
    return record


# --------------------------------------------------------------------------
# daemon thread
# --------------------------------------------------------------------------

def _loop(app) -> None:
    while True:
        try:
            settings = _settings()
            cfg = dict(settings.get("autotrade") or {})
            enabled = bool(cfg.get("enabled")) and not bool(cfg.get("kill_switch"))
            _state["last_error"] = None if enabled else _state["last_error"]
            if enabled:
                with app.app_context():
                    cycle(settings)
                seconds = int(cfg.get("tick_seconds") or 300)
            else:
                seconds = _IDLE_SLEEP
            time.sleep(max(_MIN_TICK, seconds))
        except Exception as exc:  # noqa: BLE001 — the worker must survive
            _state["last_error"] = str(exc)
            journal_append({"event": "loop_error", "error": str(exc)})
            time.sleep(_IDLE_SLEEP)


def start(app) -> None:
    """Start the daemon once per process (mirrors _start_auto_sync).

    Suppressed under a test client and under ``TRADINGAGENTS_AUTOTRADE=0``:
    importing ``gui.app`` registers the routes on the *real* application
    while pytest only ever serves the fixture one, so the guard has to live
    here rather than in the caller.
    """
    import os

    if _state["started"]:
        return
    if os.environ.get("TRADINGAGENTS_AUTOTRADE", "").lower() in ("0", "off", "false"):
        return
    if getattr(app, "config", {}).get("TESTING"):
        return
    _state["started"] = True
    thread = threading.Thread(target=_loop, args=(app,),
                              name="autotrade", daemon=True)
    _state["thread"] = thread
    thread.start()


# --------------------------------------------------------------------------
# status for the GUI
# --------------------------------------------------------------------------

def _signals_today(env: str) -> dict:
    """Судьба решений за сегодня: отправлено / HOLD / отклонено риском."""
    from moex.autotrade import journal_entries, today_msk

    day = today_msk()
    rows = [r for r in journal_entries(limit=2000)
            if str(r.get("date") or "") == day
            and str(r.get("environment") or "") == env]
    placed = sum(1 for r in rows if r.get("event") == "order" and r.get("placed"))
    holds = sum(1 for r in rows if r.get("event") == "decision"
                and r.get("action") == "hold")
    blocked = sum(1 for r in rows if r.get("event") == "risk_blocked")
    return {"placed": placed, "hold": holds, "risk_blocked": blocked,
            "rejected": holds + blocked}


def _orders_journal_today(env: str) -> list[dict]:
    from moex.autotrade import journal_entries, today_msk, \
        order_counts_toward_limit

    day = today_msk()
    rows = [r for r in journal_entries(limit=2000)
            if r.get("event") == "order"
            and str(r.get("environment") or "") == env
            and str(r.get("date") or "") == day]
    for r in rows:
        r["counts_toward_limit"] = order_counts_toward_limit(r)
    return rows[::-1]


def status(settings: dict | None = None) -> dict:
    settings = settings or _settings()
    cfg = dict(settings.get("autotrade") or {})
    env = resolve_environment(settings)
    gates = evaluate(settings)
    summary = gates_summary(gates)
    from moex.autotrade import order_budget
    from moex.screener import effective_config

    max_orders = int(cfg.get("max_orders_per_day") or 0)
    scfg = effective_config(settings)
    return {
        "settings": cfg,
        "environment": env,
        "mode": str((settings.get("tinvest") or {}).get("mode") or ""),
        "running": _state["cycle_in_flight"],
        "loop_started": _state["started"],
        "last_cycle_at": _state["last_cycle_at"],
        "last_cycle": _state["last_cycle"],
        "last_error": _state["last_error"],
        "decisions_today": decisions_today(env),
        "orders_today": len(orders_today(env)),
        "orders_budget": order_budget(env, max_orders),
        "orders": _orders_journal_today(env),
        "signals_today": _signals_today(env),
        "screener": {
            "enabled": bool(scfg.get("enabled")),
            "settings": scfg,
            "funded_today": _screener_funded_today(),
            "last_scan": _state["last_scan"],
        },
        "gates": summary,
        "journal": journal_entries(limit=50),
        "environments": ["sandbox", "live"],
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def reset_state_for_tests() -> None:
    """Clear the process-local runtime block (tests only)."""
    _state.update({
        "started": False, "thread": None, "cycle_in_flight": False,
        "last_cycle_at": None, "last_cycle": None, "last_error": None,
        "last_scan": None,
    })
    _FACTS.update({"at": 0.0, "key": None, "facts": None})
