# Observability Hooks

Copy hook scripts from:
https://github.com/disler/claude-code-hooks-multi-agent-observability

## Setup
1. Clone the observability repo
2. Copy .claude/hooks/* from that repo into this directory
3. Start observability server: cd <repo> && just start
4. Replace $PROJECT_NAME in .claude/settings.json with your project name
5. Dashboard at http://localhost:5173

## Required hook scripts (from observability repo)
- send_event.py — Core event sender
- pre_tool_use.py — Tool validation + blocking
- post_tool_use.py — Result logging
- notification.py, stop.py
- subagent_start.py, subagent_stop.py
- session_start.py, session_end.py
- user_prompt_submit.py, pre_compact.py
- permission_request.py, post_tool_use_failure.py

## Custom hooks (add your own)
Place project-specific validation hooks here.
Example: kms_security_guard.py for PKCS#11 protection.
