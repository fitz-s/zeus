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
