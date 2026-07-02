import logging
from datetime import datetime
from typing import Any

from config import ADMIN_IDS

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Return True if the user is a configured admin."""
    return user_id in ADMIN_IDS


def paginate(items: list[Any], page: int, per_page: int = 8) -> tuple[list, int]:
    """Slice a list into pages. Returns (page_items, total_pages)."""
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return items[start: start + per_page], total_pages


def truncate(text: str, max_len: int = 40) -> str:
    """Truncate text with an ellipsis."""
    return text[:max_len] + "…" if len(text) > max_len else text


def is_within_schedule(start_str: str, end_str: str, timezone_str: str) -> bool:
    """Return True if the current time is within the active schedule window."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            tz = ZoneInfo(timezone_str)
        except (ZoneInfoNotFoundError, Exception):
            tz = ZoneInfo("UTC")
        now = datetime.now(tz).time()
    except ImportError:
        now = datetime.utcnow().time()

    try:
        from datetime import time as dtime
        start = datetime.strptime(start_str, "%H:%M").time()
        end   = datetime.strptime(end_str,   "%H:%M").time()
    except ValueError:
        return True  # Fail open

    if start <= end:
        return start <= now <= end
    # Overnight window (e.g., 22:00 → 06:00)
    return now >= start or now <= end


def format_uptime(start_time: datetime) -> str:
    """Return a human-readable uptime string."""
    delta = datetime.now() - start_time
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours >= 24:
        days = hours // 24
        hours %= 24
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s"
