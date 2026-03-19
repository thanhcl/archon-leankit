"""Tests for context_compressor — KB filtering, pattern compression, learnings compression, token budget."""

import pytest

from src.server.services.engine.context_compressor import (
    apply_token_budget,
    compress_code_patterns,
    compress_kb_chunk,
    compress_learnings,
    estimate_tokens,
    filter_kb_chunks,
)


# -- Token estimation --


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_basic():
    # 100 chars / 4 = 25 tokens
    assert estimate_tokens("a" * 100) == 25


# -- KB chunk filtering --


def _kb_chunk(content: str, score: float, source: str = "test") -> dict:
    return {
        "content": content,
        "metadata": {"url": source},
        "similarity_score": score,
    }


def test_filter_kb_chunks_removes_low_relevance():
    chunks = [
        _kb_chunk("high", 0.9),
        _kb_chunk("mid", 0.7),
        _kb_chunk("low", 0.3),
    ]
    result = filter_kb_chunks(chunks, min_relevance=0.65)
    assert len(result) == 2
    assert result[0]["content"] == "high"
    assert result[1]["content"] == "mid"


def test_filter_kb_chunks_sorted_by_score_desc():
    chunks = [
        _kb_chunk("a", 0.7),
        _kb_chunk("b", 0.95),
        _kb_chunk("c", 0.8),
    ]
    result = filter_kb_chunks(chunks, min_relevance=0.0)
    scores = [c["similarity_score"] for c in result]
    assert scores == [0.95, 0.8, 0.7]


def test_filter_kb_chunks_respects_max():
    chunks = [_kb_chunk(f"c{i}", 0.9) for i in range(10)]
    result = filter_kb_chunks(chunks, max_chunks=3)
    assert len(result) == 3


def test_filter_kb_chunks_empty():
    assert filter_kb_chunks([]) == []


def test_filter_kb_chunks_all_below_threshold():
    chunks = [_kb_chunk("x", 0.1), _kb_chunk("y", 0.2)]
    result = filter_kb_chunks(chunks, min_relevance=0.5)
    assert result == []


def test_filter_kb_chunks_uses_similarity_fallback():
    """Handles chunks with 'similarity' key instead of 'similarity_score'."""
    chunk = {"content": "test", "metadata": {}, "similarity": 0.8}
    result = filter_kb_chunks([chunk], min_relevance=0.7)
    assert len(result) == 1


# -- KB chunk compression --


def test_compress_kb_chunk_truncates():
    chunk = _kb_chunk("A" * 1000, 0.9)
    result = compress_kb_chunk(chunk, max_length=200)
    assert len(result["content"]) <= 200


def test_compress_kb_chunk_cuts_at_sentence():
    content = "First sentence. Second sentence. Third sentence that is quite long."
    chunk = _kb_chunk(content, 0.9)
    result = compress_kb_chunk(chunk, max_length=50)
    # Should cut at a period boundary
    assert result["content"].endswith(".")


def test_compress_kb_chunk_preserves_short():
    chunk = _kb_chunk("short", 0.9)
    result = compress_kb_chunk(chunk, max_length=200)
    assert result["content"] == "short"


def test_compress_kb_chunk_preserves_metadata():
    chunk = _kb_chunk("text", 0.9, source="http://example.com")
    result = compress_kb_chunk(chunk)
    assert result["similarity_score"] == 0.9
    assert result["metadata"]["url"] == "http://example.com"


# -- Code pattern compression --


def _pattern(name: str, code: str = "x = 1", **kw) -> dict:
    return {"pattern_name": name, "code_example": code, "language": "python", **kw}


def test_compress_code_patterns_limits_count():
    patterns = [_pattern(f"p{i}") for i in range(10)]
    result = compress_code_patterns(patterns, max_patterns=3)
    assert len(result) == 3


def test_compress_code_patterns_truncates_code():
    patterns = [_pattern("big", code="x" * 1000)]
    result = compress_code_patterns(patterns, max_code_length=100)
    assert len(result[0]["code_example"]) == 100


def test_compress_code_patterns_preserves_metadata():
    patterns = [_pattern("test", context="when testing", confidence=0.9)]
    result = compress_code_patterns(patterns)
    assert result[0]["pattern_name"] == "test"
    assert result[0]["context"] == "when testing"


def test_compress_code_patterns_empty():
    assert compress_code_patterns([]) == []


# -- Token budget --


def test_apply_token_budget_filters_low_relevance():
    chunks = [_kb_chunk("hi", 0.9), _kb_chunk("lo", 0.2)]
    kb, patterns, learnings, metrics = apply_token_budget(
        kb_chunks=chunks, code_patterns=[], task_text="task",
        token_budget=5000, min_relevance=0.5,
    )
    assert len(kb) == 1
    assert kb[0]["content"] == "hi"
    assert metrics["original_kb_chunks"] == 2
    assert metrics["post_filter_kb_chunks"] == 1


