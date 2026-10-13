"""Config plumbing shared by patches and CLI (spec §4.1, §8).

Merges the MOEX market fragment into a run config without touching any
original key: original vendors/configs stay byte-identical for non-MOEX
markets; for MOEX we only ADD tool_vendors/data_vendors overrides plus
explicit market/ticker/board identifiers.
"""

from __future__ import annotations

from .gui_adapter import is_moex_config, moex_vendor_config, register_moex_vendors


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
    return merged


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
