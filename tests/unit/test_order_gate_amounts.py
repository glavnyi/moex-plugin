"""Order-gate regressions: unknown amounts and the fresh open-order count.

Two live bugs, both on the §6.3 "check the limits right before sending"
contract:

* ``portfolio_context()`` carries no ``last_price`` (only the GUI preview
  added one), so an auto-trade market proposal had no price, its
  ``estimated_amount`` stayed empty and ``check_proposal`` — which guards
  every money limit with ``if amount and …`` — checked *nothing* and
  returned allowed. Reproduced on the real 2026-10-17 SBER report.
* ``place_confirmed_order`` re-ran the risk check with the default
  ``open_order_count=0``, so ``max_open_orders`` was enforced only by the
  preview: both callers read the open orders and threw the number away.

No network: the broker client, ``list_orders`` and ``post_order`` are faked.
"""

from __future__ import annotations

import pytest

from moex.analytics import place_confirmed_order, prepare_order
from moex.risk import check_proposal


# --------------------------------------------------------------------------
# unknown amount must block, not silently pass
# --------------------------------------------------------------------------

def _market_proposal(**kw):
    prop = {"side": "buy", "ticker": "SBER", "lots": 1, "order_type": "market",
            "limit_price": None, "estimated_amount": None}
    prop.update(kw)
    return prop


def test_unknown_amount_blocks_a_buy(sandbox_ctx):
    result = check_proposal(_market_proposal(), sandbox_ctx, None)
    assert not result.allowed
    assert any("сумма заявки неизвестна" in v for v in result.violations)


def test_unknown_amount_blocks_a_sell(sandbox_ctx):
    result = check_proposal(_market_proposal(side="sell"), sandbox_ctx, None)
    assert not result.allowed
    assert any("сумма заявки неизвестна" in v for v in result.violations)


def test_zero_amount_is_treated_as_unknown(sandbox_ctx):
    result = check_proposal(_market_proposal(estimated_amount=0.0), sandbox_ctx, None)
    assert not result.allowed


def test_hold_is_still_allowed_without_an_amount(sandbox_ctx):
    result = check_proposal(_market_proposal(side="hold"), sandbox_ctx, None)
    assert result.allowed, result.violations


def test_prepare_order_without_any_price_is_blocked():
    # No limit price, no quote, no held position → nothing to measure.
    ctx = {"sandbox": True, "cash": 1_000_000.0, "total_value": 1_000_000.0,
           "positions": []}
    preview = prepare_order(_market_proposal(), ctx, None)
    assert preview["proposal"]["estimated_amount"] is None
    assert not preview["risk"]["allowed"]


def test_prepare_order_prices_a_sell_from_the_held_position(sandbox_ctx):
    # portfolio_context() has no last_price — the held current_price must be
    # used so the amount (and therefore the limits) exist.
    preview = prepare_order(
        _market_proposal(side="sell", lots=None), sandbox_ctx, None)
    assert preview["proposal"]["lots"] == 10           # full held position
    assert preview["proposal"]["estimated_amount"] == pytest.approx(10 * 252.0)
    assert preview["risk"]["allowed"], preview["risk"]["violations"]


# --------------------------------------------------------------------------
# lot size belongs in the amount
# --------------------------------------------------------------------------

def test_amount_is_scaled_by_lot_size(sandbox_ctx):
    # A proposal that already carries an estimate without the lot multiplier:
    # 2 lots × 100 RUB × lot 100 = 20 000 RUB, not 200 RUB.
    prop = {"side": "buy", "ticker": "SBER", "lots": 2, "order_type": "limit",
            "limit_price": 100.0, "estimated_amount": 200.0}
    preview = prepare_order(prop, sandbox_ctx, None, lot_size=100)
    assert preview["proposal"]["estimated_amount"] == 20_000.0


def test_lot_size_reaches_the_position_limit(sandbox_ctx):
    prop = {"side": "buy", "ticker": "SBER", "lots": 40, "order_type": "limit",
            "limit_price": 100.0, "estimated_amount": 4_000.0}
    preview = prepare_order(prop, sandbox_ctx, None, lot_size=100)
    assert not preview["risk"]["allowed"]          # 400 000… 100× → over cap
    assert any("максимума" in v or "позиция" in v
               for v in preview["risk"]["violations"])


# --------------------------------------------------------------------------
# the placement gate re-checks the open orders
# --------------------------------------------------------------------------

class _Client:
    environment = "SANDBOX"
    mode = "sandbox"
    sandbox = True


def _preview(**prop):
    base = {"side": "buy", "ticker": "SBER", "lots": 1, "order_type": "market",
            "limit_price": None, "estimated_amount": 1_000.0}
    base.update(prop)
    return {"environment": "SANDBOX", "proposal": base, "preview_id": "p"}


def test_placement_rejects_when_open_orders_are_maxed(sandbox_ctx, monkeypatch):
    import tinvest.orders as ords

    monkeypatch.setattr(ords, "list_orders", lambda client, aid: [object()] * 3)
    settings = {"risk": {"max_open_orders": 3}}
    result = place_confirmed_order(_Client(), "acc", _preview(), confirm=True,
                                   portfolio_ctx=sandbox_ctx, settings=settings)
    assert result["placed"] is False
    assert any("заявок" in v for v in result.get("violations") or [])


def test_placement_uses_the_caller_supplied_count(sandbox_ctx, monkeypatch):
    import tinvest.orders as ords

    def boom(client, aid):
        raise AssertionError("caller passed the count — no re-read needed")

    monkeypatch.setattr(ords, "list_orders", boom)
    # The explicit count short-circuits the re-read; the proposal itself is
    # refused by the risk engine (no held position to sell) before any broker
    # call could happen.
    result = place_confirmed_order(
        _Client(), "acc", _preview(side="sell", ticker="GAZP"), confirm=True,
        portfolio_ctx=sandbox_ctx, settings=None, open_order_count=0)
    assert result["placed"] is False
    assert any("позиция не найдена" in v for v in result.get("violations") or [])


def test_placement_fails_closed_when_the_count_cannot_be_read(sandbox_ctx,
                                                              monkeypatch):
    import tinvest.orders as ords

    def boom(client, aid):
        raise RuntimeError("no network")

    monkeypatch.setattr(ords, "list_orders", boom)
    result = place_confirmed_order(_Client(), "acc", _preview(), confirm=True,
                                   portfolio_ctx=sandbox_ctx, settings=None)
    assert result["placed"] is False
    assert "перечитать открытые заявки" in result.get("reason", "")


def test_placement_still_refuses_without_confirmation(sandbox_ctx):
    result = place_confirmed_order(_Client(), "acc", _preview(), confirm=False,
                                   portfolio_ctx=sandbox_ctx, settings=None)
    assert result["placed"] is False
    assert "confirmation" in result["reason"]
