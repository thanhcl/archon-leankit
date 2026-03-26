"""Tests for PromptBuilder — unified template, KB, retry, self-review, compression."""

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
        "repo_guidance_packs": [],
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


# -- Legacy (uncompressed) template tests --


@pytest.mark.asyncio
async def test_unified_prompt_structure():
    """Single template contains all required sections (legacy mode)."""
    builder = PromptBuilder(rag_service=FakeRAGService(results=KB_CHUNKS), compress=False)
    prompt, stats = await builder.build(_make_task())

    assert "# Task: Add login endpoint" in prompt
    assert "# Priority: high | Assigned: Agent-1" in prompt
    assert "# Project: virtual-office" in prompt
    assert "## Relevant Knowledge Base" in prompt
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
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    simple, _ = await builder.build(_make_task(complexity="simple"))
    complex_, _ = await builder.build(_make_task(complexity="complex"))

    # Both should have the unified structure
    assert "# Task:" in simple
    assert "# Task:" in complex_
    # Neither should have PRP header
    assert "# PRP:" not in simple
    assert "# PRP:" not in complex_


@pytest.mark.asyncio
async def test_no_include_self_review_param():
    """build() no longer accepts include_self_review — always included."""
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task())
    assert "Self-Review" in prompt


@pytest.mark.asyncio
async def test_kb_chunks_in_prompt():
    builder = PromptBuilder(rag_service=FakeRAGService(results=KB_CHUNKS), compress=False)
    prompt, _ = await builder.build(_make_task())
    assert "bcrypt" in prompt
    assert "score 0.92" in prompt


@pytest.mark.asyncio
async def test_acceptance_criteria():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task())
    assert "- [ ] POST /api/auth/login returns JWT" in prompt
    assert "- [ ] Invalid credentials return 401" in prompt


@pytest.mark.asyncio
async def test_custom_build_command():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task(), build_command="mvn test")
    assert "mvn test" in prompt


@pytest.mark.asyncio
async def test_no_retry_section_when_zero():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task(retry_count=0))
    assert "Previous Attempt Failed" not in prompt


@pytest.mark.asyncio
async def test_retry_section():
    task = _make_task(
        retry_count=1,
        architect_review={"feedback": "Missing validation"},
        rejection_reason="Incomplete",
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(task)
    assert "Previous Attempt Failed" in prompt
    assert "Missing validation" in prompt


@pytest.mark.asyncio
async def test_execution_prompt_included():
    task = _make_task(execution_prompt="Use adapter pattern for providers")
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(task)
    assert "Execution Strategy" in prompt
    assert "adapter pattern" in prompt


@pytest.mark.asyncio
async def test_editing_boundaries_included_in_legacy_prompt():
    task = _make_task(
        allowed_paths=["src/server/**"],
        forbidden_paths=["src/store/**"],
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(task)
    assert "## Editing Boundaries" in prompt
    assert "`src/server/**`" in prompt
    assert "`src/store/**`" in prompt
    assert "stop and report the conflict" in prompt


@pytest.mark.asyncio
async def test_repo_guidance_packs_included_in_legacy_prompt():
    task = _make_task(
        repo_guidance_packs=[
            {
                "title": "API boundary",
                "guidance": "Keep route handlers thin and push business logic into services.",
                "path_scope": ["src/server/api_routes/**", "src/server/services/**"],
            }
        ]
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(task)
    assert "## Repository Guidance Packs" in prompt
    assert "### API boundary" in prompt
    assert "`src/server/api_routes/**`" in prompt
    assert "route handlers thin" in prompt


@pytest.mark.asyncio
async def test_kb_failure_graceful():
    builder = PromptBuilder(rag_service=FakeRAGService(should_fail=True), compress=False)
    prompt, _ = await builder.build(_make_task())
    assert "# Task:" in prompt
    assert "No relevant KB context found" in prompt


@pytest.mark.asyncio
async def test_kb_query_params():
    rag = FakeRAGService()
    builder = PromptBuilder(rag_service=rag, max_kb_chunks=3, compress=False)
    await builder.build(_make_task(title="Fix auth endpoint", description="Validate JWT tokens"))
    # Keywords extracted via KeywordExtractor (stopwords removed)
    assert "auth" in rag.last_query
    assert rag.last_match_count == 3


@pytest.mark.asyncio
async def test_kb_query_uses_keyword_extraction():
    """Search query uses keyword extraction with stopword removal."""
    rag = FakeRAGService()
    builder = PromptBuilder(rag_service=rag, compress=False)
    await builder.build(_make_task(
        title="Add the login endpoint for authentication",
        description="Implement POST /api/auth/login with JWT tokens",
    ))
    query = rag.last_query
    # Stopwords like "the", "for" should be removed
    assert "the" not in query.split()
    # Technical terms should be preserved
    assert "login" in query or "authentication" in query
    assert "jwt" in query.lower() or "auth" in query.lower()


@pytest.mark.asyncio
async def test_empty_description():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task(description=""))
    assert "No description provided" in prompt


@pytest.mark.asyncio
async def test_empty_acceptance_criteria():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task(acceptance_criteria=[]))
    assert "- [ ] Task completed as described" in prompt


@pytest.mark.asyncio
async def test_project_title_fallback():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task(source_app=None), project={"title": "My Project"})
    assert "# Project: My Project" in prompt


