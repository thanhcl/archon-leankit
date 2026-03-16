"""Tests for PromptBuilder — simple, complex, retry, and KB integration."""

import pytest

from src.server.services.engine.prompt_builder import PromptBuilder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeRAGService:
    """Mock RAG service that returns canned KB results."""

    def __init__(self, results: list | None = None, should_fail: bool = False):
        self.results = results or []
        self.should_fail = should_fail
        self.last_query: str | None = None
        self.last_match_count: int | None = None

    async def perform_rag_query(
        self, query: str, match_count: int = 5, return_mode: str = "chunks", **kwargs
    ):
        self.last_query = query
        self.last_match_count = match_count
        if self.should_fail:
            return False, {"error": "KB unavailable"}
        return True, {"results": self.results}


def _make_task(**overrides) -> dict:
    """Create a minimal task dict with sensible defaults."""
    base = {
        "id": "task-001",
        "title": "Add login endpoint",
        "description": "Implement POST /api/auth/login with JWT tokens",
        "status": "assigned",
        "priority": "high",
        "assignee": "Agent-1",
        "complexity": "simple",
        "source_app": "virtual-office",
        "acceptance_criteria": [
            {"text": "POST /api/auth/login returns JWT"},
            {"text": "Invalid credentials return 401"},
        ],
        "retry_count": 0,
        "architect_review": None,
        "execution_result": None,
        "rejection_reason": None,
        "execution_prompt": None,
    }
    base.update(overrides)
    return base


KB_CHUNKS = [
    {
        "content": "Authentication should use bcrypt for password hashing and JWT for session tokens.",
        "metadata": {"url": "https://docs.example.com/auth", "source_id": "src-1"},
        "similarity_score": 0.92,
    },
    {
        "content": "The /api/auth routes are defined in auth_api.py and use FastAPI's Depends() for injection.",
        "metadata": {"url": "https://docs.example.com/api-routes", "source_id": "src-2"},
        "similarity_score": 0.85,
    },
]


# ---------------------------------------------------------------------------
# Tests: Simple prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_prompt_structure():
    """Simple task produces a prompt with all required sections."""
    rag = FakeRAGService(results=KB_CHUNKS)
    builder = PromptBuilder(rag_service=rag)
    task = _make_task()

    prompt = await builder.build(task)

    assert "# Task: Add login endpoint" in prompt
    assert "# Priority: high | Assigned: Agent-1" in prompt
    assert "# Project: virtual-office" in prompt
    assert "## Context (from Knowledge Base)" in prompt
    assert "## Requirements" in prompt
    assert "## Acceptance Criteria" in prompt
    assert "## Task Assessment" in prompt
    assert "TASK_ASSESSMENT: simple|complex" in prompt
    assert "ESTIMATED_RISK: low|medium|high" in prompt
    assert "## Instructions" in prompt
    assert "RESULT: SUCCESS|FAILURE" in prompt


@pytest.mark.asyncio
async def test_simple_prompt_includes_kb_chunks():
    """KB chunks appear in the prompt with score and source."""
    rag = FakeRAGService(results=KB_CHUNKS)
    builder = PromptBuilder(rag_service=rag)
    task = _make_task()

    prompt = await builder.build(task)

    assert "score 0.92" in prompt
    assert "https://docs.example.com/auth" in prompt
    assert "bcrypt" in prompt
    assert "Chunk 1" in prompt
    assert "Chunk 2" in prompt


@pytest.mark.asyncio
async def test_simple_prompt_acceptance_criteria():
    """Acceptance criteria rendered as checklist."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task()

    prompt = await builder.build(task)

    assert "- [ ] POST /api/auth/login returns JWT" in prompt
    assert "- [ ] Invalid credentials return 401" in prompt


@pytest.mark.asyncio
async def test_simple_prompt_custom_build_command():
    """Custom build command overrides the default."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task()

    prompt = await builder.build(task, build_command="mvn compile && mvn test")

    assert "mvn compile && mvn test" in prompt
    assert "pnpm" not in prompt


@pytest.mark.asyncio
async def test_simple_prompt_no_retry_section_when_zero():
    """No retry section when retry_count is 0."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(retry_count=0)

    prompt = await builder.build(task)

    assert "Previous Feedback" not in prompt


# ---------------------------------------------------------------------------
# Tests: Complex prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complex_prompt_structure():
    """Complex task produces PRP-style prompt with numbered sections."""
    rag = FakeRAGService(results=KB_CHUNKS)
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(complexity="complex")

    prompt = await builder.build(task)

    assert "# PRP: Add login endpoint" in prompt
    assert "Complexity: complex" in prompt
    assert "## 1. Overview" in prompt
    assert "## 2. Execution Strategy" in prompt
    assert "## 3. Context (from Knowledge Base)" in prompt
    assert "## 5. Task Assessment" in prompt
    assert "TASK_ASSESSMENT: simple|complex" in prompt
    assert "## 6. Acceptance Criteria" in prompt
    assert "## 7. Cross-Cutting Concerns" in prompt
    assert "## 8. Validation" in prompt


@pytest.mark.asyncio
async def test_complex_prompt_with_execution_prompt():
    """Complex task uses execution_prompt for strategy section."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(
        complexity="complex",
        execution_prompt="Use the adapter pattern for auth providers.",
    )

    prompt = await builder.build(task)

    assert "Use the adapter pattern for auth providers." in prompt


