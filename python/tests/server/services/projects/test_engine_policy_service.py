"""Tests for EnginePolicyService CRUD operations."""

from unittest.mock import MagicMock

from src.server.services.projects.engine_policy_service import EnginePolicyService, extract_project_policy_sources


def _make_service(data=None, error=None):
    """Build an EnginePolicyService with a mocked Supabase client."""
    client = MagicMock()
    svc = EnginePolicyService(supabase_client=client)

    def _make_chain(table_data=None, table_error=None):
        execute_result = MagicMock()
        execute_result.data = table_data

        chain = MagicMock()
        chain.execute.return_value = execute_result
        chain.eq.return_value = chain
        chain.select.return_value = chain
        chain.maybe_single.return_value = chain
        chain.upsert.return_value = chain
        chain.delete.return_value = chain

        if table_error:
            chain.execute.side_effect = table_error

        return chain

    policy_chain = _make_chain(table_data=data, table_error=error)
    project_chain = _make_chain(table_data=None)
    settings_chain = _make_chain(table_data=None)

    def _table(table_name):
        if table_name == "archon_engine_policies":
            return policy_chain
        if table_name == "archon_projects":
            return project_chain
        if table_name == "archon_settings":
            return settings_chain
        raise AssertionError(f"Unexpected table requested: {table_name}")

    client.table.side_effect = _table
    chains = {
        "policy": policy_chain,
        "project": project_chain,
        "settings": settings_chain,
    }
    return svc, client, chains


class TestGetPolicy:
    def test_returns_policy_when_found(self):
        policy_row = {
            "id": "p1",
            "project_id": "proj-1",
            "is_active": True,
            "model_routing": {},
            "retry_policy": {},
            "budget_policy": {},
            "isolation_policy": {},
        }
        svc, _, _ = _make_service(data=policy_row)
        ok, result = svc.get_policy("proj-1")
        assert ok is True
        policy = result["policy"]
        assert policy["id"] == "p1"
        assert policy["project_id"] == "proj-1"
        assert policy["is_default"] is False
        assert policy["model_routing"] == {}
        assert policy["review_policy"] == {}

    def test_returns_seed_defaults_when_not_found(self):
        svc, _, _ = _make_service(data=None)
        ok, result = svc.get_policy("proj-1")
        assert ok is True
        policy = result["policy"]
        assert policy is not None
        assert policy["is_default"] is True
        assert policy["project_id"] == "proj-1"
        # All policy columns present
        assert "model_routing" in policy
        assert "retry_policy" in policy
        assert "budget_policy" in policy
        assert "isolation_policy" in policy
        assert "review_policy" in policy

    def test_seed_defaults_have_expected_retry_values(self):
        svc, _, _ = _make_service(data=None)
        _, result = svc.get_policy("proj-x")
        retry = result["policy"]["retry_policy"]
        assert retry["max_attempts"] == 3
        assert retry["retry_on_timeout"] is True

    def test_seed_defaults_have_expected_isolation_values(self):
        svc, _, _ = _make_service(data=None)
        _, result = svc.get_policy("proj-x")
        isolation = result["policy"]["isolation_policy"]
        assert isolation["worktree_mode"] == "shared"
        assert isolation["sandbox_network"] is False

    def test_returns_error_on_exception(self):
        svc, _, _ = _make_service(error=RuntimeError("db error"))
        ok, result = svc.get_policy("proj-1")
        assert ok is False
        assert "db error" in result["error"]

    def test_uses_legacy_project_fallbacks_when_policy_missing(self):
        svc, _, chains = _make_service(data=None)
        chains["project"].execute.return_value.data = {
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "isolation_mode": "git-worktree",
            },
            "team_lead_config": {"review_mode": "api"},
            "director_config": {},
        }

        ok, result = svc.get_policy("proj-1")

        assert ok is True
        policy = result["policy"]
        assert policy["model_routing"]["default_runner"] == "codex-cli"
        assert policy["review_policy"]["review_mode"] == "api"
        assert policy["isolation_policy"]["worktree_mode"] == "isolated"

    def test_legacy_fallback_uses_deterministic_section_precedence(self):
        svc, _, chains = _make_service(data=None)
        chains["project"].execute.return_value.data = {
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "review_mode": "self-review",
                "isolation_mode": "shared",
            },
            "team_lead_config": {
                "preferred_runner": "claude-code-cli",
                "review_mode": "api",
                "isolation_mode": "git-worktree",
            },
            "director_config": {
                "preferred_runner": "claude-code-cli",
                "review_mode": "multi-perspective",
                "isolation_mode": "per-task",
            },
        }

        ok, result = svc.get_policy("proj-1")

        assert ok is True
        policy = result["policy"]
        assert policy["model_routing"]["default_runner"] == "codex-cli"
        assert policy["review_policy"]["review_mode"] == "api"
        assert policy["isolation_policy"]["worktree_mode"] == "shared"


