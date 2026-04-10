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

import hashlib
from typing import Any

from ...config.logfire_config import get_logger
from ..search.keyword_extractor import extract_keywords
from .context_compressor import apply_token_budget, estimate_tokens
from .task_boundaries import normalize_path_rules

logger = get_logger(__name__)

MAX_KB_CHUNKS = 3
MAX_CHUNK_LENGTH = 200
DEFAULT_BUILD_COMMAND = "pnpm build && pnpm test"
DEFAULT_TOKEN_BUDGET = 1500
DEFAULT_MIN_RELEVANCE = 0.60
MAX_RETRY_CONTEXT_TOKENS = 300
DEFAULT_WAKEUP_BUDGET = 200

# Compact report template — replaces verbose per-field descriptions with a single block.
# This alone saves ~250 tokens compared to the old verbose template.
_COMPACT_REPORT_TEMPLATE = """\
## Output (REQUIRED — structured)
Before coding, output: TASK_ASSESSMENT: simple|complex / ESTIMATED_FILES: N / ESTIMATED_RISK: low|medium|high / ASSESSMENT_REASONING: one sentence
Steps: 1) Assess 2) Research 3) Implement 4) Test 5) Run: `{build_command}`
{execute_self_review_hint}
Self-review: criteria met? security? backward compat? performance?
{self_review_code_review_hint}
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
        wakeup_budget: int = DEFAULT_WAKEUP_BUDGET,
    ):
        self._rag_service = rag_service
        self._learning_processor = learning_processor
        self._codebase_intel = None  # Lazy-init CodebaseIntelligence
        self.max_kb_chunks = max_kb_chunks
        self.max_chunk_length = max_chunk_length
        self.default_build_command = default_build_command
        self.token_budget = token_budget
        self.min_relevance = min_relevance
        self.compress = compress
        self._wakeup_budget = wakeup_budget

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
        agent_definition: dict[str, Any] | None = None,
        review_feedback: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a unified execution prompt for the given task.

        When compress=True (default), applies context compression:
        - Filters KB chunks by relevance score
        - Limits and truncates code patterns
        - Uses compact boilerplate template
        - Enforces token budget with progressive shedding

        Args:
            agent_definition: Optional agent definition dict. When provided, its
                prompt_template is injected as a role preamble before the task
                requirements section.
            review_feedback: Optional structured feedback from archon_review_feedback.
                When provided, enhances retry context with per-criterion scores.

        Returns:
            Tuple of (prompt_text, injection_stats) where injection_stats
            tracks how many learnings, patterns, and KB chunks were injected.
        """
        cmd = build_command or self.default_build_command
        kb_context = await self._fetch_kb_context(task)
        code_patterns = self._fetch_relevant_patterns(task)
        learnings = self._fetch_relevant_learnings(task)
        intel_summary = await self._fetch_codebase_intel(task)

        # Build wake-up context (MemPalace-inspired tiered loading)
        wakeup_text, wakeup_tokens = await self._build_wakeup_context(
            task, project=project, agent_definition=agent_definition,
            wakeup_budget=self._wakeup_budget,
        )

        if self.compress:
            prompt = self._render_compressed(task, project, kb_context, cmd, code_patterns, learnings, agent_definition, review_feedback=review_feedback, intel_summary=intel_summary, wakeup_context=wakeup_text)
        else:
            prompt = self._render(task, project, kb_context, cmd, code_patterns, learnings, agent_definition, review_feedback=review_feedback, intel_summary=intel_summary, wakeup_context=wakeup_text)

        injection_stats = self._compute_injection_stats(prompt, kb_context, code_patterns, learnings, wakeup_tokens=wakeup_tokens)
        return prompt, injection_stats

    def _compute_injection_stats(
        self,
        prompt: str,
        kb_chunks: list[dict[str, Any]],
        code_patterns: list[dict[str, Any]],
        learnings: list[dict[str, Any]],
        wakeup_tokens: int = 0,
    ) -> dict[str, Any]:
        """Compute stats about what was injected into the prompt."""
        parts: list[str] = []
        if kb_chunks:
            parts.append(self._format_kb_chunks(kb_chunks))
        if learnings:
            parts.append(self._format_learnings(learnings))
        if code_patterns:
            parts.append(self._format_code_patterns(code_patterns))
        guidance_text = "".join(parts) if parts else ""
        injection_tokens = estimate_tokens(guidance_text) if guidance_text else 0
        total_prompt_tokens = estimate_tokens(prompt) if prompt else 0
        task_context_tokens = total_prompt_tokens - injection_tokens - wakeup_tokens

        # Guidance pack hash for versioning — correlate pack changes with success rate
        guidance_hash = hashlib.md5(guidance_text.encode()).hexdigest()[:12] if guidance_text else ""

        return {
            "learnings": len(learnings),
            "patterns": len(code_patterns),
            "kb_chunks": len(kb_chunks),
            "tokens": injection_tokens,
            "wakeup_tokens": wakeup_tokens,
            "guidance_pack_hash": guidance_hash,
            "prompt_token_breakdown": {
                "guidance_pack_tokens": injection_tokens,
                "wakeup_tokens": wakeup_tokens,
                "task_context_tokens": max(0, task_context_tokens),
                "total_prompt_tokens": total_prompt_tokens,
            },
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
            file_paths = task.get("allowed_paths") or []
            return self._learning_processor.get_relevant_learnings(
                project_id=task.get("project_id"),
                task_keywords=keywords,
                file_paths=file_paths if file_paths else None,
                limit=10,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch learnings (non-fatal): {e}")
            return []

    @staticmethod
    def _format_learnings(learnings: list[dict[str, Any]]) -> str:
        """Format learnings as compact one-liners with severity icons.

        Includes effective confidence and source when available (from
        structured reflection). Shows recurrence count for recurring patterns.
        """
        if not learnings:
            return ""
        severity_icons = {
            "error": "\u274c",              # red X
            "correction": "\u26a0\ufe0f",     # warning
            "best_practice": "\u2705",       # check
            "knowledge_gap": "\U0001f4a1",   # lightbulb
            # GStack-inspired reflection types
            "pitfall": "\U0001f6a7",         # construction
            "operational": "\u2699\ufe0f",    # gear
            "tool": "\U0001f527",            # wrench
            "architecture_insight": "\U0001f3d7\ufe0f",  # building construction
        }
        lines: list[str] = ["## Relevant Learnings from Previous Runs", ""]
        for learning in learnings:
            ltype = learning.get("type", "knowledge_gap")
            icon = severity_icons.get(ltype, "\U0001f4a1")
            desc = (learning.get("description") or "")[:150]
            area = learning.get("area", "")
            recurrence = learning.get("recurrence_count", 1)
            eff_conf = learning.get("effective_confidence")

            area_tag = f" [{area}]" if area else ""
            recurrence_tag = f" ({recurrence}x)" if recurrence > 1 else ""
            conf_tag = f" conf={eff_conf}/10" if eff_conf is not None else ""
            lines.append(f"- {icon}{area_tag}{recurrence_tag}{conf_tag} {desc}")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Agent Wake-Up Context (MemPalace-inspired tiered loading)
    # ------------------------------------------------------------------

    async def _build_wakeup_context(
        self,
        task: dict[str, Any],
        project: dict[str, Any] | None = None,
        agent_definition: dict[str, Any] | None = None,
        wakeup_budget: int = DEFAULT_WAKEUP_BUDGET,
    ) -> tuple[str, int]:
        """Build a compact wake-up context blob for agent session start.

        Assembles four sections (each gracefully omitted if data unavailable):
        1. Agent Identity — role and specialization (~20 tokens)
        2. Project Context — name and tech stack (~40 tokens)
        3. Top-N Promoted Learnings — project-wide patterns (~80 tokens)
        4. Recent Execution Context — last 3 completed runs (~60 tokens)

        Returns:
            Tuple of (wakeup_text, wakeup_token_count).
            Empty string and 0 if no context is available.
        """
        sections: list[str] = []

        # 1. Agent Identity
        if agent_definition:
            name = agent_definition.get("name") or "Agent"
            role = agent_definition.get("role") or ""
            specialization = agent_definition.get("specialization") or ""
            identity_parts = [f"You are **{name}**"]
            if role:
                identity_parts.append(f"a {role} agent")
            if specialization:
                identity_parts.append(f"specializing in {specialization}")
            sections.append(", ".join(identity_parts) + ".")

        # 2. Project Context
        project_name = (project or {}).get("title") or task.get("source_app") or ""
        if project_name:
            # Tech stack from codebase intel (already fetched separately)
            stack = (project or {}).get("tech_stack") or ""
            proj_line = f"Project: **{project_name}**"
            if stack:
                proj_line += f" | Stack: {stack}"
            sections.append(proj_line)

        # 3. Top-N Promoted Learnings (project-wide, not task-scoped)
        project_id = task.get("project_id")
        top_learnings = self._fetch_project_top_learnings(project_id)
        if top_learnings:
            learning_lines = ["Key project patterns:"]
            for learning in top_learnings[:5]:
                desc = (learning.get("description") or "")[:100]
                tier = learning.get("tier", "")
                tier_tag = " [promoted]" if tier == "promoted" else ""
                learning_lines.append(f"- {desc}{tier_tag}")
            sections.append("\n".join(learning_lines))

        # 4. Recent Execution Context
        recent_runs = await self._fetch_recent_execution_summaries(project_id)
        if recent_runs:
            run_lines = ["Recent work:"]
            for i, run in enumerate(recent_runs[:3], 1):
                summary = (run.get("result_summary") or "completed")[:80]
                run_lines.append(f"{i}) {summary}")
            sections.append("\n".join(run_lines))

        if not sections:
            return "", 0

        wakeup_text = "## Agent Context\n" + "\n".join(sections)

        # Enforce token budget — truncate if over budget
        wakeup_tokens = estimate_tokens(wakeup_text)
        if wakeup_tokens > wakeup_budget:
            # Progressive truncation: drop recent runs first, then learnings
            if recent_runs and len(sections) > 2:
                sections = sections[:-1]  # Drop recent runs
                wakeup_text = "## Agent Context\n" + "\n".join(sections)
                wakeup_tokens = estimate_tokens(wakeup_text)
            if wakeup_tokens > wakeup_budget and top_learnings and len(sections) > 1:
                sections = sections[:-1]  # Drop learnings
                wakeup_text = "## Agent Context\n" + "\n".join(sections)
                wakeup_tokens = estimate_tokens(wakeup_text)

        return wakeup_text, wakeup_tokens

    def _fetch_project_top_learnings(
        self, project_id: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch top project-wide learnings (probation/promoted) for wake-up context."""
        if self._learning_processor is None:
            return []
        try:
            return self._learning_processor.get_project_top_learnings(
                project_id=project_id,
                limit=5,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch project top learnings (non-fatal): {e}")
            return []

    async def _fetch_recent_execution_summaries(
        self, project_id: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch result summaries from the last 3 completed runs for this project."""
        if not project_id:
            return []
        try:
            from ...utils import get_supabase_client

            client = get_supabase_client()
            resp = (
                client.table("archon_execution_runs")
                .select("task_id,result_summary,finished_at")
                .eq("project_id", project_id)
                .eq("status", "completed")
                .not_.is_("result_summary", "null")
                .order("finished_at", desc=True)
                .limit(3)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.warning(f"Failed to fetch recent execution summaries (non-fatal): {e}")
            return []

    # ------------------------------------------------------------------
    # Codebase intelligence integration
    # ------------------------------------------------------------------

    async def _fetch_codebase_intel(self, task: dict[str, Any]) -> str | None:
        """Fetch compressed codebase intel for the project."""
        project_id = task.get("project_id")
        if not project_id:
            return None
        try:
            if self._codebase_intel is None:
                from ..discovery.codebase_intel import CodebaseIntelligence
                self._codebase_intel = CodebaseIntelligence()
            return await self._codebase_intel.get_compressed_summary(
                project_id=project_id,
                max_tokens=1000,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch codebase intel (non-fatal): {e}")
            return None

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
    def _format_agent_definition(agent_definition: dict[str, Any]) -> str | None:
        """Render the agent role preamble from an agent definition.

        Uses the definition's prompt_template if present, interpolating
        {task_title} and {task_description} tokens. Falls back to a short
        capabilities summary when no template is defined.
        """
        if not agent_definition:
            return None

        name = agent_definition.get("name") or ""
        template = (agent_definition.get("prompt_template") or "").strip()
        capabilities: list[str] = agent_definition.get("capabilities") or []

        if not name and not template and not capabilities:
            return None

        lines: list[str] = [f"## Agent Role: {name}", ""] if name else ["## Agent Role", ""]

        if template:
            lines.append(template)
        elif capabilities:
            caps_str = ", ".join(capabilities)
            lines.append(f"Specializations: {caps_str}")

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
    def _context_efficiency_hints(task: dict[str, Any], review_feedback: dict[str, Any] | None = None) -> str | None:
        """Generate context efficiency hints based on task metadata.

        Adapts RTK's filtering philosophy into prompt-level guidance for
        cases where RTK cannot intercept (Claude Code built-in tools like
        Read, Grep, Glob bypass PreToolUse hooks).

        Hints are advisory — the CC runtime decides whether to follow them.
        Injected between task context and compaction hints (CI-6).

        See: docs/design/what-we-adopt-from-rtk.md § B-1
        """
        hints: list[str] = []

        # Retry execution: focus on errors, don't re-read everything
        retry_count = task.get("retry_count") or 0
        if retry_count > 0:
            hints.append(
                "- **Retry efficiency:** Focus on error messages and test failures. "
                "Do not re-read entire test suites or build logs from the previous attempt. "
                "Summarize what changed, not what stayed the same."
            )

        # Large scope: use targeted searches
        allowed_paths = task.get("allowed_paths") or []
        if len(allowed_paths) > 20:
            hints.append(
                "- **Large scope:** Use targeted searches (`grep -r \"pattern\" path/`) "
                "rather than recursive directory listings. Prefer specific file reads "
                "over broad exploration."
            )

        # Test execution tasks: summarize passing, detail failures
        task_type = (task.get("task_type") or "").lower()
        description = (task.get("description") or "").lower()
        has_test_context = any(
            kw in task_type or kw in description
            for kw in ("test", "bug", "fix", "implementation", "feature")
        )
        if has_test_context:
            hints.append(
                "- **Test output:** If all tests pass, report the summary count only. "
                "On failure, focus on failing test names, error messages, and relevant "
                "stack traces — skip passing test details."
            )

        # Research/exploration tasks: summarize as you go
        if task_type in ("research", "exploration", "spike", "investigate"):
            hints.append(
                "- **Exploration efficiency:** Summarize findings as you go. After reading "
                "a file, note the key takeaways. Avoid re-reading files unless a specific "
                "section needs re-examination."
            )

        if not hints:
            return None

        return "\n".join([
            "## Context Efficiency",
            "Follow these guidelines to preserve context window space:",
            "",
            *hints,
        ])

    @staticmethod
    def _compaction_hint(previous_stage: str, next_stage: str) -> str:
        """Generate a context compaction hint at a stage transition boundary.

        Suggests to the runner that when Claude Code compacts its context at this
        boundary, it should preserve the key cross-stage artifacts. The hint is
        advisory — the runner is free to include or omit it.
        """
        return (
            f"<!-- COMPACTION HINT ({previous_stage} → {next_stage}): "
            "If Claude Code compacts context here, preserve: "
            "task_id, acceptance criteria, files modified, test results. -->"
        )

    @staticmethod
    def _format_locked_contract(task: dict[str, Any]) -> str | None:
        """Render locked contract criteria as execution context.

        Prefers formal contract criteria from ``_formal_contract_criteria``
        (loaded from archon_task_contracts via current_contract_id).  Falls
        back to the JSONB mirror at ``architect_review.locked_contract`` when
        no formal criteria are available.  (H4-R1: formal-contract-first.)
        """
        # 1. Prefer formal contract criteria (DB-sourced via current_contract_id)
        locked_contract = task.get("_formal_contract_criteria")

        # 2. Fallback to JSONB mirror on architect_review
        if not isinstance(locked_contract, list) or not locked_contract:
            architect_review = task.get("architect_review")
            if isinstance(architect_review, dict):
                locked_contract = architect_review.get("locked_contract")

        if not isinstance(locked_contract, list) or not locked_contract:
            return None

        lines = [
            "## Locked Contract",
            "Execute against the following locked criteria. Do not propose a new contract.",
            "",
        ]
        for i, criterion in enumerate(locked_contract, 1):
            if not isinstance(criterion, dict):
                continue
            text = criterion.get("criterion", "")
            threshold = criterion.get("threshold", "")
            line = f"{i}. {text}"
            if threshold:
                line += f" — pass condition: {threshold}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _format_editing_boundaries(task: dict[str, Any]) -> str | None:
        """Render task-level allowed/forbidden path guidance for the runner."""
        allowed_paths = normalize_path_rules(task.get("allowed_paths"))
        forbidden_paths = normalize_path_rules(task.get("forbidden_paths"))
        if not allowed_paths and not forbidden_paths:
            return None

        lines = [
            "## Editing Boundaries",
            "Respect the path scope below. If the requested solution requires files outside this boundary, stop and report the conflict instead of editing them.",
            "",
        ]

        if allowed_paths:
            lines.append("Allowed paths:")
            lines.extend(f"- `{rule}`" for rule in allowed_paths)
            lines.append("")

        if forbidden_paths:
            lines.append("Forbidden paths:")
            lines.extend(f"- `{rule}`" for rule in forbidden_paths)

        return "\n".join(lines).rstrip()

    @staticmethod
    def _format_repo_guidance_packs(task: dict[str, Any]) -> str | None:
        """Render repo-scoped guidance packs for runner execution."""
        packs = task.get("repo_guidance_packs") or []
        if not isinstance(packs, list) or not packs:
            return None

        lines: list[str] = [
            "## Repository Guidance Packs",
            "Apply these repo-specific implementation rules together with the task requirements and editing boundaries.",
            "",
        ]

        rendered_any = False
        for pack in packs:
            if not isinstance(pack, dict):
                continue

            title = str(pack.get("title") or "").strip()
            guidance = str(pack.get("guidance") or pack.get("text") or "").strip()
            if not title or not guidance:
                continue

            rendered_any = True
            lines.append(f"### {title}")
            path_scope = normalize_path_rules(pack.get("path_scope"))
            if path_scope:
                lines.append("Path scope:")
                lines.extend(f"- `{rule}`" for rule in path_scope)
            lines.append(guidance)
            lines.append("")

        if not rendered_any:
            return None

        return "\n".join(lines).rstrip()

    @staticmethod
    def _classify_retry_direction(
        feedback: dict[str, Any] | None,
        rejection_reason: str | None,
    ) -> str:
        """Classify the retry direction based on review feedback and rejection reason.

        Returns "pivot" when the overall approach should change; "refine" otherwise.
        Pivot triggers: score < 3.0, or pivot/rethink/rewrite keywords in feedback or rejection.
        """
        _pivot_keywords = {"pivot", "rethink", "rewrite", "redesign", "fundamental"}

        if feedback is not None:
            score = feedback.get("overall_score")
            if score is not None and float(score) < 3.0:
                return "pivot"
            direction = (feedback.get("suggested_retry_direction") or "").lower()
            if any(kw in direction for kw in _pivot_keywords):
                return "pivot"

        if rejection_reason:
            rejection_lower = rejection_reason.lower()
            if any(kw in rejection_lower for kw in _pivot_keywords):
                return "pivot"

        return "refine"

    @staticmethod
    def _format_structured_feedback(feedback: dict[str, Any]) -> str:
        """Render structured review feedback as a human-readable retry context block."""
        lines: list[str] = []
        score = feedback.get("overall_score")
        verdict = feedback.get("verdict", "changes-requested")
        reviewer = feedback.get("reviewer_identity", "reviewer")
        direction = feedback.get("suggested_retry_direction") or ""

        lines.append(f"**Review score:** {score}/10 | **Verdict:** {verdict} | **Reviewer:** {reviewer}")

        findings = feedback.get("findings") or []
        if findings:
            lines.append("")
            lines.append("**Per-criterion scores:**")
            for f in findings:
                if not isinstance(f, dict):
                    continue
                criterion = f.get("criterion", "?")
                f_score = f.get("score", 0)
                passed = f.get("passed", False)
                details = f.get("details", "")
                evidence = f.get("evidence", "")
                marker = "✓" if passed else "✗"
                line = f"  {marker} {criterion}: {int(f_score)}/10"
                if details:
                    line += f" — {details}"
                if not passed and evidence:
                    line += f" ({evidence})"
                lines.append(line)

        if direction:
            lines.append("")
            lines.append(f"**Suggested focus:** {direction}")

        return "\n".join(lines)

    def _format_retry_feedback(
        self,
        task: dict[str, Any],
        review_feedback: dict[str, Any] | None = None,
    ) -> str | None:
        """Format retry context with failure details, limited to MAX_RETRY_CONTEXT_TOKENS.

        Includes: structured review feedback (when available), previous execution result,
        architect feedback, rejection reason, relevant failure learnings, and an explicit
        "avoid repeating" instruction.
        """
        retry_count = task.get("retry_count") or 0
        if retry_count == 0:
            return None

        rejection = task.get("rejection_reason")
        direction = self._classify_retry_direction(review_feedback, rejection)
        sections: list[str] = [
            f"## Previous Attempt Failed (attempt {retry_count}) — {direction.upper()}",
            "",
        ]

        # 1. Structured review feedback (primary source — replaces ad-hoc architect_review field)
        if review_feedback is not None:
            sections.append(self._format_structured_feedback(review_feedback))
            sections.append("")
        else:
            sections.append(f"_Retry attempt {retry_count} — no structured feedback available._")

        # 2. Extract failure details from execution_result
        prev_result = task.get("execution_result")
        if isinstance(prev_result, dict):
            error_msg = prev_result.get("summary") or prev_result.get("error")
            if error_msg:
                sections.append(f"**What failed:** {error_msg}")
            run_summary = prev_result.get("run_result_summary")
            if run_summary and run_summary != error_msg:
                sections.append(f"**Previous run:** {run_summary}")
            stderr = prev_result.get("stderr_preview")
            if stderr:
                sections.append(f"**Error output:** {stderr[:300]}")
            result_status = prev_result.get("result", "")
            if result_status:
                sections.append(f"**Result status:** {result_status}")
            findings = prev_result.get("review_findings") or prev_result.get("findings")
            if isinstance(findings, list) and findings:
                critical = [f for f in findings if isinstance(f, dict) and f.get("severity") == "critical"]
                if critical:
                    sections.append("**Critical findings:**")
                    for f in critical[:3]:
                        sections.append(f"- {f.get('description', '')[:100]}")

        # 3. Architect feedback (fallback when no structured feedback)
        if review_feedback is None:
            review = task.get("architect_review")
            if isinstance(review, dict):
                feedback = review.get("feedback") or review.get("comments")
                if feedback:
                    sections.append(f"**Architect feedback:** {feedback}")

        # 4. Rejection reason
        if rejection:
            sections.append(f"**Rejection reason:** {rejection}")

        # 5. Fetch failure-relevant learnings from same task type/keywords
        failure_learnings = self._fetch_failure_learnings(task, prev_result if isinstance(prev_result, dict) else None)
        if failure_learnings:
            sections.append("")
            sections.append("**Relevant learnings from similar past failures:**")
            for learning in failure_learnings[:3]:
                desc = (learning.get("description") or "")[:100]
                sections.append(f"- {desc}")

        # 6. Partial context from a previous timeout — give the retry a head start
        partial_context = prev_result.get("partial_context") if isinstance(prev_result, dict) else None
        if partial_context:
            sections.append("")
            sections.append("**## PREVIOUS_ATTEMPT — partial work context (continue, don't restart)**")
            files_modified = partial_context.get("files_modified") or []
            if files_modified:
                files_list = ", ".join(files_modified[:5])
                sections.append(f"Files modified before timeout: {files_list}")
            partial_output = partial_context.get("partial_output")
            if partial_output:
                sections.append(f"Last agent message: {partial_output[:300]}")
            reason = partial_context.get("reason")
            if reason:
                sections.append(f"Stopped because: {reason}")
            sections.append("Pick up from where the previous attempt left off.")

        # 7. Explicit "avoid repeating" instruction
        review = task.get("architect_review") if isinstance(task.get("architect_review"), dict) else None
        avoid_items = self._extract_avoid_items(
            prev_result if isinstance(prev_result, dict) else None, review, rejection
        )
        if avoid_items:
            sections.append("")
            sections.append("**Avoid repeating:**")
            for item in avoid_items[:3]:
                sections.append(f"- {item}")

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

    @staticmethod
    def build_team_prompt(base_prompt: str, team_policy: dict[str, Any]) -> str:
        """Wrap an execution prompt in Agent Team orchestration instructions.

        When team_policy is enabled for a project, complex tasks are executed
        by a CC Agent Team instead of a single session. The team lead spawns
        coder, tester, and reviewer teammates that work in parallel within
        the same worktree.

        The wrapped prompt tells the CC lead session to:
        1. Spawn teammates using the agent definitions in .claude/agents/team/
        2. Coordinate work: coder first → tester → reviewer
        3. Enforce the golden rule: no file overlap between teammates
        4. Synthesize results into the standard output format
        """
        team_size = team_policy.get("team_size", 3)
        coder_model = team_policy.get("coder_model", "claude-sonnet-4-6")
        reviewer_model = team_policy.get("reviewer_model", "claude-opus-4-6")

        return "\n".join([
            "# Agent Team Execution Mode",
            "",
            "You have Agent Teams enabled. Create a team to execute this task:",
            "",
            "## Team Structure",
            f"Spawn {team_size} teammates:",
            f"1. **Coder** (model: {coder_model}) — using the `coder` agent type",
            "   - Implement the changes in the assigned domain files",
            "   - Run compile + unit tests after each change",
            "   - Report completion when done",
            "",
            f"2. **Tester** (model: {coder_model}) — using the `tester` agent type",
            "   - Wait for Coder to complete",
            "   - Write unit + integration tests for all changes",
            "   - Cover: happy path, edge cases, error handling, security",
            "   - Run full test suite and report results",
            "",
            f"3. **Reviewer** (model: {reviewer_model}) — using the `reviewer` agent type",
            "   - Wait for Tester to complete",
            "   - Cross-review all production code and test code",
            "   - Check: consistency, security, performance, patterns",
            "   - Report findings with severity ratings",
            "",
            "## Coordination Rules",
            "- **GOLDEN RULE**: No file overlap between teammates",
            "- Coder: ONLY production code files",
            "- Tester: ONLY test files",
            "- Reviewer: READ ONLY — never modify any files",
            "- If blocked → message the lead (you)",
            "",
            "## Task to Accomplish",
            "---",
            base_prompt,
            "---",
            "",
            "## Output Requirements",
            "After ALL teammates complete, synthesize their results.",
            "Produce the standard structured output:",
            "- SELF_REVIEW: PASS/FAIL (based on reviewer findings)",
            "- REVIEW_CONFIDENCE: 0.0-1.0",
            "- REVIEW_FINDINGS: JSON array of reviewer findings",
            "- RESULT: SUCCESS/FAILURE",
            "- FILES_CHANGED: total count from coder + tester",
            "- TESTS_ADDED: count from tester",
            "- SUMMARY: combined summary of all teammates' work",
        ])

    def _render_compressed(
        self,
        task: dict[str, Any],
        project: dict[str, Any] | None,
        kb_chunks: list[dict[str, Any]],
        build_command: str,
        code_patterns: list[dict[str, Any]] | None = None,
        learnings: list[dict[str, Any]] | None = None,
        agent_definition: dict[str, Any] | None = None,
        review_feedback: dict[str, Any] | None = None,
        intel_summary: str | None = None,
        wakeup_context: str = "",
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

        parts: list[str] = []

        # Wake-up context — agent identity, project context, top learnings, recent runs
        if wakeup_context:
            parts += [wakeup_context, "", "---", ""]

        parts += [
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

        retry_section = self._format_retry_feedback(task, review_feedback=review_feedback)
        if retry_section:
            parts += [retry_section, ""]

        agent_section = self._format_agent_definition(agent_definition)
        if agent_section:
            parts += [agent_section]

        parts += [
            "## Requirements",
            description,
            "",
        ]

        boundary_section = self._format_editing_boundaries(task)
        if boundary_section:
            parts += [boundary_section, ""]

        repo_guidance_section = self._format_repo_guidance_packs(task)
        if repo_guidance_section:
            parts += [repo_guidance_section, ""]

        # Codebase intelligence (Phase 6: cold-start reduction)
        if intel_summary:
            parts += [intel_summary, ""]

        if exec_prompt:
            parts += ["## Execution Strategy", exec_prompt, ""]

        # Context efficiency hints (B-1: RTK-inspired prompt guidance)
        efficiency_section = self._context_efficiency_hints(task, review_feedback=review_feedback)
        if efficiency_section:
            parts += [efficiency_section, ""]

        parts += [
            "## Acceptance Criteria",
            self._format_acceptance_criteria(task),
            "",
        ]

        locked_contract_section = self._format_locked_contract(task)
        if locked_contract_section:
            parts += [locked_contract_section, ""]

        parts += [
            _COMPACT_REPORT_TEMPLATE.format(
                build_command=build_command,
                execute_self_review_hint=self._compaction_hint("execute", "self-review"),
                self_review_code_review_hint=self._compaction_hint("self-review", "code-review"),
            ),
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
        agent_definition: dict[str, Any] | None = None,
        review_feedback: dict[str, Any] | None = None,
        intel_summary: str | None = None,
        wakeup_context: str = "",
    ) -> str:
        title = task.get("title", "Untitled")
        priority = task.get("priority", "medium")
        assignee = task.get("assignee", "Agent")
        source_app = task.get("source_app") or (project or {}).get("title") or "unknown"
        description = task.get("description") or "_No description provided._"
        exec_prompt = task.get("execution_prompt") or ""

        parts: list[str] = []

        # Wake-up context — agent identity, project context, top learnings, recent runs
        if wakeup_context:
            parts += [wakeup_context, "", "---", ""]

        parts += [
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

        retry_section = self._format_retry_feedback(task, review_feedback=review_feedback)
        if retry_section:
            parts += [retry_section, ""]

        agent_section = self._format_agent_definition(agent_definition)
        if agent_section:
            parts += [agent_section]

        parts += [
            "## Requirements",
            description,
            "",
        ]

        boundary_section = self._format_editing_boundaries(task)
        if boundary_section:
            parts += [boundary_section, ""]

        repo_guidance_section = self._format_repo_guidance_packs(task)
        if repo_guidance_section:
            parts += [repo_guidance_section, ""]

        # Codebase intelligence (Phase 6: cold-start reduction)
        if intel_summary:
            parts += [intel_summary, ""]

        if exec_prompt:
            parts += ["## Execution Strategy", exec_prompt, ""]

        parts += [
            "## Acceptance Criteria",
            self._format_acceptance_criteria(task),
            "",
        ]

        locked_contract_section = self._format_locked_contract(task)
        if locked_contract_section:
            parts += [locked_contract_section, ""]

        parts += [
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
            self._compaction_hint("execute", "self-review"),
            "",
            "## Self-Review (REQUIRED after implementation)",
            "Review your own changes before reporting:",
            "1. Check each acceptance criteria — all must pass",
            "2. Security scan: XSS, injection, auth bypass, key exposure",
            "3. Backward compatibility: existing APIs must not break",
            "4. Performance: no N+1 queries, no large allocations",
            "",
            self._compaction_hint("self-review", "code-review"),
            "",
            "## Structured Reflection (REQUIRED before reporting completion)",
            "",
            "Before reporting results, answer each question. For every YES,",
            "record a structured learning. Skip questions with no finding.",
            "",
            "1. **Unexpected failures:** Did any command, tool call, or API",
            "   fail unexpectedly? What was the root cause?",
            "2. **Backtracking:** Did you try an approach that didn't work",
            "   and had to change direction? What was wrong with the first",
            "   approach and why did the alternative succeed?",
            "3. **Project quirks:** Did you discover a project-specific",
            "   behavior (build order, env vars, timing, auth, config)?",
            "4. **Time sinks:** Did anything take longer than expected",
            "   because of a missing flag, config, or undocumented behavior?",
            "5. **Contract/schema gaps:** Did you hit a runtime mismatch",
            "   that the type system did not catch? (schema drift, missing",
            "   field, enum mismatch)",
            "6. **Guidance gaps:** Was the guidance pack missing, outdated,",
            "   or incorrect about anything? What should it say instead?",
            "",
            "**Quality gate:** Only log findings that would save a future",
            "run >= 5 minutes. Do not log one-time transient errors or",
            "obvious things.",
            "",
            "## Code Patterns (REQUIRED in output)",
            "Extract reusable expert-level code patterns:",
            "- Patterns that solve common problems elegantly",
            "- Error handling approaches worth standardizing",
            "- Security patterns that should be replicated",
            "- Testing patterns that ensure quality",
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
            '  {"type":"error|correction|best_practice|knowledge_gap|pitfall|operational|tool|architecture_insight",',
            '   "description":"concise one-sentence description",',
            '   "area":"frontend|backend|infra|tests|config|security|database",',
            '   "confidence": 1-10,',
            '   "source":"observed|inferred|user_stated",',
            '   "files":["path/to/relevant/file.py"],',
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
            '   "source_files":["file1.py","file2.py"]}',
            "]",
            "If no patterns, output: CODE_PATTERNS: []",
        ]

        return "\n".join(parts)
