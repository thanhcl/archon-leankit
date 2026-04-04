"""
Runner capability matrix and task-to-runner routing policy.

This module keeps routing policy separate from TaskEngine so multi-runner
selection can evolve without turning the engine back into a god module.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ...config.logfire_config import get_logger
from .codex_runner import CODEX_RUNNER_KEY
from .runner_adapter import DEFAULT_RUNNER_KEY

logger = get_logger(__name__)

RoutingSource = Literal["explicit", "policy", "default"]
_TRUTHY_ENV_VALUES = {"1", "true", "yes"}

# Default path for runner token profiles config
# __file__ is at python/src/server/services/engine/runner_routing.py
# parents[4] resolves to the python/ root directory
_DEFAULT_PROFILES_PATH = Path(__file__).parents[4] / "config" / "runner_token_profiles.json"


def _load_token_profiles(profiles_path: str | Path | None = None) -> dict[str, Any]:
    """Load runner token profiles from the JSON config file.

    Resolves path via LEANKIT_RUNNER_TOKEN_PROFILES_PATH env var, then
    falls back to the provided path or the bundled default.
    """
    path = (
        os.environ.get("LEANKIT_RUNNER_TOKEN_PROFILES_PATH")
        or profiles_path
        or _DEFAULT_PROFILES_PATH
    )
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data.get("profiles", {})
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


# Profile selection policy: (task_type, priority) → profile_name
# More-specific (task_type, priority) pairs take precedence over (None, priority) wildcards.
_PROFILE_POLICY: list[tuple[str | None, str | None, str]] = [
    # task_type         priority        profile
    ("bug", "low", "simple_bugfix"),
    ("bug", "medium", "simple_bugfix"),
    ("bug", None, "standard_feature"),
    ("docs", None, "simple_bugfix"),
    ("refactor", "low", "simple_bugfix"),
    ("refactor", "medium", "standard_feature"),
    ("refactor", None, "standard_feature"),
    ("test", "critical", "complex_architecture"),
    ("test", "high", "complex_architecture"),
    ("test", None, "standard_feature"),
    ("feature", "critical", "complex_architecture"),
    ("feature", "high", "complex_architecture"),
    ("feature", "medium", "standard_feature"),
    ("feature", "low", "simple_bugfix"),
    ("improvement", "critical", "complex_architecture"),
    ("improvement", "high", "complex_architecture"),
    ("improvement", None, "standard_feature"),
    (None, "critical", "complex_architecture"),
    (None, "high", "complex_architecture"),
    (None, "low", "simple_bugfix"),
]


def select_token_profile(task: dict[str, Any]) -> str:
    """Select a runner token profile name for a task.

    Matches on task_type and priority using a priority-ordered policy table.
    Falls back to ``standard_feature`` when no rule matches.

    Returns:
        Profile name string (e.g. ``"simple_bugfix"``).
    """
    task_type = str(task.get("task_type") or "feature").lower()
    priority = str(task.get("priority") or "medium").lower()

    for rule_type, rule_priority, profile_name in _PROFILE_POLICY:
        type_match = rule_type is None or rule_type == task_type
        priority_match = rule_priority is None or rule_priority == priority
        if type_match and priority_match:
            return profile_name

    return "standard_feature"


@dataclass(frozen=True)
class RunnerCapability:
    """Describes what a runner is currently best suited for."""

    runner_key: str
    label: str
    source_app: str
    supports_execute: bool = True
    supports_review: bool = True
    strengths: list[str] = field(default_factory=list)
    preferred_task_types: list[str] = field(default_factory=list)
    preferred_tags: list[str] = field(default_factory=list)
    preferred_created_from: list[str] = field(default_factory=list)
    preferred_complexities: list[str] = field(default_factory=list)
    preferred_priorities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunnerSelection:
    """Result of routing a task to an execution runner."""

    runner_key: str
    source: RoutingSource
    reason: str


RUNNER_CAPABILITY_MATRIX: dict[str, RunnerCapability] = {
    DEFAULT_RUNNER_KEY: RunnerCapability(
        runner_key=DEFAULT_RUNNER_KEY,
        label="Claude Code CLI",
        source_app=DEFAULT_RUNNER_KEY,
        strengths=["complex-implementation", "security-review", "high-risk-tasks"],
        preferred_task_types=["bug", "feature", "improvement"],
        preferred_tags=["security", "backend", "api", "migration"],
        preferred_complexities=["complex"],
        preferred_priorities=["high", "critical"],
    ),
    CODEX_RUNNER_KEY: RunnerCapability(
        runner_key=CODEX_RUNNER_KEY,
        label="Codex CLI",
        source_app=CODEX_RUNNER_KEY,
        strengths=["bootstrap", "scaffolding", "refactor", "docs", "tests"],
        preferred_task_types=["docs", "refactor", "test"],
        preferred_tags=["bootstrap", "project-bootstrap", "scaffold", "template", "docs", "tests", "refactor"],
        preferred_created_from=["bootstrap", "project-bootstrap", "template"],
        preferred_complexities=["simple"],
        preferred_priorities=["low"],
    ),
}

_EXPLICIT_FIELDS = ("runner_key", "runner", "execution_runner")
_BOOTSTRAP_TAGS = {"bootstrap", "project-bootstrap", "scaffold", "template"}
_CODEX_TAGS = {"docs", "documentation", "tests", "test", "refactor", "scaffold", "template", "bootstrap"}
_CLAUDE_TAGS = {"security", "auth", "database", "backend", "api", "migration"}
_CLAUDE_BOOTSTRAP_TAGS = {
    "bootstrap-validation",
    "bootstrap-architecture",
    "quality-gate",
    "strict-review",
    "project-type:api-service",
}

# Languages where Codex performs well (typed, scaffolding-heavy ecosystems)
_CODEX_PREFERRED_LANGUAGES = {"java", "kotlin", "scala", "swift", "objective-c"}
# Languages where Claude Code performs well (complex logic, security-sensitive)
_CLAUDE_PREFERRED_LANGUAGES = {"python", "go", "rust", "c", "cpp", "c++"}

# Tags that indicate a high-risk task requiring approval before execution
_HIGH_RISK_TAGS = {"security", "database", "migration", "auth", "infra", "infrastructure", "secrets", "credentials"}

# Collaboration mode values for architect-planned task disposition
COLLABORATION_MODE_AUTO = "auto"        # low-risk: auto-approve after architect plan
COLLABORATION_MODE_REVIEW = "review"    # medium-risk: send to owner review
COLLABORATION_MODE_APPROVE = "approve"  # high-risk: pause for explicit owner approval

_VALID_COLLABORATION_MODES = {COLLABORATION_MODE_AUTO, COLLABORATION_MODE_REVIEW, COLLABORATION_MODE_APPROVE}

# Task types classified as low-risk by default (suitable for auto-approve)
_LOW_RISK_TASK_TYPES = {"docs", "test", "refactor"}
# Task types classified as high-risk by default
_HIGH_RISK_TASK_TYPES = {"feature", "improvement", "bug"}

# Tags that classify a task as medium-risk (require owner review)
_MEDIUM_RISK_TAGS = {"api", "backend", "frontend", "performance", "data"}


def classify_architect_risk(task: dict[str, Any]) -> str:
    """Classify the risk level of a task for architect collaboration mode resolution.

    Risk classification heuristic based on task_type, tags, priority, and complexity:
    - HIGH: security/infra/database tags, or critical priority with complex complexity
    - LOW: docs/test/refactor task types with low/medium priority and no risk tags
    - MEDIUM: everything else

    Returns:
        Risk level string: "low", "medium", or "high".
    """
    tags = set(_normalize_string_list(task.get("tags")))
    task_type = str(task.get("task_type") or "feature").lower()
    priority = str(task.get("priority") or "medium").lower()
    complexity = str(task.get("complexity") or "simple").lower()

    # High-risk: explicit risk tags OR (critical priority + complex)
    if tags & _HIGH_RISK_TAGS:
        return "high"
    if priority == "critical" and complexity == "complex":
        return "high"

    # Low-risk: documentation/test/refactor types with no risk tags and non-critical priority
    if task_type in _LOW_RISK_TASK_TYPES and priority not in {"critical", "high"}:
        return "low"

    # Medium-risk: tasks with API/backend tags or explicit medium indicators
    if tags & _MEDIUM_RISK_TAGS:
        return "medium"
    if priority == "high" or complexity == "complex":
        return "medium"

    # Default: low-risk for simple, low/medium priority tasks
    if priority in {"low", "medium"} and complexity == "simple":
        return "low"

    return "medium"


def resolve_collaboration_mode(
    task: dict[str, Any],
    model_routing: dict[str, Any] | None = None,
) -> str:
    """Resolve the effective architect collaboration mode for a task.

    Checks the project policy first; falls back to a heuristic classification
    based on task attributes.

    Policy keys in model_routing:
    - ``collaboration_mode``: explicit override ("auto", "review", "approve")
    - ``collaboration_mode_low_risk``: mode for low-risk tasks (default: "auto")
    - ``collaboration_mode_medium_risk``: mode for medium-risk tasks (default: "review")
    - ``collaboration_mode_high_risk``: mode for high-risk tasks (default: "approve")

    Returns:
        One of: "auto", "review", "approve".
    """
    # Explicit task-level override wins unconditionally
    task_mode = task.get("collaboration_mode")
    if isinstance(task_mode, str) and task_mode.strip() in _VALID_COLLABORATION_MODES:
        return task_mode.strip()

    # Explicit policy-level global override
    if model_routing:
        policy_mode = model_routing.get("collaboration_mode")
        if isinstance(policy_mode, str) and policy_mode.strip() in _VALID_COLLABORATION_MODES:
            return policy_mode.strip()

    # Risk-tier policy overrides (per-tier configuration)
    risk_level = classify_architect_risk(task)

    if model_routing:
        tier_key = f"collaboration_mode_{risk_level}_risk"
        tier_mode = model_routing.get(tier_key)
        if isinstance(tier_mode, str) and tier_mode.strip() in _VALID_COLLABORATION_MODES:
            return tier_mode.strip()

    # Default mapping: low→auto, medium→review, high→approve
    _DEFAULT_MODE_BY_RISK: dict[str, str] = {
        "low": COLLABORATION_MODE_AUTO,
        "medium": COLLABORATION_MODE_REVIEW,
        "high": COLLABORATION_MODE_APPROVE,
    }
    return _DEFAULT_MODE_BY_RISK.get(risk_level, COLLABORATION_MODE_REVIEW)


def get_runner_capabilities() -> list[dict[str, Any]]:
    """Return the runner capability matrix in a JSON-friendly shape."""
    enabled_runner_keys = _filter_disabled_runners(set(RUNNER_CAPABILITY_MATRIX.keys()))
    return [
        asdict(capability)
        for runner_key, capability in RUNNER_CAPABILITY_MATRIX.items()
        if runner_key in enabled_runner_keys
    ]


def get_runtime_default_runner_key() -> str:
    """Return the effective runtime default runner, respecting env kill switches."""
    disable_claude = _env_flag("LEANKIT_ENGINE_DISABLE_CLAUDE_CODE")
    disable_codex = _env_flag("LEANKIT_ENGINE_DISABLE_CODEX")

    if disable_claude and not disable_codex:
        return CODEX_RUNNER_KEY
    return DEFAULT_RUNNER_KEY


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY_ENV_VALUES


def _runner_disabled_by_policy(runner_key: str, model_routing: dict[str, Any] | None = None) -> bool:
    if runner_key == CODEX_RUNNER_KEY and model_routing and model_routing.get("disable_codex"):
        return True
    if runner_key == DEFAULT_RUNNER_KEY and model_routing and model_routing.get("disable_claude_code"):
        return True
    if runner_key == CODEX_RUNNER_KEY and _env_flag("LEANKIT_ENGINE_DISABLE_CODEX"):
        return True
    if runner_key == DEFAULT_RUNNER_KEY and _env_flag("LEANKIT_ENGINE_DISABLE_CLAUDE_CODE"):
        return True
    return False


def _filter_disabled_runners(
    available_runner_keys: set[str],
    model_routing: dict[str, Any] | None = None,
) -> set[str]:
    return {
        runner_key
        for runner_key in available_runner_keys
        if not _runner_disabled_by_policy(runner_key, model_routing)
    }


def get_fallback_runner_chain(
    primary_runner_key: str,
    available_runner_keys: set[str],
    model_routing: dict[str, Any] | None = None,
) -> list[str]:
    """Return an ordered list of fallback runner keys to try when the primary runner fails.

    When ``model_routing.fallback_policy.runner_fallback_chain`` is configured,
    that chain is used (filtered to available runners). When
    ``runner_fallback_enabled`` is explicitly False, an empty list is returned.
    Otherwise a default chain derived from ``RUNNER_CAPABILITY_MATRIX`` is used.

    Args:
        primary_runner_key: The runner that already failed.
        available_runner_keys: Runner keys currently registered with the engine.
        model_routing: Optional model_routing JSONB from archon_engine_policies.

    Returns:
        Ordered list of runner keys to try, excluding the primary runner.
    """
    effective_available = _filter_disabled_runners(set(available_runner_keys), model_routing)

    if model_routing:
        fallback_policy = model_routing.get("fallback_policy") or {}
        if isinstance(fallback_policy, dict):
            if fallback_policy.get("runner_fallback_enabled") is False:
                return []
            configured_chain = fallback_policy.get("runner_fallback_chain")
            if configured_chain and isinstance(configured_chain, list):
                return [
                    rk
                    for rk in configured_chain
                    if isinstance(rk, str) and rk in effective_available and rk != primary_runner_key
                ]

    # Default chain: CC → Codex, excluding the primary runner
    default_chain = [DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY]
    return [rk for rk in default_chain if rk in effective_available and rk != primary_runner_key]


def check_approval_required(
    task: dict[str, Any],
    model_routing: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Determine whether a task requires human approval before execution.

    Approval is required when:
    1. The project policy explicitly lists tags in ``require_approval_for`` that
       overlap with the task's tags.
    2. The project policy sets ``require_approval_high_risk: true`` and the task
       meets the built-in high-risk criteria (critical priority + high-risk tags,
       or critical+complex).
    3. No policy is set but the task has critical priority combined with high-risk
       tags — the global safety net.

    Returns:
        (required, reason) — reason explains *why* approval is or isn't required.
    """
    tags = set(_normalize_string_list(task.get("tags")))
    priority = str(task.get("priority") or "medium").lower()
    complexity = str(task.get("complexity") or "simple").lower()

    # Policy-driven: explicit tag list
    if model_routing:
        require_for_tags = _normalize_string_list(model_routing.get("require_approval_for") or [])
        if require_for_tags:
            matched = tags & set(require_for_tags)
            if matched:
                return True, f"policy:require_approval_for:{','.join(sorted(matched))}"

        # Policy-driven: high-risk gate enabled
        if model_routing.get("require_approval_high_risk"):
            if priority == "critical" and (tags & _HIGH_RISK_TAGS or complexity == "complex"):
                matched_tags = tags & _HIGH_RISK_TAGS
                detail = f"tags={','.join(sorted(matched_tags))}" if matched_tags else f"complexity={complexity}"
                return True, f"policy:require_approval_high_risk:{detail}"

    # Global safety net: critical priority + high-risk tags (no policy needed)
    if priority == "critical" and tags & _HIGH_RISK_TAGS:
        matched_tags = tags & _HIGH_RISK_TAGS
        return True, f"global:critical+high-risk-tags:{','.join(sorted(matched_tags))}"

    return False, "no-approval-required"


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                normalized.append(item.strip().lower())
        return normalized
    return []


