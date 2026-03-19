#!/bin/bash
ARCHON_API="http://localhost:8181"
# Telegram config (thay bằng bot token và chat ID của bạn)
TG_BOT_TOKEN="${TG_BOT_TOKEN:-}"
TG_CHAT_ID="${TG_CHAT_ID:-}"

# Count tasks by status
RESULT=$(curl -s "$ARCHON_API/api/tasks" 2>/dev/null | python3 -c "
import sys,json
try:
    tasks = json.load(sys.stdin).get('tasks',[])
    review = [t for t in tasks if t['status'] == 'review']
    failed = [t for t in tasks if t['status'] == 'failed']
    executing = [t for t in tasks if t['status'] == 'executing']
    
    if review or failed:
        lines = []
        if review:
            lines.append(f'📋 {len(review)} tasks chờ review:')
            for t in review:
                conf = (t.get('architect_review') or {}).get('confidence', '?')
                lines.append(f'  • {t[\"title\"][:50]} (conf:{conf})')
        if failed:
            lines.append(f'❌ {len(failed)} tasks failed:')
            for t in failed:
                lines.append(f'  • {t[\"title\"][:50]} (retry:{t.get(\"retry_count\",0)})')
        if executing:
            lines.append(f'⏳ {len(executing)} tasks đang chạy')
        lines.append('')
        lines.append('→ Mở Claude Chat, nói: review tasks')
        print('\n'.join(lines))
except:
    pass
" 2>/dev/null)

if [ -n "$RESULT" ]; then
    echo "$RESULT"
    
    # Telegram notification
    if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ]; then
        MSG=$(echo "$RESULT" | python3 -c "import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read()))")
        curl -s "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage?chat_id=${TG_CHAT_ID}&text=${MSG}&parse_mode=HTML" > /dev/null 2>&1
    fi
fi
