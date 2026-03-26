"""
Engine Policy Service for Archon.

Stores and retrieves per-project engine routing policies that can override
the global runner/model heuristics in runner_routing.py.
"""

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

_TABLE = "archon_engine_policies"
_PROJECTS_TABLE = "archon_projects"
_SETTINGS_TABLE = "archon_settings"
_REVIEW_CONFIG_KEY = "REVIEW_CONFIG"

_POLICY_COLUMNS = (
    "model_routing",
    "retry_policy",
    "budget_policy",
    "isolation_policy",
    "review_policy",
    "capacity_policy",
)

_VALID_RUNNERS = {"claude-code-cli", "codex-cli"}
_VALID_REVIEW_MODES = {"self-review", "api", "multi-perspective"}

_LEGACY_RUNNER_KEYS = ("preferred_runner", "runner_preference", "default_runner", "runner_key", "execution_runner")
_LEGACY_REVIEW_MODE_KEYS = ("review_mode",)
_LEGACY_ISOLATION_KEYS = ("isolation_mode", "worktree_mode", "isolation")
_LEGACY_RUNNER_SECTIONS = ("office_settings", "team_lead_config", "director_config")
_LEGACY_REVIEW_SECTIONS = ("team_lead_config", "director_config", "office_settings")
_LEGACY_ISOLATION_SECTIONS = ("office_settings", "team_lead_config", "director_config")
_CANONICAL_POLICY_KEYS = (
    ("model_routing", "default_runner"),
    ("review_policy", "review_mode"),
    ("isolation_policy", "worktree_mode"),
)

# Default values returned when no policy row exists for a project.
_DEFAULT_POLICY: dict[str, Any] = {
    "model_routing": {},
    "retry_policy": {
        "max_attempts": 3,
        "backoff_seconds": 30,
        "retry_on_timeout": True,
        "retry_on_exit": False,
    },
    "budget_policy": {
        "daily_limit_usd": None,
        "sprint_limit_usd": None,
        "task_limit_usd": None,
        "alert_threshold": 0.8,
    },
    "isolation_policy": {
        "worktree_mode": "shared",
        "sandbox_network": False,
        "clean_env": False,
        "allowed_tools": [],
    },
    "review_policy": {},
    "capacity_policy": {},
}


