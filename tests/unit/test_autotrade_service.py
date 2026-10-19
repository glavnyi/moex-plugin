"""Runtime half of auto-trading: settings in, cycle out.

Everything here runs without a broker, an LLM or a schedule: the service
takes its world (token, market, portfolio) from helpers that the tests
replace with facts, and the cycle's only job is to honour the gate chain
and journal what it decided.
"""

from __future__ import annotations

import integration.autotrade_service as ats
import pytest


@pytest.fixture(autouse=True)
def clean_service(isolated_home):
    """Fresh process state and a network-free settings block per test."""
    from moex.config import load_settings, update_settings

    ats.reset_state_for_tests()
    original = dict(load_settings().get("autotrade") or {})
    update_settings("autotrade", {"only_when_market_open": False,
                                  "enabled": False, "watchlist": ["SBER"]})
    yield
    ats.reset_state_for_tests()
    update_settings("autotrade", original)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

def test_settings_whitelist_drops_unknown_keys():
    from moex.config import load_settings

    ats.update_settings({"enabled": False, "max_orders_per_day": 4,
                         "api_key": "drop-me", "watchlist": [" gap ", "sber"]})
    saved = load_settings()["autotrade"]
    assert "api_key" not in saved
    assert saved["max_orders_per_day"] == 4
    assert saved["watchlist"] == ["GAP", "SBER"]


def test_enabling_is_recorded_in_the_journal():
    from moex.autotrade import journal_entries

    ats.update_settings({"enabled": True})
    assert any(e.get("event") == "enabled" for e in journal_entries())
    ats.update_settings({"enabled": False})


def test_clearing_the_kill_switch_is_a_separate_recorded_act():
    from moex.autotrade import journal_entries

    ats.update_settings({"kill_switch": True})
    ats.update_settings({"kill_switch": False})
    assert any(e.get("event") == "kill_switch_cleared"
               for e in journal_entries())


def test_bogus_values_fall_back_instead_of_crashing():
    ats.update_settings({"environment": "paper", "max_orders_per_day": "many",
                         "watchlist": None})
    from moex.config import load_settings

    saved = load_settings()["autotrade"]
    assert saved["environment"] == "sandbox"
    assert saved["max_orders_per_day"] >= 1
    assert saved["watchlist"] == []


# --------------------------------------------------------------------------
# client / market helpers
# --------------------------------------------------------------------------

def test_client_refuses_modes_that_cannot_connect(monkeypatch):
    monkeypatch.setenv("TINVEST_TOKEN", "unit-test-token")
    for mode in ("", "disabled", "paper"):
        client, err = ats._client(mode)
        assert client is None and "§5.1" in err


def test_client_is_none_without_a_token(monkeypatch):
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_TINVEST_TOKEN", raising=False)
    client, err = ats._client("sandbox")
    assert client is None and "TINVEST_TOKEN" in err


def test_market_gate_fails_closed_when_the_status_cannot_be_read(monkeypatch):
    def boom():
        raise RuntimeError("ISS недоступен")

    monkeypatch.setattr("moex.market_data.get_market_status", boom, raising=False)
    open_now, detail = ats._market_open({"only_when_market_open": True})
    assert open_now is False and "ISS недоступен" in detail


def test_market_gate_can_be_skipped_explicitly(monkeypatch):
    monkeypatch.setattr("moex.market_data.get_market_status",
                        lambda: (_ for _ in ()).throw(AssertionError("no call")),
                        raising=False)
    assert ats._market_open({"only_when_market_open": False})[0] is True


# --------------------------------------------------------------------------
# one cycle
# --------------------------------------------------------------------------

def test_cycle_is_blocked_by_the_enabled_gate(isolated_home):
    record = ats.cycle()
    assert record["ok"] is False and record["blocked_by"] == "enabled"
    assert record["results"] == []
    from moex.autotrade import journal_entries

    assert any(e.get("event") == "cycle_blocked" for e in journal_entries())


