"""Integration layer: MOEX vendors ⇄ TradingAgents (GUI + standalone)."""

from .config_adapter import describe_config, enrich_run_config
from .core_adapter import install as install_core
from .gui_adapter import (
    is_moex_config,
    moex_vendor_config,
    register_moex_vendors,
)
from .version_check import check as version_check

__all__ = [
    "describe_config",
    "enrich_run_config",
    "install_core",
    "is_moex_config",
    "moex_vendor_config",
    "register_moex_vendors",
    "version_check",
]
