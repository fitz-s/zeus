# The two remaining storage levers, and what each is actually blocked on (2026-09-19)

Both of the big remaining reclaims are now measured rather than estimated, and each is
blocked by one specific, nameable condition — not by "needs more work".

## 1. `zeus_trades.db`: 63.4 GB dead space, blocked by two live positions

| | |
|---|---|
| file | 199.5 GB |
| freelist | **63.4 GB (31.8% of the file)** |
| `auto_vacuum` | 0 |
| growth | **0 MB/min** — writes reuse freed pages rather than extending the file |

The reclaim path exists and is well-built: `scripts/ops/vacuum_reset_trades_db.py`, three
operator-run phases (`--check` / `--vacuum-into` / `--swap`). Its read-only `--check`
refuses today, correctly:

```
REFUSED: 2 open position(s) exist and entries_paused is not set.
```

Those two are genuinely live — Hong Kong `pending_exit` and Shanghai `day0_window`, both
on 2026-09-19 markets, $25.85 at risk. This is a trading-state gate protecting capital,
not a stale guard, and it clears on its own when those settle.

**The part worth knowing:** two live code paths already call
`PRAGMA incremental_vacuum(1000)` after their retention deletes —
`src/state/snapshot_repo.py:228` and
`src/events/triggers/market_channel_ingestor.py:4085` — and both are **no-ops today
solely because `auto_vacuum=0`**. Their own comments say so. So the VACUUM reset is not
merely a one-time 63 GB reclaim: it switches on self-maintenance that is already written,
already wired, and currently inert. This is exactly the family_books shape, where pairing
the VACUUM with `auto_vacuum=INCREMENTAL` is what stopped the 29 GB from rebuilding.

Space is the second constraint: `VACUUM INTO` needs ~136 GB of output against 66 GiB free.
It does not fit today and needs either external storage or a post-settlement window when
the positions have cleared and more has been reclaimed elsewhere.

## 2. `calibration_pairs`: 65% of every row is repeated low-cardinality TEXT

Measured over 100,000 rows sampled across five rowid strata (head/25%/50%/75%/98%), not
one contiguous block:

| column | bytes/row | distinct (100k sample) |
|---|---|---|
| `dataset_id` | **34.0** | **3** |
| `decision_group_id` | 40.0 | 491 |
| `bin_source` | 12.0 | 1 |
| `source_id` | 10.0 | 1 |
| `city` / `observation_field` / `cluster` | 9.0 each | 1-4 |
| `authority` | 8.0 | 1 |
| ... 16 such columns | **156 of 241 B/row (65%)** | |

48,157,324 rows, so **~7.5 GB is repeated identifier text**. `dataset_id` alone stores a
34-byte string 48 million times to express one of three values.

`zeus-forecasts.db` is 99.4 GB with a **freelist of 65 pages (0.0%)** — there is no dead
space to reclaim there at all. Encoding is the only lever on that file, which is what
makes this measurement worth acting on.

**The constraint a migration must respect:** `idx_calibration_pairs_refit_core` indexes
`(temperature_metric, dataset_id, training_allowed, authority)`, so the index duplicates
the same wide strings again. A dictionary-FK migration has to rewrite that index too, and
every reader of those columns, or it will silently change refit selection. That is the
work, and it is not a one-line change.

## 3. Pre-submit latency: the first instrumented pass refuted its own premise

`stages=` is live and has produced **n=887** real samples:

```
p50=354.0ms  p90=1003.0ms  p99=1989.0ms  max=9659.0ms

book_native_side_receipt       19.10 ms p50    295.50 p99
encode_candidate_evaluations   14.40           84.40
delta_candidate_evaluations    12.40          156.70
delta_book_native_side         10.70           70.90
delta_audit_context             1.90           26.80
encode_audit_context            1.70           11.60
delta_holding_coverage          0.10            0.50
encode_holding_coverage         0.00            0.40
encode_minimum_repair           0.00            0.60
---------------------------------------------------
sum of stage medians           60.30 ms   of a 354 ms p50
UNATTRIBUTED                  293.70 ms   (83%)
```

The earlier decomposition predicted the component builders held the residual. **They do
not** — all nine together are 17% of the p50. `_store_global_auction_receipt` runs to
~1,200 lines and every wrapper sat in its first 420; the two
`persist(CycleArtifact(...))` calls that serialize and write the receipt, and the
`global_auction_artifact_summary_hash` calls that hash it whole, were outside all of them.
Now wrapped on both the compact and full paths, with the wiring antibody extended and
mutation-verified. The next mesh restart produces the attribution.