def test_forced_cycle_still_honours_the_other_gates(isolated_home):
    ats.update_settings({"kill_switch": True})
    record = ats.cycle(force=True)
    assert record["blocked_by"] == "kill_switch"  # `enabled` was the only
    # thing force is allowed to drop.
    ats.update_settings({"kill_switch": False})


def test_forced_cycle_skips_when_the_account_is_unreadable(isolated_home, monkeypatch):
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_TINVEST_TOKEN", raising=False)
    record = ats.cycle(force=True)
    assert record["ok"] is False
    assert record["blocked_by"] in ("read_write", "portfolio_readable")
    assert record["results"] == []  # nothing was attempted


def test_cycle_rejects_a_concurrent_invocation(isolated_home):
    ats._state["cycle_in_flight"] = True
    try:
        assert ats.cycle() == {"ok": False, "skipped": "цикл уже выполняется"}
    finally:
        ats._state["cycle_in_flight"] = False


def test_cycle_journals_a_block_and_remembers_it(isolated_home):
    ats.cycle()
    status = ats.status()
    assert status["last_cycle"]["blocked_by"] == "enabled"
    assert status["last_cycle_at"]
    assert status["loop_started"] is False
    assert status["gates"]["blocked_by"] == "enabled"
    assert status["journal"]  # at least the block we just wrote


def test_analysis_parameters_look_like_a_hand_started_run():
    params = ats._analysis_params("sber", {"analysts": ["market"],
                                           "research_depth": 2,
                                           "language": "Russian"})
    assert params["ticker"] == "SBER"
    assert params["date"]  # the run is dated like any other
    assert params["market"] == "MOEX"
    assert params["analysts"] == ["market"]
    assert params["research_depth"] == 2
    assert params["output_language"] == "Russian"
    assert params["auto_trading"] is True  # provenance, §6.2 step 9
    assert params["triggered_by"] == "autotrade"


def test_gui_runs_resolves_the_served_module_not_a_second_import(monkeypatch):
    """``python -m gui.app`` runs the module as ``__main__``.

    A plain ``from gui.app import runs`` would execute the module a second
    time, handing the daemon a *different* RunManager than the HTTP API —
    invisible analyses and a permanent ``analysis_idle`` gate. The resolver
    must pick the module that owns the live Flask app.
    """
    import sys
    import types

    served = types.ModuleType("__main__")
    phantom_app = types.SimpleNamespace(import_name="gui.app")
    served_app = types.SimpleNamespace(import_name="__main__")
    served.runs, served.app = object(), served_app
    # A second copy of the module, as ``import gui.app`` would create.
    dup = types.ModuleType("gui.app")
    dup.runs, dup.app = object(), phantom_app

    monkeypatch.setitem(sys.modules, "__main__", served)
    monkeypatch.setitem(sys.modules, "gui.app", dup)

    assert ats._gui_runs() is served.runs
    assert ats._gui_runs() is not dup.runs


def test_gui_runs_falls_back_to_the_import_when_no_app_is_served(monkeypatch):
    """No ``__main__`` app (a CLI or a test client) → import ``gui.app``.

    ``__main__`` under pytest is the test runner: it has no ``app``, so the
    loop must skip it and take the imported copy.
    """
    import sys
    import types

    main = sys.modules.get("__main__")
    assert main is None or not hasattr(getattr(main, "app", None), "import_name")

    fake = types.ModuleType("gui.app")
    fake.runs, fake.app = object(), types.SimpleNamespace(import_name="gui.app")
    monkeypatch.setitem(sys.modules, "gui.app", fake)

    assert ats._gui_runs() is fake.runs


def test_open_positions_counts_only_real_lots():
    ctx = {"positions": [
        {"type": "currency", "ticker": "RUB", "quantity_lots": 100},
        {"type": "share", "ticker": "SBER", "quantity_lots": 3},
        {"type": "share", "ticker": "GAZP", "quantity_lots": 0},
    ]}
    assert ats._open_positions(ctx) == 1
    assert ats._holding(ctx, "sber") is True
    assert ats._holding(ctx, "GAZP") is False
    assert ats._open_positions(None) == 0


