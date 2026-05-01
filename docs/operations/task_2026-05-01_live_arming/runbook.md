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

## User-channel WS env vars needed (currently missing)

**Symptom (every cycle, today)**: `ws_user_channel.gap_reason='not_configured'`. The daemon never starts the authenticated user-channel WebSocket because `PolymarketUserChannelIngestor.from_env(...)` is gated on `ZEUS_USER_CHANNEL_WS_ENABLED=1` and that flag is absent from `~/Library/LaunchAgents/com.zeus.live-trading.plist`.

**Inventory of env vars consumed by the user-channel boot path** (`src/main.py::_start_user_channel_ingestor_if_enabled` + `src/ingest/polymarket_user_channel.py::WSAuth.from_env`):

| # | Var | Purpose | Currently in plist? |
|---|---|---|---|
| 1 | `ZEUS_USER_CHANNEL_WS_ENABLED` | Master toggle (`1`/`true`/`yes`/`on`). When absent, the boot path skips quietly and the WS guard reports `not_configured`. | **Missing** |
| 2 | `POLYMARKET_USER_WS_CONDITION_IDS` | Comma-separated list of condition IDs to subscribe to. Empty triggers `condition_ids_missing` gap (intentional fail-closed). | **Missing** |
| 3 | `POLYMARKET_API_KEY` | L2 API key for user-channel auth (`WSAuth.from_env`). | Present |
| 4 | `POLYMARKET_API_SECRET` | L2 API secret. | Present |
| 5 | `POLYMARKET_API_PASSPHRASE` | L2 API passphrase. | Present |

**Action required before the daemon can leave `reduce_only=True`**: add `ZEUS_USER_CHANNEL_WS_ENABLED=1` and `POLYMARKET_USER_WS_CONDITION_IDS="<comma-separated condition ids>"` to the live-trading plist under `EnvironmentVariables`, then `launchctl unload && launchctl load` the plist. The boot warning now lists every missing var on each cold start so operators don't have to re-derive this list.

```bash
# After flipping the plist:
/usr/libexec/PlistBuddy -c \
  "Add :EnvironmentVariables:ZEUS_USER_CHANNEL_WS_ENABLED string 1" \
  ~/Library/LaunchAgents/com.zeus.live-trading.plist
/usr/libexec/PlistBuddy -c \
  "Add :EnvironmentVariables:POLYMARKET_USER_WS_CONDITION_IDS string '<id1>,<id2>,...'" \
  ~/Library/LaunchAgents/com.zeus.live-trading.plist
```

This is documentation-only — Fix 2 in this session adds the boot-time WARNING log line that names the missing vars; the actual plist edit is operator territory.

## Auto-pause hardening (2026-05-01)

Auto-pause used to write `effective_until=NULL`, which is permanent. A single transient `ValueError` would lock entries forever until the operator manually expired the override. Fix shipped 2026-05-01:

- Auto-pause overrides now default to `now + 15min` expiry. If the issue is transient, entries auto-resume after 15 min; if it persists, the next failure re-pauses (and the streak counter re-arms).
- A streak counter at `state/auto_pause_streak.json` requires 3 consecutive same-reason failures within a 5-min window before `pause_entries` is called. Single-cycle hiccups log a WARNING but do not pause.
- `pause_entries(...)` is idempotent on the same active reason — duplicate inserts within the same active window are skipped.
- Operator helper `scripts/expire_auto_pause.sh` clears the override + tombstone + streak file in one shot, without re-running the full `arm_live_mode.sh` flow.

## User-channel WS auto-derive (post-2026-05-01)

The hardcoded `POLYMARKET_USER_WS_CONDITION_IDS` plist value is a structural failure mode (operator directive 2026-05-01: "任何硬编码bankroll都是一次严重的结构性失误" — same shape applies to hardcoded condition_id lists; markets rotate daily and the plist drifts from on-chain truth). The fix is to derive the subscription set from the canonical market scanner so the daemon subscribes to exactly the markets it can trade.

### Three env vars

| # | Var | Purpose | Set by `arm_live_mode.sh`? |
|---|---|---|---|
| 1 | `ZEUS_USER_CHANNEL_WS_ENABLED` | Master toggle. Required for any user-channel WS path. Without it, boot logs `user-channel WS not configured: missing env vars [...]` and stays `reduce_only=True`. | Yes — `=1` in both `com.zeus.live-trading.plist` and `com.zeus.data-ingest.plist`. |
| 2 | `ZEUS_USER_CHANNEL_WS_AUTO_DERIVE` | Auto-derive switch. When `=1` AND `POLYMARKET_USER_WS_CONDITION_IDS` is empty, the daemon calls `src.data.market_scanner.find_weather_markets()` at boot and subscribes to the executable condition_ids it returns. | Yes — `=1` in both plists. |
| 3 | `POLYMARKET_USER_WS_CONDITION_IDS` | Operator override. When non-empty, the value (comma-separated condition IDs) wins over auto-derive — used for pinning a static list during testing or surgical recovery. | No — left empty so auto-derive owns the list. Operator can `PlistBuddy Add` it for a hand-picked override. |

### Auto-derive vs operator-pinned

- **Auto-derive (default after `arm_live_mode.sh`)**: subscription set is a snapshot taken at daemon boot. Markets opening AFTER boot won't be subscribed until the next daemon restart. A future packet may add a periodic re-subscription; out of scope here.
- **Operator-pinned**: set `POLYMARKET_USER_WS_CONDITION_IDS="<id1>,<id2>,..."` in the plist. The boot path detects the non-empty value and skips the scanner call entirely; auto-derive becomes a no-op.

### Operator action

Just run `bash scripts/arm_live_mode.sh`. The script now sets all three toggles needed (`ZEUS_HARVESTER_LIVE_ENABLED`, `ZEUS_USER_CHANNEL_WS_ENABLED`, `ZEUS_USER_CHANNEL_WS_AUTO_DERIVE`) for both daemon plists. Idempotent like the rest of the flow.

### Boot output: expected lines

When auto-derive succeeds:

```
INFO  user-channel WS auto-derive yielded N condition_ids (POLYMARKET_USER_WS_CONDITION_IDS empty, ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1)
INFO  M3 user-channel ingestor started for N condition_ids (auto_derived=True)
```

When auto-derive yields 0 markets (gamma down, all weather events filtered, etc.):

```
WARN  user-channel WS auto-derive yielded 0 condition_ids; daemon stays in reduce_only=True mode. Markets may be empty or the gamma query failed; check src.data.market_scanner.
```

The empty case does NOT crash the daemon — the WS guard records `condition_ids_missing` and live submits stay fail-closed until the next boot picks up real markets.
