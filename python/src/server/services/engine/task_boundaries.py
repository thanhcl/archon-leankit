"""Task editing boundary helpers for execution runners."""

from __future__ import annotations

import fnmatch
from typing import Any


def normalize_path_rules(paths: list[str] | None) -> list[str]:
    """Normalize repo-relative glob patterns into a stable list."""
    if not paths:
        return []

    normalized: list[str] = []
    for path in paths:
        if not isinstance(path, str):
            continue
        trimmed = path.strip().replace("\\", "/").lstrip("./")
        if trimmed:
            normalized.append(trimmed)
    return normalized


def normalize_repo_path(path: str | None) -> str | None:
    """Normalize a repo-relative path for boundary matching."""
    if not isinstance(path, str):
        return None
    trimmed = path.strip().replace("\\", "/").lstrip("./")
    return trimmed or None


def task_has_boundary_rules(task: dict[str, Any]) -> bool:
    """Return whether the task has any editing boundaries configured."""
    return bool(
        normalize_path_rules(task.get("allowed_paths"))
        or normalize_path_rules(task.get("forbidden_paths"))
    )


def _matches_any_allowed(path: str, allowed_paths: list[str]) -> bool:
    """Check whether *path* is permitted by any rule in *allowed_paths*.

    Supports two matching modes:
    - **Directory prefix**: rules ending with ``/`` match any file under
      that directory (e.g. ``tests/engine/`` allows ``tests/engine/test_foo.py``).
    - **Glob pattern**: all other rules are matched via ``fnmatch``.
    """
    for rule in allowed_paths:
        if rule.endswith("/"):
            if path.startswith(rule) or path == rule.rstrip("/"):
                return True
        else:
            if fnmatch.fnmatch(path, rule) or path == rule:
                return True
    return False


def validate_task_boundaries(task: dict[str, Any], changed_files: list[str]) -> dict[str, Any]:
    """Validate changed files against configured allowed/forbidden path rules."""
    allowed_paths = normalize_path_rules(task.get("allowed_paths"))
    forbidden_paths = normalize_path_rules(task.get("forbidden_paths"))
    normalized_changed = sorted({
        path
        for raw in changed_files
        if (path := normalize_repo_path(raw)) is not None
    })

    payload: dict[str, Any] = {
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "changed_files": normalized_changed,
        "violations": [],
    }

    if not allowed_paths and not forbidden_paths:
        payload["status"] = "not-configured"
        return payload

    if not normalized_changed:
        payload["status"] = "no-changes"
        return payload

    violations: list[dict[str, str]] = []
    for path in normalized_changed:
        for forbidden_rule in forbidden_paths:
            if fnmatch.fnmatch(path, forbidden_rule):
                violations.append({
                    "path": path,
                    "rule_type": "forbidden",
                    "matched_rule": forbidden_rule,
                })

        if allowed_paths and not _matches_any_allowed(path, allowed_paths):
            violations.append({
                "path": path,
                "rule_type": "outside-allowed",
                "matched_rule": ",".join(allowed_paths),
            })

    payload["violations"] = violations
    payload["status"] = "violation" if violations else "clean"
    return payload
