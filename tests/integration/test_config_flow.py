from __future__ import annotations

from integration.config_adapter import describe_config, enrich_run_config


def test_non_moex_config_is_byte_identical():
    base = {"llm_provider": "openai", "data_vendors": {"core_stock_apis": "yfinance"}}
    out = enrich_run_config({"market": "US", "ticker": "AAPL"}, base)
    assert out is base or out == base
    assert "tool_vendors" not in out
    assert "market" not in out


def test_moex_fragment_keeps_original_keys():
    base = {"llm_provider": "openai", "data_vendors": {"core_stock_apis": "yfinance"}}
    out = enrich_run_config({"market": "MOEX", "ticker": "sber", "board": "tqbr"}, base)
    assert out["llm_provider"] == "openai"
    assert out["market"] == "MOEX"
    assert out["ticker"] == "SBER"
    assert out["board"] == "TQBR"
    assert out["data_vendors"]["core_stock_apis"] == "moex"
    assert out["tool_vendors"]["get_stock_data"] == "moex"
    assert len(out["tool_vendors"]) == 9


def test_register_is_idempotent_and_preserves_originals():
    from integration import register_moex_vendors
    import tradingagents.dataflows.interface as itf

    register_moex_vendors()
    register_moex_vendors()
    for method in ("get_stock_data", "get_news"):
        assert "moex" in itf.VENDOR_METHODS[method]
        assert "yfinance" in itf.VENDOR_METHODS[method]
    assert "moex" in itf.VENDOR_LIST


def test_moex_impl_refuses_non_moex_config():
    from integration.gui_adapter import moex_stock_data
    import pytest

    with pytest.raises(ValueError, match="market is unset"):
        moex_stock_data("SBER", "2026-09-01", "2026-10-10")


def test_describe_config_no_secrets():
    desc = describe_config({"market": "MOEX", "ticker": "SBER", "board": "TQBR",
                            "tool_vendors": {"get_stock_data": "moex"}})
    assert desc["is_moex"] is True
    blob = str(desc).lower()
    assert "token" not in blob and "secret" not in blob


def test_version_check_pins_match_manifest():
    from integration.version_check import PINNED, check

    import yaml
    from pathlib import Path

    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "patches" / "manifest.yaml")
        .read_text(encoding="utf-8")
    )
    assert PINNED["tradingagents"] == manifest["pinned"]["tradingagents"]["commit"]
    assert (PINNED["TradingAgents-GUI"]
            == manifest["pinned"]["TradingAgents-GUI"]["commit"])
    rep = check()
    assert rep["tradingagents"]["match"] is True
    assert rep["TradingAgents-GUI"]["match"] is True
