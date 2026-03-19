"""Tests for TaskService extended fields: task_type, phase, module, sprint, tags, subtasks."""

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
