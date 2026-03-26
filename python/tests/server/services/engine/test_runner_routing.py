"""Tests for runner capability matrix and routing policy."""

import json
import os
import tempfile

import pytest

from src.server.services.engine.runner_adapter import DEFAULT_RUNNER_KEY
from src.server.services.engine.runner_routing import (
    CODEX_RUNNER_KEY,
    COLLABORATION_MODE_APPROVE,
    COLLABORATION_MODE_AUTO,
    COLLABORATION_MODE_REVIEW,
    _load_token_profiles,
    apply_policy_overrides,
    check_approval_required,
    classify_architect_risk,
    get_runner_capabilities,
    resolve_collaboration_mode,
    resolve_runner_selection,
    select_token_profile,
)


def test_get_runner_capabilities_includes_claude_and_codex():
    capabilities = get_runner_capabilities()
    runner_keys = {item["runner_key"] for item in capabilities}
    assert DEFAULT_RUNNER_KEY in runner_keys
    assert CODEX_RUNNER_KEY in runner_keys


def test_explicit_runner_override_wins():
    selection = resolve_runner_selection(
        {"runner_key": CODEX_RUNNER_KEY, "priority": "critical", "complexity": "complex"},
        available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        default_runner_key=DEFAULT_RUNNER_KEY,
    )
    assert selection.runner_key == CODEX_RUNNER_KEY
    assert selection.source == "explicit"


def test_bootstrap_tasks_prefer_codex():
    selection = resolve_runner_selection(
        {"tags": ["project-bootstrap"], "task_type": "feature"},
        available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        default_runner_key=DEFAULT_RUNNER_KEY,
    )
    assert selection.runner_key == CODEX_RUNNER_KEY
    assert selection.reason == "policy:bootstrap-scaffold"


def test_docs_and_tests_prefer_codex():
    selection = resolve_runner_selection(
        {"task_type": "docs", "priority": "low", "complexity": "simple"},
        available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        default_runner_key=DEFAULT_RUNNER_KEY,
    )
    assert selection.runner_key == CODEX_RUNNER_KEY
    assert selection.source == "policy"


def test_critical_complex_tasks_prefer_claude():
    selection = resolve_runner_selection(
        {"priority": "critical", "complexity": "complex", "tags": ["security"]},
        available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        default_runner_key=DEFAULT_RUNNER_KEY,
    )
    assert selection.runner_key == DEFAULT_RUNNER_KEY
    assert selection.reason == "policy:claude-high-risk"


def test_strict_api_bootstrap_validation_prefers_claude():
    selection = resolve_runner_selection(
        {
            "created_from": "project-bootstrap-validation",
            "tags": ["project-bootstrap", "bootstrap-validation", "project-type:api-service", "bootstrap-policy:strict"],
            "task_type": "test",
        },
        available_runner_keys={DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY},
        default_runner_key=DEFAULT_RUNNER_KEY,
    )
    assert selection.runner_key == DEFAULT_RUNNER_KEY
    assert selection.reason == "policy:claude-bootstrap-gate"


def test_policy_falls_back_to_default_when_codex_missing():
    selection = resolve_runner_selection(
        {"task_type": "docs", "priority": "low", "complexity": "simple"},
        available_runner_keys={DEFAULT_RUNNER_KEY},
        default_runner_key=DEFAULT_RUNNER_KEY,
    )
    assert selection.runner_key == DEFAULT_RUNNER_KEY


# ---------------------------------------------------------------------------
# Token profile selection tests
# ---------------------------------------------------------------------------