def test_status_reports_the_configured_environment(isolated_home):
    ats.update_settings({"environment": "live"})
    try:
        status = ats.status()
        assert status["environment"] == "live"
        assert status["environments"] == ["sandbox", "live"]
        assert status["settings"]["environment"] == "live"
        # Turning the engine on never turns on the live confirmation.
        ats.update_settings({"enabled": True})
        assert ats.status()["settings"]["live_confirmed"] is False
    finally:
        ats.update_settings({"environment": "sandbox", "enabled": False})


# --------------------------------------------------------------------------
# decision → order (the path a cycle actually spends money on)
# --------------------------------------------------------------------------

class _Prop:
    def __init__(self, side="buy", lots=1, order_type="market", limit_price=None):
        self.side, self.lots = side, lots
        self.order_type, self.limit_price = order_type, limit_price


def _place_env(monkeypatch, tmp_path, *, risk_allowed=True, placed=True):
    """Fake the broker and the risk engine, keep the journal real."""
    import integration.autotrade_service as ats
    import moex.analytics as an
    import tinvest.orders as ords
    import tinvest.portfolio as pf

    monkeypatch.setattr(ords, "_journal_path",
                        lambda: tmp_path / "orders_journal.jsonl")
    calls = {"place": [], "sync": []}
    monkeypatch.setattr(an, "extract_proposal",
                        lambda report, ticker: _Prop())
    monkeypatch.setattr(an, "prepare_order",
                        lambda prop, ctx, settings, open_order_count=0,
                        lot_size=1: {
                            "proposal": {"side": prop.side, "lots": prop.lots,
                                         "ticker": "SBER", "figi": "F",
                                         "lot_size": lot_size},
                            "environment": "SANDBOX",
                            "risk": {"allowed": risk_allowed,
                                     "violations": [] if risk_allowed
                                     else ["максимальная сумма"],
                                     "warnings": []},
                        })
    monkeypatch.setattr(an, "place_confirmed_order",
                        lambda client, aid, preview, *, confirm, portfolio_ctx,
                        settings, open_order_count=None:
                        calls["place"].append(
                            {"confirm": confirm, "preview": preview,
                             "open_order_count": open_order_count}) or
                        {"placed": placed, "order_id": "oid-1",
                         "state": "accepted"} if placed
                        else {"placed": False, "reason": "нет заявки"})
    monkeypatch.setattr(an, "sync_lessons_from_operations",
                        lambda client, aid: calls["sync"].append("lessons"))
    monkeypatch.setattr(ords, "list_orders", lambda client, aid: [])
    monkeypatch.setattr(pf, "portfolio_context",
                        lambda client, aid: {"sandbox": True, "positions": [],
                                             "cash": 1e6, "total_value": 1e6})
    # No ISS round-trip in unit tests (spec §14.4): quote + lot lookups are
    # stubbed, the production code paths are exercised by their own tests.
    monkeypatch.setattr(ats, "_inject_last_price", lambda ctx, ticker: None)
    monkeypatch.setattr(ats, "_lot_size", lambda ticker: 1)
    return calls


class _FakeBrokerClient:
    sandbox, mode, environment, can_mutate = True, "sandbox", "SANDBOX", True
    def require_mutation(self, what):
        pass


