# Growth census analysis — Lane W10

Generated 2026-07-21 from `census_raw.jsonl` (799/799 objects measured, 0 timeouts,
matches sqlite_master counts exactly: trades 274, forecasts 163, world 362). No dbstat
was re-run by this lane; all byte/cell figures below are read from the external
driver's output. Growth-rate measurements use rowid-window sampling through the
mandated safe read pattern (SEARCH plans only, verified via EXPLAIN QUERY PLAN before
each query type — never SCAN).

`df -h /`: 926Gi total, 21Gi used at mount, **115Gi available** (checked at report
time; was 116Gi at lane start — normal drift, not attributable to this audit, which
made zero writes).

`-wal` file sizes at report time (all well under the 512MiB stop-line):
zeus_trades.db-wal 288MB, zeus-forecasts.db-wal 4.5MB, zeus-world.db-wal 23.6MB.

## (a) Coverage + per-DB byte attribution

Coverage: **100% — 799/799 objects measured, 0 timeouts.** No re-polling needed.

| db | measured total | table bytes | index bytes | index share |
|---|---|---|---|---|
| trades | 100.83 GB | 79.31 GB | 21.51 GB | 21.3% |
| forecasts | 42.87 GB | 20.65 GB | 22.22 GB | **51.8%** |
| world | 89.95 GB | 68.04 GB | 21.91 GB | 24.4% |
| **fleet** | **233.65 GB** | 168.00 GB | 65.64 GB | 28.1% |

Fleet-wide: unused (internal slack) = 27.73 GB (11.9% of measured bytes), payload =
201.16 GB. This is not a fleet-wide crisis by the consult's leaf-packing threshold —
slack is concentrated in a handful of the largest append-heavy tables, not spread
thin across many objects.

**forecasts.db is index-inverted**: index bytes (22.22GB) exceed table bytes
(20.65GB). This is driven almost entirely by one table — see calibration_pairs below.

Top-20 objects fleet-wide by raw bytes (table+index, not summed into families):

| object | type | GB | unused GB | mx_payload |
|---|---|---|---|---|
| trades.executable_market_snapshots | table | 46.29 | 5.55 | 6662 |
| world.opportunity_events | table | 32.21 | 4.65 | 167967 |
| trades.execution_feasibility_evidence | table | 20.43 | 3.23 | 5707 |
| forecasts.calibration_pairs | table | 12.84 | 0.58 | 289 |
| world.no_trade_regret_events | table | 12.06 | 0.24 | 68668 |
| world.execution_feasibility_evidence | table | 11.63 | 2.03 | 5484 |
| trades.decision_log | table | 8.16 | 0.08 | 1146549 |
| forecasts.sqlite_autoindex_calibration_pairs_1 | index | 6.69 | 1.02 | 142 |
| trades.idx_execution_feasibility_evidence_token_created | index | 4.56 | 1.46 | 119 |
| forecasts.ensemble_snapshots | table | 3.59 | 0.69 | 9554 |
| forecasts.forecast_posteriors | table | 3.54 | 0.03 | 111391 |
| world.decision_certificates | table | 3.36 | 0.74 | 118985 |
| trades.idx_execution_feasibility_evidence_token_time | index | 3.24 | 0.32 | 112 |
| forecasts.idx_calibration_pairs_refit_core | index | 2.90 | 0.01 | 71 |
| world.idx_opportunity_events_pending_order | index | 2.82 | 0.22 | 149 |
| forecasts.idx_calibration_pairs_group_lookup_lead | index | 2.74 | 0.02 | 66 |
| forecasts.idx_calibration_pairs_group_lookup | index | 2.65 | 0.02 | 57 |
| world.idx_opportunity_events_channel_token | index | 2.54 | 0.21 | 133 |
| world.opportunity_event_processing | table | 2.51 | 0.06 | 1254 |
| trades.sqlite_autoindex_execution_feasibility_evidence_1 | index | 2.45 | 0.31 | 81 |

Leaf-packing flags (consult thresholds: <65% actionable, 65–75% investigate if
unused>1GiB, applied to the top-20 by bytes):

- **trades.idx_execution_feasibility_evidence_token_created: 68.0% packing, 1.46GB
  unused — actionable per threshold**, and it sits on a table whose growth has
  frozen (see (c)) — a rebuild candidate once the parent table's write-path status
  is confirmed, not before.
