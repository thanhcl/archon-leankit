-- Wiki KB: Discovery history — track discovered items for dedup + audit
-- Every URL discovered by any strategy is recorded here to prevent re-processing.

CREATE TABLE IF NOT EXISTS archon_discovery_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feed_id         UUID REFERENCES archon_discovery_feeds(id) ON DELETE SET NULL,
    project_id      UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,

    -- Discovered item
    url             TEXT NOT NULL,
    title           TEXT,
    snippet         TEXT,

    -- Processing status
    status          TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN (
                        'discovered', 'queued', 'ingesting', 'ingested',
                        'skipped', 'failed'
                    )),
    skip_reason     TEXT,
    error_message   TEXT,

    -- Linkage to RAG + Wiki
    source_id       TEXT,
    wiki_page_ids   JSONB DEFAULT '[]'::jsonb,

    -- Dedup: hash of URL for fast lookup
    url_hash        TEXT NOT NULL,

    -- Timestamps
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at     TIMESTAMPTZ,

    UNIQUE(url_hash)
);

-- Note: url_hash is computed by application code as SHA256(url) to avoid
-- dependency on pgcrypto extension. Application MUST set url_hash on insert.

CREATE INDEX IF NOT EXISTS idx_discovery_history_project   ON archon_discovery_history(project_id);
CREATE INDEX IF NOT EXISTS idx_discovery_history_feed      ON archon_discovery_history(feed_id);
CREATE INDEX IF NOT EXISTS idx_discovery_history_status    ON archon_discovery_history(status);
CREATE INDEX IF NOT EXISTS idx_discovery_history_url_hash  ON archon_discovery_history(url_hash);
CREATE INDEX IF NOT EXISTS idx_discovery_history_discovered ON archon_discovery_history(discovered_at DESC);

NOTIFY pgrst, 'reload schema';
