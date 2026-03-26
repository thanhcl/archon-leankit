"""Tests for typed API contracts with runtime validation."""

import pytest
from pydantic import ValidationError

from src.server.models.api_contracts import (
    CreateExecutionRunRequest,
    CreateTaskRequest,
    ExecutionRunResponse,
    ExecutionRunStage,
    ExecutionRunStatus,
    ProjectResponse,
    RuleResponse,
    RuleSource,
    SprintDay,
    SprintStatsResponse,
    SprintSummary,
    SprintTrends,
    TaskComplexity,
    TaskListResponse,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    UpdateTaskRequest,
    validate_response,
    validate_response_safe,
)


class TestTaskStatusEnum:
    def test_all_15_statuses_exist(self):
        assert len(TaskStatus) == 15

    def test_valid_status_values(self):
        expected = {
            "draft", "proposed", "approved", "planning", "owner-qa",
            "assigned", "executing", "architect-review", "code-review", "review",
            "done", "failed", "escalated", "on-hold", "cancelled",
        }
        assert {s.value for s in TaskStatus} == expected

    def test_enum_string_comparison(self):
        assert TaskStatus.DRAFT == "draft"
        assert TaskStatus.ARCHITECT_REVIEW == "architect-review"
        assert TaskStatus.CODE_REVIEW == "code-review"


class TestTaskPriorityEnum:
    def test_all_4_priorities(self):
        assert len(TaskPriority) == 4
        assert {p.value for p in TaskPriority} == {"low", "medium", "high", "critical"}


class TestTaskComplexityEnum:
    def test_values(self):
        assert TaskComplexity.SIMPLE == "simple"
        assert TaskComplexity.COMPLEX == "complex"


class TestExecutionRunEnums:
    def test_execution_run_status_values(self):
        assert {s.value for s in ExecutionRunStatus} == {
            "queued", "running", "reviewing", "completed", "failed", "cancelled"
        }

    def test_execution_run_stage_values(self):
        assert {s.value for s in ExecutionRunStage} == {
            "execute", "architect-review", "code-review", "retry"
        }


class TestRuleSourceEnum:
    def test_values(self):
        assert RuleSource.MANUAL == "manual"
        assert RuleSource.AUTO == "auto"
        assert RuleSource.SYSTEM == "system"
        assert len(RuleSource) == 3


class TestTaskResponse:
    @pytest.fixture
    def valid_task_data(self):
        return {
            "id": "task-001",
            "project_id": "proj-001",
            "title": "Test task",
            "description": "A test task",
            "status": "draft",
            "assignee": "User",
            "task_order": 10,
            "priority": "high",
            "created_at": "2026-03-18T10:00:00Z",
            "updated_at": "2026-03-18T10:00:00Z",
        }

    def test_valid_task(self, valid_task_data):
        task = TaskResponse(**valid_task_data)
        assert task.id == "task-001"
        assert task.status == "draft"
        assert task.priority == "high"

    def test_defaults_applied(self, valid_task_data):
        task = TaskResponse(**valid_task_data)
        assert task.complexity == "simple"
        assert task.retry_count == 0
        assert task.max_retries == 3
        assert task.archived is False

    def test_invalid_status_rejected(self, valid_task_data):
        valid_task_data["status"] = "invalid-status"
        with pytest.raises(ValidationError):
            TaskResponse(**valid_task_data)

    def test_invalid_priority_rejected(self, valid_task_data):
        valid_task_data["priority"] = "ultra"
        with pytest.raises(ValidationError):
            TaskResponse(**valid_task_data)

    def test_all_statuses_accepted(self, valid_task_data):
        for status in TaskStatus:
            valid_task_data["status"] = status.value
            task = TaskResponse(**valid_task_data)
            assert task.status == status.value

    def test_optional_lifecycle_fields(self, valid_task_data):
        valid_task_data.update({
            "owner": "alice",
            "source_app": "virtual-office",
            "complexity": "complex",
            "acceptance_criteria": [{"text": "Must pass"}],
            "execution_prompt": "Do the thing",
            "max_retries": 5,
        })
        task = TaskResponse(**valid_task_data)
        assert task.owner == "alice"
        assert task.complexity == "complex"

    def test_missing_required_field_rejected(self):
        with pytest.raises(ValidationError):
            TaskResponse(id="t1", title="test")  # missing project_id, status, created_at, updated_at

    def test_model_validates_from_dict(self, valid_task_data):
        task = TaskResponse.model_validate(valid_task_data)
        assert task.title == "Test task"


class TestTaskListResponse:
    def test_valid_list(self):
        data = {
            "tasks": [
                {
                    "id": "t1",
                    "project_id": "p1",
                    "title": "Task 1",
                    "status": "draft",
                    "created_at": "2026-03-18T10:00:00Z",
                    "updated_at": "2026-03-18T10:00:00Z",
                }
            ],
            "total_count": 1,
        }
        response = TaskListResponse.model_validate(data)
        assert len(response.tasks) == 1
        assert response.total_count == 1

    def test_nested_invalid_task_rejected(self):
        data = {
            "tasks": [{"id": "t1", "status": "bad-status"}],
            "total_count": 1,
        }
        with pytest.raises(ValidationError):
            TaskListResponse.model_validate(data)

    def test_empty_list(self):
        data = {"tasks": [], "total_count": 0}
        response = TaskListResponse.model_validate(data)
        assert response.tasks == []


