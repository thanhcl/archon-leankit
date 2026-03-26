"""Tests for predicted task-overlap helpers."""

from src.server.services.engine.task_conflicts import predict_task_overlap, task_scope_rules


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Test task",
        "allowed_paths": [],
        "repo_guidance_packs": [],
    }
    base.update(overrides)
    return base


def test_task_scope_rules_merges_allowed_paths_and_repo_guidance_scopes():
    task = _make_task(
        allowed_paths=["src/server/**"],
        repo_guidance_packs=[
            {
                "title": "API layer",
                "guidance": "Keep routes thin",
                "path_scope": ["src/server/api_routes/**", "src/server/**"],
            }
        ],
    )

    assert task_scope_rules(task) == ["src/server/**", "src/server/api_routes/**"]


def test_predict_task_overlap_detects_nested_scope_overlap():
    candidate = _make_task(
        id="task-002",
        title="Candidate",
        allowed_paths=["src/server/auth/**"],
    )
    active = _make_task(
        id="task-003",
        title="Active auth refactor",
        repo_guidance_packs=[
            {
                "title": "Server discipline",
                "guidance": "Keep engine services stable",
                "path_scope": ["src/server/**"],
            }
        ],
    )

    conflict = predict_task_overlap(candidate, active)

    assert conflict is not None
    assert conflict["status"] == "predicted-overlap"
    assert conflict["active_task_id"] == "task-003"
    assert "src/server" in conflict["overlapping_roots"]


def test_predict_task_overlap_ignores_different_projects():
    candidate = _make_task(id="task-002", project_id="proj-001", allowed_paths=["src/server/**"])
    active = _make_task(id="task-003", project_id="proj-999", allowed_paths=["src/server/**"])

    assert predict_task_overlap(candidate, active) is None


def test_predict_task_overlap_skips_tasks_without_predictable_scope():
    candidate = _make_task(id="task-002")
    active = _make_task(id="task-003", allowed_paths=["src/server/**"])

    assert predict_task_overlap(candidate, active) is None
