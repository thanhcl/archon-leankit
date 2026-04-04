-- Migration: Add explicit workstream and phase identity fields to plan items.
-- These denormalised columns make workstream grouping canonical in the control-plane
-- data model, eliminating the need for clients to parse item_key prefixes.
--
-- workstream_key  – short canonical identifier extracted from item_key prefix
--                   (e.g. "B" from "B-P1-01", "ARCH" from "ARCH-P2-03")
-- workstream_name – human-readable label for the workstream (defaults to key)
-- phase_key       – short canonical identifier for the phase extracted from
--                   the item_key phase segment (e.g. "p1" from "B-P1-01")
-- phase_name      – denormalised copy of the parent phase title

ALTER TABLE project_implementation_items
    ADD COLUMN IF NOT EXISTS workstream_key  TEXT,
    ADD COLUMN IF NOT EXISTS workstream_name TEXT,
    ADD COLUMN IF NOT EXISTS phase_key       TEXT,
    ADD COLUMN IF NOT EXISTS phase_name      TEXT;

-- Index to support efficient workstream-based grouping queries
CREATE INDEX IF NOT EXISTS idx_impl_items_workstream_key
    ON project_implementation_items (workstream_key)
    WHERE workstream_key IS NOT NULL;

-- Backfill existing rows using item_key pattern ([A-Z]+)-(P\d+)-\d+
-- workstream_key / workstream_name = prefix before "-P" (e.g. "B", "ARCH")
-- phase_key = lowercased phase segment (e.g. "p1", "p2")
-- phase_name is left NULL; it will be populated on next re-import
UPDATE project_implementation_items
SET
    workstream_key  = (regexp_match(item_key, '^([A-Z]+)-P\d+-\d+$'))[1],
    workstream_name = (regexp_match(item_key, '^([A-Z]+)-P\d+-\d+$'))[1],
    phase_key       = lower((regexp_match(item_key, '^[A-Z]+-(P\d+)-\d+$'))[1])
WHERE item_key IS NOT NULL
  AND item_key ~ '^[A-Z]+-P\d+-\d+$';