class TestProjectPolicySourceExtraction:
    def test_extracts_project_scoped_policy_without_global_review_fallback(self):
        project_row = {
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "isolation_mode": "git-worktree",
            },
            "team_lead_config": {},
            "director_config": {},
        }

        result = extract_project_policy_sources(project_row)

        assert result["model_routing"]["default_runner"] == "codex-cli"
        assert result["isolation_policy"]["worktree_mode"] == "isolated"
        assert "review_policy" not in result


class TestGetActivePolicy:
    def test_returns_policy_row_when_active(self):
        policy_row = {"id": "p1", "is_active": True, "model_routing": {"default_runner": "codex-cli"}}
        svc, _, _ = _make_service(data=policy_row)
        result = svc.get_active_policy("proj-1")
        assert result is not None
        assert result["id"] == "p1"
        assert result["is_default"] is False
        assert result["model_routing"]["default_runner"] == "codex-cli"

    def test_returns_none_when_no_policy(self):
        svc, _, _ = _make_service(data=None)
        result = svc.get_active_policy("proj-1")
        assert result is None

    def test_returns_none_on_exception(self):
        svc, _, _ = _make_service(error=RuntimeError("connection failed"))
        result = svc.get_active_policy("proj-1")
        assert result is None

    def test_merges_legacy_review_fallback_into_active_policy(self):
        policy_row = {"id": "p1", "project_id": "proj-1", "is_active": True, "model_routing": {}}
        svc, _, chains = _make_service(data=policy_row)
        chains["settings"].execute.return_value.data = {"value": '{"review_mode":"multi-perspective"}'}

        result = svc.get_active_policy("proj-1")

        assert result is not None
        assert result["review_policy"]["review_mode"] == "multi-perspective"

    def test_returns_legacy_project_policy_when_active_policy_missing(self):
        svc, _, chains = _make_service(data=None)
        chains["project"].execute.return_value.data = {
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "isolation_mode": "git-worktree",
            },
            "team_lead_config": {"review_mode": "api"},
            "director_config": {},
        }

        result = svc.get_active_policy("proj-1")

        assert result is not None
        assert result["is_default"] is True
        assert result["model_routing"]["default_runner"] == "codex-cli"
        assert result["review_policy"]["review_mode"] == "api"
        assert result["isolation_policy"]["worktree_mode"] == "isolated"

    def test_stored_policy_wins_over_legacy_fallback_values(self):
        policy_row = {
            "id": "p1",
            "project_id": "proj-1",
            "is_active": True,
            "model_routing": {"default_runner": "claude-code-cli"},
            "review_policy": {"review_mode": "multi-perspective"},
            "isolation_policy": {"worktree_mode": "per-task"},
        }
        svc, _, chains = _make_service(data=policy_row)
        chains["project"].execute.return_value.data = {
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "isolation_mode": "git-worktree",
            },
            "team_lead_config": {"review_mode": "api"},
            "director_config": {},
        }

        result = svc.get_active_policy("proj-1")

        assert result is not None
        assert result["model_routing"]["default_runner"] == "claude-code-cli"
        assert result["review_policy"]["review_mode"] == "multi-perspective"
        assert result["isolation_policy"]["worktree_mode"] == "per-task"


