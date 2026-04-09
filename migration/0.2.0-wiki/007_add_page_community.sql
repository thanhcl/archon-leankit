-- E4: Add community assignment to wiki pages for topic clustering

ALTER TABLE archon_wiki_pages
    ADD COLUMN IF NOT EXISTS community TEXT;

CREATE INDEX IF NOT EXISTS idx_wiki_pages_community
    ON archon_wiki_pages(community)
    WHERE community IS NOT NULL;

NOTIFY pgrst, 'reload schema';
