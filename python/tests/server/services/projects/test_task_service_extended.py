"""Tests for TaskService extended fields: task_type, phase, module, sprint, tags, boundaries."""

from unittest.mock import MagicMock

import pytest

from src.server.services.projects.task_service import TaskService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "project_id": "proj-001",
        "title": "Test task",
        "description": "A test task",
        "status": "draft",
        "assignee": "User",
        "task_order": 0,
        "priority": "medium",
        "feature": None,
        "complexity": "simple",
        "owner": None,
        "source_app": None,
        "retry_count": 0,
        "max_retries": 3,
        "state_changed_at": "2026-01-01T00:00:00",
        "blocked_by": [],
        "allowed_paths": [],
        "forbidden_paths": [],
        "repo_guidance_packs": [],
        "created_by": "owner",
        "created_from": "api",
        "executed_by": None,
        "reviewed_by": [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "archived": False,
        "task_type": "feature",
        "phase": None,
        "module": None,
        "sprint": None,
        "tags": [],
        "parent_task_id": None,
        "sources": [],
        "code_examples": [],
        "state_history": [],
    }
    base.update(overrides)
    return base


def _mock_client(select_data=None, insert_data=None, update_data=None):
    client = MagicMock()
    table = MagicMock()

    # select chain
    select = MagicMock()
    select.eq.return_value = select
    select.not_.in_.return_value = select
    select.or_.return_value = select
    select.gte.return_value = select
    select.order.return_value = select
    execute_result = MagicMock()
    execute_result.data = select_data if select_data is not None else []
    select.execute.return_value = execute_result
    table.select.return_value = select

    # insert chain
    insert = MagicMock()
    insert_result = MagicMock()
    insert_result.data = insert_data if insert_data is not None else [_make_task()]
    insert.execute.return_value = insert_result
    table.insert.return_value = insert

    # update chain
    update = MagicMock()
    update.eq.return_value = update
    update_result = MagicMock()
    update_result.data = update_data if update_data is not None else [_make_task()]
    update.execute.return_value = update_result
    table.update.return_value = update

    client.table.return_value = table
    return client


# ---------------------------------------------------------------------------
# TaskType validation
# ---------------------------------------------------------------------------


class TestValidateTaskType:
    def test_valid_types(self):
        svc = TaskService(supabase_client=MagicMock())
        for task_type in ("bug", "feature", "improvement", "docs", "refactor", "test"):
            valid, msg = svc.validate_task_type(task_type)
            assert valid is True
            assert msg == ""

    def test_invalid_type(self):
        svc = TaskService(supabase_client=MagicMock())
        valid, msg = svc.validate_task_type("epic")
        assert valid is False
        assert "Invalid task_type" in msg


# ---------------------------------------------------------------------------
# Create task with new fields
# ---------------------------------------------------------------------------


class TestCreateTaskExtendedFields:
    @pytest.mark.asyncio
    async def test_create_with_task_type(self):
        task_data = _make_task(task_type="bug", module="engine", sprint="S1", tags=["urgent"])
        client = _mock_client(insert_data=[task_data])
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-001",
            title="Fix critical bug",
            task_type="bug",
            module="engine",
            sprint="S1",
            tags=["urgent"],
        )

        assert success is True
        # Verify insert was called with task_type
        insert_call_args = client.table.return_value.insert.call_args[0][0]
        assert insert_call_args["task_type"] == "bug"
        assert insert_call_args["module"] == "engine"
        assert insert_call_args["sprint"] == "S1"
        assert insert_call_args["tags"] == ["urgent"]

    @pytest.mark.asyncio
    async def test_create_with_invalid_task_type(self):
        client = _mock_client()
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-001",
            title="Bad type",
            task_type="epic",
        )

        assert success is False
        assert "Invalid task_type" in result["error"]

    @pytest.mark.asyncio
    async def test_create_with_repo_guidance_packs(self):
        task_data = _make_task(repo_guidance_packs=[{
            "title": "API service boundary",
            "guidance": "Keep FastAPI routes thin and move business logic into services.",
            "path_scope": ["src/server/api_routes/**", "src/server/services/**"],
        }])
        client = _mock_client(insert_data=[task_data])
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-001",
            title="Harden API boundary",
            repo_guidance_packs=[{
                "title": " API service boundary ",
                "guidance": " Keep FastAPI routes thin and move business logic into services. ",
                "path_scope": [" ./src/server/api_routes/** ", "src\\server\\services\\**"],
            }],
        )

        assert success is True
        insert_call_args = client.table.return_value.insert.call_args[0][0]
        assert insert_call_args["repo_guidance_packs"] == [{
            "title": "API service boundary",
            "guidance": "Keep FastAPI routes thin and move business logic into services.",
            "path_scope": ["src/server/api_routes/**", "src/server/services/**"],
        }]
        assert result["task"]["repo_guidance_packs"] == task_data["repo_guidance_packs"]

    @pytest.mark.asyncio
    async def test_create_with_editing_boundaries(self):
        task_data = _make_task(
            allowed_paths=["src/server/**"],
            forbidden_paths=["src/store/**"],
        )
        client = _mock_client(insert_data=[task_data])
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-001",
            title="Scoped task",
            allowed_paths=[" src/server/** ", "./docs/*.md"],
            forbidden_paths=["src\\store\\**", ""],
        )

        assert success is True
        insert_call_args = client.table.return_value.insert.call_args[0][0]
        assert insert_call_args["allowed_paths"] == ["src/server/**", "docs/*.md"]
        assert insert_call_args["forbidden_paths"] == ["src/store/**"]

    @pytest.mark.asyncio
    async def test_create_defaults_to_feature(self):
        task_data = _make_task()
        client = _mock_client(insert_data=[task_data])
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-001",
            title="Default type task",
        )

        assert success is True
        insert_call_args = client.table.return_value.insert.call_args[0][0]
        assert insert_call_args["task_type"] == "feature"
        assert insert_call_args["tags"] == []


