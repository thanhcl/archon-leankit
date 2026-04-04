#!/bin/bash
set -a
ROOT_DIR=~/Development/TrueAI/archon-leankit
if [ -f "$ROOT_DIR/.env" ]; then
  . "$ROOT_DIR/.env"
fi
set +a

# Ensure claude CLI is on PATH (installed at ~/.local/bin)
export PATH="$HOME/.local/bin:$PATH"

cd "$ROOT_DIR/python"
export SUPABASE_URL=http://localhost:8000
export SUPABASE_SERVICE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyAgCiAgICAicm9sZSI6ICJzZXJ2aWNlX3JvbGUiLAogICAgImlzcyI6ICJzdXBhYmFzZS1kZW1vIiwKICAgICJpYXQiOiAxNjQxNzY5MjAwLAogICAgImV4cCI6IDE3OTk1MzU2MDAKfQ.DaYlNEoUrrEn2Ig7tqibS-PHK5vgusbcbo7X36XVt4Q"
# Keep both Claude Code and Codex enabled so routing can use the appropriate
# runner per task/stage policy.
unset LEANKIT_ENGINE_DISABLE_CODEX
unset LEANKIT_ENGINE_DISABLE_CLAUDE_CODE
uv run python start_engine.py
