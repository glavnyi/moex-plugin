"""Risk limits for sandbox trading (spec §6.3)."""

from .checks import (
    DEFAULT_RISK,
    RiskResult,
    _daily_realized_pnl,
    check_proposal,
    effective_limits,
    size_position,
)

# Public name for the Moscow-day realized PnL (§6.3) — shared by the risk
# gate and the auto-trading cycle so both read the same number.
daily_realized_pnl = _daily_realized_pnl

__all__ = [
    "DEFAULT_RISK",
    "RiskResult",
    "check_proposal",
    "daily_realized_pnl",
    "effective_limits",
    "size_position",
]
