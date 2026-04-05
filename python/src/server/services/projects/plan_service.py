"""Service layer for project implementation plans, phases, items, dependencies, and task links."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.server.utils import get_supabase_client

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

_PLANS_TABLE = "project_implementation_plans"
_PHASES_TABLE = "project_implementation_phases"
_ITEMS_TABLE = "project_implementation_items"
_DEPS_TABLE = "project_implementation_item_dependencies"
_LINKS_TABLE = "project_implementation_item_task_links"
_SNAPSHOTS_TABLE = "project_implementation_snapshots"


class PlanService:
    """CRUD operations for implementation plans, phases, items, and dependencies."""

    def __init__(self, supabase_client=None):
        self.supabase_client = supabase_client or get_supabase_client()

    # ── Plans ────────────────────────────────────────────────────────────

    def list_plans(self, project_id: str, limit: int = 50) -> tuple[bool, dict[str, Any]]:
        """List all implementation plans for a project."""
        try:
            result = (
                self.supabase_client.table(_PLANS_TABLE)
                .select("*")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            plans = result.data or []
            return True, {"plans": plans, "total_count": len(plans)}
        except Exception as exc:
            logger.error(f"Failed to list plans | project_id={project_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_plan(self, plan_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single implementation plan by ID."""
        try:
            result = (
                self.supabase_client.table(_PLANS_TABLE)
                .select("*")
                .eq("id", plan_id)
                .maybe_single()
                .execute()
            )
            if result is None or not result.data:
                return False, {"error": f"Plan {plan_id} not found"}
            return True, {"plan": result.data}
        except Exception as exc:
            logger.error(f"Failed to get plan | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def create_plan(
        self,
        *,
        project_id: str,
        title: str,
        description: str | None = None,
        status: str = "draft",
        created_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new implementation plan."""
        if not project_id:
            return False, {"error": "project_id is required"}
        if not title:
            return False, {"error": "title is required"}
        try:
            now = datetime.now().isoformat()
            payload: dict[str, Any] = {
                "project_id": project_id,
                "title": title,
                "description": description,
                "status": status,
                "created_by": created_by,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }
            result = self.supabase_client.table(_PLANS_TABLE).insert(payload).execute()
            if not result.data:
                return False, {"error": "Failed to create plan"}
            return True, {"plan": result.data[0]}
        except Exception as exc:
            logger.error(f"Failed to create plan | project_id={project_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def update_plan(
        self,
        plan_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Update an existing implementation plan."""
        ok, check = self.get_plan(plan_id)
        if not ok:
            return False, check

        updates: dict[str, Any] = {"updated_at": datetime.now().isoformat()}
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if status is not None:
            updates["status"] = status
        if metadata is not None:
            updates["metadata"] = metadata

        try:
            result = (
                self.supabase_client.table(_PLANS_TABLE)
                .update(updates)
                .eq("id", plan_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Failed to update plan {plan_id}"}
            return True, {"plan": result.data[0]}
        except Exception as exc:
            logger.error(f"Failed to update plan | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    # ── Phases ───────────────────────────────────────────────────────────

    def list_phases(self, plan_id: str) -> tuple[bool, dict[str, Any]]:
        """List all phases for an implementation plan."""
        ok, check = self.get_plan(plan_id)
        if not ok:
            return False, check
        try:
            result = (
                self.supabase_client.table(_PHASES_TABLE)
                .select("*")
                .eq("plan_id", plan_id)
                .order("phase_order", desc=False)
                .execute()
            )
            phases = result.data or []
            return True, {"phases": phases, "total_count": len(phases)}
        except Exception as exc:
            logger.error(f"Failed to list phases | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def create_phase(
        self,
        plan_id: str,
        *,
        title: str,
        description: str | None = None,
        phase_order: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new phase within an implementation plan."""
        ok, check = self.get_plan(plan_id)
        if not ok:
            return False, check
        if not title:
            return False, {"error": "title is required"}
        try:
            now = datetime.now().isoformat()
            payload: dict[str, Any] = {
                "plan_id": plan_id,
                "title": title,
                "description": description,
                "phase_order": phase_order,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }
            result = self.supabase_client.table(_PHASES_TABLE).insert(payload).execute()
            if not result.data:
                return False, {"error": "Failed to create phase"}
            return True, {"phase": result.data[0]}
        except Exception as exc:
            logger.error(f"Failed to create phase | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    # ── Items ────────────────────────────────────────────────────────────

    def list_items(self, plan_id: str, phase_id: str | None = None) -> tuple[bool, dict[str, Any]]:
        """List items for a plan, optionally scoped to a phase."""
        ok, check = self.get_plan(plan_id)
        if not ok:
            return False, check
        try:
            query = (
                self.supabase_client.table(_ITEMS_TABLE)
                .select("*")
                .eq("plan_id", plan_id)
            )
            if phase_id is not None:
                query = query.eq("phase_id", phase_id)
            result = query.order("item_order", desc=False).execute()
            items = result.data or []
            return True, {"items": items, "total_count": len(items)}
        except Exception as exc:
            logger.error(f"Failed to list items | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_item(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single implementation item by ID."""
        try:
            result = (
                self.supabase_client.table(_ITEMS_TABLE)
                .select("*")
                .eq("id", item_id)
                .maybe_single()
                .execute()
            )
            if result is None or not result.data:
                return False, {"error": f"Item {item_id} not found"}
            return True, {"item": result.data}
        except Exception as exc:
            logger.error(f"Failed to get item | item_id={item_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def create_item(
        self,
        plan_id: str,
        *,
        title: str,
        phase_id: str | None = None,
        description: str | None = None,
        status: str = "planned",
        item_order: int = 0,
        priority: str = "medium",
        complexity: str = "simple",
        item_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a new item within an implementation plan."""
        ok, check = self.get_plan(plan_id)
        if not ok:
            return False, check
        if not title:
            return False, {"error": "title is required"}
        try:
            now = datetime.now().isoformat()
            payload: dict[str, Any] = {
                "plan_id": plan_id,
                "phase_id": phase_id,
                "title": title,
                "description": description,
                "status": status,
                "item_order": item_order,
                "priority": priority,
                "complexity": complexity,
                "item_key": item_key,
                "metadata": metadata or {},
                "created_at": now,
                "updated_at": now,
            }
            result = self.supabase_client.table(_ITEMS_TABLE).insert(payload).execute()
            if not result.data:
                return False, {"error": "Failed to create item"}
            return True, {"item": result.data[0]}
        except Exception as exc:
            logger.error(f"Failed to create item | plan_id={plan_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def update_item(
        self,
        item_id: str,
        *,
        phase_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        item_order: int | None = None,
        priority: str | None = None,
        complexity: str | None = None,
        item_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        _bypass_lock: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """Update an existing implementation item.

        Rejects mutations on contract-locked items unless ``_bypass_lock``
        is True (used internally by sprint batch approve/create).
        """
        ok, check = self.get_item(item_id)
        if not ok:
            return False, check

        # Guard: reject mutations on contract-locked items (N4-1)
        if not _bypass_lock:
            item_meta = (check.get("item") or {}).get("metadata") or {}
            if item_meta.get("contract_locked"):
                return False, {"error": f"Item {item_id} is contract-locked by sprint batch"}

        updates: dict[str, Any] = {"updated_at": datetime.now().isoformat()}
        if phase_id is not None:
            updates["phase_id"] = phase_id
        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if status is not None:
            updates["status"] = status
        if item_order is not None:
            updates["item_order"] = item_order
        if priority is not None:
            updates["priority"] = priority
        if complexity is not None:
            updates["complexity"] = complexity
        if item_key is not None:
            updates["item_key"] = item_key
        if metadata is not None:
            updates["metadata"] = metadata

        try:
            result = (
                self.supabase_client.table(_ITEMS_TABLE)
                .update(updates)
                .eq("id", item_id)
                .execute()
            )
            if not result.data:
                return False, {"error": f"Failed to update item {item_id}"}
            return True, {"item": result.data[0]}
        except Exception as exc:
            logger.error(f"Failed to update item | item_id={item_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    # ── Dependencies ─────────────────────────────────────────────────────

    def list_dependencies(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """List all dependencies for an implementation item."""
        ok, check = self.get_item(item_id)
        if not ok:
            return False, check
        try:
            result = (
                self.supabase_client.table(_DEPS_TABLE)
                .select("*")
                .eq("dependent_id", item_id)
                .order("created_at", desc=False)
                .execute()
            )
            deps = result.data or []
            return True, {"dependencies": deps, "total_count": len(deps)}
        except Exception as exc:
            logger.error(f"Failed to list dependencies | item_id={item_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def create_dependency(
        self,
        dependent_id: str,
        *,
        dependency_id: str,
        dependency_type: str = "blocks",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Create a dependency edge between two implementation items."""
        ok, check = self.get_item(dependent_id)
        if not ok:
            return False, {"error": f"Dependent item {dependent_id} not found"}

        ok2, check2 = self.get_item(dependency_id)
        if not ok2:
            return False, {"error": f"Dependency item {dependency_id} not found"}

        if dependent_id == dependency_id:
            return False, {"error": "An item cannot depend on itself"}

        try:
            payload: dict[str, Any] = {
                "dependent_id": dependent_id,
                "dependency_id": dependency_id,
                "dependency_type": dependency_type,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
            }
            result = self.supabase_client.table(_DEPS_TABLE).insert(payload).execute()
            if not result.data:
                return False, {"error": "Failed to create dependency"}
            return True, {"dependency": result.data[0]}
        except Exception as exc:
            logger.error(
                f"Failed to create dependency | dependent_id={dependent_id} | dependency_id={dependency_id} | error={exc}",
                exc_info=True,
            )
            return False, {"error": str(exc)}

    # ── Task Links ───────────────────────────────────────────────────────

    _REF_PATTERN = re.compile(r"\bRef:\s*([\w][\w\-]+)", re.IGNORECASE)

    def list_task_links(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """List all task links for an implementation item."""
        ok, check = self.get_item(item_id)
        if not ok:
            return False, check
        try:
            result = (
                self.supabase_client.table(_LINKS_TABLE)
                .select("*")
                .eq("item_id", item_id)
                .order("created_at", desc=False)
                .execute()
            )
            links = result.data or []
            return True, {"links": links, "total_count": len(links)}
        except Exception as exc:
            logger.error(f"Failed to list task links | item_id={item_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_link(self, link_id: str) -> tuple[bool, dict[str, Any]]:
        """Get a single task link by ID."""
        try:
            result = (
                self.supabase_client.table(_LINKS_TABLE)
                .select("*")
                .eq("id", link_id)
                .maybe_single()
                .execute()
            )
            if result is None or not result.data:
                return False, {"error": f"Link {link_id} not found"}
            return True, {"link": result.data}
        except Exception as exc:
            logger.error(f"Failed to get link | link_id={link_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def create_task_link(
        self,
        item_id: str,
        *,
        task_id: str,
        link_type: str = "implements",
    ) -> tuple[bool, dict[str, Any]]:
        """Create a link between an implementation item and an Archon task."""
        ok, check = self.get_item(item_id)
        if not ok:
            return False, {"error": f"Item {item_id} not found"}

        if not task_id:
            return False, {"error": "task_id is required"}

        try:
            payload: dict[str, Any] = {
                "item_id": item_id,
                "task_id": task_id,
                "link_type": link_type,
                "created_at": datetime.now().isoformat(),
            }
            result = self.supabase_client.table(_LINKS_TABLE).insert(payload).execute()
            if not result.data:
                return False, {"error": "Failed to create task link"}
            return True, {"link": result.data[0]}
        except Exception as exc:
            logger.error(
                f"Failed to create task link | item_id={item_id} | task_id={task_id} | error={exc}",
                exc_info=True,
            )
            return False, {"error": str(exc)}

    def delete_task_link(self, link_id: str) -> tuple[bool, dict[str, Any]]:
        """Delete a task link record by ID."""
        ok, check = self.get_link(link_id)
        if not ok:
            return False, check
        try:
            self.supabase_client.table(_LINKS_TABLE).delete().eq("id", link_id).execute()
            return True, {"deleted": link_id}
        except Exception as exc:
            logger.error(f"Failed to delete task link | link_id={link_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def get_item_tasks(self, item_id: str) -> tuple[bool, dict[str, Any]]:
        """Return tasks linked to an item via the task links table, with task status info."""
        ok, check = self.get_item(item_id)
        if not ok:
            return False, check
        try:
            links_result = (
                self.supabase_client.table(_LINKS_TABLE)
                .select("id, task_id, link_type")
                .eq("item_id", item_id)
                .execute()
            )
            links = links_result.data or []
            if not links:
                return True, {"tasks": [], "total_count": 0}

            task_ids = [lnk["task_id"] for lnk in links]
            tasks_result = (
                self.supabase_client.table("archon_tasks")
                .select("id, title, status, assignee")
                .in_("id", task_ids)
                .execute()
            )
            tasks_by_id = {t["id"]: t for t in (tasks_result.data or [])}

            enriched = []
            for lnk in links:
                task = tasks_by_id.get(lnk["task_id"])
                if task:
                    enriched.append({
                        "id": task["id"],
                        "title": task["title"],
                        "status": task["status"],
                        "assignee": task.get("assignee", "User"),
                        "link_type": lnk["link_type"],
                    })
            return True, {"tasks": enriched, "total_count": len(enriched)}
        except Exception as exc:
            logger.error(f"Failed to get item tasks | item_id={item_id} | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

    def run_ref_link_parser(self, project_id: str | None = None) -> tuple[bool, dict[str, Any]]:
        """Scan task descriptions for 'Ref: <item_key>' patterns and populate plan_item_id.

        For each matched item_key:
        - Looks up the matching project_implementation_items row
        - Updates the task's plan_item_id FK
        - Creates a task link record if one doesn't already exist
        """
        try:
            query = self.supabase_client.table("archon_tasks").select("id, description, plan_item_id")
            if project_id:
                query = query.eq("project_id", project_id)
            tasks_result = query.execute()
            tasks = tasks_result.data or []
        except Exception as exc:
            logger.error(f"Failed to fetch tasks for auto-link | error={exc}", exc_info=True)
            return False, {"error": str(exc)}

        scanned = 0
        linked = 0
        skipped = 0
        errors: list[str] = []

        for task in tasks:
            scanned += 1
            description = task.get("description") or ""
            match = self._REF_PATTERN.search(description)
            if not match:
                skipped += 1
                continue

            item_key = match.group(1).strip()
            try:
                item_result = (
                    self.supabase_client.table(_ITEMS_TABLE)
                    .select("id, item_key, title, status")
                    .eq("item_key", item_key)
                    .maybe_single()
                    .execute()
                )
                if item_result is None or not item_result.data:
                    skipped += 1
                    continue

                item = item_result.data
                item_id = item["id"]
                task_id = task["id"]

                # Update plan_item_id on the task if not already set
                if task.get("plan_item_id") != item_id:
                    self.supabase_client.table("archon_tasks").update(
                        {"plan_item_id": item_id, "updated_at": datetime.now().isoformat()}
                    ).eq("id", task_id).execute()

                # Upsert task link (ignore duplicate due to unique constraint)
                try:
                    self.supabase_client.table(_LINKS_TABLE).insert(
                        {"item_id": item_id, "task_id": task_id, "link_type": "implements",
                         "created_at": datetime.now().isoformat()}
                    ).execute()
                except Exception:
                    pass  # Duplicate link already exists; safe to ignore

                linked += 1
            except Exception as exc:
                errors.append(f"task {task['id']}: {exc}")
                logger.error(f"Auto-link error | task_id={task['id']} | item_key={item_key} | error={exc}", exc_info=True)

        return True, {"scanned": scanned, "linked": linked, "skipped": skipped, "errors": errors}

    # ── Sprint Batch Orchestration (N4-1) ────────────────────────────────

    def create_sprint_batch(
        self,
        plan_id: str,
        *,
        batch_size: int = 5,
        filter_status: str = "READY",
        created_by: str = "archon",
    ) -> tuple[bool, dict[str, Any]]:
        """Group eligible plan items into a sprint-sized batch with contract lock.

        Selects up to ``batch_size`` items with the given status, marks them as
        ``contract_locked=True`` in item metadata, and stores the batch record
        in plan metadata.  Only Archon (control plane) may create batches —
        Virtual Office is read-only.

        Returns:
            (success, {"batch": {...}, "locked_items": [...]})
        """
        ok, items_result = self.list_items(plan_id)
        if not ok:
            return False, items_result

        all_items = items_result.get("items", [])
        eligible = [
            item for item in all_items
            if (item.get("status") or "").upper() == filter_status.upper()
            and not (item.get("metadata") or {}).get("contract_locked")
        ]

        # Sort by priority (P0 > P1 > P2 > P3) then item_order descending
        priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        eligible.sort(key=lambda i: (
            priority_rank.get((i.get("priority") or "P3").upper(), 9),
            -(i.get("item_order") or 0),
        ))

        selected = eligible[:batch_size]
        if not selected:
            return False, {"error": "No eligible items found for batching"}

        batch_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        batch_record = {
            "batch_id": batch_id,
            "plan_id": plan_id,
            "status": "pending_approval",
            "created_by": created_by,
            "created_at": datetime.now().isoformat(),
            "item_ids": [item["id"] for item in selected],
            "item_count": len(selected),
        }

        # Lock selected items
        locked_items = []
        for item in selected:
            item_meta = dict(item.get("metadata") or {})
            item_meta["contract_locked"] = True
            item_meta["sprint_batch_id"] = batch_id
            ok_update, _ = self.update_item(item["id"], metadata=item_meta)
            if ok_update:
                locked_items.append(item["id"])

        # Store batch in plan metadata
        ok_plan, plan_result = self.get_plan(plan_id)
        if ok_plan:
            plan_meta = dict((plan_result.get("plan") or {}).get("metadata") or {})
            batches = plan_meta.get("sprint_batches", [])
            batches.append(batch_record)
            plan_meta["sprint_batches"] = batches
            self.update_plan(plan_id, metadata=plan_meta)

        return True, {"batch": batch_record, "locked_items": locked_items}

    def get_sprint_batch(self, plan_id: str, batch_id: str) -> tuple[bool, dict[str, Any]]:
        """Retrieve a sprint batch record (read-only, VO-accessible)."""
        ok, plan_result = self.get_plan(plan_id)
        if not ok:
            return False, plan_result

        plan_meta = (plan_result.get("plan") or {}).get("metadata") or {}
        batches = plan_meta.get("sprint_batches", [])
        for batch in batches:
            if batch.get("batch_id") == batch_id:
                return True, {"batch": batch}
        return False, {"error": f"Batch {batch_id} not found"}

    def approve_sprint_batch(
        self,
        plan_id: str,
        batch_id: str,
        *,
        approved_by: str = "chief-teamlead",
    ) -> tuple[bool, dict[str, Any]]:
        """Release the human gate on a sprint batch and unlock items for execution.

        Only Archon (control plane) may approve — VO stays read-only.
        """
        ok, plan_result = self.get_plan(plan_id)
        if not ok:
            return False, plan_result

        plan_meta = dict((plan_result.get("plan") or {}).get("metadata") or {})
        batches = plan_meta.get("sprint_batches", [])
        target = None
        for batch in batches:
            if batch.get("batch_id") == batch_id:
                target = batch
                break
        if target is None:
            return False, {"error": f"Batch {batch_id} not found"}

        if target.get("status") != "pending_approval":
            return False, {"error": f"Batch {batch_id} is not pending approval (status: {target.get('status')})"}

        target["status"] = "approved"
        target["approved_by"] = approved_by
        target["approved_at"] = datetime.now().isoformat()

        # Unlock items
        unlocked = []
        for item_id in target.get("item_ids", []):
            ok_item, item_result = self.get_item(item_id)
            if not ok_item:
                continue
            item_meta = dict((item_result.get("item") or {}).get("metadata") or {})
            item_meta["contract_locked"] = False
            item_meta["sprint_approved"] = True
            self.update_item(item_id, metadata=item_meta)
            unlocked.append(item_id)

        plan_meta["sprint_batches"] = batches
        self.update_plan(plan_id, metadata=plan_meta)

        return True, {"batch": target, "unlocked_items": unlocked}
