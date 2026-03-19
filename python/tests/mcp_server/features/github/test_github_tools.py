"""Unit tests for GitHub integration tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp import Context

from src.mcp_server.features.github.github_tools import register_github_tools


@pytest.fixture
def mock_mcp():
    """Create a mock MCP server for testing."""
    mock = MagicMock()
    mock._tools = {}

    def tool_decorator():
        def decorator(func):
            mock._tools[func.__name__] = func
            return func
        return decorator

    mock.tool = tool_decorator
    return mock


@pytest.fixture
def mock_context():
    """Create a mock context for testing."""
    return MagicMock(spec=Context)


@pytest.fixture
def github_tools(mock_mcp):
    """Register and return GitHub tools."""
    register_github_tools(mock_mcp)
    return mock_mcp._tools


# --- Registration tests ---

def test_all_tools_registered(github_tools):
    """All four GitHub tools should be registered."""
    expected = {"github_find_commits", "github_find_pull_requests", "github_find_issues", "github_get_task_context"}
    assert set(github_tools.keys()) == expected


# --- github_find_commits ---

@pytest.mark.asyncio
async def test_find_commits_success(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "sha": "abc1234567890",
            "html_url": "https://github.com/owner/repo/commit/abc1234567890",
            "commit": {
                "message": "fix: resolve auth bug",
                "author": {"name": "Alice", "date": "2026-03-15T10:00:00Z"},
            },
        },
        {
            "sha": "def4567890123",
            "html_url": "https://github.com/owner/repo/commit/def4567890123",
            "commit": {
                "message": "feat: add user profile",
                "author": {"name": "Bob", "date": "2026-03-14T09:00:00Z"},
            },
        },
    ]

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_commits"](mock_context, repo="owner/repo")

    data = json.loads(result)
    assert data["success"] is True
    assert data["count"] == 2
    assert data["commits"][0]["sha"] == "abc1234"
    assert data["commits"][0]["author"] == "Alice"


@pytest.mark.asyncio
async def test_find_commits_with_query_filter(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"sha": "aaa", "html_url": "", "commit": {"message": "fix: auth bug", "author": {"name": "A", "date": ""}}},
        {"sha": "bbb", "html_url": "", "commit": {"message": "feat: new feature", "author": {"name": "B", "date": ""}}},
    ]

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_commits"](mock_context, repo="owner/repo", query="auth")

    data = json.loads(result)
    assert data["success"] is True
    assert data["count"] == 1
    assert "auth" in data["commits"][0]["message"].lower()


@pytest.mark.asyncio
async def test_find_commits_repo_not_found(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_commits"](mock_context, repo="owner/nonexistent")

    data = json.loads(result)
    assert data["success"] is False
    assert data["error"]["type"] == "not_found"


@pytest.mark.asyncio
async def test_find_commits_auth_error(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 401

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_commits"](mock_context, repo="owner/repo")

    data = json.loads(result)
    assert data["success"] is False
    assert data["error"]["type"] == "auth_error"


@pytest.mark.asyncio
async def test_find_commits_invalid_repo_format(github_tools, mock_context):
    result = await github_tools["github_find_commits"](mock_context, repo="invalid")
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"]["type"] == "validation_error"


@pytest.mark.asyncio
async def test_find_commits_github_url_format(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_commits"](
            mock_context, repo="https://github.com/coleam00/Archon"
        )

    data = json.loads(result)
    assert data["success"] is True
    assert data["repo"] == "coleam00/Archon"


@pytest.mark.asyncio
async def test_find_commits_per_page_clamped(github_tools, mock_context):
    """per_page > 100 should be clamped to 100."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_commits"](mock_context, repo="owner/repo", per_page=200)

    data = json.loads(result)
    assert data["success"] is True
    # Verify the actual request used clamped value
    call_kwargs = mock_async_client.get.call_args
    assert call_kwargs[1]["params"]["per_page"] == 100


# --- github_find_pull_requests ---

@pytest.mark.asyncio
async def test_find_pull_requests_success(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "number": 42,
            "title": "Add authentication feature",
            "state": "open",
            "user": {"login": "alice"},
            "created_at": "2026-03-10T10:00:00Z",
            "updated_at": "2026-03-15T10:00:00Z",
            "head": {"ref": "feature/auth"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/owner/repo/pull/42",
            "draft": False,
            "labels": [{"name": "feature"}, {"name": "priority"}],
        }
    ]

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_pull_requests"](mock_context, repo="owner/repo")

    data = json.loads(result)
    assert data["success"] is True
    assert data["count"] == 1
    pr = data["pull_requests"][0]
    assert pr["number"] == 42
    assert pr["title"] == "Add authentication feature"
    assert pr["labels"] == ["feature", "priority"]


@pytest.mark.asyncio
async def test_find_pull_requests_invalid_state(github_tools, mock_context):
    result = await github_tools["github_find_pull_requests"](mock_context, repo="owner/repo", state="invalid")
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"]["type"] == "validation_error"


# --- github_find_issues ---

