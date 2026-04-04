"""Tests for per-profile tool call budget (C-P6-02)."""

import pytest

from src.server.services.engine.execution_health_monitor import (
    HEALTH_BUDGET_EXCEEDED,
    HEALTH_HEALTHY,
    ExecutionHealthMonitor,
)


class TestPerTaskToolBudget:

    def test_default_limit_used_when_no_override(self):
        monitor = ExecutionHealthMonitor(total_limit=30)
        assert monitor.get_task_total_limit("t1") == 30

    def test_per_task_override(self):
        monitor = ExecutionHealthMonitor(total_limit=30)
        monitor.set_task_total_limit("t1", 15)
        assert monitor.get_task_total_limit("t1") == 15
        assert monitor.get_task_total_limit("t2") == 30  # other tasks unaffected

    def test_budget_exceeded_with_override(self):
        monitor = ExecutionHealthMonitor(total_limit=100)
        monitor.set_task_total_limit("t1", 5)

        # Record 6 tool calls
        for i in range(6):
            monitor.on_stream_event("t1", {"event": "tool_use", "tool_name": f"tool_{i}"})

        status = monitor.check_health("t1")
        assert status == HEALTH_BUDGET_EXCEEDED

    def test_under_budget_with_override(self):
        monitor = ExecutionHealthMonitor(total_limit=100)
        monitor.set_task_total_limit("t1", 10)

        for i in range(5):
            monitor.on_stream_event("t1", {"event": "tool_use", "tool_name": f"tool_{i}"})

        status = monitor.check_health("t1")
        assert status == HEALTH_HEALTHY

    def test_reset_clears_override(self):
        monitor = ExecutionHealthMonitor(total_limit=30)
        monitor.set_task_total_limit("t1", 5)
        monitor.reset("t1")
        assert monitor.get_task_total_limit("t1") == 30  # back to default

    def test_abort_reason_includes_custom_limit(self):
        monitor = ExecutionHealthMonitor(total_limit=100)
        monitor.set_task_total_limit("t1", 3)

        for i in range(4):
            monitor.on_stream_event("t1", {"event": "tool_use", "tool_name": f"tool_{i}"})

        monitor.check_health("t1")
        reason = monitor.get_abort_reason("t1")
        assert "limit 3" in reason
