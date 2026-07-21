# Lane W13 — Operational Posture (backup/restore, APFS, integrity, cross-DB orphans, timestamps)

Read-only investigation per SAFETY LAW v2. WAL sizes recorded before/after; no STOP triggered.

WAL at time of this lane's DB reads: `zeus_trades.db-wal` 241,345,512 B (241 MB),
`zeus-world.db-wal` 11,770,872 B (11.8 MB), `zeus-forecasts.db-wal` 2,204,232 B
(2.2 MB). None exceed the 512 MiB threshold.

## Ranked findings (money-path / correctness risk first)

### F1 [P0] No tested off-machine backup exists for any of the 3 live canonical DBs
`tmutil destinationinfo` shows one configured Time Machine destination,
**"帅哥的SDXC"** (local disk, ID `38FD1147-...`). `tmutil latestbackup` /
`tmutil status` returns **`Failed to mount destination.`** (`Error Domain=
com.apple.backupd.ErrorDomain Code=18`) — the only off-machine backup path
Zeus's host is configured for is currently non-functional. The only backups
that exist are two **local, same-disk, purgeable** APFS snapshots
(`com.apple.TimeMachine.2026-07-19-125620.local`,
`com.apple.TimeMachine.2026-07-20-125550.local`, both `Purgeable: Yes` per
`diskutil apfs listSnapshots /System/Volumes/Data`) — these protect against
accidental file deletion for ~1-2 days, not against disk/controller failure,
and macOS is free to purge them under space pressure (disk is at 87%).

Repo search (`rg` over `*.py`/`*.sh` for `.backup(`, `VACUUM INTO`,
`shutil.copy*` near `.db`, `sqlite3.backup`) found **no scheduled/automatic
backup mechanism** for `state/*.db`. The only code path that creates a raw
copy of a live canonical DB is `backup_world_db()` in
`scripts/source_contract_auto_convert.py:1531-1550`:

```python
def backup_world_db(db_path: Path, *, evidence_root: Path) -> dict[str, Any]:
    ...
    if os.environ.get("ZEUS_ALLOW_LIVE_DB_BACKUP") != "1":
        raise RuntimeError(
            "refusing to create live DB backup without ZEUS_ALLOW_LIVE_DB_BACKUP=1"
        )
    ...
    backup_path = evidence_root / f"{db_path.name}.backup"
    shutil.copy2(db_path, backup_path)
```

This is a **raw single-file `shutil.copy2`** with no `-wal`/`-shm` handling
at all — on a WAL-mode DB this omits every committed-but-not-yet-checkpointed
transaction sitting in the `-wal` file, i.e. exactly the "backup that omits
WAL = silently loses committed transactions" failure mode. It is (a) gated
off by default via `ZEUS_ALLOW_LIVE_DB_BACKUP=1`, and (b) invoked only from
one manual operational script (`source_contract_auto_convert.py:1877`), not
from any scheduled job — it is not a backup system, it is a one-off
convenience copy for a specific migration flow, and even that copy would be
non-restorable-clean if a checkpoint hadn't just run.

`state/backups/` (the directory name most likely to hold DB backups)
contains only 3 unrelated small artifacts from 2026-05-26/27 (an
`ensemble_snapshots_v2` `.sql`/`.json` pair and a ledger `.md`) — no DB
snapshots.

`~/Library/LaunchAgents` has one backup-named agent, `ai.openclaw.backup.git.plist`
— it backs up a **git** repo, unrelated to `state/*.db`.

**Verdict: today, a disk failure or corruption event loses all 218 GB of
Zeus's canonical state with no recovery path.** The nearest thing to a
safety net (local TM snapshots) is same-disk and already at risk of
auto-purge under the current 87%-full condition.

### F2 [P1] settlement_outcomes (forecasts) is missing rows for chain-confirmed-settled positions (trades)
Bounded, indexed probe (EQP-verified `SCAN position_current` — table is
1,311 rows total, safe for full read; forecasts side uses
`idx_settlement_outcomes_city_date_metric`): of 257 `position_current` rows
with `settled_at IS NOT NULL OR settlement_price IS NOT NULL`, **16 have no
matching row** in `zeus-forecasts.db.settlement_outcomes` on
`(city, target_date, temperature_metric)`. Sample, all `chain_state='synced'`
(on-chain confirmed) and `phase='settled'`, `exit_reason='SETTLEMENT'`:

