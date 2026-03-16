"""Tests for PromptBuilder — unified template, KB, retry, self-review."""

import pytest

from src.server.services.engine.prompt_builder import PromptBuilder


class FakeRAGService:
    def __init__(self, results=None, should_fail=False):
        self.results = results or []
        self.should_fail = should_fail
        self.last_query = None
        self.last_match_count = None

    async def perform_rag_query(self, query, match_count=5, return_mode="chunks", **kw):
        self.last_query = query
        self.last_match_count = match_count
        if self.should_fail:
            return False, {"error": "KB unavailable"}
        return True, {"results": self.results}


def _make_task(**overrides):
    base = {
        "id": "task-001",
        "title": "Add login endpoint",
        "description": "Implement POST /api/auth/login with JWT tokens",
        "status": "assigned",
        "priority": "high",
        "assignee": "Agent-1",
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
        "content": "Authentication should use bcrypt for password hashing.",
        "metadata": {"url": "https://docs.example.com/auth", "source_id": "src-1"},
        "similarity_score": 0.92,
    },
]


# -- Unified template tests --


@pytest.mark.asyncio
async def test_unified_prompt_structure():
    """Single template contains all required sections."""
    builder = PromptBuilder(rag_service=FakeRAGService(results=KB_CHUNKS))
    prompt = await builder.build(_make_task())

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
    assert "## Self-Review" in prompt
    assert "SELF_REVIEW: PASS|NEEDS_ATTENTION" in prompt
    assert "REVIEW_CONFIDENCE:" in prompt
    assert "## Report" in prompt
    assert "RESULT: SUCCESS|FAILURE" in prompt


@pytest.mark.asyncio
async def test_no_separate_complex_template():
    """build() produces same template regardless of complexity field."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    simple = await builder.build(_make_task(complexity="simple"))
    complex_ = await builder.build(_make_task(complexity="complex"))

    # Both should have the unified structure
    assert "# Task:" in simple
    assert "# Task:" in complex_
    # Neither should have PRP header
    assert "# PRP:" not in simple
    assert "# PRP:" not in complex_


@pytest.mark.asyncio
async def test_no_include_self_review_param():
    """build() no longer accepts include_self_review — always included."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task())
    assert "Self-Review" in prompt


@pytest.mark.asyncio
async def test_kb_chunks_in_prompt():
    builder = PromptBuilder(rag_service=FakeRAGService(results=KB_CHUNKS))
    prompt = await builder.build(_make_task())
    assert "bcrypt" in prompt
    assert "score 0.92" in prompt


@pytest.mark.asyncio
async def test_acceptance_criteria():
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task())
    assert "- [ ] POST /api/auth/login returns JWT" in prompt
    assert "- [ ] Invalid credentials return 401" in prompt


@pytest.mark.asyncio
async def test_custom_build_command():
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task(), build_command="mvn test")
    assert "mvn test" in prompt


@pytest.mark.asyncio
async def test_no_retry_section_when_zero():
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task(retry_count=0))
    assert "Previous Feedback" not in prompt


@pytest.mark.asyncio
async def test_retry_section():
    task = _make_task(
        retry_count=1,
        architect_review={"feedback": "Missing validation"},
        rejection_reason="Incomplete",
    )
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(task)
    assert "Previous Feedback" in prompt
    assert "Missing validation" in prompt


@pytest.mark.asyncio
async def test_execution_prompt_included():
    task = _make_task(execution_prompt="Use adapter pattern for providers")
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(task)
    assert "Execution Strategy" in prompt
    assert "adapter pattern" in prompt


@pytest.mark.asyncio
async def test_kb_failure_graceful():
    builder = PromptBuilder(rag_service=FakeRAGService(should_fail=True))
    prompt = await builder.build(_make_task())
    assert "# Task:" in prompt
    assert "No relevant KB context found" in prompt


@pytest.mark.asyncio
async def test_kb_query_params():
    rag = FakeRAGService()
    builder = PromptBuilder(rag_service=rag, max_kb_chunks=3)
    await builder.build(_make_task(title="Fix auth", description="x" * 200))
    assert "Fix auth" in rag.last_query
    assert rag.last_match_count == 3


@pytest.mark.asyncio
async def test_empty_description():
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task(description=""))
    assert "No description provided" in prompt


@pytest.mark.asyncio
async def test_empty_acceptance_criteria():
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task(acceptance_criteria=[]))
    assert "- [ ] Task completed as described" in prompt


@pytest.mark.asyncio
async def test_project_title_fallback():
    builder = PromptBuilder(rag_service=FakeRAGService())
    prompt = await builder.build(_make_task(source_app=None), project={"title": "My Project"})
    assert "# Project: My Project" in prompt


@pytest.mark.asyncio
async def test_chunk_truncation():
    builder = PromptBuilder(rag_service=FakeRAGService(results=[{
        "content": "A" * 1000,
        "metadata": {"url": "test"},
        "similarity_score": 0.9,
    }]), max_chunk_length=200)
    prompt = await builder.build(_make_task())
    assert "A" * 200 in prompt
    assert "A" * 201 not in prompt
