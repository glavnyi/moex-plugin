"""build_moex_snapshot — real-data prompt injection (MOEX runs).

Guards against the live bug where free models never emit tool calls and
the analysts therefore fabricated prices (report: close 285.76 vs real
ISS close 286.43).
"""

from __future__ import annotations


def _stub(monkeypatch, **kw):
    import moex.fundamentals as fund
    import moex.instruments as instr
    import moex.market_data as md
    import moex.news as news
    from integration import gui_adapter as ga

    monkeypatch.setattr(
        md, "stock_data_report",
        kw.get("stock", lambda *a, **k: "2026-10-14,286.43"))
    monkeypatch.setattr(
        md, "indicator_values",
        kw.get("ind", lambda *a, **k: "close_50_sma=277.99"))
    monkeypatch.setattr(
        fund, "fundamentals_report",
        kw.get("fund", lambda *a, **k: "Name: Sberbank"))
    monkeypatch.setattr(
        instr, "resolve_instrument",
        kw.get("instr", lambda *a, **k: type("I", (), {"secid": "SBER"})()))
    monkeypatch.setattr(
        news, "fetch_all",
        kw.get("fetch", lambda *a, **k: type("A", (), {"items": []})()))
    monkeypatch.setattr(
        news, "news_report",
        kw.get("nrep", lambda *a, **k: "MOEX: sample headline"))
    return ga


def test_snapshot_contains_real_sections(monkeypatch):
    ga = _stub(monkeypatch)
    text = ga.build_moex_snapshot("SBER", "TQBR", as_of="2026-10-14")
    assert "Pre-fetched real MOEX data" in text
    assert "286.43" in text              # OHLCV from ISS
    assert "close_50_sma=277.99" in text
    assert "Sberbank" in text
    assert "NEVER invent" in text
    assert len(text) <= ga._SNAPSHOT_MAX_CHARS


def test_snapshot_marks_failed_sections(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ISS down")

    ga = _stub(monkeypatch, stock=boom)
    text = ga.build_moex_snapshot("SBER", "TQBR", as_of="2026-10-14")
    assert "[unavailable: ISS down]" in text
    assert "Sberbank" in text            # other sections still present


def test_snapshot_banner_reflects_session_state(monkeypatch):
    import moex.market_data as md
    from integration import gui_adapter as ga

    monkeypatch.setattr(md, "get_market_status",
                        lambda: {"trading": True, "statuses": ["T"]})
    assert "is TRADING right now" in ga._market_status_block()
    assert "PROVISIONAL" in ga._market_status_block()

    monkeypatch.setattr(md, "get_market_status",
                        lambda: {"trading": False, "statuses": ["N"]})
    assert "is not trading" in ga._market_status_block()

    def boom():
        raise RuntimeError("ISS down")
    monkeypatch.setattr(md, "get_market_status", boom)
    assert "unavailable" in ga._market_status_block()