Note the p50 also moved: 516 ms in the earlier probe vs 354 ms here, so any before/after
claim on this path has to come from the same instrumented window, not across them.

## Not a leak: free space vs `du`

Free space on the data volume falls and recovers on its own while the three main
databases are static (0 MB/min each, verified over 60-second windows). `zeus_trades.db-wal`
sawtooths to ~310 MB and collapses back on checkpoint, and a `du` taken mid-checkpoint
reads ~20 GB high. Measure a store's reclaim with `du` on the store; a volume with
concurrent writers cannot answer that question.

## 4. Correction: `forecast_posteriors` is the lever, not `calibration_pairs`

The `dbstat` pass finished after §2 was written and reorders the priorities. Objects in
`zeus-forecasts.db` (99.4 GB, freelist 0.0%):

```
57.10 GB  forecast_posteriors            <- over half the database
12.84 GB  calibration_pairs
 6.69 GB  sqlite_autoindex_calibration_pairs_1
 4.96 GB  ensemble_snapshots
 2.89 GB  idx_calibration_pairs_refit_core
 2.73 GB  idx_calibration_pairs_group_lookup_lead
 2.65 GB  idx_calibration_pairs_group_lookup
 2.44 GB  idx_calibration_pairs_decision_group
 1.61 GB  idx_calibration_pairs_city_date_metric
 1.36 GB  idx_calibration_pairs_bucket
```

Two corrections to §2:

- **`forecast_posteriors` at 57.1 GB is the largest object by far**, and §2 did not
  mention it. Its 604,164 rows average 93,275 B, of which `provenance_json` is
  **89,203 B — 95.6%**, so one column is ~54 GB, over half the file.
- **`calibration_pairs`'s seven indexes total 20.4 GB against a 12.8 GB table.** The
  dictionary-FK idea in §2 was sized against the table alone; the indexes are the larger
  half, and `sqlite_autoindex_calibration_pairs_1` (6.69 GB, the `pair_id` PK) is
  untouched by any column-encoding change.

### What is inside the 54 GB

```
q_bootstrap_samples_by_bin          66.2%
day0_causal_evidence_bundle         10.6%
day0_remaining_vector_witness        8.7%
bayes_precision_fusion               7.2%
bin_topology                         6.1%
```

The same field that dominated `.total_loss`'s evidence snapshots.

### Why plain compression is refused, and what works instead

`provenance_json` is read by `json_extract` **inside SQL** — `center_debias_live_fit.py`
lines 132 and 144-145 filter and project on `$.anchor_value_c` and `$.q_shape`. SQLite
cannot `json_extract` a compressed blob, so compressing the column would break the live
center-debias fit. (The `json_extract(NEW.provenance_json, ...)` trigger in
`src/state/db.py:4002` is on `settlements`, not this table, so it is not an additional
constraint here.)

The separable part is the bulk field. `q_bootstrap_samples_by_bin` has **zero**
`json_extract` sites and exactly one reader,
`src/data/day0_hourly_vectors.py:1164`, which reads it in Python via
`provenance.get(...)` to rebuild the carrier sample matrix. It is a real consumer, so the
field must stay readable — this is a split, not a delete.

Measured over 500 real rows:

| | MB (500 rows) | share |
|---|---|---|
| `provenance_json` today | 44.45 | 100% |
| JSON kept, still SQL-visible | 9.88 | 22.2% |
| `q_bootstrap_samples_by_bin` split out | 34.55 | 77.7% |
| that bulk as a zstd-19+dict BLOB | 12.75 | 2.71x |
| **total after split** | **22.64** | **1.96x** |

**57.1 GB becomes ~29.1 GB: a 28 GB reclaim**, with `json_extract` still working on
everything SQL actually queries. That is roughly four times the `calibration_pairs`
table-column lever, and it needs no index rewrite.

Note the compression ratio is far lower here than the 8.01x measured on `.total_loss`:
these rows are large and individually varied, so a trained dictionary adds little
(zstd-19 alone is 3.25x, with the dictionary 3.34x). Sized from the measurement, not from
the earlier store's ratio.

### Revised order

1. **`forecast_posteriors` provenance split** — 28 GB, no index work, one Python reader
   to reroute, needs a dual-read decoder and a backfill.
2. **`zeus_trades.db` VACUUM reset** — 63.4 GB, blocked on two live positions and on
   ~136 GB of output space.
