"""Tests for ExecutionRunService."""

from unittest.mock import MagicMock

import pytest

from src.server.services.projects.execution_run_service import ExecutionRunService


def _mock_client(select_data=None, insert_data=None, update_data=None):
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    select = MagicMock()
    select.eq.return_value = select
    select.order.return_value = select
    select.limit.return_value = select
    select_execute = MagicMock()
    select_execute.data = select_data if select_data is not None else []
    select.execute.return_value = select_execute
    table.select.return_value = select

    insert = MagicMock()
    insert_execute = MagicMock()
    insert_execute.data = insert_data if insert_data is not None else []
    insert.execute.return_value = insert_execute
    table.insert.return_value = insert

    update = MagicMock()
    update.eq.return_value = update
    update_execute = MagicMock()
    update_execute.data = update_data if update_data is not None else []
    update.execute.return_value = update_execute
    table.update.return_value = update

    return client


def _make_run(**overrides):
    base = {
        "id": "run-001",
        "task_id": "task-001",
        "project_id": "proj-001",
        "status": "queued",
        "stage": "execute",
        "retry_index": 0,
        "started_at": "2026-03-20T00:00:00Z",
        "metadata": {},
    }
    base.update(overrides)
    return base


class TestCreateRun:
    @pytest.mark.asyncio
    async def test_creates_run(self):
        client = _mock_client(insert_data=[_make_run()])
        service = ExecutionRunService(supabase_client=client)

        ok, result = await service.create_run(
            task_id="task-001",
            project_id="proj-001",
            status="queued",
            stage="execute",
        )

        assert ok is True
        assert result["run"]["id"] == "run-001"

    @pytest.mark.asyncio
    async def test_rejects_invalid_status(self):
        service = ExecutionRunService(supabase_client=_mock_client())
        ok, result = await service.create_run(
            task_id="task-001",
            project_id="proj-001",
            status="bogus",
        )
        assert ok is False
        assert "Invalid execution run status" in result["error"]

    @pytest.mark.asyncio
    async def test_creates_run_with_metadata_heartbeat_fallback_when_column_missing(self):
        client = _mock_client(insert_data=[_make_run(metadata={"heartbeat_at": "2026-03-20T00:00:00Z"})])
        insert = client.table.return_value.insert
        first_response = MagicMock()
        first_response.execute.side_effect = Exception(
            "{'code': 'PGRST204', 'message': \"Could not find the 'heartbeat_at' column of 'archon_execution_runs' in the schema cache\"}"
        )
        second_response = MagicMock()
        second_execute = MagicMock()
        second_execute.data = [_make_run(metadata={"heartbeat_at": "2026-03-20T00:00:00Z"})]
        second_response.execute.return_value = second_execute
        insert.side_effect = [first_response, second_response]

        service = ExecutionRunService(supabase_client=client)
        ok, result = await service.create_run(
            task_id="task-001",
            project_id="proj-001",
            started_at="2026-03-20T00:00:00Z",
            heartbeat_at="2026-03-20T00:00:00Z",
        )

        assert ok is True
        assert result["run"]["heartbeat_at"] == "2026-03-20T00:00:00Z"


class TestListRuns:
    def test_lists_runs(self):
        client = _mock_client(select_data=[_make_run(status="running")])
        service = ExecutionRunService(supabase_client=client)

        ok, result = service.list_runs(task_id="task-001")

        assert ok is True
        assert result["total_count"] == 1
        assert result["runs"][0]["status"] == "running"

    def test_filters_runs_by_bootstrap_plan_id_from_metadata(self):
        client = _mock_client(select_data=[
            _make_run(
                id="run-001",
                metadata={"bootstrap_plan_id": "plan-001"},
            ),
            _make_run(
                id="run-002",
                metadata={"bootstrap_plan_id": "plan-002"},
            ),
            _make_run(
                id="run-003",
                bootstrap_plan_id="plan-001",
                metadata={},
            ),
        ])
        service = ExecutionRunService(supabase_client=client)

        ok, result = service.list_runs(bootstrap_plan_id="plan-001")

        assert ok is True
        assert result["total_count"] == 2
        assert [run["id"] for run in result["runs"]] == ["run-001", "run-003"]
        assert "bootstrap_plan_id=plan-001" in result["filters_applied"]

    def test_rejects_invalid_stage_filter(self):
        service = ExecutionRunService(supabase_client=_mock_client())
        ok, result = service.list_runs(stage="bogus")
        assert ok is False
        assert "Invalid execution run stage" in result["error"]


