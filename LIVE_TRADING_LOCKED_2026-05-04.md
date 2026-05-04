# LIVE TRADING LOCKED — 2026-05-04

**Status:** Trade daemon shut down + plist quarantined + control_overrides entries_paused=true
**Locked by:** operator directive 2026-05-04 (via Claude Opus 4.7)
**Reason:** TIGGE 12z cycle asymmetry — live cannot run safely until Platt models retrained on dual-cycle data.

## Why locked

- TIGGE archive ingest currently has 00z-only for 17 months (852 issue_dates × 0 12z rows)
- ECMWF Open Data live feed has both 00z + 12z cycles
- Platt v2 calibration was trained on 00z-only TIGGE → cycle-blind buckets
- Mixing 12z forecasts at live time → systematic miscalibration (overconfident at shorter leads)
- 00z-only-trained Platt applied to 12z forecast = invalid statistical inference
- Live trading on miscalibrated probabilities = expected loss

This is not a software bug — it is a math/data-architecture asymmetry that must be fixed before going live again.

## Lock layers (defense in depth)

1. **launchctl bootout** of `com.zeus.live-trading` (active daemon killed; PID 76474 terminated 2026-05-04)
2. **Plist quarantined**: `~/Library/LaunchAgents/com.zeus.live-trading.plist` → renamed to `.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak` so `launchctl load`/`bootstrap` will not find it
3. **control_overrides::global:entries:gate = true** (reason: `LIVE_UNSAFE_2026_05_04_TIGGE_12Z_GAP_PLATT_NOT_RETRAINED`) — even if daemon were re-launched manually, evaluator would refuse entries
4. **This file** — human-readable record of state and unlock procedure

## What is still running

- `com.zeus.data-ingest` (PID 4571) — TIGGE / Open Data / observations ingest. **Should keep running** during the lock.
- `com.zeus.riskguard-live` (PID 14177) — RiskGuard tick. **Should keep running** for position monitoring on whatever existing positions exist.
- `com.zeus.heartbeat-sensor` — heartbeat probe. Keep running.

## Unlock procedure (in order; do not skip)

### Step 1: Confirm 12z TIGGE backfill is complete
```bash
sqlite3 state/zeus-world.db "SELECT substr(issue_time,12,2) cycle, COUNT(*) FROM ensemble_snapshots_v2 WHERE data_version LIKE 'tigge_%' AND issue_time >= date('now','-90 days') GROUP BY cycle;"
# Both 00z and 12z must show non-zero counts in the recent 90-day window
```

### Step 2: Confirm Platt v2 retrained with cycle stratification
```bash
sqlite3 state/zeus-world.db "PRAGMA table_info(platt_models_v2);"
# Schema must include a 'cycle' column (or equivalent stratifier)

sqlite3 state/zeus-world.db "SELECT cycle, COUNT(*) FROM platt_models_v2 WHERE is_active=1 GROUP BY cycle;"
# Must have rows for both '00' and '12'
```

If schema does NOT yet include cycle stratification, the math architecture is still asymmetric. Do NOT unlock.

### Step 3: Confirm live entry routing fix (PR #136) is merged
```bash
grep -n "openmeteo_ensemble_ecmwf_ifs025" src/data/forecast_source_registry.py
# Should NOT be the value of ENSEMBLE_MODEL_SOURCE_MAP[ecmwf_ifs025] for entry_primary path
# Should route to ecmwf_open_data instead
```

### Step 4: Lift control-plane lock
```bash
source .venv/bin/activate
python -c "
from src.control.control_plane import resume_entries  # or unpause_entries — verify exact API
resume_entries(
    reason_code='UNLOCK_2026_MM_DD_PLATT_RETRAINED_CYCLE_AWARE',
    issued_by='operator_<your_name>',
)
"
sqlite3 state/zeus-world.db "SELECT * FROM control_overrides WHERE target_key='entries';"
# value column must show 'false'
```

### Step 5: Restore plist and load
```bash
mv ~/Library/LaunchAgents/com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak \
   ~/Library/LaunchAgents/com.zeus.live-trading.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zeus.live-trading.plist
launchctl list | grep com.zeus.live-trading  # PID > 0 = running
```

### Step 6: Smoke test
- Wait one full opening_hunt cycle (15 min, fires at :14, :29, :44, :59 minute marks)
- Verify in `state/zeus_trades.db opportunity_fact` that recent rows have `should_trade=1` for at least some candidates
- Verify `venue_order_facts` row count incrementing (orders being placed)

## Do NOT short-circuit

If anyone (including operator) tries to unlock without Step 2 (Platt cycle-stratified retrain), the live system will trade on miscalibrated probabilities. That is expected loss.

## Authority

This lock is operator-authorized 2026-05-04. Lifting it requires operator approval AND completion of all six unlock steps. Document the unlock with a commit/note recording the date, who unlocked, and which retraining run produced the active Platt models.
