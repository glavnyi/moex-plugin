"""Register MOEX vendors into a standalone TradingAgents v0.7.0 install.

Standalone keeps the same ``VENDOR_METHODS``/``route_to_vendor``
dispatch, but inside ``tradingagents.dataflows.router``. The impls are
shared with the GUI adapter (same upstream signatures, same refusal to
serve non-MOEX configs).
"""

from __future__ import annotations

from .gui_adapter import (
    MOEX_CATEGORIES,
    MOEX_METHODS,
    _IMPLS,
    is_moex_config,
    moex_vendor_config,
    register_moex_vendors as _register_gui,  # noqa: F401 — shared impls
)

_REGISTERED = False


def register_moex_vendors_standalone() -> list[str]:
    """Idempotently add the ``moex`` vendor to standalone router table."""
    global _REGISTERED
    from tradingagents.dataflows import router

    added = []
    for method, fn in _IMPLS.items():
        table = router.VENDOR_METHODS.setdefault(method, {})
        if "moex" not in table:
            table["moex"] = fn
            added.append(method)
    _REGISTERED = True
    return added


def install(market: str = "MOEX") -> dict:
    """Best-effort: register into whichever copy of tradingagents is importable."""
    result = {"gui": [], "standalone": []}
    try:
        result["gui"] = _register_gui()
    except Exception as exc:  # noqa: BLE001 — GUI copy may be absent
        result["gui_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["standalone"] = register_moex_vendors_standalone()
    except Exception as exc:  # noqa: BLE001 — standalone copy may be absent
        result["standalone_error"] = f"{type(exc).__name__}: {exc}"
    return result


__all__ = [
    "MOEX_CATEGORIES",
    "MOEX_METHODS",
    "install",
    "is_moex_config",
    "moex_vendor_config",
    "register_moex_vendors_standalone",
]
