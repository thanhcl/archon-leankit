"""Tests for task decomposition (C-P7-02)."""

import pytest

from src.server.services.engine.task_decomposer import TaskDecomposer


def _make_task(**overrides):
    base = {
        "id": "t1",
        "title": "Complex Feature",
        "priority": "high",
        "complexity": "simple",
        "parent_task_id": None,
        "decomposition_mode": "none",
        "acceptance_criteria": [],
        "allowed_paths": ["src/"],
        "tags": ["backend"],
    }
    base.update(overrides)
    return base


class TestShouldDecompose:

    def test_simple_task_no_decomposition(self):
        decomposer = TaskDecomposer()
        should, reason = decomposer.should_decompose(_make_task())
        assert should is False

    def test_child_task_never_decomposes(self):
        decomposer = TaskDecomposer()
        should, reason = decomposer.should_decompose(_make_task(parent_task_id="parent1"))
        assert should is False
        assert "child" in reason

    def test_already_coordinator(self):
        decomposer = TaskDecomposer()
        should, reason = decomposer.should_decompose(_make_task(decomposition_mode="coordinator"))
        assert should is False

    def test_architect_review_suggests_children(self):
        decomposer = TaskDecomposer()
        review = {
            "decomposition": {
                "children": [
                    {"title": "A"},
                    {"title": "B"},
                ],
            },
        }
        should, reason = decomposer.should_decompose(_make_task(), review)
        assert should is True
        assert "2 children" in reason

    def test_architect_feedback_mentions_split(self):
        decomposer = TaskDecomposer()
        review = {"feedback": "This task should be split into smaller units"}
        should, reason = decomposer.should_decompose(_make_task(), review)
        assert should is True

    def test_complex_with_many_criteria(self):
        decomposer = TaskDecomposer()
        task = _make_task(
            complexity="complex",
            acceptance_criteria=[f"AC-{i}" for i in range(6)],
        )
        should, reason = decomposer.should_decompose(task)
        assert should is True
        assert "6 acceptance criteria" in reason

    def test_complex_with_few_criteria_no_decompose(self):
        decomposer = TaskDecomposer()
        task = _make_task(complexity="complex", acceptance_criteria=["AC-1", "AC-2"])
        should, reason = decomposer.should_decompose(task)
        assert should is False


class TestBuildDecompositionSpecs:

    def test_uses_architect_children(self):
        decomposer = TaskDecomposer()
        review = {
            "decomposition": {
                "children": [
                    {"title": "Auth module", "description": "Implement auth", "priority": "high"},
                    {"title": "Tests", "description": "Write tests", "priority": "medium"},
                ],
            },
        }
        specs = decomposer.build_decomposition_specs(_make_task(), review)
        assert len(specs) == 2
        assert specs[0]["title"] == "Auth module"
        assert specs[1]["title"] == "Tests"

    def test_falls_back_to_criteria_split(self):
        decomposer = TaskDecomposer()
        task = _make_task(
            acceptance_criteria=[
                {"description": f"Criterion {i}"} for i in range(6)
            ],
        )
        specs = decomposer.build_decomposition_specs(task)
        assert len(specs) >= 2
        assert "Part" in specs[0]["title"]

    def test_no_criteria_returns_empty(self):
        decomposer = TaskDecomposer()
        specs = decomposer.build_decomposition_specs(_make_task())
        assert specs == []

    def test_max_children_limit(self):
        decomposer = TaskDecomposer(max_children=3)
        review = {
            "decomposition": {
                "children": [{"title": f"Child {i}"} for i in range(10)],
            },
        }
        specs = decomposer.build_decomposition_specs(_make_task(), review)
        assert len(specs) == 3

    def test_inherits_parent_paths(self):
        decomposer = TaskDecomposer()
        task = _make_task(allowed_paths=["src/auth/"])
        review = {
            "decomposition": {
                "children": [{"title": "Child", "description": "Do work"}],
            },
        }
        specs = decomposer.build_decomposition_specs(task, review)
        assert specs[0]["allowed_paths"] == ["src/auth/"]

    def test_child_overrides_parent_paths(self):
        decomposer = TaskDecomposer()
        review = {
            "decomposition": {
                "children": [
                    {"title": "Child", "allowed_paths": ["tests/"]},
                ],
            },
        }
        specs = decomposer.build_decomposition_specs(_make_task(), review)
        assert specs[0]["allowed_paths"] == ["tests/"]

    def test_string_criteria_handled(self):
        decomposer = TaskDecomposer()
        task = _make_task(acceptance_criteria=["AC1", "AC2", "AC3", "AC4"])
        specs = decomposer.build_decomposition_specs(task)
        assert len(specs) >= 2