def test_place_for_places_a_confirmed_order_and_journals_it(
        monkeypatch, tmp_path, isolated_home):
    import moex.autotrade as mat
    import tinvest.reconciliation as recon

    calls = _place_env(monkeypatch, tmp_path)
    monkeypatch.setattr(recon, "sync",
                        lambda client, aid: calls["sync"].append("snapshot"))
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")
    monkeypatch.setattr(ats, "today_msk", lambda: "2026-10-16")

    out = ats._place_for(_FakeBrokerClient(), {}, {}, "SBER",
                         "DECISION: buy 1 lot", "run-1", "sandbox")
    assert out["placed"] is True and out["order_id"] == "oid-1"
    # §6.2 step 9 — the cycle, and only the gate chain, supplies the confirm.
    assert calls["place"][0]["confirm"] is True
    assert calls["place"][0]["preview"]["environment"] == "SANDBOX"
    # §5.7 — snapshot and lessons refreshed right after a placement.
    assert "snapshot" in calls["sync"] and "lessons" in calls["sync"]

    events = [e["event"] for e in mat.journal_entries()]
    assert events[:2] == ["decision", "order"]
    row = mat.journal_entries()[1]
    assert row["placed"] is True and row["order_id"] == "oid-1"
    assert row["environment"] == "sandbox" and row["mode"] == "sandbox"


def test_place_for_hold_never_builds_an_order(monkeypatch, tmp_path,
                                              isolated_home):
    import moex.autotrade as mat
    import moex.analytics as an

    _place_env(monkeypatch, tmp_path)
    monkeypatch.setattr(an, "extract_proposal",
                        lambda report, ticker: _Prop(side="hold"))
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")

    out = ats._place_for(_FakeBrokerClient(), {}, {}, "SBER", "hold", "run-1",
                         "sandbox")
    assert out["placed"] is False and out["action"] == "hold"
    events = [e["event"] for e in mat.journal_entries()]
    assert events == ["decision"]           # recorded, but nothing sent
    assert not (tmp_path / "orders_journal.jsonl").exists()


def test_place_for_refuses_when_risk_says_no(monkeypatch, tmp_path,
                                             isolated_home):
    import moex.autotrade as mat

    calls = _place_env(monkeypatch, tmp_path, risk_allowed=False)
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")

    out = ats._place_for(_FakeBrokerClient(), {}, {}, "SBER", "buy", "run-1",
                         "sandbox")
    assert out["placed"] is False
    assert "риск" in out["reason"] or out["violations"]
    assert calls["place"] == []                       # broker never touched
    events = [e["event"] for e in mat.journal_entries()]
    assert events == ["decision", "risk_blocked"]


def test_cycle_walks_the_watchlist_with_budget_and_dedup(monkeypatch, tmp_path,
                                                         isolated_home):
    """One ticker per day, cap respected, every outcome journaled."""
    import moex.autotrade as mat

    calls = _place_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")

    facts = {
        "client": _FakeBrokerClient(), "client_ok": True,
        "client_can_mutate": True, "client_error": "",
        "market_open": True, "market_detail": "торги идут",
        "portfolio_ok": True, "open_positions": 0, "ctx": {"positions": []},
    }
    monkeypatch.setattr(ats, "_gather_facts",
                        lambda settings, cfg, mode: facts)
    monkeypatch.setattr(ats, "_analysis_busy", lambda: False)

    ran: list[str] = []
    def fake_analysis(ticker, cfg):
        ran.append(ticker)
        return f"run-{ticker}", "DECISION"

    monkeypatch.setattr(ats, "_run_analysis", fake_analysis)
    ats.update_settings({
        "enabled": True, "watchlist": ["SBER", "GAZP"],
        "max_orders_per_day": 2, "only_when_market_open": False,
    })
    # SBER already has today's decision → skipped before any LLM call.
    mat.journal_append({"event": "decision", "date": mat.today_msk(),
                        "environment": "sandbox", "ticker": "SBER",
                        "action": "buy"})

    record = ats.cycle()
    assert record["ok"] is True, record
    assert ran == ["GAZP"], ran                      # SBER never started
    skipped = [r for r in record["results"] if r.get("skipped")]
    assert skipped and skipped[0]["ticker"] == "SBER"
    assert record["orders_today"] == 1

    events = [(e["event"], e.get("ticker")) for e in mat.journal_entries()]
    assert ("cycle_done", None) in events
    assert ("order", "GAZP") in events
    # Still one decision per ticker per day for tomorrow's cycle.
    assert "GAZP" in mat.decisions_today("sandbox")


