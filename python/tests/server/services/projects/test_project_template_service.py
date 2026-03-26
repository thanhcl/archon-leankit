"""Tests for project template service."""

from __future__ import annotations

import pytest

from src.server.services.projects.project_template_service import (
    ProjectTemplate,
    ProjectTemplateService,
    project_template_service,
)


class TestProjectTemplateServiceListTemplates:
    def test_returns_at_least_two_templates(self):
        templates = project_template_service.list_templates()
        assert len(templates) >= 2

    def test_includes_backend_python_template(self):
        templates = project_template_service.list_templates()
        ids = [t.id for t in templates]
        assert "backend-python" in ids

    def test_includes_frontend_nextjs_template(self):
        templates = project_template_service.list_templates()
        ids = [t.id for t in templates]
        assert "frontend-nextjs" in ids

    def test_templates_have_required_fields(self):
        templates = project_template_service.list_templates()
        for template in templates:
            assert template.id
            assert template.name
            assert template.description
            assert template.project_type
            assert template.bootstrap_policy in ("standard", "rapid", "strict")
            assert isinstance(template.task_pack, (list, tuple))


class TestProjectTemplateServiceGetTemplate:
    def test_returns_backend_python(self):
        template = project_template_service.get_template("backend-python")
        assert template is not None
        assert template.id == "backend-python"
        assert template.project_type == "api-service"

    def test_returns_frontend_nextjs(self):
        template = project_template_service.get_template("frontend-nextjs")
        assert template is not None
        assert template.id == "frontend-nextjs"
        assert template.project_type == "web-app"

    def test_returns_none_for_unknown_template(self):
        template = project_template_service.get_template("does-not-exist")
        assert template is None


class TestProjectTemplateServiceTaskPack:
    def test_backend_python_has_task_pack(self):
        tasks = project_template_service.get_task_pack("backend-python")
        assert len(tasks) > 0

    def test_frontend_nextjs_has_task_pack(self):
        tasks = project_template_service.get_task_pack("frontend-nextjs")
        assert len(tasks) > 0

    def test_unknown_template_returns_empty_list(self):
        tasks = project_template_service.get_task_pack("unknown")
        assert tasks == []

    def test_task_pack_items_have_required_fields(self):
        for template_id in ("backend-python", "frontend-nextjs"):
            tasks = project_template_service.get_task_pack(template_id)
            for task in tasks:
                assert task.key
                assert task.title
                assert task.description
                assert task.execution_prompt
                assert len(task.acceptance_criteria) > 0

    def test_task_pack_dependency_keys_exist(self):
        """Tasks that reference blocked_on_key must point to a valid key in the same pack (or a bootstrap key)."""
        bootstrap_keys = {"scaffold", "validation", "architecture", "followup"}
        for template_id in ("backend-python", "frontend-nextjs"):
            tasks = project_template_service.get_task_pack(template_id)
            task_keys = {t.key for t in tasks} | bootstrap_keys
            for task in tasks:
                if task.blocked_on_key:
                    assert task.blocked_on_key in task_keys, (
                        f"Template {template_id}: task '{task.key}' has blocked_on_key "
                        f"'{task.blocked_on_key}' which is not a valid key"
                    )


class TestProjectTemplateServiceApplyToCreateKwargs:
    def test_applies_project_type_when_not_set(self):
        kwargs = project_template_service.apply_to_create_kwargs("backend-python", {})
        assert kwargs["project_type"] == "api-service"

    def test_applies_bootstrap_policy_when_not_set(self):
        kwargs = project_template_service.apply_to_create_kwargs("frontend-nextjs", {})
        assert kwargs["bootstrap_policy"] == "standard"

    def test_does_not_override_explicit_project_type(self):
        kwargs = project_template_service.apply_to_create_kwargs(
            "backend-python", {"project_type": "web-app"}
        )
        assert kwargs["project_type"] == "web-app"

    def test_does_not_override_explicit_bootstrap_policy(self):
        kwargs = project_template_service.apply_to_create_kwargs(
            "backend-python", {"bootstrap_policy": "strict"}
        )
        assert kwargs["bootstrap_policy"] == "strict"

    def test_unknown_template_returns_kwargs_unchanged(self):
        original = {"project_type": "general-app", "bootstrap_policy": "rapid"}
        result = project_template_service.apply_to_create_kwargs("unknown", original)
        assert result == original

    def test_preserves_existing_keys(self):
        original = {"title": "My Project", "description": "desc"}
        result = project_template_service.apply_to_create_kwargs("backend-python", original)
        assert result["title"] == "My Project"
        assert result["description"] == "desc"
