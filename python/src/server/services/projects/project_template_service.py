"""Project template service — static template definitions with task packs and policy defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TemplateTaskDefinition:
    """A pre-defined task included in a project template's task pack."""

    key: str
    title: str
    description: str
    task_type: str  # feature, test, docs, improvement
    priority: str  # high, medium, low
    complexity: str  # simple, complex
    execution_prompt: str
    acceptance_criteria: list[str]
    tags: list[str]
    blocked_on_key: str | None = None
    max_retries: int = 2


@dataclass(frozen=True)
class ProjectTemplate:
    """A reusable project configuration template."""

    id: str
    name: str
    description: str
    # Bootstrap configuration
    project_type: str
    bootstrap_policy: str
    bootstrap_architect_provider: str | None
    # Policy defaults applied when creating an engine policy for the project
    default_model_routing: dict[str, Any]
    default_review_policy: dict[str, Any]
    # Extra tasks added to the bootstrap plan beyond the standard scaffold/validation/followup
    task_pack: list[TemplateTaskDefinition] = field(default_factory=list)
    # UI hints
    icon: str = ""
    color: str = ""


# ---------------------------------------------------------------------------
# Template: backend-python
# ---------------------------------------------------------------------------

_BACKEND_PYTHON_TASK_PACK: list[TemplateTaskDefinition] = [
    TemplateTaskDefinition(
        key="python-env-setup",
        title="Configure Python environment and dependency management",
        description=(
            "Set up Python virtual environment, dependency management (uv or pip), and baseline "
            "project configuration for a Python backend service."
        ),
        task_type="feature",
        priority="high",
        complexity="simple",
        max_retries=2,
        execution_prompt="\n".join([
            "Configure the Python development environment for this backend service.",
            "Set up dependency management with uv (preferred) or pip.",
            "Create pyproject.toml or requirements files with pinned versions.",
            "Add a .python-version file if applicable.",
            "Ensure all dependencies install cleanly in a fresh environment.",
            "Document the environment setup steps in the README.",
        ]),
        acceptance_criteria=[
            "pyproject.toml or requirements files exist with pinned dependencies.",
            "uv sync or pip install completes without errors in a clean environment.",
            "README documents the environment setup steps.",
            "Python version is specified via .python-version or pyproject.toml.",
        ],
        tags=["backend", "python", "environment", "template:backend-python"],
        blocked_on_key="scaffold",
    ),
    TemplateTaskDefinition(
        key="python-api-baseline",
        title="Implement baseline FastAPI service with health check",
        description=(
            "Create the baseline FastAPI application with a health check endpoint, "
            "structured logging, and startup validation."
        ),
        task_type="feature",
        priority="high",
        complexity="simple",
        max_retries=2,
        execution_prompt="\n".join([
            "Implement a baseline FastAPI service with:",
            "- GET /health endpoint returning service name and status",
            "- Structured logging using Python's standard logging or loguru",
            "- CORS middleware configured for local development",
            "- A main.py entrypoint that can be started with uvicorn",
            "- Environment variable configuration using pydantic-settings or python-dotenv",
            "Keep the baseline minimal; do not add business logic yet.",
        ]),
        acceptance_criteria=[
            "GET /health returns 200 with status field.",
            "Application starts with uvicorn src.main:app --reload.",
            "Environment variables are loaded from .env or environment.",
            "Structured logging emits to stdout.",
        ],
        tags=["backend", "python", "fastapi", "api", "template:backend-python"],
        blocked_on_key="python-env-setup",
    ),
    TemplateTaskDefinition(
        key="python-testing-baseline",
        title="Set up pytest testing baseline",
        description="Configure pytest with async support, basic fixtures, and a smoke test for the health endpoint.",
        task_type="test",
        priority="medium",
        complexity="simple",
        max_retries=2,
        execution_prompt="\n".join([
            "Set up a pytest testing baseline for the Python backend service.",
            "- Add pytest and pytest-asyncio to dev dependencies.",
            "- Create a conftest.py with an async test client fixture for FastAPI.",
            "- Write a smoke test for the GET /health endpoint.",
            "- Ensure pytest runs cleanly with uv run pytest or pytest.",
            "- Add a Makefile target or script alias for running tests.",
        ]),
        acceptance_criteria=[
            "pytest runs cleanly with no errors on a fresh environment.",
            "Health endpoint smoke test passes.",
            "pytest-asyncio is configured for async tests.",
            "Test run command is documented in README.",
        ],
        tags=["backend", "python", "testing", "pytest", "template:backend-python"],
        blocked_on_key="python-api-baseline",
    ),
]

_BACKEND_PYTHON = ProjectTemplate(
    id="backend-python",
    name="Backend Python",
    description=(
        "Python backend service with FastAPI, structured logging, environment configuration, "
        "and a pytest testing baseline."
    ),
    project_type="api-service",
    bootstrap_policy="standard",
    bootstrap_architect_provider=None,
    default_model_routing={
        "provider_preference": "claude-code-cli",
        "notes": "Python API service — standard Claude Code runner.",
    },
    default_review_policy={"review_mode": "self-review"},
    task_pack=_BACKEND_PYTHON_TASK_PACK,
    icon="server",
    color="blue",
)

# ---------------------------------------------------------------------------
# Template: frontend-nextjs
# ---------------------------------------------------------------------------

