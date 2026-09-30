# Observation-index deployment and rollback — operator only

**Not executed against production.** This runbook deliberately admits only a flat maintenance window. Do not stop a healthy trading process while it owns open positions or nonterminal commands. A live-capital repair requires the existing guarded restart/handoff mechanism instead; this document does not waive it. All commands below are `verify locally` at deployment, except the copied-table benchmark described at the end.

## 1. Release and entry pause

Have the separately authorized release owner integrate the reviewed feature into `origin/live` and fast-forward the live checkout. This consult does not push or merge `live`. Do not run the feature worktree as a production daemon.

```bash
cd /Users/leofitz/zeus
PY=/Users/leofitz/zeus/.venv/bin/python
$PY scripts/deploy_live.py status
git diff --quiet && git diff --cached --quiet
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/live)"
$PY scripts/check_schema_fingerprint.py
```

Read and retain the current pause generation first. Preserve any pre-existing operator pause; do not replace it just to make deployment resumable. When there is no existing pause, the operator may create the migration pause through the existing control-plane writer:

```bash
$PY - <<'PY'
from datetime import datetime, timezone
from src.control.control_plane import pause_entries, _active_entries_pause_row
from src.state.db import get_world_connection_read_only
c=get_world_connection_read_only()
try: old=_active_entries_pause_row(c,now_iso=datetime.now(timezone.utc).isoformat())
finally: c.close()
if old is not None:
    print('PRESERVE_EXISTING_PAUSE',dict(old))
else:
    pause_entries('operator:observation_index_migration',issued_by='operator')
c=get_world_connection_read_only()
try:
    row=_active_entries_pause_row(c,now_iso=datetime.now(timezone.utc).isoformat())
    assert row is not None, 'Pause did not durably persist'
    print('SELECTED_PAUSE_GENERATION',dict(row))
finally: c.close()
PY
```

Acceptance: durable entries-paused state, no new entries, ordinary monitoring still running. Pausing entries does not cancel resting orders. Do not force-close positions or declare cancellation from a request alone.

## 2. Require flatness, then unload writers

The exact canonical classifier used by the guarded deployment tool must find zero obligations immediately before shutdown:

```bash
$PY - <<'PY'
from pathlib import Path
from scripts.deploy_live import _canonical_live_restart_obligations
r=_canonical_live_restart_obligations(Path('/Users/leofitz/zeus/state/zeus_trades.db'))
print(r)
assert r['open_position_count']==0 and r['nonterminal_command_count']==0, 'STOP: live obligations require continuous monitoring'
PY
```

Abort on any open obligation, unreadable truth, or new pause generation. The following labels and launchd working directories were read from the operator's actual plists on September 30, 2026. Unload trading first; then its producers/sidecars. No `kill -9`, `--allow-dirty`, or `--allow-unpushed` bypass.

```bash
DOMAIN="gui/$(id -u)"
for name in live-trading forecast-live data-ingest substrate-observer price-channel-ingest post-trade-capital venue-heartbeat riskguard-live heartbeat-sensor; do
  label="com.zeus.$name"
  if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$label" || exit 1
  fi
  if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
    echo "STOP: still loaded $label"; exit 1
  fi
done
lsof /Users/leofitz/zeus/state/zeus-world.db /Users/leofitz/zeus/state/zeus-world.db-wal 2>/dev/null
```

Inspect remaining holders, including manual workers and maintenance jobs. Do not kill another task. The exclusive cutover lease below refuses while any sanctioned connection remains open; an SQLite write transaction additionally detects raw unsanctioned writers. Unknown holders mean abort/restart the old reviewed code through the guarded path, not force the migration.

## 3. Fresh copied-table backup and exact migration

Make a new audit copy using `benchmark_observation_index.py` with a never-used destination. It reads production through `mode=ro`, copies every `observation_prints` row and benchmarks the migration on the copy. It does not copy unrelated ~100GB WORLD tables. Keep the resulting source/copy hashes and file as the pre-migration evidence backup. Do not overwrite an earlier copy.

```bash
$PY artifacts/fast_obs_audit/benchmark_observation_index.py \
  --source /Users/leofitz/zeus/state/zeus-world.db \
  --destination "/Users/leofitz/zeus/artifacts/fast_obs_audit/deploy-$(date -u +%Y%m%dT%H%M%SZ)/observations.sqlite"
```

The production migration is index-only and one transaction. Under the existing exclusive cutover-lease protocol, compare full row-content hashes before/after. The timeout is an upper bound that aborts the statement, not a guarantee of completion. Never remove the lease file.

