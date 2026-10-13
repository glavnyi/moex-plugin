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


def _ok(**kw):
    return jsonify({"ok": True, **kw}), 200


def _err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": str(message)}), status


def _settings():
    from moex.config import load_settings

    return load_settings()


def _tinvest_client(sandbox: bool = True):
    from tinvest.client import TInvestClient, TInvestError

    mode = str((_settings().get("tinvest") or {}).get("mode") or "").lower()
    if mode in ("live_trading", "production", "live"):
        raise TInvestError(
            "live_trading is refused in this build (§6.5): set tinvest.mode "
            "to sandbox/readonly/disabled"
        )
    if not sandbox and mode in ("disabled", ""):
        raise TInvestError("tinvest.mode is 'disabled' — nothing to connect to")
    client = TInvestClient.from_settings(sandbox=sandbox)
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
            return _ok(limits=effective_limits(_settings()), defaults=DEFAULT_RISK)
        body = request.get_json(silent=True) or {}
        allowed = {k: v for k, v in body.items() if k in DEFAULT_RISK}
        update_settings("risk", allowed)
        return _ok(limits=effective_limits(_settings()))

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

        configured = bool(os.environ.get("TINVEST_TOKEN")
                          or os.environ.get("TRADINGAGENTS_TINVEST_TOKEN"))
        settings = _settings().get("tinvest") or {}
        mode = str(settings.get("mode") or "").lower()
        if mode == "live_trading":
            mode = "disabled"  # refused in this build (§6.5)
        elif mode not in ("disabled", "readonly", "sandbox", "live_readonly"):
            mode = "sandbox" if configured else "disabled"
        if not configured:
            mode = "disabled"
        return _ok(
            configured=configured,
            mode=mode if configured else "disabled",
            production_trading=False,
            active_account=settings.get("active_account") or "",
            sync_seconds=settings.get("sync_seconds") or 60,
            note="Реальная торговля в этой версии отключена (§6.5). "
                 "Токен читается только из окружения TINVEST_TOKEN.",
        )

    @app.route("/api/tinvest/accounts")
    def _tinvest_accounts():
        try:
            from tinvest.sandbox_accounts import list_accounts

            client = _tinvest_client(sandbox=True)
            return _ok(accounts=[a.__dict__ for a in list_accounts(client)],
                       sandbox=True)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/accounts", methods=["POST"])
    def _tinvest_account_mutate():
        body = request.get_json(silent=True) or {}
        action = str(body.get("action") or "")
        try:
            from tinvest.sandbox_accounts import close_account, create_account

            client = _tinvest_client(sandbox=True)
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

            client = _tinvest_client(sandbox=True)
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

            client = _tinvest_client(sandbox=True)
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

            client = _tinvest_client(sandbox=True)
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

            client = _tinvest_client(sandbox=True)
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            rows = [o.__dict__ for o in list_orders(client, aid)]
            return _ok(orders=rows)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order/preview", methods=["POST"])
    def _tinvest_order_preview():
        body = request.get_json(silent=True) or {}
        ticker = str(body.get("ticker") or "").strip().upper()
        side = str(body.get("side") or "").strip().lower()
        if not ticker or side not in ("buy", "sell"):
            return _err("ticker and side (buy|sell) are required")
        try:
            from moex.analytics import prepare_order
            from moex.config import load_settings
            from moex.instruments import resolve_instrument
            from moex.market_data import get_quote
            from tinvest.orders import list_orders
            from tinvest.portfolio import portfolio_context

            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required — create or select a virtual account first")
            client = _tinvest_client(sandbox=True)
            inst = resolve_instrument(ticker)
            quote = get_quote(ticker)
            ctx = portfolio_context(client, aid)
            last = quote.get("last") or quote.get("close")
            if last:
                ctx["last_price"] = float(last)
            settings = load_settings()
            lots = body.get("lots")
            proposal = {
                "ticker": inst.secid,
                "side": side,
                "order_type": "limit" if str(body.get("order_type") or "market") == "limit" else "market",
                "lots": int(lots) if lots not in (None, "") else None,
                "limit_price": float(body["limit_price"]) if body.get("limit_price") else None,
                "figi": inst.secid,
            }
            preview = prepare_order(
                proposal, ctx, settings,
                open_order_count=len(list_orders(client, aid)),
                lot_size=inst.lot_size or 1,
            )
            preview["client_order_id"] = "ta-" + preview["preview_id"]  # idempotency (§5.6)
            preview["quote"] = quote
            preview["instrument"] = _instrument_row(inst)
            return _ok(preview=preview)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order", methods=["POST"])
    def _tinvest_order():
        body = request.get_json(silent=True) or {}
        preview = body.get("preview") or {}
        # §13: explicit confirmation is the rule; the only exception is the
        # separately-stored sandbox_auto_confirm switch, which can only ever
        # reach this handler with sandbox=True + mode guard in _tinvest_client.
        from moex.config import load_settings as _load

        auto = bool((_load().get("trading") or {}).get("sandbox_auto_confirm"))
        confirm = bool(body.get("confirm")) or auto
        if not confirm:
            return _err("confirmation required — preview the order first", 409)
        try:
            from moex.analytics import place_confirmed_order
            from moex.config import load_settings
            from tinvest.orders import list_orders
            from tinvest.portfolio import portfolio_context

            client = _tinvest_client(sandbox=True)
            aid = str(body.get("account_id") or _active_account())
            if not aid:
                return _err("account_id required")
            ctx = portfolio_context(client, aid)
            settings = load_settings()
            open_count = len(list_orders(client, aid))
            result = place_confirmed_order(
                client, aid, preview, confirm=True,
                portfolio_ctx=ctx, settings=settings,
            )
            result["open_orders_before"] = open_count
            if result.get("placed"):
                return _ok(order=result, sandbox=True)
            return _err(result.get("reason") or "not placed", 409)
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/order_state")
    def _tinvest_order_state():
        try:
            from moex.analytics import track_order

            client = _tinvest_client(sandbox=True)
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

            client = _tinvest_client(sandbox=True)
            aid = str(body.get("account_id") or _active_account())
            oid = str(body.get("order_id") or "")
            if not aid or not oid:
                return _err("account_id and order_id required")
            return _ok(result=cancel_order(client, aid, oid))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/operations")
    def _tinvest_operations():
        try:
            from tinvest.operations import list_operations

            client = _tinvest_client(sandbox=True)
            aid = request.args.get("account_id") or _active_account()
            if not aid:
                return _err("account_id required")
            return _ok(operations=list_operations(client, aid, limit=100))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 400)

    @app.route("/api/tinvest/sync", methods=["POST"])
    def _tinvest_sync():
        body = request.get_json(silent=True) or {}
        try:
            from tinvest.reconciliation import sync

            client = _tinvest_client(sandbox=True)
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
