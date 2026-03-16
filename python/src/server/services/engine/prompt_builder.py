"""
Prompt Builder for LeanKit V3 Task Engine.

Generates high-quality execution prompts for Claude Code sessions
by combining task metadata, KB context, and retry feedback.

Usage:
    builder = PromptBuilder()
    prompt = await builder.build(task)
"""

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Defaults
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
        include_self_review: bool = False,
    ) -> str:
        """
        Build a complete execution prompt for the given task.

        Args:
            task: Full task dict (from DB / API).
            project: Optional project dict for extra context.
            build_command: Override build/test command. Falls back to
                           project config → default.
            include_self_review: If True, append self-review instructions
                for CC to evaluate its own changes.

        Returns:
            Ready-to-use prompt string.
        """
        complexity = task.get("complexity", "simple")
        cmd = build_command or self.default_build_command

        kb_context = await self._fetch_kb_context(task)

        if complexity == "complex":
            prompt = self._render_complex(task, project, kb_context, cmd)
        else:
            prompt = self._render_simple(task, project, kb_context, cmd)

        if include_self_review:
            prompt += "\n" + self._self_review_section()

        return prompt

    # ------------------------------------------------------------------
    # KB integration
    # ------------------------------------------------------------------

    async def _fetch_kb_context(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """Query RAG KB for relevant context chunks."""
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
        """Combine title + truncated description into a search query."""
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
    def _self_review_section() -> str:
        """Return the self-review instructions block for CC prompts."""
        return (
            "\n## Self-Review (REQUIRED before reporting)\n"
            "After implementation, review your own changes:\n"
            "1. Check each acceptance criteria — all must pass\n"
            "2. Security scan: XSS, injection, auth bypass, key exposure\n"
            "3. Backward compatibility: existing APIs must not break\n"
            "4. Performance: no N+1 queries, no large allocations\n"
            "\n"
            "Include in your output:\n"
            "SELF_REVIEW: PASS|NEEDS_ATTENTION\n"
            "REVIEW_CONFIDENCE: 0.0-1.0\n"
            'REVIEW_FINDINGS: [{"severity":"critical|warning|suggestion","category":"...","description":"..."}]\n'
        )

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
        """Return retry section if task has previous attempt feedback."""
        retry_count = task.get("retry_count") or 0
        if retry_count == 0:
            return None

        sections: list[str] = []

        # Architect review feedback
        review = task.get("architect_review")
        if isinstance(review, dict):
            feedback = review.get("feedback") or review.get("comments")
            if feedback:
                sections.append(f"**Architect feedback (attempt {retry_count}):**\n{feedback}")

        # Rejection reason (from review → assigned or architect-review → assigned)
        rejection = task.get("rejection_reason")
        if rejection:
            sections.append(f"**Rejection reason:** {rejection}")

        # Previous execution result summary
        prev_result = task.get("execution_result")
        if isinstance(prev_result, dict):
            summary = prev_result.get("summary") or prev_result.get("error")
            if summary:
                sections.append(f"**Previous execution result:** {summary}")

        if not sections:
            sections.append(f"_Retry attempt {retry_count} — no structured feedback available._")

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Simple prompt
    # ------------------------------------------------------------------

    def _render_simple(
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
            "## Acceptance Criteria",
            self._format_acceptance_criteria(task),
            "",
            "## Instructions",
            "1. Research codebase for related code",
            "2. Implement the changes",
            "3. Write tests for new functionality",
            f"4. Run: `{build_command}`",
            "5. Report result as structured output:",
            "   RESULT: SUCCESS|FAILURE",
            "   FILES_CHANGED: {count}",
            "   TESTS_ADDED: {count}",
            "   SUMMARY: {description}",
        ]

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Complex prompt (PRP-style)
    # ------------------------------------------------------------------

    def _render_complex(
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
            f"# PRP: {title}",
            f"# Priority: {priority} | Assigned: {assignee} | Complexity: complex",
            f"# Project: {source_app}",
            "",
            "## 1. Overview",
            description,
            "",
        ]

        if exec_prompt:
            parts += ["## 2. Execution Strategy", exec_prompt, ""]
        else:
            parts += [
                "## 2. Execution Strategy",
                "1. Analyze the codebase to understand current architecture",
                "2. Plan the implementation approach",
                "3. Implement changes incrementally with tests",
                "4. Validate all acceptance criteria",
                "",
            ]

        parts += [
            "## 3. Context (from Knowledge Base)",
            self._format_kb_chunks(kb_chunks),
        ]

        retry_section = self._format_retry_feedback(task)
        if retry_section:
            parts += ["## 4. Previous Feedback (retry)", retry_section, ""]

        parts += [
            "## 5. Acceptance Criteria",
            self._format_acceptance_criteria(task),
            "",
            "## 6. Cross-Cutting Concerns",
            "- Ensure no regressions in existing tests",
            "- Follow project coding conventions and linting rules",
            "- Keep changes minimal and focused — avoid scope creep",
            "- Preserve backwards compatibility unless explicitly told otherwise",
            "",
            "## 7. Validation",
            f"Run: `{build_command}`",
            "",
            "Report result as structured output:",
            "```",
            "RESULT: SUCCESS|FAILURE",
            "FILES_CHANGED: {count}",
            "TESTS_ADDED: {count}",
            "SUMMARY: {description}",
            "```",
        ]

        return "\n".join(parts)
