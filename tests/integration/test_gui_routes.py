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


def test_live_trading_mode_is_refused(flask_app, monkeypatch):
    import moex.config as mcfg

    mcfg.update_settings("tinvest", {"mode": "live_trading"})
    monkeypatch.setenv("TINVEST_TOKEN", "dummy-token-for-test")
    r = flask_app.get("/api/tinvest/accounts")
    assert r.status_code == 400
    assert "live_trading" in r.get_json()["error"]
    status = flask_app.get("/api/tinvest/status").get_json()
    assert status["mode"] == "disabled"  # refused → report disabled (§6.5)
    mcfg.update_settings("tinvest", {"mode": "sandbox"})


def test_risk_round_trip(flask_app):
    r = flask_app.get("/api/moex/risk")
    assert r.status_code == 200
    defaults = r.get_json()["limits"]
    assert defaults["require_confirmation"] is True

    r2 = flask_app.post("/api/moex/risk", json={"max_order_amount": 777.0})
    assert r2.status_code == 200
    assert r2.get_json()["limits"]["max_order_amount"] == 777.0
    again = flask_app.get("/api/moex/risk").get_json()["limits"]
    assert again["max_order_amount"] == 777.0
    assert again["max_open_orders"] == defaults["max_open_orders"]


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