# ---------------------------------------------------------------------------
# Update task with new fields
# ---------------------------------------------------------------------------


class TestUpdateTaskExtendedFields:
    @pytest.mark.asyncio
    async def test_update_task_type(self):
        updated = _make_task(task_type="bug")
        client = _mock_client(update_data=[updated])
        svc = TaskService(supabase_client=client)

        success, result = await svc.update_task("task-001", {"task_type": "bug"})

        assert success is True
        update_call_args = client.table.return_value.update.call_args[0][0]
        assert update_call_args["task_type"] == "bug"

    @pytest.mark.asyncio
    async def test_update_invalid_task_type(self):
        client = _mock_client()
        svc = TaskService(supabase_client=client)

        success, result = await svc.update_task("task-001", {"task_type": "epic"})

        assert success is False
        assert "Invalid task_type" in result["error"]

    @pytest.mark.asyncio
    async def test_update_phase_module_sprint_tags(self):
        updated = _make_task(phase="phase-2", module="archon-api", sprint="S3", tags=["perf", "critical"])
        client = _mock_client(update_data=[updated])
        svc = TaskService(supabase_client=client)

        success, result = await svc.update_task("task-001", {
            "phase": "phase-2",
            "module": "archon-api",
            "sprint": "S3",
            "tags": ["perf", "critical"],
        })

        assert success is True
        update_call_args = client.table.return_value.update.call_args[0][0]
        assert update_call_args["phase"] == "phase-2"
        assert update_call_args["module"] == "archon-api"
        assert update_call_args["sprint"] == "S3"
        assert update_call_args["tags"] == ["perf", "critical"]

    @pytest.mark.asyncio
    async def test_update_editing_boundaries(self):
        updated = _make_task(
            allowed_paths=["src/server/**"],
            forbidden_paths=["src/store/**"],
        )
        client = _mock_client(update_data=[updated])
        svc = TaskService(supabase_client=client)

        success, result = await svc.update_task("task-001", {
            "allowed_paths": [" ./src/server/** ", "docs/*.md"],
            "forbidden_paths": ["src\\store\\**"],
        })

        assert success is True
        update_call_args = client.table.return_value.update.call_args[0][0]
        assert update_call_args["allowed_paths"] == ["src/server/**", "docs/*.md"]
        assert update_call_args["forbidden_paths"] == ["src/store/**"]

    @pytest.mark.asyncio
    async def test_update_repo_guidance_packs(self):
        updated = _make_task(repo_guidance_packs=[{
            "title": "Docs safety",
            "guidance": "Do not edit generated files manually.",
            "path_scope": ["docs/**"],
        }])
        client = _mock_client(update_data=[updated])
        svc = TaskService(supabase_client=client)

        success, result = await svc.update_task("task-001", {
            "repo_guidance_packs": [
                {
                    "title": " Docs safety ",
                    "guidance": " Do not edit generated files manually. ",
                    "path_scope": [" ./docs/** "],
                },
                {
                    "title": "",
                    "guidance": "skip invalid",
                },
            ]
        })

        assert success is True
        update_call_args = client.table.return_value.update.call_args[0][0]
        assert update_call_args["repo_guidance_packs"] == [{
            "title": "Docs safety",
            "guidance": "Do not edit generated files manually.",
            "path_scope": ["docs/**"],
        }]
        assert result["task"]["repo_guidance_packs"] == updated["repo_guidance_packs"]


