-- Wiki KB: Auto-discovery feed configuration
-- Each feed represents one source of new knowledge (GitHub, Reddit, RSS, etc.)

CREATE TABLE IF NOT EXISTS archon_discovery_feeds (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id            UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,

    -- Feed config
    feed_type             TEXT NOT NULL CHECK (feed_type IN (
                              'github_trending', 'github_repo', 'reddit',
                              'rss', 'hackernews', 'web_search', 'reference_snowball'
                          )),
    name                  TEXT NOT NULL,
    config                JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Scheduling
    enabled               BOOLEAN NOT NULL DEFAULT true,
    poll_interval_hours   INT NOT NULL DEFAULT 24 CHECK (poll_interval_hours >= 1),
    last_polled_at        TIMESTAMPTZ,
    next_poll_at          TIMESTAMPTZ,

    -- Limits
    max_items_per_poll    INT NOT NULL DEFAULT 5 CHECK (max_items_per_poll >= 1 AND max_items_per_poll <= 50),

    -- Stats
    total_discovered      INT NOT NULL DEFAULT 0,
    total_ingested        INT NOT NULL DEFAULT 0,

    -- Timestamps
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_discovery_feeds_project   ON archon_discovery_feeds(project_id);
CREATE INDEX IF NOT EXISTS idx_discovery_feeds_type      ON archon_discovery_feeds(feed_type);
CREATE INDEX IF NOT EXISTS idx_discovery_feeds_enabled   ON archon_discovery_feeds(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_discovery_feeds_next_poll ON archon_discovery_feeds(next_poll_at)
    WHERE enabled = true AND next_poll_at IS NOT NULL;

NOTIFY pgrst, 'reload schema';
