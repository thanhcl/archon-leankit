-- Wiki KB: Cross-reference links between wiki pages
-- Replaces plain-text [[wiki-links]] with typed, weighted, bidirectional DB links.

CREATE TABLE IF NOT EXISTS archon_wiki_links (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Link endpoints
    from_page_id  UUID NOT NULL REFERENCES archon_wiki_pages(id) ON DELETE CASCADE,
    to_page_id    UUID NOT NULL REFERENCES archon_wiki_pages(id) ON DELETE CASCADE,

    -- Link metadata
    link_type     TEXT NOT NULL DEFAULT 'related' CHECK (link_type IN (
                      'related', 'depends_on', 'contradicts', 'extends',
                      'supersedes', 'example_of', 'part_of'
                  )),
    context       TEXT,
    strength      NUMERIC(3,2) DEFAULT 0.50 CHECK (strength >= 0 AND strength <= 1),

    -- Provenance
    created_by    TEXT NOT NULL DEFAULT 'system' CHECK (created_by IN ('system', 'agent', 'human')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Constraints
    UNIQUE(from_page_id, to_page_id, link_type),
    CHECK(from_page_id != to_page_id)
);

-- Indexes for graph traversal (both directions)
CREATE INDEX IF NOT EXISTS idx_wiki_links_from   ON archon_wiki_links(from_page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_links_to     ON archon_wiki_links(to_page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_links_type   ON archon_wiki_links(link_type);

-- Composite index for finding all links between two pages
CREATE INDEX IF NOT EXISTS idx_wiki_links_pair   ON archon_wiki_links(from_page_id, to_page_id);

NOTIFY pgrst, 'reload schema';
