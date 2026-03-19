"""
Context Compressor for Task Engine prompts.

Reduces prompt token usage through:
- Token budget estimation and enforcement
- KB chunk filtering by relevance score
- Code pattern truncation
- Adaptive content reduction when over budget

Target: 30-50% cost reduction per task execution.
"""

from typing import Any

from ...config.logfire_config import get_logger

logger = get_logger(__name__)

# Approximate tokens per character (GPT/Claude tokenizer heuristic)
CHARS_PER_TOKEN = 4

# Default token budget for the entire prompt (excluding what CC reads from disk)
DEFAULT_TOKEN_BUDGET = 2000

# Minimum relevance score to include a KB chunk
DEFAULT_MIN_RELEVANCE = 0.65

# Maximum chars for a code pattern example in compressed mode
MAX_PATTERN_CODE_LENGTH = 300


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // CHARS_PER_TOKEN


def filter_kb_chunks(
    chunks: list[dict[str, Any]],
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    max_chunks: int = 5,
) -> list[dict[str, Any]]:
    """Filter KB chunks by relevance score, keeping only high-value ones.

    Returns chunks sorted by score descending, capped at max_chunks.
    """
    scored = []
    for chunk in chunks:
        score = chunk.get("similarity_score") or chunk.get("similarity") or 0
        if score >= min_relevance:
            scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored[:max_chunks]]


def compress_kb_chunk(chunk: dict[str, Any], max_length: int = 300) -> dict[str, Any]:
    """Compress a KB chunk by trimming content to essential information."""
    content = (chunk.get("content") or "")[:max_length]
    # Strip trailing incomplete sentence
    if len(content) == max_length:
        last_period = content.rfind(".")
        last_newline = content.rfind("\n")
        cut = max(last_period, last_newline)
        if cut > max_length // 2:
            content = content[: cut + 1]

    return {**chunk, "content": content}


def compress_code_patterns(
    patterns: list[dict[str, Any]],
    max_patterns: int = 3,
    max_code_length: int = MAX_PATTERN_CODE_LENGTH,
) -> list[dict[str, Any]]:
    """Compress code patterns: limit count and truncate code examples."""
    compressed = []
    for p in patterns[:max_patterns]:
        code = (p.get("code_example") or "")[:max_code_length]
        compressed.append({**p, "code_example": code})
    return compressed


def compress_learnings(
    learnings: list[dict[str, Any]],
    max_learnings: int = 10,
    max_desc_length: int = 150,
) -> list[dict[str, Any]]:
    """Compress learnings: limit count and truncate descriptions."""
    compressed = []
    for learning in learnings[:max_learnings]:
        desc = (learning.get("description") or "")[:max_desc_length]
        compressed.append({**learning, "description": desc})
    return compressed


def apply_token_budget(
    kb_chunks: list[dict[str, Any]],
    code_patterns: list[dict[str, Any]],
    task_text: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    learnings: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Adaptively reduce context to fit within token budget.

    Strategy (progressive shedding):
    1. Filter KB chunks by relevance score
    2. Compress KB chunks, code patterns, and learnings
    3. If still over budget, reduce KB chunk count
    4. If still over budget, drop learnings
    5. If still over budget, drop code patterns entirely

    Returns:
        Tuple of (filtered_kb_chunks, filtered_patterns, filtered_learnings, metrics)
    """
    learnings = learnings or []

    metrics: dict[str, Any] = {
        "original_kb_chunks": len(kb_chunks),
        "original_patterns": len(code_patterns),
        "original_learnings": len(learnings),
        "token_budget": token_budget,
    }

    # Step 1: Filter by relevance
    filtered_kb = filter_kb_chunks(kb_chunks, min_relevance=min_relevance)
    metrics["post_filter_kb_chunks"] = len(filtered_kb)

    # Step 2: Compress
    compressed_kb = [compress_kb_chunk(c) for c in filtered_kb]
    compressed_patterns = compress_code_patterns(code_patterns)
    compressed_learnings = compress_learnings(learnings)

    # Estimate current token usage
    kb_text = "\n".join(c.get("content", "") for c in compressed_kb)
    pattern_text = "\n".join(p.get("code_example", "") for p in compressed_patterns)
    learning_text = "\n".join(item.get("description", "") for item in compressed_learnings)
    total_tokens = estimate_tokens(task_text + kb_text + pattern_text + learning_text)
    metrics["estimated_tokens_after_compress"] = total_tokens

    # Step 3: Progressively shed KB chunks if over budget
    while total_tokens > token_budget and len(compressed_kb) > 1:
        compressed_kb.pop()  # Drop lowest-relevance (already sorted desc)
        kb_text = "\n".join(c.get("content", "") for c in compressed_kb)
        total_tokens = estimate_tokens(task_text + kb_text + pattern_text + learning_text)

    # Step 4: Drop learnings if still over budget
    if total_tokens > token_budget and compressed_learnings:
        compressed_learnings = []
        learning_text = ""
        total_tokens = estimate_tokens(task_text + kb_text + pattern_text + learning_text)

    # Step 5: Drop patterns if still over budget
    if total_tokens > token_budget and compressed_patterns:
        compressed_patterns = []
        pattern_text = ""
        total_tokens = estimate_tokens(task_text + kb_text + pattern_text)

    metrics["final_kb_chunks"] = len(compressed_kb)
    metrics["final_patterns"] = len(compressed_patterns)
    metrics["final_learnings"] = len(compressed_learnings)
    metrics["final_estimated_tokens"] = total_tokens

    reduction = metrics["original_kb_chunks"] + metrics["original_patterns"] + metrics["original_learnings"]
    final = metrics["final_kb_chunks"] + metrics["final_patterns"] + metrics["final_learnings"]
    if reduction > 0:
        metrics["reduction_pct"] = round((1 - final / reduction) * 100, 1)
    else:
        metrics["reduction_pct"] = 0.0

    return compressed_kb, compressed_patterns, compressed_learnings, metrics