# --------------------------------------------------------------------------
# screener + виды заявок (цель «Автоматический отбор инструментов»)
# --------------------------------------------------------------------------

def test_orders_today_counts_only_placed_new_position_orders(isolated_home):
    """Лимит расходуют только фактически отправленные новые/доборы/retry:
    отказ риска, закрытия и защитные ордера бюджет не тратят."""
    import moex.autotrade as mat

    day = mat.today_msk()

    def row(**kw):
        base = {"event": "order", "date": day, "environment": "sandbox",
                "ticker": "SBER", "placed": True}
        base.update(kw)
        mat.journal_append(base)

    row(kind="open", order_id="1")
    row(kind="increase", order_id="2")
    row(kind="retry", order_id="3")
    row(kind="close", order_id="4")               # закрытие — бесплатно
    row(kind="protective", order_id="5")          # стоп/тейк — бесплатно
    row(kind="open", placed=False, reason="нет")  # не отправлена
    row(order_id="6")                             # старый формат — считается
    orders = mat.orders_today("sandbox")
    assert {o["order_id"] for o in orders} == {"1", "2", "3", "6"}
    assert mat.order_budget("sandbox", 3) == {
        "used": 4, "limit": 3, "left": 0, "date": day}


def test_order_budget_reports_left_when_under_the_limit(isolated_home):
    import moex.autotrade as mat

    budget = mat.order_budget("sandbox", 3)
    assert budget["used"] == 0 and budget["left"] == 3


def test_screener_unlocks_the_watchlist_gate(isolated_home):
    from moex.autotrade import evaluate_gates, first_blocked

    settings = {"autotrade": {"enabled": True, "watchlist": []},
                "screener": {"enabled": True},
                "tinvest": {"mode": "sandbox"}}
    gates = evaluate_gates(settings, mode="sandbox", client_ok=True,
                           client_can_mutate=True, portfolio_ok=True,
                           market_open=True, open_positions=0,
                           now=None)
    blocked = first_blocked(gates)
    # Может упасть что-то другое (бюджеты <=0), но не watchlist.
    assert blocked is None or blocked.name != "watchlist"


def test_screener_settings_are_validated_and_saved(isolated_home):
    from moex.config import load_settings
    from moex.screener import DEFAULTS

    ats.update_settings({"screener": {"enabled": True,
                                      "max_candidates_per_day": 5,
                                      "min_turnover_rub": "garbage",
                                      "exclude": ["sber"]}})
    saved = load_settings()["screener"]
    assert saved["enabled"] is True
    assert saved["max_candidates_per_day"] == 5
    assert saved["min_turnover_rub"] == DEFAULTS["min_turnover_rub"]
    assert saved["exclude"] == ["SBER"]


def test_cycle_runs_the_screener_candidate(monkeypatch, tmp_path, isolated_home):
    import moex.autotrade as mat

    _place_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")
    facts = {
        "client": _FakeBrokerClient(), "client_ok": True,
        "client_can_mutate": True, "client_error": "",
        "market_open": True, "market_detail": "торги идут",
        "portfolio_ok": True, "open_positions": 0, "ctx": {"positions": []},
    }
    monkeypatch.setattr(ats, "_gather_facts", lambda s, c, m: facts)
    monkeypatch.setattr(ats, "_analysis_busy", lambda: False)
    ran: list[str] = []

    def fake_analysis(ticker, cfg):
        ran.append(ticker)
        return "run-x", "DECISION"

    monkeypatch.setattr(ats, "_run_analysis", fake_analysis)
    monkeypatch.setattr(ats, "_screener_candidates",
                        lambda s, c, e, ctx: [{"secid": "LKOH",
                                               "turnover_rub": 5e9,
                                               "spread_pct": 0.05,
                                               "last": 7000.0}])
    ats.update_settings({"enabled": True, "watchlist": [],
                         "max_orders_per_day": 3,
                         "only_when_market_open": False,
                         "screener": {"enabled": True}})

    record = ats.cycle()
    assert record["ok"] is True, record
    assert ran == ["LKOH"]                        # скринер сдал кандидата
    events = [(e["event"], e.get("ticker")) for e in mat.journal_entries()]
    assert ("screener_candidate", "LKOH") in events
    assert ("order", "LKOH") in events


