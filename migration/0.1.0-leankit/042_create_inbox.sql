-- Migration: 042_create_inbox.sql
-- Purpose: Per-member/per-agent inbox for notifications.
-- Workstream: M-P4-06 (Inbox & Notification Model)

CREATE TABLE IF NOT EXISTS archon_inbox_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_type TEXT NOT NULL CHECK (
        recipient_type IN ('member', 'agent')
    ),
    recipient_id UUID NOT NULL,
    item_type TEXT NOT NULL CHECK (
        item_type IN (
            'assignment', 'mention', 'review_request',
            'blocker', 'progress_update', 'completion',
            'failure', 'escalation', 'comment', 'system'
        )
    ),
    priority TEXT NOT NULL DEFAULT 'info' CHECK (
        priority IN ('action_required', 'attention', 'info')
    ),
    title TEXT NOT NULL,
    body TEXT NULL,
    source_task_id UUID NULL,
    source_run_id UUID NULL,
    source_project_id UUID NULL,
    read_at TIMESTAMPTZ NULL,
    archived_at TIMESTAMPTZ NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inbox_recipient
    ON archon_inbox_items(recipient_type, recipient_id);

CREATE INDEX IF NOT EXISTS idx_inbox_unread
    ON archon_inbox_items(recipient_type, recipient_id, read_at)
    WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_inbox_created
    ON archon_inbox_items(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_inbox_priority
    ON archon_inbox_items(priority)
    WHERE read_at IS NULL;

COMMENT ON TABLE archon_inbox_items IS
    'Per-member and per-agent inbox for notifications. Powered by internal event bus.';

COMMENT ON COLUMN archon_inbox_items.recipient_type IS
    'Who receives this notification: member (human) or agent (AI).';

COMMENT ON COLUMN archon_inbox_items.item_type IS
    'Notification category: assignment, mention, review_request, blocker, progress_update, completion, failure, escalation, comment, system.';

COMMENT ON COLUMN archon_inbox_items.priority IS
    'Urgency: action_required (needs response), attention (should review), info (FYI).';