# ---------------------------------------------------------------------------
# List tasks with new filters
# ---------------------------------------------------------------------------


class TestListTasksExtendedFilters:
    def test_filter_by_task_type(self):
        tasks = [_make_task(task_type="bug")]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(task_type="bug")

        assert success is True
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["task_type"] == "bug"

    def test_filter_by_module(self):
        tasks = [_make_task(module="engine")]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(module="engine")

        assert success is True
        assert result["tasks"][0]["module"] == "engine"

    def test_filter_by_sprint(self):
        tasks = [_make_task(sprint="S2")]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(sprint="S2")

        assert success is True
        assert result["tasks"][0]["sprint"] == "S2"

    def test_filter_by_phase(self):
        tasks = [_make_task(phase="phase-1")]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(phase="phase-1")

        assert success is True
        assert result["tasks"][0]["phase"] == "phase-1"

    def test_filter_by_feature(self):
        tasks = [_make_task(feature="auth-system")]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(feature="auth-system")

        assert success is True
        assert result["tasks"][0]["feature"] == "auth-system"

    def test_list_includes_repo_guidance_packs(self):
        tasks = [_make_task(repo_guidance_packs=[{
            "title": "Engine prompt hygiene",
            "guidance": "Keep task prompts structured and deterministic.",
            "path_scope": ["src/server/services/engine/**"],
        }])]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks()

        assert success is True
        assert result["tasks"][0]["repo_guidance_packs"] == tasks[0]["repo_guidance_packs"]

    def test_filter_by_parent_task_id(self):
        tasks = [_make_task(parent_task_id="parent-001")]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(parent_task_id="parent-001")

        assert success is True
        assert result["tasks"][0]["parent_task_id"] == "parent-001"

    def test_invalid_task_type_filter(self):
        client = _mock_client()
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks(task_type="epic")

        assert success is False
        assert "Invalid task_type" in result["error"]

    def test_new_fields_in_response(self):
        tasks = [_make_task(
            task_type="improvement",
            phase="phase-2",
            module="toolkit",
            sprint="S5",
            tags=["dx"],
            parent_task_id="parent-002",
            allowed_paths=["src/server/**"],
            forbidden_paths=["src/store/**"],
        )]
        client = _mock_client(select_data=tasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.list_tasks()

        assert success is True
        task = result["tasks"][0]
        assert task["task_type"] == "improvement"
        assert task["phase"] == "phase-2"
        assert task["module"] == "toolkit"
        assert task["sprint"] == "S5"
        assert task["tags"] == ["dx"]
        assert task["parent_task_id"] == "parent-002"
        assert task["allowed_paths"] == ["src/server/**"]
        assert task["forbidden_paths"] == ["src/store/**"]


# ---------------------------------------------------------------------------
# Subtasks
# ---------------------------------------------------------------------------


class TestGetSubtasks:
    def test_returns_subtasks(self):
        subtasks = [
            _make_task(id="sub-1", parent_task_id="task-001", title="Subtask 1"),
            _make_task(id="sub-2", parent_task_id="task-001", title="Subtask 2"),
        ]
        client = _mock_client(select_data=subtasks)
        svc = TaskService(supabase_client=client)

        success, result = svc.get_subtasks("task-001")

        assert success is True
        assert result["count"] == 2
        assert len(result["subtasks"]) == 2

    def test_no_subtasks(self):
        client = _mock_client(select_data=[])
        svc = TaskService(supabase_client=client)

        success, result = svc.get_subtasks("task-001")

        assert success is True
        assert result["count"] == 0
        assert result["subtasks"] == []


# ---------------------------------------------------------------------------
# Circular dependency detection
# ---------------------------------------------------------------------------


def _mock_client_for_cycle(chain: dict[str, list[str]]) -> MagicMock:
    """Build a mock Supabase client where each task ID maps to its blocked_by list.

    chain: {"task-A": ["task-B"], "task-B": ["task-C"], ...}
    """
    client = MagicMock()

    def _table_side_effect(table_name: str):
        table = MagicMock()

        def _select_side_effect(fields: str):
            sel = MagicMock()

            def _eq_side_effect(col: str, val: str):
                blocked_by = chain.get(val, [])
                result = MagicMock()
                if val in chain:
                    result.data = [{"id": val, "blocked_by": blocked_by}]
                else:
                    result.data = []
                inner = MagicMock()
                inner.execute.return_value = result
                return inner

            sel.eq.side_effect = _eq_side_effect
            return sel

        table.select.side_effect = _select_side_effect
        return table

    client.table.side_effect = _table_side_effect
    return client


class TestDetectCircularDependency:
    def test_no_blocked_by_returns_no_cycle(self):
        svc = TaskService(supabase_client=MagicMock())
        has_cycle, msg = svc._detect_circular_dependency([])
        assert has_cycle is False
        assert msg == ""

    def test_linear_chain_no_cycle(self):
        """A → B → C: creating a task blocked by A should be fine."""
        client = _mock_client_for_cycle({"task-A": ["task-B"], "task-B": ["task-C"], "task-C": []})
        svc = TaskService(supabase_client=client)
        has_cycle, msg = svc._detect_circular_dependency(["task-A"])
        assert has_cycle is False

    def test_simple_direct_cycle_detected(self):
        """A is blocked by B and B is blocked by A — cycle detected."""
        client = _mock_client_for_cycle({"task-A": ["task-B"], "task-B": ["task-A"]})
        svc = TaskService(supabase_client=client)
        has_cycle, msg = svc._detect_circular_dependency(["task-A", "task-B"])
        assert has_cycle is True
        assert "task-A" in msg or "task-B" in msg

    def test_indirect_cycle_detected(self):
        """A → B → C → A: cycle in the transitive graph."""
        client = _mock_client_for_cycle({
            "task-A": ["task-B"],
            "task-B": ["task-C"],
            "task-C": ["task-A"],
        })
        svc = TaskService(supabase_client=client)
        # new task X blocked by A; the chain A→B→C→A is a pre-existing cycle among blockers
        has_cycle, msg = svc._detect_circular_dependency(["task-A", "task-B"])
        assert has_cycle is True

    def test_new_task_id_in_blocker_chain_detected(self):
        """Update case: task-X is being set to blocked_by=[task-A] but task-A is already
        blocked by task-X — direct cycle."""
        client = _mock_client_for_cycle({"task-A": ["task-X"]})
        svc = TaskService(supabase_client=client)
        has_cycle, msg = svc._detect_circular_dependency(["task-A"], new_task_id="task-X")
        assert has_cycle is True
        assert "task-X" in msg

    def test_no_cycle_with_existing_task_id(self):
        """Update case: task-X blocked by task-A, task-A has no blocked_by — no cycle."""
        client = _mock_client_for_cycle({"task-A": []})
        svc = TaskService(supabase_client=client)
        has_cycle, msg = svc._detect_circular_dependency(["task-A"], new_task_id="task-X")
        assert has_cycle is False

    def test_db_error_skips_gracefully(self):
        """DB errors during cycle detection are skipped — no false positives."""
        client = MagicMock()
        client.table.return_value.select.return_value.eq.side_effect = Exception("DB error")
        svc = TaskService(supabase_client=client)
        has_cycle, msg = svc._detect_circular_dependency(["task-A"])
        assert has_cycle is False


class TestCreateTaskBlockedByValidation:
    @pytest.mark.asyncio
    async def test_create_task_rejects_circular_blocked_by(self):
        """create_task should fail when blocked_by would create a cycle among existing tasks."""
        # blocker-A is blocked by blocker-B; blocker-B is blocked by blocker-A → pre-existing cycle
        client = _mock_client_for_cycle({"blocker-A": ["blocker-B"], "blocker-B": ["blocker-A"]})
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-1",
            title="New task",
            blocked_by=["blocker-A", "blocker-B"],
        )
        assert success is False
        assert "Circular dependency" in result["error"]

    @pytest.mark.asyncio
    async def test_create_task_accepts_valid_blocked_by(self):
        """create_task should succeed when blocked_by forms no cycle."""
        # The cycle detection fetches the blocker's own blocked_by via select().eq()
        # Since we need a full insert mock too, use _mock_client here.
        # For cycle detection: blocker-1 has empty blocked_by
        client = MagicMock()

        # Cycle check: select("id, blocked_by").eq("id", "blocker-1") → no blocked_by
        blocker_select = MagicMock()
        blocker_select.execute.return_value = MagicMock(
            data=[{"id": "blocker-1", "blocked_by": []}]
        )
        blocker_eq = MagicMock(return_value=blocker_select)
        cycle_select = MagicMock()
        cycle_select.eq.side_effect = blocker_eq

        # Reorder check: select("id, task_order").eq("project_id", ...).eq("status", ...).gte(...)
        reorder_select = MagicMock()
        reorder_select.eq.return_value = reorder_select
        reorder_select.gte.return_value = reorder_select
        reorder_select.execute.return_value = MagicMock(data=[])

        # Insert
        insert_result = MagicMock()
        insert_result.data = [_make_task(id="new-t", blocked_by=["blocker-1"])]
        insert_mock = MagicMock()
        insert_mock.execute.return_value = insert_result

        def _select_side_effect(fields: str):
            if "blocked_by" in fields:
                return cycle_select
            return reorder_select

        table = MagicMock()
        table.select.side_effect = _select_side_effect
        table.insert.return_value = insert_mock

        client.table.return_value = table
        svc = TaskService(supabase_client=client)

        success, result = await svc.create_task(
            project_id="proj-1",
            title="New task",
            blocked_by=["blocker-1"],
        )
        assert success is True
        assert result["task"]["blocked_by"] == ["blocker-1"]