def _first_supported(preferred: list[str], available_runner_keys: set[str]) -> str | None:
    for runner_key in preferred:
        if runner_key in available_runner_keys:
            return runner_key
    return None


def apply_policy_overrides(
    model_routing: dict[str, Any],
    stage: str,
    available_runner_keys: set[str],
    default_runner_key: str,
) -> RunnerSelection | None:
    """Return a RunnerSelection derived from a project-level model_routing policy, or None.

    Checks stage-specific overrides first, then falls back to the policy's
    default_runner. Returns None when the policy provides no actionable runner.

    Args:
        model_routing: The model_routing JSONB from archon_engine_policies.
        stage: Execution stage — "execute", "architect-review", "code-review".
        available_runner_keys: Runner keys registered with the current engine.
        default_runner_key: Engine-level default used as ultimate fallback.
    """
    if not model_routing:
        return None

    # Stage-specific runner override
    stage_overrides = model_routing.get("stage_overrides") or {}
    stage_entry = stage_overrides.get(stage) or {}
    if isinstance(stage_entry, dict):
        runner_key = stage_entry.get("runner")
        if isinstance(runner_key, str) and runner_key.strip():
            candidate = runner_key.strip()
            if candidate in available_runner_keys:
                return RunnerSelection(
                    runner_key=candidate,
                    source="policy",
                    reason=f"project-policy:stage-override:{stage}",
                )

    # Policy-level default runner
    policy_default = model_routing.get("default_runner")
    if isinstance(policy_default, str) and policy_default.strip():
        candidate = policy_default.strip()
        if candidate in available_runner_keys:
            return RunnerSelection(
                runner_key=candidate,
                source="policy",
                reason="project-policy:default-runner",
            )

    return None


