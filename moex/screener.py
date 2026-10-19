"""Скринер MOEX — автоматический отбор кандидатов для автоторговли.

Отвечает на вопрос цели «Автоматический отбор инструментов»: какие тикеры
борда стоит отдавать в TradingAgents сегодня. Выборка вселенной (одна
страница ISS по всему борду, TTL-кэш) отделена от чистой логики отбора
(``select_candidates``) — фильтры и ранжирование тестируются на фикстурах
без сети (§14.4).

Каждый отказ получает имя причины, и GUI показывает разбивку — скринер не
«тихо выбрасывает» бумаги. Отложенные (решение по тикеру уже принято
сегодня / позиция открыта) — не отказ, а очередь: они вернутся завтра.

Лимит «анализов в день» (``max_candidates_per_day``) — бюджет LLM, он
трекется журналом авторежима в сервисном слое и переживает перезапуск;
здесь считается только отбор по рынку.
"""

from __future__ import annotations

from .http import MoexHttpError, get_json

ISS = "https://iss.moex.com/iss"

# Пороги по умолчанию — консервативные: остальное решает ранжирование по
# обороту. Всё переопределяется секцией «screener» в настройках.
DEFAULTS: dict = {
    "enabled": False,
    "board": "TQBR",
    # Оборот за торговый день в рублях (VALTODAY). Ранняя сессия: у
    # даже голубых фишек оборот ещё мал, поэтому порог скромный.
    "min_turnover_rub": 1_000_000.0,
    # Относительный спред (ASK-BID)/LAST в процентах.
    "max_spread_pct": 1.5,
    # Цена ниже рубля — марсовые бумаги с огромным спредом.
    "min_price": 1.0,
    # Уровень листинга (1/2 — основной/вторичный, 3 — допуск к обороту).
    "list_levels": [1, 2],
    # Сколько кандидатов в день отдавать в TradingAgents (бюджет LLM).
    "max_candidates_per_day": 3,
    # Ручной стоп-лист: эти тикеры скринер никогда не выбирает.
    "exclude": [],
}

# Причины отказа — стабильные имена для журнала и GUI.
REJECT_REASONS = ("not_trading", "no_price", "cheap", "illiquid",
                  "wide_spread", "low_list", "excluded")
DEFER_REASONS = ("decided", "holding")


def effective_config(settings: dict | None) -> dict:
    """Секция «screener» настроек поверх DEFAULTS с типовой валидацией."""
    raw = dict((settings or {}).get("screener") or {})
    cfg = dict(DEFAULTS)
    cfg["enabled"] = bool(raw.get("enabled"))
    board = str(raw.get("board") or "").strip().upper()
    if board:
        cfg["board"] = board
    for key in ("min_turnover_rub", "max_spread_pct", "min_price"):
        try:
            cfg[key] = max(0.0, float(raw.get(key, cfg[key])))
        except (TypeError, ValueError):
            continue
    try:
        cfg["max_candidates_per_day"] = max(1, int(
            raw.get("max_candidates_per_day", cfg["max_candidates_per_day"])))
    except (TypeError, ValueError):
        pass
    levels = raw.get("list_levels")
    if isinstance(levels, (list, tuple)):
        parsed = [int(x) for x in levels if str(x).strip().isdigit()]
        if parsed:
            cfg["list_levels"] = sorted(set(parsed))
    exclude = raw.get("exclude")
    if isinstance(exclude, (list, tuple)):
        cfg["exclude"] = sorted({str(t).strip().upper() for t in exclude
                                 if str(t).strip()})
    return cfg