@pytest.mark.asyncio
async def test_chunk_truncation():
    builder = PromptBuilder(rag_service=FakeRAGService(results=[{
        "content": "A" * 1000,
        "metadata": {"url": "test"},
        "similarity_score": 0.9,
    }]), max_chunk_length=200, compress=False)
    prompt, _ = await builder.build(_make_task())
    assert "A" * 200 in prompt
    assert "A" * 201 not in prompt


# -- Compressed template tests --


@pytest.mark.asyncio
async def test_compressed_prompt_structure():
    """Compressed prompt has all essential sections in compact form."""
    builder = PromptBuilder(rag_service=FakeRAGService(results=KB_CHUNKS), compress=True)
    prompt, _ = await builder.build(_make_task())

    assert "# Task: Add login endpoint" in prompt
    assert "# Priority: high | Assigned: Agent-1" in prompt
    assert "# Project: virtual-office" in prompt
    assert "## Relevant Knowledge Base" in prompt
    assert "## Requirements" in prompt
    assert "## Acceptance Criteria" in prompt
    # Compact report template
    assert "TASK_ASSESSMENT: simple|complex" in prompt
    assert "RESULT: SUCCESS|FAILURE" in prompt
    assert "SELF_REVIEW: PASS|NEEDS_ATTENTION" in prompt
    assert "LEARNINGS:" in prompt
    assert "CODE_PATTERNS:" in prompt


@pytest.mark.asyncio
async def test_compressed_shorter_than_legacy():
    """Compressed prompt should be significantly shorter than legacy."""
    rag = FakeRAGService(results=KB_CHUNKS)
    legacy_builder = PromptBuilder(rag_service=rag, compress=False)
    compressed_builder = PromptBuilder(rag_service=rag, compress=True)

    task = _make_task()
    legacy, _ = await legacy_builder.build(task)
    compressed, _ = await compressed_builder.build(task)

    # Compressed should be at least 30% shorter
    reduction = 1 - len(compressed) / len(legacy)
    assert reduction >= 0.20, f"Expected >=20% reduction, got {reduction:.1%}"


@pytest.mark.asyncio
async def test_compressed_filters_low_relevance_kb():
    """Compressed mode filters out low-relevance KB chunks."""
    chunks = [
        {"content": "high relevance", "metadata": {"url": "a"}, "similarity_score": 0.9},
        {"content": "low relevance", "metadata": {"url": "b"}, "similarity_score": 0.3},
    ]
    builder = PromptBuilder(
        rag_service=FakeRAGService(results=chunks),
        compress=True, min_relevance=0.5,
    )
    prompt, _ = await builder.build(_make_task())
    assert "high relevance" in prompt
    assert "low relevance" not in prompt


@pytest.mark.asyncio
async def test_compressed_custom_build_command():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(_make_task(), build_command="cd python && uv run pytest")
    assert "cd python && uv run pytest" in prompt


@pytest.mark.asyncio
async def test_compressed_acceptance_criteria():
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(_make_task())
    assert "- [ ] POST /api/auth/login returns JWT" in prompt


