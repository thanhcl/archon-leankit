"""Tests for PlanMarkdownParser and PlanImportService."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.server.services.projects.plan_import_service import (
    PlanImportService,
    PlanMarkdownParser,
    compute_sha256,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SIMPLE_DOC = """
# Plan: Alpha

## Phase 1: Foundation

### B-P1-01: Setup database
Database schema for all core tables.

- **Status**: done
- **Priority**: high
- **Complexity**: medium

### B-P1-02: Create API layer
REST API endpoints.

- **Status**: planned
- **Priority**: high
- **Dependencies**: B-P1-01

**Acceptance Criteria**:
- All CRUD endpoints implemented
- Tests pass

## Phase 2: Features

### B-P2-01: Add frontend
- **Status**: planned
- **Priority**: medium
- **Dependencies**: B-P1-02
"""

_MINIMAL_DOC = """
# Minimal Plan

## Phase 1: Only Phase

### X-P1-01: Only Item
- **Status**: planned
"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_client(data=None, error=None):
    """Build a mock Supabase client with a chainable query builder."""
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = data

    chain = MagicMock()
    chain.execute.return_value = execute_result
    chain.eq.return_value = chain
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.delete.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.maybe_single.return_value = chain

    if error:
        chain.execute.side_effect = error

    client.table.return_value = chain
    return client, chain


# ── PlanMarkdownParser ────────────────────────────────────────────────────────


class TestPlanMarkdownParserTitleExtraction:
    def test_extracts_plain_title(self):
        parser = PlanMarkdownParser()
        doc = "# My Plan\n\n## Phase 1: P\n\n### A-P1-01: Item\n- **Status**: planned\n"
        plan = parser.parse(doc)
        assert plan.title == "My Plan"

    def test_strips_implementation_plan_prefix(self):
        parser = PlanMarkdownParser()
        doc = "# Implementation Plan: Fancy Title\n\n## Phase 1: P\n\n### A-P1-01: X\n"
        plan = parser.parse(doc)
        assert plan.title == "Fancy Title"

    def test_strips_plan_prefix(self):
        parser = PlanMarkdownParser()
        doc = "# Plan: Short\n\n## Phase 1: P\n\n### A-P1-01: X\n"
        plan = parser.parse(doc)
        assert plan.title == "Short"

    def test_defaults_title_when_missing(self):
        parser = PlanMarkdownParser()
        doc = "## Phase 1: P\n\n### A-P1-01: X\n"
        plan = parser.parse(doc)
        assert plan.title == "Imported Plan"


