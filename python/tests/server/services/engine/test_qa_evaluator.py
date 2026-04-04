"""Tests for QA Evaluator — prompt builder and result parsers."""


from src.server.services.engine.qa_evaluator import (
    QA_EVAL_PASS_THRESHOLD,
    build_qa_eval_prompt,
    compute_qa_eval_average,
    parse_qa_eval_findings,
    parse_qa_eval_scores,
    parse_qa_eval_verdict,
)
from src.server.services.engine.qa_evaluator_templates import (
    QA_EVAL_PROJECT_TYPE_API,
    QA_EVAL_PROJECT_TYPE_FULL_STACK,
    QA_EVAL_PROJECT_TYPE_GENERAL,
    QA_EVAL_PROJECT_TYPE_LIBRARY,
    QA_EVAL_PROJECT_TYPE_WEB,
    get_qa_eval_probe_instructions,
    is_qa_eval_disabled,
    resolve_qa_eval_project_type,
)

# ── parse_qa_eval_verdict ──────────────────────────────────────────────

class TestParseQaEvalVerdict:
    def test_parse_pass(self):
        assert parse_qa_eval_verdict("QA_EVAL_VERDICT: PASS") == "PASS"

    def test_parse_fail(self):
        assert parse_qa_eval_verdict("some output\nQA_EVAL_VERDICT: FAIL\nmore") == "FAIL"

    def test_parse_escalate(self):
        assert parse_qa_eval_verdict("QA_EVAL_VERDICT: ESCALATE") == "ESCALATE"

    def test_case_insensitive(self):
        assert parse_qa_eval_verdict("qa_eval_verdict: pass") == "PASS"

    def test_empty_string(self):
        assert parse_qa_eval_verdict("") == "UNKNOWN"

    def test_no_verdict(self):
        assert parse_qa_eval_verdict("Some random output without verdict") == "UNKNOWN"


# ── parse_qa_eval_scores ──────────────────────────────────────────────

class TestParseQaEvalScores:
    def test_parse_valid_scores(self):
        stdout = (
            'QA_EVAL_SCORES: {"criteria": [{"criterion": "API returns 200", "score": 8, '
            '"evidence": "tested", "notes": "none"}], "average_score": 8.0}\n'
            "QA_EVAL_FINDINGS: []"
        )
        scores = parse_qa_eval_scores(stdout)
        assert scores is not None
        assert scores["average_score"] == 8.0
        assert len(scores["criteria"]) == 1

    def test_empty_string(self):
        assert parse_qa_eval_scores("") is None

    def test_no_scores_block(self):
        assert parse_qa_eval_scores("just some output") is None

    def test_malformed_json(self):
        assert parse_qa_eval_scores("QA_EVAL_SCORES: {not json}") is None

    def test_non_dict_json(self):
        assert parse_qa_eval_scores("QA_EVAL_SCORES: [1, 2, 3]") is None


# ── parse_qa_eval_findings ────────────────────────────────────────────

class TestParseQaEvalFindings:
    def test_parse_findings(self):
        stdout = (
            'QA_EVAL_FINDINGS: [{"severity": "critical", "category": "functional", '
            '"description": "Endpoint returns 500", "evidence": "curl -X POST /api/foo"}]\n'
            "QA_EVAL_VERDICT: FAIL"
        )
        findings = parse_qa_eval_findings(stdout)
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"

    def test_empty_findings(self):
        assert parse_qa_eval_findings("QA_EVAL_FINDINGS: []\nQA_EVAL_VERDICT: PASS") == []

    def test_no_findings_block(self):
        assert parse_qa_eval_findings("no findings here") == []

    def test_empty_string(self):
        assert parse_qa_eval_findings("") == []

    def test_malformed_json(self):
        assert parse_qa_eval_findings("QA_EVAL_FINDINGS: [not json]") == []


# ── compute_qa_eval_average ───────────────────────────────────────────

class TestComputeQaEvalAverage:
    def test_from_average_score_field(self):
        assert compute_qa_eval_average({"average_score": 7.5}) == 7.5

    def test_from_criteria_list(self):
        scores = {
            "criteria": [
                {"criterion": "a", "score": 8},
                {"criterion": "b", "score": 6},
            ]
        }
        assert compute_qa_eval_average(scores) == 7.0

    def test_none_scores(self):
        assert compute_qa_eval_average(None) == 0.0

    def test_empty_dict(self):
        assert compute_qa_eval_average({}) == 0.0

    def test_empty_criteria(self):
        assert compute_qa_eval_average({"criteria": []}) == 0.0

    def test_prefers_average_score_field(self):
        scores = {
            "average_score": 9.0,
            "criteria": [{"criterion": "a", "score": 1}],
        }
        assert compute_qa_eval_average(scores) == 9.0


