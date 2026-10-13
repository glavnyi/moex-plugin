from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GUI_ROOT = ROOT / "upstream" / "TradingAgents-GUI"
TA_ROOT = ROOT / "upstream" / "tradingagents"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.tradingagents directory."""
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(tmp_path / "ta-home"))
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_TINVEST_TOKEN", raising=False)
    return tmp_path / "ta-home"


@pytest.fixture()
def flask_app():
    from flask import Flask

    from integration.gui_routes import register_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    register_routes(app)
    return app.test_client()


@pytest.fixture()
def sandbox_ctx():
    return {
        "sandbox": True,
        "cash": 1_000_000.0,
        "total_value": 2_000_000.0,
        "positions": [
            {"ticker": "SBER", "quantity_lots": 10, "value": 250_000.0,
             "avg_price": 250.0, "current_price": 252.0},
        ],
        "fetched_at": "2026-10-13 12:00 UTC",
        "source": "test",
    }
