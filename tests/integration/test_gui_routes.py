from __future__ import annotations


def test_status_without_token(flask_app):
    r = flask_app.get("/api/tinvest/status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] and data["configured"] is False
    assert data["production_trading"] is False
    assert data["mode"] == "disabled"


def test_accounts_require_token(flask_app):
    r = flask_app.get("/api/tinvest/accounts")
    assert r.status_code == 400
    assert "TINVEST_TOKEN" in r.get_json()["error"]


def test_live_trading_mode_needs_explicit_confirmation(flask_app, monkeypatch):
    import moex.config as mcfg

    monkeypatch.setenv("TINVEST_TOKEN", "dummy-token-for-test")
    try:
        # Switching on real-money routing must be a two-field deliberate act.
        r = flask_app.post("/api/tinvest/mode", json={"mode": "live_trading"})
        assert r.status_code == 409
        assert "confirm_live" in r.get_json()["error"]
        assert mcfg.load_settings()["tinvest"]["mode"] != "live_trading"

        r = flask_app.post("/api/tinvest/mode",
                           json={"mode": "live_trading", "confirm_live": True})
        assert r.status_code == 200
        assert r.get_json()["mode"] == "live_trading"

        status = flask_app.get("/api/tinvest/status").get_json()
        assert status["mode"] == "live_trading"
        assert status["environment"] == "PRODUCTION"
        assert status["can_mutate"] is True
        assert status["production_trading"] is True
        assert "live_trading" in status["modes"]
    finally:
        mcfg.update_settings("tinvest", {"mode": "sandbox"})


def test_mode_route_rejects_unknown_modes(flask_app):
    r = flask_app.post("/api/tinvest/mode", json={"mode": "paper"})
    assert r.status_code == 400
    assert "unknown mode" in r.get_json()["error"]


def test_autotrade_routes_are_off_by_default(flask_app):
    r = flask_app.get("/api/autotrade")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] and data["settings"]["enabled"] is False
    assert data["environment"] == "sandbox"
    assert data["gates"]["blocked_by"] == "enabled"
    assert data["loop_started"] is False  # never spawned under TESTING

    # A forced cycle still honours every other gate.
    r2 = flask_app.post("/api/autotrade/run", json={"force": True})
    assert r2.status_code == 200
    assert r2.get_json()["cycle"]["ok"] is False
    assert r2.get_json()["cycle"]["blocked_by"] not in (None, "enabled")


def test_autotrade_settings_persist_and_whitelist(flask_app):
    import moex.config as mcfg

    try:
        r = flask_app.post("/api/autotrade",
                           json={"watchlist": ["sber", " gap "],
                                 "max_orders_per_day": 5,
                                 "ticker": "should-be-ignored"})
        assert r.status_code == 200
        saved = mcfg.load_settings()["autotrade"]
        assert saved["watchlist"] == ["SBER", "GAP"]
        assert saved["max_orders_per_day"] == 5
        assert "ticker" not in saved
        assert saved["enabled"] is False  # never implied by other fields
        assert saved["live_confirmed"] is False
    finally:
        mcfg.update_settings("autotrade", {"watchlist": ["SBER"],
                                           "max_orders_per_day": 3})


def test_risk_round_trip(flask_app):
    r = flask_app.get("/api/moex/risk")
    assert r.status_code == 200
    data = r.get_json()
    defaults = data["limits"]
    assert defaults["require_confirmation"] is True
    assert "sandbox_auto_confirm" in data  # §13 switch is surfaced (§6.10)

    r2 = flask_app.post("/api/moex/risk", json={"max_order_amount": 777.0})
    assert r2.status_code == 200
    assert r2.get_json()["limits"]["max_order_amount"] == 777.0
    again = flask_app.get("/api/moex/risk").get_json()["limits"]
    assert again["max_order_amount"] == 777.0
    assert again["max_open_orders"] == defaults["max_open_orders"]


def test_sandbox_auto_confirm_toggle(flask_app):
    import moex.config as mcfg

    before = mcfg.load_settings().get("trading") or {}
    original = bool(before.get("sandbox_auto_confirm"))
    try:
        r = flask_app.post("/api/moex/risk",
                           json={"sandbox_auto_confirm": True})
        assert r.status_code == 200
        assert r.get_json()["sandbox_auto_confirm"] is True
        # Persisted under "trading", sibling keys untouched.
        trading = mcfg.load_settings()["trading"]
        assert trading["sandbox_auto_confirm"] is True
        assert trading["require_order_confirmation"] == before.get(
            "require_order_confirmation", True)
        # Not a risk-section key.
        assert "sandbox_auto_confirm" not in (
            mcfg.load_settings().get("risk") or {})
    finally:
        mcfg.update_settings("trading",
                             {"sandbox_auto_confirm": original})
    assert flask_app.get("/api/moex/risk"
                         ).get_json()["sandbox_auto_confirm"] is original


def test_news_registry_lists_all_sources(flask_app):
    r = flask_app.get("/api/moex/news/registry")
    assert r.status_code == 200
    sources = r.get_json()["sources"]
    assert len(sources) >= 20
    ids = {s["id"] for s in sources}
    assert "moex-100" in ids  # MOEX — все новости
    assert "cbr-news" in ids
    unavailable = [s for s in sources if not s["available"]]
    # every unavailable source must carry an honest reason (§7.2)
    assert all(s["reason"] for s in unavailable)


def test_news_source_toggle_persists(flask_app):
    r = flask_app.post("/api/moex/news/settings", json={"sources": {"tass": False}})
    assert r.status_code == 200
    reg = flask_app.get("/api/moex/news/registry").get_json()["sources"]
    tass = next(s for s in reg if s["id"] == "tass")
    assert tass["enabled"] is False


def test_order_preview_requires_account(flask_app):
    r = flask_app.post("/api/tinvest/order/preview",
                       json={"ticker": "SBER", "side": "buy"})
    assert r.status_code == 400
    assert "account" in r.get_json()["error"].lower()


def test_place_order_requires_explicit_confirmation(flask_app):
    r = flask_app.post("/api/tinvest/order", json={"preview": {}, "confirm": False})
    assert r.status_code == 409
    assert "confirm" in r.get_json()["error"].lower()


def test_payin_requires_confirmation(flask_app):
    r = flask_app.post("/api/tinvest/payin", json={"amount": 100})
    assert r.status_code == 409


def test_sandbox_auto_confirm_only_skips_the_gate(flask_app):
    # default: still demands confirmation
    r = flask_app.post("/api/tinvest/order", json={"preview": {"ticker": "SBER"}})
    assert r.status_code == 409
    # stored sandbox-only switch (§13) passes the gate, then fails on the
    # next real check (no token) — nothing is executed without a live sandbox
    import moex.config as mcfg

    mcfg.update_settings("trading", {"sandbox_auto_confirm": True})
    r = flask_app.post("/api/tinvest/order", json={"preview": {"ticker": "SBER"}})
    assert r.status_code == 400
    assert "confirm" not in r.get_json()["error"].lower()


def test_ext_js_served(flask_app):
    r = flask_app.get("/api/moex_ext.js")
    assert r.status_code == 200
    assert r.content_type.startswith("application/javascript")
    body = r.get_data(as_text=True)
    assert "/api/analyze" in body  # market injection hook
    assert "tab-sandbox" not in body or "ti-banner" in body


def test_validate_requires_query(flask_app):
    r = flask_app.get("/api/moex/instruments")
    assert r.status_code == 400