def fetch_universe(board: str = "TQBR", *, ttl: float = 120.0) -> list[dict]:
    """Все акции борда одной страницей ISS (TTL-кэш, без пагинации).

    Возвращает построчно: secid, last, spread, spread_pct, turnover_rub
    (VALTODAY, руб.), trading_status, list_level, prev_close, issue_size.
    Пустые значения остаются None — фильтры честно отнесут их к
    «нет котировки», ничего не додумывая.
    """
    url = (f"{ISS}/engines/stock/markets/shares"
           f"/boards/{board}/securities.json")
    params = {
        "iss.meta": "off",
        "iss.only": "securities,marketdata",
        "securities.columns": "SECID,PREVLEGALCLOSEPRICE,ISSUESIZE,LISTLEVEL",
        "marketdata.columns": "SECID,LAST,BID,SPREAD,TRADINGSTATUS,VALTODAY",
    }
    data = get_json(url, params, ttl=ttl)
    if not isinstance(data, dict):
        raise MoexHttpError(f"неожиданный ответ ISS для борда {board}")

    def _rows(block) -> list[dict]:
        cols = (block or {}).get("columns") or []
        return [dict(zip(cols, row)) for row in (block or {}).get("data") or []]

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    out: list[dict] = []
    market = {str(r.get("SECID") or ""): r for r in _rows(data.get("marketdata"))}
    for sec in _rows(data.get("securities")):
        secid = str(sec.get("SECID") or "").strip().upper()
        if not secid:
            continue
        md = market.get(secid) or {}
        last = _num(md.get("LAST"))
        spread = _num(md.get("SPREAD"))
        turnover = _num(md.get("VALTODAY"))
        out.append({
            "secid": secid,
            "board": board,
            "last": last,
            "bid": _num(md.get("BID")),
            "spread": spread,
            "spread_pct": (spread / last * 100.0
                           if spread is not None and last else None),
            "turnover_rub": turnover if turnover is not None else 0.0,
            "trading_status": str(md.get("TRADINGSTATUS") or ""),
            "list_level": _int(sec.get("LISTLEVEL")),
            "prev_close": _num(sec.get("PREVLEGALCLOSEPRICE")),
            "issue_size": _int(sec.get("ISSUESIZE")),
        })
    return out


def select_candidates(rows: list[dict], cfg: dict,
                      *, decided_today: tuple | set = (),
                      holding: tuple | set = ()) -> dict:
    """Чистый отбор: фильтры → разбивка причин → ранжирование → топ-N.

    Ранжирование: дневной оборот ↓, затем спред ↑ — ликвидное и дешёвое
    в исполнении впереди. Никаких «скоров» с выдуманными весами: GUI
    показывает оборот и спред как есть.
    """
    decided = {str(t).upper() for t in decided_today}
    held = {str(t).upper() for t in holding}
    excluded = set(cfg.get("exclude") or ())
    min_price = float(cfg.get("min_price") or 0.0)
    min_turnover = float(cfg.get("min_turnover_rub") or 0.0)
    max_spread = float(cfg.get("max_spread_pct") or 0.0)
    levels = {int(x) for x in (cfg.get("list_levels") or ())}

    rejected: dict[str, int] = {}
    deferred: dict[str, list] = {"decided": [], "holding": []}
    passed: list[dict] = []

    def _reject(row: dict, reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1
        row["_rejected"] = reason

    for row in rows:
        secid = str(row.get("secid") or "").upper()
        if secid in excluded:
            _reject(row, "excluded")
            continue
        if str(row.get("trading_status") or "") != "T":
            _reject(row, "not_trading")
            continue
        last = row.get("last")
        if not last or float(last) <= 0:
            _reject(row, "no_price")
            continue
        if float(last) < min_price:
            _reject(row, "cheap")
            continue
        if float(row.get("turnover_rub") or 0.0) < min_turnover:
            _reject(row, "illiquid")
            continue
        spread_pct = row.get("spread_pct")
        if spread_pct is None or float(spread_pct) > max_spread:
            _reject(row, "wide_spread")
            continue
        if levels and row.get("list_level") not in (None, *levels):
            _reject(row, "low_list")
            continue
        # Очередь, а не отказ: вернётся в следующий торговый день.
        if secid in decided:
            deferred["decided"].append(secid)
            row["_deferred"] = "decided"
            continue
        if secid in held:
            deferred["holding"].append(secid)
            row["_deferred"] = "holding"
            continue
        passed.append(row)

    passed.sort(key=lambda r: (-float(r.get("turnover_rub") or 0.0),
                               float(r.get("spread_pct") or 0.0)))
    limit = max(1, int(cfg.get("max_candidates_per_day") or 1))
    return {
        "board": str(cfg.get("board") or "TQBR"),
        "scanned": len(rows),
        "passed": len(passed),
        "rejected": rejected,
        "deferred": deferred,
        "candidates": passed[:limit],
        "queue_limit": limit,
        "filters": {
            "min_turnover_rub": min_turnover,
            "max_spread_pct": max_spread,
            "min_price": min_price,
            "list_levels": sorted(levels),
        },
    }
