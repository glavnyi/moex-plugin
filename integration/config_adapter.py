"""Config plumbing shared by patches and CLI (spec §4.1, §8).

Merges the MOEX market fragment into a run config without touching any
original key: original vendors/configs stay byte-identical for non-MOEX
markets; for MOEX we only ADD tool_vendors/data_vendors overrides plus
explicit market/ticker/board identifiers.
"""

from __future__ import annotations

import logging

from .gui_adapter import is_moex_config, moex_vendor_config, register_moex_vendors

logger = logging.getLogger(__name__)


def enrich_run_config(params: dict, config: dict) -> dict:
    """Called from the patched run_manager._build_config.

    ``params`` carries the user's run parameters (must contain market,
    ticker, optional board); ``config`` is the merged GUI config.
    Returns the config to hand to TradingAgentsGraph.
    """
    market = str(params.get("market") or config.get("market") or "").upper()
    if market != "MOEX":
        return config  # untouched original behaviour for other markets

    try:
        register_moex_vendors()  # GUI vendored copy (dataflows.interface)
    except ImportError:
        from .core_adapter import install as _install_standalone

        _install_standalone()  # standalone copy (dataflows.router)
    # Settlement (alpha vs a MOEX index) wraps the graph class once per
    # process; without it yfinance never returns Russian closes and every
    # decision stays pending, which also starves the PM's past_context.
    try:
        from .moex_settlement import install as _install_settlement

        if not _install_settlement().get("gui"):
            logger.warning("MOEX settlement hooks are not installed — outcomes "
                           "will stay pending and past_context stays empty")
    except Exception as exc:  # noqa: BLE001 — an unwired settlement must not block the run
        logger.warning("MOEX settlement not installed: %s", exc)
    ticker = str(params.get("ticker") or config.get("ticker") or "").strip()
    board = params.get("board") or config.get("board") or ""
    fragment = moex_vendor_config(ticker, str(board) if board else None)

    merged = dict(config)
    # tool/data vendor overrides only for MOEX; never delete original keys
    merged["tool_vendors"] = {
        **(config.get("tool_vendors") or {}),
        **fragment["tool_vendors"],
    }
    merged["data_vendors"] = {
        **(config.get("data_vendors") or {}),
        **fragment["data_vendors"],
    }
    merged["market"] = "MOEX"
    merged["ticker"] = ticker.upper()
    merged["board"] = fragment["board"]
    # Real-data snapshot for analyst prompts: free/edge models frequently
    # never emit tool calls, so without this the analysts fabricate prices.
    try:
        from .gui_adapter import build_moex_snapshot

        snapshot = build_moex_snapshot(ticker, str(fragment["board"] or ""))
    except Exception:  # noqa: BLE001 — snapshot is best-effort, never fatal
        snapshot = ""
    # §11 step 8: lessons from closed trades ride the same prompt channel, so
    # every analyst sees what past decisions actually earned. Written ahead of
    # the snapshot's own "every figure below comes from MOEX ISS" banner, so
    # the lessons are not framed as ISS data.
    #
    # A point-in-time run (scripts/backtest_moex) closes this channel with
    # moex_lessons=False: a lesson recorded in 2026 is knowledge of the
    # future for a cell dated 2023.
    if _lessons_wanted(params):
        try:
            from moex.analytics import lessons_block

            lessons = lessons_block()
        except Exception:  # noqa: BLE001 — an unreadable lesson store must not block a run
            lessons = ""
        if lessons:
            snapshot = "\n\n".join(x for x in (lessons, snapshot) if x)
    if snapshot:
        merged["moex_data_snapshot"] = snapshot
    return merged


def _lessons_wanted(params: dict) -> bool:
    """False/0/off — «уроки» §11 в этот запуск не подмешивать (бэктест)."""
    v = (params or {}).get("moex_lessons", True)
    if isinstance(v, str):
        return v.strip().lower() not in {"0", "false", "off", "no"}
    return bool(v)


def describe_config(config: dict) -> dict:
    """Diagnostics for the GUI status endpoint (no secrets)."""
    cfg = config or {}
    return {
        "market": str(cfg.get("market") or "").upper() or "US (default)",
        "ticker": cfg.get("ticker") or "",
        "board": cfg.get("board") or "",
        "is_moex": is_moex_config(cfg),
        "tool_vendors": cfg.get("tool_vendors") or {},
        "data_vendors": cfg.get("data_vendors") or {},
    }
