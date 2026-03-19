# GitHub MCP Feature Module Integration

## Overview

This document covers the standard approach for adding a GitHub feature module to the existing Archon MCP server at `python/src/mcp_server/`.

The Archon MCP server uses **FastMCP** with **httpx** for all external HTTP calls. GitHub integration follows the same pattern: a feature module registered in `register_modules()` inside `mcp_server.py`. GitHub API calls go directly to `api.github.com` via httpx using a Personal Access Token — no separate HTTP service proxy needed (unlike other Archon features that proxy through the internal API service).

---

## Architecture Fit

Archon's MCP server already uses httpx for outbound HTTP. GitHub integration is a pure addition:

```
mcp_server.py
  └── register_modules()
        └── from src.mcp_server.features.github import register_github_tools
              └── github_tools.py  ← new file
```

The GitHub module does NOT route through the internal Archon API service (`get_api_url()`). It calls `https://api.github.com` directly using a token from env.

---

## File Structure to Create

```
python/src/mcp_server/features/github/
├── __init__.py          # exports register_github_tools
└── github_tools.py      # tool implementations
```

---

## Environment Variable

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

Required scopes:
- `repo` — full access to private repos
- `public_repo` — public repos only
- `read:org` — for org-level data

---

## The 26 Tools from the Official GitHub MCP Server

The official `ghcr.io/github/github-mcp-server` (referenced in the awesome-llm-apps agent) exposes these tool groups, selectable via `GITHUB_TOOLSETS` env var:

### Repos toolset
| Tool | Description |
|------|-------------|
| `create_repository` | Initialize new repo |
| `fork_repository` | Fork an existing repo |
| `get_file_contents` | Retrieve file or directory contents |
| `create_or_update_file` | Create/update a single file |
| `push_files` | Commit multiple files in one operation |
| `create_branch` | Create branch from source branch |
| `search_repositories` | Query repos with pagination |
| `list_commits` | Retrieve branch commit history |

### Issues toolset
| Tool | Description |
|------|-------------|
| `create_issue` | New issue with title, body, assignees, labels |
| `list_issues` | Filter by state, labels, date ranges |
| `update_issue` | Modify existing issue properties |
| `get_issue` | Get specific issue details |
| `add_issue_comment` | Post comment on issue |

### Pull requests toolset
| Tool | Description |
|------|-------------|
| `create_pull_request` | Open PR with draft/maintainer options |
| `list_pull_requests` | Filter by state, branch, sort |
| `get_pull_request` | Fetch PR details including diff status |
| `get_pull_request_files` | List changed files with patch info |
| `get_pull_request_status` | Retrieve combined status checks |
| `get_pull_request_comments` | Access review comments |
| `get_pull_request_reviews` | Fetch review details and states |
| `create_pull_request_review` | Submit APPROVE/REQUEST_CHANGES/COMMENT |
| `merge_pull_request` | Merge with merge/squash/rebase method |
| `update_pull_request_branch` | Sync PR branch with base |

### Search toolset
| Tool | Description |
|------|-------------|
| `search_code` | Find code across repos |
| `search_issues` | Locate issues/PRs with filtering |
| `search_users` | Discover users |

---

## Implementation Pattern

### `__init__.py`

```python
from .github_tools import register_github_tools

__all__ = ["register_github_tools"]
```

### `github_tools.py` — Full Pattern

