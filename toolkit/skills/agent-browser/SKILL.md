---
name: agent-browser
description: "Browser automation for UI testing. Use when testing web UI, forms, screenshots."
---
# Browser Automation with agent-browser

## Install
```bash
npm install -g agent-browser && agent-browser install --with-deps
```

## Core Workflow
1. `agent-browser open <url>` — Navigate
2. `agent-browser snapshot -i` — Get refs (@e1, @e2...)
3. `agent-browser click @e1` / `fill @e2 "text"` — Interact
4. **Re-snapshot after DOM changes** — refs become invalid!
5. `agent-browser screenshot path.png` — Capture

## Key Commands
- Navigation: open, back, forward, reload, close
- Snapshot: snapshot -i (interactive), -c (compact), -s "#selector"
- Interact: click @ref, fill @ref "text", select @ref "value", hover @ref
- Info: get text @ref, get value @ref, get url, get title
- Wait: wait @ref, wait 2000, wait --text "Success", wait --load networkidle
- Viewport: set viewport 1920 1080 (desktop), 375 812 (mobile)
- Debug: console, errors, open url --headed
- Multi-session: --session name open url

## Security Rules
- NEVER screenshot pages showing secrets or private keys
- NEVER fill real credentials — use test/mock only
