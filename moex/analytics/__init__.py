"""Analytics: proposal extraction, analysis context, order workflow, lessons,
sandbox dashboard (balance / positions / realized PnL windows)."""

from .dashboard import build_dashboard, pnl_windows
from .deals import detect_closed_deals, sync_lessons_from_operations
from .proposal import TradeProposal, extract_proposal, normalize_side
from .workflow import (
    build_analysis_context,
    delete_lesson,
    lessons_block,
    load_lessons,
    map_order_state,
    place_confirmed_order,
    prepare_order,
    record_lesson,
    set_lesson_enabled,
    track_order,
)

__all__ = [
    "TradeProposal",
    "build_analysis_context",
    "build_dashboard",
    "delete_lesson",
    "detect_closed_deals",
    "extract_proposal",
    "lessons_block",
    "load_lessons",
    "map_order_state",
    "normalize_side",
    "place_confirmed_order",
    "pnl_windows",
    "prepare_order",
    "record_lesson",
    "set_lesson_enabled",
    "sync_lessons_from_operations",
    "track_order",
]
