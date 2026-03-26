-- Migration: 005_project_office_config.sql
-- Purpose: Extend archon_projects with Virtual Office configuration.
-- Archon becomes single source of truth for project + office config.

ALTER TABLE archon_projects ADD COLUMN IF NOT EXISTS source_app TEXT;
ALTER TABLE archon_projects ADD COLUMN IF NOT EXISTS layout_id TEXT DEFAULT 'classic';
ALTER TABLE archon_projects ADD COLUMN IF NOT EXISTS team_config JSONB DEFAULT '[]';
ALTER TABLE archon_projects ADD COLUMN IF NOT EXISTS director_config JSONB DEFAULT '{"name": "Director", "color": "#1B3A5C"}';
ALTER TABLE archon_projects ADD COLUMN IF NOT EXISTS team_lead_config JSONB DEFAULT '{"name": "Team Lead", "color": "#4F46E5"}';
ALTER TABLE archon_projects ADD COLUMN IF NOT EXISTS office_settings JSONB DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_projects_source_app ON archon_projects(source_app);

COMMENT ON COLUMN archon_projects.source_app IS 'Virtual Office source app identifier (e.g., sesb-kms-project)';
COMMENT ON COLUMN archon_projects.layout_id IS 'Office layout theme: classic, executive-suite, central-hub, etc.';
COMMENT ON COLUMN archon_projects.team_config IS 'Array of workstation assignments: [{slotIndex, agentId, name, role, color, decorations}]';
COMMENT ON COLUMN archon_projects.director_config IS 'Director display config: {name, color}';
COMMENT ON COLUMN archon_projects.team_lead_config IS 'Team lead display config: {name, color}';
COMMENT ON COLUMN archon_projects.office_settings IS 'Virtual Office settings: {defaultView, showAgentLabels, mockMode, ...}';