| position_id | city | target_date | metric | settled_at |
|---|---|---|---|---|
| 20d1b043-254 | Tokyo | 2026-07-20 | high | 2026-07-20T17:36:43Z |
| 32be639c-c22 | Ankara | 2026-07-20 | high | 2026-07-21T00:41:42Z |
| 384f1dd8-5c1 | Hong Kong | 2026-07-13 | high | 2026-07-15T00:20:24Z |
| 3983413f-a62 | Hong Kong | 2026-07-13 | high | 2026-07-15T00:20:24Z |
| 83ede0f8-d31 | Paris | 2026-07-02 | low | 2026-07-15T00:20:24Z |

Verified with direct lookups (`SELECT ... FROM settlement_outcomes WHERE
city=? AND target_date=?`) for all 5 sampled — each returns **zero rows**.
This is not a same-day pipeline lag: the Paris 2026-07-02 and Hong Kong
2026-07-13 gaps are 18 and 8 days old respectively as of 2026-07-21 and
still unfilled. `settlement_outcomes` is documented in
`architecture/db_table_ownership.yaml:159-167` as "Canonical settlement
truth table" — money has settled on-chain (`chain_state='synced'`) for
these 16 positions with no corresponding truth-table record in the DB that
is supposed to own that truth. HYPOTHESIS on cause not investigated further
here (out of scope for a read-only ops lane): possibly a settlement path
that writes `position_current` directly without a corresponding
`settlement_outcomes` insert for certain exit routes.

