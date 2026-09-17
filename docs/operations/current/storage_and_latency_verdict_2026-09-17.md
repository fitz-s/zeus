# Storage + latency verdict — 2026-09-17

Consolidated operator answer. Every number below is measured read-only on the live
host, or explicitly marked ESTIMATE / UNVERIFIED. Backing detail:
`storage_audit_raw_stores.md`, `storage_audit_databases.md`.

Disk moved **94% → 96% during the audit itself** (53 GiB → 44 GiB free of 926 GiB).
Growth is ~800 MB/day in `zeus_trades.db` alone. This is not a slow leak.

---

## 1. The one change that fixes storage and latency together

The global auction encodes its decision receipts with `zlib.compress(level=9)` then
`base64.b64encode`, inline, at `src/engine/global_batch_runtime.py:2361,2431,2478,3057,3110,3196`.

Benchmarked on one real 1.99 MB payload pulled from `decision_log`:

| Encoding | Output | Ratio | Time/call |
|---|---|---|---|
| **`zlib-9` + base64 (production today)** | 1.33 MB | 1.50x | **65.0 ms** |
| `zlib-6` raw BLOB | 1.00 MB | 1.99x | 46.9 ms |
| `zstd-1` raw BLOB | 0.85 MB | 2.34x | 2.9 ms |
| **`zstd-3` raw BLOB** | **0.71 MB** | **2.79x** | **5.7 ms** |
| `zstd-10` raw BLOB | 0.63 MB | 3.16x | 24.9 ms |
| `zstd-19` raw BLOB | 0.58 MB | 3.41x | 431.8 ms |

`zlib-9 + base64` → `zstd-3` BLOB is **46.2% smaller and 11.4x faster (65.0 → 5.7 ms,
≈59 ms saved per write)**, with **zero information loss** — the same bytes decode back.
Two independent causes of the waste: base64 inflates binary by 33% (measured 25.0% of
those stored strings), and `zlib-9` is both the slowest zlib level and worse than `zstd-3`.

**This encode is synchronous and happens BEFORE order submission.** Verified call
chain: `_store_global_preflight_receipt` (`:5021`) is called at `:9855`, after
`after_preflight = venue_submit_count()` (`:9832`) and before submission, and it runs
against `auction_deadline` (`:9557`, `effective_actuation_deadline` at `:7654`, derived
from book freshness). A `preflight_commit_guard` re-checks that deadline around the
write and rejects with `GLOBAL_REAUCTION_EPOCH_EXPIRED` / `HELD_SELL_DEADLINE_EXPIRED`
if it has passed. So the ~59 ms is pure latency at the probability-flip moment, and in
the worst case converts a live decision into a deadline reject.

Note on level choice: use `zstd-3` on the write path. `zstd-19` is the best ratio but
**432 ms/call — 6.6x slower than production** and must never go on the hot path; reserve
it for offline archival rewrites.

Applies to `candidate_evaluations_zlib_b64`, `book_native_side_states_zlib_b64`,
`audit_context_zlib_b64` and their `_delta_` siblings. Measured on the 25 biggest recent
`decision_log` rows: 72.33 MB stored, 66.62 MB (**92% of the row**) is those base64
strings.

---

## 2. Why space is never returned: retention was written and never installed

All three trades-DB retention jobs exist, are well-engineered (the `decision_log` one
carries a full consumer-window audit), and are **not installed**:

- `deploy/launchd/com.zeus.decision-log-retention.plist` — "ARTIFACT ONLY. NOT auto-installed"
- `com.zeus.executable-market-snapshots-retention.plist` — same
- `com.zeus.execution-feasibility-evidence-retention.plist` — same

`ls ~/Library/LaunchAgents | grep -iE 'retention|vacuum|prune'` → nothing.
`logs/decision_log_retention.log` and siblings **do not exist** — no evidence any has
ever run with `--apply`. Corroborated by data: `decision_log` still spans 2026-05-02→now
and `executable_market_snapshots` 2026-05-15→now, which a 30-day window would have
truncated.

`scripts/prune_terminal_opportunity_events.py` (the only thing that physically deletes
`opportunity_events`) is wired into **no** cron/launchd/daemon — manual-only. Its own
docstring records that unbounded growth in this table previously caused 14s+ writer WAL
lock holds that starved snapshot capture.

