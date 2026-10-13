from __future__ import annotations

from moex.analytics.workflow import (
    _rewrite_lesson,
    delete_lesson,
    lessons_block,
    load_lessons,
    record_lesson,
    set_lesson_enabled,
)


def _record(**kw):
    base = {
        "proposal": {"side": "buy", "ticker": "SBER", "lots": 5, "market": "MOEX"},
        "decision_context": {"news_count": 3, "as_of": "2026-10-10"},
        "execution": {"order_id": "ta-abc", "filled_lots": 5, "avg_price": 250.0},
        "realized_pnl": 1234.5,
        "explanation": "Гипотеза подтвердилась: рост выручки.",
    }
    base.update(kw)
    return record_lesson(**base)


def test_record_and_load_roundtrip_persists():
    saved = _record()
    assert saved["id"] and saved["created_at"]
    assert saved["enabled"] is True and saved["used_in_future_runs"] is False
    rows = load_lessons()
    assert len(rows) == 1
    row = rows[0]
    assert row["proposal"]["ticker"] == "SBER"
    assert row["decision_context"]["news_count"] == 3
    assert row["execution"]["order_id"] == "ta-abc"
    assert row["realized_pnl"] == 1234.5
    # file-backed: a fresh load (as after a restart) sees the same data
    assert load_lessons() == rows


def test_lesson_toggle_filters_future_runs():
    keep = _record()
    hide = _record(realized_pnl=-50.0, explanation="Гипотеза не подтвердилась.")
    assert set_lesson_enabled(hide["id"], False) is True
    enabled = load_lessons()
    assert [r["id"] for r in enabled] == [keep["id"]]
    assert len(load_lessons(enabled_only=False)) == 2
    assert set_lesson_enabled("missing", True) is False


def test_delete_lesson_removes_row():
    a = _record()
    b = _record(realized_pnl=-10.0, explanation="Локальный стоп.")
    assert delete_lesson(a["id"]) is True
    assert delete_lesson(a["id"]) is False  # already gone
    rows = load_lessons()
    assert [r["id"] for r in rows] == [b["id"]]


def test_lessons_block_is_informational_capped_and_skips_used():
    for i in range(7):
        _record(realized_pnl=float(i), explanation=f"Вывод {i}")
    block = lessons_block(max_items=3)
    assert block.startswith("# Lessons from past closed trades (informational, not rules)")
    lines = [x for x in block.splitlines() if x.startswith("- [")]
    assert len(lines) == 3  # capped at max_items (newest tail)
    assert "pnl=" in lines[-1]

    # disabled lessons drop out of the block entirely
    rows = load_lessons()
    set_lesson_enabled(rows[0]["id"], False)
    block2 = lessons_block(max_items=100)
    assert rows[0]["id"] not in block2

    # lessons already consumed by a future run are not re-injected
    used = load_lessons()[0]
    assert _rewrite_lesson(used["id"], {"used_in_future_runs": True}) is True
    assert used["id"] not in lessons_block(max_items=100)
