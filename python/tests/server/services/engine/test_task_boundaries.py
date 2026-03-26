"""Tests for task editing boundary helpers."""

from src.server.services.engine.task_boundaries import (
    normalize_path_rules,
    task_has_boundary_rules,
    validate_task_boundaries,
)


def test_normalize_path_rules_trims_and_normalizes_separators():
    assert normalize_path_rules([" ./src/server/** ", "src\\store\\**", "", None]) == [
        "src/server/**",
        "src/store/**",
    ]


def test_task_has_boundary_rules_detects_any_rule():
    assert task_has_boundary_rules({"allowed_paths": ["src/**"]}) is True
    assert task_has_boundary_rules({"forbidden_paths": ["docs/**"]}) is True
    assert task_has_boundary_rules({"allowed_paths": [], "forbidden_paths": []}) is False


def test_validate_task_boundaries_marks_clean_when_changes_are_in_scope():
    result = validate_task_boundaries(
        {"allowed_paths": ["src/server/**"], "forbidden_paths": ["src/store/**"]},
        ["src/server/task_engine.py"],
    )

    assert result["status"] == "clean"
    assert result["violations"] == []


def test_validate_task_boundaries_marks_violation_for_forbidden_or_outside_paths():
    result = validate_task_boundaries(
        {"allowed_paths": ["src/server/**"], "forbidden_paths": ["src/store/**"]},
        ["src/store/office_store.py", "docs/guide.md"],
    )

    assert result["status"] == "violation"
    violation_types = {item["rule_type"] for item in result["violations"]}
    assert "forbidden" in violation_types
    assert "outside-allowed" in violation_types