`scripts/ops/vacuum_reset_trades_db.py` exists, is careful and three-phase, and its own
header states `--vacuum-into`/`--swap` have **never executed against a live DB**.

`maintenance_worker/` does **not** own DB retention despite the name — its rules only
touch filesystem artifacts.

Second mechanism: `auto_vacuum=NONE` on every DB. A `DELETE` moves pages to the freelist,
which future inserts reuse but which is **never returned to the OS** without an explicit
`VACUUM`. That is why hand-cleaning is a treadmill.

- `zeus_trades.db`: 199.5 GB file, **55.7 GB freelist** (13,599,792 of 48,711,657 pages)
- `family_books.db`: 55.5 GB file, **22.9 GB freelist** (41%)
- `zeus-world.db` freelist = 1 page, `zeus-forecasts.db` = 0 → those two are genuine
  live growth, not bloat.

---

## 3. Where the bytes actually are (measured)

### `zeus-forecasts.db` (98.4 GB) — the forecast data you must not lose

`forecast_posteriors` ≈ 46.8 GB at 78 KB/row. Per-column share of the row:

| Column | Bytes | Share |
|---|---|---|
| **`provenance_json`** | **68,030 B** | **94.6%** |
| `q_json` | 1,000 B | 1.4% |
| `q_ucb_json` | 984 B | 1.4% |
| `q_lcb_json` | 943 B | 1.3% |

The actual forecast numbers are ~4% of the row; audit provenance is 94.6%. Measured on
`provenance_json`: zlib-6 4.37x, zstd-10 4.47x, zstd-19 4.95x, **zstd-19 + trained
dictionary 5.75x → table ≈ 8 GB**. Nothing is discarded; the field decodes identically.
This is the single largest lossless reclaim in the system and it touches **no** forecast
value, only the provenance envelope.

Others: `market_events` ≈22 GB, `calibration_pairs` ≈19.6 GB (48.16M rows, all scalars —
no blob lever; its cost is 7 indexes, ESTIMATED ≈505 B/row of index vs ≈255 B/row of data).

### `zeus_trades.db` (199.5 GB)

`decision_log` real size **22.4 GB across 106,846 rows**, of which **13.2 GB is older
than 30 days** — precisely what the uninstalled retention job targets. (My earlier
112 GB figure was a rowid-HWM upper bound; ~684k rows had already been deleted, which is
where much of the freelist came from. The rowid HWM is not a row count.)

Daily growth, trades DB: `decision_log` ~528 MB/day + `position_events` ~195 MB/day +
`execution_feasibility_evidence` ~49 MB/day + `executable_market_snapshots` ~21 MB/day
≈ **800 MB/day**.

`orderbook_depth_json` in `executable_market_snapshots` is stored as **uncompressed raw
TEXT**, zlib-6 ratio **3.49x**. `depth_before_json` in `execution_feasibility_evidence`
is uncompressed, ratio **5.04x**.

### `zeus-world.db` (100.8 GB)

`opportunity_events` — 19,136,009 rows, `payload_json` 77.5% of the row, ~0.5–1.0 KB/row
across the whole table → **≈12–18 GB**, unbounded, prune script not wired.
zlib ratio 2.03x.

`execution_feasibility_evidence` in world holds **12,975,290 rows frozen since
2026-06-18** while the live writer correctly targets trades (`src/state/domains.py:109`
declares `Domain.TRADE`). Dead weight from a retired legacy writer — but verified
not-written, **not** verified not-read. Do not drop without a read-path check.

Corrections to my own first pass, from re-sampling head/middle/tail instead of tail only:
`no_trade_regret_events` is ≈2 GB (not 30.5 GB) and `opportunity_events` is ≈12–18 GB
(not 320 GB). Tail-only sampling on tables whose row size shifted over time is unsafe.

### `family_books.db` (55.5 GB) — worst ratio in the system

Holds **only 5 days** (2026-09-12 → 2026-09-17). Real data ≈**7.1 GB**
(`book_top` 16.4M rows × 309 B, `book_depth` 11.5M × 178 B) + ≈3.9 GB indexes +
**22.9 GB freelist**; the rest is page fragmentation from hourly deletes.

