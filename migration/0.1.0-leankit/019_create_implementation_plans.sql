-- Migration: 019_create_implementation_plans.sql
-- Purpose: Create control-plane tables for project implementation plans,
--          phases, items, dependencies, task links, and snapshots.

-- ── 1. Root plan record ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_implementation_plans (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    UUID        NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,
    title         TEXT        NOT NULL,
    description   TEXT        NULL,
    status        TEXT        NOT NULL DEFAULT 'draft',
    created_by    TEXT        NULL,
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_impl_plans_project_id
    ON project_implementation_plans(project_id);

CREATE INDEX IF NOT EXISTS idx_impl_plans_status
    ON project_implementation_plans(status);

CREATE INDEX IF NOT EXISTS idx_impl_plans_created_at
    ON project_implementation_plans(created_at DESC);

COMMENT ON TABLE project_implementation_plans IS
    'Root record for a structured implementation plan attached to a project.';

-- ── 2. Phases ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_implementation_phases (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     UUID        NOT NULL REFERENCES project_implementation_plans(id) ON DELETE CASCADE,
    title       TEXT        NOT NULL,
    description TEXT        NULL,
    phase_order INT         NOT NULL DEFAULT 0,
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_impl_phases_plan_id
    ON project_implementation_phases(plan_id);

CREATE INDEX IF NOT EXISTS idx_impl_phases_order
    ON project_implementation_phases(plan_id, phase_order);

COMMENT ON TABLE project_implementation_phases IS
    'Ordered phases within an implementation plan.';

-- ── 3. Items ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_implementation_items (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id      UUID        NOT NULL REFERENCES project_implementation_plans(id) ON DELETE CASCADE,
    phase_id     UUID        NULL REFERENCES project_implementation_phases(id) ON DELETE SET NULL,
    title        TEXT        NOT NULL,
    description  TEXT        NULL,
    status       TEXT        NOT NULL DEFAULT 'planned',
    item_order   INT         NOT NULL DEFAULT 0,
    priority     TEXT        NOT NULL DEFAULT 'medium',
    complexity   TEXT        NOT NULL DEFAULT 'simple',
    item_key     TEXT        NULL,
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_impl_item_status CHECK (
        status IN ('planned', 'ready', 'in_progress', 'blocked', 'review', 'done', 'deferred', 'cancelled')
    )
);

CREATE INDEX IF NOT EXISTS idx_impl_items_plan_id
    ON project_implementation_items(plan_id);

CREATE INDEX IF NOT EXISTS idx_impl_items_phase_id
    ON project_implementation_items(phase_id);

CREATE INDEX IF NOT EXISTS idx_impl_items_status
    ON project_implementation_items(status);

CREATE INDEX IF NOT EXISTS idx_impl_items_key
    ON project_implementation_items(item_key)
    WHERE item_key IS NOT NULL;

COMMENT ON TABLE project_implementation_items IS
    'Individual work items within an implementation plan phase.
     status must be one of: planned, ready, in_progress, blocked, review, done, deferred, cancelled.';

-- ── 4. Dependencies ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_implementation_item_dependencies (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    dependent_id    UUID        NOT NULL REFERENCES project_implementation_items(id) ON DELETE CASCADE,
    dependency_id   UUID        NOT NULL REFERENCES project_implementation_items(id) ON DELETE CASCADE,
    dependency_type TEXT        NOT NULL DEFAULT 'blocks',
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_impl_dep UNIQUE (dependent_id, dependency_id),
    CONSTRAINT chk_impl_dep_no_self CHECK (dependent_id != dependency_id)
);

CREATE INDEX IF NOT EXISTS idx_impl_deps_dependent_id
    ON project_implementation_item_dependencies(dependent_id);

CREATE INDEX IF NOT EXISTS idx_impl_deps_dependency_id
    ON project_implementation_item_dependencies(dependency_id);

COMMENT ON TABLE project_implementation_item_dependencies IS
    'First-class dependency edges between implementation items.
     dependency_type describes the relationship (e.g. blocks, requires, related_to).';

COMMENT ON COLUMN project_implementation_item_dependencies.dependent_id IS
    'The item that depends on (is blocked by) the dependency_id item.';

COMMENT ON COLUMN project_implementation_item_dependencies.dependency_id IS
    'The item that must be completed before the dependent_id item can start.';

-- ── 5. Task links ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_implementation_item_task_links (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id    UUID        NOT NULL REFERENCES project_implementation_items(id) ON DELETE CASCADE,
    task_id    UUID        NOT NULL REFERENCES archon_tasks(id) ON DELETE CASCADE,
    link_type  TEXT        NOT NULL DEFAULT 'implements',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_impl_task_link UNIQUE (item_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_impl_task_links_item_id
    ON project_implementation_item_task_links(item_id);

CREATE INDEX IF NOT EXISTS idx_impl_task_links_task_id
    ON project_implementation_item_task_links(task_id);

COMMENT ON TABLE project_implementation_item_task_links IS
    'Links between implementation plan items and Archon tasks, providing
     traceability from the canonical execution spine to the plan.';

-- ── 6. Snapshots ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project_implementation_snapshots (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     UUID        NOT NULL REFERENCES project_implementation_plans(id) ON DELETE CASCADE,
    label       TEXT        NULL,
    snapshot    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_by  TEXT        NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_impl_snapshots_plan_id
    ON project_implementation_snapshots(plan_id);

CREATE INDEX IF NOT EXISTS idx_impl_snapshots_created_at
    ON project_implementation_snapshots(plan_id, created_at DESC);

COMMENT ON TABLE project_implementation_snapshots IS
    'Point-in-time snapshots of implementation plan state for versioning and audit.';