@pytest.mark.asyncio
async def test_compressed_retry_section():
    task = _make_task(
        retry_count=1,
        architect_review={"feedback": "Missing validation"},
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "Previous Attempt Failed" in prompt
    assert "Missing validation" in prompt


@pytest.mark.asyncio
async def test_compressed_execution_prompt():
    task = _make_task(execution_prompt="Use adapter pattern")
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "Execution Strategy" in prompt
    assert "adapter pattern" in prompt


@pytest.mark.asyncio
async def test_compressed_kb_failure_graceful():
    builder = PromptBuilder(rag_service=FakeRAGService(should_fail=True), compress=True)
    prompt, _ = await builder.build(_make_task())
    assert "# Task:" in prompt
    assert "No relevant KB context found" in prompt


@pytest.mark.asyncio
async def test_compressed_no_kb_when_all_low_relevance():
    chunks = [
        {"content": "irrelevant", "metadata": {"url": "x"}, "similarity_score": 0.1},
    ]
    builder = PromptBuilder(
        rag_service=FakeRAGService(results=chunks),
        compress=True, min_relevance=0.5,
    )
    prompt, _ = await builder.build(_make_task())
    assert "No relevant KB context found" in prompt


@pytest.mark.asyncio
async def test_compress_false_uses_legacy_render():
    """compress=False produces the verbose legacy template."""
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task())
    # Legacy template has verbose section headers
    assert "## Task Assessment (REQUIRED — output BEFORE implementation)" in prompt
    assert "## Self-Review (REQUIRED after implementation)" in prompt
    assert "## Learnings (REQUIRED in output)" in prompt


@pytest.mark.asyncio
async def test_compressed_default_is_on():
    """Compression is enabled by default."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    assert builder.compress is True


# -- Learnings injection tests --


class FakeLearningProcessor:
    def __init__(self, learnings=None, patterns=None):
        self._learnings = learnings or []
        self._patterns = patterns or []
        self.last_keywords = None

    def get_relevant_learnings(self, project_id=None, task_keywords=None, limit=10):
        self.last_keywords = task_keywords
        return self._learnings

    def get_relevant_patterns(self, project_id=None, limit=5):
        return self._patterns


SAMPLE_LEARNINGS = [
    {
        "type": "error",
        "description": "Missing null check on user input causes 500 errors",
        "area": "backend",
        "recurrence_count": 3,
    },
    {
        "type": "best_practice",
        "description": "Always validate JWT expiry before processing",
        "area": "security",
        "recurrence_count": 1,
    },
]


@pytest.mark.asyncio
async def test_learnings_injected_compressed():
    """Compressed prompt includes learnings section."""
    lp = FakeLearningProcessor(learnings=SAMPLE_LEARNINGS)
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=True,
    )
    prompt, _ = await builder.build(_make_task())
    assert "## Relevant Learnings from Previous Tasks" in prompt
    assert "Missing null check" in prompt
    assert "validate JWT expiry" in prompt


@pytest.mark.asyncio
async def test_learnings_injected_legacy():
    """Legacy prompt includes learnings section."""
    lp = FakeLearningProcessor(learnings=SAMPLE_LEARNINGS)
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=False,
    )
    prompt, _ = await builder.build(_make_task())
    assert "## Relevant Learnings from Previous Tasks" in prompt
    assert "Missing null check" in prompt


@pytest.mark.asyncio
async def test_learnings_severity_icons():
    """Learnings are formatted with severity icons."""
    lp = FakeLearningProcessor(learnings=SAMPLE_LEARNINGS)
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=True,
    )
    prompt, _ = await builder.build(_make_task())
    assert "\u274c" in prompt  # error icon
    assert "\u2705" in prompt  # best_practice icon


@pytest.mark.asyncio
async def test_learnings_recurrence_tag():
    """Learnings with recurrence > 1 show count tag."""
    lp = FakeLearningProcessor(learnings=SAMPLE_LEARNINGS)
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=True,
    )
    prompt, _ = await builder.build(_make_task())
    assert "(3x)" in prompt  # recurrence count for first learning
    assert "(1x)" not in prompt  # single occurrence not shown


@pytest.mark.asyncio
async def test_no_learnings_no_section():
    """No learnings section when no learnings available."""
    lp = FakeLearningProcessor(learnings=[])
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=True,
    )
    prompt, _ = await builder.build(_make_task())
    assert "## Relevant Learnings from Previous Tasks" not in prompt


@pytest.mark.asyncio
async def test_learnings_graceful_without_processor():
    """No crash when learning_processor is None."""
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=None, compress=True,
    )
    prompt, _ = await builder.build(_make_task())
    assert "# Task:" in prompt
    assert "## Relevant Learnings from Previous Tasks" not in prompt


@pytest.mark.asyncio
async def test_learnings_keywords_from_title():
    """Task title is split into keywords for learning search."""
    lp = FakeLearningProcessor(learnings=[])
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=True,
    )
    await builder.build(_make_task(title="Add login endpoint"))
    assert lp.last_keywords is not None
    assert "Add" in lp.last_keywords
    assert "login" in lp.last_keywords
    assert "endpoint" in lp.last_keywords


@pytest.mark.asyncio
async def test_default_kb_chunks_is_3():
    """Default max KB chunks is 3 per requirements."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    assert builder.max_kb_chunks == 3


