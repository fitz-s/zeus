# `_held_to_binary_settlement_quality` per-statement timings — 2026-09-13

Hop: `scripts/evaluate_current_regime_capital_advantage.py::_held_to_binary_settlement_quality`
(~line 2327 before this change). **106.4 s of a 140.7 s incremental
`evaluate()`** in today's cProfile, measured after the three landed hop fixes
(`aaea688b0`, `ccb93b1b5`, `163f1f236`, `53af70848`, `ff3f69cd0`, `f4eef07d8`).

Measured read-only against the live `state/zeus_trades.db` (199 GB, 15 GB WAL
pinned by a foreign reader) and `state/zeus-forecasts.db` through
`sqlite3.connect('file:...?mode=ro', uri=True)`, every statement under a
`threading.Timer(N, conn.interrupt)` watchdog. `WINDOW_DAYS = 35.0`,
`GLOBAL_HOLD_RECEIPT_SCAN_ROWS = 5_000`, `as_of = datetime.now(timezone.utc)`.

## Frontier mechanism this hop did NOT use

`ccb93b1b5` added `scan_floor_decision_log_id`, a prior-run `decision_log`
frontier, but it is threaded only into `_settled_global_counterfactual_evidence`.
This hop walked a **fixed** tail on every cycle:
`id >= MAX(id) - GLOBAL_HOLD_RECEIPT_SCAN_ROWS`, recomputed from the live
`MAX(id)` each run, with no prior input at all. Its signature took no `prior`.

## Statements

The whole hop is **one statement**. Instrumented per-statement (proxy
connection wrapping `execute`, whole hop under a 600 s watchdog):

| # | statement | calls | wall time | rows | status |
| --- | --- | --- | --- | --- | --- |
| **S1** | **`decision_log` receipt-tail scan with three `json_extract` tests** | **1** | **52.517 s** | **76** | OK |
| S2 | `position_current` by `position_id` | 3 | 0.000 s | 3 | OK |
| S3 | `SELECT COALESCE(MAX(id),0) FROM decision_log` | 1 | 0.000 s | 1 | OK |
| S4 | `position_events` `EXIT_ORDER_FILLED` probe | 0 | — | — | never reached (all 3 positions unsettled) |
| S5 | `_verified_settlement` / `_condition_resolved_yes` on forecasts | 0 | — | — | never reached |

Whole hop: **52.681 s**, of which S1 is 52.517 s (99.7 %). Output was 490 bytes
(`graded=0`, `awaiting=3`) — 52.5 s of work for a 490-byte answer.

S2 and S4 scale with graded positions, not window size, and the per-position
`position_events` probe already rides
`idx_position_events_position_type_sequence`. They were never the cost and are
left untouched.

### Why S1 dominates

```sql
SELECT id,mode,artifact_json FROM decision_log
 WHERE id>=? AND timestamp>=?
   AND mode IN (?,?,?)
   AND json_extract(artifact_json,'$.summary.schema_version')=22
   AND json_extract(artifact_json,'$.summary.global_selection_revision')=?
   AND json_extract(artifact_json,'$.summary.holding_auction_coverage_zlib_b64')
       IS NOT NULL
 ORDER BY id
```

Plan: `SEARCH decision_log USING INDEX idx_decision_log_mode (mode=? AND rowid>?)`
+ `USE TEMP B-TREE FOR ORDER BY`.

`decision_log` carries exactly two indexes, neither composite:

```
idx_decision_log_ts     (timestamp)
idx_decision_log_mode   (mode)
```

So `mode`, `timestamp` and `id` are the only index-usable terms, and **4,062
rows** pass all three. The three `json_extract` calls then run per surviving
row, each parsing that row's full `artifact_json`. The 76 rows the scan returns
carry 47,561,554 bytes between them — **625 KB mean per row**. At that size the
4,062-row band holds roughly **2.5 GB of `artifact_json`**, and every byte is
read off the 199 GB DB's overflow pages to return 76 rows.

Index-usable terms are free; the blob read is the whole cost:

| branch | wall time | rows | plan |
| --- | --- | --- | --- |
| `SELECT id … mode IN (?,?,?) AND id>=?` | 0.004 s | 4,062 | `SEARCH … USING COVERING INDEX idx_decision_log_mode` |
| `SELECT id … timestamp>=? AND id>=?` | 0.185 s | 5,001 | `SEARCH … USING COVERING INDEX idx_decision_log_ts` |
| `SELECT COUNT(*) … mode+timestamp+id` | 26.941 s | — | (reads `artifact_json` pages) |
| shipped S1 (adds the three `json_extract`) | 52.5–56.4 s | 76 | as above |

### Rewrite attempt 1 — SQL `INTERSECT` of the two covering-index id sets (rejected)

```sql
SELECT id FROM decision_log WHERE mode IN (?,?,?) AND id>=?
INTERSECT
SELECT id FROM decision_log WHERE timestamp>=? AND id>=?
```

**35.938 s** for 4,062 ids, against 0.004 s + 0.185 s for the same two branches
run separately. The right arm loses its covering index
(`SEARCH decision_log USING INTEGER PRIMARY KEY (rowid>?)`), so it walks the row
bodies it was meant to avoid. Rejected: an id-set intersection only helps when
both arms stay index-only, and this one does not. Narrowing the id set would not
have helped anyway — 4,062 of the band's rows genuinely satisfy the indexed
terms; the `json_extract` blob read is the cost, not the id arithmetic.

