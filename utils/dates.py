"""
utils/dates.py — Date math helpers used throughout the bot.
"""

import re
from datetime import date, datetime, timedelta, timezone

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def week_start(from_date: date | None = None) -> date:
    """Return the Monday of the given date's week (defaults to today)."""
    d = from_date or date.today()
    return d - timedelta(days=d.weekday())


def week_start_str(from_date: date | None = None) -> str:
    """ISO string for the Monday of the current week (used as poll identity key)."""
    return week_start(from_date).isoformat()


def date_for_day_this_week(day_int: int, from_date: date | None = None) -> date:
    """Return the actual date for weekday day_int (0=Mon) in the current week."""
    monday = week_start(from_date)
    return monday + timedelta(days=day_int)


def next_session_date(session_weekday: int, from_date: date | None = None) -> date:
    """Return the next occurrence of session_weekday on or after from_date."""
    d = from_date or date.today()
    days_ahead = (session_weekday - d.weekday()) % 7
    return d + timedelta(days=days_ahead)


def day_int_to_name(n: int) -> str:
    """0 → 'Monday', 6 → 'Sunday'."""
    return DAY_NAMES[n]


def day_int_to_short(n: int) -> str:
    """0 → 'Mon', 6 → 'Sun'."""
    return DAY_SHORT[n]


def friendly_date(d: date) -> str:
    """'Saturday, Jun 7' style."""
    return d.strftime("%A, %b %-d")


# ── Time parsing ──────────────────────────────────────────────────────────────

def _parse_single_time(s: str) -> int | None:
    """Parse a single time string → minutes since midnight. Returns None if invalid."""
    s = s.strip().lower().replace(".", "")
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    ampm = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif ampm is None and 1 <= hour <= 6:
        hour += 12  # Ambiguous small number → assume PM
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def parse_time_range(s: str) -> tuple[int, int] | None:
    """
    Parse a time-range string like '10am - 5pm' or '10:30 to 17:00'.
    Returns (start_mins, end_mins) as minutes-since-midnight, or None if unparseable.
    """
    parts = re.split(r'\s*[-–—]\s*|\s+to\s+', s.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    start = _parse_single_time(parts[0])
    end = _parse_single_time(parts[1])
    if start is None or end is None or start >= end:
        return None
    return (start, end)


def minutes_to_time_str(mins: int) -> str:
    """Convert minutes-since-midnight to a readable string. 600 → '10:00am'."""
    h, m = divmod(mins, 60)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{suffix}"


# ── Session scheduling ────────────────────────────────────────────────────────

def compute_session_start(
    winning_day: int,
    all_windows: dict[str, dict[int, tuple[int, int]]],
    min_hours: float,
) -> int | None:
    """
    Compute the latest common start time (minutes since midnight) for winning_day.
    Uses the latest start across all players who specified a window, then checks
    that the minimum session length fits before the earliest end time.
    Returns None if no windows are set or the windows are incompatible.
    all_windows: {user_id: {day_of_week: (start_mins, end_mins)}}
    """
    windows = [w[winning_day] for w in all_windows.values() if winning_day in w]
    if not windows:
        return None
    latest_start = max(s for s, e in windows)
    earliest_end = min(e for s, e in windows)
    if latest_start + int(min_hours * 60) > earliest_end:
        return None  # Minimum session length doesn't fit in the overlap
    return latest_start


def build_event_datetimes(
    winning_day: int,
    week_start_iso: str,
    start_mins: int | None,
    cfg_row: dict,
) -> tuple[datetime, datetime]:
    """
    Return (start_dt, end_dt) as timezone-aware UTC datetimes for a Discord event.
    Falls back to config session_time or 7 PM if start_mins is not determined.
    week_start_iso: ISO date string for the Monday of the poll week.
    """
    monday = date.fromisoformat(week_start_iso)
    event_date = monday + timedelta(days=winning_day)
    tz_offset = int(cfg_row.get("timezone_offset") or 0)
    tz = timezone(timedelta(hours=tz_offset))

    if start_mins is not None:
        hour, minute = divmod(start_mins, 60)
    else:
        parsed = _parse_single_time(cfg_row.get("session_time") or "")
        hour, minute = divmod(parsed, 60) if parsed is not None else (19, 0)

    min_hours = float(cfg_row.get("min_session_hours") or 2.0)
    start_dt = datetime(event_date.year, event_date.month, event_date.day, hour, minute, tzinfo=tz)
    # If the computed time is in the past, move to the same day next week
    if start_dt <= datetime.now(timezone.utc):
        start_dt += timedelta(weeks=1)
    end_dt = start_dt + timedelta(hours=min_hours)
    return start_dt, end_dt
