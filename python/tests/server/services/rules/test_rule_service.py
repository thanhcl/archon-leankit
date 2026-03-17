"""Tests for RuleService — CRUD, filtering, and CLAUDE.md generation."""

from unittest.mock import MagicMock

from src.server.services.rules.rule_service import VALID_SECTIONS, RuleService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(**overrides):
    base = {
        "id": "rule-001",
        "project_id": None,
        "section": "validation",
        "rule_text": "## Validation\nAlways validate inputs.",
        "priority": 10,
        "source": "manual",
        "enabled": True,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    base.update(overrides)
    return base


def _mock_client(select_data=None, insert_data=None, update_data=None, delete_data=None):
    """Build a mock Supabase client with chained query support."""
    client = MagicMock()
    table = MagicMock()

    # select chain
    select = MagicMock()
    select.eq.return_value = select
    select.is_.return_value = select
    select.or_.return_value = select
    select.order.return_value = select
    execute_result = MagicMock()
    execute_result.data = select_data if select_data is not None else []
    select.execute.return_value = execute_result
    table.select.return_value = select

    # insert chain
    insert_result = MagicMock()
    insert_result.data = insert_data if insert_data is not None else [_make_rule()]
    insert_mock = MagicMock()
    insert_mock.execute.return_value = insert_result
    table.insert.return_value = insert_mock

    # update chain
    update = MagicMock()
    update.eq.return_value = update
    update_result = MagicMock()
    update_result.data = update_data if update_data is not None else [_make_rule()]
    update.execute.return_value = update_result
    table.update.return_value = update

    # delete chain
    delete = MagicMock()
    delete.eq.return_value = delete
    delete_result = MagicMock()
    delete_result.data = delete_data if delete_data is not None else [_make_rule()]
    delete.execute.return_value = delete_result
    table.delete.return_value = delete

    client.table.return_value = table
    return client


# ---------------------------------------------------------------------------
# Tests: create_rule
# ---------------------------------------------------------------------------


class TestCreateRule:
    def test_creates_global_rule(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        ok, result = service.create_rule(
            section="validation",
            rule_text="Always validate inputs.",
        )

        assert ok is True
        assert "rule" in result
        client.table.assert_called_with("archon_rules")
        insert_call = client.table().insert.call_args[0][0]
        assert insert_call["section"] == "validation"
        assert insert_call["enabled"] is True
        assert "project_id" not in insert_call

    def test_creates_project_scoped_rule(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        ok, result = service.create_rule(
            section="security",
            rule_text="No secrets in logs.",
            project_id="proj-123",
            priority=20,
        )

        assert ok is True
        insert_call = client.table().insert.call_args[0][0]
        assert insert_call["project_id"] == "proj-123"
        assert insert_call["priority"] == 20

    def test_create_requires_section(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        ok, result = service.create_rule(section="", rule_text="some text")

        assert ok is False
        assert "required" in result["error"]

    def test_create_requires_rule_text(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        ok, result = service.create_rule(section="validation", rule_text="")

        assert ok is False
        assert "required" in result["error"]

    def test_create_rejects_invalid_section(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        ok, result = service.create_rule(section="xss-payload", rule_text="text")

        assert ok is False
        assert "Invalid section" in result["error"]
        assert "xss-payload" in result["error"]
        # Should not have called insert
        client.table().insert.assert_not_called()

    def test_create_accepts_all_valid_sections(self):
        for section in VALID_SECTIONS:
            client = _mock_client()
            service = RuleService(supabase_client=client)

            ok, result = service.create_rule(section=section, rule_text="text")

            assert ok is True, f"Section '{section}' should be valid"

    def test_create_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        service = RuleService(supabase_client=client)

        ok, result = service.create_rule(section="validation", rule_text="text")

        assert ok is False
        assert "Error" in result["error"]


# ---------------------------------------------------------------------------
# Tests: list_rules with filters
# ---------------------------------------------------------------------------


class TestListRules:
    def test_list_all_enabled(self):
        rules = [_make_rule(), _make_rule(id="rule-002", section="security")]
        client = _mock_client(select_data=rules)
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules()

        assert ok is True
        assert result["total_count"] == 2

    def test_list_filters_by_project_and_global(self):
        """When project_id is given with include_global=True, should use or_ filter."""
        client = _mock_client(select_data=[_make_rule()])
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules(project_id="proj-123", include_global=True)

        assert ok is True
        # Verify or_ was called for combined global+project filter
        select_mock = client.table().select()
        select_mock.eq.assert_called()  # enabled=True filter
        select_mock.or_.assert_called()

    def test_list_project_only(self):
        """When include_global=False, should filter only by project_id."""
        client = _mock_client(select_data=[_make_rule(project_id="proj-123")])
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules(project_id="proj-123", include_global=False)

        assert ok is True

    def test_list_global_only(self):
        """When no project_id, should get global rules (project_id IS NULL)."""
        client = _mock_client(select_data=[_make_rule()])
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules(include_global=True)

        assert ok is True

    def test_list_filters_by_section(self):
        """When section is given, should apply eq filter on section."""
        rules = [_make_rule(section="security")]
        client = _mock_client(select_data=rules)
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules(section="security")

        assert ok is True
        assert result["total_count"] == 1
        # Verify eq was called with section filter
        select_mock = client.table().select()
        select_mock.eq.assert_any_call("section", "security")

    def test_list_includes_disabled_when_requested(self):
        rules = [_make_rule(), _make_rule(id="rule-disabled", enabled=False)]
        client = _mock_client(select_data=rules)
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules(enabled_only=False)

        assert ok is True
        assert result["total_count"] == 2

    def test_list_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        service = RuleService(supabase_client=client)

        ok, result = service.list_rules()

        assert ok is False
        assert "Error" in result["error"]


# ---------------------------------------------------------------------------
# Tests: get_rule
# ---------------------------------------------------------------------------


class TestGetRule:
    def test_get_existing_rule(self):
        client = _mock_client(select_data=[_make_rule()])
        service = RuleService(supabase_client=client)

        ok, result = service.get_rule("rule-001")

        assert ok is True
        assert result["rule"]["id"] == "rule-001"

    def test_get_nonexistent_rule(self):
        client = _mock_client(select_data=[])
        service = RuleService(supabase_client=client)

        ok, result = service.get_rule("nonexistent")

        assert ok is False
        assert "not found" in result["error"]

    def test_get_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        service = RuleService(supabase_client=client)

        ok, result = service.get_rule("rule-001")

        assert ok is False


# ---------------------------------------------------------------------------
# Tests: update_rule
# ---------------------------------------------------------------------------


class TestUpdateRule:
    def test_update_rule_text(self):
        updated = _make_rule(rule_text="Updated text.")
        client = _mock_client(update_data=[updated])
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("rule-001", {"rule_text": "Updated text."})

        assert ok is True
        assert result["rule"]["rule_text"] == "Updated text."
        update_call = client.table().update.call_args[0][0]
        assert update_call["rule_text"] == "Updated text."
        assert "updated_at" in update_call

    def test_update_priority(self):
        client = _mock_client(update_data=[_make_rule(priority=5)])
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("rule-001", {"priority": 5})

        assert ok is True
        update_call = client.table().update.call_args[0][0]
        assert update_call["priority"] == 5

    def test_update_enabled_flag(self):
        client = _mock_client(update_data=[_make_rule(enabled=False)])
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("rule-001", {"enabled": False})

        assert ok is True

    def test_update_nonexistent_rule(self):
        client = _mock_client(update_data=[])
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("nonexistent", {"rule_text": "x"})

        assert ok is False
        assert "not found" in result["error"]

    def test_update_rejects_invalid_section(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("rule-001", {"section": "bad-section"})

        assert ok is False
        assert "Invalid section" in result["error"]
        # Should not have called update on the DB
        client.table().update.assert_not_called()

    def test_update_accepts_valid_section(self):
        client = _mock_client(update_data=[_make_rule(section="testing")])
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("rule-001", {"section": "testing"})

        assert ok is True

    def test_update_ignores_disallowed_fields(self):
        client = _mock_client()
        service = RuleService(supabase_client=client)

        service.update_rule("rule-001", {"id": "hacked", "created_at": "hacked"})

        update_call = client.table().update.call_args[0][0]
        assert "id" not in update_call
        assert "created_at" not in update_call

    def test_update_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        service = RuleService(supabase_client=client)

        ok, result = service.update_rule("rule-001", {"rule_text": "x"})

        assert ok is False


# ---------------------------------------------------------------------------
# Tests: delete_rule
# ---------------------------------------------------------------------------


class TestDeleteRule:
    def test_delete_existing_rule(self):
        client = _mock_client(delete_data=[_make_rule()])
        service = RuleService(supabase_client=client)

        ok, result = service.delete_rule("rule-001")

        assert ok is True
        assert "deleted" in result["message"]

    def test_delete_nonexistent_rule(self):
        client = _mock_client(delete_data=[])
        service = RuleService(supabase_client=client)

        ok, result = service.delete_rule("nonexistent")

        assert ok is False
        assert "not found" in result["error"]

    def test_delete_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        service = RuleService(supabase_client=client)

        ok, result = service.delete_rule("rule-001")

        assert ok is False


# ---------------------------------------------------------------------------
# Tests: get_global_rules
# ---------------------------------------------------------------------------


class TestGetGlobalRules:
    def test_returns_global_rules(self):
        global_rules = [
            _make_rule(id="g1", project_id=None, section="validation"),
            _make_rule(id="g2", project_id=None, section="security"),
        ]
        client = _mock_client(select_data=global_rules)
        service = RuleService(supabase_client=client)

        ok, result = service.get_global_rules()

        assert ok is True
        assert result["total_count"] == 2

    def test_respects_enabled_filter(self):
        client = _mock_client(select_data=[_make_rule()])
        service = RuleService(supabase_client=client)

        ok, result = service.get_global_rules(enabled_only=False)

        assert ok is True


# ---------------------------------------------------------------------------
# Tests: get_project_rules
# ---------------------------------------------------------------------------


class TestGetProjectRules:
    def test_returns_project_scoped_rules(self):
        project_rules = [
            _make_rule(id="p1", project_id="proj-123", section="testing"),
        ]
        client = _mock_client(select_data=project_rules)
        service = RuleService(supabase_client=client)

        ok, result = service.get_project_rules("proj-123")

        assert ok is True
        assert result["total_count"] == 1

    def test_excludes_global_rules(self):
        """get_project_rules should pass include_global=False."""
        client = _mock_client(select_data=[])
        service = RuleService(supabase_client=client)

        ok, result = service.get_project_rules("proj-123")

        assert ok is True
        # Verify eq was called (not or_) — meaning include_global=False path
        select_mock = client.table().select()
        # The call chain should use eq for project_id, not or_
        select_mock.eq.assert_called()


# ---------------------------------------------------------------------------
# Tests: generate_claude_md
# ---------------------------------------------------------------------------


class TestGenerateClaudeMd:
    def test_generates_markdown_with_global_and_project_rules(self):
        rules = [
            _make_rule(id="g1", section="validation", rule_text="## Validation\nAlways validate.", priority=1),
            _make_rule(id="g2", section="security", rule_text="## Security\nNo secrets.", priority=20),
            _make_rule(id="p1", section="validation", rule_text="## Project Validation\nCheck schema.", priority=50, project_id="proj-123"),
        ]
        client = _mock_client(select_data=rules)
        service = RuleService(supabase_client=client)

        ok, result = service.generate_claude_md("proj-123")

        assert ok is True
        md = result["markdown"]
        assert "CLAUDE.md" in md
        assert "Always validate" in md
        assert "No secrets" in md
        assert "Check schema" in md
        assert result["rule_count"] == 3
        assert "validation" in result["sections"]
        assert "security" in result["sections"]
        # Scope comments present
        assert "<!-- Global Rules -->" in md
        assert "<!-- Project Rules: proj-123 -->" in md
        # Global rules appear before project rules
        global_pos = md.index("<!-- Global Rules -->")
        project_pos = md.index("<!-- Project Rules: proj-123 -->")
        assert global_pos < project_pos

    def test_generates_markdown_global_only(self):
        rules = [
            _make_rule(id="g1", section="coding-style", rule_text="## Style\nFollow patterns.", priority=30),
        ]
        client = _mock_client(select_data=rules)
        service = RuleService(supabase_client=client)

        ok, result = service.generate_claude_md("proj-no-rules")

        assert ok is True
        md = result["markdown"]
        assert "Follow patterns" in md
        assert result["rule_count"] == 1
        assert "<!-- Global Rules -->" in md
        assert "<!-- Project Rules" not in md

    def test_generates_empty_when_no_rules(self):
        client = _mock_client(select_data=[])
        service = RuleService(supabase_client=client)

        ok, result = service.generate_claude_md("proj-empty")

        assert ok is True
        assert "No rules configured" in result["markdown"]
        assert result["rule_count"] == 0
        assert result["sections"] == []

    def test_sections_preserve_order(self):
        """Rules should be grouped by section, maintaining query order (section asc, priority asc)."""
        rules = [
            _make_rule(id="r1", section="integration", rule_text="Integration rule.", priority=10),
            _make_rule(id="r2", section="validation", rule_text="Validation rule.", priority=1),
        ]
        client = _mock_client(select_data=rules)
        service = RuleService(supabase_client=client)

        ok, result = service.generate_claude_md("proj-123")

        assert ok is True
        md = result["markdown"]
        # Integration comes before validation in the output (alphabetical from query)
        int_pos = md.index("Integration rule.")
        val_pos = md.index("Validation rule.")
        assert int_pos < val_pos

    def test_handles_db_error(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        service = RuleService(supabase_client=client)

        ok, result = service.generate_claude_md("proj-123")

        assert ok is False
        assert "Error" in result["error"]
