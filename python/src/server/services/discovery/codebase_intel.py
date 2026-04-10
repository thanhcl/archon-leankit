"""
Codebase Intelligence Cache for LeanKit V3.

Generates and maintains a queryable index of project structure, APIs,
dependencies, and conventions. Reduces cold-start context building for
each agent by providing pre-computed project knowledge.

Adopted from GSD's .planning/intel/ pattern:
- File tree with role annotations
- API endpoint catalog
- Import dependency graph
- Convention patterns (naming, structure)
- Stack summary

Stored as wiki pages for queryability via Wiki KB (Workstream L).

Usage:
    intel = CodebaseIntelligence(supabase_client=client)
    report = await intel.generate(project_id="proj-123", repo_path="/path/to/repo")
    summary = intel.get_compressed_summary(project_id="proj-123", max_tokens=1000)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...config.logfire_config import get_logger
from ...utils import get_supabase_client

logger = get_logger(__name__)

WIKI_PAGES_TABLE = "archon_wiki_pages"
INTEL_PAGE_TYPE = "entity"  # Wiki page type for intel pages
INTEL_TAG = "codebase-intel"
STALE_HOURS = 24
STALE_COMMIT_THRESHOLD = 10

# File role classifications
ROLE_MAP: dict[str, str] = {
    "component": "React/UI component",
    "service": "Business logic service",
    "route": "API route/endpoint",
    "test": "Test file",
    "config": "Configuration",
    "migration": "Database migration",
    "model": "Data model/schema",
    "util": "Utility/helper",
    "hook": "React hook",
    "middleware": "Middleware/interceptor",
    "script": "Build/deploy script",
    "type": "Type definitions",
    "style": "Stylesheet",
}

# Common project file patterns for role detection
_ROLE_PATTERNS: dict[str, list[str]] = {
    "test": [".test.", ".spec.", "_test.", "__test__", "test_"],
    "config": ["config.", ".config.", "tsconfig", "package.json", ".eslintrc", "vite.config", "jest.config"],
    "migration": ["migration", "migrate"],
    "route": ["route", "api/", "endpoint", "controller"],
    "service": ["service", "services/"],
    "model": ["model", "schema", "entity"],
    "hook": ["use", "hooks/"],
    "component": ["component", "components/", ".tsx", ".jsx"],
    "middleware": ["middleware", "interceptor"],
    "script": ["scripts/", ".sh", ".mjs", "Makefile"],
    "type": [".d.ts", "types.", "interfaces."],
    "style": [".css", ".scss", ".less", ".styled"],
    "util": ["util", "helper", "lib/"],
}


@dataclass
class FileEntry:
    """A file in the project tree with role annotation."""

    path: str
    role: str
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "role": self.role, "size": self.size_bytes}


@dataclass
class IntelReport:
    """Complete codebase intelligence report."""

    project_id: str
    generated_at: str = ""
    stack: dict[str, Any] = field(default_factory=dict)
    file_tree: list[FileEntry] = field(default_factory=list)
    conventions: dict[str, str] = field(default_factory=dict)
    summary: str = ""

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "stack": self.stack,
            "file_count": len(self.file_tree),
            "conventions": self.conventions,
            "summary": self.summary,
            "roles": self._role_summary(),
        }

    def _role_summary(self) -> dict[str, int]:
        """Count files by role."""
        counts: dict[str, int] = {}
        for entry in self.file_tree:
            counts[entry.role] = counts.get(entry.role, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def to_compressed_summary(self, max_tokens: int = 1000) -> str:
        """Generate a compressed summary suitable for prompt injection.

        Prioritizes: stack summary > conventions > role distribution.
        Token budget is approximate (4 chars ~ 1 token).
        """
        max_chars = max_tokens * 4
        parts: list[str] = []

        # Stack summary (always include)
        if self.stack:
            stack_lines = ["## Project Stack"]
            for key, value in self.stack.items():
                stack_lines.append(f"- {key}: {value}")
            parts.append("\n".join(stack_lines))

        # Conventions (always include)
        if self.conventions:
            conv_lines = ["## Conventions"]
            for key, value in self.conventions.items():
                conv_lines.append(f"- {key}: {value}")
            parts.append("\n".join(conv_lines))

        # Role distribution
        roles = self._role_summary()
        if roles:
            role_lines = ["## File Structure"]
            for role, count in list(roles.items())[:8]:
                role_lines.append(f"- {role}: {count} files")
            parts.append("\n".join(role_lines))

        result = "\n\n".join(parts)

        # Truncate to budget
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(truncated)"

        return result


class CodebaseIntelligence:
    """Generates and manages codebase intelligence for projects."""

    def __init__(self, supabase_client=None):
        self._client = supabase_client or get_supabase_client()

    async def generate(
        self,
        project_id: str,
        repo_path: str,
    ) -> IntelReport:
        """Generate a fresh codebase intelligence report.

        Scans the repository to build:
        - Stack summary (framework, language, package manager, test runner)
        - File tree with role annotations
        - Convention patterns (naming, structure)

        Args:
            project_id: Project ID to associate the report with.
            repo_path: Absolute path to the repository root.

        Returns:
            IntelReport with all gathered intelligence.
        """
        repo = Path(repo_path)
        if not repo.is_dir():
            logger.warning(f"Repo path not found: {repo_path}")
            return IntelReport(project_id=project_id, summary="Repository not found")

        # Detect stack
        stack = self._detect_stack(repo)

        # Scan file tree
        file_tree = self._scan_file_tree(repo)

        # Detect conventions
        conventions = self._detect_conventions(file_tree, repo)

        # Build summary
        summary_parts = []
        if stack.get("language"):
            summary_parts.append(f"Language: {stack['language']}")
        if stack.get("framework"):
            summary_parts.append(f"Framework: {stack['framework']}")
        if stack.get("package_manager"):
            summary_parts.append(f"Package manager: {stack['package_manager']}")
        summary_parts.append(f"Files: {len(file_tree)}")
        summary = " | ".join(summary_parts)

        report = IntelReport(
            project_id=project_id,
            stack=stack,
            file_tree=file_tree,
            conventions=conventions,
            summary=summary,
        )

        # Store as wiki page
        await self._store_as_wiki_page(project_id, report)

        logger.info(
            f"Codebase intel generated | project_id={project_id} | "
            f"files={len(file_tree)} | stack={stack.get('language', 'unknown')}"
        )

        return report

    async def get_or_generate(
        self,
        project_id: str,
        repo_path: str,
    ) -> IntelReport:
        """Get cached intel or generate fresh if stale.

        Args:
            project_id: Project ID.
            repo_path: Repository path (needed for generation).

        Returns:
            IntelReport from cache or freshly generated.
        """
        cached = await self._get_cached(project_id)
        if cached and not self._is_stale(cached):
            return self._parse_cached(project_id, cached)

        return await self.generate(project_id, repo_path)

    async def get_compressed_summary(
        self,
        project_id: str,
        max_tokens: int = 1000,
    ) -> str | None:
        """Get compressed intel summary for prompt injection.

        Returns None if no intel is available.
        """
        cached = await self._get_cached(project_id)
        if not cached:
            return None

        report = self._parse_cached(project_id, cached)
        return report.to_compressed_summary(max_tokens)

    # -- Stack Detection ---------------------------------------------------

    def _detect_stack(self, repo: Path) -> dict[str, Any]:
        """Detect project stack from config files."""
        stack: dict[str, Any] = {}

        # Package manager
        if (repo / "pnpm-lock.yaml").exists() or (repo / "pnpm-workspace.yaml").exists():
            stack["package_manager"] = "pnpm"
        elif (repo / "yarn.lock").exists():
            stack["package_manager"] = "yarn"
        elif (repo / "package-lock.json").exists():
            stack["package_manager"] = "npm"
        elif (repo / "Pipfile.lock").exists():
            stack["package_manager"] = "pipenv"
        elif (repo / "poetry.lock").exists():
            stack["package_manager"] = "poetry"
        elif (repo / "requirements.txt").exists():
            stack["package_manager"] = "pip"
        elif (repo / "go.sum").exists():
            stack["package_manager"] = "go modules"
        elif (repo / "Cargo.lock").exists():
            stack["package_manager"] = "cargo"

        # Language
        if (repo / "tsconfig.json").exists():
            stack["language"] = "TypeScript"
        elif (repo / "package.json").exists():
            stack["language"] = "JavaScript"
        elif (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
            stack["language"] = "Python"
        elif (repo / "go.mod").exists():
            stack["language"] = "Go"
        elif (repo / "Cargo.toml").exists():
            stack["language"] = "Rust"
        elif (repo / "pom.xml").exists() or (repo / "build.gradle").exists():
            stack["language"] = "Java"

        # Framework (read package.json or pyproject.toml)
        pkg_json = repo / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps:
                    stack["framework"] = "Next.js"
                elif "react" in deps:
                    stack["framework"] = "React"
                elif "vue" in deps:
                    stack["framework"] = "Vue"
                elif "svelte" in deps:
                    stack["framework"] = "Svelte"
                elif "express" in deps:
                    stack["framework"] = "Express"
                elif "fastify" in deps:
                    stack["framework"] = "Fastify"

                # Test runner
                if "jest" in deps:
                    stack["test_runner"] = "Jest"
                elif "vitest" in deps:
                    stack["test_runner"] = "Vitest"
                elif "mocha" in deps:
                    stack["test_runner"] = "Mocha"
            except (json.JSONDecodeError, OSError):
                pass

        pyproject = repo / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding="utf-8")
                if "fastapi" in content.lower():
                    stack["framework"] = "FastAPI"
                elif "django" in content.lower():
                    stack["framework"] = "Django"
                elif "flask" in content.lower():
                    stack["framework"] = "Flask"
                if "pytest" in content.lower():
                    stack["test_runner"] = "pytest"
            except OSError:
                pass

        # Monorepo detection
        if (repo / "pnpm-workspace.yaml").exists() or (repo / "lerna.json").exists():
            stack["monorepo"] = True
        elif (repo / "packages").is_dir() or (repo / "apps").is_dir():
            stack["monorepo"] = True

        return stack

    # -- File Tree Scanning ------------------------------------------------

    def _scan_file_tree(
        self,
        repo: Path,
        max_files: int = 500,
    ) -> list[FileEntry]:
        """Scan repository file tree with role annotations.

        Skips hidden dirs, node_modules, __pycache__, .git, etc.
        Limits to max_files to prevent memory issues on large repos.
        """
        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".next", ".nuxt",
            "dist", "build", ".cache", ".tox", ".mypy_cache",
            ".pytest_cache", "coverage", ".turbo", ".vercel",
            "venv", ".venv", "env", ".leankit-worktrees",
        }
        skip_extensions = {
            ".pyc", ".pyo", ".class", ".o", ".so", ".dylib",
            ".wasm", ".map", ".min.js", ".min.css",
            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
            ".woff", ".woff2", ".ttf", ".eot",
            ".lock", ".log",
        }

        entries: list[FileEntry] = []

        for root, dirs, files in os.walk(repo):
            # Skip hidden and generated directories
            dirs[:] = [
                d for d in dirs
                if d not in skip_dirs and not d.startswith(".")
            ]

            for fname in files:
                if len(entries) >= max_files:
                    break

                fpath = Path(root) / fname
                ext = fpath.suffix.lower()

                if ext in skip_extensions:
                    continue
                if fname.startswith("."):
                    continue

                rel_path = str(fpath.relative_to(repo))
                role = self._classify_file_role(rel_path)

                try:
                    size = fpath.stat().st_size
                except OSError:
                    size = 0

                entries.append(FileEntry(path=rel_path, role=role, size_bytes=size))

            if len(entries) >= max_files:
                break

        return entries

    @staticmethod
    def _classify_file_role(path: str) -> str:
        """Classify file role based on path patterns."""
        path_lower = path.lower()

        for role, patterns in _ROLE_PATTERNS.items():
            for pattern in patterns:
                if pattern in path_lower:
                    return role

        # Default by extension
        if path_lower.endswith((".ts", ".tsx", ".js", ".jsx")):
            return "source"
        if path_lower.endswith((".py",)):
            return "source"
        if path_lower.endswith((".md", ".txt", ".rst")):
            return "documentation"
        if path_lower.endswith((".json", ".yaml", ".yml", ".toml")):
            return "config"
        if path_lower.endswith((".sql",)):
            return "migration"

        return "other"

    # -- Convention Detection ----------------------------------------------

    def _detect_conventions(
        self,
        file_tree: list[FileEntry],
        repo: Path,
    ) -> dict[str, str]:
        """Detect naming and structure conventions from file tree."""
        conventions: dict[str, str] = {}

        if not file_tree:
            return conventions

        # Naming convention detection
        source_files = [f for f in file_tree if f.role in ("source", "component", "service", "route")]
        if source_files:
            sample_names = [Path(f.path).stem for f in source_files[:30]]
            camel_count = sum(1 for n in sample_names if n[0].islower() and any(c.isupper() for c in n[1:]))
            pascal_count = sum(1 for n in sample_names if n[0].isupper() and any(c.islower() for c in n[1:]))
            snake_count = sum(1 for n in sample_names if "_" in n and n == n.lower())
            kebab_count = sum(1 for n in sample_names if "-" in n and n == n.lower())

            max_count = max(camel_count, pascal_count, snake_count, kebab_count, 1)
            if camel_count == max_count:
                conventions["naming"] = "camelCase"
            elif pascal_count == max_count:
                conventions["naming"] = "PascalCase"
            elif snake_count == max_count:
                conventions["naming"] = "snake_case"
            elif kebab_count == max_count:
                conventions["naming"] = "kebab-case"

        # Structure convention
        top_dirs = set()
        for f in file_tree:
            parts = f.path.split("/")
            if len(parts) > 1:
                top_dirs.add(parts[0])

        feature_dirs = {"features", "modules", "domains"}
        layer_dirs = {"components", "services", "routes", "models", "utils", "hooks"}

        if feature_dirs & top_dirs:
            conventions["structure"] = "feature-based"
        elif len(layer_dirs & top_dirs) >= 2:
            conventions["structure"] = "layer-based"
        elif "src" in top_dirs:
            conventions["structure"] = "src-based"

        # Test convention
        test_files = [f for f in file_tree if f.role == "test"]
        if test_files:
            colocated = sum(1 for f in test_files if "/test" not in f.path.lower() and "tests/" not in f.path.lower())
            separated = len(test_files) - colocated
            if colocated > separated:
                conventions["tests"] = "co-located"
            else:
                conventions["tests"] = "separate directory"

        return conventions

    # -- Storage -----------------------------------------------------------

    async def _store_as_wiki_page(
        self,
        project_id: str,
        report: IntelReport,
    ) -> None:
        """Store intel report as a wiki page for queryability."""
        try:
            title = f"Codebase Intelligence: {project_id}"
            content = json.dumps(report.to_dict(), indent=2)

            # Upsert: find existing page or create new
            existing = (
                self._client.table(WIKI_PAGES_TABLE)
                .select("id")
                .eq("project_id", project_id)
                .eq("title", title)
                .maybe_single()
                .execute()
            )

            page_data = {
                "project_id": project_id,
                "title": title,
                "content": content,
                "type": INTEL_PAGE_TYPE,
                "status": "active",
                "updated_at": datetime.now().isoformat(),
            }

            if existing and existing.data:
                self._client.table(WIKI_PAGES_TABLE).update(page_data).eq(
                    "id", existing.data["id"]
                ).execute()
            else:
                page_data["created_at"] = datetime.now().isoformat()
                self._client.table(WIKI_PAGES_TABLE).insert(page_data).execute()

        except Exception as e:
            logger.warning(f"Failed to store intel as wiki page: {e}")

    async def _get_cached(self, project_id: str) -> dict[str, Any] | None:
        """Get cached intel from wiki pages."""
        try:
            title = f"Codebase Intelligence: {project_id}"
            resp = (
                self._client.table(WIKI_PAGES_TABLE)
                .select("content, updated_at")
                .eq("project_id", project_id)
                .eq("title", title)
                .eq("status", "active")
                .maybe_single()
                .execute()
            )
            if resp and resp.data:
                content = resp.data.get("content", "{}")
                try:
                    data = json.loads(content)
                    data["_updated_at"] = resp.data.get("updated_at")
                    return data
                except json.JSONDecodeError:
                    return None
            return None
        except Exception as e:
            logger.warning(f"Failed to get cached intel: {e}")
            return None

    def _is_stale(self, cached: dict[str, Any]) -> bool:
        """Check if cached intel is stale (>24 hours old)."""
        updated_at = cached.get("_updated_at") or cached.get("generated_at")
        if not updated_at:
            return True

        try:
            if isinstance(updated_at, str):
                updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                updated = updated_at
            age = datetime.now() - updated
            return age > timedelta(hours=STALE_HOURS)
        except (ValueError, TypeError):
            return True

    @staticmethod
    def _parse_cached(project_id: str, cached: dict[str, Any]) -> IntelReport:
        """Parse cached dict back into IntelReport."""
        return IntelReport(
            project_id=project_id,
            generated_at=cached.get("generated_at", ""),
            stack=cached.get("stack", {}),
            file_tree=[],  # Don't reconstruct full tree from cache
            conventions=cached.get("conventions", {}),
            summary=cached.get("summary", ""),
        )
