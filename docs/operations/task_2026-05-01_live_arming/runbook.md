# Live Arming Runbook (2026-05-01)

**Operator directive**: 把所有flag都切换成live模式，但是我们不启动live deamon就好。
**Goal**: when operator runs `launchctl load …`, daemons begin placing live orders without further configuration.

This runbook is the durable record of the arming. The reproducible script is `scripts/arm_live_mode.sh` (also committed).

## Three flags flipped

| # | Flag | Where | From → To | Why |
|---|---|---|---|---|
| 1 | `ZEUS_HARVESTER_LIVE_ENABLED` | `~/Library/LaunchAgents/com.zeus.live-trading.plist` + `com.zeus.data-ingest.plist` | absent → `1` | DR-33-A safety gate. Without it, `harvester_truth_writer` is a no-op and every settlement carries `quarantine_reason=no_observation_for_target_date`. With it, settlement truth gets written; PnL becomes real. |
| 2 | `cutover_guard` state | `state/cutover_guard.json` | absent (default `NORMAL`) → `LIVE_ENABLED` | `gate_for_intent(IntentKind.ENTRY)` returns `allow_submit=True` only in `LIVE_ENABLED`. All other states block live order submission. |
| 3 | `entries_paused` override | `state/zeus-world.db::control_overrides` | active since 2026-04-28T22:03Z → `effective_until` set to now | Auto-paused 4 days ago by `system_auto_pause` after a `ValueError`. `is_entries_paused()` returns `False` once `effective_until` is in the past. |

## What this does NOT include

- **HK quarantine release**: deferred. HKO 2026-04 archive isn't published yet (they publish ~1 month after end-of-month, so expect ~2026-06-01). When that lands and the daemon completes a 30-day backfill, operator will:
  1. Update `architecture/preflight_overrides_2026-04-28.yaml` `hko_canonical` entry with a release signature.
  2. Bulk `UPDATE observations SET authority='VERIFIED' WHERE city='Hong Kong'`.
  3. Run `rebuild_calibration_pairs_v2 + refit_platt_v2 --cluster "Hong Kong"`.
- **Paris LFPG legacy QUARANTINE downgrade**: deferred. 839 LFPG observations + 56 LFPG settlements + 8 LFPG Platt rows. Will run alongside HK release as a single batched operation. Predicate documented in `architecture/paris_station_resolution_2026-05-01.yaml`.
- **TIGGE 4/30 + 5/1 issue downloads**: ECMWF embargo lifts 5/2 00:00Z (4/30) and 5/3 00:00Z (5/1). The daemon's `_tigge_archive_backfill_cycle` (cron 14:00 UTC) targets `today-2`, so it will pick these up automatically after the operator loads the daemon.
- **Daemon load itself**: operator owns timing. See last section.

## Verification (run after `arm_live_mode.sh`)

```bash
# Verify cutover state
python -c "import json; print(json.load(open('state/cutover_guard.json'))['state'])"
# expect: LIVE_ENABLED

# Verify entries_paused expired
sqlite3 state/zeus-world.db \
  "SELECT override_id, value, effective_until FROM control_overrides \
   WHERE override_id='control_plane:global:entries_paused';"
# expect: effective_until is set (not NULL) and earlier than now

# Verify plist env var
/usr/libexec/PlistBuddy -c \
  "Print :EnvironmentVariables:ZEUS_HARVESTER_LIVE_ENABLED" \
  ~/Library/LaunchAgents/com.zeus.live-trading.plist
# expect: 1
/usr/libexec/PlistBuddy -c \
  "Print :EnvironmentVariables:ZEUS_HARVESTER_LIVE_ENABLED" \
  ~/Library/LaunchAgents/com.zeus.data-ingest.plist
# expect: 1
```

## When operator is ready to fire

```bash
launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist
launchctl load ~/Library/LaunchAgents/com.zeus.riskguard-live.plist
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist
```

The order matters slightly: ingest first → it populates `state/source_health.json`, runs the K1 migration if needed, kicks off TIGGE/Open Data catch-up. Riskguard second → it ticks every 60s and within ~5 min should leave `DATA_DEGRADED` and reach a real risk level. Trading last → it reads source_health (warn-only on STALE), passes wallet check, transitions to `LIVE_ENABLED` posture as already configured, and the first cycle will reach the strategy evaluator.

**Recommended baby-sitting**: stay attached to the trading log for at least 10 minutes after load:

```bash
tail -F logs/zeus-live.err
```

Watch for: heartbeat success per 5s, first `_harvester_cycle` at hour boundary, first order ack with `success: true, status: live`. If anything looks off, `launchctl unload` immediately — none of the gates this runbook flipped require manual rollback (the unload alone stops order submission).

## To re-arm (or undo)

The script is idempotent. To **re-arm**: `bash scripts/arm_live_mode.sh`.

To **disarm** (emergency stop):

```bash
# Pause via control plane (preferred, leaves audit trail)
python -c "
import json
cp = json.load(open('state/control_plane.json'))
cp['commands'].append({
    'command': 'pause_entries',
    'issued_by': 'operator',
    'note': 'manual disarm 2026-05-XX'
})
json.dump(cp, open('state/control_plane.json','w'), indent=2)
"
# OR: hard kill via state file
echo '{"state":"BLOCKED","transitions":[{"from":"LIVE_ENABLED","to":"BLOCKED","by":"operator","at":"NOW","reason":"emergency disarm"}]}' \
  > state/cutover_guard.json
```

`BLOCKED` state blocks every action surface (entry/exit/cancel/redemption) immediately on next gate check.
