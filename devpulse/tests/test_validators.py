# tests/test_validators.py

import pytest
from devpulse.utils.validators import (
    coerce_int, coerce_float, clamp,
    sanitize_tag, sanitize_metric, sanitize_period,
    sanitize_format, sanitize_text,
    validate_repo_path, validate_add_repo,
    validate_add_goal, validate_export_params,
)


class TestCoerce:
    def test_coerce_int_valid(self):
        assert coerce_int("42") == 42

    def test_coerce_int_invalid(self):
        assert coerce_int("abc", default=5) == 5

    def test_coerce_int_clamp(self):
        assert coerce_int("200", lo=0, hi=100) == 100

    def test_coerce_float_valid(self):
        assert coerce_float("3.14") == pytest.approx(3.14)

    def test_coerce_float_invalid(self):
        assert coerce_float("x", default=1.0) == 1.0

    def test_clamp(self):
        assert clamp(150, 0, 100) == 100
        assert clamp(-5, 0, 100) == 0
        assert clamp(50, 0, 100) == 50


class TestSanitize:
    def test_tag_valid(self):
        assert sanitize_tag("feature-auth") == "feature-auth"

    def test_tag_invalid_space(self):
        assert sanitize_tag("tag with space") is None

    def test_tag_too_long(self):
        assert sanitize_tag("a" * 65) is None

    def test_metric_valid(self):
        assert sanitize_metric("coding_time") == "coding_time"

    def test_period_valid(self):
        assert sanitize_period("weekly") == "weekly"

    def test_period_invalid(self):
        assert sanitize_period("yearly") is None

    def test_format_valid(self):
        assert sanitize_format("csv") == "csv"

    def test_format_invalid_falls_back(self):
        assert sanitize_format("xlsx") == "csv"

    def test_text_truncates(self):
        assert len(sanitize_text("x" * 5000, max_len=100)) == 100


class TestValidateRepoPath:
    def test_nonexistent_dir(self):
        _, err = validate_repo_path("/nonexistent/path/xyz")
        assert err is not None

    def test_empty(self):
        _, err = validate_repo_path("")
        assert err is not None

    def test_none(self):
        _, err = validate_repo_path(None)
        assert err is not None

    def test_valid_git_repo(self, git_repo):
        path, err = validate_repo_path(git_repo)
        assert err is None
        assert path == git_repo


class TestValidateAddGoal:
    def test_valid(self):
        out, errors = validate_add_goal({
            "metric": "coding_time",
            "target": 3600,
            "period": "daily",
        })
        assert not errors
        assert out["metric"] == "coding_time"
        assert out["target"] == 3600.0

    def test_missing_metric(self):
        _, errors = validate_add_goal({"target": 10, "period": "weekly"})
        assert any("metric" in e for e in errors)

    def test_invalid_period(self):
        _, errors = validate_add_goal({
            "metric": "commits", "target": 5, "period": "yearly"
        })
        assert any("period" in e for e in errors)

    def test_zero_target(self):
        _, errors = validate_add_goal({
            "metric": "commits", "target": 0, "period": "daily"
        })
        assert any("target" in e for e in errors)


class TestValidateExportParams:
    def test_defaults(self):
        out, errors = validate_export_params({})
        assert not errors
        assert out["fmt"]  == "csv"
        assert out["days"] == 30

    def test_custom(self):
        out, _ = validate_export_params({"format": "json", "days": "14"})
        assert out["fmt"]  == "json"
        assert out["days"] == 14

    def test_days_clamped(self):
        out, _ = validate_export_params({"days": "9999"})
        assert out["days"] == 365