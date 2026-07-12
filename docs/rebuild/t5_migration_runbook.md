# T5 quarantine phase retirement — operator runbook

Authority: `docs/rebuild/quarantine_excision_2026-07-11.md` (T5 section, "Consult
adjudication" BLOCKER-2, conductor log "T5-CORE: LANDED 05a751290"). Script:
`scripts/migrations/2026_07_quarantine_phase_retirement.py`. Kill-point acceptance
tests: `tests/test_t5_quarantine_phase_retirement_migration.py`.

This is an **offline RED cutover**, not a routine online migration. It rewrites
`zeus_trades.db` (and the `position_lots` ghost shell on `zeus-world.db`) in one
crash-safe transaction on a dedicated, non-WAL connection. Read the whole runbook
before running anything.

## What this migration does

- `position_current.phase = 'quarantined'` rows are rewritten to the position's
  TRUE current phase (`active`, or `pending_exit` if its latest `position_events`
  row is an open, unresolved exit attempt) and an **OPEN**
  `review_work_items` row (`reason_code='LEGACY_QUARANTINE_MIGRATED'`) is minted
  for each one so the pre-migration dispute stays operator-visible.
- `position_current.chain_state` in `{quarantined, quarantine_expired,
  entry_authority_quarantined}` is rewritten to `'synced'`.
- `position_events.phase_before` / `phase_after` = `'quarantined'` (historical
  rows) are rewritten to `'active'`; `event_type='CHAIN_QUARANTINED'` is
  rewritten to `'REVIEW_REQUIRED'`.
- `position_lots.state = 'QUARANTINED'` (world ghost shell + trade authoritative
  copy; 0 live rows expected per the 2026-07-11 census — a defensive path) is
  rewritten to `'CONFIRMED_EXPOSURE'` with a loud `ERROR` log per row.
- The retired literal is dropped from every CHECK constraint on the tables
  above (SQLite cannot `ALTER` a `CHECK`, so each table is rebuilt: new table
  with the tightened CHECK, `INSERT...SELECT` with the value remap above, row-
  count parity check, drop old, rename into place).
- An identical `schema_epoch` value is stamped on all three DBs in the same
  transaction. `src.state.db.assert_schema_epoch_not_mixed` (wired into
  `src/main.py`'s boot path and `--validate-boot`) refuses to start the daemon
  if the three DBs ever disagree on this value.

**Out of scope** (found during this packet, deliberately not touched — see the
executor's final report for the full list): `token_suppression`(`_history`)`.
suppression_reason` (an ACTIVE ChainOnlyFact mechanism, T2/T8-B2 territory,
*not* a lifecycle-phase literal); `settlements`/`observations.authority`
(already migrated to `DISPUTED` by T2b); `decision_integrity_quarantine` (DIQ
packet); `market_topology_state` / `source_contract_audit_events` (already
clean — no quarantine literal found).

## Preconditions

1. **RED**: entries already paused is NOT enough. Every zeus daemon must be
   fully stopped — `entries_paused` only blocks NEW entries; monitor, exit,
   settlement, and reconcile writers keep writing through it. Confirm via:
   ```
   python scripts/deploy_live.py status
   ```
   every daemon should show no pid. If any is loaded:
   ```
   launchctl bootout gui/$(id -u)/com.zeus.<label>
   ```
   for every `com.zeus.*` label (live-trading, forecast-live, data-ingest,
   substrate-observer, price-channel-ingest, post-trade-capital, riskguard-live,
   venue-heartbeat, heartbeat-sensor).
2. **Disk space**: the backup step copies all three DB files (+ any residual
   `-wal`/`-shm`) into a fresh timestamped directory under
   `state/backups/`. Confirm free space >= 3x the current combined size of
   `state/zeus-world.db`, `state/zeus-forecasts.db`, `state/zeus_trades.db`.
3. **No other agent/session** has a connection open against any of the three
   DBs (the migration's own process-scan only catches known zeus daemon
   patterns, not ad hoc `sqlite3` shells or notebooks — check yourself).

## Command

```
python scripts/migrations/2026_07_quarantine_phase_retirement.py \
    --operator-confirms-fenced
```

Do **NOT** run this through `python -m scripts.migrations apply` — it refuses
immediately with a pointer back to this command (see the script's module
docstring for why: WAL is not crash-atomic across an ATTACHed 3-DB set).

Optional flags:
- `--backup-dir PATH` — write the synchronized 3-DB backup set somewhere other
  than `state/backups/`.
- `--skip-backup` — operator directive 2026-07-12: DB backups are waived
  (disk-space precondition #2 then does not apply). Crash safety rests on the
  single attached rollback-journal transaction (kill-point matrix proven:
  untouched-or-complete at every boundary); there is no restore set, so a
  mixed-epoch state after a crash is forward-fix-only.
- `--state-dir PATH` — point at a fixture directory instead of the live
  `STATE_DIR` (tests only; never use this against real data).

## Expected output

```
backup set: state/backups/t5_quarantine_migration_<UTC timestamp>
{
  "started_at": "...",
  "finished_at": "...",
  "chain_state_rows_updated": <N>,
  "review_work_items_opened": <N>,
  "position_lots_world_flagged": 0,
  "position_lots_trade_flagged": 0,
  "rebuilds": [
    {"table": "position_current", "schema": "trade", "rebuilt": true, ...},
    {"table": "position_events", "schema": "trade", "rebuilt": true, ...},
    {"table": "position_lots", "schema": "trade", "rebuilt": true, ...},
    {"table": "position_lots", "schema": "world", "rebuilt": true, ...}
  ]
}
```

`position_lots_*_flagged` should be `0` on the live DBs (0 live rows per the
2026-07-11 census). If either is nonzero, the printed `ERROR` log lines name
the exact `lot_id`/`position_id` — review those positions manually before
trusting the automatic `CONFIRMED_EXPOSURE` remap.

Re-running the command after a successful run prints:
```
T5 migration already applied (schema_epoch='t5_quarantine_phase_retirement_v1' on all three DBs) — no-op.
```
and exits 0 without touching anything (idempotent).

If the three DBs ever disagree on `schema_epoch` (a crash mid-run that the
rollback journal could not fully unwind, or a manual partial-restore), the
command refuses immediately:
```
REFUSED: mixed schema_epoch across the three DBs: {...}. ...
```
Exit code 1. Do not re-run blind — see Rollback below.

## Verification queries

After a successful run, on `state/zeus_trades.db`:

```sql
SELECT COUNT(*) FROM position_current WHERE phase = 'quarantined';                 -- 0
SELECT COUNT(*) FROM position_current WHERE chain_state IN
  ('quarantined','quarantine_expired','entry_authority_quarantined');              -- 0
SELECT COUNT(*) FROM position_events
  WHERE event_type = 'CHAIN_QUARANTINED'
     OR phase_before = 'quarantined' OR phase_after = 'quarantined';               -- 0
SELECT COUNT(*) FROM position_lots WHERE state = 'QUARANTINED';                    -- 0
SELECT epoch FROM schema_epoch;                                                     -- t5_quarantine_phase_retirement_v1
SELECT subject_id, reason_code, status FROM review_work_items
  WHERE reason_code = 'LEGACY_QUARANTINE_MIGRATED';                                -- one OPEN row per migrated position
```

On `state/zeus-world.db`:
```sql
SELECT COUNT(*) FROM position_lots WHERE state = 'QUARANTINED';                    -- 0
SELECT epoch FROM schema_epoch;                                                     -- t5_quarantine_phase_retirement_v1
```

On `state/zeus-forecasts.db`:
```sql
SELECT epoch FROM schema_epoch;                                                     -- t5_quarantine_phase_retirement_v1
```

Also confirm the CHECK constraint itself was rebuilt (not just the data):
```sql
SELECT sql FROM sqlite_master WHERE name IN ('position_current','position_events','position_lots');
```
should show no `'quarantined'` / `'QUARANTINED'` / `'CHAIN_QUARANTINED'` literal
in any CHECK clause.

## Rollback

**Law (BLOCKER-2): all three backups restored together, or forward-fix under
operator supervision — never restore one file alone, never run the old binary
against any file that has already received a target write.**

1. Confirm the writer plane is STILL fenced (no daemon running).
2. Locate the backup set printed by the run (`backup set: state/backups/t5_quarantine_migration_<timestamp>`), verify its `manifest.json` (all three
   `integrity_check: "ok"`).
3. Copy all three files (`zeus-world.db`, `zeus-forecasts.db`,
   `zeus_trades.db` — and any `-wal`/`-shm` sidecars present in the backup
   directory) back over `state/`, replacing the current files, TOGETHER, in
   one filesystem operation window (no daemon may start between step 3 and
   step 4).
4. Verify: `sqlite3 state/zeus_trades.db 'PRAGMA integrity_check;'` (repeat for
   the other two) and confirm `schema_epoch` is absent/NULL on all three
   again (pre-migration state restored).
5. Only then resume daemons (see Post-migration steps below) if choosing not
   to re-attempt the migration; otherwise re-run the command from a clean
   pre-migration state.

If the backup set is unusable (should not happen — its own integrity was
verified at capture time, and the run refuses if any file fails
`PRAGMA integrity_check`), forward-fix under direct operator supervision using
the verification queries above to locate and hand-repair the exact rows the
transaction did not converge on. Do not attempt this without first confirming
via `read_schema_epoch`/`assert_schema_epoch_not_mixed` (importable from
`src.state.db`) exactly which DBs did and did not receive the target epoch.

## Post-migration steps

1. Restart the daemon mesh the normal way — never a bare `launchctl
   kickstart`:
   ```
   python scripts/deploy_live.py restart all
   ```
   (per repo law: `deploy_live.py` gates on clean/pushed tree + live restart
   preflight; a bare kickstart can boot a concurrent agent's mid-edit tree
   into live money.)
2. Confirm `LEGACY_QUARANTINED_STATE_REMAPPED` /
   `LEGACY_QUARANTINED_CHAIN_STATE_REMAPPED` WARNING log lines
   (`src.state.portfolio._normalize_runtime_lifecycle_state` /
   `_normalize_runtime_chain_state`) no longer appear in fresh daemon logs —
   they only fire on a legacy row this migration has now eliminated.
3. Resume entries once the standard restart-preflight/resume gates pass
   (`scripts/check_live_restart_preflight.py`, then the normal
   `resume_entries` operator command) — this migration does not itself pause
   or resume entries.
4. Open the `review_work_items` rows minted by this migration
   (`reason_code='LEGACY_QUARANTINE_MIGRATED'`) for operator review; each
   names the position that was quarantined pre-migration and the phase it was
   remapped to.
5. **Follow-up packet** (not this one): once this migration has run against
   production and the verification queries above are all clean, the mixed-
   epoch load-time bridge in `src.state.portfolio`
   (`_normalize_runtime_lifecycle_state` / `_normalize_runtime_chain_state`)
   and the raw-SQL bridge sites in `portfolio.py:has_same_token_open_db`,
   `price_channel_ingest.py`, `chain_reconciliation.py:577`,
   `canonical_write.py:193-211` become dead code and may be retired — they
   exist ONLY to keep a pre-migration row from crashing Position construction.
