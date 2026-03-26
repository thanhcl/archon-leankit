-- Migration: 025_backfill_project_policy_sources_into_engine_policies.sql
-- Purpose: Backfill project-level runner, review, and isolation preferences into
--          archon_engine_policies without overwriting canonical policy values.

WITH project_policy_sources AS (
    SELECT
        project_id,
        CASE
            WHEN office_runner IN ('claude-code-cli', 'codex-cli') THEN office_runner
            WHEN team_lead_runner IN ('claude-code-cli', 'codex-cli') THEN team_lead_runner
            WHEN director_runner IN ('claude-code-cli', 'codex-cli') THEN director_runner
            ELSE NULL
        END AS default_runner,
        CASE
            WHEN team_lead_review_mode IN ('self-review', 'api', 'multi-perspective') THEN team_lead_review_mode
            WHEN director_review_mode IN ('self-review', 'api', 'multi-perspective') THEN director_review_mode
            WHEN office_review_mode IN ('self-review', 'api', 'multi-perspective') THEN office_review_mode
            ELSE NULL
        END AS review_mode,
        CASE
            WHEN office_isolation IN ('git-worktree', 'isolated') THEN 'isolated'
            WHEN office_isolation IN ('shared', 'per-task') THEN office_isolation
            WHEN team_lead_isolation IN ('git-worktree', 'isolated') THEN 'isolated'
            WHEN team_lead_isolation IN ('shared', 'per-task') THEN team_lead_isolation
            WHEN director_isolation IN ('git-worktree', 'isolated') THEN 'isolated'
            WHEN director_isolation IN ('shared', 'per-task') THEN director_isolation
            ELSE NULL
        END AS worktree_mode
    FROM (
        SELECT
            p.id AS project_id,
            COALESCE(
                p.office_settings->>'preferred_runner',
                p.office_settings->>'runner_preference',
                p.office_settings->>'default_runner',
                p.office_settings->>'runner_key',
                p.office_settings->>'execution_runner'
            ) AS office_runner,
            COALESCE(
                p.team_lead_config->>'preferred_runner',
                p.team_lead_config->>'runner_preference',
                p.team_lead_config->>'default_runner',
                p.team_lead_config->>'runner_key',
                p.team_lead_config->>'execution_runner'
            ) AS team_lead_runner,
            COALESCE(
                p.director_config->>'preferred_runner',
                p.director_config->>'runner_preference',
                p.director_config->>'default_runner',
                p.director_config->>'runner_key',
                p.director_config->>'execution_runner'
            ) AS director_runner,
            p.team_lead_config->>'review_mode' AS team_lead_review_mode,
            p.director_config->>'review_mode' AS director_review_mode,
            p.office_settings->>'review_mode' AS office_review_mode,
            COALESCE(
                p.office_settings->>'isolation_mode',
                p.office_settings->>'worktree_mode',
                p.office_settings->>'isolation'
            ) AS office_isolation,
            COALESCE(
                p.team_lead_config->>'isolation_mode',
                p.team_lead_config->>'worktree_mode',
                p.team_lead_config->>'isolation'
            ) AS team_lead_isolation,
            COALESCE(
                p.director_config->>'isolation_mode',
                p.director_config->>'worktree_mode',
                p.director_config->>'isolation'
            ) AS director_isolation
        FROM archon_projects p
    ) legacy
),
normalized_policy_rows AS (
    SELECT
        project_id,
        CASE
            WHEN default_runner IS NULL THEN '{}'::jsonb
            ELSE jsonb_build_object('default_runner', default_runner)
        END AS model_routing,
        CASE
            WHEN worktree_mode IS NULL THEN '{}'::jsonb
            ELSE jsonb_build_object('worktree_mode', worktree_mode)
        END AS isolation_policy,
        CASE
            WHEN review_mode IS NULL THEN '{}'::jsonb
            ELSE jsonb_build_object('review_mode', review_mode)
        END AS review_policy
    FROM project_policy_sources
    WHERE default_runner IS NOT NULL OR worktree_mode IS NOT NULL OR review_mode IS NOT NULL
)
INSERT INTO archon_engine_policies (
    project_id,
    is_active,
    model_routing,
    retry_policy,
    budget_policy,
    isolation_policy,
    review_policy,
    created_at,
    updated_at
)
SELECT
    project_id,
    TRUE,
    model_routing,
    '{}'::jsonb,
    '{}'::jsonb,
    isolation_policy,
    review_policy,
    NOW(),
    NOW()
FROM normalized_policy_rows
ON CONFLICT (project_id) DO UPDATE
SET
    model_routing = CASE
        WHEN archon_engine_policies.model_routing ? 'default_runner' THEN archon_engine_policies.model_routing
        WHEN EXCLUDED.model_routing = '{}'::jsonb THEN archon_engine_policies.model_routing
        ELSE archon_engine_policies.model_routing || EXCLUDED.model_routing
    END,
    isolation_policy = CASE
        WHEN archon_engine_policies.isolation_policy ? 'worktree_mode' THEN archon_engine_policies.isolation_policy
        WHEN EXCLUDED.isolation_policy = '{}'::jsonb THEN archon_engine_policies.isolation_policy
        ELSE archon_engine_policies.isolation_policy || EXCLUDED.isolation_policy
    END,
    review_policy = CASE
        WHEN archon_engine_policies.review_policy ? 'review_mode' THEN archon_engine_policies.review_policy
        WHEN EXCLUDED.review_policy = '{}'::jsonb THEN archon_engine_policies.review_policy
        ELSE archon_engine_policies.review_policy || EXCLUDED.review_policy
    END,
    updated_at = CASE
        WHEN (
            (NOT (archon_engine_policies.model_routing ? 'default_runner') AND EXCLUDED.model_routing <> '{}'::jsonb)
            OR (NOT (archon_engine_policies.isolation_policy ? 'worktree_mode') AND EXCLUDED.isolation_policy <> '{}'::jsonb)
            OR (NOT (archon_engine_policies.review_policy ? 'review_mode') AND EXCLUDED.review_policy <> '{}'::jsonb)
        ) THEN NOW()
        ELSE archon_engine_policies.updated_at
    END;

COMMENT ON TABLE archon_engine_policies IS
    'Per-project engine policies. Project-level runner preference, review mode,
     and isolation mode are backfilled from legacy project metadata into this
     table, while legacy metadata remains a read fallback during migration.';
