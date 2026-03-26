#!/bin/bash
cd ~/Development/TrueAI/archon-leankit/python
export SUPABASE_URL=http://localhost:8000
export SUPABASE_SERVICE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyAgCiAgICAicm9sZSI6ICJzZXJ2aWNlX3JvbGUiLAogICAgImlzcyI6ICJzdXBhYmFzZS1kZW1vIiwKICAgICJpYXQiOiAxNjQxNzY5MjAwLAogICAgImV4cCI6IDE3OTk1MzU2MDAKfQ.DaYlNEoUrrEn2Ig7tqibS-PHK5vgusbcbo7X36XVt4Q"
# Force all tasks to Claude Code CLI — disable Codex routing until Codex quota resets
export LEANKIT_ENGINE_DISABLE_CODEX=true
uv run python start_engine.py
