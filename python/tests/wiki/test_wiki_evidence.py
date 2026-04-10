"""Tests for Verbatim Memory Mode — wiki evidence CRUD, search, staleness.

Covers:
- add_evidence: append, dedup by hash, eviction, quality bump
- search_evidence: term matching across project pages
- get_stale_evidence: staleness detection based on configurable threshold
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

def _make_wiki_service(mock_pages=None, mock_evidence=None):
    """Create a WikiService with mocked Supabase client."""
    from src.server.services.wiki.wiki_service import WikiService

    with patch("src.server.services.wiki.wiki_service.get_supabase_client") as mock_get:
        mock_client = MagicMock()
        mock_get.return_value = mock_client

        svc = WikiService()

        # Setup table mock chain
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table

        if mock_pages is not None:
            mock_select = MagicMock()
            mock_table.select.return_value = mock_select
            mock_eq = MagicMock()
            mock_select.eq.return_value = mock_eq

            # For add_evidence: .select().eq().execute()
            mock_response = MagicMock()
            mock_response.data = mock_pages
            mock_eq.execute.return_value = mock_response

            # Also handle .neq() for search/stale
            mock_neq = MagicMock()
            mock_eq.neq.return_value = mock_neq
            mock_neq.execute.return_value = mock_response

        return svc, mock_client


SAMPLE_QUOTE = "Always use parameterized queries to prevent SQL injection"
SAMPLE_QUOTE_ALT = "Never store secrets in environment variables without encryption"


class TestAddEvidence:
    """Tests for WikiService.add_evidence()."""

    def test_add_evidence_success(self):
        """Evidence is appended to existing empty array."""
        mock_pages = [{"evidence": "[]", "quality_score": 0.5}]
        svc, client = _make_wiki_service(mock_pages=mock_pages)

        # Mock the update call
        mock_table = client.table.return_value
        mock_update = MagicMock()
        mock_table.update.return_value = mock_update
        mock_update_eq = MagicMock()
        mock_update.eq.return_value = mock_update_eq
        mock_update_eq.execute.return_value = MagicMock()

        ok, result = svc.add_evidence(
            page_id="page-001",
            quote=SAMPLE_QUOTE,
            source_url="https://example.com/docs",
            context="SQL security best practice",
        )

        assert ok is True
        assert "quote_hash" in result
        assert result["evidence_count"] == 1
        assert result["quality_score"] > 0.5

    def test_dedup_same_quote(self):
        """Same quote (after normalization) is not duplicated."""
        existing_evidence = [{
            "quote": SAMPLE_QUOTE,
            "quote_hash": "",  # Will be computed
            "captured_at": "2026-04-01T00:00:00+00:00",
        }]
        # Pre-compute the hash the same way the service does
        import hashlib
        import re
        normalized = re.sub(r"\s+", " ", SAMPLE_QUOTE.strip().lower())
        expected_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        existing_evidence[0]["quote_hash"] = expected_hash

        mock_pages = [{"evidence": json.dumps(existing_evidence), "quality_score": 0.6}]
        svc, client = _make_wiki_service(mock_pages=mock_pages)

        ok, result = svc.add_evidence(
            page_id="page-001",
            quote=SAMPLE_QUOTE,
        )

        assert ok is True
        assert "Duplicate" in result.get("message", "")

    def test_page_not_found(self):
        """Returns error when page doesn't exist."""
        svc, client = _make_wiki_service(mock_pages=[])

        ok, result = svc.add_evidence(
            page_id="nonexistent",
            quote=SAMPLE_QUOTE,
        )

        assert ok is False
        assert "not found" in result.get("error", "")


class TestSearchEvidence:
    """Tests for WikiService.search_evidence()."""

    def test_search_finds_matching_quote(self):
        """Search returns pages with matching evidence quotes."""
        evidence = [
            {
                "quote": "Use ruff for linting instead of flake8",
                "quote_hash": "abc123",
                "context": "Python tooling standard",
                "captured_at": "2026-04-01T00:00:00+00:00",
            },
        ]
        mock_pages = [{
            "id": "page-001",
            "title": "Python Standards",
            "slug": "python-standards",
            "evidence": json.dumps(evidence),
        }]

        svc, client = _make_wiki_service(mock_pages=mock_pages)

        # Override the chain for search (uses different query)
        mock_table = client.table.return_value
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_neq = MagicMock()
        mock_eq.neq.return_value = mock_neq
        mock_neq.execute.return_value = MagicMock(data=mock_pages)

        ok, results = svc.search_evidence(
            project_id="proj-001",
            query="ruff linting",
        )

        assert ok is True
        assert len(results) == 1
        assert results[0]["page_title"] == "Python Standards"
        assert len(results[0]["matching_evidence"]) == 1

    def test_search_empty_query(self):
        """Empty query returns no results."""
        svc, _ = _make_wiki_service(mock_pages=[])

        ok, results = svc.search_evidence(project_id="proj-001", query="")
        assert ok is True
        assert len(results) == 0


class TestStaleEvidence:
    """Tests for WikiService.get_stale_evidence()."""

    def test_detects_stale_evidence(self):
        """Evidence older than stale_after_days is flagged."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        evidence = [
            {
                "quote": "Old finding",
                "quote_hash": "old123",
                "captured_at": old_date,
                "stale_after_days": 90,
                "verified_at": None,
            },
        ]
        mock_pages = [{
            "id": "page-001",
            "title": "Old Page",
            "slug": "old-page",
            "evidence": json.dumps(evidence),
        }]

        svc, client = _make_wiki_service(mock_pages=mock_pages)

        mock_table = client.table.return_value
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_neq = MagicMock()
        mock_eq.neq.return_value = mock_neq
        mock_neq.execute.return_value = MagicMock(data=mock_pages)

        ok, results = svc.get_stale_evidence(project_id="proj-001")

        assert ok is True
        assert len(results) == 1
        assert results[0]["stale_count"] == 1

    def test_fresh_evidence_not_flagged(self):
        """Evidence within stale_after_days is not flagged."""
        recent_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        evidence = [
            {
                "quote": "Recent finding",
                "quote_hash": "new123",
                "captured_at": recent_date,
                "stale_after_days": 90,
                "verified_at": None,
            },
        ]
        mock_pages = [{
            "id": "page-001",
            "title": "Recent Page",
            "slug": "recent-page",
            "evidence": json.dumps(evidence),
        }]

        svc, client = _make_wiki_service(mock_pages=mock_pages)

        mock_table = client.table.return_value
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_neq = MagicMock()
        mock_eq.neq.return_value = mock_neq
        mock_neq.execute.return_value = MagicMock(data=mock_pages)

        ok, results = svc.get_stale_evidence(project_id="proj-001")

        assert ok is True
        assert len(results) == 0
