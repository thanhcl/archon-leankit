-- E2: Add confidence classification to wiki links
-- Maps strength to semantic levels: extracted, inferred, ambiguous

ALTER TABLE archon_wiki_links
    ADD COLUMN IF NOT EXISTS confidence TEXT
        DEFAULT 'inferred'
        CHECK (confidence IN ('extracted', 'inferred', 'ambiguous'));

-- Backfill existing rows based on strength thresholds
UPDATE archon_wiki_links SET confidence = 'extracted' WHERE strength >= 0.8;
UPDATE archon_wiki_links SET confidence = 'ambiguous'  WHERE strength <= 0.3;

CREATE INDEX IF NOT EXISTS idx_wiki_links_confidence ON archon_wiki_links(confidence);

NOTIFY pgrst, 'reload schema';