def test_screener_funding_cap_is_journaled_and_survives_restarts(
        isolated_home):
    import moex.autotrade as mat

    day = mat.today_msk()
    for i in range(3):
        mat.journal_append({"event": "screener_candidate", "date": day,
                            "environment": "sandbox", "ticker": f"T{i}"})
    settings = {"autotrade": {"watchlist": [], "max_orders_per_day": 3},
                "screener": {"enabled": True, "max_candidates_per_day": 3}}
    got = ats._screener_candidates(settings, settings["autotrade"],
                                   "sandbox", {"positions": []})
    assert got == []                       # бюджет 3/3 — новые не финансируются
    assert "исчерпан" in (ats._state["last_scan"] or {}).get("note", "")


# --- _send_with_recovery: дубль-защита при сетевом сбое --------------------

class _Order:
    def __init__(self, cid, oid="oid-x",
                 status="EXECUTION_REPORT_STATUS_NEW"):
        self.client_order_id, self.order_id, self.status = cid, oid, status


def _preview(order_type="limit"):
    return {"client_order_id": "cid-1",
            "proposal": {"order_type": order_type},
            "environment": "SANDBOX"}


def test_recovery_adopts_the_order_the_broker_already_has(monkeypatch):
    import moex.analytics as an
    import tinvest.orders as ords

    posts: list = []
    monkeypatch.setattr(ords, "list_orders",
                        lambda c, a: [_Order("cid-1", "oid-broker")])
    monkeypatch.setattr(an, "place_confirmed_order",
                        lambda *a, **k: posts.append(1))
    result, recovered = ats._send_with_recovery(
        _FakeBrokerClient(), "acc", _preview(), portfolio_ctx={},
        settings={}, open_order_count=0)
    assert posts == []                       # повторная отправка не шла
    assert recovered is True and result["placed"] is True
    assert result["order_id"] == "oid-broker"


def test_market_order_is_never_resent_after_a_network_error(monkeypatch):
    import moex.analytics as an
    import tinvest.orders as ords

    posts: list = []
    monkeypatch.setattr(ords, "list_orders", lambda c, a: [])

    def boom(*a, **k):
        posts.append(1)
        raise ConnectionError("сеть упала")

    monkeypatch.setattr(an, "place_confirmed_order", boom)
    result, recovered = ats._send_with_recovery(
        _FakeBrokerClient(), "acc", _preview("market"), portfolio_ctx={},
        settings={}, open_order_count=0)
    assert len(posts) == 1                   # одна попытка, повтора нет
    assert result["placed"] is False and result["status_unknown"] is True
    assert recovered is False


def test_limit_order_checks_the_broker_before_resenting(monkeypatch):
    import moex.analytics as an
    import tinvest.orders as ords

    posts: list = []
    checks: list = []

    def fake_list(c, a):
        checks.append(1)
        return []

    def flaky(client, aid, preview, **kw):
        posts.append(preview["client_order_id"])
        if len(posts) == 1:
            raise ConnectionError("обрыв")
        return {"placed": True, "order_id": "oid-2", "state": "accepted"}

    monkeypatch.setattr(ords, "list_orders", fake_list)
    monkeypatch.setattr(an, "place_confirmed_order", flaky)
    result, recovered = ats._send_with_recovery(
        _FakeBrokerClient(), "acc", _preview("limit"), portfolio_ctx={},
        settings={}, open_order_count=0)
    assert posts == ["cid-1", "cid-1"]       # тот же client_order_id
    assert len(checks) == 2                   # проверка статуса ДО повтора
    assert result["placed"] is True and recovered is False


