-- Wiki KB: Knowledge gaps identified by lint or agents
-- Gaps feed back into the discovery pipeline for auto-filling.

CREATE TABLE IF NOT EXISTS archon_knowledge_gaps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,

    -- Gap description
    topic           TEXT NOT NULL,
    description     TEXT NOT NULL,
    gap_type        TEXT NOT NULL CHECK (gap_type IN (
                        'missing_page', 'stale_content', 'contradiction',
                        'orphan', 'shallow_coverage', 'broken_source'
                    )),

    -- Evidence
    detected_by     TEXT NOT NULL DEFAULT 'lint' CHECK (detected_by IN (
                        'lint', 'query', 'agent', 'human'
                    )),
    related_pages   JSONB DEFAULT '[]'::jsonb,

    -- Resolution
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
                        'open', 'discovery_queued', 'resolved', 'wont_fix'
                    )),
    resolved_by     UUID REFERENCES archon_wiki_pages(id) ON DELETE SET NULL,
    priority        INT NOT NULL DEFAULT 50 CHECK (priority >= 0 AND priority <= 100),

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_project   ON archon_knowledge_gaps(project_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_status    ON archon_knowledge_gaps(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_type      ON archon_knowledge_gaps(gap_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_priority  ON archon_knowledge_gaps(priority DESC)
    WHERE status = 'open';

NOTIFY pgrst, 'reload schema';
