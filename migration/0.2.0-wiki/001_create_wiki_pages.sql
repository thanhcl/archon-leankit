-- Wiki KB: Core wiki pages table
-- Transforms RAG "chunk bag" into structured knowledge graph.
-- Each page represents one entity, concept, source summary, or synthesis.

CREATE TABLE IF NOT EXISTS archon_wiki_pages (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id            UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,

    -- Identity
    slug                  TEXT NOT NULL,
    title                 TEXT NOT NULL,
    page_type             TEXT NOT NULL CHECK (page_type IN ('entity', 'concept', 'source_summary', 'synthesis')),

    -- Content
    content               TEXT NOT NULL DEFAULT '',
    summary               TEXT,

    -- Classification
    category              TEXT CHECK (category IS NULL OR category IN (
                              'people', 'tool', 'framework', 'pattern',
                              'architecture', 'api', 'infra', 'library',
                              'service', 'protocol', 'methodology'
                          )),
    tags                  JSONB DEFAULT '[]'::jsonb,

    -- Source tracking
    source_ids            JSONB DEFAULT '[]'::jsonb,
    evidence              JSONB DEFAULT '[]'::jsonb,

    -- Lifecycle
    status                TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                              'draft', 'active', 'stale', 'archived', 'contradiction'
                          )),
    quality_score         NUMERIC(3,2) DEFAULT 0.50 CHECK (quality_score >= 0 AND quality_score <= 1),

    -- Vector search (1024-dim, matching common embedding models)
    embedding             VECTOR(1024),

    -- Full-text search
    content_search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))
    ) STORED,

    -- Timestamps
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_verified_at      TIMESTAMPTZ,

    -- Unique constraint: one slug per project
    UNIQUE(project_id, slug)
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_wiki_pages_project       ON archon_wiki_pages(project_id);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_type          ON archon_wiki_pages(page_type);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_status        ON archon_wiki_pages(status);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_category      ON archon_wiki_pages(category);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_tags          ON archon_wiki_pages USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_source_ids    ON archon_wiki_pages USING GIN(source_ids);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_fts           ON archon_wiki_pages USING GIN(content_search_vector);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_updated       ON archon_wiki_pages(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_quality       ON archon_wiki_pages(quality_score DESC);

-- Vector index (IVFFlat for cosine similarity)
-- Note: Only create if pgvector extension is available and table has rows.
-- For empty tables, IVFFlat requires at least lists * 10 rows.
-- Start with HNSW which works on empty tables.
CREATE INDEX IF NOT EXISTS idx_wiki_pages_embedding
    ON archon_wiki_pages USING hnsw(embedding vector_cosine_ops);

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';
