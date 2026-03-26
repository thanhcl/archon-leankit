"""Policy-driven bootstrap planner for newly created projects."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_BOOTSTRAP_TEMPLATE = "default-app"
DEFAULT_PROJECT_TYPE = "general-app"
DEFAULT_BOOTSTRAP_POLICY = "standard"
BOOTSTRAP_PLANNER_TAG = "bootstrap-planner:v1"

PROJECT_TYPE_TAGS: dict[str, list[str]] = {
    "general-app": ["general"],
    "web-app": ["frontend", "ui"],
    "api-service": ["backend", "api"],
    "library": ["package", "sdk"],
    "automation": ["automation", "worker"],
}

PROJECT_TYPE_SCAFFOLD_NOTES: dict[str, list[str]] = {
    "general-app": [
        "Create a sensible root project structure with clear startup instructions.",
    ],
    "web-app": [
        "Create the main application shell, baseline routes, and frontend run scripts.",
        "Include environment examples for local UI development.",
    ],
    "api-service": [
        "Create the service entrypoint, baseline API module layout, and a health-check surface.",
        "Include environment examples for local service execution.",
    ],
    "library": [
        "Create package/module exports, local test scaffolding, and usage examples.",
    ],
    "automation": [
        "Create runner or worker entrypoints, task orchestration folders, and local execution instructions.",
    ],
}

PROJECT_TYPE_VALIDATION_NOTES: dict[str, list[str]] = {
    "general-app": [
        "Confirm the scaffold is runnable for a new developer without undocumented setup.",
    ],
    "web-app": [
        "Confirm the frontend shell, run scripts, and local environment examples are coherent.",
    ],
    "api-service": [
        "Confirm the service can start locally and the health-check or baseline API surface is documented.",
    ],
    "library": [
        "Confirm package exports, local tests, and usage examples are wired coherently.",
    ],
    "automation": [
        "Confirm the worker or CLI entrypoints are documented and runnable locally.",
    ],
}

BOOTSTRAP_POLICY_NOTES: dict[str, list[str]] = {
    "standard": [
        "Balance scaffold completeness with speed. Do not over-engineer the first pass.",
    ],
    "rapid": [
        "Optimize for the fastest minimal runnable skeleton and concise setup instructions.",
    ],
    "strict": [
        "Emphasize validation depth, baseline tests, and explicit setup documentation over speed.",
    ],
}


@dataclass(frozen=True)
class BootstrapPlanContext:
    """Normalized inputs used to build a bootstrap task graph."""

    project_title: str
    project_description: str
    repo_hint: str
    template: str
    project_type: str
    bootstrap_policy: str


@dataclass(frozen=True)
class BootstrapPlanItem:
    """One bootstrap task emitted by the planner."""

    key: str
    title: str
    description: str
    task_type: str
    priority: str
    complexity: str
    max_retries: int
    created_from: str
    execution_prompt: str
    acceptance_criteria: list[str]
    tags: list[str]
    blocked_on_key: str | None = None


def normalize_project_type(project_type: str | None) -> str:
    """Return the supported project type for bootstrap planning."""
    value = (project_type or DEFAULT_PROJECT_TYPE).strip().lower()
    return value if value in PROJECT_TYPE_TAGS else DEFAULT_PROJECT_TYPE


def normalize_bootstrap_policy(bootstrap_policy: str | None) -> str:
    """Return the supported bootstrap policy for bootstrap planning."""
    value = (bootstrap_policy or DEFAULT_BOOTSTRAP_POLICY).strip().lower()
    return value if value in BOOTSTRAP_POLICY_NOTES else DEFAULT_BOOTSTRAP_POLICY


def build_bootstrap_context(
    *,
    project_title: str,
    project_description: str | None = None,
    github_repo: str | None = None,
    bootstrap_template: str | None = None,
    project_type: str | None = None,
    bootstrap_policy: str | None = None,
) -> BootstrapPlanContext:
    """Build a normalized planner context from project creation inputs."""
    template = (bootstrap_template or DEFAULT_BOOTSTRAP_TEMPLATE).strip() or DEFAULT_BOOTSTRAP_TEMPLATE
    repo_hint = github_repo.strip() if isinstance(github_repo, str) and github_repo.strip() else "no repository configured yet"
    description = (project_description or "").strip()
    return BootstrapPlanContext(
        project_title=project_title,
        project_description=description,
        repo_hint=repo_hint,
        template=template,
        project_type=normalize_project_type(project_type),
        bootstrap_policy=normalize_bootstrap_policy(bootstrap_policy),
    )


def _common_tags(context: BootstrapPlanContext) -> list[str]:
    return [
        "project-bootstrap",
        "template",
        f"template:{context.template}",
        f"project-type:{context.project_type}",
        f"bootstrap-policy:{context.bootstrap_policy}",
        BOOTSTRAP_PLANNER_TAG,
        *PROJECT_TYPE_TAGS[context.project_type],
    ]


def _policy_notes(context: BootstrapPlanContext) -> list[str]:
    return BOOTSTRAP_POLICY_NOTES[context.bootstrap_policy]


def _scaffold_item(context: BootstrapPlanContext) -> BootstrapPlanItem:
    tags = [*_common_tags(context), "scaffold"]
    acceptance = [
        "Create the initial project scaffold aligned with the selected template.",
        "Produce or update the repository README with setup and run instructions.",
        "Create baseline environment/config example files required to start local development.",
        "Leave a concise summary of the scaffolded structure and any next-step follow-up tasks.",
        *PROJECT_TYPE_SCAFFOLD_NOTES[context.project_type],
    ]
    return BootstrapPlanItem(
        key="scaffold",
        title=f"Bootstrap project workspace for {context.project_title}",
        description=(
            f"Scaffold the initial workspace and project template for {context.project_title}. "
            f"Template: {context.template}. Project type: {context.project_type}. Repository: {context.repo_hint}."
        ),
        task_type="feature",
        priority="high",
        complexity="simple",
        max_retries=2,
        created_from="project-bootstrap",
        execution_prompt="\n".join([
            "Bootstrap the project workspace using the approved project template.",
            f"Project: {context.project_title}",
            f"Template: {context.template}",
            f"Project type: {context.project_type}",
            f"Bootstrap policy: {context.bootstrap_policy}",
            f"Repository: {context.repo_hint}",
            f"Description: {context.project_description or 'No description provided.'}",
            "You are responsible for creating the initial scaffold, baseline documentation, and local run setup.",
            "Do not wait for manual repo setup; create the bootstrap structure directly in the workspace.",
            "At the end, summarize what was scaffolded and note any immediate follow-up tasks that should be created.",
            *_policy_notes(context),
        ]),
        acceptance_criteria=acceptance,
        tags=tags,
    )


def _validation_item(context: BootstrapPlanContext) -> BootstrapPlanItem:
    tags = [*_common_tags(context), "bootstrap-validation"]
    if context.project_type == "api-service":
        tags.extend(["security", "validation-gate"])
    if context.bootstrap_policy == "strict":
        tags.extend(["strict-review", "quality-gate"])
    return BootstrapPlanItem(
        key="validation",
        title=f"Validate bootstrap workspace for {context.project_title}",
        description=(
            f"Validate the scaffolded workspace for {context.project_title} and confirm the template "
            f"{context.template} is runnable and documented."
        ),
        task_type="test",
        priority="high",
        complexity="simple",
        max_retries=1,
        created_from="project-bootstrap-validation",
        execution_prompt="\n".join([
            "Validate the newly scaffolded project workspace.",
            f"Project: {context.project_title}",
            f"Template: {context.template}",
            f"Project type: {context.project_type}",
            f"Bootstrap policy: {context.bootstrap_policy}",
            "Confirm the repository structure exists, the documented setup instructions are accurate, and "
            "the baseline dev workflow can start without missing prerequisites.",
            "If validation fails, describe the missing or broken setup with clear remediation notes.",
            *_policy_notes(context),
        ]),
        acceptance_criteria=[
            "Verify the scaffolded repository structure exists and matches the template intent.",
            "Verify README/setup documentation is sufficient for a fresh developer setup.",
            "Verify baseline environment or config example files exist and are referenced correctly.",
            "Record any validation gaps clearly so follow-up work can address them.",
            *PROJECT_TYPE_VALIDATION_NOTES[context.project_type],
        ],
        tags=tags,
        blocked_on_key="scaffold",
    )


def _architecture_item(context: BootstrapPlanContext) -> BootstrapPlanItem:
    return BootstrapPlanItem(
        key="architecture",
        title=f"Capture bootstrap architecture baseline for {context.project_title}",
        description=(
            f"Document the baseline architecture and implementation boundaries after validating the "
            f"{context.project_type} scaffold for {context.project_title}."
        ),
        task_type="docs",
        priority="high",
        complexity="simple",
        max_retries=1,
        created_from="project-bootstrap-architecture",
        execution_prompt="\n".join([
            "Capture the baseline architecture after bootstrap validation.",
            f"Project: {context.project_title}",
            f"Template: {context.template}",
            f"Project type: {context.project_type}",
            "Describe the runtime surfaces, main modules, development workflow, and immediate architectural constraints.",
            "Keep it concise and implementation-oriented so follow-up tasks can build on it directly.",
            *_policy_notes(context),
        ]),
        acceptance_criteria=[
            "Summarize the baseline architecture and primary runtime surfaces created by bootstrap.",
            "Call out the intended module boundaries and main development entrypoints.",
            "Identify the first architectural risks or missing pieces that should influence follow-up tasks.",
        ],
        tags=[*_common_tags(context), "bootstrap-architecture", "docs", "architecture"],
        blocked_on_key="validation",
    )


def _followup_item(context: BootstrapPlanContext, *, blocked_on_key: str) -> BootstrapPlanItem:
    tags = [*_common_tags(context), "bootstrap-followup"]
    if context.project_type == "web-app":
        tags.append("frontend")
    if context.project_type == "api-service":
        tags.extend(["backend", "api"])
    return BootstrapPlanItem(
        key="followup",
        title=f"Seed initial follow-up plan for {context.project_title}",
        description=f"Create the first implementation backlog after bootstrap validation for {context.project_title}.",
        task_type="improvement",
        priority="medium",
        complexity="simple",
        max_retries=1,
        created_from="project-bootstrap-followup",
        execution_prompt="\n".join([
            "Review the scaffold and validation output, then seed the next practical follow-up tasks.",
            f"Project: {context.project_title}",
            f"Template: {context.template}",
            f"Project type: {context.project_type}",
            f"Bootstrap policy: {context.bootstrap_policy}",
            "Create a concise initial plan covering implementation, testing, and deployment readiness.",
            "Focus on immediately actionable tasks rather than long speculative wish lists.",
            *_policy_notes(context),
        ]),
        acceptance_criteria=[
            "Summarize the project scaffold readiness after validation.",
            "Propose the first actionable implementation tasks for the new project.",
            "Call out any missing setup or environment blockers that still need attention.",
            f"Keep the initial backlog aligned with the {context.project_type} project shape.",
        ],
        tags=tags,
        blocked_on_key=blocked_on_key,
    )


def plan_project_bootstrap(context: BootstrapPlanContext) -> list[BootstrapPlanItem]:
    """Generate the initial bootstrap task graph for a new project."""
    plan: list[BootstrapPlanItem] = [
        _scaffold_item(context),
        _validation_item(context),
    ]

    followup_blocker = "validation"
    if context.bootstrap_policy == "strict":
        plan.append(_architecture_item(context))
        followup_blocker = "architecture"

    plan.append(_followup_item(context, blocked_on_key=followup_blocker))
    return plan
