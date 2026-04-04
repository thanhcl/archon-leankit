"""Tests for CockpitService."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.server.services.projects.cockpit_service import CockpitService

# ── Helpers ────────────────────────────────────────────────────────────────────


def _chain(data=None, error=None):
    """Return a mock Supabase query chain that yields ``data`` on execute()."""
    c = MagicMock()
    result = MagicMock()
    result.data = data
    if error:
        c.execute.side_effect = error
    else:
        c.execute.return_value = result
    for method in ("select", "eq", "in_", "or_", "order", "limit", "maybe_single"):
        getattr(c, method).return_value = c
    return c


def _multi_client(table_responses: dict):
    """Return a mock client where each table name returns configured chains.

    ``table_responses`` maps table name → list of data payloads (consumed in order).
    """
    client = MagicMock()
    # Track call counts per table to return sequential responses
    call_counts: dict[str, int] = {}

    def _table_side_effect(name):
        call_counts.setdefault(name, 0)
        seq = table_responses.get(name, [[]])
        idx = min(call_counts[name], len(seq) - 1)
        call_counts[name] += 1
        data = seq[idx]
        return _chain(data=data)

    client.table.side_effect = _table_side_effect
    return client


def _make_task(
    task_id="t-1",
    title="Task 1",
    status="done",
    feature="auth",
    module=None,
    retry_count=0,
    blocked_by=None,
    state_changed_at=None,
    **kwargs,
):
    now = datetime.now().isoformat()
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "retry_count": retry_count,
        "phase": None,
        "feature": feature,
        "module": module,
        "blocked_by": blocked_by or [],
        "state_changed_at": state_changed_at or now,
        "created_at": now,
        "updated_at": now,
        "complexity": "simple",
        "priority": "medium",
        **kwargs,
    }


def _make_item(item_id="i-1", plan_id="p-1", phase_id="ph-1", status="done", title="Item 1"):
    return {
        "id": item_id,
        "plan_id": plan_id,
        "phase_id": phase_id,
        "title": title,
        "status": status,
        "item_key": "I-001",
        "item_order": 1,
    }


def _make_run(task_id="t-1", cost_usd=0.05, duration_seconds=120, status="completed", stage="execute"):
    return {
        "id": f"run-{task_id}",
        "task_id": task_id,
        "status": status,
        "stage": stage,
        "cost_usd": cost_usd,
        "duration_seconds": duration_seconds,
        "started_at": datetime.now().isoformat(),
        "finished_at": datetime.now().isoformat(),
    }


def _make_feedback(task_id="t-1", reviewer_identity="code-reviewer", verdict="changes-requested"):
    return {
        "id": f"fb-{task_id}-{reviewer_identity}",
        "task_id": task_id,
        "reviewer_identity": reviewer_identity,
        "verdict": verdict,
        "created_at": datetime.now().isoformat(),
    }


# ── Tests: get_cockpit ────────────────────────────────────────────────────────


class TestGetCockpit:
    """Tests for the main get_cockpit method."""

    def test_project_not_found_returns_error(self):
        client = _multi_client({
            "archon_projects": [None],
        })
        service = CockpitService(supabase_client=client)
        success, result = service.get_cockpit("nonexistent")
        assert success is False
        assert "not found" in result["error"].lower()

    @patch("src.server.services.projects.cockpit_service.CockpitService._build_recent_alerts", return_value=[])
    def test_full_cockpit_returns_all_sections(self, _mock_alerts):
        tasks = [_make_task("t-1", status="done"), _make_task("t-2", status="executing")]
        runs = [_make_run("t-1", cost_usd=0.10)]
        plan = {"id": "plan-1", "title": "Main Plan", "status": "active", "description": "", "created_at": "2026-01-01"}
        phases = [{"id": "ph-1", "plan_id": "plan-1", "title": "Phase 1", "phase_order": 0}]
        items = [
            _make_item("i-1", "plan-1", "ph-1", "done"),
            _make_item("i-2", "plan-1", "ph-1", "in_progress"),
        ]
        project = {"id": "proj-1", "title": "Test Project", "description": "", "created_at": "2026-01-01", "updated_at": "2026-01-01"}

        client = _multi_client({
            "archon_projects": [project],
            "project_implementation_plans": [[plan]],
            "archon_tasks": [tasks],
            "project_implementation_phases": [phases],
            "project_implementation_items": [items],
            "project_implementation_item_task_links": [[]],
            "archon_execution_runs": [runs],
            "archon_review_feedback": [[]],
        })

        service = CockpitService(supabase_client=client)
        success, result = service.get_cockpit("proj-1")

        assert success is True
        assert result["project_id"] == "proj-1"

        # All required sections present
        for section in ("header", "workstreams", "phases", "quality", "issue_ledger", "throughput", "recent_alerts"):
            assert section in result, f"Missing section: {section}"

        # issue_ledger is a list
        assert isinstance(result["issue_ledger"], list)

        # Quality section contains all required metrics
        quality = result["quality"]
        for key in ("first_pass_rate", "avg_retries", "done_count", "total_runs",
                    "failed_tasks_last_7d", "escalations", "evaluator_pass_rate",
                    "code_review_rejection_rate"):
            assert key in quality, f"Missing quality metric: {key}"

    @patch("src.server.services.projects.cockpit_service.CockpitService._build_recent_alerts", return_value=[])
    def test_cockpit_without_plan(self, _mock_alerts):
        """Projects without plans should still return a valid cockpit."""
        tasks = [_make_task("t-1", status="done")]
        project = {"id": "proj-1", "title": "No Plan Project", "description": "", "created_at": "2026-01-01", "updated_at": "2026-01-01"}

        client = _multi_client({
            "archon_projects": [project],
            "project_implementation_plans": [[]],  # No plan
            "archon_tasks": [tasks],
            "archon_execution_runs": [[]],
            "archon_review_feedback": [[]],
            "project_implementation_item_task_links": [[]],
        })

        service = CockpitService(supabase_client=client)
        success, result = service.get_cockpit("proj-1")

        assert success is True
        assert result["header"]["plan_title"] == "No Plan Project"
        assert result["phases"] == []

    @patch("src.server.services.projects.cockpit_service.CockpitService._build_recent_alerts", return_value=[])
    def test_cockpit_with_no_tasks(self, _mock_alerts):
        project = {"id": "proj-1", "title": "Empty", "description": "", "created_at": "2026-01-01", "updated_at": "2026-01-01"}

        client = _multi_client({
            "archon_projects": [project],
            "project_implementation_plans": [[]],
            "archon_tasks": [[]],
            "archon_execution_runs": [[]],
            "archon_review_feedback": [[]],
            "project_implementation_item_task_links": [[]],
        })

        service = CockpitService(supabase_client=client)
        success, result = service.get_cockpit("proj-1")

        assert success is True
        assert result["header"]["completion_percent"] == 0.0
        assert result["quality"]["done_count"] == 0
        assert result["throughput"]["items_done"] == 0


class TestBuildHeader:
    def test_completion_from_items(self):
        service = CockpitService(supabase_client=MagicMock())
        project = {"title": "P"}
        plan = {"title": "My Plan"}
        items = [
            _make_item(status="done"),
            _make_item(item_id="i-2", status="in_progress"),
            _make_item(item_id="i-3", status="deferred"),  # excluded
        ]

        header = service._build_header(project, plan, items, [])
        assert header["plan_title"] == "My Plan"
        assert header["completion_percent"] == 50.0  # 1 done / 2 active

    def test_completion_from_tasks_when_no_items(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task(status="done"), _make_task(task_id="t-2", status="executing")]
        header = service._build_header({"title": "P"}, None, [], tasks)
        assert header["completion_percent"] == 50.0

    def test_header_includes_progress_signal_and_last_completed(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task("t-1", status="done"), _make_task("t-2", status="executing")]
        header = service._build_header({"title": "P"}, None, [], tasks)
        assert "progress_signal" in header
        assert "last_completed_at" in header
        assert header["progress_signal"] in ("active", "stale", "blocked", "idle")
        assert header["last_completed_at"] is not None  # t-1 is done

    def test_header_progress_signal_active_for_recent_tasks(self):
        service = CockpitService(supabase_client=MagicMock())
        # state_changed_at defaults to now
        tasks = [_make_task("t-1", status="executing")]
        header = service._build_header({"title": "P"}, None, [], tasks)
        assert header["progress_signal"] == "active"


class TestBuildWorkstreams:
    def test_groups_by_feature(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [
            _make_task("t-1", status="done", feature="auth"),
            _make_task("t-2", status="executing", feature="auth"),
            _make_task("t-3", status="done", feature="payments"),
        ]
        ws = service._build_workstreams(tasks)
        auth = next(w for w in ws if w["name"] == "auth")
        assert auth["done"] == 1
        assert auth["total"] == 2

    def test_ungrouped_tasks(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task("t-1", feature=None, module=None)]
        ws = service._build_workstreams(tasks)
        assert ws[0]["name"] == "ungrouped"


class TestBuildPhases:
    def test_phase_progress(self):
        service = CockpitService(supabase_client=MagicMock())
        phases = [{"id": "ph-1", "plan_id": "p-1", "title": "Phase 1", "phase_order": 0}]
        items = [
            _make_item("i-1", phase_id="ph-1", status="done"),
            _make_item("i-2", phase_id="ph-1", status="in_progress"),
        ]
        result = service._build_phases(phases, items)
        assert len(result) == 1
        assert result[0]["done"] == 1
        assert result[0]["total"] == 2
        assert result[0]["status"] == "active"

    def test_completed_phase(self):
        service = CockpitService(supabase_client=MagicMock())
        phases = [{"id": "ph-1", "plan_id": "p-1", "title": "Done Phase", "phase_order": 0}]
        items = [_make_item("i-1", phase_id="ph-1", status="done")]
        result = service._build_phases(phases, items)
        assert result[0]["status"] == "done"

    def test_empty_phases(self):
        service = CockpitService(supabase_client=MagicMock())
        assert service._build_phases([], []) == []


class TestBuildQuality:
    def test_quality_with_done_tasks(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [
            _make_task("t-1", status="done", retry_count=0),
            _make_task("t-2", status="done", retry_count=2),
        ]
        quality = service._build_quality(tasks, [_make_run("t-1"), _make_run("t-2")])
        assert quality["first_pass_rate"] == 0.5
        assert quality["avg_retries"] == 1.0
        assert quality["done_count"] == 2
        assert quality["total_runs"] == 2

    def test_quality_no_done_tasks(self):
        service = CockpitService(supabase_client=MagicMock())
        quality = service._build_quality([_make_task(status="executing")], [])
        assert quality["first_pass_rate"] == 0.0
        assert quality["done_count"] == 0

    def test_failed_tasks_last_7d(self):
        service = CockpitService(supabase_client=MagicMock())
        recent_fail = _make_task("t-fail", status="failed")
        old_fail = _make_task(
            "t-old-fail",
            status="failed",
            state_changed_at=(datetime.now() - timedelta(days=10)).isoformat(),
        )
        quality = service._build_quality([recent_fail, old_fail], [])
        assert quality["failed_tasks_last_7d"] == 1

    def test_escalations_counted(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [
            _make_task("t-1", status="done"),
            _make_task("t-2", status="escalated"),
            _make_task("t-3", status="escalated"),
        ]
        quality = service._build_quality(tasks, [])
        assert quality["escalations"] == 2

    def test_evaluator_pass_rate_with_rejections(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [
            _make_task("t-1", status="done"),
            _make_task("t-2", status="done"),
            _make_task("t-3", status="done"),
        ]
        # t-1 was rejected by qa-evaluator; t-2 and t-3 passed
        feedback = [_make_feedback("t-1", reviewer_identity="qa-evaluator", verdict="changes-requested")]
        quality = service._build_quality(tasks, [], feedback)
        assert quality["evaluator_pass_rate"] == round(2 / 3, 3)

    def test_evaluator_pass_rate_all_pass(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task("t-1", status="done"), _make_task("t-2", status="done")]
        quality = service._build_quality(tasks, [], [])
        assert quality["evaluator_pass_rate"] == 1.0

    def test_code_review_rejection_rate(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [
            _make_task("t-1", status="done"),
            _make_task("t-2", status="done"),
        ]
        # Only t-1 had a code-reviewer rejection
        feedback = [_make_feedback("t-1", reviewer_identity="code-reviewer", verdict="changes-requested")]
        quality = service._build_quality(tasks, [], feedback)
        assert quality["code_review_rejection_rate"] == 0.5

    def test_code_review_rejection_rate_no_rejections(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task("t-1", status="done")]
        quality = service._build_quality(tasks, [], [])
        assert quality["code_review_rejection_rate"] == 0.0

    def test_quality_all_metrics_present(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task("t-1", status="done")]
        quality = service._build_quality(tasks, [])
        required_keys = {
            "first_pass_rate", "avg_retries", "done_count", "total_runs",
            "failed_tasks_last_7d", "escalations", "evaluator_pass_rate",
            "code_review_rejection_rate",
        }
        assert required_keys <= set(quality.keys())


def _make_link(item_id: str, task_id: str) -> dict:
    return {"item_id": item_id, "task_id": task_id}


class TestBuildIssueLedger:
    """Tests for the _build_issue_ledger method."""

    def _svc(self):
        return CockpitService(supabase_client=MagicMock())

    # ── 1. blocked_dependency ────────────────────────────────────────────

    def test_blocked_dependency_detected(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="assigned", blocked_by=["t-0"])]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        blocked = [i for i in issues if i["issue_type"] == "blocked_dependency"]
        assert len(blocked) == 1
        assert blocked[0]["linked_scope"]["task_id"] == "t-1"
        assert blocked[0]["severity"] == "critical"

    def test_done_task_not_in_blocked_dependency(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="done", blocked_by=["t-0"])]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        blocked = [i for i in issues if i["issue_type"] == "blocked_dependency"]
        assert len(blocked) == 0

    # ── 2. stale_lifecycle ───────────────────────────────────────────────

    def test_stale_executing_detected_warning(self):
        svc = self._svc()
        old_time = (datetime.now() - timedelta(hours=3)).isoformat()
        tasks = [_make_task("t-1", status="executing", state_changed_at=old_time)]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        stale = [i for i in issues if i["issue_type"] == "stale_lifecycle"]
        assert len(stale) == 1
        assert stale[0]["severity"] == "warning"

    def test_stale_executing_detected_critical_after_4h(self):
        svc = self._svc()
        old_time = (datetime.now() - timedelta(hours=5)).isoformat()
        tasks = [_make_task("t-1", status="executing", state_changed_at=old_time)]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        stale = [i for i in issues if i["issue_type"] == "stale_lifecycle"]
        assert stale[0]["severity"] == "critical"

    def test_stale_in_flight_statuses_detected(self):
        """All in-flight statuses should be detected as stale."""
        svc = self._svc()
        old_time = (datetime.now() - timedelta(hours=3)).isoformat()
        for status in ("executing", "architect-review", "code-review", "qa-eval"):
            tasks = [_make_task("t-1", status=status, state_changed_at=old_time)]
            issues = svc._build_issue_ledger(tasks, [], [], [])
            stale = [i for i in issues if i["issue_type"] == "stale_lifecycle"]
            assert len(stale) == 1, f"Expected stale for status={status}"

    def test_recent_executing_not_stale(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="executing")]  # state_changed_at=now
        issues = svc._build_issue_ledger(tasks, [], [], [])
        stale = [i for i in issues if i["issue_type"] == "stale_lifecycle"]
        assert len(stale) == 0

    # ── 3. repeat_failure_hotspot ────────────────────────────────────────

    def test_repeat_failure_hotspot_detected(self):
        svc = self._svc()
        tasks = [
            _make_task("t-1", feature="auth", retry_count=3),
            _make_task("t-2", feature="auth", retry_count=3),
        ]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        hotspots = [i for i in issues if i["issue_type"] == "repeat_failure_hotspot"]
        assert len(hotspots) == 1
        assert hotspots[0]["linked_scope"]["feature"] == "auth"
        assert hotspots[0]["linked_scope"]["total_retries"] == 6

    def test_repeat_failure_below_threshold_not_detected(self):
        svc = self._svc()
        tasks = [_make_task("t-1", feature="auth", retry_count=2)]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        hotspots = [i for i in issues if i["issue_type"] == "repeat_failure_hotspot"]
        assert len(hotspots) == 0

    # ── 4. high_retry_hotspot ────────────────────────────────────────────

    def test_high_retry_hotspot_individual_task(self):
        svc = self._svc()
        tasks = [_make_task("t-1", retry_count=3)]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        hotspots = [i for i in issues if i["issue_type"] == "high_retry_hotspot"]
        assert len(hotspots) == 1
        assert hotspots[0]["linked_scope"]["retry_count"] == 3
        assert hotspots[0]["severity"] == "warning"

    def test_high_retry_hotspot_critical_at_double_threshold(self):
        svc = self._svc()
        tasks = [_make_task("t-1", retry_count=6)]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        hotspots = [i for i in issues if i["issue_type"] == "high_retry_hotspot"]
        assert hotspots[0]["severity"] == "critical"

    # ── 5. unmapped_task_gap ─────────────────────────────────────────────

    def test_unmapped_task_gap_detected(self):
        svc = self._svc()
        items = [_make_item("i-1", status="in_progress")]
        # No links → item i-1 is unmapped
        issues = svc._build_issue_ledger([], items, [], [])
        gaps = [i for i in issues if i["issue_type"] == "unmapped_task_gap"]
        assert len(gaps) == 1
        assert gaps[0]["linked_scope"]["item_id"] == "i-1"

    def test_mapped_item_no_gap(self):
        svc = self._svc()
        items = [_make_item("i-1", status="in_progress")]
        links = [_make_link("i-1", "t-1")]
        issues = svc._build_issue_ledger([], items, links, [])
        gaps = [i for i in issues if i["issue_type"] == "unmapped_task_gap"]
        assert len(gaps) == 0

    def test_deferred_item_not_flagged_as_gap(self):
        svc = self._svc()
        items = [_make_item("i-1", status="deferred")]
        issues = svc._build_issue_ledger([], items, [], [])
        gaps = [i for i in issues if i["issue_type"] == "unmapped_task_gap"]
        assert len(gaps) == 0

    # ── 6. failed_evaluator_review ───────────────────────────────────────

    def test_qa_evaluator_rejection_detected(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="qa-eval")]
        feedback = [_make_feedback("t-1", reviewer_identity="qa-evaluator", verdict="changes-requested")]
        issues = svc._build_issue_ledger(tasks, [], [], feedback)
        failed = [i for i in issues if i["issue_type"] == "failed_evaluator_review"]
        assert len(failed) == 1
        assert failed[0]["linked_scope"]["reviewer"] == "qa-evaluator"

    def test_code_reviewer_rejection_detected(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="code-review")]
        feedback = [_make_feedback("t-1", reviewer_identity="code-reviewer", verdict="changes-requested")]
        issues = svc._build_issue_ledger(tasks, [], [], feedback)
        failed = [i for i in issues if i["issue_type"] == "failed_evaluator_review"]
        assert len(failed) == 1
        assert failed[0]["linked_scope"]["reviewer"] == "code-reviewer"

    def test_code_reviewer_critical_at_high_cycle_count(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="code-review", review_cycle=2)]
        feedback = [_make_feedback("t-1", reviewer_identity="code-reviewer", verdict="changes-requested")]
        issues = svc._build_issue_ledger(tasks, [], [], feedback)
        failed = [i for i in issues if i["issue_type"] == "failed_evaluator_review"]
        assert failed[0]["severity"] == "critical"

    def test_done_task_reviewer_rejection_ignored(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="done")]
        feedback = [_make_feedback("t-1", reviewer_identity="qa-evaluator")]
        issues = svc._build_issue_ledger(tasks, [], [], feedback)
        failed = [i for i in issues if i["issue_type"] == "failed_evaluator_review"]
        assert len(failed) == 0

    # ── Sorting ──────────────────────────────────────────────────────────

    def test_issues_sorted_critical_first(self):
        svc = self._svc()
        tasks = [
            _make_task("t-1", status="assigned", blocked_by=["t-0"]),  # critical
            _make_task("t-2", feature="auth", retry_count=3),           # warning (high_retry)
        ]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        severities = [i["severity"] for i in issues]
        critical_idx = severities.index("critical")
        warning_idx = severities.index("warning")
        assert critical_idx < warning_idx

    # ── All issue fields present ─────────────────────────────────────────

    def test_issue_has_required_fields(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="assigned", blocked_by=["t-0"])]
        issues = svc._build_issue_ledger(tasks, [], [], [])
        issue = issues[0]
        for field in ("issue_type", "severity", "source", "linked_scope", "recommended_action"):
            assert field in issue, f"Missing field: {field}"


class TestBuildThroughput:
    def test_throughput_aggregation(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [
            _make_task("t-1", status="done", feature="auth"),
            _make_task("t-2", status="executing", feature="auth"),
        ]
        runs = [_make_run("t-1", cost_usd=0.10), _make_run("t-2", cost_usd=0.05)]
        tp = service._build_throughput(tasks, runs)
        assert tp["items_done"] == 1
        assert tp["total_tasks"] == 2
        assert tp["total_cost_usd"] == 0.15

    def test_throughput_no_runs(self):
        service = CockpitService(supabase_client=MagicMock())
        tp = service._build_throughput([], [])
        assert tp["items_done"] == 0
        assert tp["total_cost_usd"] == 0.0


class TestHealthBadge:
    def test_healthy(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task(status="done", retry_count=0)]
        assert service._compute_health_badge(tasks) == "healthy"

    def test_warning_on_failed(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task(f"t-{i}", status="done") for i in range(10)]
        tasks.append(_make_task("t-fail", status="failed"))
        assert service._compute_health_badge(tasks) == "warning"

    def test_critical_on_many_failures(self):
        service = CockpitService(supabase_client=MagicMock())
        tasks = [_make_task(f"t-{i}", status="done") for i in range(5)]
        tasks.extend([_make_task(f"t-f-{i}", status="failed") for i in range(5)])
        assert service._compute_health_badge(tasks) == "critical"

    def test_empty_tasks_healthy(self):
        service = CockpitService(supabase_client=MagicMock())
        assert service._compute_health_badge([]) == "healthy"


class TestComputeProgressSignal:
    def _svc(self):
        return CockpitService(supabase_client=MagicMock())

    def test_no_tasks_returns_idle(self):
        svc = self._svc()
        signal, last_completed = svc._compute_progress_signal([])
        assert signal == "idle"
        assert last_completed is None

    def test_all_done_returns_idle(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="done"), _make_task("t-2", status="cancelled")]
        signal, last_completed = svc._compute_progress_signal(tasks)
        assert signal == "idle"

    def test_active_when_recent_state_change(self):
        svc = self._svc()
        # state_changed_at defaults to now in _make_task
        tasks = [_make_task("t-1", status="executing"), _make_task("t-2", status="done")]
        signal, _ = svc._compute_progress_signal(tasks)
        assert signal == "active"

    def test_stale_when_no_recent_activity(self):
        svc = self._svc()
        old_time = (datetime.now() - timedelta(hours=72)).isoformat()
        tasks = [_make_task("t-1", status="todo", state_changed_at=old_time)]
        signal, _ = svc._compute_progress_signal(tasks)
        assert signal == "stale"

    def test_blocked_when_unfinished_task_has_blocker(self):
        svc = self._svc()
        tasks = [
            _make_task("t-1", status="assigned", blocked_by=["t-0"]),
        ]
        signal, _ = svc._compute_progress_signal(tasks)
        assert signal == "blocked"

    def test_blocked_takes_priority_over_stale(self):
        svc = self._svc()
        old_time = (datetime.now() - timedelta(hours=72)).isoformat()
        tasks = [_make_task("t-1", status="assigned", blocked_by=["t-0"], state_changed_at=old_time)]
        signal, _ = svc._compute_progress_signal(tasks)
        assert signal == "blocked"

    def test_done_task_with_blocker_not_blocked(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="done", blocked_by=["t-0"])]
        signal, _ = svc._compute_progress_signal(tasks)
        assert signal == "idle"

    def test_last_completed_at_reflects_most_recent_done(self):
        svc = self._svc()
        old_ts = (datetime.now() - timedelta(hours=10)).isoformat()
        recent_ts = (datetime.now() - timedelta(hours=2)).isoformat()
        tasks = [
            _make_task("t-1", status="done", state_changed_at=old_ts),
            _make_task("t-2", status="done", state_changed_at=recent_ts),
            _make_task("t-3", status="executing"),
        ]
        _, last_completed = svc._compute_progress_signal(tasks)
        assert last_completed == recent_ts

    def test_last_completed_at_none_when_no_done_tasks(self):
        svc = self._svc()
        tasks = [_make_task("t-1", status="executing")]
        _, last_completed = svc._compute_progress_signal(tasks)
        assert last_completed is None