_FRONTEND_NEXTJS_TASK_PACK: list[TemplateTaskDefinition] = [
    TemplateTaskDefinition(
        key="nextjs-env-setup",
        title="Configure Next.js environment and TypeScript",
        description=(
            "Set up the Next.js project with TypeScript, ESLint, and environment variable configuration "
            "for local development."
        ),
        task_type="feature",
        priority="high",
        complexity="simple",
        max_retries=2,
        execution_prompt="\n".join([
            "Configure the Next.js development environment:",
            "- Ensure TypeScript is configured with strict mode in tsconfig.json.",
            "- Add ESLint with Next.js recommended rules.",
            "- Create .env.local.example with all required environment variables documented.",
            "- Configure next.config.js/ts with baseline settings (image domains, redirects if needed).",
            "- Verify npm run dev starts the app cleanly on the documented port.",
            "- Document environment setup in README.",
        ]),
        acceptance_criteria=[
            "TypeScript strict mode is enabled in tsconfig.json.",
            "ESLint passes with no errors on the baseline scaffold.",
            ".env.local.example documents all required environment variables.",
            "npm run dev starts the application without errors.",
            "README documents the setup steps.",
        ],
        tags=["frontend", "nextjs", "typescript", "environment", "template:frontend-nextjs"],
        blocked_on_key="scaffold",
    ),
    TemplateTaskDefinition(
        key="nextjs-ui-baseline",
        title="Implement baseline page layout and navigation",
        description=(
            "Create the root layout, navigation shell, and a home page with baseline styling "
            "using Tailwind CSS or the project's chosen CSS solution."
        ),
        task_type="feature",
        priority="high",
        complexity="simple",
        max_retries=2,
        execution_prompt="\n".join([
            "Implement the baseline page layout and navigation for the Next.js application:",
            "- Create a root layout (app/layout.tsx or pages/_app.tsx).",
            "- Add a navigation component with placeholder links.",
            "- Implement a home page (/) with a minimal welcome UI.",
            "- Apply baseline styling with Tailwind CSS (or the project's CSS solution).",
            "- Ensure the layout is responsive at mobile and desktop breakpoints.",
            "- Keep business logic out of this task; focus on structure and navigation.",
        ]),
        acceptance_criteria=[
            "Root layout wraps all pages with consistent header/navigation.",
            "Home page renders at / without errors.",
            "Responsive layout works at 375px (mobile) and 1440px (desktop).",
            "No TypeScript errors in the baseline layout components.",
        ],
        tags=["frontend", "nextjs", "ui", "layout", "template:frontend-nextjs"],
        blocked_on_key="nextjs-env-setup",
    ),
    TemplateTaskDefinition(
        key="nextjs-testing-baseline",
        title="Set up Vitest or Jest testing baseline",
        description=(
            "Configure a frontend testing baseline with Vitest (or Jest), React Testing Library, "
            "and a smoke test for the home page."
        ),
        task_type="test",
        priority="medium",
        complexity="simple",
        max_retries=2,
        execution_prompt="\n".join([
            "Set up a testing baseline for the Next.js application:",
            "- Add Vitest (preferred for Next.js app router) or Jest with jsdom.",
            "- Configure React Testing Library.",
            "- Write a smoke test that renders the home page and checks for a heading.",
            "- Add a test script to package.json (npm test or npm run test).",
            "- Ensure tests run cleanly in CI (no browser required).",
            "- Document how to run tests in the README.",
        ]),
        acceptance_criteria=[
            "npm test or npm run test runs cleanly with no errors.",
            "Home page smoke test passes.",
            "React Testing Library is configured.",
            "Test run command is documented in README.",
        ],
        tags=["frontend", "nextjs", "testing", "vitest", "template:frontend-nextjs"],
        blocked_on_key="nextjs-ui-baseline",
    ),
]

_FRONTEND_NEXTJS = ProjectTemplate(
    id="frontend-nextjs",
    name="Frontend Next.js",
    description=(
        "Next.js frontend application with TypeScript, Tailwind CSS, and a Vitest testing baseline."
    ),
    project_type="web-app",
    bootstrap_policy="standard",
    bootstrap_architect_provider=None,
    default_model_routing={
        "provider_preference": "claude-code-cli",
        "notes": "Next.js frontend — standard Claude Code runner.",
    },
    default_review_policy={"review_mode": "self-review"},
    task_pack=_FRONTEND_NEXTJS_TASK_PACK,
    icon="layout",
    color="green",
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, ProjectTemplate] = {
    t.id: t for t in [_BACKEND_PYTHON, _FRONTEND_NEXTJS]
}


class ProjectTemplateService:
    """Provides access to the built-in project templates."""

    def list_templates(self) -> list[ProjectTemplate]:
        """Return all available project templates."""
        return list(_TEMPLATES.values())

    def get_template(self, template_id: str) -> ProjectTemplate | None:
        """Return a template by ID, or None if not found."""
        return _TEMPLATES.get(template_id)

    def apply_to_create_kwargs(
        self,
        template_id: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge template defaults into project creation kwargs.

        Template values are used only when the caller has not already provided
        an explicit override for the corresponding field.
        """
        template = _TEMPLATES.get(template_id)
        if template is None:
            return kwargs
        merged = dict(kwargs)
        if not merged.get("project_type"):
            merged["project_type"] = template.project_type
        if not merged.get("bootstrap_policy"):
            merged["bootstrap_policy"] = template.bootstrap_policy
        if not merged.get("bootstrap_architect_provider") and template.bootstrap_architect_provider:
            merged["bootstrap_architect_provider"] = template.bootstrap_architect_provider
        return merged

    def get_task_pack(self, template_id: str) -> list[TemplateTaskDefinition]:
        """Return the extra task pack for a template (empty list if not found)."""
        template = _TEMPLATES.get(template_id)
        return list(template.task_pack) if template else []


project_template_service = ProjectTemplateService()
