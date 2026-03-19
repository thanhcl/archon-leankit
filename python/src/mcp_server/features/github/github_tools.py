"""
GitHub integration tools for Archon MCP Server.

Queries the GitHub REST API directly to provide repository context
(commits, PRs, issues) for task enrichment during execution.
"""

import json
import logging
import os

import httpx
from mcp.server.fastmcp import Context, FastMCP

from src.mcp_server.utils.error_handling import MCPErrorFormatter

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 10


def _get_github_token() -> str | None:
    """Get GitHub token from environment variables."""
    return os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")


def _github_headers() -> dict[str, str]:
    """Build GitHub API request headers."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_repo(repo: str) -> tuple[str, str]:
    """Parse 'owner/repo' or GitHub URL into (owner, repo).

    Args:
        repo: Repository identifier — either 'owner/repo' or a full GitHub URL.

    Returns:
        Tuple of (owner, repo_name).

    Raises:
        ValueError: If the format is unrecognized.
    """
    import re

    if repo.startswith("http"):
        match = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", repo)
        if not match:
            raise ValueError(f"Invalid GitHub URL: {repo}")
        return match.group(1), match.group(2)

    parts = repo.strip("/").split("/")
    if len(parts) == 2:
        return parts[0], parts[1]

    raise ValueError(f"Expected 'owner/repo' format, got: {repo}")


def _clamp_per_page(per_page: int) -> int:
    """Clamp per_page to GitHub's max of 100."""
    return max(1, min(per_page, MAX_PER_PAGE))


