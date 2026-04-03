"""Tests for cron expression parser."""
from __future__ import annotations

import datetime

import pytest

from llm_code.cron.parser import CronExpression, parse_cron, next_fire_time


class TestParseCron:
    def test_all_wildcards(self):
        expr = parse_cron("* * * * *")
        assert expr.minute == tuple(range(0, 60))
        assert expr.hour == tuple(range(0, 24))
        assert expr.day_of_month == tuple(range(1, 32))
        assert expr.month == tuple(range(1, 13))
        assert expr.day_of_week == tuple(range(0, 7))

    def test_single_values(self):
        expr = parse_cron("30 14 1 6 3")
        assert expr.minute == (30,)
        assert expr.hour == (14,)
        assert expr.day_of_month == (1,)
        assert expr.month == (6,)
        assert expr.day_of_week == (3,)

    def test_range(self):
        expr = parse_cron("0-5 * * * *")
        assert expr.minute == tuple(range(0, 6))

    def test_list(self):
        expr = parse_cron("0,15,30,45 * * * *")
        assert expr.minute == (0, 15, 30, 45)

    def test_step(self):
        expr = parse_cron("*/10 * * * *")
        assert expr.minute == (0, 10, 20, 30, 40, 50)

    def test_range_with_step(self):
        expr = parse_cron("10-30/5 * * * *")
        assert expr.minute == (10, 15, 20, 25, 30)

    def test_combined_fields(self):
        expr = parse_cron("0 9,17 * * 1-5")
        assert expr.hour == (9, 17)
        assert expr.day_of_week == (1, 2, 3, 4, 5)

    def test_invalid_too_few_fields(self):
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("* * *")

    def test_invalid_out_of_range_minute(self):
        with pytest.raises(ValueError, match="minute"):
            parse_cron("60 * * * *")

    def test_invalid_out_of_range_hour(self):
        with pytest.raises(ValueError, match="hour"):
            parse_cron("0 25 * * *")

    def test_invalid_syntax(self):
        with pytest.raises(ValueError):
            parse_cron("abc * * * *")


class TestNextFireTime:
    def test_every_minute(self):
        expr = parse_cron("* * * * *")
        after = datetime.datetime(2026, 4, 3, 10, 30, 15)
        nxt = next_fire_time(expr, after)
        assert nxt == datetime.datetime(2026, 4, 3, 10, 31)

    def test_specific_time(self):
        expr = parse_cron("0 9 * * *")
        after = datetime.datetime(2026, 4, 3, 10, 0, 0)
        nxt = next_fire_time(expr, after)
        assert nxt == datetime.datetime(2026, 4, 4, 9, 0)

    def test_wraps_month(self):
        expr = parse_cron("0 0 1 * *")
        after = datetime.datetime(2026, 4, 2, 0, 0, 0)
        nxt = next_fire_time(expr, after)
        assert nxt == datetime.datetime(2026, 5, 1, 0, 0)

    def test_specific_dow(self):
        # 2026-04-03 is Friday (day_of_week=4 in 0=Mon convention, 5 in 0=Sun)
        # Using 0=Sunday: Friday=5
        expr = parse_cron("0 12 * * 1")  # Monday
        after = datetime.datetime(2026, 4, 3, 13, 0, 0)  # Friday afternoon
        nxt = next_fire_time(expr, after)
        assert nxt.weekday() == 0  # Monday in Python weekday()

    def test_no_match_raises_after_one_year(self):
        # Feb 30 never exists
        expr = parse_cron("0 0 30 2 *")
        after = datetime.datetime(2026, 1, 1, 0, 0, 0)
        with pytest.raises(ValueError, match="no matching"):
            next_fire_time(expr, after)
