-- Migration: 018_create_engine_policies.sql
-- Purpose: Introduce per-project engine policies for runner/model routing,
--          retry behaviour, budget limits, and execution isolation.

CREATE TABLE IF NOT EXISTS archon_engine_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    model_routing JSONB NOT NULL DEFAULT '{}'::jsonb,
    retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    budget_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    isolation_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_engine_policy_project UNIQUE (project_id)
);

CREATE INDEX IF NOT EXISTS idx_engine_policies_project_id
    ON archon_engine_policies(project_id);

CREATE INDEX IF NOT EXISTS idx_engine_policies_is_active
    ON archon_engine_policies(is_active);

COMMENT ON TABLE archon_engine_policies IS
    'Per-project engine policies. When is_active=true the JSONB policy columns
     override global runner/model heuristics, retry settings, budget limits,
     and execution isolation configuration.';

COMMENT ON COLUMN archon_engine_policies.project_id IS
    'Project this policy applies to. One policy per project.';

COMMENT ON COLUMN archon_engine_policies.is_active IS
    'When false the policy is ignored and global defaults are used.';

COMMENT ON COLUMN archon_engine_policies.model_routing IS
    'JSONB routing overrides. Supported keys:
     default_runner          TEXT   – runner_key to use when no stage rule matches
     stage_overrides         OBJECT – per-stage runner/model: {"execute": {"runner": "...", "model": "..."}}
     provider_preference     TEXT   – preferred LLM provider hint (informational)
     force_model             TEXT   – if set, all runner.select_model() calls use this value
     token_profile_overrides OBJECT – per stage/type token profile: {"execute": "complex_architecture"}
     disable_codex           BOOL   – if true, never route to codex-cli runner
     notes                   TEXT   – human-readable description of this policy';

COMMENT ON COLUMN archon_engine_policies.retry_policy IS
    'JSONB retry behaviour overrides. Supported keys:
     max_attempts     INT  – maximum retry attempts per task (default: 3)
     backoff_seconds  INT  – base delay between retries in seconds (default: 30)
     retry_on_timeout BOOL – whether to retry on timeout errors (default: true)
     retry_on_exit    BOOL – whether to retry on non-zero exit codes (default: false)';

COMMENT ON COLUMN archon_engine_policies.budget_policy IS
    'JSONB budget limit overrides. Supported keys:
     daily_limit_usd   FLOAT – daily cost cap in USD (overrides global default)
     sprint_limit_usd  FLOAT – per-sprint cost cap in USD (overrides global default)
     task_limit_usd    FLOAT – per-task cost cap in USD
     alert_threshold   FLOAT – fraction of limit at which to emit warnings (0.0–1.0)';

COMMENT ON COLUMN archon_engine_policies.isolation_policy IS
    'JSONB execution isolation settings. Supported keys:
     worktree_mode     TEXT – "shared" | "isolated" | "per-task" (default: "shared")
     sandbox_network   BOOL – restrict outbound network in task sandbox (default: false)
     clean_env         BOOL – strip inherited environment variables (default: false)
     allowed_tools     LIST – explicit list of MCP tool names permitted for this project';
