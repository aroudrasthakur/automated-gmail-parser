"""Calendar-day helpers in America/Chicago (inbox 'today' for SweList)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")


def chicago_now() -> datetime:
    return datetime.now(CHICAGO)


def chicago_today() -> date:
    return chicago_now().date()


def parse_chicago_date(value: str) -> date:
    """Parse YYYY-MM-DD as a Chicago calendar date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def day_utc_bounds(d: date) -> tuple[int, int]:
    """Unix seconds [after, before) for one Chicago calendar day."""
    start = datetime.combine(d, time.min, tzinfo=CHICAGO)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def days_in_range(total_days: int, skip_recent_days: int) -> list[date]:
    """Chicago calendar dates included when scanning `total_days` back, skipping the most recent window."""
    if total_days <= 0 or skip_recent_days >= total_days:
        return []
    today = chicago_today()
    oldest = today - timedelta(days=total_days - 1)
    end = today if skip_recent_days == 0 else today - timedelta(days=skip_recent_days)
    out = []
    d = oldest
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def range_utc_bounds(total_days: int, skip_recent_days: int) -> tuple[int, int] | None:
    """Unix [after, before) covering Chicago calendar days in the range."""
    days = days_in_range(total_days, skip_recent_days)
    if not days:
        return None
    after, _ = day_utc_bounds(days[0])
    _, before = day_utc_bounds(days[-1])
    return after, before


def bounds_for_days(days: list[date]) -> tuple[int, int] | None:
    if not days:
        return None
    after, _ = day_utc_bounds(min(days))
    _, before = day_utc_bounds(max(days))
    return after, before
