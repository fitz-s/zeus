# family_books compacted, and what `.total_loss`'s 20 GB actually is (2026-09-18)

Two results: the family_books reclaim is **done and the leak mechanism is gone**, and the
`.total_loss` store is characterized — with a correction to this audit's own earlier
compression estimate and a clear verdict that it must **not** be mass-deleted.

## 1. family_books.db — complete

| | |
|---|---|
| file | 55.5 GB → **26.4 GB** |
| reclaimed | **29.1 GB** (dead space was 52% of the file: 6,998,895 of 13,550,017 pages) |
| `VACUUM INTO` | 8 min 18 s, output 26.4 GB |
| `quick_check` | **ok** |
| `auto_vacuum` | **2 (INCREMENTAL)** — the leak mechanism is closed |
| data | all 5 days of order-book ticks preserved |

Row-count verification against the pre-stop baseline:

```
book_top    13,852,537 == 13,852,537  ✓
book_depth   9,801,866 ==  9,801,866  ✓
token_meta      13,860 ==     13,860  ✓
feed_health      5,992 ->      5,994  (+2)
```

The `feed_health` delta is **not** loss. Those two rows are stamped 15:38:41 and
15:39:43 UTC, i.e. after I read the baseline and before the writer took SIGTERM — it is
a one-row-per-minute feed telemetry table, so it gained two rows rather than losing any.
Every market-data table matched exactly.

`auto_vacuum=INCREMENTAL` is the part that matters long-term: it only takes effect when
set immediately before a VACUUM, which is why the two were paired. Without it, the hourly
retention DELETEs would rebuild the 29 GB of dead space within about two weeks — that is
precisely how this file reached 55 GB.

### Sequencing note for the next operator

The runbook's step 6 (`swap + auto_vacuum + VACUUM` in one command) had to be **split**,
because free space had fallen to 10 GiB and the post-swap VACUUM needs ~27 GB transiently.
The safe order when space is tight is: swap → verify readable → delete the pre-swap
original (releases the space) → *then* convert `auto_vacuum`. Executed in that order here.

### The producer was already dead

Before stopping it, pid 90803 was alive and logging (`gamma … failed:` warnings) at
**0.0% CPU with its newest data row ~30 h stale** (`book_top` ended 2026-09-17T09:11).
So runbook step 8 ("decide the producer's future") is easier than written: it had already
stopped producing. Keeping it means promoting
`scripts/research/family_book_capture.py` from the unmerged `research/family-book-capture`
branch onto `live` and relaunching from `/Users/leofitz/zeus` (not a scratchpad worktree),
with `auto_vacuum=INCREMENTAL` set at creation. The 6.0 GB
`archives/family_books-20260910T233354Z.db.zst` snapshot is still an open delete candidate.

## 2. `.total_loss` (20 GB) — DO NOT mass-delete

663 `evidence.db` files across 1,804 incident dirs. It is **not** a growing leak: 101
files from 2026-08-22/23 hold **10.62 GB (56%)**, the other 562 hold 8.41 GB, and
September's are 1–6 MB each. A one-off historical burst.

**248 incidents are still live** — `retry_pending` 164, `queued` 84 — and the daemon
selects on exactly those states (`total_loss_loop.py:397`). Worse for the
delete-it-all idea: line 4191 treats **`observing` as resumable too**, alongside
`queued`/`retry_pending`, and `observing` is 1,933 rows. So `observing` is *not* a
terminal state and mass-deleting this store would destroy live work. 0 of 1,804 have
`production.json`; only 184 have even started `diagnosis.json`.

### Correction to this audit's earlier estimate

The raw-store audit reported "gzip 8x measured" on `evidence.db`. That was measured on
the wrong table. `dbstat` on a representative 205.7 MB file:

```
source_clocks   161 MB   <- 1,997 rows at ~108 KB/row
monitor_events   31 MB   <- 5,462 rows at ~4.7 KB/row
exit_decisions    1 MB
freelist          0      <- no dead space; the bytes are real
```

`source_clocks.raw_json` averages **84–108 KB per row** and is the whole cost — the same
shape as `forecast_posteriors.provenance_json` (94.6% of its row). Measured on 200 real
rows (22.2 MB):

| codec | ratio | 20 GB store becomes |
|---|---|---|
| gzip / zlib-6 | 2.41x | ~8.3 GB |
| zstd-3 | 2.45x | ~8.2 GB |
| zstd-19 | 2.79x | ~7.2 GB |
| **zstd-19 + trained dict** | **3.81x** | **~5.3 GB** |

So the honest figure is **~15 GB reclaimable by lossless compression**, not the ~17 GB
implied by the 8x estimate — and the lever is `source_clocks`, not `monitor_events`.

### Recommended action

Compress `source_clocks.raw_json` (and `monitor_events.raw_json`) in place with
zstd-19 + a trained dictionary, leaving every row present and every incident resumable.
`zstandard==0.25.0` is already pinned and installed. Do **not** delete incident
directories until someone designs an explicit retention rule keyed on a genuinely
terminal state — which, per line 4191, `observing` is not.

## Cumulative state

| store | before | after |
|---|---|---|
| `51 source data` | 72 GB | **7.5 MB** |
| `family_books.db` | 55.5 GB | **26.4 GB** |
| `.total_loss` | 20 GB | 20 GB (characterized; ~15 GB compressible) |

Disk went 97% → 88% at the peak of this work. The ECMWF retention continues to fire
autonomously — it evicted a further 7.57 GB (4.27 + 3.30 across the two tracks) without
any intervention, which is the real acceptance test for those fixes.

Still infrastructure-blocked: `zeus_trades.db`'s 55.7 GB freelist needs a `VACUUM INTO`
output of ~144 GB, which does not fit even at the 109 GiB high-water mark reached here.