def test_broker_rejection_is_definitive_and_never_resent(monkeypatch):
    import moex.analytics as an
    import tinvest.orders as ords
    from tinvest.client import TInvestError

    posts: list = []
    monkeypatch.setattr(ords, "list_orders", lambda c, a: [])

    def reject(*a, **k):
        posts.append(1)
        raise TInvestError("30043: недостаточно средств")

    monkeypatch.setattr(an, "place_confirmed_order", reject)
    result, _ = ats._send_with_recovery(
        _FakeBrokerClient(), "acc", _preview("limit"), portfolio_ctx={},
        settings={}, open_order_count=0)
    assert len(posts) == 1
    assert result["placed"] is False and "отклонил" in result["reason"]


def test_uncheckable_broker_status_fails_closed(monkeypatch):
    import moex.analytics as an
    import tinvest.orders as ords

    posts: list = []
    monkeypatch.setattr(ords, "list_orders",
                        lambda c, a: (_ for _ in ()).throw(
                            ConnectionError("broker down")))
    monkeypatch.setattr(an, "place_confirmed_order",
                        lambda *a, **k: posts.append(1))
    result, recovered = ats._send_with_recovery(
        _FakeBrokerClient(), "acc", _preview("limit"), portfolio_ctx={},
        settings={}, open_order_count=0)
    assert posts == [] and result["status_unknown"] is True
    assert "проверить не удалось" in result["reason"]


def test_active_broker_order_on_the_ticker_blocks_a_second_send(
        monkeypatch, tmp_path, isolated_home):
    """§4 «защита от повторных покупок»: активная заявка по тикеру у
    брокера (по client_order_id из журнала) → повторная не уходит."""
    import types

    import moex.autotrade as mat
    import tinvest.orders as ords

    calls = _place_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")
    monkeypatch.setattr(ats, "today_msk", lambda: "2026-10-16")

    # Отправленная нами заявка по SBER есть в журнале...
    mat.journal_append({"event": "order", "date": "2026-10-16",
                        "environment": "sandbox", "ticker": "SBER",
                        "placed": True, "order_id": "oid-old",
                        "client_order_id": "cid-1"})
    # ...и она ещё открыта у брокера.
    monkeypatch.setattr(ords, "list_orders",
                        lambda c, a: [types.SimpleNamespace(
                            client_order_id="cid-1", order_id="oid-old",
                            status="NEW")])

    out = ats._place_for(_FakeBrokerClient(), {}, {}, "SBER",
                         "DECISION: buy 1 lot", "run-9", "sandbox")
    assert out["placed"] is False and "активная заявка" in out["reason"]
    assert calls["place"] == []            # отправки не было вообще
    skips = [r for r in mat.journal_entries()
             if r.get("event") == "decision" and r.get("reason")]
    assert any("активная заявка" in r["reason"] for r in skips)


def test_client_order_id_stays_a_strict_uuid(monkeypatch, tmp_path,
                                             isolated_home):
    """T-Invest принимает только RFC-4122 UUID (код 30028) — никаких
    префиксов с тикером; журнал хранит тот же cid, что ушёл брокеру."""
    import uuid as uuid_mod

    import moex.autotrade as mat

    calls = _place_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ats, "_account", lambda s: "acc-1")
    monkeypatch.setattr(ats, "today_msk", lambda: "2026-10-16")

    ats._place_for(_FakeBrokerClient(), {}, {}, "SBER",
                   "DECISION: buy 1 lot", "run-1", "sandbox")
    cid = calls["place"][0]["preview"]["client_order_id"]
    assert str(uuid_mod.UUID(cid)) == cid          # строгий UUID
    row = [r for r in mat.journal_entries() if r["event"] == "order"][-1]
    assert row["client_order_id"] == cid
    assert row["kind"] == "open"