# ── build_qa_eval_prompt ──────────────────────────────────────────────

class TestBuildQaEvalPrompt:
    def test_includes_task_title(self):
        task = {"title": "Add auth endpoint", "description": "Implement login"}
        prompt = build_qa_eval_prompt(task, [])
        assert "Add auth endpoint" in prompt

    def test_includes_contract_criteria(self):
        task = {"title": "T", "description": "D"}
        contract = [
            {"criterion": "returns HTTP 200", "threshold": "status code 200", "category": "functional"},
        ]
        prompt = build_qa_eval_prompt(task, contract)
        assert "returns HTTP 200" in prompt
        assert "functional" in prompt

    def test_includes_git_diff(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], git_diff="diff --git a/foo.py")
        assert "diff --git a/foo.py" in prompt

    def test_includes_build_command(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], build_command="pnpm build && pnpm test")
        assert "pnpm build && pnpm test" in prompt

    def test_hostile_evaluator_instructions(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [])
        assert "hostile" in prompt.lower()
        assert "kill" in prompt.lower()
        assert "QA_EVAL_VERDICT" in prompt
        assert "QA_EVAL_SCORES" in prompt

    def test_no_contract_shows_untestable_message(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [])
        assert "untestable" in prompt.lower()

    def test_includes_acceptance_criteria(self):
        task = {
            "title": "T",
            "description": "D",
            "acceptance_criteria": [{"description": "Feature works correctly"}],
        }
        prompt = build_qa_eval_prompt(task, [])
        assert "Feature works correctly" in prompt

    def test_truncates_long_git_diff(self):
        task = {"title": "T", "description": "D"}
        long_diff = "x" * 10000
        prompt = build_qa_eval_prompt(task, [], git_diff=long_diff)
        assert "[diff truncated]" in prompt

    def test_pass_threshold_in_prompt(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [])
        assert str(QA_EVAL_PASS_THRESHOLD) in prompt

    def test_project_type_api_inserts_probe_instructions(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], project_type=QA_EVAL_PROJECT_TYPE_API)
        assert "HTTP" in prompt
        assert "endpoint" in prompt.lower()

    def test_project_type_web_inserts_probe_instructions(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], project_type=QA_EVAL_PROJECT_TYPE_WEB)
        assert "dev server" in prompt.lower()

    def test_project_type_library_inserts_probe_instructions(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], project_type=QA_EVAL_PROJECT_TYPE_LIBRARY)
        assert "import" in prompt.lower()

    def test_project_type_full_stack_inserts_probe_instructions(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], project_type=QA_EVAL_PROJECT_TYPE_FULL_STACK)
        assert "backend" in prompt.lower()
        assert "frontend" in prompt.lower()

    def test_unknown_project_type_falls_back_to_general(self):
        task = {"title": "T", "description": "D"}
        general_prompt = build_qa_eval_prompt(task, [], project_type=QA_EVAL_PROJECT_TYPE_GENERAL)
        unknown_prompt = build_qa_eval_prompt(task, [], project_type="totally-unknown-type")
        # Both should include the general probe instructions
        assert "General Application" in general_prompt
        assert "General Application" in unknown_prompt

    def test_empty_project_type_falls_back_to_general(self):
        task = {"title": "T", "description": "D"}
        prompt = build_qa_eval_prompt(task, [], project_type="")
        assert "General Application" in prompt


# ── get_qa_eval_probe_instructions ────────────────────────────────────────────


