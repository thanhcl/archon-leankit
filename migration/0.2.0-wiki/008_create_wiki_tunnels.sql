-- Cross-Project Knowledge Tunnels
-- Enables governed knowledge sharing between wiki pages in different projects.
-- Inspired by MemPalace "Tunnels" concept.
--
-- Tunnels are created pending approval. Only approved (approved_at IS NOT NULL)
-- and non-revoked (revoked_at IS NULL) tunnels are active.

CREATE TABLE IF NOT EXISTS archon_wiki_tunnels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_page_id    UUID NOT NULL REFERENCES archon_wiki_pages(id) ON DELETE CASCADE,
    to_page_id      UUID NOT NULL REFERENCES archon_wiki_pages(id) ON DELETE CASCADE,
    tunnel_type     TEXT NOT NULL CHECK (tunnel_type IN (
                        'reference', 'extends', 'consumes', 'publishes'
                    )),
    visibility      TEXT NOT NULL DEFAULT 'read_only' CHECK (visibility IN (
                        'read_only', 'bidirectional'
                    )),
    context         TEXT,
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,

    CONSTRAINT uq_wiki_tunnel UNIQUE (from_page_id, to_page_id, tunnel_type),
    CONSTRAINT ck_wiki_tunnel_not_self CHECK (from_page_id != to_page_id)
);

CREATE INDEX IF NOT EXISTS idx_wiki_tunnels_from
    ON archon_wiki_tunnels (from_page_id) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_wiki_tunnels_to
    ON archon_wiki_tunnels (to_page_id) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_wiki_tunnels_type
    ON archon_wiki_tunnels (tunnel_type);

-- Partial index for active (approved + non-revoked) tunnels — used in graph traversal
CREATE INDEX IF NOT EXISTS idx_wiki_tunnels_active
    ON archon_wiki_tunnels (from_page_id, to_page_id)
    WHERE approved_at IS NOT NULL AND revoked_at IS NULL;
