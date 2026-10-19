from __future__ import annotations

from moex.risk import DEFAULT_RISK, check_proposal, effective_limits, size_position


def _proposal(**kw):
    base = {"side": "buy", "ticker": "SBER", "lots": 10, "order_type": "limit",
            "limit_price": 300.0, "estimated_amount": 3000.0}
    base.update(kw)
    return base


def test_effective_limits_defaults():
    limits = effective_limits(None)
    assert limits == DEFAULT_RISK


def test_effective_limits_overrides_only_known_keys():
    settings = {"risk": {"max_order_amount": 123.0, "legacy_key": 999}}
    limits = effective_limits(settings)
    assert limits["max_order_amount"] == 123.0
    assert "legacy_key" not in limits


def test_settings_defaults_use_effective_key_names():
    from moex.config import DEFAULT_SETTINGS

    risk_keys = set(DEFAULT_SETTINGS["risk"])
    assert risk_keys <= set(DEFAULT_RISK)


def test_hold_is_always_allowed(sandbox_ctx):
    result = check_proposal(_proposal(side="hold"), sandbox_ctx, None)
    assert result.allowed


def test_small_buy_allowed(sandbox_ctx):
    result = check_proposal(_proposal(), sandbox_ctx, None)
    assert result.allowed, result.violations


def test_buy_over_max_order_amount_rejected(sandbox_ctx):
    result = check_proposal(
        _proposal(lots=2000, estimated_amount=600_000.0), sandbox_ctx, None
    )
    assert not result.allowed
    assert any("максимума" in v for v in result.violations)


def test_buy_over_cash_rejected(sandbox_ctx):
    result = check_proposal(
        _proposal(lots=5000, estimated_amount=1_500_000.0), sandbox_ctx, None
    )
    assert not result.allowed
    assert any("средств" in v for v in result.violations)


def test_position_concentration_rejected(sandbox_ctx):
    result = check_proposal(
        _proposal(lots=2000, estimated_amount=600_000.0), sandbox_ctx, None
    )
    assert not result.allowed
    assert any("составит" in v for v in result.violations)


def test_sell_without_position_rejected(sandbox_ctx):
    result = check_proposal(_proposal(side="sell", ticker="GAZP"), sandbox_ctx, None)
    assert not result.allowed
    assert any("позиция не найдена" in v for v in result.violations)


def test_sell_of_held_position_allowed(sandbox_ctx):
    result = check_proposal(_proposal(side="sell", ticker="SBER"), sandbox_ctx, None)
    assert result.allowed, result.violations


def test_allowed_instruments_whitelist(sandbox_ctx):
    settings = {"risk": {"allowed_instruments": ["GAZP"]}}
    result = check_proposal(_proposal(), sandbox_ctx, settings)
    assert not result.allowed


def test_buys_disabled(sandbox_ctx):
    settings = {"risk": {"allow_new_buys": False}}
    result = check_proposal(_proposal(), sandbox_ctx, settings)
    assert not result.allowed


def test_open_order_limit(sandbox_ctx):
    result = check_proposal(_proposal(), sandbox_ctx, None, open_order_count=5)
    assert not result.allowed
    assert any("заявок" in v for v in result.violations)


def test_non_sandbox_context_rejected():
    ctx = {"sandbox": False, "cash": 10_000_000.0, "total_value": 10_000_000.0,
           "positions": []}
    result = check_proposal(_proposal(), ctx, None)
    assert not result.allowed
    # No mode configured → fail closed, and the message names the way out.
    assert any("live_trading" in v for v in result.violations)
    assert any("§5.1" in v for v in result.violations)


def test_size_position_caps_by_cash_and_pct():
    ctx = {"sandbox": True, "cash": 100_000.0, "total_value": 1_000_000.0,
           "positions": []}
    sizing = size_position("buy", 300.0, ctx, None, lot_size=10)
    assert sizing["lots"] >= 1
    amount = sizing["lots"] * 300.0
    assert amount <= 100_000.0
    assert amount / 1_000_000.0 * 100 <= 30.0 + 1e-9


def test_daily_loss_limit_violated_by_realized_losses(tmp_path, monkeypatch,
                                                      sandbox_ctx):
    import json
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    lessons = tmp_path / "tinvest" / "lessons.jsonl"
    lessons.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=3))).isoformat()
    row = {"id": "x1", "created_at": today, "realized_pnl": -120.0,
           "enabled": True, "used_in_future_runs": False,
           "proposal": {"ticker": "SBER", "side": "sell"},
           "execution": {}, "explanation": "loss"}
    lessons.write_text(json.dumps(row) + "\n", encoding="utf-8")

    settings = {"risk": {"daily_loss_limit": 100.0}}
    result = check_proposal(_proposal(), sandbox_ctx, settings)
    assert not result.allowed
    assert any("дневной лимит потерь" in v for v in result.violations)


def test_daily_loss_limit_ok_when_within_budget(tmp_path, monkeypatch,
                                                sandbox_ctx):
    import json
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    lessons = tmp_path / "tinvest" / "lessons.jsonl"
    lessons.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=3))).isoformat()
    row = {"id": "x1", "created_at": today, "realized_pnl": -40.0,
           "enabled": True, "used_in_future_runs": False,
           "proposal": {"ticker": "SBER", "side": "sell"},
           "execution": {}, "explanation": "small loss"}
    lessons.write_text(json.dumps(row) + "\n", encoding="utf-8")

    settings = {"risk": {"daily_loss_limit": 100.0}}
    result = check_proposal(_proposal(), sandbox_ctx, settings)
    assert result.allowed, result.violations
    assert any("realized PnL" in w for w in result.warnings)


def test_daily_loss_limit_not_enforced_when_unset(tmp_path, monkeypatch,
                                                  sandbox_ctx):
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path))
    result = check_proposal(_proposal(), sandbox_ctx, None)
    assert result.allowed, result.violations
    assert not any("дневной лимит" in v for v in result.violations)


# --------------------------------------------------------------------------
# venue (§5.1) — a production context is only tradeable in live_trading
# --------------------------------------------------------------------------

def _production_ctx(sandbox_ctx):
    ctx = dict(sandbox_ctx)
    ctx["sandbox"] = False
    return ctx


def test_production_context_is_refused_without_live_trading(sandbox_ctx):
    for settings in (None, {"tinvest": {"mode": "sandbox"}},
                     {"tinvest": {"mode": "readonly"}},
                     {"tinvest": {"mode": "disabled"}}):
        result = check_proposal(_proposal(), _production_ctx(sandbox_ctx), settings)
        assert not result.allowed
        assert any("live_trading" in v for v in result.violations), result.violations


def test_production_context_is_allowed_in_live_trading(sandbox_ctx):
    settings = {"tinvest": {"mode": "live_trading"}}
    result = check_proposal(_proposal(), _production_ctx(sandbox_ctx), settings)
    assert result.allowed, result.violations


def test_automation_scope_does_not_gate_the_manual_venue(sandbox_ctx):
    # §13's switch is about automation, not about which broker is reachable;
    # it stays enforced by the auto-trading gates instead.
    settings = {"tinvest": {"mode": "live_trading"},
                "risk": {"automation_sandbox_only": True}}
    result = check_proposal(_proposal(), _production_ctx(sandbox_ctx), settings)
    assert result.allowed, result.violations


def test_sandbox_context_needs_no_mode(sandbox_ctx):
    # Sandbox keeps working with no settings at all — the default install.
    assert check_proposal(_proposal(), sandbox_ctx, None).allowed