The market values you actually want are **3% of each row**. 64% is repeated identifier
strings: `token_id` (77 B) + `condition_id` (66 B) + `event_slug` (55 B) re-stored on
every one of 16.4M rows for only **13,860 distinct tokens**. An INTEGER FK into the
existing `token_meta` removes essentially all of it, and shrinks the two
`(token_id, received_at_utc)` indexes that re-carry the 77-byte key.

Two hypotheses of mine that the data **refuted**: only 0.4% of `book_depth` rows are the
empty book, and consecutive-identical-hash (no-change) ticks are **0%**. This is dense
real order-book data, not redundant polling.

Written by `scripts/research/family_book_capture.py`, which exists **only on unmerged
branch `research/family-book-capture`** and whose live instance (pid 90803) runs from an
**orphaned worktree scratchpad**, not the `live` checkout. It has row retention
(`--retain-days 5`, hourly DELETE) and **no VACUUM**. No consumer anywhere in the repo
reads this DB.

### Raw file stores

- **MN2T3/MX2T3 GRIB (46 GB)** — working as designed. Retention is automatic, 2-day
  window, proof-gated on canonical DB truth before deleting
  (`src/data/ecmwf_open_data.py:289-436`). The leading-dot filenames are an intentional
  cache convention (`:1441-1454`) and **do** match the retention regex (`:255-258`).
  Not a leak. **KEEP_AS_IS.**
- **Your MN2T6/MX2T6 dirs (1.4 GB) are dead.** `raw/open_ens_mn2t6_localday_min/` (847 MB)
  and `open_ens_mx2t6_localday_max/` (545 MB): producer's `--output-root` moved to
  `coordinate_manifests` in `160b5ff40` (2026-09-09), no consumer ever, content already
  absorbed into `ensemble_snapshots.members_json`. **DEAD_DELETE.** Your instinct was right.
- **Real ECMWF leak found:** 412 `.partial` files = **4.1 GB** and 1,682 `.ranges.json`
  = 11 MB are **permanently invisible to retention** — `_raw_file_identity` (`:279-286`)
  returns `None` for any name not matching the two `.grib2` patterns, so the 2-day cutoff
  never reaches them regardless of age. `.ranges.json` is additionally never unlinked on
  the success path (`_fetch_step:2214-2273` unlinks the `.partial` pair but not the
  range-resume manifest). Unbounded in file count, growing every cycle.
- `state/replacement_forecast_live/` (7.5 GB): `seed_processed` has **>1,017,000
  hardlinked receipt files**, oldest 2026-08-30, with no reader and no reaper. The
  `*_latest` dirs are self-bounded by `os.replace` and are fine.
- `.total_loss/incidents/` (20 GB): 663 `evidence.db`, 73 of them >50 MB and concentrated
  in 2026-08-21..23. Only in-progress temp dirs are cleaned; completed incident dirs have
  no retention. `raw_json` gzips **8x** measured.
- `raw/oracle_shadow_snapshots/` (76 MB): superseded 2026-06-09, zero references. **DEAD_DELETE.**
- `state/research/archives/family_books-*.db.zst` (6.0 GB): one-off manual snapshot, no consumer.
- `logs/` (548 MB): rotation works. Minor gap — `logs/ritual_signal/*.jsonl` (25 MB) is
  outside the rotation glob (`rotate_zeus_logs.sh:70`).

---

## 4. Two things I checked and am NOT recommending

Reporting these so they don't get "fixed" later on a false premise.

- **`PRAGMA synchronous`** is never set (default FULL). Benchmarked FULL vs NORMAL in WAL
  on this SSD: 0.09 vs 0.02 ms/commit. ~0.07 ms saved per commit, in exchange for giving
  up a durability guarantee on a money path. **Not worth it** — 800x smaller than the
  encode win in §1.
- **`ANALYZE` / `sqlite_stat1`** — genuinely absent (never appears in the codebase;
  `zeus_trades.db` has no `sqlite_stat1`, world has 9 rows for 115 tables, forecasts 1
  for 41). `EXPLAIN QUERY PLAN` does report `SCAN decision_log`. **But** `id` is
  `INTEGER PRIMARY KEY`, so `ORDER BY id DESC LIMIT 8` is a reverse rowid walk: measured
  **0.0 ms**, and 0.8 ms when pulling `artifact_json`. No hot-path cost found.
  Not a priority; revisit only if a specific slow query appears.