```bash
$PY - <<'PY'
import fcntl, os, sqlite3, time
from pathlib import Path
from artifacts.fast_obs_audit.benchmark_observation_index import digest
from src.state.db_writer_lock import cutover_lease_path
from src.state.schema.observation_prints_schema import ensure_table
path=Path('/Users/leofitz/zeus/state/zeus-world.db')
fd=os.open(str(cutover_lease_path(path)),os.O_RDWR|os.O_CREAT,0o644)
c=None
try:
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)  # Refuse any remaining runtime connection.
    c=sqlite3.connect(str(path),timeout=2)
    c.execute('PRAGMA synchronous=FULL')
    before=digest(c)
    c.execute('BEGIN IMMEDIATE')
    deadline=time.monotonic()+30
    c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    try:
        ensure_table(c)
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally: c.set_progress_handler(None,0)
    columns=[r[2] for r in c.execute('PRAGMA index_info(ux_observation_prints_identity)')]
    assert columns==['city','station_id','source_channel','publish_ts_utc','value_native','fetched_at_utc']
    after=digest(c)
    assert after==before,(before,after)
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print('MIGRATED_WITHOUT_ROW_CHANGE',before,columns)
finally:
    if c is not None:c.close()
    fcntl.flock(fd,fcntl.LOCK_UN)
    os.close(fd)
PY
```

Acceptance: exact six-column index, unchanged count and SHA-256, integrity OK, no live table updates/deletes. A schema/timeout error is not success. If the transaction failed, verify the old index remains before proceeding.

## 4. Restart order and acceptance

Start data production, then forecast consumption, then books/capital/risk/heartbeat, and trading last. Use the existing tool rather than direct bootstrap; live-trading's command also revalidates/reloads prerequisites and enforces its own preflight, code-identity, collateral and monitor-progress checks.

```bash
$PY scripts/deploy_live.py restart data-ingest
$PY scripts/deploy_live.py restart forecast-live
$PY scripts/deploy_live.py restart substrate-observer
$PY scripts/deploy_live.py restart price-channel-ingest
$PY scripts/deploy_live.py restart post-trade-capital
$PY scripts/deploy_live.py restart riskguard-live
$PY scripts/deploy_live.py restart heartbeat-sensor
# live-trading restart owns the venue-heartbeat sidecar under its restart lock.
# Do not start that watchdog independently before the guarded trading boot.
$PY scripts/deploy_live.py restart live-trading
$PY scripts/deploy_live.py status
```

Stop the sequence at the first failed gate; do not assume bootstrap equals readiness. Acceptance: all loaded SHAs equal the approved live SHA; current WORLD observations preserve native units/station; consumed FORECAST revision catches up after restart; q-ready wakes reach readers; no lease/schema errors; review retries advance; canonical command acknowledgments have correct q lineage. Production first-availability and auction/network percentiles remain measurements to make after authorized deployment, not numbers established by this runbook.

Do not automatically resume entries. Only the operator may resume the exact migration-owned pause generation through `resume_entries(reason, issued_by='operator', expected_override_issued_at=..., expected_override_reason='operator:observation_index_migration', expected_override_issued_by='operator')` after all acceptance checks. A different or pre-existing pause must remain untouched.

## 5. Rollback

Code rollback is a separately reviewed revert/forward-fix release on `origin/live`, followed by the same guarded restart process. Do not reset the live checkout, erase observation rows, or restore the copied table over newer observations.

**Retain the six-column index and adjacent-revision append rule on rollback.** Once A→B→A corrections have been recorded, rebuilding the old five-column unique index may fail or tempt destructive deduplication. A rollback build must carry this schema compatibility fix. The old read shape remains compatible; old append semantics are not sufficient for new reversions. If index migration failed before commit, SQLite rollback preserves the old index; verify it, then leave the previous reviewed code running with entries paused until the owner resolves the failure.

## Executed nonproduction measurement

`round3_migration_measurement.json`: all 485,931 rows from read-only WORLD copied to a 147,955,712-byte table-only database. Copy: 4463.669 ms. Index migration+commit: 782.067 ms. Idempotent repeat: 0.058 ms. SQLite 3.53.2, WAL, synchronous FULL. Before/after SHA-256 `cbec366910c19f65fbbb29770b841eb17001d748daa13cad354e248392111e06`; integrity OK. A→B→A→A inserts `[true,true,true,false]` and that synthetic transaction was rolled back. This is not production-contention or power-loss testing.
