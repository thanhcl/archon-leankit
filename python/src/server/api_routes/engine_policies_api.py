"""
Engine Policies API — per-project model routing configuration.

GET    /api/engine-policies/{project_id}  — retrieve active policy (or seed defaults)
PUT    /api/engine-policies/{project_id}  — create or replace policy
DELETE /api/engine-policies/{project_id}  — remove policy (reverts to global defaults)
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.projects.engine_policy_service import EnginePolicyService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/engine-policies", tags=["engine-policies"])


class UpsertEnginePolicyRequest(BaseModel):
    is_active: bool = True
    model_routing: dict[str, Any] = {}
    retry_policy: dict[str, Any] = {}
    budget_policy: dict[str, Any] = {}
    isolation_policy: dict[str, Any] = {}
    review_policy: dict[str, Any] = {}
    capacity_policy: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# GET — read current policy (or seed defaults)
# ---------------------------------------------------------------------------


@router.get("/{project_id}")
async def get_engine_policy(project_id: str):
    """Return the engine routing policy for a project.

    Returns seed defaults when no policy has been explicitly configured,
    so callers always receive a fully-populated policy object.
    """
    try:
        service = EnginePolicyService()
        ok, result = service.get_policy(project_id)
        if not ok:
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to fetch policy"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GET engine policy failed | project_id={project_id} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# PUT — create or replace policy
# ---------------------------------------------------------------------------


@router.put("/{project_id}")
async def upsert_engine_policy(project_id: str, request: UpsertEnginePolicyRequest):
    """Create or replace the engine policy for a project.

    **model_routing** keys:

    | Key                      | Type   | Description                                                         |
    |--------------------------|--------|---------------------------------------------------------------------|
    | ``default_runner``       | string | runner_key used when no stage rule matches                          |
    | ``stage_overrides``      | object | per-stage runner/model, e.g. ``{"execute": {"runner": "codex-cli"}}`` |
    | ``force_model``          | string | override every runner.select_model() call                           |
    | ``token_profile_overrides`` | object | per-stage token profile, e.g. ``{"execute": "complex_architecture"}`` |
    | ``provider_preference``  | string | informational preferred provider hint                               |
    | ``disable_codex``        | bool   | never route to codex-cli                                            |
    | ``notes``                | string | human-readable description                                          |
    | ``fallback_policy``      | object | multi-level fallback matrix (see below)                             |
    | ``collaboration_mode``   | string | architect collaboration mode override: "auto", "review", "approve"  |
    | ``collaboration_mode_low_risk`` | string | mode for low-risk tasks (default: "auto")                  |
    | ``collaboration_mode_medium_risk`` | string | mode for medium-risk tasks (default: "review")           |
    | ``collaboration_mode_high_risk`` | string | mode for high-risk tasks (default: "approve")            |

    **model_routing.fallback_policy** keys:

    | Key                          | Type         | Description                                                         |
    |------------------------------|--------------|---------------------------------------------------------------------|
    | ``runner_fallback_enabled``  | bool         | enable runner-level fallback when binary crashes (default: true)    |
    | ``runner_fallback_chain``    | list[string] | ordered runner keys to try after primary runner fails               |
    | ``model_fallback_chain``     | list[string] | ordered model names to try after selected model fails               |

    **retry_policy** keys: ``max_attempts``, ``backoff_seconds``, ``retry_on_timeout``, ``retry_on_exit``.

    **budget_policy** keys: ``daily_limit_usd``, ``sprint_limit_usd``, ``task_limit_usd``, ``alert_threshold``.

    **isolation_policy** keys: ``worktree_mode``, ``sandbox_network``, ``clean_env``, ``allowed_tools``.

    **review_policy** keys: ``review_mode``.

    **capacity_policy** keys: ``pool_id`` (string), ``pool_slots`` (int ≥ 1).
    Assigns this project to a named shared agent pool and sets the maximum number
    of pool slots this project may hold concurrently. Leave empty to use no pool.

    Set ``is_active=false`` to keep the policy stored but temporarily disable it.
    """
    try:
        _validate_model_routing(request.model_routing)
        _validate_retry_policy(request.retry_policy)
        _validate_budget_policy(request.budget_policy)
        _validate_isolation_policy(request.isolation_policy)
        _validate_review_policy(request.review_policy)
        _validate_capacity_policy(request.capacity_policy)

        service = EnginePolicyService()
        ok, result = service.upsert_policy(
            project_id=project_id,
            model_routing=request.model_routing,
            retry_policy=request.retry_policy,
            budget_policy=request.budget_policy,
            isolation_policy=request.isolation_policy,
            review_policy=request.review_policy,
            capacity_policy=request.capacity_policy,
            is_active=request.is_active,
        )
        if not ok:
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to save policy"))

        logger.info(f"Engine policy upserted | project_id={project_id} | is_active={request.is_active}")
        return {"message": "Engine policy saved", "policy": result.get("policy")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PUT engine policy failed | project_id={project_id} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# DELETE — remove policy
# ---------------------------------------------------------------------------


@router.delete("/{project_id}")
async def delete_engine_policy(project_id: str):
    """Remove the engine routing policy for a project.

    After deletion the project reverts to global runner/model heuristics.
    """
    try:
        service = EnginePolicyService()
        ok, result = service.delete_policy(project_id)
        if not ok:
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to delete policy"))

        logger.info(f"Engine policy deleted | project_id={project_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DELETE engine policy failed | project_id={project_id} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": str(e)}) from e


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------

_VALID_RUNNERS = {"claude-code-cli", "codex-cli"}
_VALID_STAGES = {"execute", "architect-review", "code-review"}
_VALID_PROFILE_NAMES = {"simple_bugfix", "standard_feature", "complex_architecture", "research_exploration"}
_VALID_WORKTREE_MODES = {"shared", "isolated", "per-task"}
_VALID_REVIEW_MODES = {"self-review", "api", "multi-perspective"}
_VALID_COLLABORATION_MODES = {"auto", "review", "approve"}


def _validate_model_routing(model_routing: dict[str, Any]) -> None:
    """Raise HTTPException(400) for obviously invalid model_routing values."""
    if not isinstance(model_routing, dict):
        raise HTTPException(status_code=400, detail="model_routing must be an object")

    fallback_policy = model_routing.get("fallback_policy")
    if fallback_policy is not None:
        _validate_fallback_policy(fallback_policy)

    # Validate collaboration_mode fields
    collaboration_mode = model_routing.get("collaboration_mode")
    if collaboration_mode is not None and collaboration_mode not in _VALID_COLLABORATION_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"collaboration_mode must be one of {sorted(_VALID_COLLABORATION_MODES)}, got '{collaboration_mode}'",
        )

    for tier in ("low", "medium", "high"):
        tier_mode = model_routing.get(f"collaboration_mode_{tier}_risk")
        if tier_mode is not None and tier_mode not in _VALID_COLLABORATION_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"collaboration_mode_{tier}_risk must be one of {sorted(_VALID_COLLABORATION_MODES)}, got '{tier_mode}'",
            )

    default_runner = model_routing.get("default_runner")
    if default_runner is not None and default_runner not in _VALID_RUNNERS:
        raise HTTPException(
            status_code=400,
            detail=f"default_runner must be one of {sorted(_VALID_RUNNERS)}, got '{default_runner}'",
        )

    stage_overrides = model_routing.get("stage_overrides")
    if stage_overrides is not None:
        if not isinstance(stage_overrides, dict):
            raise HTTPException(status_code=400, detail="stage_overrides must be an object")
        for stage, entry in stage_overrides.items():
            if stage not in _VALID_STAGES:
                raise HTTPException(
                    status_code=400,
                    detail=f"stage_overrides key must be one of {sorted(_VALID_STAGES)}, got '{stage}'",
                )
            if not isinstance(entry, dict):
                raise HTTPException(status_code=400, detail=f"stage_overrides['{stage}'] must be an object")
            runner = entry.get("runner")
            if runner is not None and runner not in _VALID_RUNNERS:
                raise HTTPException(
                    status_code=400,
                    detail=f"stage_overrides['{stage}'].runner must be one of {sorted(_VALID_RUNNERS)}, got '{runner}'",
                )

    tpo = model_routing.get("token_profile_overrides")
    if tpo is not None:
        if not isinstance(tpo, dict):
            raise HTTPException(status_code=400, detail="token_profile_overrides must be an object")
        valid_keys = _VALID_STAGES | {"*"}
        for key, profile in tpo.items():
            if key not in valid_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"token_profile_overrides key must be a stage or '*', got '{key}'",
                )
            if profile not in _VALID_PROFILE_NAMES:
                raise HTTPException(
                    status_code=400,
                    detail=f"token_profile_overrides['{key}'] must be one of {sorted(_VALID_PROFILE_NAMES)}, got '{profile}'",
                )


def _validate_fallback_policy(fallback_policy: dict[str, Any]) -> None:
    """Raise HTTPException(400) for invalid model_routing.fallback_policy values."""
    if not isinstance(fallback_policy, dict):
        raise HTTPException(status_code=400, detail="model_routing.fallback_policy must be an object")

    runner_fallback_enabled = fallback_policy.get("runner_fallback_enabled")
    if runner_fallback_enabled is not None and not isinstance(runner_fallback_enabled, bool):
        raise HTTPException(status_code=400, detail="model_routing.fallback_policy.runner_fallback_enabled must be a boolean")

    runner_fallback_chain = fallback_policy.get("runner_fallback_chain")
    if runner_fallback_chain is not None:
        if not isinstance(runner_fallback_chain, list):
            raise HTTPException(status_code=400, detail="model_routing.fallback_policy.runner_fallback_chain must be a list")
        for i, rk in enumerate(runner_fallback_chain):
            if not isinstance(rk, str) or rk not in _VALID_RUNNERS:
                raise HTTPException(
                    status_code=400,
                    detail=f"model_routing.fallback_policy.runner_fallback_chain[{i}] must be one of {sorted(_VALID_RUNNERS)}, got '{rk}'",
                )

    model_fallback_chain = fallback_policy.get("model_fallback_chain")
    if model_fallback_chain is not None:
        if not isinstance(model_fallback_chain, list):
            raise HTTPException(status_code=400, detail="model_routing.fallback_policy.model_fallback_chain must be a list")
        for i, model in enumerate(model_fallback_chain):
            if not isinstance(model, str) or not model.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"model_routing.fallback_policy.model_fallback_chain[{i}] must be a non-empty string",
                )


def _validate_retry_policy(retry_policy: dict[str, Any]) -> None:
    """Raise HTTPException(400) for invalid retry_policy values."""
    if not isinstance(retry_policy, dict):
        raise HTTPException(status_code=400, detail="retry_policy must be an object")

    max_attempts = retry_policy.get("max_attempts")
    if max_attempts is not None:
        if not isinstance(max_attempts, int) or max_attempts < 0:
            raise HTTPException(status_code=400, detail="retry_policy.max_attempts must be a non-negative integer")

    backoff_seconds = retry_policy.get("backoff_seconds")
    if backoff_seconds is not None:
        if not isinstance(backoff_seconds, int | float) or backoff_seconds < 0:
            raise HTTPException(status_code=400, detail="retry_policy.backoff_seconds must be a non-negative number")

    for bool_key in ("retry_on_timeout", "retry_on_exit"):
        val = retry_policy.get(bool_key)
        if val is not None and not isinstance(val, bool):
            raise HTTPException(status_code=400, detail=f"retry_policy.{bool_key} must be a boolean")


def _validate_budget_policy(budget_policy: dict[str, Any]) -> None:
    """Raise HTTPException(400) for invalid budget_policy values."""
    if not isinstance(budget_policy, dict):
        raise HTTPException(status_code=400, detail="budget_policy must be an object")

    for limit_key in ("daily_limit_usd", "sprint_limit_usd", "task_limit_usd"):
        val = budget_policy.get(limit_key)
        if val is not None and (not isinstance(val, int | float) or val < 0):
            raise HTTPException(status_code=400, detail=f"budget_policy.{limit_key} must be a non-negative number")

    alert_threshold = budget_policy.get("alert_threshold")
    if alert_threshold is not None:
        if not isinstance(alert_threshold, int | float) or not (0.0 <= alert_threshold <= 1.0):
            raise HTTPException(status_code=400, detail="budget_policy.alert_threshold must be between 0.0 and 1.0")


def _validate_isolation_policy(isolation_policy: dict[str, Any]) -> None:
    """Raise HTTPException(400) for invalid isolation_policy values."""
    if not isinstance(isolation_policy, dict):
        raise HTTPException(status_code=400, detail="isolation_policy must be an object")

    worktree_mode = isolation_policy.get("worktree_mode")
    if worktree_mode is not None and worktree_mode not in _VALID_WORKTREE_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"isolation_policy.worktree_mode must be one of {sorted(_VALID_WORKTREE_MODES)}, got '{worktree_mode}'",
        )

    for bool_key in ("sandbox_network", "clean_env"):
        val = isolation_policy.get(bool_key)
        if val is not None and not isinstance(val, bool):
            raise HTTPException(status_code=400, detail=f"isolation_policy.{bool_key} must be a boolean")

    allowed_tools = isolation_policy.get("allowed_tools")
    if allowed_tools is not None and not isinstance(allowed_tools, list):
        raise HTTPException(status_code=400, detail="isolation_policy.allowed_tools must be a list")


def _validate_review_policy(review_policy: dict[str, Any]) -> None:
    """Raise HTTPException(400) for invalid review_policy values."""
    if not isinstance(review_policy, dict):
        raise HTTPException(status_code=400, detail="review_policy must be an object")

    review_mode = review_policy.get("review_mode")
    if review_mode is not None and review_mode not in _VALID_REVIEW_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"review_policy.review_mode must be one of {sorted(_VALID_REVIEW_MODES)}, got '{review_mode}'",
        )


def _validate_capacity_policy(capacity_policy: dict[str, Any]) -> None:
    """Raise HTTPException(400) for invalid capacity_policy values."""
    if not isinstance(capacity_policy, dict):
        raise HTTPException(status_code=400, detail="capacity_policy must be an object")

    pool_id = capacity_policy.get("pool_id")
    if pool_id is not None:
        if not isinstance(pool_id, str) or not pool_id.strip():
            raise HTTPException(status_code=400, detail="capacity_policy.pool_id must be a non-empty string")

    pool_slots = capacity_policy.get("pool_slots")
    if pool_slots is not None:
        if not isinstance(pool_slots, int) or pool_slots < 1:
            raise HTTPException(status_code=400, detail="capacity_policy.pool_slots must be an integer >= 1")
