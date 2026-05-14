#!/bin/bash
# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: Live monitoring during first-order qualification (operator directive 2026-05-11)
#
# Detects key data-chain milestones on the path to first live order:
# Tracks BOTH variants (3h derived + 6h backfill) so we see if
# derivation from mx2t6_high → mx2t3 keeps pace with source_run growth:
#   - mx2t3/mn2t3 source_run + readiness_state (derived 3-hour family)
#   - mx2t6/mn2t6 source_run + readiness_state (6-hour backfill family)
# A growing mx2t6_src but flat mx2t3_pr signals a derivation gap.
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
    def _count_src(dv):
        return conn.execute("""
            SELECT COUNT(*) FROM source_run
            WHERE data_version=?
              AND observed_members=expected_members AND status='SUCCESS'
        """, (dv,)).fetchone()[0]
    def _count_pr(dv):
        return conn.execute("""
            SELECT COUNT(*) FROM readiness_state
            WHERE data_version=?
              AND strategy_key='producer_readiness' AND status='LIVE_ELIGIBLE'
        """, (dv,)).fetchone()[0]
    mx2t3_src = _count_src('ecmwf_opendata_mx2t3_local_calendar_day_max_v1')
    mn2t3_src = _count_src('ecmwf_opendata_mn2t3_local_calendar_day_min_v1')
    mx2t6_src = _count_src('ecmwf_opendata_mx2t6_local_calendar_day_max_v1')
    mn2t6_src = _count_src('ecmwf_opendata_mn2t6_local_calendar_day_min_v1')
    mx2t3_pr = _count_pr('ecmwf_opendata_mx2t3_local_calendar_day_max_v1')
    mn2t3_pr = _count_pr('ecmwf_opendata_mn2t3_local_calendar_day_min_v1')
    mx2t6_pr = _count_pr('ecmwf_opendata_mx2t6_local_calendar_day_max_v1')
    mn2t6_pr = _count_pr('ecmwf_opendata_mn2t6_local_calendar_day_min_v1')
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

print(f"src[3h={mx2t3_src}/{mn2t3_src} 6h={mx2t6_src}/{mn2t6_src}] pr[3h={mx2t3_pr}/{mn2t3_pr} 6h={mx2t6_pr}/{mn2t6_pr}] latest={latest} g11={g11_state} fn={funnel.get('evaluated','?')}/{funnel.get('selected','?')}/{funnel.get('submitted','?')}/{funnel.get('filled','?')}")
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