@pytest.mark.asyncio
async def test_default_chunk_length_is_200():
    """Default max chunk length is 200 per requirements."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    assert builder.max_chunk_length == 200


@pytest.mark.asyncio
async def test_default_token_budget_is_1500():
    """Default token budget is 1500 per requirements."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    assert builder.token_budget == 1500


@pytest.mark.asyncio
async def test_default_min_relevance_is_060():
    """Default min relevance is 0.60 per requirements."""
    builder = PromptBuilder(rag_service=FakeRAGService())
    assert builder.min_relevance == 0.60


@pytest.mark.asyncio
async def test_kb_query_empty_title_and_description():
    """Empty title and description produces no KB query."""
    rag = FakeRAGService()
    builder = PromptBuilder(rag_service=rag, compress=True)
    prompt, _ = await builder.build(_make_task(title="", description=""))
    assert rag.last_query is None  # perform_rag_query never called
    assert "No relevant KB context found" in prompt


@pytest.mark.asyncio
async def test_section_header_is_relevant_knowledge_base():
    """Section header matches requirement: '## Relevant Knowledge Base'."""
    builder = PromptBuilder(rag_service=FakeRAGService(results=KB_CHUNKS), compress=True)
    prompt, _ = await builder.build(_make_task())
    assert "## Relevant Knowledge Base" in prompt


# -- Injection stats tests --


@pytest.mark.asyncio
async def test_injection_stats_with_all_content():
    """Injection stats correctly count KB chunks, learnings, and patterns."""
    lp = FakeLearningProcessor(
        learnings=SAMPLE_LEARNINGS,
        patterns=[{"pattern_name": "test", "code_example": "x = 1", "language": "python"}],
    )
    builder = PromptBuilder(
        rag_service=FakeRAGService(results=KB_CHUNKS),
        learning_processor=lp,
        compress=True,
    )
    _, stats = await builder.build(_make_task())
    assert stats["kb_chunks"] == 1
    assert stats["learnings"] == 2
    assert stats["patterns"] == 1
    assert stats["tokens"] > 0


@pytest.mark.asyncio
async def test_injection_stats_empty():
    """Injection stats are zero when nothing is injected."""
    builder = PromptBuilder(
        rag_service=FakeRAGService(),
        learning_processor=None,
        compress=True,
    )
    _, stats = await builder.build(_make_task())
    assert stats["kb_chunks"] == 0
    assert stats["learnings"] == 0
    assert stats["patterns"] == 0
    assert stats["tokens"] == 0


@pytest.mark.asyncio
async def test_injection_stats_structure():
    """Injection stats dict has all required keys."""
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    _, stats = await builder.build(_make_task())
    assert set(stats.keys()) == {"learnings", "patterns", "kb_chunks", "tokens"}


# -- Retry failure context enrichment tests --