class TestSelectTokenProfile:
    def test_bug_low_priority_returns_simple_bugfix(self):
        assert select_token_profile({"task_type": "bug", "priority": "low"}) == "simple_bugfix"

    def test_bug_medium_priority_returns_simple_bugfix(self):
        assert select_token_profile({"task_type": "bug", "priority": "medium"}) == "simple_bugfix"

    def test_bug_high_priority_returns_standard_feature(self):
        assert select_token_profile({"task_type": "bug", "priority": "high"}) == "standard_feature"

    def test_docs_returns_simple_bugfix(self):
        assert select_token_profile({"task_type": "docs", "priority": "medium"}) == "simple_bugfix"

    def test_feature_critical_returns_complex_architecture(self):
        assert select_token_profile({"task_type": "feature", "priority": "critical"}) == "complex_architecture"

    def test_feature_high_returns_complex_architecture(self):
        assert select_token_profile({"task_type": "feature", "priority": "high"}) == "complex_architecture"

    def test_feature_medium_returns_standard_feature(self):
        assert select_token_profile({"task_type": "feature", "priority": "medium"}) == "standard_feature"

    def test_feature_low_returns_simple_bugfix(self):
        assert select_token_profile({"task_type": "feature", "priority": "low"}) == "simple_bugfix"

    def test_improvement_critical_returns_complex_architecture(self):
        assert select_token_profile({"task_type": "improvement", "priority": "critical"}) == "complex_architecture"

    def test_critical_priority_any_type_returns_complex_architecture(self):
        assert select_token_profile({"task_type": "test", "priority": "critical"}) == "complex_architecture"

    def test_missing_fields_default_to_standard_feature(self):
        # No task_type or priority → defaults to feature/medium → standard_feature
        assert select_token_profile({}) == "standard_feature"

    def test_unknown_task_type_falls_back_to_priority(self):
        # Unknown task_type, high priority → complex_architecture via wildcard rule
        assert select_token_profile({"task_type": "unknown_type", "priority": "high"}) == "complex_architecture"

    def test_unknown_task_type_low_priority_falls_back_to_simple(self):
        assert select_token_profile({"task_type": "unknown_type", "priority": "low"}) == "simple_bugfix"

    def test_refactor_medium_returns_standard_feature(self):
        assert select_token_profile({"task_type": "refactor", "priority": "medium"}) == "standard_feature"

    def test_refactor_low_returns_simple_bugfix(self):
        assert select_token_profile({"task_type": "refactor", "priority": "low"}) == "simple_bugfix"


# ---------------------------------------------------------------------------
# Token profile loading tests
# ---------------------------------------------------------------------------