class TestUpsertPolicy:
    def test_upserts_and_returns_policy(self):
        saved = {
            "id": "p1",
            "project_id": "proj-1",
            "is_active": True,
            "model_routing": {"default_runner": "codex-cli"},
            "retry_policy": {"max_attempts": 5},
            "budget_policy": {"daily_limit_usd": 50.0},
            "isolation_policy": {"worktree_mode": "isolated"},
            "review_policy": {"review_mode": "api"},
        }
        svc, _, chains = _make_service(data=[saved])
        ok, result = svc.upsert_policy(
            "proj-1",
            model_routing={"default_runner": "codex-cli"},
            retry_policy={"max_attempts": 5},
            budget_policy={"daily_limit_usd": 50.0},
            isolation_policy={"worktree_mode": "isolated"},
            review_policy={"review_mode": "api"},
        )
        assert ok is True
        assert result["policy"] == saved

    def test_upserts_with_only_model_routing(self):
        saved = {"id": "p1", "project_id": "proj-1", "is_active": True, "model_routing": {}}
        svc, _, _ = _make_service(data=[saved])
        ok, result = svc.upsert_policy("proj-1", model_routing={})
        assert ok is True
        assert result["policy"] == saved

    def test_upsert_payload_contains_all_policy_columns(self):
        saved = {"id": "p1", "project_id": "proj-1"}
        svc, _, chains = _make_service(data=[saved])
        svc.upsert_policy(
            "proj-1",
            model_routing={"disable_codex": True},
            retry_policy={"max_attempts": 2},
            budget_policy={"task_limit_usd": 10.0},
            isolation_policy={"clean_env": True},
            review_policy={"review_mode": "api"},
        )
        upsert_call_args = chains["policy"].upsert.call_args[0][0]
        assert upsert_call_args["model_routing"] == {"disable_codex": True}
        assert upsert_call_args["retry_policy"] == {"max_attempts": 2}
        assert upsert_call_args["budget_policy"] == {"task_limit_usd": 10.0}
        assert upsert_call_args["isolation_policy"] == {"clean_env": True}
        assert upsert_call_args["review_policy"] == {"review_mode": "api"}

    def test_rejects_missing_project_id(self):
        svc, _, _ = _make_service()
        ok, result = svc.upsert_policy("", {})
        assert ok is False
        assert "project_id" in result["error"]

    def test_returns_error_when_data_empty(self):
        svc, _, _ = _make_service(data=[])
        ok, result = svc.upsert_policy("proj-1", {})
        assert ok is False
        assert "no data" in result["error"].lower()

    def test_returns_error_on_exception(self):
        svc, _, _ = _make_service(error=RuntimeError("upsert failed"))
        ok, result = svc.upsert_policy("proj-1", {})
        assert ok is False
        assert "upsert failed" in result["error"]


class TestSyncProjectPolicySources:
    def test_mirrors_legacy_metadata_into_engine_policy_row(self):
        svc, _, chains = _make_service(data=None)
        chains["policy"].execute.side_effect = [
            MagicMock(data=None),
            MagicMock(
                data=[
                    {
                        "id": "policy-1",
                        "project_id": "proj-1",
                        "is_active": True,
                        "model_routing": {"default_runner": "codex-cli"},
                        "retry_policy": {},
                        "budget_policy": {},
                        "review_policy": {"review_mode": "api"},
                        "isolation_policy": {"worktree_mode": "isolated"},
                    }
                ]
            ),
        ]

        ok, result = svc.sync_project_policy_sources({
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "isolation_mode": "git-worktree",
            },
            "team_lead_config": {"review_mode": "api"},
            "director_config": {},
        })

        assert ok is True
        upsert_payload = chains["policy"].upsert.call_args[0][0]
        assert upsert_payload["project_id"] == "proj-1"
        assert upsert_payload["model_routing"] == {"default_runner": "codex-cli"}
        assert upsert_payload["review_policy"] == {"review_mode": "api"}
        assert upsert_payload["isolation_policy"] == {"worktree_mode": "isolated"}
        assert result["mirrored"] is True

    def test_existing_canonical_policy_wins_over_mirrored_legacy_values(self):
        stored = {
            "id": "policy-1",
            "project_id": "proj-1",
            "is_active": True,
            "model_routing": {"default_runner": "claude-code-cli", "disable_codex": True},
            "retry_policy": {"max_attempts": 4},
            "budget_policy": {"task_limit_usd": 5.0},
            "review_policy": {"review_mode": "multi-perspective"},
            "isolation_policy": {"worktree_mode": "per-task"},
        }
        svc, _, chains = _make_service(data=stored)

        ok, result = svc.sync_project_policy_sources({
            "id": "proj-1",
            "office_settings": {
                "preferred_runner": "codex-cli",
                "isolation_mode": "git-worktree",
            },
            "team_lead_config": {"review_mode": "api"},
            "director_config": {},
        })

        assert ok is True
        assert chains["policy"].upsert.call_count == 0
        assert result["policy"]["model_routing"]["default_runner"] == "claude-code-cli"
        assert result["policy"]["review_policy"]["review_mode"] == "multi-perspective"
        assert result["policy"]["isolation_policy"]["worktree_mode"] == "per-task"
        assert result["mirrored"] is False


class TestDeletePolicy:
    def test_deletes_policy_successfully(self):
        svc, _, _ = _make_service(data=None)
        ok, result = svc.delete_policy("proj-1")
        assert ok is True
        assert "proj-1" in result["message"]

    def test_rejects_missing_project_id(self):
        svc, _, _ = _make_service()
        ok, result = svc.delete_policy("")
        assert ok is False

    def test_returns_error_on_exception(self):
        svc, _, _ = _make_service(error=RuntimeError("delete failed"))
        ok, result = svc.delete_policy("proj-1")
        assert ok is False
        assert "delete failed" in result["error"]