def register_github_tools(mcp: FastMCP):
    """Register GitHub integration tools with the MCP server."""

    @mcp.tool()
    async def github_find_commits(
        ctx: Context,
        repo: str,
        branch: str | None = None,
        author: str | None = None,
        query: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
    ) -> str:
        """
        List recent commits for a GitHub repository.

        Use this to get context about recent changes when working on tasks.

        Args:
            repo: Repository in 'owner/repo' format or full GitHub URL
            branch: Branch name (defaults to repository default branch)
            author: Filter by commit author username
            query: Search term to filter commit messages (client-side filter)
            per_page: Number of commits to return (max 100, default 10)
            page: Page number for pagination

        Returns:
            JSON with recent commits including sha, message, author, date

        Examples:
            github_find_commits(repo="coleam00/Archon")
            github_find_commits(repo="coleam00/Archon", branch="main", per_page=5)
            github_find_commits(repo="coleam00/Archon", query="fix auth")
        """
        try:
            owner, repo_name = _parse_repo(repo)
        except ValueError as e:
            return MCPErrorFormatter.format_error("validation_error", str(e))

        per_page = _clamp_per_page(per_page)

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/commits"
        params: dict[str, str | int] = {"per_page": per_page, "page": page}
        if branch:
            params["sha"] = branch
        if author:
            params["author"] = author

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=_github_headers(), params=params)

            if response.status_code == 404:
                return MCPErrorFormatter.format_error(
                    "not_found",
                    f"Repository {owner}/{repo_name} not found or insufficient permissions",
                    suggestion="Check the repo name and ensure your GITHUB_TOKEN has access",
                )
            if response.status_code == 401:
                return MCPErrorFormatter.format_error(
                    "auth_error",
                    "GitHub authentication failed",
                    suggestion="Set GITHUB_TOKEN or GITHUB_PERSONAL_ACCESS_TOKEN environment variable",
                )
            if response.status_code != 200:
                return MCPErrorFormatter.from_http_error(response, "list commits")

            raw_commits = response.json()
            commits = []
            for c in raw_commits:
                commit_data = c.get("commit", {})
                entry = {
                    "sha": c.get("sha", "")[:7],
                    "full_sha": c.get("sha", ""),
                    "message": commit_data.get("message", ""),
                    "author": commit_data.get("author", {}).get("name", ""),
                    "date": commit_data.get("author", {}).get("date", ""),
                    "url": c.get("html_url", ""),
                }
                commits.append(entry)

            # Client-side message filter
            if query:
                query_lower = query.lower()
                commits = [c for c in commits if query_lower in c["message"].lower()]

            return json.dumps({
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "branch": branch,
                "commits": commits,
                "count": len(commits),
                "page": page,
                "per_page": per_page,
            })

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "list commits")
        except Exception as e:
            logger.error(f"Error listing commits for {repo}: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "list commits")

    @mcp.tool()
    async def github_find_pull_requests(
        ctx: Context,
        repo: str,
        state: str = "open",
        query: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
    ) -> str:
        """
        List pull requests for a GitHub repository.

        Use this to find related PRs when working on tasks.

        Args:
            repo: Repository in 'owner/repo' format or full GitHub URL
            state: PR state filter — 'open', 'closed', or 'all' (default: 'open')
            query: Search term to filter PR titles (client-side filter)
            per_page: Number of PRs to return (max 100, default 10)
            page: Page number for pagination

        Returns:
            JSON with pull requests including number, title, state, author, dates

        Examples:
            github_find_pull_requests(repo="coleam00/Archon")
            github_find_pull_requests(repo="coleam00/Archon", state="closed", query="auth")
        """
        try:
            owner, repo_name = _parse_repo(repo)
        except ValueError as e:
            return MCPErrorFormatter.format_error("validation_error", str(e))

        if state not in ("open", "closed", "all"):
            return MCPErrorFormatter.format_error(
                "validation_error",
                f"Invalid state '{state}'. Must be 'open', 'closed', or 'all'",
            )

        per_page = _clamp_per_page(per_page)

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/pulls"
        params: dict[str, str | int] = {
            "state": state,
            "per_page": per_page,
            "page": page,
            "sort": "updated",
            "direction": "desc",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=_github_headers(), params=params)

            if response.status_code == 404:
                return MCPErrorFormatter.format_error(
                    "not_found",
                    f"Repository {owner}/{repo_name} not found or insufficient permissions",
                    suggestion="Check the repo name and ensure your GITHUB_TOKEN has access",
                )
            if response.status_code != 200:
                return MCPErrorFormatter.from_http_error(response, "list pull requests")

            raw_prs = response.json()
            prs = []
            for pr in raw_prs:
                entry = {
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "state": pr.get("state", ""),
                    "author": pr.get("user", {}).get("login", ""),
                    "created_at": pr.get("created_at", ""),
                    "updated_at": pr.get("updated_at", ""),
                    "head_branch": pr.get("head", {}).get("ref", ""),
                    "base_branch": pr.get("base", {}).get("ref", ""),
                    "url": pr.get("html_url", ""),
                    "draft": pr.get("draft", False),
                    "labels": [label.get("name", "") for label in pr.get("labels", [])],
                }
                prs.append(entry)

            # Client-side title filter
            if query:
                query_lower = query.lower()
                prs = [p for p in prs if query_lower in p["title"].lower()]

            return json.dumps({
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "state": state,
                "pull_requests": prs,
                "count": len(prs),
                "page": page,
                "per_page": per_page,
            })

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "list pull requests")
        except Exception as e:
            logger.error(f"Error listing PRs for {repo}: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "list pull requests")

    @mcp.tool()
    async def github_find_issues(
        ctx: Context,
        repo: str,
        state: str = "open",
        labels: str | None = None,
        query: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
    ) -> str:
        """
        List issues for a GitHub repository.

        Use this to find related issues when working on tasks.
        Note: GitHub's issues endpoint also returns pull requests.
        This tool filters them out — use github_find_pull_requests for PRs.

        Args:
            repo: Repository in 'owner/repo' format or full GitHub URL
            state: Issue state filter — 'open', 'closed', or 'all' (default: 'open')
            labels: Comma-separated list of label names to filter by
            query: Search term to filter issue titles (client-side filter)
            per_page: Number of issues to return (max 100, default 10)
            page: Page number for pagination

        Returns:
            JSON with issues including number, title, state, author, labels

        Examples:
            github_find_issues(repo="coleam00/Archon")
            github_find_issues(repo="coleam00/Archon", state="all", labels="bug")
            github_find_issues(repo="coleam00/Archon", query="authentication")
        """
        try:
            owner, repo_name = _parse_repo(repo)
        except ValueError as e:
            return MCPErrorFormatter.format_error("validation_error", str(e))

        if state not in ("open", "closed", "all"):
            return MCPErrorFormatter.format_error(
                "validation_error",
                f"Invalid state '{state}'. Must be 'open', 'closed', or 'all'",
            )

        per_page = _clamp_per_page(per_page)

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/issues"
        params: dict[str, str | int] = {
            "state": state,
            "per_page": per_page,
            "page": page,
            "sort": "updated",
            "direction": "desc",
        }
        if labels:
            params["labels"] = labels

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=_github_headers(), params=params)

            if response.status_code == 404:
                return MCPErrorFormatter.format_error(
                    "not_found",
                    f"Repository {owner}/{repo_name} not found or insufficient permissions",
                    suggestion="Check the repo name and ensure your GITHUB_TOKEN has access",
                )
            if response.status_code != 200:
                return MCPErrorFormatter.from_http_error(response, "list issues")

            raw_issues = response.json()
            issues = []
            for issue in raw_issues:
                # Filter out pull requests (GitHub includes them in /issues)
                if "pull_request" in issue:
                    continue

                entry = {
                    "number": issue.get("number"),
                    "title": issue.get("title", ""),
                    "state": issue.get("state", ""),
                    "author": issue.get("user", {}).get("login", ""),
                    "created_at": issue.get("created_at", ""),
                    "updated_at": issue.get("updated_at", ""),
                    "labels": [label.get("name", "") for label in issue.get("labels", [])],
                    "url": issue.get("html_url", ""),
                    "comments": issue.get("comments", 0),
                }
                issues.append(entry)

            # Client-side title filter
            if query:
                query_lower = query.lower()
                issues = [i for i in issues if query_lower in i["title"].lower()]

            return json.dumps({
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "state": state,
                "issues": issues,
                "count": len(issues),
                "page": page,
                "per_page": per_page,
            })

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "list issues")
        except Exception as e:
            logger.error(f"Error listing issues for {repo}: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "list issues")

    @mcp.tool()
    async def github_get_task_context(
        ctx: Context,
        repo: str,
        search_terms: str,
        commits_count: int = 5,
        prs_count: int = 5,
        issues_count: int = 5,
    ) -> str:
        """
        Get enriched GitHub context for a task by searching commits, PRs, and issues.

        This is a convenience tool that searches across all three GitHub entities
        in a single call to quickly build context for task execution.

        Args:
            repo: Repository in 'owner/repo' format or full GitHub URL
            search_terms: Keywords to search for across commits, PRs, and issues
            commits_count: Max recent commits to fetch (default 5)
            prs_count: Max recent PRs to fetch (default 5)
            issues_count: Max recent issues to fetch (default 5)

        Returns:
            JSON with matching commits, pull_requests, and issues

        Examples:
            github_get_task_context(repo="coleam00/Archon", search_terms="authentication")
            github_get_task_context(repo="coleam00/Archon", search_terms="MCP tools", commits_count=10)
        """
        try:
            owner, repo_name = _parse_repo(repo)
        except ValueError as e:
            return MCPErrorFormatter.format_error("validation_error", str(e))

        headers = _github_headers()
        search_lower = search_terms.lower()
        result: dict = {
            "success": True,
            "repo": f"{owner}/{repo_name}",
            "search_terms": search_terms,
            "commits": [],
            "pull_requests": [],
            "issues": [],
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Fetch commits
                commits_resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/commits",
                    headers=headers,
                    params={"per_page": min(commits_count * 3, MAX_PER_PAGE)},
                )
                if commits_resp.status_code == 200:
                    for c in commits_resp.json():
                        msg = c.get("commit", {}).get("message", "")
                        if search_lower in msg.lower():
                            result["commits"].append({
                                "sha": c.get("sha", "")[:7],
                                "message": msg.split("\n")[0],  # First line only
                                "author": c.get("commit", {}).get("author", {}).get("name", ""),
                                "date": c.get("commit", {}).get("author", {}).get("date", ""),
                            })
                            if len(result["commits"]) >= commits_count:
                                break

                # Fetch PRs (open + recently closed)
                prs_resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/pulls",
                    headers=headers,
                    params={"state": "all", "per_page": min(prs_count * 3, MAX_PER_PAGE), "sort": "updated", "direction": "desc"},
                )
                if prs_resp.status_code == 200:
                    for pr in prs_resp.json():
                        title = pr.get("title", "")
                        if search_lower in title.lower():
                            result["pull_requests"].append({
                                "number": pr.get("number"),
                                "title": title,
                                "state": pr.get("state", ""),
                                "author": pr.get("user", {}).get("login", ""),
                                "url": pr.get("html_url", ""),
                            })
                            if len(result["pull_requests"]) >= prs_count:
                                break

                # Fetch issues
                issues_resp = await client.get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/issues",
                    headers=headers,
                    params={"state": "all", "per_page": min(issues_count * 3, MAX_PER_PAGE), "sort": "updated", "direction": "desc"},
                )
                if issues_resp.status_code == 200:
                    for issue in issues_resp.json():
                        if "pull_request" in issue:
                            continue
                        title = issue.get("title", "")
                        if search_lower in title.lower():
                            result["issues"].append({
                                "number": issue.get("number"),
                                "title": title,
                                "state": issue.get("state", ""),
                                "author": issue.get("user", {}).get("login", ""),
                                "url": issue.get("html_url", ""),
                            })
                            if len(result["issues"]) >= issues_count:
                                break

            result["total_matches"] = len(result["commits"]) + len(result["pull_requests"]) + len(result["issues"])
            return json.dumps(result)

        except httpx.RequestError as e:
            return MCPErrorFormatter.from_exception(e, "get task context")
        except Exception as e:
            logger.error(f"Error getting task context for {repo}: {e}", exc_info=True)
            return MCPErrorFormatter.from_exception(e, "get task context")
