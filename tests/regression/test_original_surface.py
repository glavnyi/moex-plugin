from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUI_ROOT = ROOT / "upstream" / "TradingAgents-GUI"
TA_ROOT = ROOT / "upstream" / "tradingagents"

sys.path.insert(0, str(GUI_ROOT))


def _pristine(root: Path, rel: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{rel}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _routes(py_source: str) -> set[str]:
    return set(re.findall(r'@app\.route\("([^"]+)"', py_source))


def _tabs(html_source: str) -> set[str]:
    return set(re.findall(r'data-tab="([^"]+)"', html_source))


@pytest.fixture(scope="module")
def pristine_app_src() -> str:
    return _pristine(GUI_ROOT, "gui/app.py")


@pytest.fixture(scope="module")
def pristine_index_src() -> str:
    return _pristine(GUI_ROOT, "gui/templates/index.html")


def test_all_original_routes_survive(pristine_app_src):
    from gui.app import app

    original = _routes(pristine_app_src)
    present = {str(r) for r in app.url_map.iter_rules()}
    missing = original - present
    assert not missing, f"original routes lost: {sorted(missing)}"


def test_original_nav_tabs_survive(pristine_index_src):
    html = (GUI_ROOT / "gui" / "templates" / "index.html").read_text(encoding="utf-8")
    original_tabs = _tabs(pristine_index_src)
    new_tabs = _tabs(html)
    assert original_tabs <= new_tabs, f"lost tabs: {original_tabs - new_tabs}"
    # documented additions only
    assert {"moex", "sandbox"} <= new_tabs - original_tabs


def test_original_run_config_behaviour_kept():
    from tradingagents.default_config import DEFAULT_CONFIG
    from gui.run_manager import _build_config

    params = {
        "provider": "openai", "quick_model": "gpt-4o-mini", "deep_model": "gpt-4o",
        "research_depth": "4", "output_language": "English",
        "data_vendors": {"news_data": "alpha_vantage"},
    }
    cfg = _build_config(params, DEFAULT_CONFIG)
    assert cfg["llm_provider"] == "openai"
    assert cfg["quick_think_llm"] == "gpt-4o-mini"
    assert cfg["deep_think_llm"] == "gpt-4o"
    assert cfg["max_debate_rounds"] == 4
    assert cfg["output_language"] == "English"
    assert cfg["data_vendors"]["news_data"] == "alpha_vantage"
    # non-MOEX runs must not grow MOEX keys (upstream ships an empty
    # tool_vendors block in DEFAULT_CONFIG — it must stay empty)
    assert cfg.get("tool_vendors") == {}
    assert "market" not in cfg


def test_moex_run_config_enriched():
    from tradingagents.default_config import DEFAULT_CONFIG
    from gui.run_manager import _build_config

    cfg = _build_config({"market": "MOEX", "ticker": "SBER", "board": "TQBR"},
                        DEFAULT_CONFIG)
    assert cfg["market"] == "MOEX"
    assert cfg["ticker"] == "SBER"
    assert cfg["tool_vendors"]["get_stock_data"] == "moex"


def test_cli_help_keeps_original_flags_and_adds_market():
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(TA_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c",
         "from typer.testing import CliRunner; from cli.main import app; "
         "raise SystemExit(0 if CliRunner().invoke(app, ['--help']).exit_code == 0 else 1)"],
        capture_output=True, text=True, cwd=str(TA_ROOT), env=env,
    )
    assert proc.returncode == 0, proc.stderr[-500:]

    env2 = dict(os.environ)
    env2["PYTHONPATH"] = str(TA_ROOT)
    proc2 = subprocess.run(
        [sys.executable, "-c",
         "from typer.testing import CliRunner; from cli.main import app; "
         "print(CliRunner().invoke(app, ['--help']).output)"],
        capture_output=True, text=True, cwd=str(TA_ROOT), env=env2,
    )
    out = proc2.stdout
    for flag in ("--ticker", "--date", "--analysts", "--portfolio",
                 "--checkpoint", "--market", "--board"):
        assert flag in out, f"missing flag {flag}"


def test_gui_index_contains_extension_assets():
    html = (GUI_ROOT / "gui" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "/api/moex_ext.js" in html
    assert 'id="tab-moex"' in html
    assert 'id="tab-sandbox"' in html
    # the market selector is injected by ext.js into the Analyze form
    ext = (ROOT / "integration" / "ext.js").read_text(encoding="utf-8")
    assert "market-select" in ext
    assert "/api/analyze" in ext


def test_app_registers_extension_routes():
    from gui.app import app

    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/tinvest/status" in rules
    assert "/api/moex/risk" in rules
    assert "/api/moex_ext.js" in rules
