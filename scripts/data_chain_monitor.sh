#!/bin/bash
# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: Live monitoring during first-order qualification (operator directive 2026-05-11)
#
# Detects key data-chain milestones on the path to first live order:
#   - New mx2t6/mn2t6 source_run row with full members
#   - New mx2t6/mn2t6 producer_readiness row
#   - Gate 11 transitions BLOCKING → CLEAR
#   - lifecycle_funnel.selected > 0
#   - lifecycle_funnel.submitted > 0
#   - lifecycle_funnel.filled > 0
# Emits a line ONLY on state change. Silent otherwise.

cd /Users/leofitz/.openclaw/workspace-venus/zeus
STATE_FILE=/tmp/zeus_chain_state.txt
touch "$STATE_FILE"

while true; do
  snapshot=$(.venv/bin/python << 'PY'
import sqlite3, json
try:
    conn = sqlite3.connect('state/zeus-world.db', timeout=2)
    mx2t6_src = conn.execute("""
        SELECT COUNT(*) FROM source_run
        WHERE data_version='ecmwf_opendata_mx2t6_local_calendar_day_max_v1'
          AND observed_members=expected_members AND status='SUCCESS'
    """).fetchone()[0]
    mn2t6_src = conn.execute("""
        SELECT COUNT(*) FROM source_run
        WHERE data_version='ecmwf_opendata_mn2t6_local_calendar_day_min_v1'
          AND observed_members=expected_members AND status='SUCCESS'
    """).fetchone()[0]
    mx2t6_pr = conn.execute("""
        SELECT COUNT(*) FROM readiness_state
        WHERE data_version='ecmwf_opendata_mx2t6_local_calendar_day_max_v1'
          AND strategy_key='producer_readiness' AND status='LIVE_ELIGIBLE'
    """).fetchone()[0]
    mn2t6_pr = conn.execute("""
        SELECT COUNT(*) FROM readiness_state
        WHERE data_version='ecmwf_opendata_mn2t6_local_calendar_day_min_v1'
          AND strategy_key='producer_readiness' AND status='LIVE_ELIGIBLE'
    """).fetchone()[0]
    # Latest source_run age (freshness) - K1 blocker observability
    latest = conn.execute("""
        SELECT MAX(source_run_id) FROM readiness_state
        WHERE data_version LIKE 'ecmwf_opendata_m%' AND status='LIVE_ELIGIBLE'
    """).fetchone()[0] or 'NONE'
    conn.close()
except Exception as e:
    print(f"db_err={e}")
    raise SystemExit(0)

# Read status_summary for funnel + gate 11
try:
    ss = json.load(open('state/status_summary.json'))
    cycle = ss.get('cycle',{})
    funnel = ss.get('lifecycle_funnel',{}).get('counts',{})
    g11_state = "unknown"
    for b in cycle.get('block_registry',[]):
        if b.get('name') == 'evaluate_entry_forecast_rollout_gate':
            g11_state = b.get('state','unknown')
            break
except Exception:
    funnel = {}
    g11_state = "unknown"

print(f"mx2t6_src={mx2t6_src} mn2t6_src={mn2t6_src} mx2t6_pr={mx2t6_pr} mn2t6_pr={mn2t6_pr} latest_run={latest} g11={g11_state} eval={funnel.get('evaluated','?')} sel={funnel.get('selected','?')} sub={funnel.get('submitted','?')} fil={funnel.get('filled','?')}")
PY
)
  last=$(cat "$STATE_FILE" 2>/dev/null)
  if [ "$snapshot" != "$last" ]; then
    ts=$(date -u +%H:%M:%SZ)
    echo "CHAIN $ts $snapshot"
    echo "$snapshot" > "$STATE_FILE"
  fi
  sleep 60
done