3. **`calibration_pairs` dictionary-FK** — ~7.5 GB in the table, but the 20.4 GB of
   indexes dominate and several index the very columns being narrowed.

## 5. Where the reclaimed space went: a TimeMachine local snapshot

§3's "not a leak" note was right that the databases are static and that `du` beats `df`
for measuring a store. It did not explain why free space kept falling anyway. It is a
local APFS snapshot:

```
com.apple.TimeMachine.2026-09-18-121115.local
```

Taken at **12:11 local today**, it pins every block freed since then — including the
~10 GB evicted from `.total_loss` at 14:44 and 18:0x. That is why `du` shows the store at
10 GB while the volume never gave the space back:

| | |
|---|---|
| free at the start of this work | ~80 GiB |
| free now | 66 GiB |
| main DBs growth (60 s windows, each) | **0 MB/min** |
| `~/Library/Caches` growth (100 s window) | **0 MB** |
| free-space decline over the same window | ~17 MB / 100 s |
| `zeus/` growth vs free-space loss (90 s) | **70 MB grown vs 428 MB lost** |

Six-sevenths of the decline is outside the repo and matches no growing file, which is the
signature of blocks held by a snapshot rather than blocks in use.

The destination is an SDXC card (`帅哥的SDXC`) and `/Volumes` shows only `Macintosh HD`,
so the drive is **not attached**: the snapshot cannot be flushed to a backup and thins
only on macOS's own schedule or when space pressure forces it.

**Consequence for §1**: the `zeus_trades.db` VACUUM reset needs ~136 GB of output. Some of
the headroom it is waiting on is already reclaimed and merely pinned. Deleting the
snapshot (`sudo tmutil deletelocalsnapshots 2026-09-18-121115`) is an operator decision —
it discards a restore point taken before today's evictions, which is exactly why it is
worth a deliberate choice rather than a reflex.

**Method note that outlives this incident**: when files are static and free space still
falls, check `tmutil listlocalsnapshots` before hunting for a writer. A snapshot is
invisible to `du`, so the two tools disagree by design, and every "which file is growing"
probe will come back empty — as several did here.

### Applied: 95 GiB returned, and no sudo was needed

`sudo tmutil deletelocalsnapshots` cannot run through a `!`-prefixed shell (no TTY for the
password prompt). `tmutil thinlocalsnapshots`, which needs no elevation, does the same job
here:

```
tmutil thinlocalsnapshots /System/Volumes/Data 21474836480 4
Thinned local snapshots:
com.apple.TimeMachine.2026-09-18-121115.local
```

| | before | after |
|---|---|---|
| free | 65 GiB | **160 GiB** |
| used | 93% | **82%** |
| local snapshots | 1 | **0** |

The snapshot was holding **95 GiB** — far more than the ~10 GB evicted today, so it had
been accumulating freed blocks well before this session. The estimate in §5 was the
floor, not the total.

**This unblocks §1's space constraint.** `VACUUM INTO` needs ~136 GB of output and there
are now 160 GiB free (172.0 GB container free). The `--check` still refuses, now on the
one remaining precondition:

```
REFUSED: 2 open position(s) exist and entries_paused is not set.
```

Hong Kong `pending_exit` and Shanghai `day0_window`, both on 2026-09-19 markets, $25.85
at risk. That gate protects capital and clears on settlement; it is not something to
force. When it clears, the 63.4 GB reclaim and — more durably — the two dormant
`incremental_vacuum` call sites both become available.

Worth keeping in mind for the future: thinning is reversible only in the sense that a new
snapshot can be taken. It discards restore points, so it stays an operator action even
though it needs no password.

## 6. Applied: the live bundle lookup is 200x faster, for 0.23 GB

The §4 plan was to split `provenance_json` for 28 GB. Profiling first found a better
first move: the blob's cost is not only disk, it is **read latency on the decision path**.

`_replacement_bundle_identity_is_live` filtered `forecast_posteriors` on
`json_extract(provenance_json, '$.day0_causal_evidence_bundle.bundle_identity')`. At
93,275 B/row — about 23 pages of overflow chain — every candidate row was faulted in
whole to read one identifier:

| | |
|---|---|
| live, `json_extract` on the blob | **1.427 ms/call** |
| same query, blob untouched | 0.014 ms/call |
| reaching small JSON keys across the table | **34.2 s** (vs 0.014 s without the blob) |

The fix is a VIRTUAL generated column over that one key, plus a covering index. VIRTUAL
stores no bytes, so the largest table in the database gains only the index, and SQLite
derives the value itself, so it cannot drift from the JSON it mirrors.

