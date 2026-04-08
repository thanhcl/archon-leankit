"""
Integration tests for Wiki KB service.

These tests verify the core wiki operations against a live Supabase instance.
Run with: pytest python/tests/wiki/ -v

Prerequisites:
- Supabase running with wiki tables migrated
- SUPABASE_URL and SUPABASE_SERVICE_KEY set
"""

import json
import os
import pytest
from uuid import uuid4

# Skip entire module if Supabase is not configured
pytestmark = pytest.mark.skipif(
    not os.getenv("SUPABASE_URL"),
    reason="SUPABASE_URL not set — skip integration tests",
)


@pytest.fixture
def wiki_service():
    from src.server.services.wiki.wiki_service import WikiService
    return WikiService()


@pytest.fixture
def lint_service():
    from src.server.services.wiki.wiki_lint_service import WikiLintService
    return WikiLintService()


@pytest.fixture
def discovery_pipeline():
    from src.server.services.discovery.discovery_pipeline import DiscoveryPipeline
    return DiscoveryPipeline()


@pytest.fixture
def test_project_id():
    """Use Archon-LeanKit project ID for tests."""
    return "524dfa20-2a47-4c38-8d9a-bb75e7caaece"


class TestWikiPageCRUD:
    """Test wiki page create, read, update, delete."""

    def test_create_page(self, wiki_service, test_project_id):
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Test Wiki Page",
            content="# Test\n\nThis is a test wiki page.",
            page_type="concept",
            category="pattern",
            tags=["test", "wiki"],
            summary="A test wiki page for integration testing",
        )
        assert ok, f"Failed to create page: {page}"
        assert page["slug"] == "test-wiki-page"
        assert page["page_type"] == "concept"

        # Cleanup
        wiki_service.delete_page(page["id"])

    def test_get_page_by_id(self, wiki_service, test_project_id):
        # Create
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Get By ID Test",
            content="Content here",
            page_type="entity",
        )
        assert ok

        # Get
        ok, fetched = wiki_service.get_page(page_id=page["id"], include_links=True)
        assert ok
        assert fetched["title"] == "Get By ID Test"
        assert "links" in fetched

        # Cleanup
        wiki_service.delete_page(page["id"])

    def test_get_page_by_slug(self, wiki_service, test_project_id):
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Slug Lookup Test",
            content="Test content",
            page_type="concept",
        )
        assert ok

        ok, fetched = wiki_service.get_page(
            slug="slug-lookup-test", project_id=test_project_id
        )
        assert ok
        assert fetched["id"] == page["id"]

        wiki_service.delete_page(page["id"])

    def test_update_page(self, wiki_service, test_project_id):
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Update Test",
            content="Original content",
            page_type="concept",
        )
        assert ok

        ok, updated = wiki_service.update_page(
            page_id=page["id"],
            content="Updated content",
            tags=["updated"],
            status="active",
        )
        assert ok
        assert updated["status"] == "active"

        wiki_service.delete_page(page["id"])

    def test_search_pages(self, wiki_service, test_project_id):
        # Create a searchable page
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Searchable React Hooks Pattern",
            content="React hooks are a powerful pattern for state management in functional components.",
            page_type="concept",
            tags=["react", "hooks"],
            status="active",
        )
        assert ok

        # Search
        ok, results = wiki_service.search_pages(
            query="React hooks", project_id=test_project_id
        )
        assert ok
        assert len(results) >= 1
        assert any(r["id"] == page["id"] for r in results)

        wiki_service.delete_page(page["id"])

    def test_delete_page(self, wiki_service, test_project_id):
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Delete Me",
            content="To be deleted",
            page_type="entity",
        )
        assert ok

        ok, msg = wiki_service.delete_page(page["id"])
        assert ok

        ok, fetched = wiki_service.get_page(page_id=page["id"])
        assert not ok  # Should not be found