```python
"""
GitHub integration tools for Archon MCP Server.

Provides read and write access to GitHub repositories, issues, and pull requests
using the GitHub REST API via httpx with Personal Access Token authentication.
"""

import json
import logging
import os
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp import Context, FastMCP

from src.mcp_server.utils.error_handling import MCPErrorFormatter

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = httpx.Timeout(30.0)


def _get_github_token() -> str | None:
    """Retrieve GitHub token from environment."""
    return os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")


def _github_headers() -> dict:
    """Build standard GitHub API request headers."""
    token = _get_github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_url(path: str) -> str:
    """Construct full GitHub API URL from path."""
    return urljoin(GITHUB_API_BASE, path)


def register_github_tools(mcp: FastMCP) -> None:
    """Register GitHub tools with the MCP server."""

    @mcp.tool()
    async def get_github_issue(
        ctx: Context,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> str:
        """
        Get a specific GitHub issue by number.

        Args:
            owner: Repository owner (user or org)
            repo: Repository name
            issue_number: Issue number

        Returns:
            JSON with issue details (title, body, state, labels, assignees, comments count)
        """
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    _github_url(f"/repos/{owner}/{repo}/issues/{issue_number}"),
                    headers=_github_headers(),
                )
                if response.status_code == 200:
                    return json.dumps({"success": True, "issue": response.json()})
                elif response.status_code == 404:
                    return MCPErrorFormatter.format_error(
                        "not_found",
                        f"Issue #{issue_number} not found in {owner}/{repo}",
                        http_status=404,
                    )
                elif response.status_code == 401:
                    return MCPErrorFormatter.format_error(
                        "auth_error",
                        "GitHub token invalid or missing",
                        suggestion="Set GITHUB_PERSONAL_ACCESS_TOKEN in environment",
                        http_status=401,
                    )
                else:
                    return MCPErrorFormatter.from_http_error(response, "get GitHub issue")
        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "get GitHub issue")

    @mcp.tool()
    async def list_github_issues(
        ctx: Context,
        owner: str,
        repo: str,
        state: str = "open",          # "open" | "closed" | "all"
        labels: str | None = None,     # comma-separated label names
        per_page: int = 20,
        page: int = 1,
    ) -> str:
        """
        List issues in a GitHub repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: Filter by state — "open", "closed", or "all"
            labels: Comma-separated label names to filter by
            per_page: Results per page (max 100)
            page: Page number for pagination

        Returns:
            JSON array of issues
        """
        try:
            params: dict = {"state": state, "per_page": min(per_page, 100), "page": page}
            if labels:
                params["labels"] = labels

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    _github_url(f"/repos/{owner}/{repo}/issues"),
                    headers=_github_headers(),
                    params=params,
                )
                if response.status_code == 200:
                    return json.dumps({
                        "success": True,
                        "issues": response.json(),
                        "page": page,
                        "per_page": per_page,
                    })
                else:
                    return MCPErrorFormatter.from_http_error(response, "list GitHub issues")
        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "list GitHub issues")

    @mcp.tool()
    async def list_github_pull_requests(
        ctx: Context,
        owner: str,
        repo: str,
        state: str = "open",          # "open" | "closed" | "all"
        base: str | None = None,       # Filter by base branch
        per_page: int = 20,
        page: int = 1,
    ) -> str:
        """
        List pull requests in a GitHub repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: PR state — "open", "closed", or "all"
            base: Filter PRs targeting this base branch
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            JSON array of pull requests
        """
        try:
            params: dict = {"state": state, "per_page": min(per_page, 100), "page": page}
            if base:
                params["base"] = base

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    _github_url(f"/repos/{owner}/{repo}/pulls"),
                    headers=_github_headers(),
                    params=params,
                )
                if response.status_code == 200:
                    return json.dumps({
                        "success": True,
                        "pull_requests": response.json(),
                        "page": page,
                        "per_page": per_page,
                    })
                else:
                    return MCPErrorFormatter.from_http_error(response, "list GitHub pull requests")
        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "list GitHub pull requests")

    @mcp.tool()
    async def list_github_commits(
        ctx: Context,
        owner: str,
        repo: str,
        branch: str = "main",
        per_page: int = 20,
        page: int = 1,
    ) -> str:
        """
        List commits on a branch of a GitHub repository.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch name (default: "main")
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            JSON array of commits with sha, message, author, date
        """
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    _github_url(f"/repos/{owner}/{repo}/commits"),
                    headers=_github_headers(),
                    params={"sha": branch, "per_page": min(per_page, 100), "page": page},
                )
                if response.status_code == 200:
                    return json.dumps({
                        "success": True,
                        "commits": response.json(),
                        "branch": branch,
                        "page": page,
                    })
                else:
                    return MCPErrorFormatter.from_http_error(response, "list GitHub commits")
        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "list GitHub commits")

    @mcp.tool()
    async def search_github_issues(
        ctx: Context,
        query: str,
        per_page: int = 20,
        page: int = 1,
    ) -> str:
        """
        Search GitHub issues and pull requests using GitHub search syntax.

        Args:
            query: GitHub search query (e.g. "repo:owner/repo label:bug is:open")
            per_page: Results per page (max 100)
            page: Page number

        Returns:
            JSON with total_count and array of matching issues/PRs

        Query examples:
            "repo:owner/repo is:issue is:open label:bug"
            "repo:owner/repo is:pr is:merged base:main"
            "repo:owner/repo assignee:username is:open"
        """
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    _github_url("/search/issues"),
                    headers=_github_headers(),
                    params={"q": query, "per_page": min(per_page, 100), "page": page},
                )
                if response.status_code == 200:
                    data = response.json()
                    return json.dumps({
                        "success": True,
                        "total_count": data.get("total_count", 0),
                        "items": data.get("items", []),
                        "page": page,
                    })
                elif response.status_code == 422:
                    return MCPErrorFormatter.format_error(
                        "invalid_query",
                        "GitHub search query is invalid",
                        suggestion="Check search syntax at https://docs.github.com/en/search-github",
                        http_status=422,
                    )
                else:
                    return MCPErrorFormatter.from_http_error(response, "search GitHub issues")
        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "search GitHub issues")
```