def test_apply_token_budget_sheds_chunks_when_over():
    # Create chunks with large content to exceed budget
    chunks = [_kb_chunk("A" * 2000, 0.9 - i * 0.01) for i in range(5)]
    kb, patterns, learnings, metrics = apply_token_budget(
        kb_chunks=chunks, code_patterns=[], task_text="task",
        token_budget=200, min_relevance=0.0,
    )
    # Should have reduced chunk count to fit budget
    assert len(kb) < 5
    assert metrics["final_kb_chunks"] < metrics["post_filter_kb_chunks"]


def test_apply_token_budget_drops_patterns_last():
    chunks = [_kb_chunk("A" * 2000, 0.9)]
    patterns = [_pattern("p1", code="x" * 2000)]
    kb, pats, learnings, metrics = apply_token_budget(
        kb_chunks=chunks, code_patterns=patterns, task_text="task" * 100,
        token_budget=50, min_relevance=0.0,
    )
    # When budget is very tight, patterns get dropped
    assert metrics["final_patterns"] == 0


def test_apply_token_budget_within_budget_keeps_all():
    chunks = [_kb_chunk("short", 0.9)]
    patterns = [_pattern("p1")]
    kb, pats, learnings, metrics = apply_token_budget(
        kb_chunks=chunks, code_patterns=patterns, task_text="task",
        token_budget=5000, min_relevance=0.0,
    )
    assert len(kb) == 1
    assert len(pats) == 1
    assert metrics["final_kb_chunks"] == 1
    assert metrics["final_patterns"] == 1


def test_apply_token_budget_empty_inputs():
    kb, pats, learnings, metrics = apply_token_budget(
        kb_chunks=[], code_patterns=[], task_text="task",
        token_budget=5000,
    )
    assert kb == []
    assert pats == []
    assert learnings == []
    assert metrics["reduction_pct"] == 0.0


def test_apply_token_budget_metrics_structure():
    kb, pats, learnings, metrics = apply_token_budget(
        kb_chunks=[_kb_chunk("x", 0.9)],
        code_patterns=[_pattern("p")],
        task_text="task",
    )
    assert "original_kb_chunks" in metrics
    assert "original_patterns" in metrics
    assert "original_learnings" in metrics
    assert "post_filter_kb_chunks" in metrics
    assert "final_kb_chunks" in metrics
    assert "final_patterns" in metrics
    assert "final_learnings" in metrics
    assert "final_estimated_tokens" in metrics
    assert "reduction_pct" in metrics
    assert "token_budget" in metrics


# -- Learnings compression --


def _learning(desc: str, ltype: str = "error", area: str = "backend", recurrence: int = 1) -> dict:
    return {
        "description": desc,
        "type": ltype,
        "area": area,
        "recurrence_count": recurrence,
    }


def test_compress_learnings_limits_count():
    learnings = [_learning(f"learning {i}") for i in range(20)]
    result = compress_learnings(learnings, max_learnings=10)
    assert len(result) == 10


def test_compress_learnings_truncates_description():
    learnings = [_learning("x" * 500)]
    result = compress_learnings(learnings, max_desc_length=100)
    assert len(result[0]["description"]) == 100


def test_compress_learnings_preserves_metadata():
    learnings = [_learning("test desc", ltype="correction", area="frontend", recurrence=3)]
    result = compress_learnings(learnings)
    assert result[0]["type"] == "correction"
    assert result[0]["area"] == "frontend"
    assert result[0]["recurrence_count"] == 3


def test_compress_learnings_empty():
    assert compress_learnings([]) == []


def test_apply_token_budget_with_learnings():
    chunks = [_kb_chunk("short", 0.9)]
    patterns = [_pattern("p1")]
    learnings = [_learning("some learning")]
    kb, pats, lrn, metrics = apply_token_budget(
        kb_chunks=chunks, code_patterns=patterns, task_text="task",
        token_budget=5000, min_relevance=0.0, learnings=learnings,
    )
    assert len(lrn) == 1
    assert metrics["original_learnings"] == 1
    assert metrics["final_learnings"] == 1


def test_apply_token_budget_drops_learnings_before_patterns():
    """Learnings are dropped before patterns when over budget."""
    chunks = [_kb_chunk("A" * 1000, 0.9)]
    patterns = [_pattern("p1", code="x" * 500)]
    learnings = [_learning("y" * 500)]
    kb, pats, lrn, metrics = apply_token_budget(
        kb_chunks=chunks, code_patterns=patterns, task_text="task" * 100,
        token_budget=200, min_relevance=0.0, learnings=learnings,
    )
    # Learnings should be dropped before patterns
    assert metrics["final_learnings"] == 0
