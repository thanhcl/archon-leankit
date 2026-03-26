-- Add repo guidance packs so control-plane task policy can deliver repo-scoped
-- implementation instructions directly into runner prompts.

ALTER TABLE archon_tasks
ADD COLUMN IF NOT EXISTS repo_guidance_packs JSONB DEFAULT '[]'::JSONB;

COMMENT ON COLUMN archon_tasks.repo_guidance_packs IS
'Optional repo-scoped guidance packs attached to a task, each with a title, guidance text, and optional path_scope.';