---

### Registering in `mcp_server.py`

Add inside `register_modules()` following the exact pattern used by other modules:

```python
# GitHub Integration Tools
try:
    from src.mcp_server.features.github import register_github_tools

    register_github_tools(mcp)
    modules_registered += 1
    logger.info("GitHub tools registered")
except ImportError as e:
    logger.warning(f"GitHub tools module not available (optional): {e}")
except (SyntaxError, NameError, AttributeError) as e:
    logger.error(f"Code error in GitHub tools - MUST FIX: {e}")
    logger.error(traceback.format_exc())
    raise
except Exception as e:
    logger.error(f"Failed to register GitHub tools: {e}")
    logger.error(traceback.format_exc())
```

---

## Key API Details

### Authentication Header
```
Authorization: Bearer ghp_xxxx
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

### Rate Limits
- Authenticated: 5,000 requests/hour
- Search API: 30 requests/minute (separate quota)
- Unauthenticated: 60 requests/hour (avoid this)

Check remaining quota from response headers:
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset` (Unix timestamp)

### Pagination
GitHub uses `Link` response headers for pagination, not a `next_page` field in the body. For MCP tools, accept `page` + `per_page` params and pass them through. The response body is always an array.

### Common Status Codes
| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 204 | No content (delete success) |
| 301 | Moved permanently (follow redirect) |
| 304 | Not modified (ETag match) |
| 401 | Bad credentials |
| 403 | Forbidden (rate limit or no permission) |
| 404 | Not found or no access to private repo |
| 422 | Validation failed |
| 429 | Secondary rate limit hit |

---

## PyGithub vs httpx

Two valid approaches exist in the Python ecosystem:

| Approach | Library | Use When |
|----------|---------|----------|
| Direct HTTP | `httpx` (async) | Already used by Archon; best fit for this codebase |
| OO wrapper | `PyGithub` | Better for complex object graphs (milestones, pagination objects) |

**Archon should use `httpx`** — it's already a dependency, matches the async pattern of all other tools, and avoids adding `PyGithub` as a dependency.

The PyGithub pattern (from `AstroMined/pygithub-mcp-server`) uses a singleton `GitHubClient` class with `Auth.Token(token)` and `Github(auth=auth)`. That pattern is worth knowing but is not needed here.

---

## Gotchas

- **Private repo 404**: GitHub returns 404 (not 403) for private repos when the token lacks access. Always check for 404 in error messages noting "or insufficient permissions".
- **Issues endpoint returns PRs too**: `GET /repos/{owner}/{repo}/issues` includes pull requests. Filter with `params["pulls"] = False` or check `"pull_request"` key in each result.
- **Search rate limit is separate**: The `/search/issues` endpoint has its own 30 req/min limit separate from the main 5000/hr limit.
- **Token env variable naming**: The official GitHub MCP server uses `GITHUB_PERSONAL_ACCESS_TOKEN`. The awesome-llm-apps agent maps `GITHUB_TOKEN` -> `GITHUB_PERSONAL_ACCESS_TOKEN`. Support both in `_get_github_token()`.
- **API version header**: Always send `X-GitHub-Api-Version: 2022-11-28`. Omitting it works today but GitHub may deprecate behavior.
- **`per_page` max is 100**: Clamp to `min(per_page, 100)` before sending to avoid 422 errors.

---

## Reference Implementations

- Official GitHub MCP Server (Docker): `ghcr.io/github/github-mcp-server` — used via `StdioServerParameters` with Docker
- PyGithub MCP Server: `github.com/AstroMined/pygithub-mcp-server` — modular pattern with configurable tool groups
- awesome-llm-apps agent: `Shubhamsaboo/awesome-llm-apps/mcp_ai_agents/github_mcp_agent/github_agent.py` — uses `agno` + `MCPTools` + `StdioServerParameters` to wrap the official Docker image

Sources:
- [Github MCP Server tools listing](https://mcp.so/server/github/modelcontextprotocol?tab=tools)
- [PyGithub MCP Server](https://github.com/AstroMined/pygithub-mcp-server)
- [awesome-llm-apps github_mcp_agent](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/mcp_ai_agents/github_mcp_agent)
- [GitHub REST API reference](https://docs.github.com/en/rest)