class EnginePolicyService:
    """Service class for engine policy CRUD operations."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_policy(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Return the engine policy for a project, or seed defaults if absent."""
        try:
            stored_policy = self._fetch_policy_row(project_id, active_only=False)
            fallback_policy, fallback_sources = self._get_legacy_policy_with_sources(project_id)
            _log_applied_legacy_fallbacks(
                project_id=project_id,
                stored_policy=stored_policy,
                fallback_policy=fallback_policy,
                fallback_sources=fallback_sources,
            )

            if stored_policy or fallback_policy:
                return True, {
                    "policy": _build_effective_policy(
                        project_id,
                        stored_policy=stored_policy,
                        fallback_policy=fallback_policy,
                        include_defaults=True,
                    )
                }

            return True, {"policy": _build_default_policy(project_id)}
        except Exception as e:
            logger.error(f"Failed to get engine policy | project_id={project_id} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def get_active_policy(self, project_id: str) -> dict[str, Any] | None:
        """Return the active engine policy row dict, or None.

        Convenience method for internal engine use — returns None on any error
        so callers can fall back to global defaults without crashing.
        """
        try:
            stored_policy = self._fetch_policy_row(project_id, active_only=True)
            fallback_policy, fallback_sources = self._get_legacy_policy_with_sources(project_id)
            _log_applied_legacy_fallbacks(
                project_id=project_id,
                stored_policy=stored_policy,
                fallback_policy=fallback_policy,
                fallback_sources=fallback_sources,
            )

            if stored_policy:
                return _build_effective_policy(
                    project_id,
                    stored_policy=stored_policy,
                    fallback_policy=fallback_policy,
                    include_defaults=False,
                )

            if fallback_policy:
                return _build_effective_policy(
                    project_id,
                    fallback_policy=fallback_policy,
                    include_defaults=False,
                )

            return None
        except Exception as e:
            logger.warning(f"Could not load engine policy, using defaults | project_id={project_id} | error={e}")
            return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_policy(
        self,
        project_id: str,
        model_routing: dict[str, Any],
        retry_policy: dict[str, Any] | None = None,
        budget_policy: dict[str, Any] | None = None,
        isolation_policy: dict[str, Any] | None = None,
        review_policy: dict[str, Any] | None = None,
        capacity_policy: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> tuple[bool, dict[str, Any]]:
        """Create or replace the engine policy for a project."""
        if not project_id:
            return False, {"error": "project_id is required"}

        now = datetime.utcnow().isoformat()
        payload: dict[str, Any] = {
            "project_id": project_id,
            "is_active": is_active,
            "model_routing": model_routing,
            "retry_policy": retry_policy if retry_policy is not None else {},
            "budget_policy": budget_policy if budget_policy is not None else {},
            "isolation_policy": isolation_policy if isolation_policy is not None else {},
            "review_policy": review_policy if review_policy is not None else {},
            "capacity_policy": capacity_policy if capacity_policy is not None else {},
            "updated_at": now,
        }

        try:
            result = (
                self.supabase_client.table(_TABLE)
                .upsert(payload, on_conflict="project_id")
                .execute()
            )
            if result.data:
                return True, {"policy": result.data[0]}
            return False, {"error": "Upsert returned no data"}
        except Exception as e:
            logger.error(f"Failed to upsert engine policy | project_id={project_id} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def sync_project_policy_sources(self, project_row: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
        """Mirror deprecated project metadata policy fields into archon_engine_policies.

        This keeps the canonical project-level runner, review, and isolation
        values in ``archon_engine_policies`` while preserving legacy metadata as
        a read fallback during migration. Existing canonical policy values always
        win over mirrored legacy metadata.
        """
        if not isinstance(project_row, dict):
            return False, {"error": "project_row must be a dict"}

        project_id = project_row.get("id")
        if not isinstance(project_id, str) or not project_id.strip():
            return False, {"error": "project_row.id is required"}

        legacy_policy = extract_project_policy_sources(project_row)
        if not legacy_policy:
            return True, {"policy": None, "mirrored": False}

        try:
            stored_policy = self._fetch_policy_row(project_id, active_only=False) or {}
            merged_columns = _merge_policy_columns_with_precedence(
                preferred=legacy_policy,
                canonical=stored_policy,
            )

            if stored_policy and all(
                merged_columns[column] == stored_policy.get(column, {})
                for column in _POLICY_COLUMNS
            ):
                return True, {"policy": stored_policy, "mirrored": False}

            ok, result = self.upsert_policy(
                project_id=project_id,
                model_routing=merged_columns["model_routing"],
                retry_policy=merged_columns["retry_policy"],
                budget_policy=merged_columns["budget_policy"],
                isolation_policy=merged_columns["isolation_policy"],
                review_policy=merged_columns["review_policy"],
                is_active=bool(stored_policy.get("is_active", True)),
            )
            if not ok:
                return ok, result
            return True, {"policy": result.get("policy"), "mirrored": True}
        except Exception as e:
            logger.error(
                f"Failed to mirror deprecated project policy fields | project_id={project_id} | error={e}",
                exc_info=True,
            )
            return False, {"error": str(e)}

    def delete_policy(self, project_id: str) -> tuple[bool, dict[str, Any]]:
        """Remove the engine policy for a project (reverts to global defaults)."""
        if not project_id:
            return False, {"error": "project_id is required"}

        try:
            self.supabase_client.table(_TABLE).delete().eq("project_id", project_id).execute()
            return True, {"message": f"Engine policy deleted for project {project_id}"}
        except Exception as e:
            logger.error(f"Failed to delete engine policy | project_id={project_id} | error={e}", exc_info=True)
            return False, {"error": str(e)}

    def _fetch_policy_row(self, project_id: str, *, active_only: bool) -> dict[str, Any] | None:
        query = self.supabase_client.table(_TABLE).select("*").eq("project_id", project_id)
        if active_only:
            query = query.eq("is_active", True)
        result = query.maybe_single().execute()
        return result.data if result is not None else None

    def _get_legacy_policy(self, project_id: str) -> dict[str, Any]:
        policy, _ = self._get_legacy_policy_with_sources(project_id)
        return policy

    def _get_legacy_policy_with_sources(self, project_id: str) -> tuple[dict[str, Any], dict[str, str]]:
        try:
            project_row = self._fetch_project_row(project_id)
            review_config = self._fetch_review_config()
            return _build_legacy_policy_with_sources(project_row, review_config)
        except Exception as e:
            logger.warning(
                f"Could not resolve legacy engine policy fallback | project_id={project_id} | error={e}",
                exc_info=True,
            )
            return {}, {}

    def _fetch_project_row(self, project_id: str) -> dict[str, Any] | None:
        result = (
            self.supabase_client.table(_PROJECTS_TABLE)
            .select("id, director_config, team_lead_config, office_settings")
            .eq("id", project_id)
            .maybe_single()
            .execute()
        )
        return result.data if result is not None else None

    def _fetch_review_config(self) -> dict[str, Any]:
        result = (
            self.supabase_client.table(_SETTINGS_TABLE)
            .select("value")
            .eq("key", _REVIEW_CONFIG_KEY)
            .maybe_single()
            .execute()
        )
        row = result.data if result is not None else {}
        return _coerce_json_object(row.get("value"))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_default_policy(project_id: str) -> dict[str, Any]:
    """Return a synthetic default policy dict (not persisted)."""
    return {
        "project_id": project_id,
        "is_active": True,
        "is_default": True,
        **deepcopy(_DEFAULT_POLICY),
    }


def _build_effective_policy(
    project_id: str,
    *,
    stored_policy: dict[str, Any] | None = None,
    fallback_policy: dict[str, Any] | None = None,
    include_defaults: bool,
) -> dict[str, Any]:
    policy = _build_default_policy(project_id) if include_defaults else _build_runtime_policy(project_id)
    policy["is_default"] = stored_policy is None

    if stored_policy:
        for key, value in stored_policy.items():
            if key not in _POLICY_COLUMNS:
                policy[key] = value

    for source in (fallback_policy or {}, stored_policy or {}):
        for column in _POLICY_COLUMNS:
            value = source.get(column)
            if isinstance(value, dict):
                policy[column] = _deep_merge_dicts(policy.get(column, {}), value)

    return policy


def _build_runtime_policy(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "is_active": True,
        "is_default": True,
        **{column: {} for column in _POLICY_COLUMNS},
    }


def _build_legacy_policy(project_row: dict[str, Any] | None, review_config: dict[str, Any] | None) -> dict[str, Any]:
    policy, _ = _build_legacy_policy_with_sources(project_row, review_config)
    return policy


def _build_legacy_policy_with_sources(
    project_row: dict[str, Any] | None,
    review_config: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    runner_key, runner_source = _first_valid_project_value_with_source(
        project_row,
        section_names=_LEGACY_RUNNER_SECTIONS,
        candidate_keys=_LEGACY_RUNNER_KEYS,
        normalizer=_normalize_runner_key,
    )
    review_mode, review_source = _first_valid_project_value_with_source(
        project_row,
        section_names=_LEGACY_REVIEW_SECTIONS,
        candidate_keys=_LEGACY_REVIEW_MODE_KEYS,
        normalizer=_normalize_review_mode,
    )
    if review_mode is None and isinstance(review_config, dict):
        review_mode = _normalize_review_mode(review_config.get("review_mode"))
        if review_mode is not None:
            review_source = f"settings.{_REVIEW_CONFIG_KEY}.review_mode"

    worktree_mode, isolation_source = _first_valid_project_value_with_source(
        project_row,
        section_names=_LEGACY_ISOLATION_SECTIONS,
        candidate_keys=_LEGACY_ISOLATION_KEYS,
        normalizer=_normalize_worktree_mode,
    )

    policy: dict[str, Any] = {column: {} for column in _POLICY_COLUMNS}
    sources: dict[str, str] = {}
    if runner_key is not None:
        policy["model_routing"] = {"default_runner": runner_key}
        if runner_source is not None:
            sources["model_routing.default_runner"] = runner_source
    if review_mode is not None:
        policy["review_policy"] = {"review_mode": review_mode}
        if review_source is not None:
            sources["review_policy.review_mode"] = review_source
    if worktree_mode is not None:
        policy["isolation_policy"] = {"worktree_mode": worktree_mode}
        if isolation_source is not None:
            sources["isolation_policy.worktree_mode"] = isolation_source
    return {column: value for column, value in policy.items() if value}, sources


def extract_project_policy_sources(project_row: dict[str, Any] | None) -> dict[str, Any]:
    """Extract project-scoped policy values from deprecated metadata fields."""
    return _build_legacy_policy(project_row, review_config=None)


def _project_section(project_row: dict[str, Any] | None, section_name: str) -> dict[str, Any] | None:
    if not isinstance(project_row, dict):
        return None

    value = project_row.get(section_name)
    if isinstance(value, dict):
        return value
    return None


def _deep_merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def _merge_policy_columns_with_precedence(
    *,
    preferred: dict[str, Any] | None,
    canonical: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge policy columns while preserving canonical values over deprecated ones."""
    merged = {column: {} for column in _POLICY_COLUMNS}
    for column in _POLICY_COLUMNS:
        preferred_value = preferred.get(column) if isinstance(preferred, dict) else None
        canonical_value = canonical.get(column) if isinstance(canonical, dict) else None
        if isinstance(preferred_value, dict):
            merged[column] = _deep_merge_dicts(merged[column], preferred_value)
        if isinstance(canonical_value, dict):
            merged[column] = _deep_merge_dicts(merged[column], canonical_value)
    return merged


def _coerce_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _first_valid_project_value(
    project_row: dict[str, Any] | None,
    *,
    section_names: tuple[str, ...],
    candidate_keys: tuple[str, ...],
    normalizer,
) -> str | None:
    value, _ = _first_valid_project_value_with_source(
        project_row,
        section_names=section_names,
        candidate_keys=candidate_keys,
        normalizer=normalizer,
    )
    return value


def _first_valid_project_value_with_source(
    project_row: dict[str, Any] | None,
    *,
    section_names: tuple[str, ...],
    candidate_keys: tuple[str, ...],
    normalizer,
) -> tuple[str | None, str | None]:
    for section_name in section_names:
        section = _project_section(project_row, section_name)
        if section is None:
            continue
        for key in candidate_keys:
            normalized = normalizer(section.get(key))
            if normalized is not None:
                return normalized, f"{section_name}.{key}"
    return None, None


def _log_applied_legacy_fallbacks(
    *,
    project_id: str,
    stored_policy: dict[str, Any] | None,
    fallback_policy: dict[str, Any] | None,
    fallback_sources: dict[str, str],
) -> None:
    applied_fallbacks = _collect_applied_legacy_fallbacks(
        stored_policy=stored_policy,
        fallback_policy=fallback_policy,
        fallback_sources=fallback_sources,
    )
    if not applied_fallbacks:
        return

    logger.warning(
        "Using deprecated project policy fallback | project_id=%s | applied_fallbacks=%s",
        project_id,
        applied_fallbacks,
    )


def _collect_applied_legacy_fallbacks(
    *,
    stored_policy: dict[str, Any] | None,
    fallback_policy: dict[str, Any] | None,
    fallback_sources: dict[str, str],
) -> list[str]:
    if not isinstance(fallback_policy, dict) or not fallback_policy:
        return []

    applied: list[str] = []
    for column, key in _CANONICAL_POLICY_KEYS:
        source = fallback_sources.get(f"{column}.{key}")
        if source is None:
            continue

        fallback_column = fallback_policy.get(column)
        if not isinstance(fallback_column, dict):
            continue
        fallback_value = fallback_column.get(key)
        if not isinstance(fallback_value, str) or not fallback_value.strip():
            continue

        stored_column = stored_policy.get(column) if isinstance(stored_policy, dict) else None
        if isinstance(stored_column, dict):
            stored_value = stored_column.get(key)
            if isinstance(stored_value, str) and stored_value.strip():
                continue

        applied.append(f"{column}.{key}<-{source}")

    return applied


def _normalize_runner_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate in _VALID_RUNNERS:
        return candidate
    return None


def _normalize_review_mode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate in _VALID_REVIEW_MODES:
        return candidate
    return None


def _normalize_worktree_mode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    aliases = {
        "git-worktree": "isolated",
        "isolated": "isolated",
        "per-task": "per-task",
        "shared": "shared",
        "worktree": "worktree",  # conflict-detected: create worktree for overlapping tasks
        "queue": "queue",        # conflict-detected: queue overlapping tasks (explicit alias for shared)
    }
    return aliases.get(candidate)
