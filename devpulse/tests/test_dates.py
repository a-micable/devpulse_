# tests/test_dates.py

import datetime
import pytest
from devpulse.utils.dates import (
    parse_dt, format_duration, format_relative,
    format_date, date_range, week_bounds, iso_week_label,
)


class TestParseDt:
    def test_iso_with_microseconds(self):
        dt = parse_dt("2025-03-15T10:30:00.123456")
        assert dt.year == 2025
        assert dt.microsecond == 123456

    def test_iso_without_microseconds(self):
        dt = parse_dt("2025-03-15T10:30:00")
        assert dt.minute == 30

    def test_date_only(self):
        dt = parse_dt("2025-03-15")
        assert dt.day == 15

    def test_none_returns_none(self):
        assert parse_dt(None) is None

    def test_invalid_returns_none(self):
        assert parse_dt("not-a-date") is None

    def test_passthrough_datetime(self):
        now = datetime.datetime.utcnow()
        assert parse_dt(now) is now


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_none(self):
        assert format_duration(None) == "0s"

    def test_seconds_only(self):
        assert format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert format_duration(90) == "1m 30s"

    def test_hours_minutes_seconds(self):
        assert format_duration(3723) == "1h 2m 3s"

    def test_exact_hour(self):
        assert format_duration(3600) == "1h"

    def test_negative(self):
        assert format_duration(-10) == "0s"


class TestFormatRelative:
    def _dt_ago(self, seconds):
        return datetime.datetime.utcnow() - datetime.timedelta(seconds=seconds)

    def test_just_now(self):
        assert format_relative(self._dt_ago(5)) == "just now"

    def test_minutes_ago(self):
        result = format_relative(self._dt_ago(300))
        assert result == "5m ago"

    def test_hours_ago(self):
        result = format_relative(self._dt_ago(7200))
        assert result == "2h ago"

    def test_yesterday(self):
        result = format_relative(self._dt_ago(86400 + 60))
        assert result == "yesterday"

    def test_days_ago(self):
        result = format_relative(self._dt_ago(86400 * 5))
        assert "days ago" in result

    def test_none_returns_empty(self):
        assert format_relative(None) == ""


class TestDateRange:
    def test_yields_correct_count(self):
        days = list(date_range(7))
        assert len(days) == 7

    def test_ordered_ascending(self):
        days = list(date_range(5))
        assert days == sorted(days)


class TestWeekBounds:
    def test_returns_two_strings(self):
        start, end = week_bounds()
        assert len(start) == 10
        assert len(end) == 10

    def test_start_before_end(self):
        start, end = week_bounds()
        assert start <= end

    def test_offset(self):
        s0, _ = week_bounds(0)
        s1, _ = week_bounds(-1)
        assert s1 < s0