**Applied to the live database** (290.5 s):

```
before: bundle_identity present = False
after : column=True index=True
verify: rows disagreeing with json_extract (last 5000) = 0
LIVE new path: 0.007 ms/call
plan: SEARCH forecast_posteriors USING INDEX idx_forecast_posteriors_bundle_identity
```

`zeus-forecasts.db` 99.43 -> 99.66 GB: **0.23 GB for a 200x lookup speedup**. Equivalence
was proven before the change too, on a copy of 8,000 real rows: identical results for all
40 distinct bundle identities, 6.041 ms/call -> 0.014 ms/call.

### The ordering hazard, and why the DB went first

The rerouted query sits inside `except sqlite3.Error: return False`. A daemon loading the
new code against a database without the column would not crash — it would read **every
bundle as not-live** and silently stop admitting entries. So the column was applied to the
live database *before* any restart can pick up the new code. The running daemons are 12 h
old and still hold the pre-change reader, so the live path was never exposed.

`ensure_forecast_runtime_indexes` (called at `substrate_observer_daemon` boot) converges
this for any other database, and `_ensure_forecast_indexes` carries it for the
ATTACH-from-world.db branch.

### What this changes about §4

The 28 GB split is still worth doing, but it is no longer the *latency* argument — this
change already took the hot lookup off the blob. The split remains a storage argument, and
a second latency argument only for whatever still reads the whole column. Worth measuring
each remaining reader the same way before assuming it needs the split at all: the cheapest
fix for a blob you only need one key from is to index that key, not to move the blob.

## 7. The same fix, applied to the calibration fit

Sweeping the remaining `json_extract`-on-`provenance_json` readers found one far worse
than the bundle lookup. `center_debias_live_fit._RESIDUAL_SQL` filters on `$.q_shape` and
projects `$.anchor_value_c`, over many rows rather than one:

```
300 rows cold: 108.534 s = 361.8 ms/row
```

That is the same class of cost `src/ingest_main.py` documents behind its 600 s bound —
an un-deduped fitter join that json_extracted a ~93 KB blob per row and took 546.37 s.
Two more VIRTUAL generated columns and a partial index:

| | |
|---|---|
| before, cold | **361.8 ms/row** |
| after, full 605k-row table | **0.012 s total**, 310,899 rows matched |
| `zeus-forecasts.db` | 99.66 -> 100.32 GB |

`anchor_value_c` takes REAL affinity, which converts the JSON string form and leaves an
absent key NULL rather than 0.0; the consumer already calls `float()`, so both shapes read
the same.

Not every reader needed this. `replacement_forecast_bundle_reader:1723` filters on three
blob keys but costs 0.287 ms/call, because other predicates narrow the candidate set
first, and `day0_extreme_updated`'s `json_extract`es are on observation rows, not this
table. Measured rather than assumed.

### Two defects this surfaced, both of them the real value

**The fixture had diverged from production.** `tests/calibration/test_center_debias_live_fit.py`
built `forecast_posteriors` with its own DDL, so when the shipped SQL moved to the columns
the fixture kept passing while production would have raised `no such column`. The fixture
now mirrors them, and a new antibody binds the two permanently: every bare `p.<col>` the
shipped SQL reads must exist on a real `init_schema_forecasts` table.

**The migration was not idempotent.** `_table_columns` uses `PRAGMA table_info`, which
omits VIRTUAL generated columns entirely, so on an already-migrated database every ALTER
re-fired and died with `duplicate column name: bundle_identity`. It uses `table_xinfo`
now. This was found by *running* the migration against the live database — a fresh
in-memory schema only ever calls the helper once, so no test could have caught it.

That same PRAGMA blind spot had already produced a false negative earlier in this session,
when a test reported the column absent from a table that carried it.

### Running total on this table

| query | before | after |
|---|---|---|
| live bundle lookup | 1.427 ms/call | **0.007 ms/call** |
| center-debias filter | 361.8 ms/row | **0.012 s / 605k rows** |
| cost | | **+0.89 GB total** |

The §4 split (28 GB) remains available and is now purely a storage argument: both hot
readers are off the blob.

## 8. The pre-submit residual is closed: it is `persist`, and it is not the write

The instrumentation from §3 reached production. Across **n=143** live receipts carrying
the new stages:

