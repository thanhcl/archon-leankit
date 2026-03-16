"""
Prompt Builder for LeanKit V3 Task Engine.

Generates a single unified execution prompt for Claude Code sessions
by combining task metadata, KB context, retry feedback, task assessment,
and self-review instructions.

Usage:
    builder = PromptBuilder()
    prompt = await builder.build(task)
"""

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

MAX_KB_CHUNKS = 5
MAX_CHUNK_LENGTH = 500
DEFAULT_BUILD_COMMAND = "pnpm build && pnpm test"


class PromptBuilder:
    """Builds execution prompts from task + knowledge base context."""

    def __init__(
        self,
        rag_service: Any | None = None,
        max_kb_chunks: int = MAX_KB_CHUNKS,
        max_chunk_length: int = MAX_CHUNK_LENGTH,
        default_build_command: str = DEFAULT_BUILD_COMMAND,
    ):
        self._rag_service = rag_service
        self.max_kb_chunks = max_kb_chunks
        self.max_chunk_length = max_chunk_length
        self.default_build_command = default_build_command

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
    ) -> str:
        """Build a unified execution prompt for the given task.

        Single template for all tasks. CC self-assesses complexity at runtime.
        Always includes self-review instructions.
        """
        cmd = build_command or self.default_build_command
        kb_context = await self._fetch_kb_context(task)
        return self._render(task, project, kb_context, cmd)

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
        title = (task.get("title") or "").strip()
        desc = (task.get("description") or "").strip()[:100]
        parts = [p for p in (title, desc) if p]
        return " ".join(parts)

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

    @staticmethod
    def _format_retry_feedback(task: dict[str, Any]) -> str | None:
        retry_count = task.get("retry_count") or 0
        if retry_count == 0:
            return None
        sections: list[str] = []
        review = task.get("architect_review")
        if isinstance(review, dict):
            feedback = review.get("feedback") or review.get("comments")
            if feedback:
                sections.append(f"**Architect feedback (attempt {retry_count}):**\n{feedback}")
        rejection = task.get("rejection_reason")
        if rejection:
            sections.append(f"**Rejection reason:** {rejection}")
        prev_result = task.get("execution_result")
        if isinstance(prev_result, dict):
            summary = prev_result.get("summary") or prev_result.get("error")
            if summary:
                sections.append(f"**Previous execution result:** {summary}")
        if not sections:
            sections.append(f"_Retry attempt {retry_count} — no structured feedback available._")
        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Unified template
    # ------------------------------------------------------------------

    def _render(
        self,
        task: dict[str, Any],
        project: dict[str, Any] | None,
        kb_chunks: list[dict[str, Any]],
        build_command: str,
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
            "## Context (from Knowledge Base)",
            self._format_kb_chunks(kb_chunks),
        ]

        retry_section = self._format_retry_feedback(task)
        if retry_section:
            parts += ["## Previous Feedback (retry)", retry_section, ""]

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
        ]

        return "\n".join(parts)
