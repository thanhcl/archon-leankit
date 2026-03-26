"""Markdown parser and import service for project implementation plans.

Parses canonical plan documents (X-PY-ZZ item key format) and imports them
into the plan model atomically. Supports diff preview on re-import and flags
removed items rather than deleting them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

_PLANS_TABLE = "project_implementation_plans"
_PHASES_TABLE = "project_implementation_phases"
_ITEMS_TABLE = "project_implementation_items"
_DEPS_TABLE = "project_implementation_item_dependencies"

# Matches item keys like B-P1-01, ARCH-P2-03, B-P10-01
_ITEM_KEY_RE = re.compile(r"^([A-Z]+-P\d+-\d+):\s*(.+)$")
# Matches structured fields: - **Field**: value  or  * **Field**: value
_FIELD_RE = re.compile(r"^[-*]\s+\*\*([^*]+)\*\*:\s*(.*)$")
# Matches table-row fields: | **Field** | value |
_TABLE_FIELD_RE = re.compile(r"^\|\s*\*\*([^*]+)\*\*\s*\|\s*(.*?)\s*\|?\s*$")

_VALID_STATUSES = frozenset(
    {"planned", "ready", "in_progress", "blocked", "review", "done", "deferred", "cancelled"}
)
_VALID_PRIORITIES = frozenset({"low", "medium", "high", "critical"})
_VALID_COMPLEXITIES = frozenset({"simple", "medium", "complex"})

# Status normalisation map (common aliases in plan docs)
_STATUS_ALIASES: dict[str, str] = {
    "todo": "planned",
    "not started": "planned",
    "in progress": "in_progress",
    "wip": "in_progress",
    "complete": "done",
    "completed": "done",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class ParsedItem:
    item_key: str
    title: str
    phase_title: str
    status: str = "planned"
    priority: str = "medium"
    complexity: str = "simple"
    description: str | None = None
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    item_order: int = 0


@dataclass
class ParsedPhase:
    title: str
    description: str | None = None
    phase_order: int = 0
    items: list[ParsedItem] = field(default_factory=list)


@dataclass
class ParsedPlan:
    title: str
    description: str | None = None
    phases: list[ParsedPhase] = field(default_factory=list)

    @property
    def all_items(self) -> list[ParsedItem]:
        result: list[ParsedItem] = []
        for phase in self.phases:
            result.extend(phase.items)
        return result


# ── Parser ────────────────────────────────────────────────────────────────────


class PlanMarkdownParser:
    """Parse canonical implementation plan markdown into structured data.

    Supported markdown format::

        # Plan Title
        Optional plan description.

        ## Phase 1: Phase Name
        Optional phase description.

        ### B-P1-01: Item Title
        Optional item description.

        - **Status**: planned
        - **Priority**: high
        - **Complexity**: medium
        - **Dependencies**: B-P2-01, B-P2-02

        **Acceptance Criteria**:
        - Criterion 1
        - Criterion 2
    """

    def parse(self, content: str) -> ParsedPlan:
        """Parse markdown content and return a ParsedPlan."""
        lines = content.splitlines()

        plan_title = "Imported Plan"
        plan_desc_parts: list[str] = []
        phases: list[ParsedPhase] = []
        current_phase: ParsedPhase | None = None
        current_item: ParsedItem | None = None

        reading_plan_desc = False
        reading_item_desc = False
        in_acceptance_criteria = False
        item_order_counter = 0

        for line in lines:
            stripped = line.strip()

            # ── H1: plan title ────────────────────────────────────────────
            if stripped.startswith("# "):
                raw = stripped[2:].strip()
                # Normalise common title prefixes
                for prefix in ("implementation plan:", "plan:"):
                    if raw.lower().startswith(prefix):
                        raw = raw[len(prefix):].strip()
                        break
                plan_title = raw or "Imported Plan"
                reading_plan_desc = True
                continue

            # ── H2 or H3: phase ──────────────────────────────────────────
            if stripped.startswith("## ") or (stripped.startswith("### ") and not stripped.startswith("#### ")):
                hdr_len = 4 if stripped.startswith("### ") else 3
                raw_phase_title = stripped[hdr_len:].strip()
                # Strip "Phase N:" prefix from display title
                phase_display = re.sub(
                    r"^Phase\s+\d+:\s*", "", raw_phase_title, flags=re.IGNORECASE
                ).strip() or raw_phase_title

                current_phase = ParsedPhase(title=phase_display, phase_order=len(phases))
                phases.append(current_phase)
                current_item = None
                reading_plan_desc = False
                reading_item_desc = False
                in_acceptance_criteria = False
                continue

            # ── H3/H4: item (must match key pattern) ────────────────────────
            if (stripped.startswith("### ") or stripped.startswith("#### ")) and current_phase is not None:
                hdr_len = 5 if stripped.startswith("#### ") else 4
                m = _ITEM_KEY_RE.match(stripped[hdr_len:].strip())
                if m:
                    current_item = ParsedItem(
                        item_key=m.group(1),
                        title=m.group(2).strip(),
                        phase_title=current_phase.title,
                        item_order=item_order_counter,
                    )
                    item_order_counter += 1
                    current_phase.items.append(current_item)
                    reading_item_desc = True
                    in_acceptance_criteria = False
                continue

            # ── Plan description (before any phase) ───────────────────────
            if reading_plan_desc and current_phase is None and stripped and not stripped.startswith("#"):
                plan_desc_parts.append(stripped)
                continue

            if current_item is None:
                # Phase description handling (between ## and first ###)
                continue

            # ── Acceptance criteria header ─────────────────────────────────
            if re.match(r"^\*?\*?acceptance criteria\*?\*?:?$", stripped, re.IGNORECASE):
                in_acceptance_criteria = True
                reading_item_desc = False
                continue

            # ── Structured field: - **Key**: value ────────────────────────
            fm = _FIELD_RE.match(stripped)
            if not fm:
                # Also try table-row format: | **Key** | value |
                fm = _TABLE_FIELD_RE.match(stripped)
            if fm:
                fname = fm.group(1).strip().lower()
                fval = fm.group(2).strip().strip('`').strip()
                self._apply_field(current_item, fname, fval)
                reading_item_desc = False
                in_acceptance_criteria = False
                continue

            # ── Acceptance criteria list items ────────────────────────────
            if in_acceptance_criteria and re.match(r"^[-*]\s+", stripped):
                ac_text = re.sub(r"^[-*]\s+", "", stripped).strip()
                if ac_text:
                    current_item.acceptance_criteria.append(ac_text)
                continue

            # ── Item description (prose before structured fields) ─────────
            if (
                reading_item_desc
                and stripped
                and not stripped.startswith("-")
                and not stripped.startswith("*")
                and not stripped.startswith("#")
            ):
                if current_item.description is None:
                    current_item.description = stripped
                else:
                    current_item.description += " " + stripped
                continue

            # ── Dividers and blank lines ──────────────────────────────────
            if not stripped or stripped in ("---", "***", "___"):
                reading_item_desc = False
                continue

        plan_desc = " ".join(plan_desc_parts) if plan_desc_parts else None
        return ParsedPlan(title=plan_title, description=plan_desc, phases=phases)

    @staticmethod
    def _apply_field(item: ParsedItem, name: str, value: str) -> None:
        """Apply a structured field value to a ParsedItem."""
        if name == "status":
            # Strip markdown formatting and dependency suffixes
            # e.g. "`DONE`" → "done", "`BLOCKED` — depends on X" → "blocked"
            norm = re.sub(r"[`*]", "", value).strip().lower()
            norm = re.split(r"\s*[—–-]\s*", norm)[0].strip()
            norm = _STATUS_ALIASES.get(norm, norm)
            if norm in _VALID_STATUSES:
                item.status = norm
        elif name == "priority":
            if value.lower() in _VALID_PRIORITIES:
                item.priority = value.lower()
        elif name == "complexity":
            if value.lower() in _VALID_COMPLEXITIES:
                item.complexity = value.lower()
        elif name == "dependencies":
            if value and value.lower() not in ("none", "-", "n/a"):
                deps = [d.strip() for d in re.split(r"[,;]+", value) if d.strip()]
                item.dependencies = deps


# ── Import service ────────────────────────────────────────────────────────────


def compute_sha256(content: str) -> str:
    """Compute SHA-256 hex digest of UTF-8 encoded content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PlanImportService:
    """Import canonical plan markdown documents into the plan model.

    Supports both initial import (creates plan + phases + items + dependencies
    atomically) and re-import (diff preview and controlled update).
    """

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()
        self._parser = PlanMarkdownParser()

    # ── Public API ────────────────────────────────────────────────────────

    def import_plan(
        self,
        project_id: str,
        content: str,
        *,
        plan_id: str | None = None,
        source_path: str | None = None,
        preview_only: bool = True,
    ) -> tuple[bool, dict[str, Any]]:
        """Parse markdown and import it into the plan model.

        Args:
            project_id: Target project UUID.
            content: Raw markdown string.
            plan_id: If provided, re-import into this existing plan.
            source_path: Optional path label stored for reference.
            preview_only: If True (default), return diff without applying changes.
                          For a new plan this is always False (creates immediately).

        Returns:
            (ok, result) where result contains action, plan_id, diff, counts.
        """
        if not project_id:
            return False, {"error": "project_id is required"}
        if not content or not content.strip():
            return False, {"error": "content is required"}

        source_hash = compute_sha256(content)
        parsed = self._parser.parse(content)

        if not parsed.phases:
            return False, {"error": "No phases found in document; check markdown format"}
        if not parsed.all_items:
            return False, {"error": "No items with valid keys (X-PY-ZZ format) found in document"}

        # Determine whether this is a new import or re-import
        existing_plan = self._resolve_existing_plan(project_id, plan_id, parsed.title)

        if existing_plan is None:
            # New plan — create atomically regardless of preview_only
            return self._create_plan_atomic(project_id, parsed, source_hash, source_path)

        # Re-import: compute diff
        existing_items = self._load_existing_items(existing_plan["id"])
        diff = self._compute_diff(existing_items, parsed)

        if preview_only:
            return True, {
                "action": "diff_preview",
                "plan_id": existing_plan["id"],
                "source_hash": source_hash,
                "hash_changed": existing_plan.get("source_doc_hash") != source_hash,
                "diff": diff,
            }

        # Apply re-import
        return self._apply_reimport(existing_plan, parsed, existing_items, diff, source_hash, source_path)

    # ── Private: plan resolution ──────────────────────────────────────────

    def _resolve_existing_plan(
        self, project_id: str, plan_id: str | None, parsed_title: str
    ) -> dict[str, Any] | None:
        """Find an existing plan to re-import into, if any."""
        if plan_id:
            result = (
                self.supabase_client.table(_PLANS_TABLE)
                .select("*")
                .eq("id", plan_id)
                .eq("project_id", project_id)
                .maybe_single()
                .execute()
            )
            return result.data if result is not None else None

        # Match by title under same project
        result = (
            self.supabase_client.table(_PLANS_TABLE)
            .select("*")
            .eq("project_id", project_id)
            .eq("title", parsed_title)
            .maybe_single()
            .execute()
        )
        return result.data if result is not None else None

    # ── Private: initial atomic creation ─────────────────────────────────

    def _create_plan_atomic(
        self,
        project_id: str,
        parsed: ParsedPlan,
        source_hash: str,
        source_path: str | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create plan + phases + items + dependencies in one logical transaction.

        If any step fails the plan record is deleted (cascading removes the rest).
        """
        now = datetime.now().isoformat()

        # 1. Create plan
        plan_payload: dict[str, Any] = {
            "project_id": project_id,
            "title": parsed.title,
            "description": parsed.description,
            "status": "draft",
            "source_doc_hash": source_hash,
            "source_doc_path": source_path,
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        try:
            r = self.supabase_client.table(_PLANS_TABLE).insert(plan_payload).execute()
            if not r.data:
                return False, {"error": "Failed to create plan record"}
            plan = r.data[0]
        except Exception as exc:
            logger.error(f"Failed to insert plan | project_id={project_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

        plan_id = plan["id"]

        try:
            phase_count, item_count, dep_count = self._insert_phases_items_deps(
                plan_id, parsed
            )
        except Exception as exc:
            logger.error(
                f"Atomic import failed — rolling back plan | plan_id={plan_id} | error={exc}",
                exc_info=True,
            )
            # Cascade delete cleans up phases/items/deps
            self._delete_plan_best_effort(plan_id)
            return False, {"error": f"Import failed during data insertion: {exc}"}

        return True, {
            "action": "created",
            "plan_id": plan_id,
            "source_hash": source_hash,
            "phases_created": phase_count,
            "items_created": item_count,
            "dependencies_created": dep_count,
            "diff": None,
        }

    def _insert_phases_items_deps(
        self, plan_id: str, parsed: ParsedPlan
    ) -> tuple[int, int, int]:
        """Insert phases, items, and dependency edges. Returns (phase, item, dep) counts."""
        now = datetime.now().isoformat()
        # key_to_db_id is used to resolve dependency edges
        key_to_db_id: dict[str, str] = {}

        # 2. Insert phases
        phase_id_map: dict[str, str] = {}  # phase_title -> db id
        for phase in parsed.phases:
            r = (
                self.supabase_client.table(_PHASES_TABLE)
                .insert(
                    {
                        "plan_id": plan_id,
                        "title": phase.title,
                        "description": phase.description,
                        "phase_order": phase.phase_order,
                        "metadata": {},
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                .execute()
            )
            if not r.data:
                raise RuntimeError(f"Failed to insert phase '{phase.title}'")
            phase_id_map[phase.title] = r.data[0]["id"]

        # 3. Insert items
        for item in parsed.all_items:
            phase_db_id = phase_id_map.get(item.phase_title)
            r = (
                self.supabase_client.table(_ITEMS_TABLE)
                .insert(
                    {
                        "plan_id": plan_id,
                        "phase_id": phase_db_id,
                        "title": item.title,
                        "description": self._build_item_description(item),
                        "status": item.status,
                        "item_order": item.item_order,
                        "priority": item.priority,
                        "complexity": item.complexity,
                        "item_key": item.item_key,
                        "metadata": {},
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                .execute()
            )
            if not r.data:
                raise RuntimeError(f"Failed to insert item '{item.item_key}'")
            key_to_db_id[item.item_key] = r.data[0]["id"]

        # 4. Insert dependencies
        dep_count = 0
        for item in parsed.all_items:
            dependent_db_id = key_to_db_id.get(item.item_key)
            if not dependent_db_id:
                continue
            for dep_key in item.dependencies:
                dependency_db_id = key_to_db_id.get(dep_key)
                if not dependency_db_id:
                    logger.warning(
                        f"Dependency key '{dep_key}' for item '{item.item_key}' not found in document — skipping"
                    )
                    continue
                try:
                    r = (
                        self.supabase_client.table(_DEPS_TABLE)
                        .insert(
                            {
                                "dependent_id": dependent_db_id,
                                "dependency_id": dependency_db_id,
                                "dependency_type": "blocks",
                                "metadata": {},
                                "created_at": now,
                            }
                        )
                        .execute()
                    )
                    if r.data:
                        dep_count += 1
                except Exception as exc:
                    logger.warning(
                        f"Skipping duplicate dependency '{item.item_key}' -> '{dep_key}': {exc}"
                    )

        return len(parsed.phases), len(parsed.all_items), dep_count

    @staticmethod
    def _build_item_description(item: ParsedItem) -> str | None:
        """Combine description text and acceptance criteria into a single description."""
        parts: list[str] = []
        if item.description:
            parts.append(item.description)
        if item.acceptance_criteria:
            ac_text = "Acceptance Criteria:\n" + "\n".join(
                f"- {c}" for c in item.acceptance_criteria
            )
            parts.append(ac_text)
        return "\n\n".join(parts) if parts else None

    def _delete_plan_best_effort(self, plan_id: str) -> None:
        """Delete a plan record (cascade removes phases/items/deps)."""
        try:
            self.supabase_client.table(_PLANS_TABLE).delete().eq("id", plan_id).execute()
        except Exception as exc:
            logger.error(f"Rollback delete failed | plan_id={plan_id} | error={exc}", exc_info=True)

    # ── Private: diff ────────────────────────────────────────────────────

    def _load_existing_items(self, plan_id: str) -> list[dict[str, Any]]:
        """Load all existing items for a plan indexed by item_key."""
        r = (
            self.supabase_client.table(_ITEMS_TABLE)
            .select("*")
            .eq("plan_id", plan_id)
            .order("item_order", desc=False)
            .execute()
        )
        return r.data or []

    def _compute_diff(
        self,
        existing_items: list[dict[str, Any]],
        parsed: ParsedPlan,
    ) -> dict[str, Any]:
        """Compare existing DB items against parsed doc items.

        Returns a diff dict with added/removed/changed/unchanged lists.
        Removed items are flagged but never silently deleted.
        """
        existing_by_key: dict[str, dict[str, Any]] = {
            item["item_key"]: item
            for item in existing_items
            if item.get("item_key")
        }
        parsed_by_key: dict[str, ParsedItem] = {
            item.item_key: item for item in parsed.all_items
        }

        added: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        changed: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []

        for key, parsed_item in parsed_by_key.items():
            if key not in existing_by_key:
                added.append(
                    {
                        "item_key": key,
                        "title": parsed_item.title,
                        "phase": parsed_item.phase_title,
                        "status": parsed_item.status,
                    }
                )
            else:
                existing = existing_by_key[key]
                changes = self._find_changes(existing, parsed_item)
                if changes:
                    changed.append({"item_key": key, "title": parsed_item.title, "changes": changes})
                else:
                    unchanged.append({"item_key": key, "title": parsed_item.title})

        for key, existing in existing_by_key.items():
            if key not in parsed_by_key:
                removed.append(
                    {
                        "item_key": key,
                        "title": existing.get("title", key),
                        "status": existing.get("status"),
                        "note": "Present in DB but missing from import document — will be flagged, not deleted",
                    }
                )

        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
        }

    @staticmethod
    def _find_changes(existing: dict[str, Any], parsed: ParsedItem) -> list[dict[str, Any]]:
        """Return a list of field-level changes between existing DB item and parsed item."""
        changes: list[dict[str, Any]] = []
        check_fields: list[tuple[str, Any]] = [
            ("title", parsed.title),
            ("status", parsed.status),
            ("priority", parsed.priority),
            ("complexity", parsed.complexity),
        ]
        for field_name, new_val in check_fields:
            old_val = existing.get(field_name)
            if old_val != new_val:
                changes.append({"field": field_name, "old": old_val, "new": new_val})
        return changes

    # ── Private: apply re-import ──────────────────────────────────────────

    def _apply_reimport(
        self,
        existing_plan: dict[str, Any],
        parsed: ParsedPlan,
        existing_items: list[dict[str, Any]],
        diff: dict[str, Any],
        source_hash: str,
        source_path: str | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Apply diff changes to an existing plan."""
        plan_id = existing_plan["id"]
        now = datetime.now().isoformat()

        existing_by_key: dict[str, dict[str, Any]] = {
            item["item_key"]: item
            for item in existing_items
            if item.get("item_key")
        }
        parsed_by_key: dict[str, ParsedItem] = {
            item.item_key: item for item in parsed.all_items
        }

        # Ensure phases exist, collect phase title → db id
        phase_id_map = self._ensure_phases(plan_id, parsed.phases, now)

        # Update changed items
        for change in diff.get("changed", []):
            key = change["item_key"]
            existing = existing_by_key.get(key)
            parsed_item = parsed_by_key.get(key)
            if not existing or not parsed_item:
                continue
            updates: dict[str, Any] = {"updated_at": now}
            for c in change["changes"]:
                updates[c["field"]] = c["new"]
            updates["description"] = self._build_item_description(parsed_item)
            phase_db_id = phase_id_map.get(parsed_item.phase_title)
            if phase_db_id:
                updates["phase_id"] = phase_db_id
            try:
                self.supabase_client.table(_ITEMS_TABLE).update(updates).eq("id", existing["id"]).execute()
            except Exception as exc:
                logger.error(f"Failed to update item '{key}' | error={exc}", exc_info=True)

        # Insert added items
        added_key_to_id: dict[str, str] = {}
        for add in diff.get("added", []):
            key = add["item_key"]
            parsed_item = parsed_by_key[key]
            phase_db_id = phase_id_map.get(parsed_item.phase_title)
            try:
                r = (
                    self.supabase_client.table(_ITEMS_TABLE)
                    .insert(
                        {
                            "plan_id": plan_id,
                            "phase_id": phase_db_id,
                            "title": parsed_item.title,
                            "description": self._build_item_description(parsed_item),
                            "status": parsed_item.status,
                            "item_order": parsed_item.item_order,
                            "priority": parsed_item.priority,
                            "complexity": parsed_item.complexity,
                            "item_key": key,
                            "metadata": {},
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    .execute()
                )
                if r.data:
                    added_key_to_id[key] = r.data[0]["id"]
            except Exception as exc:
                logger.error(f"Failed to insert added item '{key}' | error={exc}", exc_info=True)

        # Flag removed items — never silently delete
        for rem in diff.get("removed", []):
            key = rem["item_key"]
            existing = existing_by_key.get(key)
            if not existing:
                continue
            try:
                existing_meta = existing.get("metadata") or {}
                existing_meta["removed_in_reimport"] = True
                existing_meta["removed_at"] = now
                self.supabase_client.table(_ITEMS_TABLE).update(
                    {"metadata": existing_meta, "updated_at": now}
                ).eq("id", existing["id"]).execute()
            except Exception as exc:
                logger.error(f"Failed to flag removed item '{key}' | error={exc}", exc_info=True)

        # Re-insert dependencies for changed and added items
        all_key_to_id = {**{k: v["id"] for k, v in existing_by_key.items()}, **added_key_to_id}
        affected_keys = {c["item_key"] for c in diff.get("changed", [])} | set(added_key_to_id)
        dep_count = self._upsert_dependencies(affected_keys, parsed_by_key, all_key_to_id, now)

        # Update plan record with new hash
        try:
            self.supabase_client.table(_PLANS_TABLE).update(
                {
                    "source_doc_hash": source_hash,
                    "source_doc_path": source_path or existing_plan.get("source_doc_path"),
                    "updated_at": now,
                }
            ).eq("id", plan_id).execute()
        except Exception as exc:
            logger.error(f"Failed to update plan hash | plan_id={plan_id} | error={exc}", exc_info=True)

        return True, {
            "action": "updated",
            "plan_id": plan_id,
            "source_hash": source_hash,
            "diff": diff,
            "dependencies_updated": dep_count,
        }

    def _ensure_phases(
        self, plan_id: str, phases: list[ParsedPhase], now: str
    ) -> dict[str, str]:
        """Return a map of phase_title → db_id, inserting any missing phases."""
        r = (
            self.supabase_client.table(_PHASES_TABLE)
            .select("id,title")
            .eq("plan_id", plan_id)
            .execute()
        )
        existing: dict[str, str] = {row["title"]: row["id"] for row in (r.data or [])}

        for phase in phases:
            if phase.title not in existing:
                try:
                    ins = (
                        self.supabase_client.table(_PHASES_TABLE)
                        .insert(
                            {
                                "plan_id": plan_id,
                                "title": phase.title,
                                "description": phase.description,
                                "phase_order": phase.phase_order,
                                "metadata": {},
                                "created_at": now,
                                "updated_at": now,
                            }
                        )
                        .execute()
                    )
                    if ins.data:
                        existing[phase.title] = ins.data[0]["id"]
                except Exception as exc:
                    logger.error(f"Failed to insert phase '{phase.title}' | error={exc}", exc_info=True)

        return existing

    def _upsert_dependencies(
        self,
        affected_keys: set[str],
        parsed_by_key: dict[str, ParsedItem],
        key_to_id: dict[str, str],
        now: str,
    ) -> int:
        """Re-insert dependency edges for affected items.

        Existing edges are left intact; new edges are inserted (ignoring duplicates).
        """
        dep_count = 0
        for key in affected_keys:
            parsed_item = parsed_by_key.get(key)
            if not parsed_item:
                continue
            dependent_id = key_to_id.get(key)
            if not dependent_id:
                continue
            for dep_key in parsed_item.dependencies:
                dep_id = key_to_id.get(dep_key)
                if not dep_id:
                    logger.warning(
                        f"Dependency key '{dep_key}' for item '{key}' not found — skipping"
                    )
                    continue
                try:
                    r = (
                        self.supabase_client.table(_DEPS_TABLE)
                        .insert(
                            {
                                "dependent_id": dependent_id,
                                "dependency_id": dep_id,
                                "dependency_type": "blocks",
                                "metadata": {},
                                "created_at": now,
                            }
                        )
                        .execute()
                    )
                    if r.data:
                        dep_count += 1
                except Exception as exc:
                    # Duplicate edges are expected for unchanged dependencies
                    logger.debug(f"Dependency '{key}' -> '{dep_key}' not inserted: {exc}")
        return dep_count
