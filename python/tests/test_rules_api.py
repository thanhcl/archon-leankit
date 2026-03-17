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


# --- API Integration Tests ---


class TestRulesAPIEndpoints:
    """Integration tests for the Rules API endpoints."""

    def test_list_rules_endpoint(self, client):
        response = client.get("/api/rules")
        assert response.status_code == 200

    def test_create_rule_endpoint(self, client, mock_supabase_client):
        # Setup mock for insert
        rule_row = {
            "id": "new-rule",
            "section": "testing",
            "rule_text": "Write tests first",
            "priority": 50,
            "source": "manual",
            "enabled": True,
            "project_id": None,
        }
        mock_supabase_client.table.return_value.insert.return_value.execute.return_value.data = [rule_row]

        response = client.post(
            "/api/rules",
            json={"section": "testing", "rule_text": "Write tests first"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rule"]["section"] == "testing"

    def test_get_rule_endpoint_not_found(self, client, mock_supabase_client):
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        response = client.get("/api/rules/nonexistent-id")
        assert response.status_code == 404

    def test_update_rule_endpoint(self, client, mock_supabase_client):
        updated_row = {"id": "rule-1", "section": "security", "rule_text": "Updated", "enabled": True}
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            updated_row
        ]

        response = client.put("/api/rules/rule-1", json={"rule_text": "Updated"})
        assert response.status_code == 200

    def test_update_rule_no_fields(self, client):
        response = client.put("/api/rules/rule-1", json={})
        assert response.status_code == 422

    def test_delete_rule_endpoint(self, client, mock_supabase_client):
        mock_supabase_client.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = [
            {"id": "rule-1"}
        ]

        response = client.delete("/api/rules/rule-1")
        assert response.status_code == 200

    def test_generate_claude_md_endpoint(self, client, mock_supabase_client):
        rules = [
            {
                "id": "r1",
                "section": "validation",
                "rule_text": "## Validate\nDo it.",
                "priority": 1,
                "enabled": True,
                "project_id": None,
            }
        ]
        mock_query = MagicMock()
        mock_query.execute.return_value.data = rules
        mock_query.eq.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_supabase_client.table.return_value.select.return_value = mock_query

        response = client.get("/api/rules/generate-claude-md/proj-123")
        assert response.status_code == 200
        data = response.json()
        assert "markdown" in data
        assert "# CLAUDE.md" in data["markdown"]