## Root cause

The hop re-read every one of the ~4,000 receipt-tail rows' `artifact_json`
every 5-minute cycle to discover the same ~77 rows that carry a schema-22
holding-coverage blob. Which rows those are is a fact about rows, and it was
being rediscovered from 2.5 GB of JSON on every run.

## Fix — per-hop receipt scan frontier (applied)

The artifact now carries, per run: the frontier `MAX(id)`, the `mode` and
`timestamp` of the row sitting at it, and the ids of the rows that satisfied the
predicate at or below it.

The next run re-reads the row at the recorded frontier id; a matching
`(mode, timestamp)` proves the ids at or below it still name the same rows.
`decision_log` is append-only under `id INTEGER PRIMARY KEY AUTOINCREMENT`: its
only writer is `src/state/decision_chain.py`, which `INSERT`s and
retention-`DELETE`s, and no statement anywhere in `src/` or `scripts/` rewrites
`artifact_json`, `mode` or `timestamp` (`UPDATE decision_log` appears only in
`tests/`). `sqlite_sequence` confirms the counter is live, so a deleted id is
never reissued.

Given a verified frontier the run issues two statements instead of one:

1. the named ids re-read with the **identical** predicate, and
2. an extension walk over `id > frontier`, same predicate.

Both substitute `_HOLD_RECEIPT_ROW_PREDICATE`, one named string, so the filter
is provably the same text as the full walk's. All three branches also bound
`id <= MAX(id)` so the recorded frontier is honest about what was scanned.

| step | wall time | rows |
| --- | --- | --- |
| frontier identity lookup (`id=MAX(id)`) | 0.000 s | 1 |
| full shipped walk | 56.419 s | 76 |
| **named re-read of the 76 ids, same predicate** | **1.821 s** | **76** |
| extension walk `id > MAX(id)-100` | 0.011 s | 2 |
| extension walk `id > MAX(id)-300` | 0.056 s | 5 |

Row set from the named re-read is identical to the full walk's
(`row set identical: True`).

### Why naming the survivors is complete

Both bounds of the shipped window are monotone non-decreasing in `as_of`: the id
floor is `MAX(id) - 5000` and rises as rows are appended, and the `timestamp`
cutoff is `as_of - WINDOW_DAYS`. A row can therefore only ever **leave** the
scanned set, never re-enter it, so no unscanned row below the frontier can
become eligible on a later run. Re-applying the full predicate to each named id
is what makes departures exact: a row that retention deleted, or that the
advancing cutoff pushed out of window, simply fails the predicate and drops out
of the resumed run exactly as it drops out of a full one.

The frontier names **rows, never grades**. Every named row's `artifact_json` is
re-read, its coverage blob re-decompressed, re-hashed against
`holding_auction_coverage_sha256`, re-checked by
`assert_global_auction_summary_integrity`, and re-graded against
`position_current` and the verified settlement on every run. A rewritten
coverage blob at a named id flips the answer on a resumed run
(`test_hold_receipt_scan_regrades_a_rewritten_coverage_blob`).

A missing prior, a malformed entry, a candidate id above its own frontier, or a
different `(mode, timestamp)` at the frontier drops the run back to a full walk.

## Byte-equality on the live DBs

One read-only connection per DB, one pinned WAL snapshot (`BEGIN` … never
committed), one `as_of`, `_canonical_json_bytes` over the whole hop output:

| run | wall time | candidates | bytes |
| --- | --- | --- | --- |
| full walk (`prior=None`) | 57.592 s | 77 | 1,202 |
| incremental (`prior=` the full walk's output) | 2.031 s | 77 | 1,202 |
| incremental again (`prior=` the incremental output) | 1.512 s | 77 | 1,202 |

`BYTE_EQUALITY: identical = true`. `BYTE_EQUALITY_2ND_HOP: identical = true` —
resuming from a resumed artifact reproduces the full walk exactly, so the
frontier does not decay across chained runs.

Cross-`as_of`, which is the actual daemon path (the prior artifact is always
from an earlier cycle):

| run | wall time | candidates | bytes |
| --- | --- | --- | --- |
| full walk at `as_of = T-6h` | 65.434 s | 79 | 1,237 |
| incremental at `as_of = T` (prior from `T-6h`) | 2.655 s | 79 | 1,237 |
| full walk at `as_of = T` | 63.876 s | 79 | 1,237 |

`CROSS_AS_OF_BYTE_EQUALITY: identical = true`.

**24–28x on this hop** (57.6 s → 2.0 s same-`as_of`; 63.9 s → 2.7 s across a
6-hour `as_of` gap), against 106.4 s of a 140.7 s incremental `evaluate()` in
today's cProfile.

## What was not measured

- A cold-cache run. The live `-wal` is 15 GB and pinned by a foreign reader, so
  every number here is on whatever page cache the running system had warm and
  the absolute values are lower bounds on cold cost. The full-versus-incremental
  ratio is measured inside one snapshot, so it is unaffected.
- The graded path (S4, S5) at scale. All four in-window HOLD positions were
  `awaiting` (not yet `phase='settled'`) at measurement time, so the
  `position_events` exit probe and the two forecasts-DB settlement lookups never
  executed on live data. They are per-graded-position, ride existing indexes,
  and are untouched by this change.
- A cProfile of the whole `evaluate()` after this change. Only the hop was
  re-timed; the 140.7 s figure above is today's pre-change profile.