- executable_market_snapshots (88.0%, 5.55GB unused), opportunity_events (85.6%,
  4.65GB unused), execution_feasibility_evidence/trades (84.2%, 3.23GB unused),
  execution_feasibility_evidence/world ghost (82.5%, 2.03GB unused): all pass the
  ">80% healthy for monotonically appended b-trees" bar individually, but the
  **absolute unused bytes are large** — none of this is reclaimable without VACUUM,
  which is banned live. Not actionable now; relevant only if/when an external-clone
  rebuild happens.

Overflow pressure (mx_payload > 4061, i.e. records that overflow on 4KiB pages):
executable_market_snapshots (6662), opportunity_events (167967 — huge, JSON payload
column), execution_feasibility_evidence both copies (5707/5484), no_trade_regret_events
(68668), decision_log (1146549 — enormous, `artifact_json` blobs), forecast_posteriors
(111391), decision_certificates (118985). These are all JSON-payload tables; expected
given schema shape, not itself a defect.

## (b) Identity checks

**world.db bulk is legitimately event/fact-stream payload, not misplaced market
metadata.** The top-3 world.db tables (opportunity_events 32.2GB, no_trade_regret_events
12.1GB, and the execution_feasibility_evidence ghost 11.6GB) are all EDLI event-log /
evidence-log tables, not market metadata bloat.

**Ghost-shell position tables are genuinely near-empty**, confirming the manifest's
"empty shell" description for the cutover-era ghosts:

| table | trades (canonical) | world (ghost) |
|---|---|---|
| position_events | 902.9MB / 397,975 rows | 0.004MB / 0 rows |
| position_current | 0.9MB / 1,311 rows | 0.004MB / 0 rows |
| position_lots | 0.25MB / 265 rows | 0.004MB / 0 rows |
| trade_decisions | 2.0MB / 3,601 rows | 0.004MB / 4 rows |
| execution_fact | 0.38MB / 856 rows | 0.004MB / 0 rows |
| venue_order_facts | 33.4MB / 43,976 rows | 0.004MB / 4 rows |
| venue_command_events | 8.8MB / 7,376 rows | 0.004MB / 0 rows |
| venue_trade_facts | 4.7MB / 2,135 rows | 0.004MB / 0 rows |
| settlement_commands | 0.08MB / 143 rows | 0.004MB / 0 rows |
| settlement_command_events | 0.43MB / 299 rows | 0.004MB / 0 rows |

All at the 4096-byte single-page floor on the ghost side. These are safe to drop per
the manifest's 2026-08-15 date and pose zero disk risk today.

**Two "ghost" tables are NOT empty shells and materially misdescribed in the
manifest — this is the headline finding of this lane:**

1. **`decision_log` on trades — manifest says "Ghost on zeus_trades.db from
   pre-PR-S4b init_schema(trade_conn). Drop after 2026-08-09" (schema_version_owner:
   null, i.e. explicitly not authoritative). Actual measured state: 8.16GB, ~115K
   live rows, and — per (c) below — the single fastest-growing table in the
   fleet at ~2.34GB/day, with a fresh row as of the census timestamp (2026-07-21T06:06,
   same batch as today's live trading activity). Meanwhile the "canonical" world.db
   copy of decision_log has 0 rows.** The manifest's canonical/ghost labeling for this
   table pair is inverted relative to where the data and the writes actually are.
   Scheduling this table for an unconditional drop on 2026-08-09 would delete the
   only populated copy of live decision-artifact history and would not stop future
   growth (nothing currently targets the "canonical" world-side table). This needs an
   operator decision before 2026-08-09, not an automatic drop.

2. **`execution_feasibility_evidence` on world.db — manifest says "Ghost on
   zeus-world.db from pre-trade-repoint schema drift... Live sidecars write
   zeus_trades.db." Actual measured state: 11.63GB, ~13M rows** (not small residual
   drift). Growth check (c) shows this copy's last row is dated 2026-06-18 — frozen
   for 33 days, consistent with "pre-repoint drift" that genuinely stopped after the
   repoint. So the *classification* (dead, no longer written) checks out; the
   *magnitude* claim ("ghost"/"residual drift") undersells that it is a frozen
   11.6GB+2.8GB-index dead-weight table, not a rounding error. Safe to drop for space
   once confirmed no reader depends on it (manifest already says none do), but it
   is a much bigger win than the manifest text implies.

