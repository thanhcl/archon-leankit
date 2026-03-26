"""Tests for engine policy integration with runner_routing.py."""

import pytest

from src.server.services.engine.runner_adapter import DEFAULT_RUNNER_KEY
from src.server.services.engine.runner_routing import (
    CODEX_RUNNER_KEY,
    apply_policy_overrides,
    resolve_runner_selection,
)

_ALL_RUNNERS = {DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY}


# ---------------------------------------------------------------------------
# apply_policy_overrides unit tests
# ---------------------------------------------------------------------------


class TestApplyPolicyOverrides:
    def test_returns_none_for_empty_policy(self):
        result = apply_policy_overrides({}, "execute", _ALL_RUNNERS, DEFAULT_RUNNER_KEY)
        assert result is None

    def test_stage_specific_runner_override(self):
        policy = {"stage_overrides": {"execute": {"runner": CODEX_RUNNER_KEY}}}
        result = apply_policy_overrides(policy, "execute", _ALL_RUNNERS, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY
        assert result.reason == "project-policy:stage-override:execute"

    def test_stage_specific_override_not_matched_for_different_stage(self):
        policy = {"stage_overrides": {"code-review": {"runner": CODEX_RUNNER_KEY}}}
        result = apply_policy_overrides(policy, "execute", _ALL_RUNNERS, DEFAULT_RUNNER_KEY)
        # No execute override — fall through to default_runner
        assert result is None

    def test_policy_default_runner_used_when_no_stage_match(self):
        policy = {"default_runner": CODEX_RUNNER_KEY}
        result = apply_policy_overrides(policy, "execute", _ALL_RUNNERS, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY
        assert result.reason == "project-policy:default-runner"

    def test_stage_override_takes_precedence_over_policy_default(self):
        policy = {
            "default_runner": DEFAULT_RUNNER_KEY,
            "stage_overrides": {"execute": {"runner": CODEX_RUNNER_KEY}},
        }
        result = apply_policy_overrides(policy, "execute", _ALL_RUNNERS, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY

    def test_unavailable_runner_is_skipped(self):
        policy = {"stage_overrides": {"execute": {"runner": CODEX_RUNNER_KEY}}}
        # Only claude available
        result = apply_policy_overrides(policy, "execute", {DEFAULT_RUNNER_KEY}, DEFAULT_RUNNER_KEY)
        # codex not available, no default_runner in policy → None
        assert result is None

    def test_policy_default_runner_unavailable_returns_none(self):
        policy = {"default_runner": CODEX_RUNNER_KEY}
        result = apply_policy_overrides(policy, "execute", {DEFAULT_RUNNER_KEY}, DEFAULT_RUNNER_KEY)
        assert result is None


# ---------------------------------------------------------------------------
# resolve_runner_selection with model_routing tests
# ---------------------------------------------------------------------------


class TestResolveRunnerSelectionWithPolicy:
    def test_explicit_task_runner_wins_over_policy(self):
        policy = {"default_runner": CODEX_RUNNER_KEY}
        task = {"runner_key": DEFAULT_RUNNER_KEY, "priority": "low", "complexity": "simple"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=policy,
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert selection.source == "explicit"

    def test_policy_default_runner_applied_before_heuristics(self):
        # Normally "docs" type would go to codex; policy overrides to claude
        policy = {"default_runner": DEFAULT_RUNNER_KEY}
        task = {"task_type": "docs", "priority": "low", "complexity": "simple"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=policy,
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert "project-policy" in selection.reason

    def test_policy_stage_override_for_code_review(self):
        policy = {"stage_overrides": {"code-review": {"runner": CODEX_RUNNER_KEY}}}
        task = {"task_type": "feature", "priority": "critical", "complexity": "complex"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=policy,
            stage="code-review",
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.reason == "project-policy:stage-override:code-review"

    def test_disable_codex_flag_prevents_codex_routing(self):
        policy = {"disable_codex": True}
        task = {"task_type": "docs", "priority": "low", "complexity": "simple"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=policy,
        )
        # Codex disabled → global heuristic falls back to claude
        assert selection.runner_key == DEFAULT_RUNNER_KEY

    def test_no_policy_falls_back_to_global_heuristics(self):
        task = {"task_type": "docs", "priority": "low", "complexity": "simple"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=None,
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.source == "policy"

    def test_empty_policy_falls_back_to_global_heuristics(self):
        task = {"task_type": "feature", "priority": "critical", "complexity": "complex"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing={},
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert selection.reason == "policy:claude-high-risk"

    def test_default_stage_is_execute(self):
        policy = {"stage_overrides": {"execute": {"runner": CODEX_RUNNER_KEY}}}
        task = {"task_type": "feature", "priority": "high"}
        # Default stage="execute" should match
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=policy,
        )
        assert selection.runner_key == CODEX_RUNNER_KEY

    def test_policy_stage_not_matching_falls_through_to_heuristics(self):
        # Stage override only for architect-review; task goes to execute → heuristics apply
        policy = {"stage_overrides": {"architect-review": {"runner": CODEX_RUNNER_KEY}}}
        task = {"task_type": "feature", "priority": "critical", "complexity": "complex"}
        selection = resolve_runner_selection(
            task,
            available_runner_keys=_ALL_RUNNERS,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=policy,
            stage="execute",
        )
        # Heuristic: critical/complex → claude
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert selection.reason == "policy:claude-high-risk"
