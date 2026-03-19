"""
Prompt Builder for LeanKit V3 Task Engine.

Generates a single unified execution prompt for Claude Code sessions
by combining task metadata, KB context, retry feedback, task assessment,
and self-review instructions.

Includes context compression to reduce token usage by 30-50%:
- KB chunks filtered by relevance score (>= 0.65)
- Code patterns limited and truncated
- Compact static boilerplate (assessment, review, report templates)
- Token budget enforcement with progressive shedding

Usage:
    builder = PromptBuilder()
    prompt = await builder.build(task)
"""

from typing import Any

from ...config.logfire_config import get_logger
from ..search.keyword_extractor import extract_keywords
from .context_compressor import apply_token_budget, estimate_tokens

logger = get_logger(__name__)

MAX_KB_CHUNKS = 3
MAX_CHUNK_LENGTH = 200
DEFAULT_BUILD_COMMAND = "pnpm build && pnpm test"
DEFAULT_TOKEN_BUDGET = 1500
DEFAULT_MIN_RELEVANCE = 0.60
MAX_RETRY_CONTEXT_TOKENS = 300

# Compact report template — replaces verbose per-field descriptions with a single block.
# This alone saves ~250 tokens compared to the old verbose template.
_COMPACT_REPORT_TEMPLATE = """\
## Output (REQUIRED — structured)
Before coding, output: TASK_ASSESSMENT: simple|complex / ESTIMATED_FILES: N / ESTIMATED_RISK: low|medium|high / ASSESSMENT_REASONING: one sentence
Steps: 1) Assess 2) Research 3) Implement 4) Test 5) Run: `{build_command}`
Self-review: criteria met? security? backward compat? performance?
Report block:
SELF_REVIEW: PASS|NEEDS_ATTENTION
REVIEW_CONFIDENCE: 0.0-1.0
REVIEW_FINDINGS: [{{"severity":"...","category":"...","description":"..."}}]
RESULT: SUCCESS|FAILURE
FILES_CHANGED: N
TESTS_ADDED: N
SUMMARY: description
LEARNINGS: [{{"type":"error|correction|best_practice|knowledge_gap","description":"...","area":"frontend|backend|infra|tests|config|security|database","suggested_rule":"..."}}]
CODE_PATTERNS: [{{"pattern_name":"...","category":"security|error-handling|testing|architecture|performance|api-design","code_example":"...","context":"...","anti_pattern":"...","source_files":["..."]}}]
Empty arrays OK: LEARNINGS: [] / CODE_PATTERNS: []"""