@pytest.mark.asyncio
async def test_find_issues_filters_out_prs(github_tools, mock_context):
    """Issues endpoint returns PRs too — tool should filter them out."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "number": 10,
            "title": "Real issue",
            "state": "open",
            "user": {"login": "alice"},
            "created_at": "",
            "updated_at": "",
            "labels": [],
            "html_url": "",
            "comments": 3,
        },
        {
            "number": 11,
            "title": "This is actually a PR",
            "state": "open",
            "user": {"login": "bob"},
            "created_at": "",
            "updated_at": "",
            "labels": [],
            "html_url": "",
            "comments": 0,
            "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/11"},
        },
    ]

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_issues"](mock_context, repo="owner/repo")

    data = json.loads(result)
    assert data["success"] is True
    assert data["count"] == 1
    assert data["issues"][0]["number"] == 10


@pytest.mark.asyncio
async def test_find_issues_with_labels(github_tools, mock_context):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_find_issues"](
            mock_context, repo="owner/repo", labels="bug,critical"
        )

    data = json.loads(result)
    assert data["success"] is True
    call_kwargs = mock_async_client.get.call_args
    assert call_kwargs[1]["params"]["labels"] == "bug,critical"


# --- github_get_task_context ---

@pytest.mark.asyncio
async def test_get_task_context_combined_search(github_tools, mock_context):
    commits_response = MagicMock()
    commits_response.status_code = 200
    commits_response.json.return_value = [
        {"sha": "aaa", "commit": {"message": "fix auth login flow", "author": {"name": "A", "date": ""}}},
        {"sha": "bbb", "commit": {"message": "update readme", "author": {"name": "B", "date": ""}}},
    ]

    prs_response = MagicMock()
    prs_response.status_code = 200
    prs_response.json.return_value = [
        {"number": 1, "title": "Auth feature", "state": "open", "user": {"login": "a"}, "html_url": ""},
        {"number": 2, "title": "Update docs", "state": "closed", "user": {"login": "b"}, "html_url": ""},
    ]

    issues_response = MagicMock()
    issues_response.status_code = 200
    issues_response.json.return_value = [
        {"number": 5, "title": "Auth broken on mobile", "state": "open", "user": {"login": "c"}, "html_url": "", "labels": []},
        {"number": 6, "title": "Unrelated", "state": "open", "user": {"login": "d"}, "html_url": "", "labels": []},
    ]

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.side_effect = [commits_response, prs_response, issues_response]
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_get_task_context"](
            mock_context, repo="owner/repo", search_terms="auth"
        )

    data = json.loads(result)
    assert data["success"] is True
    assert len(data["commits"]) == 1  # Only "fix auth login flow"
    assert len(data["pull_requests"]) == 1  # Only "Auth feature"
    assert len(data["issues"]) == 1  # Only "Auth broken on mobile"
    assert data["total_matches"] == 3


@pytest.mark.asyncio
async def test_get_task_context_invalid_repo(github_tools, mock_context):
    result = await github_tools["github_get_task_context"](
        mock_context, repo="invalid-no-slash", search_terms="test"
    )
    data = json.loads(result)
    assert data["success"] is False


@pytest.mark.asyncio
async def test_get_task_context_handles_api_errors_gracefully(github_tools, mock_context):
    """If some endpoints fail, should still return partial results."""
    commits_response = MagicMock()
    commits_response.status_code = 200
    commits_response.json.return_value = [
        {"sha": "aaa", "commit": {"message": "fix: test change", "author": {"name": "A", "date": ""}}},
    ]

    # PRs endpoint returns error
    prs_response = MagicMock()
    prs_response.status_code = 403

    # Issues endpoint returns error
    issues_response = MagicMock()
    issues_response.status_code = 403

    with patch("src.mcp_server.features.github.github_tools.httpx.AsyncClient") as mock_client:
        mock_async_client = AsyncMock()
        mock_async_client.get.side_effect = [commits_response, prs_response, issues_response]
        mock_client.return_value.__aenter__.return_value = mock_async_client

        result = await github_tools["github_get_task_context"](
            mock_context, repo="owner/repo", search_terms="test"
        )

    data = json.loads(result)
    assert data["success"] is True
    assert len(data["commits"]) == 1
    assert len(data["pull_requests"]) == 0  # Failed, so empty
    assert len(data["issues"]) == 0  # Failed, so empty


# --- _parse_repo tests ---

def test_parse_repo_owner_slash_repo():
    from src.mcp_server.features.github.github_tools import _parse_repo
    assert _parse_repo("coleam00/Archon") == ("coleam00", "Archon")


def test_parse_repo_github_url():
    from src.mcp_server.features.github.github_tools import _parse_repo
    assert _parse_repo("https://github.com/coleam00/Archon") == ("coleam00", "Archon")


def test_parse_repo_github_url_with_git():
    from src.mcp_server.features.github.github_tools import _parse_repo
    assert _parse_repo("https://github.com/coleam00/Archon.git") == ("coleam00", "Archon")


def test_parse_repo_invalid():
    from src.mcp_server.features.github.github_tools import _parse_repo
    with pytest.raises(ValueError):
        _parse_repo("invalid")


# --- _github_headers tests ---

def test_github_headers_with_token():
    from src.mcp_server.features.github.github_tools import _github_headers
    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test123"}):
        headers = _github_headers()
    assert headers["Authorization"] == "Bearer ghp_test123"
    assert headers["Accept"] == "application/vnd.github+json"


def test_github_headers_without_token():
    from src.mcp_server.features.github.github_tools import _github_headers
    with patch.dict("os.environ", {}, clear=True):
        headers = _github_headers()
    assert "Authorization" not in headers
