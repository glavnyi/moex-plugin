"""MOEX settlement: alpha against IMOEX, not Yahoo's SPY, plus §11 lessons.

The bug this closes: yfinance returns zero bars for every Russian symbol, so
upstream ``_fetch_returns`` scored nothing, every MOEX memory entry stayed
``pending`` and ``get_past_context`` — which drops pending entries — handed
the Portfolio Manager an empty lessons line. ``lessons_block()`` had no
caller at all.
"""

from __future__ import annotations

import pandas as pd
import pytest

from integration.moex_settlement import (
    DEFAULT_BENCHMARK,
    benchmark_for,
    fetch_returns,
    install,
    installed,
    is_moex,
    uninstall,
)

DAYS = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06",
        "2026-10-07", "2026-10-08", "2026-10-09"]


def _frame(closes):
    return pd.DataFrame({"Date": DAYS[:len(closes)], "Close": closes})


@pytest.fixture(autouse=True)
def fresh_hooks():
    """Every test starts and ends with the upstream methods in place."""
    uninstall()
    yield
    uninstall()


def _stub_candles(monkeypatch, stock, bench):
    import moex.market_data as md

    def fake(target, start, end, *, interval="1d", market=None, board=None, **kw):
        name = str(target).upper()
        if name in {"IMOEX", "MOEX", DEFAULT_BENCHMARK}:
            return _frame(bench)
        return _frame(stock)

    monkeypatch.setattr(md, "get_candles", fake)


def test_is_moex_guard_matches_the_vendor_guard():
    assert is_moex({"market": "MOEX"}) is True
    assert is_moex({"market": "moex"}) is True
    assert is_moex({"market": "US"}) is False
    assert is_moex({}) is False
    assert is_moex(None) is False


def test_benchmark_prefers_config_then_settings_then_default(monkeypatch):
    assert benchmark_for({"moex_benchmark": "RGBI"}) == "RGBI"
    assert benchmark_for({}) == DEFAULT_BENCHMARK

    import moex.config as cfg

    monkeypatch.setitem(cfg.DEFAULT_SETTINGS["evaluation"], "benchmark", "MOEXB")
    assert benchmark_for({}) == "MOEXB"


def test_fetch_returns_scores_raw_and_alpha_over_the_window(monkeypatch):
    # +10% for the share, +5% for the index -> +5pp of alpha.
    _stub_candles(monkeypatch,
                  stock=[100, 102, 104, 106, 108, 110, 112],
                  bench=[2000, 2020, 2040, 2060, 2080, 2100, 2120])
    raw, alpha, days = fetch_returns(
        {"market": "MOEX", "board": "TQBR"}, "SBER", "2026-10-01", holding_days=5)
    assert raw == pytest.approx(0.10)
    assert alpha == pytest.approx(0.05)
    assert days == 5


def test_fetch_returns_stays_pending_while_the_window_has_not_traded(monkeypatch):
    _stub_candles(monkeypatch, stock=[100, 102, 104], bench=[2000] * 7)
    assert fetch_returns({"market": "MOEX"}, "SBER", "2026-10-01") == (
        None, None, None)


def test_fetch_returns_stays_pending_when_the_index_lags_the_exit(monkeypatch):
    # The index has no bar on or after the exit: nothing to measure against.
    _stub_candles(monkeypatch, stock=[100, 102, 104, 106, 108, 110, 112],
                  bench=[2000, 2010, 2020])
    assert fetch_returns({"market": "MOEX"}, "SBER", "2026-10-01") == (
        None, None, None)


def test_fetch_returns_never_scores_against_a_missing_index(monkeypatch):
    _stub_candles(monkeypatch, stock=[100, 102, 104, 106, 108, 110, 112],
                  bench=[])
    assert fetch_returns({"market": "MOEX"}, "SBER", "2026-10-01") == (
        None, None, None)


def test_install_wraps_once_and_uninstall_restores_the_originals():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    original_benchmark = TradingAgentsGraph._resolve_benchmark
    original_returns = TradingAgentsGraph._fetch_returns

    assert install()["gui"] is True
    assert install()["gui"] is True   # idempotent, not double-wrapped
    assert installed() is True

    class _Run:
        config = {"market": "MOEX", "board": "TQBR"}

    assert TradingAgentsGraph._resolve_benchmark(_Run(), "SBER") == DEFAULT_BENCHMARK

    uninstall()
    assert TradingAgentsGraph._resolve_benchmark is original_benchmark
    assert TradingAgentsGraph._fetch_returns is original_returns
    assert installed() is False


def test_wrapper_delegates_foreign_markets_to_upstream():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    install()
    class _Run:
        config = {"market": "US"}   # no market key -> not MOEX

    # Upstream falls back to SPY for an empty suffix map: untouched behaviour.
    assert TradingAgentsGraph._resolve_benchmark(_Run(), "AAPL") == "SPY"


def test_wrapper_reads_the_benchmark_for_a_moex_run(monkeypatch):
    import moex.config as cfg
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    monkeypatch.setitem(cfg.DEFAULT_SETTINGS["evaluation"], "benchmark", "RGBI")
    install()

    class _Run:
        config = {"market": "MOEX"}

    assert TradingAgentsGraph._resolve_benchmark(_Run(), "SBER") == "RGBI"


def test_enrich_run_config_wires_settlement_and_injects_lessons(monkeypatch):
    from integration import config_adapter as ca
    from integration import gui_adapter as ga
    from moex.analytics import record_lesson

    monkeypatch.setattr(ga, "build_moex_snapshot",
                        lambda ticker, board="": "## Pre-fetched real MOEX data\n286.43")
    record_lesson(
        proposal={"side": "sell", "ticker": "SBER"},
        decision_context={"source": "test"},
        execution={"closed_at": "2026-10-15"},
        realized_pnl=-120.0,
        explanation="Стоп сработал по новостям.",
    )

    merged = ca.enrich_run_config({"market": "MOEX", "ticker": "SBER"},
                                  {"market": "MOEX", "ticker": "SBER"})

    assert installed() is True                 # settlement hooked by enrichment
    snapshot = merged["moex_data_snapshot"]
    assert "Lessons from past closed trades" in snapshot
    assert "Стоп сработал по новостям." in snapshot
    # Lessons lead, so the snapshot's "every figure below comes from MOEX ISS"
    # banner still describes the ISS payload underneath it.
    assert snapshot.index("Lessons from past closed trades") < snapshot.index(
        "Pre-fetched real MOEX data")


def test_enrich_run_config_leaves_other_markets_alone(monkeypatch):
    from integration import config_adapter as ca

    original = {"market": "US", "ticker": "AAPL", "data_vendors": {"news_data": "yfinance"}}
    assert ca.enrich_run_config({"market": "US", "ticker": "AAPL"}, original) is original
    assert installed() is False                # nothing was hooked