def resolve_runner_selection(
    task: dict[str, Any],
    *,
    available_runner_keys: set[str],
    default_runner_key: str,
    model_routing: dict[str, Any] | None = None,
    stage: str = "execute",
    repo_language: str | None = None,
    repo_framework: str | None = None,
) -> RunnerSelection:
    """Resolve a runner using explicit overrides first, then policy.

    Routing priority (highest to lowest):
    1. Explicit task-level runner field
    2. Project-level model_routing policy (stage overrides + default_runner)
    3. Repo language/framework heuristics
    4. Task-type and tag heuristics
    5. Engine default

    Args:
        task: Task dict with fields like task_type, priority, complexity, tags.
        available_runner_keys: Runner keys registered with the current engine.
        default_runner_key: Fallback when no rule/policy applies.
        model_routing: Optional project-level model_routing JSONB. When provided
            and is_active=True, evaluated after explicit overrides but before
            the global heuristics.
        stage: Execution stage for stage-specific policy lookups.
        repo_language: Primary language of the project repository (e.g. "python",
            "java"). Overrides can also come from model_routing.repo_metadata.
        repo_framework: Primary framework used in the repo (e.g. "fastapi",
            "spring"). Informational; currently influences logging only.
    """
    task_id = task.get("id", "<unknown>")

    # 1. Explicit task-level override always wins
    effective_available = _filter_disabled_runners(set(available_runner_keys), model_routing)

    for explicit_field in _EXPLICIT_FIELDS:
        value = task.get(explicit_field)
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            if candidate in effective_available:
                selection = RunnerSelection(
                    runner_key=candidate,
                    source="explicit",
                    reason=f"explicit:{explicit_field}",
                )
                logger.info(
                    "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                    task_id, selection.runner_key, selection.source, selection.reason,
                )
                return selection
            logger.info(
                "Runner override ignored | task_id=%s | runner=%s | field=%s | reason=runner-disabled",
                task_id,
                candidate,
                explicit_field,
            )

    # 2. Project-level policy (from archon_engine_policies)
    if model_routing:
        policy_selection = apply_policy_overrides(model_routing, stage, effective_available, default_runner_key)
        if policy_selection is not None:
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, policy_selection.runner_key, policy_selection.source, policy_selection.reason,
            )
            return policy_selection

    task_type = str(task.get("task_type") or "feature").lower()
    priority = str(task.get("priority") or "medium").lower()
    complexity = str(task.get("complexity") or "simple").lower()
    created_from = str(task.get("created_from") or "").lower()
    tags = set(_normalize_string_list(task.get("tags")))

    available_for_heuristics = effective_available

    # Resolve effective repo language/framework (policy repo_metadata overrides arg)
    effective_language: str | None = None
    effective_framework: str | None = None
    if model_routing:
        repo_meta = model_routing.get("repo_metadata") or {}
        if isinstance(repo_meta, dict):
            if isinstance(repo_meta.get("language"), str):
                effective_language = repo_meta["language"].strip().lower() or None
            if isinstance(repo_meta.get("framework"), str):
                effective_framework = repo_meta["framework"].strip().lower() or None
    if effective_language is None and repo_language:
        effective_language = repo_language.strip().lower() or None
    if effective_framework is None and repo_framework:
        effective_framework = repo_framework.strip().lower() or None

    # 3. Repo language/framework routing
    if effective_language:
        if effective_language in _CODEX_PREFERRED_LANGUAGES:
            runner_key = _first_supported([CODEX_RUNNER_KEY, default_runner_key], available_for_heuristics)
            if runner_key:
                reason = f"policy:repo-language:{effective_language}"
                if effective_framework:
                    reason += f":{effective_framework}"
                selection = RunnerSelection(runner_key=runner_key, source="policy", reason=reason)
                logger.info(
                    "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s | language=%s | framework=%s",
                    task_id, selection.runner_key, selection.source, selection.reason,
                    effective_language, effective_framework,
                )
                return selection

        elif effective_language in _CLAUDE_PREFERRED_LANGUAGES:
            runner_key = _first_supported([DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY], available_for_heuristics)
            if runner_key:
                reason = f"policy:repo-language:{effective_language}"
                if effective_framework:
                    reason += f":{effective_framework}"
                selection = RunnerSelection(runner_key=runner_key, source="policy", reason=reason)
                logger.info(
                    "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s | language=%s | framework=%s",
                    task_id, selection.runner_key, selection.source, selection.reason,
                    effective_language, effective_framework,
                )
                return selection

    # 4-pre. Approval-aware routing: tasks with explicit approval
    # requirements (task-level or policy-level collaboration_mode="approve")
    # should prefer Claude Code for complex review flows
    task_collab = task.get("collaboration_mode")
    policy_collab = (model_routing or {}).get("collaboration_mode")
    if task_collab == COLLABORATION_MODE_APPROVE or policy_collab == COLLABORATION_MODE_APPROVE:
        runner_key = _first_supported([DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY], available_for_heuristics)
        if runner_key:
            selection = RunnerSelection(
                runner_key=runner_key, source="policy", reason="policy:approval-aware-claude"
            )
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # Coordinator children → use same runner as parent for consistency
    if task.get("parent_task_id") and task.get("decomposition_mode") != "coordinator":
        parent_runner = task.get("_parent_runner_key")
        if isinstance(parent_runner, str) and parent_runner in effective_available:
            selection = RunnerSelection(
                runner_key=parent_runner, source="policy", reason="policy:coordinator-child-affinity"
            )
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # 4a. Bootstrap quality-gate tasks → Claude Code
    if (created_from.startswith("project-bootstrap-") or "project-bootstrap" in tags) and (
        tags & _CLAUDE_BOOTSTRAP_TAGS
    ):
        runner_key = _first_supported([DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY], available_for_heuristics)
        if runner_key:
            selection = RunnerSelection(runner_key=runner_key, source="policy", reason="policy:claude-bootstrap-gate")
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # 4b. Bootstrap/scaffold tasks → Codex
    if created_from in {"bootstrap", "project-bootstrap", "template"} or tags & _BOOTSTRAP_TAGS:
        runner_key = _first_supported([CODEX_RUNNER_KEY, default_runner_key], available_for_heuristics)
        if runner_key:
            selection = RunnerSelection(runner_key=runner_key, source="policy", reason="policy:bootstrap-scaffold")
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # 4c. Docs/refactor/test workloads → Codex
    if task_type in {"docs", "refactor", "test"} or tags & _CODEX_TAGS:
        runner_key = _first_supported([CODEX_RUNNER_KEY, default_runner_key], available_for_heuristics)
        if runner_key:
            selection = RunnerSelection(
                runner_key=runner_key, source="policy", reason="policy:codex-preferred-workload"
            )
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # 4d. High-risk or complex tasks → Claude Code
    if priority == "critical" or complexity == "complex" or tags & _CLAUDE_TAGS:
        runner_key = _first_supported([DEFAULT_RUNNER_KEY, CODEX_RUNNER_KEY], available_for_heuristics)
        if runner_key:
            selection = RunnerSelection(runner_key=runner_key, source="policy", reason="policy:claude-high-risk")
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # 4e. Low-risk simple tasks → Codex
    if priority == "low" and complexity == "simple":
        runner_key = _first_supported([CODEX_RUNNER_KEY, default_runner_key], available_for_heuristics)
        if runner_key:
            selection = RunnerSelection(runner_key=runner_key, source="policy", reason="policy:codex-low-risk")
            logger.info(
                "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
                task_id, selection.runner_key, selection.source, selection.reason,
            )
            return selection

    # 5. Engine default
    fallback_runner_key = _first_supported([default_runner_key, CODEX_RUNNER_KEY, DEFAULT_RUNNER_KEY], available_for_heuristics)
    selection = RunnerSelection(
        runner_key=fallback_runner_key or default_runner_key,
        source="default",
        reason="default:engine-default-runner",
    )
    logger.info(
        "Runner selected | task_id=%s | runner=%s | source=%s | reason=%s",
        task_id, selection.runner_key, selection.source, selection.reason,
    )
    return selection