@pytest.mark.asyncio
async def test_retry_includes_execution_result_details():
    """Retry prompt includes error details from execution_result."""
    task = _make_task(
        retry_count=1,
        execution_result={
            "summary": "Build failed: missing import statement",
            "stderr_preview": "ModuleNotFoundError: No module named 'foo'",
            "result": "FAILURE",
        },
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "Previous Attempt Failed" in prompt
    assert "Build failed: missing import statement" in prompt
    assert "ModuleNotFoundError" in prompt
    assert "FAILURE" in prompt


@pytest.mark.asyncio
async def test_retry_includes_avoid_repeating_section():
    """Retry prompt includes 'Avoid repeating' instruction."""
    task = _make_task(
        retry_count=1,
        execution_result={"summary": "Forgot to handle None input"},
        architect_review={"feedback": "Add null checks"},
        rejection_reason="Missing error handling",
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "Avoid repeating" in prompt
    assert "Forgot to handle None input" in prompt


@pytest.mark.asyncio
async def test_retry_fetches_failure_learnings():
    """Retry prompt includes learnings relevant to the failure."""
    failure_learnings = [
        {
            "type": "error",
            "description": "Always check for None before accessing .id property",
            "area": "backend",
            "recurrence_count": 2,
        },
    ]
    lp = FakeLearningProcessor(learnings=failure_learnings)
    task = _make_task(
        retry_count=1,
        execution_result={"summary": "AttributeError: NoneType has no attribute id"},
    )
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=lp, compress=True,
    )
    prompt, _ = await builder.build(task)
    assert "Relevant learnings from similar past failures" in prompt
    assert "Always check for None" in prompt


@pytest.mark.asyncio
async def test_retry_context_token_budget():
    """Retry context is truncated to MAX_RETRY_CONTEXT_TOKENS (~300 tokens)."""
    from src.server.services.engine.prompt_builder import MAX_RETRY_CONTEXT_TOKENS

    # Create a task with very long error output
    task = _make_task(
        retry_count=1,
        execution_result={
            "summary": "X" * 500,
            "stderr_preview": "Y" * 500,
            "result": "FAILURE",
        },
        architect_review={"feedback": "Z" * 500},
        rejection_reason="W" * 500,
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)

    # Extract the retry section from the prompt
    retry_start = prompt.find("## Previous Attempt Failed")
    assert retry_start >= 0
    # Find next major section
    retry_end = prompt.find("\n## ", retry_start + 1)
    if retry_end == -1:
        retry_end = len(prompt)
    retry_section = prompt[retry_start:retry_end]

    # Verify it's within budget (300 tokens * 4 chars + some margin for truncation)
    max_chars = MAX_RETRY_CONTEXT_TOKENS * 4
    assert len(retry_section) <= max_chars + 50  # small margin for "..." suffix


@pytest.mark.asyncio
async def test_retry_no_failure_learnings_without_processor():
    """No crash when fetching failure learnings without learning_processor."""
    task = _make_task(
        retry_count=1,
        execution_result={"summary": "Test failed"},
    )
    builder = PromptBuilder(
        rag_service=FakeRAGService(), learning_processor=None, compress=True,
    )
    prompt, _ = await builder.build(task)
    assert "Previous Attempt Failed" in prompt
    assert "Relevant learnings from similar past failures" not in prompt


@pytest.mark.asyncio
async def test_retry_critical_findings_included():
    """Retry context includes critical findings from previous execution."""
    task = _make_task(
        retry_count=1,
        execution_result={
            "summary": "Tests failed",
            "review_findings": [
                {"severity": "critical", "description": "SQL injection vulnerability in login"},
                {"severity": "warning", "description": "Missing docstring"},
            ],
        },
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "Critical findings" in prompt
    assert "SQL injection" in prompt
    assert "Missing docstring" not in prompt  # Only critical findings included


@pytest.mark.asyncio
async def test_editing_boundaries_included_in_compressed_prompt():
    task = _make_task(
        allowed_paths=["src/server/**"],
        forbidden_paths=["src/store/**"],
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "## Editing Boundaries" in prompt
    assert "`src/server/**`" in prompt
    assert "`src/store/**`" in prompt


@pytest.mark.asyncio
async def test_repo_guidance_packs_included_in_compressed_prompt():
    task = _make_task(
        repo_guidance_packs=[
            {
                "title": "Engine discipline",
                "guidance": "Preserve lifecycle audit semantics when refactoring execution services.",
                "path_scope": ["src/server/services/engine/**"],
            }
        ]
    )
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(task)
    assert "## Repository Guidance Packs" in prompt
    assert "### Engine discipline" in prompt
    assert "`src/server/services/engine/**`" in prompt
    assert "lifecycle audit semantics" in prompt


# -- Compaction hint tests --


def test_compaction_hint_contains_required_fields():
    """_compaction_hint output mentions all required preserved fields."""
    hint = PromptBuilder._compaction_hint("execute", "self-review")
    assert "task_id" in hint
    assert "acceptance criteria" in hint
    assert "files modified" in hint
    assert "test results" in hint


def test_compaction_hint_reflects_stage_names():
    """_compaction_hint includes the previous and next stage names."""
    hint = PromptBuilder._compaction_hint("execute", "self-review")
    assert "execute" in hint
    assert "self-review" in hint

    hint2 = PromptBuilder._compaction_hint("self-review", "code-review")
    assert "self-review" in hint2
    assert "code-review" in hint2


@pytest.mark.asyncio
async def test_compaction_hints_in_compressed_prompt():
    """Compressed prompt includes both stage-transition compaction hints."""
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=True)
    prompt, _ = await builder.build(_make_task())

    assert "execute" in prompt and "self-review" in prompt
    assert "code-review" in prompt
    assert "task_id" in prompt
    assert "acceptance criteria" in prompt
    assert "files modified" in prompt
    assert "test results" in prompt


@pytest.mark.asyncio
async def test_compaction_hints_in_legacy_prompt():
    """Legacy prompt includes both stage-transition compaction hints."""
    builder = PromptBuilder(rag_service=FakeRAGService(), compress=False)
    prompt, _ = await builder.build(_make_task())

    assert "execute" in prompt and "self-review" in prompt
    assert "code-review" in prompt
    assert "task_id" in prompt
    assert "acceptance criteria" in prompt
    assert "files modified" in prompt
    assert "test results" in prompt
