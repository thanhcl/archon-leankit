#!/usr/bin/env python3
"""Send event to Observability server — fail-safe, never blocks CC"""
import sys
import os
import uuid
from datetime import datetime, timezone

try:
    import urllib.request
    import json
    
    args = sys.argv[1:]
    source_app = "unknown"
    event_type = "unknown"
    summarize = False

    i = 0
    while i < len(args):
        if args[i] == "--source-app" and i + 1 < len(args):
            source_app = args[i + 1]; i += 2
        elif args[i] == "--event-type" and i + 1 < len(args):
            event_type = args[i + 1]; i += 2
        elif args[i] == "--summarize":
            summarize = True; i += 1
        else:
            i += 1

    server_url = os.environ.get("OBSERVABILITY_URL", "http://localhost:4000/api/events")
    
    # Read stdin for hook context (CC passes JSON via stdin)
    hook_data = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                hook_data = json.loads(raw)
    except Exception:
        pass
    
    # Build UnifiedEvent matching O-2 schema
    payload = json.dumps({
        "id": str(uuid.uuid4()),
        "type": "hook",
        "event": event_type,
        "source": "claude-code",
        "sourceApp": source_app,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "tool": hook_data.get("tool_name", hook_data.get("tool", "")),
            "input": hook_data.get("tool_input", ""),
            "output": hook_data.get("tool_output", "") if summarize else "",
            "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
            "model": os.environ.get("CLAUDE_MODEL", ""),
        }
    }).encode()
    
    req = urllib.request.Request(
        server_url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=2)
except Exception:
    pass  # NEVER block CC