**opportunity_events regrowth since the 2026-06-16 prune: confirmed, and unbounded
again.** `scripts/prune_terminal_opportunity_events.py` (created 2026-06-16) fixed an
acute incident: EDLI's `_edli_prune_pending_working_set` only ever marks rows
`expired`/`ignored`/`dead_letter` — it never physically deletes them, so
opportunity_events/opportunity_event_processing grow unbounded by design, and at the
2026-06-16 incident this had reached 7,050,590 terminal rows. The prune script is
explicitly documented (`src/state/db_writer_lock.py:679`) as a **standalone
one-time/maintenance sweep — not a daemon-path writer, and confirmed (d) not on any
cron/scheduler**. 35 days later, opportunity_events is at 32.2GB table + 12.1GB index
= 44.3GB total, growing at a measured ~0.13GB/day today (see (c)) — slower than the
burst that caused the original incident, but structurally identical: nothing is
deleting terminal rows, so this recurs indefinitely unless the prune becomes
scheduled or a real retention organ replaces the mark-only pattern.

## (c) Growth: rowid-window sampling, 9 largest table families

Method: for each table, `SELECT max(rowid)` (verified `SEARCH ... USING INTEGER
PRIMARY KEY`, never SCAN), then the timestamp column at `rowid=max` and at
`rowid>=max-10000` (both single-row `SEARCH` lookups). `rows/day` from the elapsed
time over that 10,000-row window; `bytes/row` = table-family bytes (table + all its
indexes, mapped via `sqlite_master.tbl_name`) / `max(rowid)`. Note: the census
`cells` field is unreliable as a row-count proxy on high-overflow tables (it exceeds
`max(rowid)` on both executable_market_snapshots and opportunity_events, which is
arithmetically impossible for a live row count on a plain rowid table — likely
double-counting overflow-chain structural cells in the driver's aggregate query).
All growth math below uses `max(rowid)`, never `cells`.

**Actively growing (6 of 9 sampled):**

| table | latest row (UTC) | rows/day | bytes/row | GB/day |
|---|---|---|---|---|
| trades.decision_log (mislabeled ghost) | 2026-07-21 06:06 | 32,960 | 70.9KB | **2.337** |
| world.no_trade_regret_events | 2026-07-21 06:06 | 51,285 | 17.5KB | 0.897 |
| trades.executable_market_snapshots | 2026-07-21 06:06 | 49,614 | 5.26KB | 0.261 |
| world.opportunity_events | 2026-07-21 06:06 | 51,877 | 2.48KB | 0.129 |
| world.decision_certificates | 2026-07-21 01:28 | 1,550 | 3.33KB | 0.005 |
| forecasts.ensemble_snapshots | 2026-07-20 20:26 | 1,537 | 3.22KB | 0.005 |

Sum of measured active growth: **3.63 GB/day (3.38 GiB/day)** across just these 6
tables, which are the fleet's dominant contributors by size.

**Frozen — zero measured growth despite the surrounding system being live (3 of 9
sampled):**

| table | latest row (UTC) | staleness | note |
|---|---|---|---|
| **forecasts.calibration_pairs** | 2026-05-31 03:07 | **51 days** | 34.26GB total (12.84GB table + 21.42GB index — the single most index-heavy object in the fleet, 1.67x). `src/execution/harvester.py:2459` still calls `add_calibration_pair`, and `forecasts.settlements` is confirmed fresh (settled_at up to 2026-07-21T03:00 today, target_date through 2026-07-19) — settlement is happening, so either the write path is gated off (`_emit_learning_write_blocked` early-return, or `p_raw_vector` empty before that point) or silently no-op. **This is outside DB-census scope to root-cause fully, but is evidence the calibration/Platt retraining loop may be training on data that stopped 51 days ago — flag for the calibration-boot-profile owner, independent of the disk question.** |
| trades.execution_feasibility_evidence (canonical) | 2026-07-17 09:41 | 4 days | Manifest describes this as the live order runtime's pre-submit book-evidence seam; a 4-day silence on a 24/7 trading system's evidence table is worth a second look, though it may simply track a lull in feasibility-check volume rather than a broken writer. |
| world.execution_feasibility_evidence (ghost) | 2026-06-18 23:35 | 33 days | Consistent with the manifest's "stopped after the trade-repoint" story — this one checks out as inert, see (b). |

**ENOSPC ETA** (free = 115GiB at report time; growth excludes the 3 frozen tables,
which contribute 0 by measurement, and covers only the 6 sampled tables — a lower
bound on true fleet-wide growth since dozens of smaller tables are not sampled):

| scenario | rate | days to ENOSPC |
|---|---|---|
| current measured rate (6 tables, 3.38 GiB/day) | 3.38 GiB/day | **~34 days** |
| 2x current rate | 6.77 GiB/day | ~17 days |
| current rate **minus** the mislabeled decision_log ghost (1.21 GiB/day) | 1.21 GiB/day | ~95 days |
| 2x of that | 2.41 GiB/day | ~48 days |

The single highest-leverage lever in this dataset: **trades.decision_log alone is
~62% of the measured daily growth (2.34 of 3.63 GB/day).** It's the fastest-growing
table in the fleet, has the largest mx_payload (1,146,549 bytes — huge JSON
artifacts), and is currently mislabeled `schema_version_owner: null` /
"drop after 2026-08-09" in the ownership manifest despite being live and populated.
Resolving what actually owns this table (confirm it's genuinely wanted, or find and
fix the writer if it's an accidental duplicate of `decision_events`) roughly triples
the runway before the growth-driven ENOSPC scenario above.

## (d) Retention landscape

`rg -l "DELETE FROM|def prune|retention" scripts/*.py` plus manual read of each hit
confirms: **`scripts/prune_terminal_opportunity_events.py` is the only physical-delete
retention script for a big append-heavy table, and it is not scheduled** —
`src/state/db_writer_lock.py:679` documents it explicitly as a "standalone
one-time/maintenance retention sweep... runs OUTSIDE the daemon," and no
crontab/launchd/cron-skill reference to it was found. Confirmed (b): opportunity_events
has regrown since the 2026-06-16 one-off run and nothing will prune it again without
another manual invocation.

Two other one-off scripts exist and are explicitly one-time, not retention organs:
- `scripts/drop_world_ghost_tables.py` — operator-invoked, dry-run by default, drops
  the K1-split-era world.db ghost copies (observations, settlements, settlements_v2,
  source_run, market_events_v2, ensemble_snapshots, calibration_pairs_v2). Not the
  same ghosts flagged in (b) above (different table set, an earlier cutover).
- `scripts/task_2026-06-09_drop_dead_tables.py` — one-off, `delete_by: 2026-07-01`
  (already past), drops 21 audited-dead tables across the 3 DBs, optional
  `--vacuum-world`. Superseded/expired per its own delete_by date; not a standing
  retention mechanism either.

`src/state/append_only_supersession.py` (`archive_row_before_overwrite`) does **not**
imply any physical delete — confirmed by reading the full ~80-line file. It is purely
additive: before a caller's `ON CONFLICT DO UPDATE`, it INSERTs a full JSON snapshot
of the pre-image row into a `<table>_supersessions` sibling table. This is a growth
contributor (every logical update now also writes a permanent archive row), not a
retention mechanism — the opposite direction from what the question's phrasing might
suggest.

**Net retention landscape: there is no scheduled/automatic physical retention
anywhere in this fleet for any of the tables measured in (a)/(c).** The
mark-not-delete pattern that caused the 2026-06-16 incident on opportunity_events is
architectural (EDLI's `_edli_prune_pending_working_set` only sets
`processing_status`), and the only remediation that exists is a manual script an
operator has to remember to re-run.

## Summary for the operator

- Coverage: 100% (799/799), no re-measurement needed.
- Fleet total measured: 233.65GB (100.83 trades + 42.87 forecasts + 89.95 world),
  matching on-disk file sizes (93.94 + 39.93 + 83.79 GiB) within rounding.
- Free space: 115GiB. At the measured growth rate from just the 6 actively-growing
  sampled tables (3.38 GiB/day), **ENOSPC in ~34 days**; at 2x, ~17 days.
- The single biggest lever: `trades.decision_log` (8.16GB, growing ~2.34GB/day,
  mislabeled as a droppable ghost in the ownership manifest while being the fleet's
  fastest-growing live table) — resolving its true ownership status is worth doing
  before its scheduled 2026-08-09 drop date, and would nearly triple the runway
  (34 days → ~95 days) if stopped/redirected.
- Second lever: opportunity_events retention is unscheduled since the 2026-06-16
  one-off prune; it will re-accumulate terminal rows indefinitely without a recurring
  job.
- Independent of disk: `forecasts.calibration_pairs` has received no new row in 51
  days despite settlements continuing today — flagged for the calibration-loop owner,
  not resolved here.
