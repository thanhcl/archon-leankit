"""Tests for auto-approval policy (B-P4-03)."""

import pytest

from src.server.services.engine.auto_approval import (
    FULL_AUTO_TASK_TYPES,
    evaluate_auto_approval,
)


def _make_task(**overrides):
    base = {
        "id": "t1",
        "project_id": "p1",
        "task_type": "feature",
        "retry_count": 0,
    }
    base.update(overrides)
    return base


class TestEvaluateAutoApproval:

    def test_tier_0_always_manual(self):
        ok, reason = evaluate_auto_approval("code-review", _make_task(), {}, tier=0)
        assert ok is False
        assert "tier=0" in reason

    def test_tier_1_code_review_clean_run(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {"result": "SUCCESS"}, tier=1
        )
        assert ok is True
        assert "tier 1" in reason

    def test_tier_1_code_review_retry_blocked(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(retry_count=1), {}, tier=1
        )
        assert ok is False
        assert "retry_count" in reason

    def test_tier_1_code_review_boundary_violation(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {}, tier=1,
            boundary_validation={"status": "violation"},
        )
        assert ok is False
        assert "boundary violation" in reason

    def test_tier_1_code_review_failure_result(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {"result": "FAILURE"}, tier=1
        )
        assert ok is False
        assert "FAILURE" in reason

    def test_tier_1_architect_review_blocked(self):
        ok, reason = evaluate_auto_approval(
            "architect-review", _make_task(), {}, tier=1
        )
        assert ok is False
        assert "tier < 2" in reason

    def test_tier_2_architect_review_passes(self):
        ok, reason = evaluate_auto_approval(
            "architect-review", _make_task(), {}, tier=2
        )
        assert ok is True

    def test_tier_2_owner_review_blocked(self):
        ok, reason = evaluate_auto_approval(
            "review", _make_task(), {}, tier=2
        )
        assert ok is False
        assert "tier < 3" in reason

    def test_tier_3_full_auto_docs(self):
        ok, reason = evaluate_auto_approval(
            "review", _make_task(task_type="docs"), {}, tier=3
        )
        assert ok is True
        assert "full auto" in reason

    def test_tier_3_full_auto_learning(self):
        ok, reason = evaluate_auto_approval(
            "review", _make_task(task_type="learning"), {}, tier=3
        )
        assert ok is True

    def test_tier_3_feature_not_eligible(self):
        ok, reason = evaluate_auto_approval(
            "review", _make_task(task_type="feature"), {}, tier=3
        )
        assert ok is False
        assert "not eligible" in reason

    def test_security_sensitive_files_blocked(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {},
            tier=3,
            boundary_validation={"changed_files": ["src/auth/login.py"]},
        )
        assert ok is False
        assert "security-sensitive" in reason

    def test_env_file_blocked(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {},
            tier=3,
            boundary_validation={"changed_files": [".env.production"]},
        )
        assert ok is False
        assert "security-sensitive" in reason

    def test_migration_file_blocked(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {},
            tier=3,
            boundary_validation={"changed_files": ["migration/001_add_users.sql"]},
        )
        assert ok is False

    def test_non_sensitive_files_pass(self):
        ok, reason = evaluate_auto_approval(
            "code-review", _make_task(), {},
            tier=1,
            boundary_validation={"changed_files": ["src/utils/helpers.py", "tests/test_helpers.py"]},
        )
        assert ok is True

    def test_unknown_stage(self):
        ok, reason = evaluate_auto_approval(
            "unknown-stage", _make_task(), {}, tier=3
        )
        assert ok is False
        assert "unknown stage" in reason