```
p50=605.0ms  p90=981.0ms  p99=1587.0ms

persist_full                  229.1 ms p50   37.9%
persist_compact               127.7          21.1%
book_native_side_receipt       62.9          10.4%
encode_candidate_evaluations   56.7           9.4%
delta_candidate_evaluations    50.4           8.3%
delta_book_native_side         28.8           4.8%
delta_audit_context            18.6           3.1%
encode_audit_context            8.5           1.4%
summary_hash_compact            0.8           0.1%
(4 more below 0.2 ms)
------------------------------------------------
sum of medians                583.7 ms  of a 605.0 ms p50
UNATTRIBUTED                   21.3 ms  (4%)
```

**Unattributed fell from 83% to 4%**, and the answer is the artifact write: `persist` is
59% of pre-submit time. That is the opposite of the earlier prediction that the component
builders held it, and the reason §3 says to instrument the whole function rather than the
part you suspect.

### But it is not the writing

Measured against the shapes involved:

| | |
|---|---|
| artifact being written | **1,025.7 KB** (96% of it zlib+base64 payloads) |
| `json.dumps` of the whole artifact | 1.7 ms |
| clean 1 MB INSERT + commit, WAL + synchronous=FULL | **3.0 ms** |
| the 50-row × 1 MB retention DELETE + commit | **9.3 ms** |
| same 1 MB INSERT against a 90k-page freelist | 2.0 ms |

None of these is 229 ms, and the freelist — the obvious suspect, given `zeus_trades.db`
carries 63.4 GB of it — changes nothing. What `persist` does *before* any of it is acquire
a write lease, which makes lock wait the candidate the numbers point at, with the trading
mesh writing concurrently from eight other daemons.

So the lease acquisition is now timed as `<owner>:lease_wait`. The clock brackets only the
acquisition and control flow is byte-identical: re-entering the context manager by hand to
wrap it in `_receipt_stage` would have changed exception handling on the money path, which
is not a trade worth making for a measurement. `_record_receipt_stage` reports an
already-measured span into the same structure and is a silent no-op outside a collection
window.

### A second finding worth its own work

The artifact is 1,025.7 KB of which **983.9 KB is base64 text of already-zlib'd payloads**:

```
book_native_side_delta_zlib_b64      498.1 KB   48.6%
candidate_evaluations_delta_zlib_b64 323.0 KB   31.5%
audit_context_zlib_b64               161.5 KB   15.7%
```

Base64 inflates by 33%, so **245.8 KB per artifact is pure encoding overhead**, written
synchronously before every submit. The same 2,900.5 KB of underlying bytes is 697.6 KB as
a zstd-3 BLOB — smaller than the base64 *text* of the zlib form, and without the inflation.
That is the receipt-codec migration already described in `receipt_codec_options_2026-09-17.md`,
and this is the measurement that sizes it: it is not only storage, it is bytes on the
pre-submit path.

Note it is not yet proven to be 229 ms of *cost* — if the time is lock wait, a smaller
artifact shortens the hold but not necessarily the wait. The next sample with
`lease_wait` present will say which, and that ordering matters: shrinking the payload
before knowing would be optimising the half the measurement does not implicate.

## 9. `lease_wait` answers §8: it is not lock contention, except in the tail

n=662 live receipts carrying `lease_wait`:

```
p50=323.0ms  p90=691.0ms  p99=1585.0ms  max=12113.0ms

persist_full                                 324.7 ms p50   (n=8, rare)
persist_compact                               53.3          (n=654)
book_native_side_receipt                      28.8
delta_candidate_evaluations                   24.4
encode_candidate_evaluations                  22.8
...
global_auction_selection_receipt:lease_wait    0.6          p99 742.5   max 11873.8
```

**The lock-wait hypothesis in §8 is refuted at the median.** `lease_wait` is 0.6 ms p50 —
0.8% of `persist_full`, 2.7% of `persist_compact`. The write lease is not what the auction
waits on.

Two further corrections to §8, both from the larger sample:

- `persist_full` is **n=8 of 662**. It is the rarer branch, and reporting it as "the
  dominant stage" over-weighted it; `persist_compact` at 53.3 ms is what almost every
  auction actually pays.
- The p50 itself moved again, 605 ms -> 323 ms across windows. Same instrumentation, same
  code: this path varies enough between windows that only same-window comparisons mean
  anything.

### The tail has two causes, and they need different fixes

Of 124 samples where `persist_compact` exceeded 200 ms:

| | count | total |
|---|---|---|
| **write-bound** (lease ≤ 50% of persist) | **105** | 44.4 s |
| **lock-bound** (lease > 50%) | 19 | 23.6 s |

