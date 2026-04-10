-- Migration: 041_create_activity_log.sql
-- Purpose: Activity log table for internal event bus listeners.
-- Workstream: M-P4-02 (Activity Log Service)

CREATE TABLE IF NOT EXISTS archon_activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'control-plane',
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_log_topic
    ON archon_activity_log(topic);

CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp
    ON archon_activity_log(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_activity_log_source
    ON archon_activity_log(source);

-- GIN index for JSONB data queries (filter by task_id, project_id, etc.)
CREATE INDEX IF NOT EXISTS idx_activity_log_data
    ON archon_activity_log USING gin(data);

COMMENT ON TABLE archon_activity_log IS
    'Workspace activity history powered by internal event bus. Records all control-plane events for audit and timeline views.';

COMMENT ON COLUMN archon_activity_log.topic IS
    'Event topic (e.g., task.created, runtime.registered, assignment.changed).';

COMMENT ON COLUMN archon_activity_log.source IS
    'Origin of the event (e.g., control-plane, execution-engine, runtime-daemon).';
