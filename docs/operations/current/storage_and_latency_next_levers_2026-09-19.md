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