class TestWikiLinks:
    """Test cross-reference links between pages."""

    def test_create_link(self, wiki_service, test_project_id):
        ok, page_a = wiki_service.create_page(
            project_id=test_project_id,
            title="Link Source",
            content="Source page",
            page_type="entity",
        )
        ok, page_b = wiki_service.create_page(
            project_id=test_project_id,
            title="Link Target",
            content="Target page",
            page_type="concept",
        )

        ok, link = wiki_service.create_link(
            from_page_id=page_a["id"],
            to_page_id=page_b["id"],
            link_type="related",
            context="Test link",
            created_by="agent",
        )
        assert ok
        assert link["link_type"] == "related"

        # Verify links appear on page
        ok, page_with_links = wiki_service.get_page(
            page_id=page_a["id"], include_links=True
        )
        assert ok
        assert len(page_with_links["links"]["outbound"]) >= 1

        wiki_service.delete_page(page_a["id"])
        wiki_service.delete_page(page_b["id"])

    def test_self_link_rejected(self, wiki_service, test_project_id):
        ok, page = wiki_service.create_page(
            project_id=test_project_id,
            title="Self Link Test",
            content="Cannot link to self",
            page_type="entity",
        )
        assert ok

        ok, result = wiki_service.create_link(
            from_page_id=page["id"],
            to_page_id=page["id"],
        )
        assert not ok  # Should reject self-link

        wiki_service.delete_page(page["id"])


class TestWikiGraph:
    """Test graph traversal."""

    def test_project_graph(self, wiki_service, test_project_id):
        # Create 3 linked pages
        pages = []
        for title in ["Graph A", "Graph B", "Graph C"]:
            ok, p = wiki_service.create_page(
                project_id=test_project_id,
                title=title,
                content=f"Content for {title}",
                page_type="concept",
            )
            assert ok
            pages.append(p)

        # Link A->B, B->C
        wiki_service.create_link(pages[0]["id"], pages[1]["id"], "related")
        wiki_service.create_link(pages[1]["id"], pages[2]["id"], "extends")

        # Get graph
        ok, graph = wiki_service.get_graph(project_id=test_project_id)
        assert ok
        assert len(graph["nodes"]) >= 3
        assert len(graph["edges"]) >= 2

        # Cleanup
        for p in pages:
            wiki_service.delete_page(p["id"])


class TestWikiLint:
    """Test knowledge health checks."""

    def test_lint_detects_orphans(self, wiki_service, lint_service, test_project_id):
        # Create an orphan page (no inbound links)
        ok, orphan = wiki_service.create_page(
            project_id=test_project_id,
            title="Orphan Page For Lint",
            content="This page has no inbound links",
            page_type="concept",
            status="active",
        )
        assert ok

        # Run lint
        ok, report = lint_service.lint(test_project_id)
        assert ok
        assert report["stats"]["total_pages"] >= 1

        wiki_service.delete_page(orphan["id"])


class TestDiscoveryPipeline:
    """Test discovery feed and history management."""

    def test_add_feed(self, discovery_pipeline, test_project_id):
        ok, feed = discovery_pipeline.add_feed(
            project_id=test_project_id,
            feed_type="rss",
            name="Test RSS Feed",
            config={"urls": ["https://example.com/feed.xml"]},
            poll_interval_hours=24,
        )
        assert ok
        assert feed["feed_type"] == "rss"

        # Cleanup
        discovery_pipeline.delete_feed(feed["id"])

    def test_record_discovery_dedup(self, discovery_pipeline, test_project_id):
        url = f"https://example.com/test-{uuid4().hex[:8]}"

        # First record
        ok, record = discovery_pipeline.record_discovery(
            project_id=test_project_id, url=url, title="Test"
        )
        assert ok

        # Duplicate should fail
        ok2, msg = discovery_pipeline.record_discovery(
            project_id=test_project_id, url=url, title="Duplicate"
        )
        assert not ok2
        assert "Duplicate" in str(msg) or "duplicate" in str(msg).lower()

    def test_list_feeds(self, discovery_pipeline, test_project_id):
        ok, feeds = discovery_pipeline.list_feeds(project_id=test_project_id)
        assert ok
        assert isinstance(feeds, list)