# ---------------------------------------------------------------------------
# TestUpdateTaskBlockedByValidation — cycle and self-reference on update
# ---------------------------------------------------------------------------


class TestUpdateTaskBlockedByValidation:
    @pytest.mark.asyncio
    async def test_update_task_rejects_self_reference(self):
        """update_task should fail when blocked_by contains the task's own ID."""
        svc = TaskService(supabase_client=MagicMock())
        success, result = await svc.update_task("task-X", {"blocked_by": ["task-X"]})
        assert success is False
        assert "cannot block itself" in result["error"]

    @pytest.mark.asyncio
    async def test_update_task_rejects_circular_blocked_by(self):
        """update_task should fail when blocked_by would create a cycle.

        Scenario: task-X is being updated to blocked_by=[task-A],
        but task-A is already blocked by task-X — direct cycle.
        """
        client = _mock_client_for_cycle({"task-A": ["task-X"]})
        svc = TaskService(supabase_client=client)
        success, result = await svc.update_task("task-X", {"blocked_by": ["task-A"]})
        assert success is False
        assert "Circular dependency" in result["error"]

    @pytest.mark.asyncio
    async def test_update_task_accepts_valid_blocked_by(self):
        """update_task should succeed when blocked_by introduces no cycle."""
        client = MagicMock()

        # Cycle check: select("id, blocked_by").eq("id", "task-A") → no blocked_by
        blocker_select = MagicMock()
        blocker_select.execute.return_value = MagicMock(
            data=[{"id": "task-A", "blocked_by": []}]
        )
        blocker_eq = MagicMock(return_value=blocker_select)
        cycle_select = MagicMock()
        cycle_select.eq.side_effect = blocker_eq

        # Update response
        update_result = MagicMock()
        update_result.data = [_make_task(id="task-X", blocked_by=["task-A"])]
        update_mock = MagicMock()
        update_mock.execute.return_value = update_result

        def _select_side_effect(fields: str):
            if "blocked_by" in fields:
                return cycle_select
            return MagicMock()

        table = MagicMock()
        table.select.side_effect = _select_side_effect
        table.update.return_value.eq.return_value = update_result
        # Make update().eq().execute() work
        table.update.return_value.eq.return_value.execute.return_value = update_result
        client.table.return_value = table

        svc = TaskService(supabase_client=client)
        success, result = await svc.update_task("task-X", {"blocked_by": ["task-A"]})
        assert success is True
        assert result["task"]["blocked_by"] == ["task-A"]

    @pytest.mark.asyncio
    async def test_update_task_clears_blocked_by(self):
        """update_task should allow clearing blocked_by to empty list."""
        client = MagicMock()

        update_result = MagicMock()
        update_result.data = [_make_task(id="task-X", blocked_by=[])]
        client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            update_result
        )

        svc = TaskService(supabase_client=client)
        success, result = await svc.update_task("task-X", {"blocked_by": []})
        assert success is True
        assert result["task"]["blocked_by"] == []