class TestLoadTokenProfiles:
    def test_loads_default_bundled_profiles(self):
        profiles = _load_token_profiles()
        assert "simple_bugfix" in profiles
        assert "standard_feature" in profiles
        assert "complex_architecture" in profiles
        assert "research_exploration" in profiles

    def test_profile_has_required_fields(self):
        profiles = _load_token_profiles()
        for name, profile in profiles.items():
            assert "max_tokens" in profile, f"{name} missing max_tokens"
            assert "temperature_pct" in profile, f"{name} missing temperature_pct"
            assert "model_hint" in profile, f"{name} missing model_hint"

    def test_simple_bugfix_uses_haiku(self):
        profiles = _load_token_profiles()
        assert profiles["simple_bugfix"]["model_hint"] == "haiku"
        assert profiles["simple_bugfix"]["max_tokens"] == 8192

    def test_complex_architecture_uses_sonnet(self):
        profiles = _load_token_profiles()
        assert profiles["complex_architecture"]["model_hint"] == "sonnet"
        assert profiles["complex_architecture"]["max_tokens"] == 20480

    def test_loads_custom_path_via_argument(self):
        custom_profiles = {
            "profiles": {
                "custom_profile": {"max_tokens": 999, "temperature_pct": 42, "model_hint": "haiku"}
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump(custom_profiles, fh)
            tmp_path = fh.name

        try:
            profiles = _load_token_profiles(profiles_path=tmp_path)
            assert "custom_profile" in profiles
            assert profiles["custom_profile"]["max_tokens"] == 999
        finally:
            os.unlink(tmp_path)

    def test_loads_custom_path_via_env_var(self, monkeypatch):
        custom_profiles = {
            "profiles": {
                "env_profile": {"max_tokens": 1234, "temperature_pct": 55, "model_hint": "sonnet"}
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump(custom_profiles, fh)
            tmp_path = fh.name

        try:
            monkeypatch.setenv("LEANKIT_RUNNER_TOKEN_PROFILES_PATH", tmp_path)
            profiles = _load_token_profiles()
            assert "env_profile" in profiles
            assert profiles["env_profile"]["max_tokens"] == 1234
        finally:
            os.unlink(tmp_path)
            monkeypatch.delenv("LEANKIT_RUNNER_TOKEN_PROFILES_PATH", raising=False)

    def test_missing_file_returns_empty_dict(self):
        profiles = _load_token_profiles(profiles_path="/nonexistent/path/profiles.json")
        assert profiles == {}

    def test_env_var_takes_precedence_over_argument(self, monkeypatch):
        custom_arg = {"profiles": {"arg_profile": {"max_tokens": 100, "temperature_pct": 10, "model_hint": "haiku"}}}
        custom_env = {"profiles": {"env_profile": {"max_tokens": 200, "temperature_pct": 20, "model_hint": "sonnet"}}}

        with (
            tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as arg_fh,
            tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as env_fh,
        ):
            json.dump(custom_arg, arg_fh)
            json.dump(custom_env, env_fh)
            arg_path = arg_fh.name
            env_path = env_fh.name

        try:
            monkeypatch.setenv("LEANKIT_RUNNER_TOKEN_PROFILES_PATH", env_path)
            profiles = _load_token_profiles(profiles_path=arg_path)
            assert "env_profile" in profiles
            assert "arg_profile" not in profiles
        finally:
            os.unlink(arg_path)
            os.unlink(env_path)
            monkeypatch.delenv("LEANKIT_RUNNER_TOKEN_PROFILES_PATH", raising=False)


# ---------------------------------------------------------------------------
# apply_policy_overrides tests (engine_policies model_routing column)
# ---------------------------------------------------------------------------


class TestApplyPolicyOverrides:
    _available = {DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY}

    def test_returns_none_for_empty_model_routing(self):
        assert apply_policy_overrides({}, "execute", self._available, DEFAULT_RUNNER_KEY) is None

    def test_returns_none_for_none_model_routing(self):
        assert apply_policy_overrides(None, "execute", self._available, DEFAULT_RUNNER_KEY) is None

    def test_default_runner_codex_returns_policy_selection(self):
        routing = {"default_runner": CODEX_RUNNER_KEY}
        result = apply_policy_overrides(routing, "execute", self._available, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY
        assert result.source == "policy"
        assert result.reason == "project-policy:default-runner"

    def test_default_runner_claude_returns_policy_selection(self):
        routing = {"default_runner": DEFAULT_RUNNER_KEY}
        result = apply_policy_overrides(routing, "execute", self._available, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == DEFAULT_RUNNER_KEY
        assert result.source == "policy"

    def test_stage_override_takes_precedence_over_default_runner(self):
        routing = {
            "default_runner": DEFAULT_RUNNER_KEY,
            "stage_overrides": {"execute": {"runner": CODEX_RUNNER_KEY}},
        }
        result = apply_policy_overrides(routing, "execute", self._available, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY
        assert result.reason == "project-policy:stage-override:execute"

    def test_stage_override_for_code_review_stage(self):
        routing = {
            "default_runner": CODEX_RUNNER_KEY,
            "stage_overrides": {"code-review": {"runner": DEFAULT_RUNNER_KEY}},
        }
        result = apply_policy_overrides(routing, "code-review", self._available, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == DEFAULT_RUNNER_KEY
        assert result.reason == "project-policy:stage-override:code-review"

    def test_stage_override_not_applied_for_different_stage(self):
        routing = {
            "default_runner": CODEX_RUNNER_KEY,
            "stage_overrides": {"architect-review": {"runner": DEFAULT_RUNNER_KEY}},
        }
        result = apply_policy_overrides(routing, "execute", self._available, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY
        assert result.reason == "project-policy:default-runner"

    def test_returns_none_when_runner_not_in_available_keys(self):
        routing = {"default_runner": "unknown-runner"}
        result = apply_policy_overrides(routing, "execute", self._available, DEFAULT_RUNNER_KEY)
        assert result is None

    def test_stage_override_runner_not_in_available_falls_back_to_default_runner(self):
        routing = {
            "default_runner": CODEX_RUNNER_KEY,
            "stage_overrides": {"execute": {"runner": "ghost-runner"}},
        }
        result = apply_policy_overrides(routing, "execute", self._available, DEFAULT_RUNNER_KEY)
        assert result is not None
        assert result.runner_key == CODEX_RUNNER_KEY
        assert result.reason == "project-policy:default-runner"


# ---------------------------------------------------------------------------
# resolve_runner_selection with model_routing policy tests
# ---------------------------------------------------------------------------


class TestResolveRunnerSelectionWithPolicy:
    _available = {DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY}

    def test_explicit_override_wins_over_policy(self):
        routing = {"default_runner": DEFAULT_RUNNER_KEY}
        selection = resolve_runner_selection(
            {"runner_key": CODEX_RUNNER_KEY},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.source == "explicit"

    def test_policy_default_runner_overrides_heuristics(self):
        routing = {"default_runner": CODEX_RUNNER_KEY}
        selection = resolve_runner_selection(
            {"priority": "critical", "complexity": "complex", "tags": ["security"]},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.source == "policy"
        assert selection.reason == "project-policy:default-runner"

    def test_policy_stage_override_routes_correctly(self):
        routing = {
            "default_runner": DEFAULT_RUNNER_KEY,
            "stage_overrides": {"execute": {"runner": CODEX_RUNNER_KEY}},
        }
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "critical"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
            stage="execute",
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.reason == "project-policy:stage-override:execute"

    def test_disable_codex_forces_claude_for_codex_workload(self):
        routing = {"disable_codex": True}
        selection = resolve_runner_selection(
            {"task_type": "docs", "priority": "low"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY

    def test_disable_codex_restricts_default_runner_in_policy(self):
        routing = {"default_runner": CODEX_RUNNER_KEY, "disable_codex": True}
        selection = resolve_runner_selection(
            {"task_type": "feature"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
        )
        assert selection.runner_key != CODEX_RUNNER_KEY

    def test_policy_none_falls_through_to_heuristics(self):
        selection = resolve_runner_selection(
            {"task_type": "docs", "priority": "low"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=None,
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.source == "policy"


# ---------------------------------------------------------------------------
# Repo language/framework routing tests
# ---------------------------------------------------------------------------


class TestRepoLanguageRouting:
    _available = {DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY}

    def test_java_repo_prefers_codex(self):
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="java",
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.source == "policy"
        assert "java" in selection.reason

    def test_kotlin_repo_prefers_codex(self):
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="kotlin",
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert "kotlin" in selection.reason

    def test_python_repo_prefers_claude(self):
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="python",
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert selection.source == "policy"
        assert "python" in selection.reason

    def test_go_repo_prefers_claude(self):
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="go",
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert "go" in selection.reason

    def test_neutral_language_falls_through_to_heuristics(self):
        # TypeScript is a neutral language — falls through to task-type heuristics
        selection = resolve_runner_selection(
            {"task_type": "docs", "priority": "low"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="typescript",
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert selection.reason == "policy:codex-preferred-workload"

    def test_reason_includes_framework_when_provided(self):
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="java",
            repo_framework="spring",
        )
        assert "java" in selection.reason
        assert "spring" in selection.reason

    def test_model_routing_repo_metadata_overrides_arg(self):
        # model_routing.repo_metadata.language takes precedence over repo_language arg
        routing = {"repo_metadata": {"language": "python"}}
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
            repo_language="java",  # should be overridden by policy
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert "python" in selection.reason

    def test_model_routing_repo_metadata_language_java(self):
        routing = {"repo_metadata": {"language": "java", "framework": "spring"}}
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
        )
        assert selection.runner_key == CODEX_RUNNER_KEY
        assert "java" in selection.reason

    def test_explicit_runner_wins_over_language_routing(self):
        selection = resolve_runner_selection(
            {"runner_key": DEFAULT_RUNNER_KEY, "task_type": "feature"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="java",
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert selection.source == "explicit"

    def test_policy_stage_override_wins_over_language_routing(self):
        routing = {"stage_overrides": {"execute": {"runner": DEFAULT_RUNNER_KEY}}}
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys=self._available,
            default_runner_key=DEFAULT_RUNNER_KEY,
            model_routing=routing,
            repo_language="java",  # language routing would pick Codex, but policy wins
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY
        assert selection.reason == "project-policy:stage-override:execute"

    def test_language_routing_falls_back_to_default_when_preferred_unavailable(self):
        # Java prefers Codex, but Codex unavailable → falls back to default_runner_key
        selection = resolve_runner_selection(
            {"task_type": "feature", "priority": "medium"},
            available_runner_keys={DEFAULT_RUNNER_KEY},
            default_runner_key=DEFAULT_RUNNER_KEY,
            repo_language="java",
        )
        assert selection.runner_key == DEFAULT_RUNNER_KEY


# ---------------------------------------------------------------------------
# check_approval_required tests
# ---------------------------------------------------------------------------


class TestCheckApprovalRequired:
    def test_no_approval_for_normal_task(self):
        required, reason = check_approval_required({"priority": "medium", "tags": ["feature"]})
        assert not required
        assert reason == "no-approval-required"

    def test_global_safety_net_critical_with_security_tag(self):
        required, reason = check_approval_required(
            {"priority": "critical", "tags": ["security", "backend"]}
        )
        assert required
        assert "critical" in reason or "security" in reason

    def test_global_safety_net_critical_with_migration_tag(self):
        required, reason = check_approval_required(
            {"priority": "critical", "tags": ["migration"]}
        )
        assert required
        assert "migration" in reason

    def test_no_approval_critical_without_high_risk_tags(self):
        # Critical priority alone (no high-risk tags) does NOT trigger global safety net
        required, _ = check_approval_required({"priority": "critical", "tags": ["feature"]})
        assert not required

    def test_policy_require_approval_for_matching_tag(self):
        routing = {"require_approval_for": ["database", "migration"]}
        required, reason = check_approval_required(
            {"priority": "low", "tags": ["database"]},
            model_routing=routing,
        )
        assert required
        assert "policy:require_approval_for" in reason
        assert "database" in reason

    def test_policy_require_approval_for_no_match(self):
        routing = {"require_approval_for": ["database"]}
        required, _ = check_approval_required(
            {"priority": "high", "tags": ["feature", "backend"]},
            model_routing=routing,
        )
        assert not required

    def test_policy_require_approval_high_risk_gate_critical_complex(self):
        routing = {"require_approval_high_risk": True}
        required, reason = check_approval_required(
            {"priority": "critical", "complexity": "complex", "tags": []},
            model_routing=routing,
        )
        assert required
        assert "require_approval_high_risk" in reason

    def test_policy_require_approval_high_risk_gate_critical_security_tag(self):
        routing = {"require_approval_high_risk": True}
        required, reason = check_approval_required(
            {"priority": "critical", "tags": ["security"]},
            model_routing=routing,
        )
        assert required
        assert "security" in reason

    def test_policy_require_approval_high_risk_gate_not_triggered_medium(self):
        routing = {"require_approval_high_risk": True}
        required, _ = check_approval_required(
            {"priority": "medium", "tags": ["security"]},
            model_routing=routing,
        )
        assert not required

    def test_no_model_routing_no_approval_for_high_risk_without_critical(self):
        # High-risk tags but not critical priority — no approval required without policy
        required, _ = check_approval_required(
            {"priority": "high", "tags": ["security", "database"]}
        )
        assert not required

    def test_tags_as_comma_string(self):
        # Tags provided as a comma-separated string should be handled correctly
        required, reason = check_approval_required(
            {"priority": "critical", "tags": "security,backend"}
        )
        assert required
        assert "security" in reason


# ---------------------------------------------------------------------------
# classify_architect_risk tests
# ---------------------------------------------------------------------------


class TestClassifyArchitectRisk:
    def test_security_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["security"], "priority": "low"}) == "high"

    def test_database_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["database"], "task_type": "feature"}) == "high"

    def test_migration_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["migration"]}) == "high"

    def test_auth_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["auth"], "priority": "medium"}) == "high"

    def test_infra_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["infra"]}) == "high"

    def test_infrastructure_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["infrastructure"]}) == "high"

    def test_critical_complex_feature_is_high_risk(self):
        assert classify_architect_risk(
            {"task_type": "feature", "priority": "critical", "complexity": "complex"}
        ) == "high"

    def test_docs_task_low_priority_is_low_risk(self):
        assert classify_architect_risk({"task_type": "docs", "priority": "low"}) == "low"

    def test_test_task_medium_priority_is_low_risk(self):
        assert classify_architect_risk({"task_type": "test", "priority": "medium"}) == "low"

    def test_refactor_task_low_priority_is_low_risk(self):
        assert classify_architect_risk({"task_type": "refactor", "priority": "low"}) == "low"

    def test_docs_task_high_priority_is_not_low_risk(self):
        # Docs with high priority is not considered low-risk
        assert classify_architect_risk({"task_type": "docs", "priority": "high"}) != "low"

    def test_feature_medium_priority_simple_is_low_risk(self):
        assert classify_architect_risk(
            {"task_type": "feature", "priority": "medium", "complexity": "simple"}
        ) == "low"

    def test_feature_high_priority_is_medium_risk(self):
        assert classify_architect_risk(
            {"task_type": "feature", "priority": "high", "complexity": "simple"}
        ) == "medium"

    def test_complex_task_without_critical_priority_is_medium_risk(self):
        assert classify_architect_risk(
            {"task_type": "feature", "priority": "high", "complexity": "complex"}
        ) == "medium"

    def test_api_tag_is_medium_risk(self):
        assert classify_architect_risk(
            {"tags": ["api"], "task_type": "feature", "priority": "medium", "complexity": "simple"}
        ) == "medium"

    def test_backend_tag_is_medium_risk(self):
        assert classify_architect_risk(
            {"tags": ["backend"], "task_type": "feature", "priority": "low"}
        ) == "medium"

    def test_no_fields_defaults_to_low_risk(self):
        # Default: no task_type/priority/tags → feature/medium/simple → low
        assert classify_architect_risk({}) == "low"

    def test_empty_tags_does_not_affect_classification(self):
        assert classify_architect_risk({"tags": [], "task_type": "docs", "priority": "medium"}) == "low"

    def test_secrets_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["secrets"]}) == "high"

    def test_credentials_tag_is_high_risk(self):
        assert classify_architect_risk({"tags": ["credentials"]}) == "high"


# ---------------------------------------------------------------------------
# resolve_collaboration_mode tests
# ---------------------------------------------------------------------------


class TestResolveCollaborationMode:
    def test_low_risk_defaults_to_auto(self):
        task = {"task_type": "docs", "priority": "low"}
        assert resolve_collaboration_mode(task) == COLLABORATION_MODE_AUTO

    def test_medium_risk_defaults_to_review(self):
        task = {"task_type": "feature", "priority": "high", "complexity": "simple"}
        assert resolve_collaboration_mode(task) == COLLABORATION_MODE_REVIEW

    def test_high_risk_defaults_to_approve(self):
        task = {"task_type": "feature", "tags": ["security"], "priority": "medium"}
        assert resolve_collaboration_mode(task) == COLLABORATION_MODE_APPROVE

    def test_explicit_task_mode_wins_over_policy(self):
        task = {"task_type": "docs", "priority": "low", "collaboration_mode": "approve"}
        routing = {"collaboration_mode": "auto"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_APPROVE

    def test_explicit_policy_mode_wins_over_risk_heuristic(self):
        task = {"task_type": "docs", "priority": "low"}
        routing = {"collaboration_mode": "approve"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_APPROVE

    def test_tier_override_low_risk(self):
        task = {"task_type": "docs", "priority": "low"}
        routing = {"collaboration_mode_low_risk": "review"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_REVIEW

    def test_tier_override_medium_risk(self):
        task = {"task_type": "feature", "priority": "high", "complexity": "simple"}
        routing = {"collaboration_mode_medium_risk": "approve"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_APPROVE

    def test_tier_override_high_risk(self):
        task = {"tags": ["security"], "priority": "medium"}
        routing = {"collaboration_mode_high_risk": "review"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_REVIEW

    def test_invalid_task_mode_falls_back_to_policy(self):
        task = {"task_type": "docs", "priority": "low", "collaboration_mode": "invalid"}
        routing = {"collaboration_mode": "approve"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_APPROVE

    def test_invalid_policy_mode_falls_back_to_heuristic(self):
        task = {"task_type": "docs", "priority": "low"}
        routing = {"collaboration_mode": "not-valid"}
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_AUTO

    def test_no_policy_high_risk_tags_returns_approve(self):
        task = {"tags": ["migration", "database"], "priority": "medium"}
        assert resolve_collaboration_mode(task, None) == COLLABORATION_MODE_APPROVE

    def test_global_mode_overrides_tier_modes(self):
        task = {"tags": ["security"], "priority": "critical", "complexity": "complex"}
        routing = {
            "collaboration_mode": "auto",
            "collaboration_mode_high_risk": "approve",
        }
        # Global collaboration_mode wins over tier-specific
        assert resolve_collaboration_mode(task, routing) == COLLABORATION_MODE_AUTO