So lock contention is real but is 15% of tail events, with a worst case of 11.9 s where
`lease_wait` is 100% of the time. The other 85% are the write itself.

Retention was ruled out on the live database: the boundary lookup costs 1.8 ms and the
candidate scan **0.1 ms returning 0 rows**. It is not the cost, on this path, today.

## 10. Applied: 27.2 ms/auction of encode, removed without touching the format

The encode stages are all zlib level 9. Measured over 34 real auction payloads from
`decision_log` (19.0 MB raw):

| level | encode | size | vs L9 |
|---|---|---|---|
| 9 | 30.8 ms/artifact | 600.2 KB | +0.0% |
| **6** | **22.8 ms** | 607.9 KB | **+1.3%** |
| 3 | 10.9 ms | 645.2 KB | +7.5% |
| 1 | 8.0 ms | 658.7 KB | +9.8% |

Level 6 is the only one that buys time without paying for it in storage on a table
measured in tens of GB — level 1 is the same trade rejected earlier in this work
(96.6 ms faster, 9.0% larger).

**The format does not change.** Same zlib stream, same `*_encoding` tag, so every existing
row stays readable and no reader moves. Verified by round-tripping a real payload at both
levels: decoded bytes and their sha256 are identical, which also means the `*_sha256`
integrity fields — computed over the *decoded* bytes — are unaffected.

That property is what makes this safe, and it is exactly what the codec swap lacks: the
decoders compare `*_encoding` against literal `"zlib+base64+..."` strings and raise on
anything else, with **48 such literals across `src/`**. zstd-3 measures 1.47x smaller and
**9.6x faster to encode** (3.3 ms vs 31.7 ms per artifact), so it is worth doing — but it
has to move every reader in one change, which is a different size of work from a level.

### What is left on this path

The remaining pre-submit cost is the write of a ~1 MB artifact, 96% of which is base64
text of already-compressed payloads (245.8 KB per artifact is base64 inflation alone).
That is the codec migration, now sized by two independent measurements: 1.47x smaller
*and* 9.6x faster, against a 48-literal reader surface.

## 11. The trades WAL: an unbounded yield, and a correction to how it was read

`zeus_trades.db-wal` was found at **2,956 MB** — 46x the 64 MiB idle band — while the
three main databases were static and nothing had alerted.

### What was actually wrong

`_defer_background_io_for_held_position_monitor` returned True whenever a held-position
monitor was active, and the checkpoint cycle returned **before measuring anything**. With
positions held around the clock:

| | deferred | ran | deferred share |
|---|---|---|---|
| **trades** | **123** | 85 | **59%** |
| world | 46 | 165 | 22% |
| forecasts | 47 | 164 | 22% |

The deferrals were not evenly spread. The longest consecutive streaks were 12, 11, 8, 7
events at a 90-second period, so the trades WAL went **up to 18 minutes with no checkpoint
at all** — and a deferred cycle emits no backlog check, no size check, nothing but the
defer line. The docstring's claim that an unexecutable residual "cannot starve WAL
drainage" is true per claim and false across a continuous series of them.

Fixed: past the same 512 MiB starvation line the yield ends, loudly. Yielding disk
priority to live capital is worth a bounded delay, never an unbounded one.

### Correction: the backlog was small, the allocation was not

Running the codebase's own `checkpoint_wal` against the live database:

```
busy=0 log_frames=15000 checkpointed=14640 page_size=4096
  log total   :  61.4 MB
  checkpointed:  60.0 MB
  outstanding :   1.5 MB
```

So the **un-checkpointed backlog was 1.5 MB**, not 2.9 GB, and after that single
checkpoint the file was 67 MB. The 2,956 MB was *allocated file size* left behind by a
spike, not undrained data — which is exactly the distinction
`_wal_checkpoint_is_starved` was rewritten to make in W5-5, and the reason the alert was
correctly silent. The live log confirms the checkpoints were running and merely CONTENDED
(`busy=1`), not starved.

Two lessons here, and the second one is mine:

- A WAL file's **size** and its **backlog** are different quantities. `ls` answers the
  first; only `PRAGMA wal_checkpoint` answers the second, and the existing alert
  deliberately measures the second.
- I reported "checkpoints can't truncate because a reader always holds a lock" from file
  size and an `F_GETLK` probe alone, before running a checkpoint. The probe showed a
  SHARED lock held by `price_channel_ingest`, which is what a healthy reader looks like.
  The fix still stands — an 18-minute drainage gap is real and unbounded — but it is
  justified by the defer streaks, not by the number I first quoted.