class TestExecutionRunResponse:
    def test_valid_execution_run(self):
        run = ExecutionRunResponse(
            id="run-001",
            task_id="task-001",
            project_id="proj-001",
            status="queued",
            stage="execute",
            started_at="2026-03-20T10:00:00Z",
        )
        assert run.status == "queued"
        assert run.stage == "execute"

    def test_invalid_execution_run_status_rejected(self):
        with pytest.raises(ValidationError):
            ExecutionRunResponse(
                id="run-001",
                task_id="task-001",
                project_id="proj-001",
                status="bad",
                stage="execute",
                started_at="2026-03-20T10:00:00Z",
            )


class TestCreateExecutionRunRequest:
    def test_defaults(self):
        req = CreateExecutionRunRequest(task_id="task-001", project_id="proj-001")
        assert req.status == "queued"
        assert req.stage == "execute"


class TestProjectResponse:
    def test_valid_project(self):
        data = {
            "id": "proj-001",
            "title": "Test Project",
            "created_at": "2026-03-18T10:00:00Z",
            "updated_at": "2026-03-18T10:00:00Z",
        }
        project = ProjectResponse.model_validate(data)
        assert project.title == "Test Project"
        assert project.pinned is False

    def test_virtual_office_fields(self):
        data = {
            "id": "proj-001",
            "title": "VO Project",
            "created_at": "2026-03-18T10:00:00Z",
            "updated_at": "2026-03-18T10:00:00Z",
            "source_app": "virtual-office",
            "layout_id": "layout-001",
            "team_config": [{"role": "dev", "agent_id": "a1"}],
            "director_config": {"model": "claude-opus-4-6"},
            "team_lead_config": {"auto_assign": True},
            "office_settings": {"theme": "tron"},
        }
        project = ProjectResponse.model_validate(data)
        assert project.source_app == "virtual-office"
        assert project.layout_id == "layout-001"


class TestRuleResponse:
    def test_valid_rule(self):
        data = {
            "id": "rule-001",
            "section": "security",
            "rule_text": "Never log secrets",
            "priority": 90,
            "source": "manual",
            "enabled": True,
            "created_at": "2026-03-18T10:00:00Z",
        }
        rule = RuleResponse.model_validate(data)
        assert rule.source == "manual"

    def test_all_source_types(self):
        for source in RuleSource:
            data = {
                "id": "r1", "section": "test", "rule_text": "rule",
                "source": source.value, "created_at": "2026-03-18T10:00:00Z",
            }
            rule = RuleResponse.model_validate(data)
            assert rule.source == source.value


class TestCreateTaskRequest:
    def test_defaults(self):
        req = CreateTaskRequest(project_id="p1", title="Test")
        assert req.status == "draft"
        assert req.priority == "medium"
        assert req.complexity == "simple"
        assert req.assignee == "User"
        assert req.max_retries == 3

    def test_override_defaults(self):
        req = CreateTaskRequest(
            project_id="p1", title="Test",
            status="approved", priority="critical",
            complexity="complex",
        )
        assert req.status == "approved"
        assert req.priority == "critical"


class TestUpdateTaskRequest:
    def test_all_optional(self):
        req = UpdateTaskRequest()
        assert req.title is None
        assert req.status is None

    def test_partial_update(self):
        req = UpdateTaskRequest(status="review", priority="high")
        assert req.status == "review"
        assert req.title is None


class TestValidationHelpers:
    def test_validate_response_valid(self):
        data = {
            "id": "t1", "project_id": "p1", "title": "Test",
            "status": "draft",
            "created_at": "2026-03-18T10:00:00Z",
            "updated_at": "2026-03-18T10:00:00Z",
        }
        result = validate_response(data, TaskResponse)
        assert result.id == "t1"

    def test_validate_response_invalid_raises(self):
        data = {"id": "t1", "status": "invalid"}
        with pytest.raises(ValidationError):
            validate_response(data, TaskResponse)

    def test_validate_response_safe_valid(self):
        data = {
            "id": "t1", "project_id": "p1", "title": "Test",
            "status": "draft",
            "created_at": "2026-03-18T10:00:00Z",
            "updated_at": "2026-03-18T10:00:00Z",
        }
        ok, result = validate_response_safe(data, TaskResponse)
        assert ok is True
        assert result.id == "t1"

    def test_validate_response_safe_invalid(self):
        data = {"id": "t1", "status": "bad"}
        ok, result = validate_response_safe(data, TaskResponse)
        assert ok is False
        assert "validation_error" in result
        assert result["model"] == "TaskResponse"


class TestSprintStatsContracts:
    def test_sprint_summary(self):
        summary = SprintSummary(total_tasks=100, done=50, first_pass_rate=0.8)
        assert summary.total_tasks == 100
        assert summary.avg_retries == 0.0  # default

    def test_sprint_day(self):
        day = SprintDay(date="2026-03-18", tasks_completed=5)
        assert day.date == "2026-03-18"
        assert day.first_pass_rate == 0.0  # default

    def test_sprint_trends_defaults(self):
        trends = SprintTrends()
        assert trends.available is False
        assert trends.tasks_completed_delta == 0

    def test_sprint_stats_response(self):
        data = {
            "project_id": "p1",
            "summary": {"total_tasks": 10, "done": 5},
            "sprints": [{"date": "2026-03-18", "tasks_completed": 5}],
            "trends": {"available": True},
        }
        stats = SprintStatsResponse.model_validate(data)
        assert stats.project_id == "p1"
        assert len(stats.sprints) == 1
        assert stats.trends.available is True
        assert stats.top_learnings == []  # default
        assert stats.code_patterns_count == 0  # default
