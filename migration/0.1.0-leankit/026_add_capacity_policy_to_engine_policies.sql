-- Migration 026: Add capacity_policy to engine policies
--
-- capacity_policy stores shared agent pool membership for a project.
-- Supported keys:
--   pool_id      (string)  — ID of the shared agent pool this project joins.
--   pool_slots   (integer) — Max concurrent pool slots this project may hold.
--                             Must be >= 1 and <= pool.total_slots.
--
-- Projects without a capacity_policy use only the global concurrency limit
-- and their per-project max_concurrent setting.

ALTER TABLE archon_engine_policies
    ADD COLUMN IF NOT EXISTS capacity_policy JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN archon_engine_policies.capacity_policy IS
    'Shared agent pool membership. Keys: pool_id (string), pool_slots (int >= 1). '
    'Leave empty to use only the global and per-project concurrency limits.';
