"""§5.1 connection modes and the auto-trading gate chain.

The spec fixes both halves of auto-trading: *which venue we are talking to*
(§5.1) and *whether a cycle may spend anything at all* (§1.1 item 14, §6.5,
§13). Neither half touches the network here — the mode is a pure function of
the settings, and the gates take their runtime facts as arguments.
"""

from __future__ import annotations

import pytest

from moex.autotrade import (
    Gate,
    already_decided,
    decisions_today,
    evaluate_gates,
    first_blocked,
    gates_summary,
    journal_append,
    orders_today,
    resolve_environment,
    today_msk,
)
from tinvest.client import MODES, MUTATING_MODES, READ_ONLY_MODES, TInvestError


# --------------------------------------------------------------------------
# §5.1 modes
# --------------------------------------------------------------------------

def test_spec_lists_exactly_five_modes():
    assert MODES == ("disabled", "readonly", "sandbox", "live_readonly",
                     "live_trading")
    assert READ_ONLY_MODES == frozenset({"readonly", "live_readonly"})
    assert MUTATING_MODES == frozenset({"sandbox", "live_trading"})


@pytest.mark.parametrize("mode,expect_sandbox,expect_mutate,expect_env", [
    ("readonly", False, False, "PRODUCTION"),
    ("sandbox", True, True, "SANDBOX"),
    ("live_readonly", False, False, "PRODUCTION"),
    ("live_trading", False, True, "PRODUCTION"),
])
def test_mode_routes_to_its_own_venue(mode, expect_sandbox, expect_mutate,
                                      expect_env):
    from tinvest.client import TInvestClient

    client = TInvestClient("unit-test-token", mode=mode)
    assert client.sandbox is expect_sandbox
    assert client.can_mutate is expect_mutate
    assert client.environment == expect_env
    assert client.can_read is True
    # A production read-only client still points at the production API.
    if not expect_sandbox:
        assert client.base_url.startswith("https://invest-public-api.tinkoff.ru")
    else:
        assert client.base_url.startswith("https://sandbox-invest-public-api.tinkoff.ru")


def test_read_only_modes_refuse_every_mutation():
    from tinvest.client import TInvestClient

    for mode in sorted(READ_ONLY_MODES):
        client = TInvestClient("unit-test-token", mode=mode)
        with pytest.raises(TInvestError, match="read-only"):
            client.require_mutation("post_order")
        with pytest.raises(TInvestError, match="read-only"):
            client.require_mutation("post_stop_order")
        with pytest.raises(TInvestError, match="read-only"):
            client.require_mutation("cancel_order")


def test_disabled_mode_cannot_be_constructed(monkeypatch):
    from tinvest.client import TInvestClient

    monkeypatch.setenv("TINVEST_TOKEN", "unit-test-token")
    with pytest.raises(TInvestError, match="disabled"):
        TInvestClient.from_mode("disabled")
    with pytest.raises(TInvestError, match="unknown"):
        TInvestClient("unit-test-token", mode="paper")


def test_from_mode_is_none_without_a_token(monkeypatch):
    from tinvest.client import TInvestClient

    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_TINVEST_TOKEN", raising=False)
    assert TInvestClient.from_mode("sandbox") is None


def test_mode_info_reports_everything_the_ui_needs():
    from tinvest.client import TInvestClient

    info = TInvestClient("unit-test-token", mode="live_trading").mode_info()
    assert info == {
        "mode": "live_trading",
        "environment": "PRODUCTION",
        "sandbox": False,
        "can_read": True,
        "can_mutate": True,
        "base_url": info["base_url"],
    }
    assert info["base_url"].startswith("https://invest-public-api.")


# --------------------------------------------------------------------------
# journal helpers (the "one decision per ticker per day" rule)
# --------------------------------------------------------------------------

def test_journal_dedupes_by_moscow_day_and_environment(isolated_home):
    day = today_msk()
    journal_append({"event": "decision", "date": day, "environment": "sandbox",
                    "ticker": "SBER"})
    journal_append({"event": "order", "date": day, "environment": "sandbox",
                    "ticker": "SBER"})
    # A second decision for the same ticker/day must be seen as already made.
    assert already_decided("sber", "sandbox") is True
    assert already_decided("SBER", "live") is False  # other venue, free to run
    assert len(decisions_today("sandbox")) == 1
    assert len(orders_today("sandbox")) == 1


