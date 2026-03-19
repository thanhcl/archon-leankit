"""Tests for task generator service."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.server.services.projects.task_generator_service import (
    _parse_llm_response,
    _validate_generated_task,
    generate_tasks_from_description,
)


class TestParseResponse:
    """Tests for _parse_llm_response."""

    def test_plain_json(self):
        raw = '[{"title": "Task 1"}]'
        result = _parse_llm_response(raw)
        assert result == [{"title": "Task 1"}]

    def test_markdown_fenced_json(self):
        raw = '```json\n[{"title": "Task 1"}]\n```'
        result = _parse_llm_response(raw)
        assert result == [{"title": "Task 1"}]

    def test_markdown_fenced_no_language(self):
        raw = '```\n[{"title": "Task 1"}]\n```'
        result = _parse_llm_response(raw)
        assert result == [{"title": "Task 1"}]

    def test_not_array_raises(self):
        raw = '{"title": "Task 1"}'
        with pytest.raises(ValueError, match="not a JSON array"):
            _parse_llm_response(raw)

    def test_invalid_json_raises(self):
        raw = "not json at all"
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response(raw)


class TestValidateGeneratedTask:
    """Tests for _validate_generated_task."""

    def test_minimal_valid_task(self):
        task = {"title": "Implement API"}
        result = _validate_generated_task(task)
        assert result["title"] == "Implement API"
        assert result["complexity"] == "simple"
        assert result["priority"] == "medium"
        assert result["task_order"] == 0

    def test_full_task(self):
        task = {
            "title": "Add auth middleware",
            "description": "Create JWT middleware",
            "feature": "auth",
            "complexity": "complex",
            "priority": "high",
            "task_order": 50,
            "acceptance_criteria": [
                {"description": "JWT tokens validated", "completed": False}
            ],
            "dependencies": ["Setup database"],
        }
        result = _validate_generated_task(task)
        assert result["title"] == "Add auth middleware"
        assert result["complexity"] == "complex"
        assert result["priority"] == "high"
        assert result["task_order"] == 50
        assert len(result["acceptance_criteria"]) == 1
        assert result["dependencies"] == ["Setup database"]

    def test_invalid_complexity_defaults(self):
        task = {"title": "Test", "complexity": "invalid"}
        result = _validate_generated_task(task)
        assert result["complexity"] == "simple"

    def test_invalid_priority_defaults(self):
        task = {"title": "Test", "priority": "invalid"}
        result = _validate_generated_task(task)
        assert result["priority"] == "medium"

    def test_negative_order_defaults_to_zero(self):
        task = {"title": "Test", "task_order": -5}
        result = _validate_generated_task(task)
        assert result["task_order"] == 0

    def test_string_acceptance_criteria_normalized(self):
        task = {
            "title": "Test",
            "acceptance_criteria": ["Criterion 1", "Criterion 2"],
        }
        result = _validate_generated_task(task)
        assert len(result["acceptance_criteria"]) == 2
        assert result["acceptance_criteria"][0] == {
            "description": "Criterion 1",
            "completed": False,
        }

    def test_missing_title_raises(self):
        with pytest.raises(ValueError, match="non-empty title"):
            _validate_generated_task({"description": "no title"})

    def test_empty_title_raises(self):
        with pytest.raises(ValueError, match="non-empty title"):
            _validate_generated_task({"title": ""})

    def test_not_dict_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            _validate_generated_task("not a dict")

    def test_title_truncated(self):
        task = {"title": "A" * 300}
        result = _validate_generated_task(task)
        assert len(result["title"]) == 200


class TestGenerateTasksFromDescription:
    """Tests for generate_tasks_from_description."""

    @pytest.mark.asyncio
    async def test_short_description_rejected(self):
        success, result = await generate_tasks_from_description("short")
        assert success is False
        assert "at least 10 characters" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_description_rejected(self):
        success, result = await generate_tasks_from_description("")
        assert success is False
        assert "at least 10 characters" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_generation(self):
        llm_response = json.dumps([
            {
                "title": "Create database schema",
                "description": "Design and implement the DB schema",
                "feature": "backend",
                "complexity": "complex",
                "priority": "high",
                "task_order": 100,
                "acceptance_criteria": [
                    {"description": "Schema created", "completed": False}
                ],
                "dependencies": [],
            },
            {
                "title": "Implement API endpoints",
                "description": "Build REST endpoints",
                "feature": "backend",
                "complexity": "complex",
                "priority": "high",
                "task_order": 90,
                "acceptance_criteria": [
                    {"description": "Endpoints work", "completed": False}
                ],
                "dependencies": ["Create database schema"],
            },
        ])

        mock_message = MagicMock()
        mock_message.content = llm_response
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        # Mock both get_llm_client and credential_service
        with (
            patch(
                "src.server.services.projects.task_generator_service.get_llm_client"
            ) as mock_get_client,
            patch(
                "src.server.services.projects.task_generator_service.credential_service"
            ) as mock_creds,
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_creds.get_active_provider = AsyncMock(
                return_value={"provider": "openai", "chat_model": "gpt-4o-mini"}
            )

            success, result = await generate_tasks_from_description(
                "Build a user authentication system with JWT tokens and role-based access",
                project_id="proj-123",
            )

        assert success is True
        assert result["count"] == 2
        assert result["project_id"] == "proj-123"
        assert len(result["tasks"]) == 2
        assert result["tasks"][0]["title"] == "Create database schema"
        assert result["tasks"][0]["project_id"] == "proj-123"

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_json(self):
        mock_message = MagicMock()
        mock_message.content = "This is not valid JSON"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with (
            patch(
                "src.server.services.projects.task_generator_service.get_llm_client"
            ) as mock_get_client,
            patch(
                "src.server.services.projects.task_generator_service.credential_service"
            ) as mock_creds,
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_creds.get_active_provider = AsyncMock(
                return_value={"provider": "openai", "chat_model": "gpt-4o-mini"}
            )

            success, result = await generate_tasks_from_description(
                "Build a feature with multiple components"
            )

        assert success is False
        assert "invalid JSON" in result["error"]

    @pytest.mark.asyncio
    async def test_llm_returns_empty_response(self):
        mock_message = MagicMock()
        mock_message.content = ""
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with (
            patch(
                "src.server.services.projects.task_generator_service.get_llm_client"
            ) as mock_get_client,
            patch(
                "src.server.services.projects.task_generator_service.credential_service"
            ) as mock_creds,
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_creds.get_active_provider = AsyncMock(
                return_value={"provider": "openai", "chat_model": "gpt-4o-mini"}
            )

            success, result = await generate_tasks_from_description(
                "Build something interesting with a lot of detail"
            )

        assert success is False
        assert "empty response" in result["error"]

    @pytest.mark.asyncio
    async def test_partial_validation_failures(self):
        """Tasks with validation errors are skipped, valid ones kept."""
        llm_response = json.dumps([
            {"title": "Valid task", "complexity": "simple"},
            {"description": "Missing title"},  # Invalid - no title
            {"title": "Another valid task"},
        ])

        mock_message = MagicMock()
        mock_message.content = llm_response
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with (
            patch(
                "src.server.services.projects.task_generator_service.get_llm_client"
            ) as mock_get_client,
            patch(
                "src.server.services.projects.task_generator_service.credential_service"
            ) as mock_creds,
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_creds.get_active_provider = AsyncMock(
                return_value={"provider": "openai", "chat_model": "gpt-4o-mini"}
            )

            success, result = await generate_tasks_from_description(
                "Build a feature that requires multiple tasks for implementation"
            )

        assert success is True
        assert result["count"] == 2
        assert len(result["validation_warnings"]) == 1

    @pytest.mark.asyncio
    async def test_no_project_id(self):
        """Tasks generated without project_id don't include it."""
        llm_response = json.dumps([
            {"title": "Task without project"},
        ])

        mock_message = MagicMock()
        mock_message.content = llm_response
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with (
            patch(
                "src.server.services.projects.task_generator_service.get_llm_client"
            ) as mock_get_client,
            patch(
                "src.server.services.projects.task_generator_service.credential_service"
            ) as mock_creds,
        ):
            mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_creds.get_active_provider = AsyncMock(
                return_value={"provider": "openai", "chat_model": "gpt-4o-mini"}
            )

            success, result = await generate_tasks_from_description(
                "Build a simple feature without project context"
            )

        assert success is True
        assert "project_id" not in result
        assert "project_id" not in result["tasks"][0]
