"""Tests for ExecutionHealthMonitor — loop detection and budget enforcement."""

import pytest

from src.server.services.engine.execution_health_monitor import (
    HEALTH_BUDGET_EXCEEDED,
    HEALTH_HEALTHY,
    HEALTH_LOOP_DETECTED,
    ExecutionHealthMonitor,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _tool_event(tool_name: str) -> dict:
    return {"event": "tool_use", "tool_name": tool_name, "args_summary": ""}


def _other_event(event_type: str = "assistant") -> dict:
    return {"event": event_type, "message": "thinking..."}


# ── Construction ─────────────────────────────────────────────────────────────


def test_default_thresholds():
    monitor = ExecutionHealthMonitor()
    assert monitor.same_tool_limit == 5
    assert monitor.total_limit == 30


def test_custom_thresholds():
    monitor = ExecutionHealthMonitor(same_tool_limit=3, total_limit=10)
    assert monitor.same_tool_limit == 3
    assert monitor.total_limit == 10


def test_env_thresholds(monkeypatch):
    monkeypatch.setenv("LEANKIT_EXECUTION_HEALTH_SAME_TOOL_LIMIT", "7")
    monkeypatch.setenv("LEANKIT_EXECUTION_HEALTH_TOTAL_LIMIT", "50")
    monitor = ExecutionHealthMonitor()
    assert monitor.same_tool_limit == 7
    assert monitor.total_limit == 50


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LEANKIT_EXECUTION_HEALTH_SAME_TOOL_LIMIT", "not-a-number")
    monitor = ExecutionHealthMonitor()
    assert monitor.same_tool_limit == 5


# ── Healthy baseline ──────────────────────────────────────────────────────────


def test_healthy_with_no_events():
    monitor = ExecutionHealthMonitor(same_tool_limit=5, total_limit=30)
    assert monitor.check_health("t-1") == HEALTH_HEALTHY


def test_healthy_below_limits():
    monitor = ExecutionHealthMonitor(same_tool_limit=5, total_limit=30)
    for _ in range(5):  # exactly at limit — should still be healthy (> not >=)
        monitor.on_stream_event("t-1", _tool_event("Bash"))
    assert monitor.check_health("t-1") == HEALTH_HEALTHY


def test_non_tool_events_are_ignored():
    monitor = ExecutionHealthMonitor(same_tool_limit=2, total_limit=5)
    for _ in range(10):
        monitor.on_stream_event("t-1", _other_event("assistant"))
        monitor.on_stream_event("t-1", _other_event("thinking"))
    assert monitor.check_health("t-1") == HEALTH_HEALTHY


def test_tool_event_without_tool_name_ignored():
    monitor = ExecutionHealthMonitor(same_tool_limit=2, total_limit=5)
    monitor.on_stream_event("t-1", {"event": "tool_use", "tool_name": ""})
    assert monitor.check_health("t-1") == HEALTH_HEALTHY


# ── Loop detection ────────────────────────────────────────────────────────────


def test_loop_detected_when_same_tool_exceeds_limit():
    monitor = ExecutionHealthMonitor(same_tool_limit=3, total_limit=30)
    for _ in range(4):  # 4 > 3
        monitor.on_stream_event("t-1", _tool_event("Read"))
    assert monitor.check_health("t-1") == HEALTH_LOOP_DETECTED


def test_loop_detected_reason_includes_tool_name():
    monitor = ExecutionHealthMonitor(same_tool_limit=2, total_limit=30)
    for _ in range(3):
        monitor.on_stream_event("t-1", _tool_event("Bash"))
    monitor.check_health("t-1")
    reason = monitor.get_abort_reason("t-1")
    assert "Bash" in reason
    assert "3" in reason


def test_loop_detected_only_for_offending_tool():
    monitor = ExecutionHealthMonitor(same_tool_limit=3, total_limit=30)
    for _ in range(3):
        monitor.on_stream_event("t-1", _tool_event("Read"))  # exactly at limit — OK
    for _ in range(2):
        monitor.on_stream_event("t-1", _tool_event("Write"))  # under limit — OK
    assert monitor.check_health("t-1") == HEALTH_HEALTHY

    monitor.on_stream_event("t-1", _tool_event("Read"))  # now 4 > 3
    assert monitor.check_health("t-1") == HEALTH_LOOP_DETECTED


# ── Budget exceeded ───────────────────────────────────────────────────────────


def test_budget_exceeded_when_total_exceeds_limit():
    monitor = ExecutionHealthMonitor(same_tool_limit=100, total_limit=5)
    tools = ["Read", "Write", "Bash", "Grep", "Glob"]
    for tool in tools:
        monitor.on_stream_event("t-1", _tool_event(tool))
    # 5 == limit, not yet exceeded
    assert monitor.check_health("t-1") == HEALTH_HEALTHY

    monitor.on_stream_event("t-1", _tool_event("Search"))  # 6 > 5
    assert monitor.check_health("t-1") == HEALTH_BUDGET_EXCEEDED


def test_budget_exceeded_reason_includes_counts():
    monitor = ExecutionHealthMonitor(same_tool_limit=100, total_limit=3)
    for i in range(4):
        monitor.on_stream_event("t-1", _tool_event(f"tool_{i}"))
    monitor.check_health("t-1")
    reason = monitor.get_abort_reason("t-1")
    assert "4" in reason
    assert "3" in reason


# ── Loop takes priority over budget ───────────────────────────────────────────


def test_loop_detected_takes_priority_over_budget():
    monitor = ExecutionHealthMonitor(same_tool_limit=2, total_limit=3)
    # 3 Bash calls — triggers loop (> 2) AND budget (3 == limit, not yet)
    for _ in range(3):
        monitor.on_stream_event("t-1", _tool_event("Bash"))
    # Loop should be flagged first
    assert monitor.check_health("t-1") == HEALTH_LOOP_DETECTED


# ── Task isolation ────────────────────────────────────────────────────────────


def test_metrics_isolated_per_task():
    monitor = ExecutionHealthMonitor(same_tool_limit=3, total_limit=30)
    for _ in range(4):
        monitor.on_stream_event("t-1", _tool_event("Bash"))
    for _ in range(2):
        monitor.on_stream_event("t-2", _tool_event("Bash"))

    assert monitor.check_health("t-1") == HEALTH_LOOP_DETECTED
    assert monitor.check_health("t-2") == HEALTH_HEALTHY


# ── Reset ─────────────────────────────────────────────────────────────────────


def test_reset_clears_metrics():
    monitor = ExecutionHealthMonitor(same_tool_limit=3, total_limit=30)
    for _ in range(4):
        monitor.on_stream_event("t-1", _tool_event("Bash"))
    assert monitor.check_health("t-1") == HEALTH_LOOP_DETECTED

    monitor.reset("t-1")
    assert monitor.check_health("t-1") == HEALTH_HEALTHY


def test_reset_nonexistent_task_is_safe():
    monitor = ExecutionHealthMonitor()
    monitor.reset("t-nonexistent")  # should not raise


# ── get_abort_reason ─────────────────────────────────────────────────────────


def test_get_abort_reason_before_any_violation_returns_default():
    monitor = ExecutionHealthMonitor()
    reason = monitor.get_abort_reason("t-unknown")
    assert reason  # non-empty default
    assert "limit" in reason.lower()


# ── get_metrics_snapshot ─────────────────────────────────────────────────────


def test_metrics_snapshot_reflects_recorded_calls():
    monitor = ExecutionHealthMonitor()
    monitor.on_stream_event("t-1", _tool_event("Bash"))
    monitor.on_stream_event("t-1", _tool_event("Bash"))
    monitor.on_stream_event("t-1", _tool_event("Read"))
    snapshot = monitor.get_metrics_snapshot("t-1")
    assert snapshot["total_count"] == 3
    assert snapshot["tool_counts"]["Bash"] == 2
    assert snapshot["tool_counts"]["Read"] == 1


def test_metrics_snapshot_for_unknown_task():
    monitor = ExecutionHealthMonitor()
    snapshot = monitor.get_metrics_snapshot("t-unknown")
    assert snapshot["total_count"] == 0
    assert snapshot["tool_counts"] == {}
