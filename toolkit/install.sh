#!/usr/bin/env bash
set -euo pipefail

# LeanKit V3 Toolkit Installer
# Usage: ./toolkit/install.sh <project_path> <source_app_name>

if [ $# -lt 2 ]; then
  echo "Usage: $0 <project_path> <source_app_name>"
  echo "  project_path   - Absolute path to the target project"
  echo "  source_app_name - App identifier for hooks (e.g. my-saas-app)"
  exit 1
fi

PROJECT_PATH="$1"
SOURCE_APP="$2"
TOOLKIT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🔧 Installing LeanKit V3 toolkit into: $PROJECT_PATH"
echo "   Source app name: $SOURCE_APP"
echo ""

# Create target directories
mkdir -p "$PROJECT_PATH/.claude/commands/opsx"
mkdir -p "$PROJECT_PATH/.claude/agents/reference/testing-personalities"
mkdir -p "$PROJECT_PATH/.claude/agents/team"
mkdir -p "$PROJECT_PATH/.claude/skills"
mkdir -p "$PROJECT_PATH/.claude/templates"
mkdir -p "$PROJECT_PATH/.claude/hooks/utils/llm"
mkdir -p "$PROJECT_PATH/.claude/hooks/utils/tts"
mkdir -p "$PROJECT_PATH/.claude/hooks/validators"
mkdir -p "$PROJECT_PATH/.claude/hooks/examples"
mkdir -p "$PROJECT_PATH/PRPs/templates"

# Copy commands
cp -r "$TOOLKIT_DIR/commands/"* "$PROJECT_PATH/.claude/commands/"
echo "  ✓ Commands copied"

# Copy agents
cp -r "$TOOLKIT_DIR/agents/"* "$PROJECT_PATH/.claude/agents/"
echo "  ✓ Agents copied"

# Copy skills
cp -r "$TOOLKIT_DIR/skills/"* "$PROJECT_PATH/.claude/skills/"
echo "  ✓ Skills copied"

# Copy templates
cp -r "$TOOLKIT_DIR/templates/"* "$PROJECT_PATH/.claude/templates/"
echo "  ✓ Templates copied"

# Copy hooks
cp -r "$TOOLKIT_DIR/hooks/"* "$PROJECT_PATH/.claude/hooks/"
echo "  ✓ Hooks copied"

# Copy PRP templates
cp -r "$TOOLKIT_DIR/prp-templates/"* "$PROJECT_PATH/PRPs/templates/"
echo "  ✓ PRP templates copied"

# Generate settings.json from template
sed "s/__SOURCE_APP__/$SOURCE_APP/g" "$TOOLKIT_DIR/settings.template.json" > "$PROJECT_PATH/.claude/settings.json"
echo "  ✓ settings.json generated (source-app: $SOURCE_APP)"

echo ""
echo "✅ Toolkit installed successfully!"
echo ""
echo "Next steps:"
echo "  1. Add Archon MCP server (if not in settings.json already):"
echo "     claude mcp add archon --transport http http://localhost:8051/mcp"
echo ""
echo "  2. Generate project-specific CLAUDE.md:"
echo "     cd $PROJECT_PATH && claude /create-rules"
echo ""
echo "  3. Add project docs to Archon knowledge base:"
echo "     - Upload relevant docs via Archon UI"
echo "     - Or use rag_search_knowledge_base to verify existing docs"
echo ""
echo "  4. Create project in Archon:"
echo "     Use manage_project('create', ...) via MCP"
