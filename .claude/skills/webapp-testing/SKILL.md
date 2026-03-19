---
name: webapp-testing
description: "UI verification via Playwright screenshots. Use when verifying UI changes, visual regression, or Virtual Office tasks."
---
# Webapp Testing — Screenshot-Based UI Verification

Playwright-powered skill for verifying UI changes via screenshots. Especially useful for
Virtual Office tasks where visual confirmation of changes is required.

## Prerequisites
- `agent-browser` skill installed (`npm install -g agent-browser && agent-browser install --with-deps`)
- Application running locally (default: `http://localhost:3737` for frontend)

## Workflow

### 1. Capture Baseline (before changes)
```bash
agent-browser open http://localhost:3737
agent-browser set viewport 1920 1080
agent-browser screenshot screenshots/baseline.png
```

### 2. Navigate to Target Page
```bash
agent-browser open http://localhost:3737/<route>
agent-browser wait --load networkidle
agent-browser screenshot screenshots/before-<feature>.png
```

### 3. Apply Changes & Verify
After code changes are applied and hot-reload completes:
```bash
agent-browser reload
agent-browser wait --load networkidle
agent-browser screenshot screenshots/after-<feature>.png
```

### 4. Interactive Verification
```bash
agent-browser snapshot -i              # Get element refs
agent-browser get text @e1             # Verify text content
agent-browser get value @e2            # Verify input values
agent-browser click @e3                # Test interactions
agent-browser wait --text "Success"    # Wait for expected result
agent-browser screenshot screenshots/interaction-result.png
```

### 5. Responsive Checks
```bash
agent-browser set viewport 375 812    # Mobile
agent-browser screenshot screenshots/mobile-<feature>.png
agent-browser set viewport 768 1024   # Tablet
agent-browser screenshot screenshots/tablet-<feature>.png
agent-browser set viewport 1920 1080  # Desktop (restore)
agent-browser screenshot screenshots/desktop-<feature>.png
```

## Verification Checklist
For each UI change, capture and review screenshots confirming:
- [ ] Component renders correctly at desktop viewport
- [ ] Component renders correctly at mobile viewport
- [ ] Interactive elements respond to clicks/input
- [ ] Loading states display properly
- [ ] Error states display properly (if applicable)
- [ ] No visual regressions in surrounding components

## Virtual Office Tasks
For Virtual Office UI verification:
1. Open the target Virtual Office page
2. Screenshot the current state
3. Apply changes, reload, screenshot again
4. Compare before/after visually via the Read tool on screenshot PNGs
5. Verify interactions (hover states, modals, tooltips)

## Report Format
```
UI VERIFICATION REPORT:
- Pages tested: {count}
- Screenshots captured: {count}
- Viewports checked: desktop, mobile, tablet
- Visual issues found: {count}
- Interactive tests: {pass}/{total}
- Status: PASS / NEEDS_ATTENTION
```

## Security Rules
- NEVER screenshot pages showing secrets, tokens, or private keys
- NEVER fill real credentials — use test/mock data only
- NEVER capture screenshots containing PII or sensitive user data