### F3 [P1] world.market_topology_state has been silently dead since 2026-05-28 while flagged status='CURRENT'
All 3,938 rows report `status='CURRENT'`, but `MIN(recorded_at)` /
`MAX(recorded_at)` across the whole table is **2026-05-19 to 2026-05-28** —
no row has been written or touched in ~8 weeks, while `position_current`
(trades) carries `target_date` values through 2026-07-22. This table looks
"live" by its own status column but has been orphaned by whatever writer
used to maintain it. This directly confounds the condition_id cross-DB
orphan probe below (F3 is the root cause of F4's headline number) and is
itself the more important finding: a fail-soft ingest silent-orphan pattern
(matches the documented Zeus failure class) on a table other live code may
still be querying as if current.

### F4 [P2, confounded by F3 — do not read as raw corruption] condition_id cross-DB orphan rate
Bounded probe (`position_current` 1,311 rows, `SCAN position_current`, safe
full read; `market_topology_state` 3,938 rows, `SCAN ... USING COVERING
INDEX`, safe full read): of 550 distinct non-null `condition_id` values in
`trades.position_current`, **533 (97%) have no matching row** in
`world.market_topology_state`. Given F3 (topology_state frozen since
2026-05-28), this reads as expected staleness rather than active
referential corruption — `position_current` retains positions opened after
the table stopped being written. Flagging as P2 rather than P0 because the
root cause is F3, but the pairing means **no current code path can rely on
`market_topology_state` to resolve a `condition_id` for anything opened
after late May** without silently missing 97% of cases.

### F4b [informational, verified] world.db ghost shells for position_id (`position_current`/`position_events`/`position_lots`) are not stale — they are **completely empty (0 rows)**
Third named cross-DB key ("position ids trades<->world ghost shells").
`architecture/db_table_ownership.yaml:561-608` declares `position_current`,
`position_events`, `position_lots` on `world.db` as `LEGACY_ARCHIVED` ghost
shells ("Authoritative copy on trade.db", drop date 2026-08-15). Direct
probe: `SELECT max(rowid) FROM position_current` (and same for
`position_events`, `position_lots`) on `zeus-world.db` all return **`NULL`**
— zero rows in all three tables — confirmed with a `LIMIT 3` sample on each
(empty result set). `trades.position_current` itself has **1,089** distinct
`position_id` rows (`EQP: SCAN position_current USING COVERING INDEX
sqlite_autoindex_position_current_1`, full read of a table this small is
safe), all of which are trivially "orphaned" from the world-side ghost
copies simply because those copies hold nothing at all, not because of any
active referential drift. This is a cleaner, stronger fact than an orphan
percentage would be: **no writer has ever populated these post-K1-split
ghost tables** (or they were fully truncated at cutover) — there is zero
risk of a reader silently picking up stale trade-position data from the
world-side copies, unlike the F3/F5 pattern where the ghost/legacy table
does hold old data that a stray reader could mistake for current. Note the
row count here (1,089) differs from the 1,311 figure the condition_id probe
(F4) used ~55 minutes earlier in this same lane — both were direct
full-table reads at different points in a live 24/7-trading table, so this
is ordinary churn (new positions opening), not a measurement error; flagged
here only for internal consistency, not as an anomaly.

### F5 [P2] trades.market_price_history is also dead since ~2026-05-20/28, same signature as F3, still referenced by 7 live modules
`world.market_price_history` is **empty** (0 rows). `trades.market_price_history`
has 622,649 rows (`max(rowid)`), but the last-100-by-rowid sample (i.e. the
**newest** rows in the table) all carry `recorded_at` timestamps from
**2026-05-20 to 2026-05-28** — the highest-rowid row is ~8 weeks stale, not
today's data. This is the same May-28 cutover date as F3 and is very likely
the same underlying writer retirement/migration event. Despite being dead,
`market_price_history` is still referenced (`rg -l`) in 7 live `src/`
modules: `state/db.py`, `state/domains.py`, `state/schema/v2_schema.py`,
`state/book_hash_transitions.py`, `observability/price_evidence_report.py`,
`analysis/market_analysis_vnext.py`, `backtest/economics.py`. A likely
successor table exists on `trades.db`: `token_price_log` (217,102 cells per
census, actively sized) — HYPOTHESIS, not confirmed here, that
`market_price_history` was superseded by `token_price_log` and any reader
still consulting the old table is silently reading frozen May data. Worth a
dedicated dead-table-lane (W8) follow-up rather than further read-only
probing here.

### F6 [P2] No `integrity_check`/`quick_check` has ever been run against any of the 3 live canonical DBs
`rg -n 'integrity_check|quick_check'` across the repo finds the mechanism
used only in: (1) tests (`tests/test_promote_calibration.py`,
`tests/test_replacement_forecast_live_schema.py`, etc.) against
test-fixture DBs; (2) three `scripts/migrations/2026_07_*.py` migration
scripts, each of which runs `PRAGMA integrity_check` **only on the small
copied artifact it just wrote** (`_integrity_check(dst)` after
`shutil.copy2(src, dst)` for the specific tables the migration touched),
never on the full 94/84/40 GB production files; (3)
`scripts/promote_model_bias_ens.py:168` and
`scripts/task_2026-06-09_drop_dead_tables.py:159`, both narrow-scope
promote/maintenance tools. **No code path, scheduled job, or LaunchAgent
ever runs `integrity_check` against the actual live `zeus_trades.db` /
`zeus-world.db` / `zeus-forecasts.db` files.** Given SAFETY LAW v2's own
ban on running it against these files interactively (too expensive/risky
live), this is a real gap with no easy fix under current constraints —
noted for FINDINGS.md, not actionable from this read-only lane.

### F7 [informational] Startup schema coherence check exists, but is epoch-equality only, not integrity
`src/main.py:615-639` (and mirrored in `_validate_boot`) calls
`assert_schema_epoch_not_mixed(world_epoch, forecasts_epoch, trade_epoch)`
via `src/state/db.py:1744` — this guards against **a partially-applied
cross-DB migration** (the three DBs disagreeing on `schema_epoch`), and
`assert_db_matches_registry` (`main.py:608`) checks the **table-name set**
of the trade DB against `architecture/db_table_ownership.yaml`. Neither
this nor `_migrations_applied` verifies that the live schema's actual DDL
(column types, indexes) matches what the registry/epoch claims — it is a
set-membership and epoch-equality check, not a schema-content check.

### F8 [benign, verified] venue_commands "orphan" positions are all rejected entries — not corruption
Indexed probe (`SCAN vc USING COVERING INDEX idx_venue_commands_position`,
`SEARCH pc USING COVERING INDEX ... LEFT-JOIN`): of 1,221 `venue_commands`
rows, 61 (5%) reference a `position_id` absent from `position_current`. All
10 sampled (most recent by `created_at`, spanning 2026-06-29 to 2026-07-19)
have `state IN ('REJECTED','SUBMIT_REJECTED','EXPIRED')` and
`intent_kind='ENTRY'` — these are entry attempts that never became a
position (rejected pre-fill), so `position_current` correctly has no row
for them. This is by-design behavior, not a referential-integrity defect.

### F9 [informational] APFS space / CoW snapshot interaction
`df -h /System/Volumes/Data`: 926 GiB total, 87% used, **117 GB avail**.
`diskutil apfs list`: the underlying container `disk3` reports **125.6 GB
"Capacity Not Allocated"** (container-wide free pool shared across System,
Data, VM, Preboot, Recovery volumes — Data's 117 GB is a subset/view of
this, not an independent budget). `diskutil apfs listSnapshots
/System/Volumes/Data` shows the two local Time Machine snapshots from F1,
**both flagged `NOTE: This snapshot limits the minimum size of APFS
Container disk3`** — i.e., any blocks freed today by a DB shrink (prune
script, eventual VACUUM) will **not** show up as reclaimed free space until
those 1-2-day-old snapshots are purged or expire. This confirms the
consult's P1 #19 concern (`df` alone lies under CoW) concretely: at current
posture, the practical reclaim lag for any space-recovery operation is
bounded by local-snapshot retention, not by "delete the bytes and they're
free."

### F10 [clean] No timestamp anomalies in the 5 sampled hot tables
Rowid/PK-window sampling (last 100 rows by primary key, `EQP`-verified
indexed seek in every case — `SEARCH ... USING INTEGER PRIMARY KEY` /
`rowid`, no full scans) against current UTC `2026-07-21T05:11:24+00:00`:

| Table (db) | Timestamp col | Sample min | Sample max | Future | Epoch-0 |
|---|---|---|---|---|---|
| `decision_log` (trades) | `timestamp` | 2026-07-21T04:50:10Z | 2026-07-21T05:08:46Z | 0 | 0 |
| `position_events` (trades) | `occurred_at` | 2026-07-21T05:02:53Z | 2026-07-21T05:09:15Z | 0 | 0 |
| `venue_commands` (trades) | `created_at` | 2026-07-17T09:44:26Z | 2026-07-21T01:28:06Z | 0 | 0 |
| `settlement_outcomes` (forecasts) | `settled_at`/`recorded_at` | 2026-07-18T02:00:00Z | 2026-07-21T03:00:03Z | 0 | 0 |
| `market_price_history` (trades) | `recorded_at` | 2026-05-20T23:42:30Z | 2026-05-28T05:55:44Z | 0 | n/a — see F5, this *is* the anomaly (staleness, not format) |

No future-dated rows, no epoch-0 rows, no local-vs-UTC format drift (every
value carries an explicit `+00:00` ISO offset) in any of the 5 tables'
live/recent data. The one anomaly present (`market_price_history`'s
newest-row staleness) is captured as F5, not a timestamp-format defect.

## Evidence appendix — exact commands run

- `tmutil listlocalsnapshots /`, `tmutil status`, `tmutil destinationinfo`,
  `tmutil destinationinfo -X`, `tmutil isexcluded state`, `tmutil latestbackup`
- `diskutil apfs list`, `diskutil apfs listSnapshots /`,
  `diskutil apfs listSnapshots /System/Volumes/Data`, `diskutil info /System/Volumes/Data`
- `df -h /`, `df -h /System/Volumes/Data`
- `rg` sweeps: `.backup(|VACUUM INTO|vacuum_into|sqlite3\.backup|shutil\.copy.*\.db|rsync.*\.db|tar.*state/.*\.db`;
  `state/[a-zA-Z_.-]*\.db` filtered to cp/rsync/tar/backup/copy/snapshot;
  `shutil\.copy2?|\.db\.bak|\.db\.backup`; `integrity_check|quick_check`;
  `schema_epoch|_migrations_applied`
- `ls -la ~/Library/LaunchAgents/`, `find ~/Library/LaunchAgents -iname '*zeus*' -o -iname '*backup*'`
- `ls -la state/backups/`
- Safe-pattern venv-Python read-only SQLite queries (per SAFETY LAW v2 §2),
  one bounded query per connection, `EXPLAIN QUERY PLAN` run before every
  row-data query, against `zeus_trades.db`, `zeus-world.db`,
  `zeus-forecasts.db` — exact SQL embedded inline above/in each finding.
- F4b follow-up probe (added after initial lane draft, to close the
  explicitly-named "position ids trades<->world ghost shells" cross-DB key):
  `SELECT max(rowid) FROM position_current` / `position_events` /
  `position_lots` against `zeus-world.db` (all `NULL`, EQP not needed — empty
  table, and each followed by a `LIMIT 3` sample confirming zero rows), and
  `SELECT position_id FROM position_current` full read against
  `zeus_trades.db` (`EQP: SCAN ... USING COVERING INDEX
  sqlite_autoindex_position_current_1`, 1,089 rows). WAL re-checked
  immediately after: `zeus_trades.db-wal` 288,173,432 B, `zeus-world.db-wal`
  23,607,632 B, `zeus-forecasts.db-wal` 362,592 B — all still far under the
  512 MiB threshold.

## Scope not covered (explicitly out of bounds for this lane)

Root-caused *why* F2/F3/F5 are stale (which writer stopped, which commit
retired it) — that is a code-history investigation, not an ops-posture
read. Flagging for W8 (dead tables) / W12 (synthesis) follow-up.
