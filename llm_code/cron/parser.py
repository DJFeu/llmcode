"""Cron expression parser — 5-field standard format (local time).

Fields: minute(0-59) hour(0-23) day-of-month(1-31) month(1-12) day-of-week(0-6, 0=Sunday)
Syntax: * (all), N (single), N-M (range), N,M (list), */N or N-M/N (step)
"""
from __future__ import annotations

import dataclasses
import datetime

_FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 6),
}

_FIELD_ORDER = ("minute", "hour", "day_of_month", "month", "day_of_week")


@dataclasses.dataclass(frozen=True)
class CronExpression:
    minute: tuple[int | str, ...]
    hour: tuple[int | str, ...]
    day_of_month: tuple[int | str, ...]
    month: tuple[int | str, ...]
    day_of_week: tuple[int | str, ...]


def _parse_field(token: str, field_name: str) -> tuple[int, ...]:
    """Parse a single cron field token into a sorted tuple of valid integers."""
    lo, hi = _FIELD_RANGES[field_name]

    if token == "*":
        return tuple(range(lo, hi + 1))

    # Handle step: */N or range/N
    if "/" in token:
        base, step_str = token.split("/", 1)
        step = int(step_str)
        if step <= 0:
            raise ValueError(f"Invalid step value in {field_name}: {step}")
        if base == "*":
            return tuple(range(lo, hi + 1, step))
        if "-" in base:
            rlo, rhi = (int(x) for x in base.split("-", 1))
        else:
            rlo, rhi = int(base), hi
        _validate_range(rlo, rhi, lo, hi, field_name)
        return tuple(range(rlo, rhi + 1, step))

    # Handle list: N,M,...
    if "," in token:
        values = sorted(int(x) for x in token.split(","))
        for v in values:
            if v < lo or v > hi:
                raise ValueError(f"Value {v} out of range for {field_name} ({lo}-{hi})")
        return tuple(values)

    # Handle range: N-M
    if "-" in token:
        rlo, rhi = (int(x) for x in token.split("-", 1))
        _validate_range(rlo, rhi, lo, hi, field_name)
        return tuple(range(rlo, rhi + 1))

    # Single value
    val = int(token)
    if val < lo or val > hi:
        raise ValueError(f"Value {val} out of range for {field_name} ({lo}-{hi})")
    return (val,)


def _validate_range(rlo: int, rhi: int, lo: int, hi: int, field_name: str) -> None:
    if rlo < lo or rhi > hi or rlo > rhi:
        raise ValueError(f"Invalid range {rlo}-{rhi} for {field_name} ({lo}-{hi})")


def parse_cron(expr: str) -> CronExpression:
    """Parse a 5-field cron expression string into a CronExpression."""
    tokens = expr.strip().split()
    if len(tokens) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(tokens)}: '{expr}'")

    fields: dict[str, tuple[int, ...]] = {}
    for token, field_name in zip(tokens, _FIELD_ORDER):
        try:
            fields[field_name] = _parse_field(token, field_name)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid {field_name} field '{token}': {exc}") from exc

    return CronExpression(**fields)


def _python_weekday_to_cron(py_wd: int) -> int:
    """Convert Python weekday (0=Mon) to cron weekday (0=Sun)."""
    return (py_wd + 1) % 7


def next_fire_time(
    expr: CronExpression,
    after: datetime.datetime,
) -> datetime.datetime:
    """Return the next datetime matching the cron expression, strictly after `after`.

    Uses local (naive) time. Raises ValueError if no match found within 1 year.
    """
    # Start from the next minute boundary
    candidate = after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    limit = after + datetime.timedelta(days=366)

    while candidate <= limit:
        cron_dow = _python_weekday_to_cron(candidate.weekday())

        if (
            candidate.month in expr.month
            and candidate.day in expr.day_of_month
            and cron_dow in expr.day_of_week
            and candidate.hour in expr.hour
            and candidate.minute in expr.minute
        ):
            return candidate

        # Advance: skip non-matching months, days, hours, minutes efficiently
        if candidate.month not in expr.month:
            # Jump to first day of next month
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                candidate = candidate.replace(month=candidate.month + 1, day=1, hour=0, minute=0)
            continue

        if candidate.day not in expr.day_of_month or cron_dow not in expr.day_of_week:
            candidate = (candidate + datetime.timedelta(days=1)).replace(hour=0, minute=0)
            continue

        if candidate.hour not in expr.hour:
            candidate = (candidate + datetime.timedelta(hours=1)).replace(minute=0)
            continue

        candidate += datetime.timedelta(minutes=1)

    raise ValueError(f"Cron expression has no matching time within 1 year after {after}")