class TestUpdateRun:
    @pytest.mark.asyncio
    async def test_updates_run(self):
        client = _mock_client(update_data=[_make_run(status="completed", finished_at="2026-03-20T01:00:00Z")])
        service = ExecutionRunService(supabase_client=client)

        ok, result = await service.update_run("run-001", {"status": "completed"})

        assert ok is True
        assert result["run"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_rejects_empty_update(self):
        service = ExecutionRunService(supabase_client=_mock_client())
        ok, result = await service.update_run("run-001", {})
        assert ok is False
        assert "No update fields provided" in result["error"]

    @pytest.mark.asyncio
    async def test_stores_llm_metrics_on_update(self):
        """Updating with total_tokens, thinking_tokens, cost_usd passes through to DB."""
        updated_run = _make_run(
            status="completed",
            total_tokens=15000,
            thinking_tokens=1200,
            cost_usd=0.0450,
        )
        client = _mock_client(update_data=[updated_run])
        service = ExecutionRunService(supabase_client=client)

        ok, result = await service.update_run("run-001", {
            "status": "completed",
            "total_tokens": 15000,
            "thinking_tokens": 1200,
            "cost_usd": 0.0450,
        })

        assert ok is True
        # Verify the update payload was passed to the DB (check via table().update() call)
        update_payload = client.table.return_value.update.call_args[0][0]
        assert update_payload["total_tokens"] == 15000
        assert update_payload["thinking_tokens"] == 1200
        assert update_payload["cost_usd"] == 0.0450

    @pytest.mark.asyncio
    async def test_updates_run_heartbeat(self):
        updated_run = _make_run(
            status="running",
            heartbeat_at="2026-03-20T00:15:00Z",
        )
        client = _mock_client(update_data=[updated_run])
        service = ExecutionRunService(supabase_client=client)

        ok, result = await service.update_run("run-001", {"heartbeat_at": "2026-03-20T00:15:00Z"})

        assert ok is True
        assert result["run"]["heartbeat_at"] == "2026-03-20T00:15:00Z"
        update_payload = client.table.return_value.update.call_args[0][0]
        assert update_payload["heartbeat_at"] == "2026-03-20T00:15:00Z"


class TestCreateRunWithLLMMetrics:
    @pytest.mark.asyncio
    async def test_creates_run_with_llm_metrics(self):
        """Creating a run with total_tokens and thinking_tokens stores them."""
        run_data = _make_run(
            total_tokens=12000,
            thinking_tokens=500,
            token_input=8000,
            token_output=4000,
            cost_usd=0.0320,
        )
        client = _mock_client(insert_data=[run_data])
        service = ExecutionRunService(supabase_client=client)

        ok, result = await service.create_run(
            task_id="task-001",
            project_id="proj-001",
            total_tokens=12000,
            thinking_tokens=500,
            token_input=8000,
            token_output=4000,
            cost_usd=0.0320,
        )

        assert ok is True
        # Verify new fields were included in the insert payload
        insert_payload = client.table.return_value.insert.call_args[0][0]
        assert insert_payload["total_tokens"] == 12000
        assert insert_payload["thinking_tokens"] == 500
        assert insert_payload["token_input"] == 8000
        assert insert_payload["token_output"] == 4000
        assert insert_payload["cost_usd"] == 0.0320

    @pytest.mark.asyncio
    async def test_creates_run_without_llm_metrics_omits_fields(self):
        """Creating a run without LLM metrics does not include None fields."""
        client = _mock_client(insert_data=[_make_run()])
        service = ExecutionRunService(supabase_client=client)

        await service.create_run(task_id="task-001", project_id="proj-001")

        insert_payload = client.table.return_value.insert.call_args[0][0]
        assert "total_tokens" not in insert_payload
        assert "thinking_tokens" not in insert_payload

    @pytest.mark.asyncio
    async def test_creates_run_with_heartbeat_defaults_to_started_at(self):
        client = _mock_client(insert_data=[_make_run(heartbeat_at="2026-03-20T00:00:00Z")])
        service = ExecutionRunService(supabase_client=client)

        await service.create_run(task_id="task-001", project_id="proj-001", started_at="2026-03-20T00:00:00Z")

        insert_payload = client.table.return_value.insert.call_args[0][0]
        assert insert_payload["started_at"] == "2026-03-20T00:00:00Z"
        assert insert_payload["heartbeat_at"] == "2026-03-20T00:00:00Z"
