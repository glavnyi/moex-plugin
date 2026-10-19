"""Обрезка новостных tool-репортов по границам блоков (контекст-бомба).

После фикса content-сниппетов (T-Invest) репорт из 60–80 items дорос до
десятков КБ — тяжело для free-модели. Обрезка обязана резать на границе
«### » (целые новости) и честно сказать, сколько пропущено.
"""

from __future__ import annotations

from integration.gui_adapter import (
    _GLOBAL_NEWS_TOOL_MAX_CHARS,
    _NEWS_TOOL_MAX_CHARS,
    _cap_report,
)


def _report(items: int, body_len: int = 700) -> str:
    blocks = "".join(
        f"### Заголовок №{i} (source: wire, 2026-10-19)\n{'x' * body_len}\n\n"
        for i in range(items)
    )
    return f"## SBER News, from 2026-10-01 to 2026-10-19:\n\n{blocks}"


def test_short_report_passes_through_unchanged():
    text = _report(3, body_len=100)
    assert _cap_report(text, _NEWS_TOOL_MAX_CHARS) == text


def test_long_report_is_cut_at_a_block_boundary():
    text = _report(80)  # ~56КБ > 12K лимита
    capped = _cap_report(text, _NEWS_TOOL_MAX_CHARS)

    assert len(capped) <= _NEWS_TOOL_MAX_CHARS + 400  # + служебная пометка
    assert "обрезано по лимиту" in capped
    # Обрезка строго на границе блока: текст до пометки кончается на
    # пустой строке после целой новости, а не посреди тела.
    body = capped.split("… (обрезано")[0]
    assert body.endswith("\n\n")


def test_note_counts_are_exact():
    text = _report(40, body_len=500)  # ~20КБ > 8K глобального лимита
    capped = _cap_report(text, _GLOBAL_NEWS_TOOL_MAX_CHARS)
    shown = capped[:capped.index("… (обрезано")].count("### ")
    total = text.count("### ")
    assert 0 < shown < total
    assert f"показано {shown} из {total} новостей" in capped
    assert f"пропущено {total - shown}" in capped


def test_tiny_limit_still_notes_the_truncation():
    capped = _cap_report(_report(10), 50)
    assert "обрезано по лимиту" in capped