class PromptBuilder:
    """Builds execution prompts from task + knowledge base context."""

    def __init__(
        self,
        rag_service: Any | None = None,
        learning_processor: Any | None = None,
        max_kb_chunks: int = MAX_KB_CHUNKS,
        max_chunk_length: int = MAX_CHUNK_LENGTH,
        default_build_command: str = DEFAULT_BUILD_COMMAND,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        min_relevance: float = DEFAULT_MIN_RELEVANCE,
        compress: bool = True,
    ):
        self._rag_service = rag_service
        self._learning_processor = learning_processor
        self.max_kb_chunks = max_kb_chunks
        self.max_chunk_length = max_chunk_length
        self.default_build_command = default_build_command
        self.token_budget = token_budget
        self.min_relevance = min_relevance
        self.compress = compress

    @property
    def rag_service(self) -> Any:
        """Lazy-init RAGService to avoid import-time side effects."""
        if self._rag_service is None:
            from ..search.rag_service import RAGService

            self._rag_service = RAGService()
        return self._rag_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(
        self,
        task: dict[str, Any],
        project: dict[str, Any] | None = None,
        build_command: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a unified execution prompt for the given task.

        When compress=True (default), applies context compression:
        - Filters KB chunks by relevance score
        - Limits and truncates code patterns
        - Uses compact boilerplate template
        - Enforces token budget with progressive shedding

        Returns:
            Tuple of (prompt_text, injection_stats) where injection_stats
            tracks how many learnings, patterns, and KB chunks were injected.
        """
        cmd = build_command or self.default_build_command
        kb_context = await self._fetch_kb_context(task)
        code_patterns = self._fetch_relevant_patterns(task)
        learnings = self._fetch_relevant_learnings(task)

        if self.compress:
            prompt = self._render_compressed(task, project, kb_context, cmd, code_patterns, learnings)
        else:
            prompt = self._render(task, project, kb_context, cmd, code_patterns, learnings)

        injection_stats = self._compute_injection_stats(prompt, kb_context, code_patterns, learnings)
        return prompt, injection_stats

    def _compute_injection_stats(
        self,
        prompt: str,
        kb_chunks: list[dict[str, Any]],
        code_patterns: list[dict[str, Any]],
        learnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute stats about what was injected into the prompt."""
        parts: list[str] = []
        if kb_chunks:
            parts.append(self._format_kb_chunks(kb_chunks))
        if learnings:
            parts.append(self._format_learnings(learnings))
        if code_patterns:
            parts.append(self._format_code_patterns(code_patterns))
        injection_tokens = estimate_tokens("".join(parts)) if parts else 0
        return {
            "learnings": len(learnings),
            "patterns": len(code_patterns),
            "kb_chunks": len(kb_chunks),
            "tokens": injection_tokens,
        }

    # ------------------------------------------------------------------
    # KB integration
    # ------------------------------------------------------------------

    async def _fetch_kb_context(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        query = self._build_search_query(task)
        if not query:
            return []
        try:
            success, result = await self.rag_service.perform_rag_query(
                query=query,
                match_count=self.max_kb_chunks,
                return_mode="chunks",
            )
            if success:
                return result.get("results", [])
            logger.warning(f"KB search failed: {result.get('error', 'unknown')}")
            return []
        except Exception as e:
            logger.warning(f"KB search error (non-fatal): {e}")
            return []

    @staticmethod
    def _build_search_query(task: dict[str, Any]) -> str:
        """Extract keywords from task title + description for KB search.

        Uses the project's KeywordExtractor to remove stopwords and
        extract meaningful technical terms (max 8 keywords).
        """
        title = (task.get("title") or "").strip()
        desc = (task.get("description") or "").strip()[:200]
        raw_text = " ".join(p for p in (title, desc) if p)
        if not raw_text:
            return ""
        keywords = extract_keywords(raw_text, min_length=2, max_keywords=8)
        return " ".join(keywords) if keywords else raw_text

    # ------------------------------------------------------------------
    # Learnings integration
    # ------------------------------------------------------------------

    def _fetch_relevant_learnings(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch relevant learnings from previous tasks."""
        if self._learning_processor is None:
            return []
        try:
            # Split task title into search keywords
            title = (task.get("title") or "").strip()
            keywords = [w for w in title.split() if len(w) > 2] if title else None
            return self._learning_processor.get_relevant_learnings(
                project_id=task.get("project_id"),
                task_keywords=keywords,
                limit=10,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch learnings (non-fatal): {e}")
            return []

    @staticmethod
    def _format_learnings(learnings: list[dict[str, Any]]) -> str:
        """Format learnings as compact one-liners with severity icons."""
        if not learnings:
            return ""
        severity_icons = {
            "error": "\u274c",           # red X
            "correction": "\u26a0\ufe0f",  # warning
            "best_practice": "\u2705",    # check
            "knowledge_gap": "\U0001f4a1",  # lightbulb
        }
        lines: list[str] = ["## Relevant Learnings from Previous Tasks", ""]
        for learning in learnings:
            ltype = learning.get("type", "knowledge_gap")
            icon = severity_icons.get(ltype, "\U0001f4a1")
            desc = (learning.get("description") or "")[:150]
            area = learning.get("area", "")
            recurrence = learning.get("recurrence_count", 1)
            area_tag = f" [{area}]" if area else ""
            recurrence_tag = f" ({recurrence}x)" if recurrence > 1 else ""
            lines.append(f"- {icon}{area_tag}{recurrence_tag} {desc}")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Code pattern integration
    # ------------------------------------------------------------------

    def _fetch_relevant_patterns(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch relevant code patterns from the pattern library."""
        if self._learning_processor is None:
            return []
        try:
            return self._learning_processor.get_relevant_patterns(
                project_id=task.get("project_id"), limit=5,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch code patterns (non-fatal): {e}")
            return []

    @staticmethod
    def _format_code_patterns(patterns: list[dict[str, Any]]) -> str:
        if not patterns:
            return ""
        lines: list[str] = ["## Code Patterns (project library)", ""]
        for p in patterns:
            lang = p.get("language", "java")
            name = p.get("pattern_name", "Unknown")
            usage = p.get("usage_count", 1)
            conf = p.get("confidence", 0.7)
            lines.append(f"### {name} ({usage}x, {conf:.0%})")
            lines.append(f"```{lang}")
            lines.append(p.get("code_example", ""))
            lines.append("```")
            ctx = p.get("context", "")
            if ctx:
                lines.append(f"Use when: {ctx}")
            anti = p.get("anti_pattern")
            if anti:
                lines.append(f"Don't: {anti}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _format_kb_chunks(self, chunks: list[dict[str, Any]]) -> str:
        if not chunks:
            return "_No relevant KB context found._"
        lines: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            content = (chunk.get("content") or "")[:self.max_chunk_length]
            metadata = chunk.get("metadata") or {}
            source = metadata.get("url") or metadata.get("source_id") or "unknown"
            score = chunk.get("similarity_score") or chunk.get("similarity") or 0
            lines.append(f"### Chunk {i}  (score {score:.2f} — {source})")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_acceptance_criteria(task: dict[str, Any]) -> str:
        criteria = task.get("acceptance_criteria") or []
        if not criteria:
            return "- [ ] Task completed as described"
        lines: list[str] = []
        for item in criteria:
            if isinstance(item, dict):
                text = item.get("text") or item.get("description") or str(item)
            else:
                text = str(item)
            lines.append(f"- [ ] {text}")
        return "\n".join(lines)

    def _format_retry_feedback(self, task: dict[str, Any]) -> str | None:
        """Format retry context with failure details, limited to MAX_RETRY_CONTEXT_TOKENS.

        Includes: previous execution result, architect feedback, rejection reason,
        relevant failure learnings, and an explicit "avoid repeating" instruction.
        """
        retry_count = task.get("retry_count") or 0
        if retry_count == 0:
            return None

        sections: list[str] = [f"## Previous Attempt Failed (attempt {retry_count})", ""]

        # 1. Extract failure details from execution_result
        prev_result = task.get("execution_result")
        if isinstance(prev_result, dict):
            error_msg = prev_result.get("summary") or prev_result.get("error")
            if error_msg:
                sections.append(f"**What failed:** {error_msg}")
            stderr = prev_result.get("stderr_preview")
            if stderr:
                sections.append(f"**Error output:** {stderr[:300]}")
            result_status = prev_result.get("result", "")
            if result_status:
                sections.append(f"**Result status:** {result_status}")
            # Extract findings if available
            findings = prev_result.get("review_findings") or prev_result.get("findings")
            if isinstance(findings, list) and findings:
                critical = [f for f in findings if isinstance(f, dict) and f.get("severity") == "critical"]
                if critical:
                    sections.append("**Critical findings:**")
                    for f in critical[:3]:
                        sections.append(f"- {f.get('description', '')[:100]}")

        # 2. Architect feedback
        review = task.get("architect_review")
        if isinstance(review, dict):
            feedback = review.get("feedback") or review.get("comments")
            if feedback:
                sections.append(f"**Architect feedback:** {feedback}")

        # 3. Rejection reason
        rejection = task.get("rejection_reason")
        if rejection:
            sections.append(f"**Rejection reason:** {rejection}")

        # 4. Fetch failure-relevant learnings from same task type/keywords
        failure_learnings = self._fetch_failure_learnings(task, prev_result)
        if failure_learnings:
            sections.append("")
            sections.append("**Relevant learnings from similar past failures:**")
            for learning in failure_learnings[:3]:
                desc = (learning.get("description") or "")[:100]
                sections.append(f"- {desc}")

        # 5. Explicit "avoid repeating" instruction
        avoid_items = self._extract_avoid_items(prev_result, review, rejection)
        if avoid_items:
            sections.append("")
            sections.append("**Avoid repeating:**")
            for item in avoid_items[:3]:
                sections.append(f"- {item}")

        if len(sections) <= 2:
            sections.append(f"_Retry attempt {retry_count} — no structured feedback available._")

        # Enforce 300-token budget
        full_text = "\n".join(sections)
        max_chars = MAX_RETRY_CONTEXT_TOKENS * 4  # ~4 chars per token
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars].rsplit("\n", 1)[0] + "\n..."

        return full_text

    def _fetch_failure_learnings(
        self,
        task: dict[str, Any],
        prev_result: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Fetch learnings relevant to the failure context."""
        if self._learning_processor is None:
            return []
        try:
            # Build keywords from task title + error message
            title = (task.get("title") or "").strip()
            keywords = [w for w in title.split() if len(w) > 2] if title else []

            if isinstance(prev_result, dict):
                error_msg = prev_result.get("summary") or prev_result.get("error") or ""
                error_words = [w for w in error_msg.split() if len(w) > 2][:5]
                keywords.extend(error_words)

            if not keywords:
                return []

            return self._learning_processor.get_relevant_learnings(
                project_id=task.get("project_id"),
                task_keywords=keywords,
                limit=3,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch failure learnings (non-fatal): {e}")
            return []

    @staticmethod
    def _extract_avoid_items(
        prev_result: dict[str, Any] | None,
        review: dict[str, Any] | None,
        rejection: str | None,
    ) -> list[str]:
        """Extract specific things to avoid from previous failure context."""
        items: list[str] = []
        if isinstance(prev_result, dict):
            summary = prev_result.get("summary") or prev_result.get("error")
            if summary:
                items.append(summary[:150])
        if isinstance(review, dict):
            feedback = review.get("feedback") or review.get("comments")
            if feedback:
                items.append(feedback[:150])
        if rejection:
            items.append(rejection[:150])
        return items

    # ------------------------------------------------------------------
    # Compressed template (default)
    # ------------------------------------------------------------------

    def _render_compressed(
        self,
        task: dict[str, Any],
        project: dict[str, Any] | None,
        kb_chunks: list[dict[str, Any]],
        build_command: str,
        code_patterns: list[dict[str, Any]] | None = None,
        learnings: list[dict[str, Any]] | None = None,
    ) -> str:
        """Render prompt with context compression applied.

        Compression strategy:
        1. Apply token budget to KB chunks, code patterns, and learnings
        2. Use compact report/assessment template
        3. Log compression metrics
        """
        title = task.get("title", "Untitled")
        priority = task.get("priority", "medium")
        assignee = task.get("assignee", "Agent")
        source_app = task.get("source_app") or (project or {}).get("title") or "unknown"
        description = task.get("description") or "_No description provided._"
        exec_prompt = task.get("execution_prompt") or ""

        # Build task text (the non-compressible parts) for budget estimation
        task_text = f"{title}\n{description}\n{exec_prompt}"

        # Apply token budget — filters and compresses KB + patterns + learnings
        compressed_kb, compressed_patterns, compressed_learnings, metrics = apply_token_budget(
            kb_chunks=kb_chunks,
            code_patterns=code_patterns or [],
            task_text=task_text,
            token_budget=self.token_budget,
            min_relevance=self.min_relevance,
            learnings=learnings or [],
        )

        logger.info(
            f"Context compression | kb: {metrics['original_kb_chunks']}→{metrics['final_kb_chunks']} | "
            f"patterns: {metrics['original_patterns']}→{metrics['final_patterns']} | "
            f"learnings: {metrics['original_learnings']}→{metrics['final_learnings']} | "
            f"tokens: ~{metrics['final_estimated_tokens']}"
        )

        parts: list[str] = [
            f"# Task: {title}",
            f"# Priority: {priority} | Assigned: {assignee}",
            f"# Project: {source_app}",
            "",
        ]

        # KB context — only if we have relevant chunks after filtering
        if compressed_kb:
            parts += ["## Relevant Knowledge Base", self._format_kb_chunks(compressed_kb), ""]
        else:
            parts += ["## Relevant Knowledge Base", "_No relevant KB context found._", ""]

        # Learnings from previous tasks
        learnings_section = self._format_learnings(compressed_learnings)
        if learnings_section:
            parts += [learnings_section, ""]

        # Code patterns — compressed
        patterns_section = self._format_code_patterns(compressed_patterns)
        if patterns_section:
            parts += [patterns_section, ""]

        retry_section = self._format_retry_feedback(task)
        if retry_section:
            parts += [retry_section, ""]

        parts += [
            "## Requirements",
            description,
            "",
        ]

        if exec_prompt:
            parts += ["## Execution Strategy", exec_prompt, ""]

        parts += [
            "## Acceptance Criteria",
            self._format_acceptance_criteria(task),
            "",
            _COMPACT_REPORT_TEMPLATE.format(build_command=build_command),
        ]

        prompt = "\n".join(parts)

        # Log total prompt size
        total_tokens = estimate_tokens(prompt)
        logger.info(f"Final prompt size | tokens: ~{total_tokens} | chars: {len(prompt)}")

        return prompt

    # ------------------------------------------------------------------
    # Uncompressed template (legacy, compress=False)
    # ------------------------------------------------------------------

    def _render(
        self,
        task: dict[str, Any],
        project: dict[str, Any] | None,
        kb_chunks: list[dict[str, Any]],
        build_command: str,
        code_patterns: list[dict[str, Any]] | None = None,
        learnings: list[dict[str, Any]] | None = None,
    ) -> str:
        title = task.get("title", "Untitled")
        priority = task.get("priority", "medium")
        assignee = task.get("assignee", "Agent")
        source_app = task.get("source_app") or (project or {}).get("title") or "unknown"
        description = task.get("description") or "_No description provided._"
        exec_prompt = task.get("execution_prompt") or ""

        parts: list[str] = [
            f"# Task: {title}",
            f"# Priority: {priority} | Assigned: {assignee}",
            f"# Project: {source_app}",
            "",
            "## Relevant Knowledge Base",
            self._format_kb_chunks(kb_chunks),
        ]

        # Inject relevant learnings from previous tasks
        learnings_section = self._format_learnings(learnings or [])
        if learnings_section:
            parts += ["", learnings_section]

        # Inject relevant code patterns from the pattern library
        patterns_section = self._format_code_patterns(code_patterns or [])
        if patterns_section:
            parts += ["", patterns_section]

        retry_section = self._format_retry_feedback(task)
        if retry_section:
            parts += [retry_section, ""]

        parts += [
            "## Requirements",
            description,
            "",
        ]

        if exec_prompt:
            parts += ["## Execution Strategy", exec_prompt, ""]

        parts += [
            "## Acceptance Criteria",
            self._format_acceptance_criteria(task),
            "",
            "## Task Assessment (REQUIRED — output BEFORE implementation)",
            "Assess the task scope before writing any code:",
            "TASK_ASSESSMENT: simple|complex",
            "ESTIMATED_FILES: {number}",
            "ESTIMATED_RISK: low|medium|high",
            "ASSESSMENT_REASONING: {one sentence}",
            "",
            "## Instructions",
            "1. Output the Task Assessment above",
            "2. Research codebase for related code",
            "3. Implement the changes",
            "4. Write tests for new functionality",
            f"5. Run: `{build_command}`",
            "",
            "## Self-Review (REQUIRED after implementation)",
            "Review your own changes before reporting:",
            "1. Check each acceptance criteria — all must pass",
            "2. Security scan: XSS, injection, auth bypass, key exposure",
            "3. Backward compatibility: existing APIs must not break",
            "4. Performance: no N+1 queries, no large allocations",
            "",
            "## Learnings (REQUIRED in output)",
            "After completing this task, reflect on what you learned:",
            "- What errors did you encounter and fix?",
            "- What patterns did you discover?",
            "- What knowledge was missing that would have helped?",
            "- What should future tasks in this area know?",
            "",
            "## Code Patterns (REQUIRED in output)",
            "After completing this task, extract reusable expert-level code patterns:",
            "- Patterns that solve common problems elegantly",
            "- Error handling approaches worth standardizing",
            "- Security patterns that should be replicated",
            "- Testing patterns that ensure quality",
            "- Architecture patterns for similar future tasks",
            "",
            "## Report (REQUIRED — structured output)",
            "SELF_REVIEW: PASS|NEEDS_ATTENTION",
            "REVIEW_CONFIDENCE: 0.0-1.0",
            'REVIEW_FINDINGS: [{"severity":"critical|warning|suggestion","category":"...","description":"..."}]',
            "RESULT: SUCCESS|FAILURE",
            "FILES_CHANGED: {count}",
            "TESTS_ADDED: {count}",
            "SUMMARY: {description}",
            "LEARNINGS: [",
            '  {"type":"error|correction|best_practice|knowledge_gap",',
            '   "description":"concise description",',
            '   "area":"frontend|backend|infra|tests|config|security|database",',
            '   "suggested_rule":"optional rule for CLAUDE.md"}',
            "]",
            "If no learnings, output: LEARNINGS: []",
            "",
            "CODE_PATTERNS: [",
            '  {"pattern_name":"descriptive name",',
            '   "category":"security|error-handling|testing|architecture|performance|api-design",',
            '   "code_example":"the actual code (keep concise, 5-30 lines)",',
            '   "context":"when and why to use this pattern",',
            '   "anti_pattern":"what NOT to do instead (optional)",',
            '   "source_files":["file1.java","file2.java"]}',
            "]",
            "If no patterns, output: CODE_PATTERNS: []",
        ]

        return "\n".join(parts)
