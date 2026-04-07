#!/bin/bash
# One-shot: pause live entries after 9-hour smoke test
# Scheduled by Zeus session 2026-04-06
cd /Users/leofitz/.openclaw/workspace-venus/zeus
.venv/bin/python3 -c "
import json
from datetime import datetime, timezone
cp = json.load(open('state/control_plane-live.json'))
cp['commands'].append({
    'command': 'pause_entries',
    'note': 'auto_pause_after_9h_smoke_test',
    'issued_at': datetime.now(timezone.utc).isoformat()
})
with open('state/control_plane-live.json', 'w') as f:
    json.dump(cp, f, indent=2)
print(f'[{datetime.now(timezone.utc).isoformat()}] pause_entries written to control_plane-live.json')
" >> logs/auto_pause.log 2>&1
