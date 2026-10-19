"""Скринер MOEX: чистый отбор кандидатов (цель «Автоматический отбор»).

Без сети и без брокера: fetch_universe тестируется на фикстуре ISS-формы
через подмену get_json, select_candidates — чистая логика.
"""

from __future__ import annotations

import pytest

from moex.screener import (
    DEFAULTS,
    effective_config,
    fetch_universe,
    select_candidates,
)


def _row(**kw):
    base = {"secid": "SBER", "board": "TQBR", "last": 300.0, "bid": 299.9,
            "spread": 0.1, "spread_pct": 0.033, "turnover_rub": 9e9,
            "trading_status": "T", "list_level": 1, "prev_close": 299.0,
            "issue_size": 21_000_000_000}
    base.update(kw)
    return base


CFG = effective_config({"screener": {"enabled": True}})


def test_a_liquid_trading_share_passes():
    scan = select_candidates([_row()], CFG)
    assert scan["scanned"] == 1 and scan["passed"] == 1
    assert scan["candidates"][0]["secid"] == "SBER"
    assert scan["rejected"] == {}


@pytest.mark.parametrize("row,reason", [
    (_row(trading_status="N"), "not_trading"),
    (_row(trading_status=""), "not_trading"),
    (_row(last=None, spread_pct=None), "no_price"),
    (_row(last=0.4), "cheap"),
    (_row(turnover_rub=50_000), "illiquid"),
    (_row(spread_pct=3.0), "wide_spread"),
    (_row(spread_pct=None), "wide_spread"),
    (_row(list_level=3), "low_list"),
    (_row(secid="AFKS"), "excluded"),
])
def test_every_filter_rejects_with_its_own_reason(row, reason):
    cfg = dict(CFG)
    cfg["exclude"] = ["AFKS"]
    scan = select_candidates([row], cfg)
    assert scan["passed"] == 0
    assert scan["rejected"] == {reason: 1}


def test_deferred_queue_is_not_a_rejection():
    scan = select_candidates([_row(), _row(secid="GAZP"), _row(secid="LKOH")],
                             CFG, decided_today=("GAZP",), holding=("LKOH",))
    assert scan["passed"] == 1
    assert scan["rejected"] == {}
    assert scan["deferred"] == {"decided": ["GAZP"], "holding": ["LKOH"]}


def test_ranking_is_turnover_desc_then_spread_asc():
    rows = [_row(secid="LOW", turnover_rub=1e6, spread_pct=0.01),
            _row(secid="HIGH", turnover_rub=9e9, spread_pct=0.5),
            _row(secid="MID", turnover_rub=5e8, spread_pct=0.2)]
    got = [c["secid"] for c in select_candidates(rows, CFG)["candidates"]]
    assert got == ["HIGH", "MID", "LOW"]


def test_queue_is_capped_by_max_candidates_per_day():
    cfg = dict(CFG)
    cfg["max_candidates_per_day"] = 2
    rows = [_row(secid=f"T{i}", turnover_rub=1e9 + i) for i in range(5)]
    scan = select_candidates(rows, cfg)
    assert scan["passed"] == 5 and len(scan["candidates"]) == 2
    assert scan["queue_limit"] == 2


def test_missing_list_level_passes_but_wrong_one_does_not():
    # Бумага уже на TQBR — отсутствие LISTLEVEL не наказывает.
    assert select_candidates([_row(list_level=None)], CFG)["passed"] == 1
    assert select_candidates([_row(list_level=3)], CFG)["passed"] == 0


# --- effective_config ------------------------------------------------------

def test_effective_config_defaults_are_safe():
    cfg = effective_config({})
    assert cfg["enabled"] is False and cfg["board"] == "TQBR"
    assert cfg["min_turnover_rub"] == DEFAULTS["min_turnover_rub"]


def test_effective_config_clamps_junk():
    cfg = effective_config({"screener": {
        "enabled": "yes", "min_turnover_rub": "-5", "max_spread_pct": "garbage",
        "max_candidates_per_day": 0, "exclude": [" sber ", "", "GAZP"],
        "list_levels": ["1", "x", 2], "board": "tqbr"}})
    assert cfg["enabled"] is True
    assert cfg["min_turnover_rub"] == 0.0
    assert cfg["max_spread_pct"] == DEFAULTS["max_spread_pct"]  # мусор → дефолт
    assert cfg["max_candidates_per_day"] == 1                    # минимум
    assert cfg["exclude"] == ["GAZP", "SBER"]
    assert cfg["list_levels"] == [1, 2]
    assert cfg["board"] == "TQBR"


# --- fetch_universe (фикстура ISS) ----------------------------------------

def _iss_fixture():
    return {
        "securities": {
            "columns": ["SECID", "PREVLEGALCLOSEPRICE", "ISSUESIZE", "LISTLEVEL"],
            "data": [["SBER", 299.0, 21_000_000_000, 1],
                     ["AFKS", 7.754, 9_650_000_000, 1],
                     ["OLD", 10.0, 1_000_000, 2]],
        },
        "marketdata": {
            "columns": ["SECID", "LAST", "BID", "SPREAD", "TRADINGSTATUS",
                        "VALTODAY"],
            "data": [["SBER", 300.0, 299.9, 0.1, "T", 9e9],
                     ["AFKS", 7.975, 7.974, 0.003, "T", 9.3e8],
                     ["OLD", None, None, None, "N", 0]],
        },
    }


def test_fetch_universe_merges_and_computes(monkeypatch):
    seen = {}
    monkeypatch.setattr("moex.screener.get_json",
                        lambda url, params, ttl=0.0: seen.update(
                            {"url": url, "params": params}) or _iss_fixture())
    rows = fetch_universe("TQBR")
    assert [r["secid"] for r in rows] == ["SBER", "AFKS", "OLD"]
    sber = rows[0]
    assert sber["last"] == 300.0 and sber["turnover_rub"] == 9e9
    assert sber["spread_pct"] == pytest.approx(0.1 / 300.0 * 100)
    assert sber["list_level"] == 1 and sber["trading_status"] == "T"
    assert rows[2]["last"] is None and rows[2]["turnover_rub"] == 0.0
    assert "/boards/TQBR/securities.json" in seen["url"]
