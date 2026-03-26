"""Predicted task-overlap helpers for shared-repo execution safety."""

from __future__ import annotations

from typing import Any

from .task_boundaries import normalize_path_rules


def _extract_repo_guidance_rules(task: dict[str, Any]) -> list[str]:
    """Collect normalized path scopes from repo guidance packs."""
    packs = task.get("repo_guidance_packs") or []
    if not isinstance(packs, list):
        return []

    rules: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        rules.extend(normalize_path_rules(pack.get("path_scope")))
    return rules


def task_scope_rules(task: dict[str, Any]) -> list[str]:
    """Return the normalized task scope rules used for overlap prediction."""
    rules = [
        *normalize_path_rules(task.get("allowed_paths")),
        *_extract_repo_guidance_rules(task),
    ]

    seen: set[str] = set()
    ordered: list[str] = []
    for rule in rules:
        if rule in seen:
            continue
        seen.add(rule)
        ordered.append(rule)
    return ordered


def _rule_root(rule: str) -> str:
    """Reduce a glob-like rule into a conservative path root."""
    normalized = (rule or "").strip().replace("\\", "/").lstrip("./")
    if not normalized:
        return ""

    segments = normalized.split("/")
    stable_segments: list[str] = []
    for segment in segments:
        if any(token in segment for token in ("*", "?", "[", "]", "{", "}")):
            break
        stable_segments.append(segment)
    return "/".join(stable_segments)


def _roots_overlap(left: str, right: str) -> bool:
    """Return whether two conservative roots may touch the same repo area."""
    if not left or not right:
        return True
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _shared_overlap_root(left: str, right: str) -> str:
    """Return the broader shared root for two overlapping scopes."""
    if not left and not right:
        return "."
    if not left:
        return right
    if not right:
        return left
    return left if len(left) <= len(right) else right


def predict_task_overlap(
    candidate_task: dict[str, Any],
    active_task: dict[str, Any],
) -> dict[str, Any] | None:
    """Predict whether two tasks may overlap in a shared checkout."""
    candidate_id = candidate_task.get("id")
    active_id = active_task.get("id")
    if candidate_id and active_id and candidate_id == active_id:
        return None

    candidate_project = candidate_task.get("project_id")
    active_project = active_task.get("project_id")
    if candidate_project and active_project and candidate_project != active_project:
        return None

    candidate_rules = task_scope_rules(candidate_task)
    active_rules = task_scope_rules(active_task)
    if not candidate_rules or not active_rules:
        return None

    candidate_roots = sorted({_rule_root(rule) for rule in candidate_rules})
    active_roots = sorted({_rule_root(rule) for rule in active_rules})
    overlapping_roots = sorted({
        _shared_overlap_root(candidate_root, active_root)
        for candidate_root in candidate_roots
        for active_root in active_roots
        if _roots_overlap(candidate_root, active_root)
    })
    if not overlapping_roots:
        return None

    active_label = active_task.get("title") or active_id or "active task"
    return {
        "status": "predicted-overlap",
        "candidate_task_id": candidate_id,
        "active_task_id": active_id,
        "candidate_scope_rules": candidate_rules,
        "active_scope_rules": active_rules,
        "overlapping_roots": overlapping_roots,
        "reason": f"Predicted repo overlap with {active_label}",
    }
