"""Tests for Rules API — CRUD and CLAUDE.md generation."""

from unittest.mock import MagicMock, patch

import pytest


# --- Service Unit Tests ---


class TestRuleService:
    """Unit tests for RuleService."""

    def _make_service(self, mock_client=None):
        """Create a RuleService with a mock Supabase client."""
        mc = mock_client or MagicMock()
        with patch("src.server.utils.get_supabase_client", return_value=mc):
            from src.server.services.rules.rule_service import RuleService

            return RuleService(supabase_client=mc), mc

    def test_create_rule_success(self):
        mock_client = MagicMock()
        rule_row = {
            "id": "rule-1",
            "section": "security",
            "rule_text": "Never log secrets",
            "priority": 20,
            "source": "manual",
            "enabled": True,
            "project_id": None,
        }
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [rule_row]

        service, _ = self._make_service(mock_client)
        ok, result = service.create_rule(section="security", rule_text="Never log secrets", priority=20)

        assert ok is True
        assert result["rule"]["id"] == "rule-1"
        assert result["rule"]["section"] == "security"

    def test_create_rule_missing_fields(self):
        service, _ = self._make_service()
        ok, result = service.create_rule(section="", rule_text="")
        assert ok is False
        assert "required" in result["error"]

    def test_get_rule_found(self):
        mock_client = MagicMock()
        rule_row = {"id": "rule-1", "section": "validation", "rule_text": "Test everything"}
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [rule_row]

        service, _ = self._make_service(mock_client)
        ok, result = service.get_rule("rule-1")
        assert ok is True
        assert result["rule"]["id"] == "rule-1"

    def test_get_rule_not_found(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        service, _ = self._make_service(mock_client)
        ok, result = service.get_rule("nonexistent")
        assert ok is False
        assert "not found" in result["error"]

    def test_update_rule_success(self):
        mock_client = MagicMock()
        updated_row = {"id": "rule-1", "section": "security", "rule_text": "Updated rule", "enabled": True}
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [updated_row]

        service, _ = self._make_service(mock_client)
        ok, result = service.update_rule("rule-1", {"rule_text": "Updated rule"})
        assert ok is True
        assert result["rule"]["rule_text"] == "Updated rule"

    def test_update_rule_not_found(self):
        mock_client = MagicMock()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

        service, _ = self._make_service(mock_client)
        ok, result = service.update_rule("nonexistent", {"enabled": False})
        assert ok is False
        assert "not found" in result["error"]

    def test_delete_rule_success(self):
        mock_client = MagicMock()
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = [
            {"id": "rule-1"}
        ]

        service, _ = self._make_service(mock_client)
        ok, result = service.delete_rule("rule-1")
        assert ok is True
        assert "deleted" in result["message"]

    def test_delete_rule_not_found(self):
        mock_client = MagicMock()
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = []

        service, _ = self._make_service(mock_client)
        ok, result = service.delete_rule("nonexistent")
        assert ok is False

    def test_list_rules_returns_rules(self):
        mock_client = MagicMock()
        rules = [
            {"id": "r1", "section": "security", "priority": 10, "enabled": True, "project_id": None},
            {"id": "r2", "section": "validation", "priority": 1, "enabled": True, "project_id": None},
        ]
        # Chain: table -> select -> eq -> is_ -> order -> order -> execute
        mock_query = MagicMock()
        mock_query.execute.return_value.data = rules
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_client.table.return_value.select.return_value = mock_query

        service, _ = self._make_service(mock_client)
        ok, result = service.list_rules()
        assert ok is True
        assert result["total_count"] == 2

    def test_generate_claude_md(self):
        mock_client = MagicMock()
        rules = [
            {
                "id": "r1",
                "section": "validation",
                "rule_text": "## Validation\nAlways validate.",
                "priority": 1,
                "enabled": True,
                "project_id": None,
            },
            {
                "id": "r2",
                "section": "security",
                "rule_text": "## Security\nNever log secrets.",
                "priority": 20,
                "enabled": True,
                "project_id": "proj-1",
            },
        ]
        mock_query = MagicMock()
        mock_query.execute.return_value.data = rules
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_client.table.return_value.select.return_value = mock_query

        service, _ = self._make_service(mock_client)
        ok, result = service.generate_claude_md("proj-1")
        assert ok is True
        assert "# CLAUDE.md" in result["markdown"]
        assert "Always validate" in result["markdown"]
        assert "Never log secrets" in result["markdown"]
        assert result["rule_count"] == 2
        assert "validation" in result["sections"]
        assert "security" in result["sections"]

    def test_generate_claude_md_empty(self):
        mock_client = MagicMock()
        mock_query = MagicMock()
        mock_query.execute.return_value.data = []
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_client.table.return_value.select.return_value = mock_query

        service, _ = self._make_service(mock_client)
        ok, result = service.generate_claude_md("proj-empty")
        assert ok is True
        assert result["rule_count"] == 0
        assert "No rules configured" in result["markdown"]


# --- API Integration Tests (self-contained, no full server import) ---


class TestRulesAPIEndpoints:
    """Integration tests for the Rules API endpoints using a minimal FastAPI app.

    Patches _get_service at the module level to avoid importing
    the full server dependency chain (openai, crawl4ai, etc.).
    """

    @pytest.fixture
    def mock_svc_and_client(self):
        """Create a minimal test client + mock service, bypassing heavy imports."""
        import importlib.util
        import sys
        import types

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Ensure parent packages exist in sys.modules without triggering __init__
        for pkg in ["src", "src.server", "src.server.api_routes"]:
            if pkg not in sys.modules:
                sys.modules[pkg] = types.ModuleType(pkg)

        # Load rules_api directly from file
        spec = importlib.util.spec_from_file_location(
            "src.server.api_routes.rules_api",
            "src/server/api_routes/rules_api.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Replace _get_service with a mock
        mock_svc = MagicMock()
        mod._get_service = lambda: mock_svc

        app = FastAPI()
        app.include_router(mod.router)
        client = TestClient(app)
        yield mock_svc, client

    def test_list_rules_endpoint(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        mock_svc.list_rules.return_value = (True, {"rules": [], "total_count": 0})
        response = client.get("/api/rules")
        assert response.status_code == 200

    def test_create_rule_endpoint(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        rule_row = {
            "id": "new-rule",
            "section": "testing",
            "rule_text": "Write tests first",
            "priority": 50,
            "source": "manual",
            "enabled": True,
            "project_id": None,
        }
        mock_svc.create_rule.return_value = (True, {"rule": rule_row})

        response = client.post(
            "/api/rules",
            json={"section": "testing", "rule_text": "Write tests first"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rule"]["section"] == "testing"

    def test_get_rule_endpoint_not_found(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        mock_svc.get_rule.return_value = (False, {"error": "Rule not-found not found"})

        response = client.get("/api/rules/nonexistent-id")
        assert response.status_code == 404

    def test_update_rule_endpoint(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        updated_row = {"id": "rule-1", "section": "security", "rule_text": "Updated", "enabled": True}
        mock_svc.update_rule.return_value = (True, {"rule": updated_row})

        response = client.put("/api/rules/rule-1", json={"rule_text": "Updated"})
        assert response.status_code == 200

    def test_update_rule_no_fields(self, mock_svc_and_client):
        _, client = mock_svc_and_client
        response = client.put("/api/rules/rule-1", json={})
        assert response.status_code == 422

    def test_delete_rule_endpoint(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        mock_svc.delete_rule.return_value = (True, {"message": "Rule rule-1 deleted"})

        response = client.delete("/api/rules/rule-1")
        assert response.status_code == 200

    def test_generate_claude_md_endpoint(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        mock_svc.generate_claude_md.return_value = (
            True,
            {
                "markdown": "# CLAUDE.md\n\n<!-- Auto-generated -->\n\n## Validate\nDo it.\n",
                "rule_count": 1,
                "sections": ["validation"],
            },
        )

        response = client.get("/api/rules/generate-claude-md/proj-123")
        assert response.status_code == 200
        data = response.json()
        assert "markdown" in data
        assert "# CLAUDE.md" in data["markdown"]

    def test_create_rule_service_error(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        mock_svc.create_rule.return_value = (False, {"error": "section and rule_text are required"})

        response = client.post("/api/rules", json={"section": "", "rule_text": ""})
        assert response.status_code == 400

    def test_list_rules_with_filters(self, mock_svc_and_client):
        mock_svc, client = mock_svc_and_client
        mock_svc.list_rules.return_value = (True, {"rules": [{"id": "r1"}], "total_count": 1})

        response = client.get("/api/rules?project_id=proj-1&section=security&enabled_only=true")
        assert response.status_code == 200
        mock_svc.list_rules.assert_called_once_with(
            project_id="proj-1",
            section="security",
            enabled_only=True,
            include_global=True,
        )
