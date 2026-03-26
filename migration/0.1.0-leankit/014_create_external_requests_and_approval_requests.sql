-- Migration: 014_create_external_requests_and_approval_requests.sql
-- Purpose: Introduce first-class control-plane records for external ingress
-- requests and mobile/operator approval workflows.

CREATE TABLE IF NOT EXISTS archon_external_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NULL REFERENCES archon_projects(id) ON DELETE SET NULL,
    task_id UUID NULL REFERENCES archon_tasks(id) ON DELETE SET NULL,
    execution_run_id UUID NULL REFERENCES archon_execution_runs(id) ON DELETE SET NULL,
    bootstrap_plan_id UUID NULL REFERENCES archon_bootstrap_plans(id) ON DELETE SET NULL,
    source_channel TEXT NOT NULL,
    request_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    materialize_as TEXT NOT NULL DEFAULT 'none',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL,
    source_app TEXT NULL,
    actor_id TEXT NULL,
    actor_display TEXT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    linked_task_id UUID NULL REFERENCES archon_tasks(id) ON DELETE SET NULL,
    linked_approval_request_id UUID NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archon_external_requests_project_id
    ON archon_external_requests(project_id);

CREATE INDEX IF NOT EXISTS idx_archon_external_requests_status
    ON archon_external_requests(status);

CREATE INDEX IF NOT EXISTS idx_archon_external_requests_source_channel
    ON archon_external_requests(source_channel);

CREATE INDEX IF NOT EXISTS idx_archon_external_requests_correlation_id
    ON archon_external_requests(correlation_id);

CREATE TABLE IF NOT EXISTS archon_approval_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NULL REFERENCES archon_projects(id) ON DELETE SET NULL,
    task_id UUID NULL REFERENCES archon_tasks(id) ON DELETE SET NULL,
    execution_run_id UUID NULL REFERENCES archon_execution_runs(id) ON DELETE SET NULL,
    bootstrap_plan_id UUID NULL REFERENCES archon_bootstrap_plans(id) ON DELETE SET NULL,
    external_request_id UUID NULL REFERENCES archon_external_requests(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL,
    requested_channel TEXT NOT NULL,
    actor_id TEXT NULL,
    actor_display TEXT NULL,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by TEXT NULL,
    decision_comment TEXT NULL,
    decided_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archon_approval_requests_project_id
    ON archon_approval_requests(project_id);

CREATE INDEX IF NOT EXISTS idx_archon_approval_requests_status
    ON archon_approval_requests(status);

CREATE INDEX IF NOT EXISTS idx_archon_approval_requests_external_request_id
    ON archon_approval_requests(external_request_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_archon_external_requests_linked_approval_request'
    ) THEN
        ALTER TABLE archon_external_requests
            ADD CONSTRAINT fk_archon_external_requests_linked_approval_request
            FOREIGN KEY (linked_approval_request_id)
            REFERENCES archon_approval_requests(id)
            ON DELETE SET NULL;
    END IF;
END $$;

COMMENT ON TABLE archon_external_requests IS
    'External ingress requests from channels like Telegram or OpenClaw.';

COMMENT ON TABLE archon_approval_requests IS
    'Approval workflows and decision audit trail for high-impact actions.';