Existing pragmas are already sound: `cache_size` 1 GiB and `mmap_size` 32 GB per
connection (`src/state/db.py:356,368`), `journal_size_limit` 64 MB (`:96`).

---

## 5. Recommended order — respects the 44 GiB free-space constraint

The sequencing matters: `VACUUM INTO` of `zeus_trades.db` needs ~144 GB for its output
and **cannot run today**. Free space must be earned first.

**Phase 1 — free space, no code, no risk (~30-35 GB, hours)**
1. Delete the dead MN2T6/MX2T6 dirs (**1.4 GB**) and `oracle_shadow_snapshots` (**76 MB**).
2. Sweep orphaned ECMWF `.partial` + `.ranges.json` (**4.1 GB**), then add the sweep to
   the existing retention so it stops recurring.
3. `VACUUM` `family_books.db` → reclaims **~23-30 GB**, fits in current free space, and
   the DB has no live consumer so the exclusive lock is harmless. Then set
   `auto_vacuum=INCREMENTAL` so its hourly deletes self-reclaim forever.
4. Decide on `family_books-*.db.zst` (**6.0 GB**) and the 1.017M `seed_processed` receipts.

**Phase 2 — install the retention that already exists (~15-20 GB)**
5. Install the three launchd retention plists (run once manually with `--apply` first and
   read the output). `decision_log` alone releases **13.2 GB** of >30-day rows.
   Gap to close first: none of the three has an explicit WAL-size gate, and repo law
   requires bulk deletes be WAL-bounded (`-wal` ≤ 1 GB). The trades WAL was observed
   touching 998 MB during this audit, so add the gate before unsupervised scheduling.
6. Wire `prune_terminal_opportunity_events.py` to a schedule.

**Phase 3 — the encode fix (latency + storage, needs code)**
7. `zlib-9`+base64 → `zstd-3` BLOB on the auction receipt path. **~59 ms off every
   decision write** and 46% fewer bytes. Keep a decoder that accepts both encodings so
   existing rows stay readable — the field already carries an explicit
   `"zlib+base64+canonical-json-v1"` encoding tag, so version it there.
8. `provenance_json` in `forecast_posteriors` → zstd-19 + trained dictionary as an
   **offline** rewrite: **46.8 GB → ~8 GB**, no forecast value touched.
9. Compress `orderbook_depth_json` (3.49x) and `depth_before_json` (5.04x).
10. `family_books` schema: `token_id`/`condition_id`/`event_slug` → INTEGER FK into
    `token_meta` (removes ~64% of both row and index bytes).

**Phase 4 — only once ~150 GB is free**
11. `scripts/ops/vacuum_reset_trades_db.py --check`, then `--vacuum-into`, then `--swap`,
    converting to `auto_vacuum=INCREMENTAL` so the 55.7 GB freelist problem cannot
    return. This has never run against a live DB — read `--check` output carefully, take
    the backup it demands, and fence the writers as its header requires.

Nothing in Phases 1-3 requires trading downtime except the `family_books` VACUUM (no
consumer) and a daemon restart for the encode change.

---

## 6. Open / UNVERIFIED

- Whether anything **reads** the frozen `zeus-world.db` `execution_feasibility_evidence`
  (12.98M rows) and the trades-side `opportunity_events` decoy (389 rows). Verified
  not-written only. A read-path check is required before dropping ≈5 GB.
- `.total_loss` incident retention needs a resumability rule first — confirm the daemon
  never revisits a stale `incident_id`. Compression (8x) is safe today regardless.
- `zeus-forecasts.db` table sizes are sampled estimates; `dbstat` timed out at 600 s on
  both large DBs (it walks the whole file before filtering, so scoping by `name=` does
  not help).
- Latency beyond the §1 encode is **not instrumented**: there is no stored
  observation→decision→`ENTRY_ORDER_POSTED` timestamp chain to compute stage percentiles
  from. Adding those checkpoints is the prerequisite for any further latency work — what
  is not measured cannot be optimized, and I will not guess at it.
- The orphaned-worktree `family_book_capture.py` process (pid 90803, 531 CPU-min) is
  writing 55 GB from an unmerged branch. Decide: promote it to `live`, or stop it.