def test_journal_is_redirectable_by_tradingagents_home(monkeypatch, tmp_path):
    from moex.autotrade import journal_append, journal_path

    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    journal_append({"event": "unit-test", "ticker": "SBER"})
    assert journal_path() == tmp_path / "tinvest" / "autotrade_journal.jsonl"
    assert journal_path().exists()


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def _base(**autotrade):
    cfg = {
        "enabled": True, "environment": "sandbox", "watchlist": ["SBER"],
        "max_orders_per_day": 3, "max_open_positions": 3,
        "only_when_market_open": False, "live_confirmed": False,
    }
    cfg.update(autotrade)
    return {"autotrade": cfg, "tinvest": {"mode": "sandbox"},
            "risk": {"automation_sandbox_only": True}}


def _open(settings, **facts):
    kwargs = {
        "mode": "sandbox", "client_ok": True, "client_can_mutate": True,
        "portfolio_ok": True, "market_open": True, "analysis_running": False,
        "open_positions": 0, "daily_realized_pnl": 0.0,
    }
    kwargs.update(facts)
    return evaluate_gates(settings, **kwargs)


def test_engine_is_off_by_default_and_says_why():
    gates = evaluate_gates({}, mode="sandbox")
    summary = gates_summary(gates)
    assert summary["ok"] is False
    assert summary["blocked_by"] == "enabled"
    assert "autotrade.enabled = false" in summary["reason"]


def test_kill_switch_blocks_without_disabling_the_engine():
    gates = _open(_base(kill_switch=True))
    blocked = first_blocked(gates)
    assert blocked is not None and blocked.name == "kill_switch"
    # `enabled` is untouched, so resuming is one deliberate switch-off away.
    assert gates[0].name == "enabled" and gates[0].ok


def test_sandbox_target_needs_the_sandbox_mode():
    # Wants the sandbox but is pointed at a production read-only mode:
    # the venue gate trips first, before anything is even discussed.
    gates = _open(_base(), mode="readonly")
    assert first_blocked(gates).name == "environment"
    # Wants the sandbox but is pointed at live trading — still the venue gate.
    gates = _open(_base(), mode="live_trading")
    assert first_blocked(gates).name == "environment"
    # Right venue, right mode, but a client that refuses to write
    # (the gate takes facts, so this is what a degraded session looks like).
    gates = _open(_base(), mode="sandbox", client_can_mutate=False)
    assert first_blocked(gates).name == "read_write"


def test_read_only_modes_can_never_run_a_cycle():
    settings = _base(environment="live")
    settings["autotrade"]["live_confirmed"] = True
    settings["risk"] = {"automation_sandbox_only": False}
    settings["tinvest"] = {"mode": "live_trading"}
    for mode in sorted(READ_ONLY_MODES):
        # Env/mode mismatch and no mutation rights both fail closed.
        gates = _open(settings, mode=mode, client_can_mutate=False)
        assert first_blocked(gates) is not None
        assert {g.name for g in gates if not g.ok} & {"environment", "read_write"}


def test_missing_token_fails_closed_with_a_reason():
    gates = _open(_base(), client_ok=False, client_can_mutate=False,
                  portfolio_ok=False, client_error="TINVEST_TOKEN не задан")
    summary = gates_summary(gates)
    assert summary["blocked_by"] == "read_write"
    assert "TINVEST_TOKEN" in summary["reason"]


def test_unreadable_portfolio_blocks_before_any_money_moves():
    gates = _open(_base(), portfolio_ok=False, client_error="401 от брокера")
    assert gates_summary(gates)["blocked_by"] == "portfolio_readable"
    assert "401" in first_blocked(gates).detail