class TestPlanMarkdownParserPhaseExtraction:
    def test_extracts_phase_count(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        assert len(plan.phases) == 2

    def test_strips_phase_number_prefix(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        assert plan.phases[0].title == "Foundation"
        assert plan.phases[1].title == "Features"

    def test_phase_order_increments(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        assert plan.phases[0].phase_order == 0
        assert plan.phases[1].phase_order == 1


class TestPlanMarkdownParserItemExtraction:
    def test_extracts_correct_item_count(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        assert len(plan.all_items) == 3

    def test_extracts_item_keys(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        keys = [i.item_key for i in plan.all_items]
        assert "B-P1-01" in keys
        assert "B-P1-02" in keys
        assert "B-P2-01" in keys

    def test_extracts_item_titles(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert by_key["B-P1-01"].title == "Setup database"

    def test_extracts_status(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert by_key["B-P1-01"].status == "done"
        assert by_key["B-P1-02"].status == "planned"

    def test_extracts_priority(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert by_key["B-P1-01"].priority == "high"
        assert by_key["B-P2-01"].priority == "medium"

    def test_extracts_complexity(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert by_key["B-P1-01"].complexity == "medium"

    def test_extracts_dependencies(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert by_key["B-P1-02"].dependencies == ["B-P1-01"]
        assert by_key["B-P2-01"].dependencies == ["B-P1-02"]
        assert by_key["B-P1-01"].dependencies == []

    def test_extracts_acceptance_criteria(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert len(by_key["B-P1-02"].acceptance_criteria) == 2

    def test_item_order_increments_across_phases(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        orders = [i.item_order for i in plan.all_items]
        assert orders == [0, 1, 2]

    def test_items_assigned_to_correct_phase(self):
        parser = PlanMarkdownParser()
        plan = parser.parse(_SIMPLE_DOC)
        by_key = {i.item_key: i for i in plan.all_items}
        assert by_key["B-P1-01"].phase_title == "Foundation"
        assert by_key["B-P2-01"].phase_title == "Features"


class TestPlanMarkdownParserEdgeCases:
    def test_ignores_h3_without_key_pattern(self):
        parser = PlanMarkdownParser()
        doc = "# P\n## Phase 1: X\n### Not an item\n### B-P1-01: Real item\n"
        plan = parser.parse(doc)
        assert len(plan.all_items) == 1
        assert plan.all_items[0].item_key == "B-P1-01"

    def test_handles_none_dependencies_value(self):
        parser = PlanMarkdownParser()
        doc = "# P\n## Phase 1: X\n### B-P1-01: Item\n- **Dependencies**: none\n"
        plan = parser.parse(doc)
        assert plan.all_items[0].dependencies == []

    def test_normalises_status_alias(self):
        parser = PlanMarkdownParser()
        doc = "# P\n## Phase 1: X\n### B-P1-01: Item\n- **Status**: todo\n"
        plan = parser.parse(doc)
        assert plan.all_items[0].status == "planned"

    def test_ignores_unknown_status(self):
        parser = PlanMarkdownParser()
        doc = "# P\n## Phase 1: X\n### B-P1-01: Item\n- **Status**: unknown_status\n"
        plan = parser.parse(doc)
        assert plan.all_items[0].status == "planned"  # default unchanged

    def test_empty_doc_produces_no_phases(self):
        parser = PlanMarkdownParser()
        plan = parser.parse("")
        assert plan.phases == []


# ── compute_sha256 ────────────────────────────────────────────────────────────


class TestComputeSha256:
    def test_returns_64_char_hex(self):
        h = compute_sha256("hello")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert compute_sha256("same") == compute_sha256("same")

    def test_different_content_different_hash(self):
        assert compute_sha256("a") != compute_sha256("b")


# ── PlanImportService ─────────────────────────────────────────────────────────


def _make_import_service_with_client(data=None, error=None):
    client, chain = _make_client(data=data, error=error)
    svc = PlanImportService(supabase_client=client)
    return svc, client, chain


class TestImportPlanValidation:
    def test_fails_without_project_id(self):
        svc, _, _ = _make_import_service_with_client()
        ok, result = svc.import_plan("", _MINIMAL_DOC)
        assert ok is False
        assert "project_id" in result["error"]

    def test_fails_with_empty_content(self):
        svc, _, _ = _make_import_service_with_client()
        ok, result = svc.import_plan("proj-1", "")
        assert ok is False
        assert "content" in result["error"]

    def test_fails_when_no_phases_found(self):
        svc, _, _ = _make_import_service_with_client()
        ok, result = svc.import_plan("proj-1", "# Just a title\nSome text\n")
        assert ok is False
        assert "No phases" in result["error"]

    def test_fails_when_no_items_found(self):
        svc, _, _ = _make_import_service_with_client()
        ok, result = svc.import_plan("proj-1", "# Plan\n## Phase 1: Only Phase\nNo items here.\n")
        assert ok is False
        assert "No items" in result["error"]


class TestImportPlanNewPlan:
    def _make_svc(self, plan_row=None, phase_row=None, item_row=None, dep_row=None):
        """Build a service where no existing plan is found and all inserts succeed."""
        client = MagicMock()
        execute_result = MagicMock()

        # Track table() call counts to return different data per call
        call_counter: dict[str, int] = {"n": 0}

        def make_chain(return_data):
            chain = MagicMock()
            result = MagicMock()
            result.data = return_data
            chain.execute.return_value = result
            chain.eq.return_value = chain
            chain.select.return_value = chain
            chain.insert.return_value = chain
            chain.update.return_value = chain
            chain.delete.return_value = chain
            chain.order.return_value = chain
            chain.maybe_single.return_value = chain
            return chain

        # resolve_existing_plan returns None (no existing plan)
        no_plan_chain = make_chain(None)

        # Plan insert
        plan_chain = make_chain(plan_row or [{"id": "plan-1", "project_id": "proj-1", "title": "Minimal Plan"}])

        # Phase insert
        phase_chain = make_chain(phase_row or [{"id": "phase-1", "plan_id": "plan-1", "title": "Only Phase"}])

        # Item insert
        item_chain = make_chain(item_row or [{"id": "item-1", "item_key": "X-P1-01"}])

        # Dep insert (no dependencies in minimal doc)
        dep_chain = make_chain(dep_row or [])

        chains = [no_plan_chain, plan_chain, phase_chain, item_chain, dep_chain]

        def table_side_effect(table_name):
            n = call_counter["n"]
            call_counter["n"] += 1
            return chains[n] if n < len(chains) else make_chain([])

        client.table.side_effect = table_side_effect
        svc = PlanImportService(supabase_client=client)
        return svc, client

    def test_creates_plan_and_returns_action(self):
        svc, _ = self._make_svc()
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC)
        assert ok is True
        assert result["action"] == "created"
        assert result["plan_id"] == "plan-1"

    def test_returns_source_hash(self):
        svc, _ = self._make_svc()
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC)
        assert ok is True
        assert result["source_hash"] == compute_sha256(_MINIMAL_DOC)

    def test_returns_phase_count(self):
        svc, _ = self._make_svc()
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC)
        assert ok is True
        assert result["phases_created"] == 1

    def test_returns_item_count(self):
        svc, _ = self._make_svc()
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC)
        assert ok is True
        assert result["items_created"] == 1

    def test_diff_is_none_for_new_plan(self):
        svc, _ = self._make_svc()
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC)
        assert ok is True
        assert result["diff"] is None

    def test_rolls_back_on_phase_insert_failure(self):
        client = MagicMock()

        def make_chain(return_data=None, side_effect=None):
            chain = MagicMock()
            result = MagicMock()
            result.data = return_data
            chain.execute.return_value = result
            if side_effect:
                chain.execute.side_effect = side_effect
            chain.eq.return_value = chain
            chain.select.return_value = chain
            chain.insert.return_value = chain
            chain.update.return_value = chain
            chain.delete.return_value = chain
            chain.order.return_value = chain
            chain.maybe_single.return_value = chain
            return chain

        call_counter = {"n": 0}
        no_plan_chain = make_chain(None)
        plan_chain = make_chain([{"id": "plan-1", "project_id": "proj-1", "title": "T"}])
        phase_fail_chain = make_chain(side_effect=RuntimeError("phase insert failed"))
        delete_chain = make_chain([])

        chains = [no_plan_chain, plan_chain, phase_fail_chain, delete_chain]

        def table_side_effect(table_name):
            n = call_counter["n"]
            call_counter["n"] += 1
            return chains[n] if n < len(chains) else make_chain([])

        client.table.side_effect = table_side_effect
        svc = PlanImportService(supabase_client=client)
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC)
        assert ok is False
        assert "Import failed" in result["error"]


class TestImportPlanDiffComputation:
    def test_compute_diff_detects_added(self):
        svc, _, _ = _make_import_service_with_client()
        existing = [{"id": "i1", "item_key": "B-P1-01", "title": "Old", "status": "done",
                     "priority": "medium", "complexity": "simple"}]
        parser = PlanMarkdownParser()
        parsed = parser.parse(_SIMPLE_DOC)
        diff = svc._compute_diff(existing, parsed)
        added_keys = {i["item_key"] for i in diff["added"]}
        assert "B-P1-02" in added_keys
        assert "B-P2-01" in added_keys

    def test_compute_diff_detects_removed(self):
        svc, _, _ = _make_import_service_with_client()
        existing = [
            {"id": "i1", "item_key": "B-P1-01", "title": "Setup database",
             "status": "done", "priority": "high", "complexity": "medium"},
            {"id": "i2", "item_key": "REMOVED-P1-99", "title": "Gone",
             "status": "planned", "priority": "low", "complexity": "simple"},
        ]
        parser = PlanMarkdownParser()
        parsed = parser.parse(_SIMPLE_DOC)
        diff = svc._compute_diff(existing, parsed)
        removed_keys = {i["item_key"] for i in diff["removed"]}
        assert "REMOVED-P1-99" in removed_keys

    def test_removed_items_include_flag_note(self):
        svc, _, _ = _make_import_service_with_client()
        existing = [
            {"id": "i1", "item_key": "OLD-P1-01", "title": "Gone",
             "status": "planned", "priority": "low", "complexity": "simple"},
        ]
        parser = PlanMarkdownParser()
        parsed = parser.parse(_MINIMAL_DOC)
        diff = svc._compute_diff(existing, parsed)
        assert len(diff["removed"]) == 1
        assert "flagged" in diff["removed"][0]["note"]

    def test_compute_diff_detects_changed_status(self):
        svc, _, _ = _make_import_service_with_client()
        existing = [
            {"id": "i1", "item_key": "X-P1-01", "title": "Only Item",
             "status": "done", "priority": "medium", "complexity": "simple"},
        ]
        parser = PlanMarkdownParser()
        parsed = parser.parse(_MINIMAL_DOC)
        diff = svc._compute_diff(existing, parsed)
        changed_keys = {i["item_key"] for i in diff["changed"]}
        assert "X-P1-01" in changed_keys
        # Verify change details
        change_entry = next(i for i in diff["changed"] if i["item_key"] == "X-P1-01")
        assert any(c["field"] == "status" for c in change_entry["changes"])

    def test_compute_diff_unchanged_items(self):
        svc, _, _ = _make_import_service_with_client()
        existing = [
            {"id": "i1", "item_key": "X-P1-01", "title": "Only Item",
             "status": "planned", "priority": "medium", "complexity": "simple"},
        ]
        parser = PlanMarkdownParser()
        parsed = parser.parse(_MINIMAL_DOC)
        diff = svc._compute_diff(existing, parsed)
        unchanged_keys = {i["item_key"] for i in diff["unchanged"]}
        assert "X-P1-01" in unchanged_keys
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["changed"] == []


class TestImportPlanReimportPreview:
    def _make_reimport_svc(self, existing_plan, existing_items):
        """Service where an existing plan is returned for re-import."""
        client = MagicMock()
        call_counter = {"n": 0}

        def make_chain(data):
            chain = MagicMock()
            r = MagicMock()
            r.data = data
            chain.execute.return_value = r
            chain.eq.return_value = chain
            chain.select.return_value = chain
            chain.insert.return_value = chain
            chain.update.return_value = chain
            chain.delete.return_value = chain
            chain.order.return_value = chain
            chain.limit.return_value = chain
            chain.maybe_single.return_value = chain
            return chain

        # Call 0: resolve existing plan (maybe_single returns existing_plan)
        plan_chain = make_chain(existing_plan)
        # Call 1: load existing items
        items_chain = make_chain(existing_items)

        chains = [plan_chain, items_chain]

        def table_side_effect(table_name):
            n = call_counter["n"]
            call_counter["n"] += 1
            return chains[n] if n < len(chains) else make_chain([])

        client.table.side_effect = table_side_effect
        svc = PlanImportService(supabase_client=client)
        return svc

    def test_preview_returns_diff_preview_action(self):
        existing_plan = {
            "id": "plan-1",
            "project_id": "proj-1",
            "title": "Minimal Plan",
            "source_doc_hash": "oldhash",
        }
        svc = self._make_reimport_svc(existing_plan, [])
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC, preview_only=True)
        assert ok is True
        assert result["action"] == "diff_preview"

    def test_preview_returns_hash_changed_true_when_different(self):
        existing_plan = {
            "id": "plan-1",
            "project_id": "proj-1",
            "title": "Minimal Plan",
            "source_doc_hash": "oldhash",
        }
        svc = self._make_reimport_svc(existing_plan, [])
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC, preview_only=True)
        assert ok is True
        assert result["hash_changed"] is True

    def test_preview_returns_hash_changed_false_when_same(self):
        content = _MINIMAL_DOC
        current_hash = compute_sha256(content)
        existing_plan = {
            "id": "plan-1",
            "project_id": "proj-1",
            "title": "Minimal Plan",
            "source_doc_hash": current_hash,
        }
        svc = self._make_reimport_svc(existing_plan, [])
        ok, result = svc.import_plan("proj-1", content, preview_only=True)
        assert ok is True
        assert result["hash_changed"] is False

    def test_preview_returns_diff(self):
        existing_plan = {
            "id": "plan-1",
            "project_id": "proj-1",
            "title": "Minimal Plan",
            "source_doc_hash": "oldhash",
        }
        existing_items = [
            {
                "id": "i1",
                "item_key": "X-P1-01",
                "title": "Only Item",
                "status": "planned",
                "priority": "medium",
                "complexity": "simple",
            }
        ]
        svc = self._make_reimport_svc(existing_plan, existing_items)
        ok, result = svc.import_plan("proj-1", _MINIMAL_DOC, preview_only=True)
        assert ok is True
        assert "diff" in result
        assert "unchanged" in result["diff"]
