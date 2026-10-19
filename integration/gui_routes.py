"""REST routes for the TradingAgents-MOEX extension (spec §9, §5.2-5.7).

Registered from a small patch on ``gui/app.py``; every handler imports
the extension lazily so the GUI keeps working if the extension is
absent. No token value is ever echoed back (spec §13).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flask import Response, jsonify, request

_JS_PATH = Path(__file__).parent / "ext.js"
_last_news_statuses: list[dict] = []

# §5.1 connection modes, shown verbatim next to the selector.
_MODE_DESCRIPTIONS = {
    "disabled": "1. Отключено — ничего не подключается.",
    "readonly": "2. Только чтение — боевой счёт, любые заявки и стопы запрещены.",
    "sandbox": "3. Песочница — виртуальный счёт, заявки разрешены.",
    "live_readonly": "4. Боевой счёт, только чтение — заявки и стопы запрещены.",
    "live_trading": "5. Реальная торговля — боевой счёт, заявки разрешены. "
                    "Деньги настоящие; требует confirm_live.",
}


def _ok(**kw):
    return jsonify({"ok": True, **kw}), 200


def _err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": str(message)}), status


def _settings():
    from moex.config import load_settings

    return load_settings()


def _tinvest_client(sandbox: bool | None = None):
    """Build a client for the configured §5.1 mode.

    ``sandbox=`` is kept for callers that must pin a venue explicitly; the
    default is to honour ``tinvest.mode``, so read-only modes reach
    production and refuse every write instead of silently using the sandbox.
    """
    from tinvest.client import MODES, TInvestClient, TInvestError

    configured = str((_settings().get("tinvest") or {}).get("mode") or "").lower()
    mode = configured if configured in MODES else "sandbox"
    if sandbox is not None:
        mode = "sandbox" if sandbox else "live_readonly"
    if mode == "disabled":
        raise TInvestError(
            "tinvest.mode is 'disabled' (§5.1) — nothing to connect to"
        )
    client = TInvestClient.from_mode(mode)
    if client is None:
        raise TInvestError(
            "TINVEST_TOKEN is not set — configure a T-Invest API token "
            "(env only, never stored in settings)"
        )
    return client


def _active_account(settings: dict | None = None) -> str:
    settings = settings or _settings()
    return str(((settings.get("tinvest") or {}).get("active_account")) or "")


def _set_active_account(account_id: str) -> None:
    from moex.config import update_settings

    update_settings("tinvest", {"active_account": account_id})


_auto_sync_state: dict = {"started": False, "last_sync": None, "last_error": None,
                          "enabled": False, "thread": None}


def _auto_sync_loop(app) -> None:
    """Background sandbox portfolio reconciler (spec §5.7, §7.5).

    Daemon thread started once per process; refreshes the persisted
    broker snapshot every ``auto_sync_seconds`` while trading mode is
    sandbox. Never raises out of the loop — a transient broker error
    marks the snapshot stale and retries next tick.
    """
    import time as _time

    while True:
        try:
            seconds = int((_settings().get("tinvest") or {})
                          .get("auto_sync_seconds") or 60)
            # §5.1: every mode that can talk to a broker synchronises; only
            # `disabled` has nothing to read.
            mode = str((_settings().get("tinvest") or {})
                       .get("mode") or "").lower()
            enabled = mode in ("readonly", "sandbox", "live_readonly",
                               "live_trading")
            _auto_sync_state["enabled"] = enabled
            if enabled and _active_account():
                from tinvest.reconciliation import sync

                client = _tinvest_client()
                with app.app_context():
                    snap = sync(client, _active_account())
                    # §11: closed sandbox deals → lessons for future runs
                    # (idempotent — consumed op ids persist in state).
                    try:
                        from moex.analytics import sync_lessons_from_operations
                        sync_lessons_from_operations(client, _active_account())
                    except Exception as lesson_exc:  # noqa: BLE001
                        snap.setdefault("errors", []).append(
                            f"lessons: {lesson_exc}")
                _auto_sync_state["last_sync"] = snap.get("synced_at")
                _auto_sync_state["last_error"] = None if not snap.get("stale") \
                    else "; ".join(snap.get("errors") or []) or "stale"
            _time.sleep(max(15, seconds))
        except Exception as exc:  # noqa: BLE001 — background worker must live
            _auto_sync_state["last_error"] = str(exc)
            _time.sleep(max(30, seconds))


def _start_auto_sync(app) -> None:
    import threading

    if _auto_sync_state["started"]:
        return
    _auto_sync_state["started"] = True
    t = threading.Thread(target=_auto_sync_loop, args=(app,),
                         name="tinvest-auto-sync", daemon=True)
    _auto_sync_state["thread"] = t
    t.start()


def _start_autotrade(app) -> None:
    """Spawn the auto-trading daemon (idempotent, mirrors _start_auto_sync).

    The thread always runs; it sleeps cheaply while ``autotrade.enabled`` is
    false, so an untouched install performs no analysis and places nothing.
    """
    try:
        import integration.autotrade_service as ats
    except Exception:  # noqa: BLE001 — extension degraded, GUI still works
        return
    ats.start(app)


def register_routes(app) -> None:
    """Called once from the patched gui/app.py."""

    # ---------------------------------------------------------------- JS
    @app.route("/api/moex_ext.js")
    def _moex_ext_js():
        from flask import Response

        body = _JS_PATH.read_text(encoding="utf-8") if _JS_PATH.exists() else "// missing"
        return Response(body, mimetype="application/javascript")

    # ----------------------------------------------------- instruments
    @app.route("/api/moex/instruments")
    def _moex_instruments():
        from moex.instruments import InstrumentNotFound, resolve_instrument, search_instruments

        q = (request.args.get("q") or "").strip()
        if not q:
            return _err("q required")
        try:
            if " " not in q and q.upper() == q and len(q) <= 12:
                # cheap exact resolve first, fall back to search
                try:
                    inst = resolve_instrument(q)
                    return _ok(results=[_instrument_row(inst)])
                except InstrumentNotFound:
                    pass
            return _ok(results=search_instruments(q, limit=15))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 502)

    @app.route("/api/moex/quote")
    def _moex_quote():
        from moex.market_data import get_quote

        ticker = (request.args.get("ticker") or "").strip()
        if not ticker:
            return _err("ticker required")
        try:
            return _ok(quote=get_quote(ticker))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 502)

    # ---------------------------------------------------------- news
    @app.route("/api/moex/news/registry")
    def _moex_news_registry():
        from moex.news.registry import SOURCE_REGISTRY

        enabled = _settings().get("news", {}).get("sources") or {}
        rows = []
        for src in SOURCE_REGISTRY:
            flag = enabled.get(src.id, src.enabled_by_default)
            if isinstance(flag, dict):
                flag = flag.get("enabled", True)
            rows.append({
                "id": src.id, "name": src.name, "category": src.category,
                "kind": src.kind, "available": src.available,
                "requires_token": src.requires_token,
                "enabled": bool(flag and (src.available or src.id in enabled)),
                "reason": src.reason, "notes": src.notes,
            })
        return _ok(sources=rows, sync_seconds=_settings().get("news", {}).get("sync_seconds"))

    @app.route("/api/moex/news/refresh", methods=["POST"])
    def _moex_news_refresh():
        global _last_news_statuses
        from moex.news import fetch_all

        body = request.get_json(silent=True) or {}
        try:
            agg = fetch_all(days=int(body.get("days") or 3), limit=80)
            _last_news_statuses = [s.to_dict() for s in agg.statuses]
            return _ok(statuses=_last_news_statuses,
                       unique_items=len(agg.unique_items),
                       fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 502)

    @app.route("/api/moex/news/statuses")
    def _moex_news_statuses():
        return _ok(statuses=_last_news_statuses)

    @app.route("/api/moex/news/preview")
    def _moex_news_preview():
        from moex.instruments import InstrumentNotFound, resolve_instrument
        from moex.news import fetch_all, news_report

        ticker = (request.args.get("ticker") or "").strip().upper()
        days = int(request.args.get("days") or 5)
        if not ticker:
            return _err("ticker required")
        try:
            inst = resolve_instrument(ticker)
        except InstrumentNotFound as exc:
            return _err(exc, 404)
        try:
            agg = fetch_all(instrument=inst, days=days, limit=60)
            end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start = (datetime.now(timezone.utc).timestamp() - days * 86400)
            start_s = datetime.utcfromtimestamp(start).strftime("%Y-%m-%d")
            return _ok(
                report=news_report(agg.items, inst.secid, start_s, end, linked=True),
                statuses=[s.to_dict() for s in agg.statuses],
            )
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 502)

    @app.route("/api/moex/news/settings", methods=["POST"])
    def _moex_news_settings():
        from moex.config import update_settings

        body = request.get_json(silent=True) or {}
        updates: dict = {}
        if isinstance(body.get("sources"), dict):
            updates["sources"] = body["sources"]
        if body.get("sync_seconds"):
            updates["sync_seconds"] = int(body["sync_seconds"])
        if updates:
            update_settings("news", updates)
        return _ok(news=_settings().get("news"))

    # ----------------------------------------------------------- risk
    @app.route("/api/moex/risk", methods=["GET", "POST"])
    def _moex_risk():
        from moex.config import update_settings
        from moex.risk import DEFAULT_RISK, effective_limits

        if request.method == "GET":
            trading = _settings().get("trading") or {}
            return _ok(limits=effective_limits(_settings()),
                       defaults=DEFAULT_RISK,
                       sandbox_auto_confirm=bool(
                           trading.get("sandbox_auto_confirm")))
        body = request.get_json(silent=True) or {}
        # sandbox_auto_confirm lives under "trading" (§13), not "risk".
        if "sandbox_auto_confirm" in body:
            update_settings("trading", {
                "sandbox_auto_confirm": bool(body["sandbox_auto_confirm"])})
        allowed = {k: v for k, v in body.items() if k in DEFAULT_RISK}
        update_settings("risk", allowed)
        trading = _settings().get("trading") or {}
        return _ok(limits=effective_limits(_settings()),
                   sandbox_auto_confirm=bool(
                       trading.get("sandbox_auto_confirm")))

    # -------------------------------------------------------- lessons
    @app.route("/api/moex/lessons", methods=["GET"])
    def _moex_lessons():
        from moex.analytics import load_lessons

        return _ok(lessons=load_lessons(enabled_only=False))

    @app.route("/api/moex/lessons", methods=["POST"])
    def _moex_lesson_toggle():
        from moex.analytics import set_lesson_enabled

        body = request.get_json(silent=True) or {}
        done = set_lesson_enabled(str(body.get("id") or ""), bool(body.get("enabled")))
        return _ok(updated=done) if done else _err("lesson not found", 404)

    @app.route("/api/moex/lessons", methods=["DELETE"])
    def _moex_lesson_delete():
        from moex.analytics import delete_lesson

        body = request.get_json(silent=True) or {}
        done = delete_lesson(str(body.get("id") or ""))
        return _ok(deleted=done) if done else _err("lesson not found", 404)

    # -------------------------------------------------------- T-Invest
    @app.route("/api/tinvest/status")
    def _tinvest_status():
        import os

        from tinvest.client import MODES, MUTATING_MODES, READ_ONLY_MODES

        configured = bool(os.environ.get("TINVEST_TOKEN")
                          or os.environ.get("TRADINGAGENTS_TINVEST_TOKEN"))
        settings = _settings().get("tinvest") or {}
        raw_mode = str(settings.get("mode") or "").lower()
        # Without a token nothing can connect — say so rather than showing a
        # mode that would fail on the first call.
        mode = raw_mode if (configured and raw_mode in MODES) else "disabled"
        can_mutate = mode in MUTATING_MODES
        environment = ("SANDBOX" if mode == "sandbox"
                       else "PRODUCTION" if mode in ("readonly", "live_readonly",
                                                     "live_trading")
                       else "—")
        note = {
            "disabled": "Токен не задан или tinvest.mode=disabled (§5.1).",
            "readonly": "Режим 2 §5.1: боевой счёт, только чтение — "
                        "заявки и стопы запрещены.",
            "sandbox": "Режим 3 §5.1: виртуальный счёт песочницы.",
            "live_readonly": "Режим 4 §5.1: боевой счёт, только чтение — "
                             "заявки и стопы запрещены.",
            "live_trading": "Режим 5 §5.1: боевой счёт, заявки разрешены. "
                            "Автоторговля требует отдельного включения "
                            "(autotrade.live_confirmed).",
        }.get(mode, "")
        return _ok(
            configured=configured,
            mode=mode,
            modes=list(MODES),
            read_only_modes=sorted(READ_ONLY_MODES),
            mutating_modes=sorted(MUTATING_MODES),
            environment=environment,
            can_read=mode != "disabled",
            can_mutate=can_mutate,
            # Kept for older callers: True only when real money can move.
            production_trading=(mode == "live_trading"),
            requested_mode=raw_mode,
            active_account=settings.get("active_account") or "",
            sync_seconds=settings.get("sync_seconds") or 60,
            auto_sync={
                "enabled": _auto_sync_state.get("enabled"),
                "last_sync": _auto_sync_state.get("last_sync"),
                "last_error": _auto_sync_state.get("last_error"),
            },
            note=note + " Токен читается только из окружения TINVEST_TOKEN.",
        )

    @app.route("/api/tinvest/accounts")
    def _tinvest_accounts():
        try:
            from tinvest.sandbox_accounts import list_accounts

            client = _tinvest_client()
            return _ok(accounts=[a.__dict__ for a in list_accounts(client)],
                       sandbox=client.sandbox, mode=client.mode,
                       environment=client.environment)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/accounts", methods=["POST"])
    def _tinvest_account_mutate():
        body = request.get_json(silent=True) or {}
        action = str(body.get("action") or "")
        try:
            from tinvest.sandbox_accounts import close_account, create_account

            client = _tinvest_client()
            if action == "create":
                account = create_account(client, str(body.get("currency") or "rub"))
                _set_active_account(account.id)
                return _ok(account=account.__dict__, virtual=True)
            if action == "close":
                aid = str(body.get("account_id") or _active_account())
                close_account(client, aid)
                if _active_account() == aid:
                    _set_active_account("")
                return _ok(closed=aid, virtual=True)
            if action == "select":
                _set_active_account(str(body.get("account_id") or ""))
                return _ok(active_account=_active_account())
            return _err(f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/payin", methods=["POST"])
    def _tinvest_payin():
        body = request.get_json(silent=True) or {}
        if not body.get("confirm"):
            return _err("explicit confirmation required for virtual pay-in", 409)
        account_id = str(body.get("account_id") or _active_account())
        if not account_id:
            return _err("account_id required")
        try:
            from tinvest.sandbox_accounts import pay_in

            client = _tinvest_client()
            result = pay_in(client, account_id, float(body.get("amount") or 0),
                            str(body.get("currency") or "rub"))
            balance = None
            try:
                from tinvest.portfolio import balance_view

                balance = balance_view(client, account_id)
            except Exception:  # noqa: BLE001
                pass
            return _ok(result=result, balance=balance, virtual=True)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/balance")
    def _tinvest_balance():
        try:
            from tinvest.portfolio import balance_view

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            return _ok(balance=balance_view(client, aid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/portfolio")
    def _tinvest_portfolio():
        try:
            from tinvest.portfolio import portfolio_context

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            return _ok(portfolio=portfolio_context(client, aid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/orders")
    def _tinvest_orders():
        try:
            from tinvest.orders import list_orders

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            rows = [o.__dict__ for o in list_orders(client, aid)]
            return _ok(orders=rows)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    def _build_preview(client, aid: str, proposal: dict) -> dict:
        """Shared preview pipeline for manual and report-derived proposals."""
        from moex.analytics import prepare_order
        from moex.config import load_settings
        from moex.instruments import resolve_instrument
        from moex.market_data import get_quote
        from tinvest.orders import list_orders, new_client_order_id
        from tinvest.portfolio import portfolio_context

        ticker = str(proposal.get("ticker") or "").strip().upper()
        inst = resolve_instrument(ticker)
        quote = get_quote(ticker)
        ctx = portfolio_context(client, aid)
        last = quote.get("last") or quote.get("close")
        if last:
            ctx["last_price"] = float(last)
        settings = load_settings()
        # T-Invest orders need the broker's own figi, not the MOEX SECID
        # ("Instrument not found" for "SBER" — live-verified).
        from tinvest.instruments import find_instrument_ref

        tref = find_instrument_ref(client, inst.secid, isin=inst.isin)
        proposal = dict(proposal)
        proposal["ticker"] = inst.secid
        proposal["figi"] = tref["figi"]
        proposal["instrument_uid"] = tref.get("uid") or None
        preview = prepare_order(
            proposal, ctx, settings,
            open_order_count=len(list_orders(client, aid)),
            lot_size=inst.lot_size or 1,
        )
        # Broker requires a strict UUID as client order id (code 30028).
        preview["client_order_id"] = new_client_order_id()  # idempotency (§5.6)
        preview["quote"] = quote
        preview["instrument"] = _instrument_row(inst)
        return preview

    @app.route("/api/tinvest/order/preview", methods=["POST"])
    def _tinvest_order_preview():
        body = request.get_json(silent=True) or {}
        ticker = str(body.get("ticker") or "").strip().upper()
        side = str(body.get("side") or "").strip().lower()
        if not ticker or side not in ("buy", "sell"):
            return _err("ticker and side (buy|sell) are required")
        try:
            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required — create or select a virtual account first")
            client = _tinvest_client()
            lots = body.get("lots")
            proposal = {
                "ticker": ticker,
                "side": side,
                "order_type": "limit" if str(body.get("order_type") or "market") == "limit" else "market",
                "lots": int(lots) if lots not in (None, "") else None,
                "limit_price": float(body["limit_price"]) if body.get("limit_price") else None,
                "stop_price": float(body["stop_price"]) if body.get("stop_price") else None,
            }
            return _ok(preview=_build_preview(client, aid, proposal))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order/from_report", methods=["POST"])
    def _tinvest_order_from_report():
        """Derive a sandbox proposal from an analyst report (spec §6.2).

        Parses BUY/SELL/HOLD + lots/limit-price hints via
        ``extract_proposal`` and runs the SAME preview pipeline as the
        manual form — risk verdict and explicit confirmation are still
        required before anything is placed; nothing is ever executed
        automatically.
        """
        body = request.get_json(silent=True) or {}
        ticker = str(body.get("ticker") or "").strip().upper()
        report = str(body.get("report") or "")
        if not ticker or not report:
            return _err("ticker and report (markdown text) are required")
        try:
            from moex.analytics import extract_proposal

            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required — create or select a virtual account first")
            client = _tinvest_client()
            prop = extract_proposal(report, ticker)
            if prop.side == "hold":
                # No actionable trade — do not mint a placeable preview.
                return _ok(hold=True, extracted=prop.to_dict(),
                           reason="В отчёте нет активного действия (HOLD)")
            proposal = {
                "ticker": prop.ticker,
                "side": prop.side,
                "order_type": prop.order_type,
                "lots": prop.lots or None,
                "limit_price": prop.limit_price,
                "stop_price": prop.stop_price,
                "rationale": prop.rationale,
            }
            preview = _build_preview(client, aid, proposal)
            preview["proposal"]["rationale"] = prop.rationale
            preview["proposal"]["stop_price"] = prop.stop_price
            preview["report_ref"] = prop.report_ref
            return _ok(preview=preview, extracted=prop.to_dict())
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order", methods=["POST"])
    def _tinvest_order():
        body = request.get_json(silent=True) or {}
        preview = body.get("preview") or {}
        # §13: explicit confirmation is the rule. The single exception is the
        # sandbox_auto_confirm switch, and it is bound to the venue: it only
        # counts when the configured mode is `sandbox`, so a sandbox setting
        # can never turn off confirmation on a real account (§1.1 item 14).
        # The venue is read from settings so the 409 is answered before any
        # client is built (and therefore before any network or token need).
        from moex.config import load_settings as _load

        settings = _load()
        configured_mode = str((settings.get("tinvest") or {}).get("mode") or "").lower()
        auto = (bool((settings.get("trading") or {}).get("sandbox_auto_confirm"))
                and configured_mode == "sandbox")
        if not (bool(body.get("confirm")) or auto):
            return _err("confirmation required — preview the order first", 409)
        try:
            from moex.analytics import place_confirmed_order
            from tinvest.orders import list_orders
            from tinvest.portfolio import portfolio_context

            client = _tinvest_client()
            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required")
            ctx = portfolio_context(client, aid)
            open_count = len(list_orders(client, aid))
            result = place_confirmed_order(
                client, aid, preview, confirm=True,
                portfolio_ctx=ctx, settings=settings,
                open_order_count=open_count,
            )
            result["open_orders_before"] = open_count
            if result.get("placed"):
                return _ok(order=result, sandbox=client.sandbox,
                           mode=client.mode, environment=client.environment)
            return _err(result.get("reason") or "not placed", 409)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order_state")
    def _tinvest_order_state():
        try:
            from moex.analytics import track_order

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            oid = request.args.get("order_id") or ""
            if not aid or not oid:
                return _err("account_id and order_id required")
            return _ok(state=track_order(client, aid, oid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order/cancel", methods=["POST"])
    def _tinvest_order_cancel():
        body = request.get_json(silent=True) or {}
        try:
            from tinvest.orders import cancel_order

            client = _tinvest_client()
            aid = str(body.get("account_id") or _active_account())
            oid = str(body.get("order_id") or "")
            if not aid or not oid:
                return _err("account_id and order_id required")
            return _ok(result=cancel_order(client, aid, oid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    def _quotation_amount(v) -> float | None:
        if isinstance(v, dict):
            try:
                return float(v.get("units") or 0) + float(v.get("nano") or 0) / 1e9
            except (TypeError, ValueError):
                return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _map_stop(s: dict) -> dict:
        direction = str(s.get("direction") or "")
        order_type = str(s.get("orderType") or "")
        status = str(s.get("status") or "")
        return {
            "stop_order_id": s.get("stopOrderId") or "",
            "ticker": s.get("ticker") or "",
            "figi": s.get("figi") or "",
            "direction": "sell" if "SELL" in direction else
                         ("buy" if "BUY" in direction else direction),
            "order_type": ("take_profit" if "TAKE_PROFIT" in order_type
                           else ("stop_loss" if "STOP_LOSS" in order_type
                                 else order_type)),
            "stop_price": _quotation_amount(s.get("stopPrice")),
            "limit_price": _quotation_amount(s.get("price")),
            "status": status.replace("STOP_ORDER_STATUS_", "").lower(),
            "lots": int(s.get("lotsRequested") or 0),
            "created_at": str(s.get("createDate") or "")[:19],
        }

    @app.route("/api/tinvest/stoporders")
    def _tinvest_stop_orders():
        try:
            from tinvest.stop_orders import list_stop_orders

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            rows = [_map_stop(s) for s in list_stop_orders(client, aid)]
            return _ok(stop_orders=rows,
                       caveat="Стоп-заявка не гарантирует исполнение по цене "
                              "стопа при гэпе или остановке торгов (§6.4).")
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/stoporder", methods=["POST"])
    def _tinvest_stop_order():
        """Place a native stop order — preview-style risk + confirm (§6.4)."""
        body = request.get_json(silent=True) or {}
        ticker = str(body.get("ticker") or "").strip().upper()
        direction = str(body.get("direction") or "").strip().lower()
        try:
            stop_price = float(body.get("stop_price") or 0)
        except (TypeError, ValueError):
            stop_price = 0.0
        if not ticker or direction not in ("buy", "sell") or stop_price <= 0:
            return _err("ticker, direction (buy|sell) and stop_price are required")
        if not body.get("confirm"):
            return _err("confirmation required — check the preview first", 409)
        try:
            from moex.analytics import prepare_order
            from moex.config import load_settings
            from moex.instruments import resolve_instrument
            from moex.market_data import get_quote
            from tinvest.instruments import find_instrument_ref
            from tinvest.portfolio import portfolio_context
            from tinvest.stop_orders import post_stop_order

            client = _tinvest_client()
            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required")
            inst = resolve_instrument(ticker)
            quote = get_quote(ticker)
            lots = body.get("lots")
            prop = {
                "ticker": inst.secid,
                "side": direction,
                "lots": int(lots) if lots not in (None, "") else None,
                "limit_price": stop_price,
            }
            settings = load_settings()
            # Reuse the standard preview sizing/risk pipeline so a bare
            # SELL stop defaults to the held position and shorts die here.
            preview = prepare_order(prop, portfolio_context(client, aid),
                                    settings, lot_size=inst.lot_size or 1)
            sized = preview["proposal"]
            if not sized.get("lots"):
                return _err("stop order requires lots (no held position?)", 400)
            risk = preview["risk"]
            if not risk.get("allowed"):
                return _err("risk check failed: " +
                            "; ".join(risk.get("violations") or []), 409)
            tref = find_instrument_ref(client, inst.secid, isin=inst.isin)
            result = post_stop_order(
                client,
                account_id=aid,
                figi=tref["figi"],
                direction=direction,
                quantity_lots=int(sized["lots"]),
                stop_price=stop_price,
                order_type=str(body.get("order_type") or "stop_loss"),
                limit_price=float(body["limit_price"])
                if body.get("limit_price") else None,
            )
            return _ok(stop_order=result, preview=sized)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/stoporder/cancel", methods=["POST"])
    def _tinvest_stop_order_cancel():
        body = request.get_json(silent=True) or {}
        try:
            from tinvest.stop_orders import cancel_stop_order

            client = _tinvest_client()
            aid = str(body.get("account_id") or _active_account())
            oid = str(body.get("stop_order_id") or "")
            if not aid or not oid:
                return _err("account_id and stop_order_id required")
            return _ok(result=cancel_stop_order(client, aid, oid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/operations")
    def _tinvest_operations():
        try:
            from tinvest.operations import list_operations

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            return _ok(operations=list_operations(client, aid, limit=100))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/dashboard")
    def _tinvest_dashboard():
        """Sandbox dashboard: balance, positions, closed deals, PnL windows."""
        try:
            from moex.analytics import build_dashboard

            client = _tinvest_client()
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            return _ok(dashboard=build_dashboard(client, aid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/sync", methods=["POST"])
    def _tinvest_sync():
        body = request.get_json(silent=True) or {}
        try:
            from tinvest.reconciliation import sync

            client = _tinvest_client()
            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required")
            return _ok(snapshot=sync(client, aid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    # integration helpers reused by the run flow (market selector)
    @app.route("/api/moex/validate")
    def _moex_validate():
        from moex.instruments import InstrumentNotFound, resolve_instrument

        ticker = (request.args.get("ticker") or "").strip().upper()
        try:
            inst = resolve_instrument(ticker)
            return _ok(instrument=_instrument_row(inst) | {"lot_size": inst.lot_size})
        except InstrumentNotFound as exc:
            return _err(exc, 404)

    # ---------------------------------------------------------- §5.1 mode
    @app.route("/api/tinvest/mode", methods=["GET", "POST"])
    def _tinvest_mode():
        """Read or set the §5.1 connection mode.

        Selecting ``live_trading`` additionally needs ``confirm_live: true``
        in the same request: real-money routing is never a side effect of
        saving a form (spec §1.1 item 14, §6.5).
        """
        from tinvest.client import MODES

        if request.method == "GET":
            current = str(_settings().get("tinvest", {}).get("mode") or "")
            return _ok(mode=current if current in MODES else "disabled",
                       modes=list(MODES),
                       descriptions=_MODE_DESCRIPTIONS)

        body = request.get_json(silent=True) or {}
        requested = str(body.get("mode") or "").lower()
        if requested not in MODES:
            return _err(f"unknown mode {requested!r}; use one of {', '.join(MODES)}",
                        400)
        if requested == "live_trading" and not bool(body.get("confirm_live")):
            return _err("боевой режим требует confirm_live: true — "
                        "реальные деньги не включаются одной галочкой", 409)
        from moex.config import update_settings as _update

        saved = _update("tinvest", {"mode": requested})
        journal_entry = {
            "event": "mode_changed", "mode": requested,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            from moex.autotrade import journal_append as _ja

            _ja(journal_entry)
        except Exception:  # noqa: BLE001 — audit trail must not block a save
            pass
        return _ok(mode=saved.get("tinvest", {}).get("mode"),
                   modes=list(MODES),
                   descriptions=_MODE_DESCRIPTIONS)

    # -------------------------------------------------------- auto-trading
    @app.route("/api/autotrade", methods=["GET", "POST"])
    def _autotrade_settings():
        try:
            import integration.autotrade_service as ats

            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                ats.update_settings(body)
            return _ok(**ats.status())
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/autotrade/run", methods=["POST"])
    def _autotrade_run():
        """Run one cycle right now, ignoring the schedule.

        ``{"force": true}`` drops only the ``enabled`` gate, so a cycle can
        be rehearsed with the master switch off; the kill switch, venue
        match, live confirmations and budgets all still apply.
        """
        try:
            import integration.autotrade_service as ats

            body = request.get_json(silent=True) or {}
            return _ok(cycle=ats.cycle(force=bool(body.get("force"))))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/autotrade/screener")
    def _autotrade_screener():
        """Предпросмотр отбора скринера — кандидаты и разбивка отказов.

        Без LLM и без заявок: кэшированная вселенная борда + чистая
        логика moex.screener. Показывает, что выберет следующий цикл.
        """
        try:
            import integration.autotrade_service as ats

            return _ok(**ats.screener_preview())
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    _start_auto_sync(app)
    _start_autotrade(app)


def _instrument_row(inst) -> dict:
    return {
        "secid": inst.secid,
        "isin": inst.isin or "",
        "short_name": inst.short_name or "",
        "full_name": inst.full_name or "",
        "instrument_type": getattr(inst.instrument_type, "value", inst.instrument_type),
        "market": getattr(inst.market, "value", inst.market),
        "board_id": inst.board_id,
        "currency": inst.currency or "",
        "status": inst.status or "",
        "prev_close": inst.prev_close,
    }