class TestGetQaEvalProbeInstructions:
    def test_api_service_instructions(self):
        instructions = get_qa_eval_probe_instructions(QA_EVAL_PROJECT_TYPE_API)
        assert "HTTP" in instructions
        assert "endpoint" in instructions.lower()

    def test_web_app_instructions(self):
        instructions = get_qa_eval_probe_instructions(QA_EVAL_PROJECT_TYPE_WEB)
        assert "dev server" in instructions.lower()
        assert "render" in instructions.lower()

    def test_library_instructions(self):
        instructions = get_qa_eval_probe_instructions(QA_EVAL_PROJECT_TYPE_LIBRARY)
        assert "import" in instructions.lower()
        assert "edge" in instructions.lower()

    def test_full_stack_instructions_combine_api_and_ui(self):
        instructions = get_qa_eval_probe_instructions(QA_EVAL_PROJECT_TYPE_FULL_STACK)
        assert "backend" in instructions.lower()
        assert "frontend" in instructions.lower()
        assert "integration" in instructions.lower()

    def test_unknown_type_returns_general_instructions(self):
        instructions = get_qa_eval_probe_instructions("not-a-real-type")
        assert "General Application" in instructions

    def test_all_templates_include_cleanup_instruction(self):
        project_types = [
            QA_EVAL_PROJECT_TYPE_API,
            QA_EVAL_PROJECT_TYPE_WEB,
            QA_EVAL_PROJECT_TYPE_FULL_STACK,
        ]
        for pt in project_types:
            instructions = get_qa_eval_probe_instructions(pt)
            assert "kill" in instructions.lower() or "cleanup" in instructions.lower(), (
                f"No cleanup instruction found for project type: {pt}"
            )


# ── resolve_qa_eval_project_type ──────────────────────────────────────────────


class TestResolveQaEvalProjectType:
    def test_no_policy_returns_general(self):
        assert resolve_qa_eval_project_type(None) == QA_EVAL_PROJECT_TYPE_GENERAL

    def test_empty_policy_returns_general(self):
        assert resolve_qa_eval_project_type({}) == QA_EVAL_PROJECT_TYPE_GENERAL

    def test_explicit_qa_eval_policy_project_type(self):
        policy = {"qa_eval_policy": {"project_type": "api-service"}}
        assert resolve_qa_eval_project_type(policy) == QA_EVAL_PROJECT_TYPE_API

    def test_top_level_project_type(self):
        policy = {"project_type": "web-app"}
        assert resolve_qa_eval_project_type(policy) == QA_EVAL_PROJECT_TYPE_WEB

    def test_qa_eval_policy_takes_precedence_over_top_level(self):
        policy = {"qa_eval_policy": {"project_type": "library"}, "project_type": "api-service"}
        assert resolve_qa_eval_project_type(policy) == QA_EVAL_PROJECT_TYPE_LIBRARY

    def test_unsupported_type_in_qa_eval_policy_falls_through(self):
        policy = {"qa_eval_policy": {"project_type": "unknown-type"}, "project_type": "web-app"}
        assert resolve_qa_eval_project_type(policy) == QA_EVAL_PROJECT_TYPE_WEB

    def test_unsupported_type_everywhere_returns_general(self):
        policy = {"qa_eval_policy": {"project_type": "bogus"}, "project_type": "also-bogus"}
        assert resolve_qa_eval_project_type(policy) == QA_EVAL_PROJECT_TYPE_GENERAL

    def test_full_stack_type(self):
        policy = {"qa_eval_policy": {"project_type": "full-stack"}}
        assert resolve_qa_eval_project_type(policy) == QA_EVAL_PROJECT_TYPE_FULL_STACK

    def test_task_param_accepted(self):
        task = {"title": "test"}
        assert resolve_qa_eval_project_type(None, task) == QA_EVAL_PROJECT_TYPE_GENERAL


# ── is_qa_eval_disabled ───────────────────────────────────────────────────────


class TestIsQaEvalDisabled:
    def test_none_policy_not_disabled(self):
        assert is_qa_eval_disabled(None) is False

    def test_empty_policy_not_disabled(self):
        assert is_qa_eval_disabled({}) is False

    def test_no_qa_eval_policy_section_not_disabled(self):
        assert is_qa_eval_disabled({"review_policy": {"review_mode": "self-review"}}) is False

    def test_disabled_true(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": True}}) is True

    def test_disabled_false(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": False}}) is False

    def test_disabled_string_true(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": "true"}}) is True

    def test_disabled_string_yes(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": "yes"}}) is True

    def test_disabled_string_false(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": "false"}}) is False

    def test_disabled_integer_1(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": 1}}) is True

    def test_disabled_integer_0(self):
        assert is_qa_eval_disabled({"qa_eval_policy": {"disabled": 0}}) is False

    def test_non_dict_qa_eval_policy_not_disabled(self):
        assert is_qa_eval_disabled({"qa_eval_policy": "yes"}) is False