def test_live_cycle_needs_three_separate_switches():
    settings = _base(environment="live")
    settings["tinvest"] = {"mode": "live_trading"}
    settings["risk"] = {"automation_sandbox_only": True}  # §13 default

    # Only the venue is right → the first missing confirmation trips.
    gates = _open(settings, mode="live_trading", client_can_mutate=True)
    assert first_blocked(gates).name == "live_confirmed"
    # Confirming the live cycle alone is still not enough: §13 keeps the
    # automation scoped to the sandbox until that switch is also flipped.
    settings["autotrade"]["live_confirmed"] = True
    gates = _open(settings, mode="live_trading", client_can_mutate=True)
    assert first_blocked(gates).name == "automation_scope"
    # All three set → nothing blocks.
    settings["risk"] = {"automation_sandbox_only": False}
    gates = _open(settings, mode="live_trading", client_can_mutate=True)
    assert gates_summary(gates)["ok"] is True
    # And a sandbox choice never implies any of the three.
    settings["autotrade"]["live_confirmed"] = False
    settings["risk"] = {"automation_sandbox_only": True}
    gates = _open(settings, mode="sandbox")
    assert first_blocked(gates).name == "environment"


def test_live_requires_live_trading_mode_and_production_client():
    settings = _base(environment="live")
    settings["autotrade"]["live_confirmed"] = True
    settings["risk"] = {"automation_sandbox_only": False}
    gates = _open(settings, mode="sandbox")  # wants live, mode is sandbox
    assert first_blocked(gates).name == "environment"
    gates = _open(settings, mode="live_trading")
    assert first_blocked(gates) is None or first_blocked(gates).name != "environment"


def test_market_gate_can_be_switched_off_explicitly():
    gates = _open(_base(only_when_market_open=False), market_open=False)
    assert "market_open" not in [g.name for g in gates]
    gates = _open(_base(only_when_market_open=True), market_open=False,
                  market_detail="статус normal_close")
    assert first_blocked(gates).name == "market_open"
    assert "normal_close" in first_blocked(gates).detail


def test_budgets_are_compared_against_the_moscow_day(isolated_home):
    day = today_msk()
    for _ in range(3):
        journal_append({"event": "order", "date": day, "environment": "sandbox",
                        "ticker": "SBER"})
    gates = _open(_base())  # max_orders_per_day = 3, already placed 3
    assert gates_summary(gates)["blocked_by"] == "orders_budget"
    # A zero limit means "no trading today", never "unlimited".
    gates = _open(_base(max_orders_per_day=0))
    assert first_blocked(gates).name == "orders_budget"


def test_open_positions_budget_blocks_new_positions():
    gates = _open(_base(), open_positions=3)
    assert gates_summary(gates)["blocked_by"] == "positions_budget"
    gates = _open(_base(max_open_positions=1), open_positions=0)
    assert gates_summary(gates)["ok"] is True


def test_a_running_analysis_skips_the_cycle():
    gates = _open(_base(), analysis_running=True)
    assert first_blocked(gates).name == "analysis_idle"


def test_daily_loss_stop_trips_on_realized_pnl():
    settings = _base()
    settings["risk"] = {"daily_loss_limit": -500}
    gates = _open(settings, daily_realized_pnl=-500.0)
    assert first_blocked(gates).name == "daily_loss"
    assert "превышен" in first_blocked(gates).detail
    gates = _open(settings, daily_realized_pnl=-100.0)
    assert first_blocked(gates) is None


def test_empty_watchlist_blocks():
    gates = _open(_base(watchlist=[]))
    assert first_blocked(gates).name == "watchlist"


def test_gates_are_ordered_and_serialisable():
    gates = _open(_base())
    names = [g.name for g in gates]
    assert names[0] == "enabled" and "analysis_idle" in names
    assert all(isinstance(g, Gate) for g in gates)
    assert all(set(g.to_dict()) == {"name", "ok", "detail"} for g in gates)
    # An all-green chain reports no blocker at all.
    assert gates_summary(gates)["blocked_by"] is None


def test_resolve_environment_defaults_to_sandbox_and_validates():
    assert resolve_environment({}) == "sandbox"
    assert resolve_environment({"autotrade": {"environment": "live"}}) == "live"
    assert resolve_environment({"autotrade": {"environment": "paper"}}) == "sandbox"