@pytest.mark.asyncio
async def test_complex_prompt_cross_cutting_concerns():
    """Complex prompt includes cross-cutting concerns."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(complexity="complex")

    prompt = await builder.build(task)

    assert "no regressions" in prompt
    assert "coding conventions" in prompt
    assert "scope creep" in prompt


# ---------------------------------------------------------------------------
# Tests: Retry prompt (includes previous feedback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_prompt_includes_architect_feedback():
    """Retry task includes architect feedback in prompt."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(
        retry_count=1,
        architect_review={"feedback": "Missing input validation on email field"},
        rejection_reason="Incomplete implementation",
    )

    prompt = await builder.build(task)

    assert "## Previous Feedback (retry)" in prompt
    assert "Missing input validation on email field" in prompt
    assert "Incomplete implementation" in prompt
    assert "attempt 1" in prompt


@pytest.mark.asyncio
async def test_retry_prompt_includes_previous_execution_result():
    """Retry includes summary from previous execution result."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(
        retry_count=2,
        execution_result={"summary": "Tests failed: 3 assertions in test_auth.py"},
    )

    prompt = await builder.build(task)

    assert "Previous Feedback" in prompt
    assert "Tests failed: 3 assertions" in prompt


@pytest.mark.asyncio
async def test_retry_complex_prompt_includes_feedback():
    """Complex retry also gets feedback section."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(
        complexity="complex",
        retry_count=1,
        rejection_reason="Architecture concern: use service layer",
    )

    prompt = await builder.build(task)

    assert "# PRP:" in prompt
    assert "## 4. Previous Feedback (retry)" in prompt
    assert "Architecture concern" in prompt


# ---------------------------------------------------------------------------
# Tests: KB integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_query_uses_title_and_description():
    """Search query combines title + first 100 chars of description."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(
        title="Fix auth bug",
        description="The login endpoint returns 500 " + "x" * 200,
    )

    await builder.build(task)

    assert rag.last_query is not None
    assert "Fix auth bug" in rag.last_query
    assert len(rag.last_query) <= len("Fix auth bug") + 1 + 100  # title + space + 100 desc chars


@pytest.mark.asyncio
async def test_kb_query_match_count():
    """Builder requests correct number of KB chunks."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag, max_kb_chunks=3)
    task = _make_task()

    await builder.build(task)

    assert rag.last_match_count == 3


@pytest.mark.asyncio
async def test_kb_failure_produces_graceful_fallback():
    """When KB search fails, prompt still generates without KB context."""
    rag = FakeRAGService(should_fail=True)
    builder = PromptBuilder(rag_service=rag)
    task = _make_task()

    prompt = await builder.build(task)

    assert "# Task: Add login endpoint" in prompt
    assert "No relevant KB context found" in prompt


@pytest.mark.asyncio
async def test_kb_chunks_truncated_to_max_length():
    """Long KB chunks are truncated to max_chunk_length."""
    long_content = "A" * 1000
    rag = FakeRAGService(results=[{
        "content": long_content,
        "metadata": {"url": "test"},
        "similarity_score": 0.9,
    }])
    builder = PromptBuilder(rag_service=rag, max_chunk_length=200)
    task = _make_task()

    prompt = await builder.build(task)

    # The chunk in the prompt should be at most 200 chars
    assert "A" * 200 in prompt
    assert "A" * 201 not in prompt


@pytest.mark.asyncio
async def test_empty_task_description():
    """Task with no description still produces valid prompt."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(description="")

    prompt = await builder.build(task)

    assert "# Task:" in prompt
    assert "No description provided" in prompt


@pytest.mark.asyncio
async def test_acceptance_criteria_fallback():
    """Empty acceptance_criteria gets default checklist item."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(acceptance_criteria=[])

    prompt = await builder.build(task)

    assert "- [ ] Task completed as described" in prompt


@pytest.mark.asyncio
async def test_project_title_fallback_for_source_app():
    """When source_app is None, project title is used."""
    rag = FakeRAGService(results=[])
    builder = PromptBuilder(rag_service=rag)
    task = _make_task(source_app=None)
    project = {"title": "My Project", "id": "proj-1"}

    prompt = await builder.build(task, project=project)

    assert "# Project: My Project" in prompt
