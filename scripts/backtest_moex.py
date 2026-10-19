#!/usr/bin/env python3
"""Бэктест MOEX: полный граф по сетке (тикер × дата) на point-in-time данных.

Смысл совпадает с tradingagents/backtest.py апстрима — оценка качества
решений, не симулятор портфеля: ячейки независимы, отдельный лог памяти
запуска и есть таблица результатов. Апстрим не правится (spec §12) — раннер
живёт в наших scripts/ и собирает конфиг тем же gui.run_manager._build_config,
что и живой прогон.

Чем отличается от «просто прогнать апстрим-бэктест»:
  * вендоры и settlement — наши MOEX-адаптеры: доходности и бенчмарк IMOEX
    через ISS, никогда yfinance/SPY (без этого каждая запись висит pending);
  * снапшот в промпт аналитиков — point-in-time: as_of = дата ячейки
    (живой конфиг запекает его без даты — для ячейки 2023 года там были бы
    цены и новости 2026-го);
  * живые «уроки» (§11) не подмешиваются — они относятся к решениям более
    поздних дат; уроки видны только через get_past_context собственных
    записей этого запуска, т.е. строго прошлых по отношению к ячейке;
  * окно новостей в снапшоте обрезается по дате ячейки (integration.
    gui_adapter.snapshot_news_window).

Возобновляемо: ячейки, уже записанные в лог памяти, пропускаются —
повторный запуск с тем же --run-id докатывает остаток.

Итог считается по settled-записям: hit rate по рейтингу и средний alpha
против IMOEX; pending (окно ещё не торговалось) отдельной строкой.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUI_ROOT = REPO_ROOT / "upstream" / "TradingAgents-GUI"

# Направление, которое утверждает каждый рейтинг (как в апстрим-бэктесте).
DIRECTION = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}


# --- чистые помощники: тестируются без сети и без импорта tradingagents ---


def grid_dates(start: str, end: str, every: int) -> list[str]:
    """Даты сетки от start до end с шагом every дней (календарные, как iter_grid)."""
    if every < 1:
        raise ValueError(f"every must be at least 1, got {every}")
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    if last < first:
        raise ValueError(f"the grid ends before it starts: {end} < {start}")
    out, cursor = [], first
    while cursor <= last:
        out.append(cursor.date().isoformat())
        cursor += timedelta(days=every)
    return out


def parse_percent(text) -> float | None:
    """"+3.2%" -> 0.032; None/мусор -> None (запись не оценена)."""
    if text is None:
        return None
    try:
        return float(str(text).strip().rstrip("%")) / 100
    except ValueError:
        return None


def summarize(entries: list[dict]) -> dict:
    """Агрегация записей лога: {resolved, pending, unscored, by_rating}.

    resolved — не pending, рейтинг читается, alpha распознан (окно
    торговалось); unscored — записи без направления-от-рейтинга (Review и
    нераспознанные метки) отдельно, чтобы не приписывать им успех.
    """
    resolved: list[tuple[dict, float]] = []
    pending = unscored = 0
    for e in entries:
        if e.get("pending"):
            pending += 1
            continue
        alpha = parse_percent(e.get("alpha"))
        if alpha is None:
            # Окно торговалось (тег не pending), но alpha не распознана —
            # это не «ещё не оценена», а неоценённая: не выдумываем исход.
            unscored += 1
            continue
        resolved.append((e, alpha))
    by_rating: dict[str, dict] = {}
    for e, alpha in resolved:
        rating = str(e.get("rating") or "?")
        slot = by_rating.setdefault(rating, {"count": 0, "hits": 0,
                                             "directional": 0, "alphas": []})
        slot["count"] += 1
        slot["alphas"].append(alpha)
        direction = DIRECTION.get(rating, 0)
        if direction:
            slot["directional"] += 1
            if alpha * direction > 0:
                slot["hits"] += 1
    for slot in by_rating.values():
        slot["hit_rate"] = (slot["hits"] / slot["directional"]) if slot["directional"] else None
        slot["mean_alpha"] = sum(slot["alphas"]) / len(slot["alphas"])
        del slot["alphas"]
    return {"resolved": len(resolved), "pending": pending, "unscored": unscored,
            "by_rating": by_rating}


def render_summary(summary: dict, *, tickers: list[str], grid: str) -> str:
    counts = (f"Решений с исходом: {summary['resolved']}"
              f" · без исхода (pending): {summary['pending']}")
    if summary.get("unscored"):
        counts += (f" · не оценено (тег settled, alpha не распознана): "
                   f"{summary['unscored']}")
    lines = [
        f"Бэктест MOEX · тикеры {', '.join(tickers)} · сетка {grid}",
        counts,
        "",
    ]
    for rating, s in summary["by_rating"].items():
        hit = (f"направление угадано {s['hit_rate']:.0%}"
               if s["hit_rate"] is not None else "без направления")
        lines.append(f"- {rating}: n={s['count']}, {hit}, "
                     f"средний alpha {s['mean_alpha']:+.2%} против IMOEX")
    lines.append("")
    if summary["pending"]:
        lines.append("Pending-записи (окно не торговалось / исход не прочитан) "
                     "в агрегат не входят; повторный запуск их закроет.")
    lines.append("Alpha измеряется вдоль 5 торговых дней с первой закрытия после "
                 "даты решения, бенчмарк — IMOEX (MOEX ISS). Одна модель-сэмпл "
                 "на ячейку, ленты новостей не архивируются — цифры "
                 "индикативны, а не воспроизводимы.")
    return "\n".join(lines)


def load_run_meta(path: Path) -> dict:
    """run_meta.json прогона — толерантен к отсутствию и порче.

    Метка ``--summary`` должна описывать САМ прогон (сетка/тикеры), а не
    текущие CLI-аргументы: summary.md — итоговый документ.
    """
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def load_env_file(path: Path) -> None:
    """KEY=VALUE из .env → os.environ, только для ещё не заданных ключей."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def journal(path: Path, line: str) -> None:
    stamped = f"{datetime.now().isoformat(timespec='seconds')} {line}"
    print(stamped, flush=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(stamped + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="SBER,GAZP,LKOH",
                    help="тикеры через запятую (default: SBER,GAZP,LKOH)")
    ap.add_argument("--start", default=None, help="первая дата сетки YYYY-MM-DD (default: -3 года)")
    ap.add_argument("--end", default=None, help="последняя дата YYYY-MM-DD (default: сегодня)")
    ap.add_argument("--every", type=int, default=30, help="шаг сетки в днях (default 30)")
    ap.add_argument("--board", default="TQBR")
    ap.add_argument("--analysts", default="market,social,news,fundamentals")
    ap.add_argument("--run-id", default=None,
                    help="id запуска; повторный запуск с тем же id докатывает ячейки")
    ap.add_argument("--max-cells", type=int, default=0,
                    help="остановиться после N новых ячеек (0 = без лимита)")
    ap.add_argument("--cell-timeout", type=int, default=2700,
                    help="секунд на ячейку, 0 = без лимита (default 2700 = run_timeout_seconds)")
    ap.add_argument("--plan", action="store_true",
                    help="показать сетку и выйти (без ISS и LLM)")
    ap.add_argument("--summary", action="store_true",
                    help="только вывести итог существующего запуска")
    args = ap.parse_args(argv)

    today = datetime.now().date()
    start = args.start or today.replace(year=today.year - 3).isoformat()
    end = args.end or today.isoformat()
    dates = grid_dates(start, end, args.every)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        ap.error("no tickers")
    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    run_id = args.run_id or f"bt_{start}_{end}_e{args.every}"

    for path in (str(REPO_ROOT), str(GUI_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)

    home = Path(os.environ.get("TRADINGAGENTS_HOME") or Path.home() / ".tradingagents")
    run_dir = home / "backtest" / run_id
    log_path = run_dir / "sweep.log"
    memory_path = run_dir / "trading_memory.md"
    meta_path = run_dir / "run_meta.json"

    if args.plan:
        print(f"run-id: {run_id}")
        print(f"сетка: {len(dates)} дат × {len(tickers)} тикеров "
              f"= {len(dates) * len(tickers)} ячеек (шаг {args.every} д)")
        print("даты: " + ", ".join(dates))
        return 0

    # Ограничиваем LLM-сессии так же, как живой сервис (override.conf).
    os.environ.setdefault("TA_LLM_TIMEOUT", "120")
    os.environ.setdefault("TA_LLM_MAX_RETRIES", "2")
    # Токены: системный конфиг (TINVEST_TOKEN — исторические новости) затем
    # .env GUI (OPENROUTER_API_KEY); файлы только заполняют незаданные ключи.
    load_env_file(Path.home() / ".config" / "tradingagents.env")
    load_env_file(GUI_ROOT / ".env")

    from gui.run_manager import _build_config
    from gui.ui_state import UIStateStore
    from tradingagents.agents.utils.memory import TradingMemoryLog
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    from integration.gui_adapter import build_moex_snapshot, register_moex_vendors
    from integration.moex_settlement import install as install_settlement

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results").mkdir(exist_ok=True)

    register_moex_vendors()
    if not install_settlement().get("gui"):
        print("[warn] MOEX settlement не установлен — записи останутся pending",
              file=sys.stderr)

    ui = UIStateStore().get()
    params_base = {
        "market": "MOEX",
        "board": args.board,
        "analysts": analysts,
        "research_depth": 3,
        "output_language": "English",  # как в живом цикле (_analysis_params)
        "provider": ui.get("provider") or "openrouter",
        "quick_model": ui.get("quick_model"),
        "deep_model": ui.get("deep_model"),
        "report_brevity": "standard",
        # Живые «уроки» §11 (lessons.jsonl) — знание БУДУЩЕГО для ячейки
        # 2023 года: канал закрыт (требование — не подмешивать в бэктест).
        "moex_lessons": False,
    }

    if args.summary:
        # Метка сетки должна описывать САМ прогон, а не текущие аргументы.
        meta = load_run_meta(meta_path)
        if meta.get("tickers"):
            tickers = [str(t).upper() for t in meta["tickers"]]
        start = str(meta.get("start") or start)
        end = str(meta.get("end") or end)
        every = int(meta.get("every") or args.every)
        entries = TradingMemoryLog({"memory_log_path": str(memory_path)}).load_entries()
        if not entries:
            print(f"нет записей: {memory_path}")
            return 1
        print(render_summary(summarize(entries), tickers=tickers,
                             grid=f"{start}…{end} шаг {every}д"))
        return 0

    journal(log_path, f"== старт: {', '.join(tickers)} · {start}…{end} шаг "
                      f"{args.every}д · {len(dates)} дат · сетка {run_id} ==")
    meta_path.write_text(json.dumps({
        "tickers": tickers, "start": start, "end": end, "every": args.every,
        "board": args.board, "analysts": analysts,
        "created": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    import signal
    import time
    from contextlib import contextmanager

    class CellTimeout(Exception):
        """Ячейка превысила --cell-timeout."""

    @contextmanager
    def cell_timeout(seconds: int):
        if seconds <= 0:
            yield
            return

        def _raise(signum, frame):  # noqa: ARG001
            raise CellTimeout(f"ячейка не уложилась в {seconds} с")

        previous = signal.signal(signal.SIGALRM, _raise)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    ran = failed = 0
    stopped = False
    for ticker in tickers:
        params = {**params_base, "ticker": ticker, "date": end}
        cfg = _build_config(params, DEFAULT_CONFIG)
        cfg["results_dir"] = str(run_dir / "results")
        cfg["memory_log_path"] = str(memory_path)
        # Точка данных — свой лог и свои отчёты; живая память не трогается.
        graph = TradingAgentsGraph(list(analysts), config=cfg)
        done = {(e["date"], e["ticker"]) for e in
                TradingMemoryLog(cfg).load_entries()}
        fresh = [d for d in dates if (d, ticker) not in done]
        journal(log_path, f"-- {ticker}: в логе {len(dates) - len(fresh)}, "
                          f"к запуску {len(fresh)}")
        for index, day in enumerate(fresh, 1):
            if args.max_cells and ran >= args.max_cells:
                stopped = True
                break
            # Point-in-time: снапшот перепекается под дату ячейки, иначе в
            # промпт 2023 года попали бы сегодняшние цены и новости.
            cfg["moex_data_snapshot"] = build_moex_snapshot(
                ticker, args.board, as_of=day)
            set_config(cfg)  # get_config() отдаёт deepcopy — перечитываем
            began = time.time()
            try:
                with cell_timeout(args.cell_timeout):
                    graph.propagate(ticker, day)
                status = "ok"
            except Exception as exc:  # одна ячейка не должна ронять свип
                status = f"FAIL {type(exc).__name__}: {exc}"
                failed += 1
            ran += 1
            journal(log_path, f"  [{ticker} {index}/{len(fresh)}] {day} — {status} "
                              f"({time.time() - began:.0f} с)")
        # Последний проход settlement по тикеру: окна, уже торговавшиеся,
        # закрываются даже без следующего propagate.
        try:
            graph._resolve_pending_entries(ticker)
        except Exception as exc:
            journal(log_path, f"  [warn] settlement {ticker}: {exc}")
        if stopped:
            break

    entries = TradingMemoryLog({"memory_log_path": str(memory_path)}).load_entries()
    summary = summarize(entries)
    text = render_summary(summary, tickers=tickers, grid=f"{start}…{end} шаг {args.every}д")
    (run_dir / "summary.md").write_text(text + "\n", encoding="utf-8")
    journal(log_path, f"== итог: ячеек запущено {ran}, с ошибкой {failed}; "
                      f"с исходом {summary['resolved']}, pending {summary['pending']}"
                      f", не оценено {summary['unscored']} ==")
    print("\n" + text)
    print(f"\nлоги: {log_path}\nитог: {run_dir / 'summary.md'}")
    if stopped:
        print(f"остановлено по --max-cells {args.max_cells}; "
              f"докатка: тот же run-id без --max-cells")
    return 0 if summary["resolved"] or not ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
