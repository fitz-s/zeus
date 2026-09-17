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

### 1a. CORRECTION — the real pre-submit cost is 516 ms, not 65 ms

The 65 ms above is only the compression slice. Production already logs the whole
operation at `global_batch_runtime.py:9430` ("global auction receipt store completed:
elapsed_s="), and the live log is unambiguous — **n=3,269 real calls over
2026-09-16 05:54 → 2026-09-17 17:45: min 0.007 s, p50 0.516 s, p90 0.965 s,
p99 2.291 s, max 21.313 s** (verified independently by me from `logs/zeus-live.log`).

Ordering is now **proven**, not inferred: `receipt_store_started = time.monotonic()`
(`:9329`) → `_store_global_auction_receipt` (`:9333`, the sole call site; every cited
zlib+base64 site lives inside it) → completion log (`:9430`) → `trade_conn.commit()`
(fsync) → only later `checkpoint("final_actuation:before_submit")` (`:10481`) →
`actuate_winner` (`~:10498`). The only paths between receipt-store and actuation are
early `reject(...)` returns, which abort submission entirely. So whenever an order is
submitted, this completes first.

**So a median of 516 ms sits between "decided to trade" and "submit," with a p99 of
2.3 s.** That is 8-40x my micro-benchmark and reframes the priority: the codec swap
addresses only part of it. The remainder is the delta-compute against the prior receipt,
the reads it performs, the serialization and the fsync commit. **UNVERIFIED: the split
between compression, DB I/O and delta-compute.** Profile `_store_global_auction_receipt`
internals before assuming the codec swap alone recovers the 516 ms — but note the
instrumentation to do so already half-exists, so this is cheap to settle.

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
real order-book data, not redundant polling — and the code explains why:
`maybe_checkpoint_depth()` (`family_book_capture.py:402-419`) already hash-dedupes
`book_depth` by `ladder.state_hash()`. Its own docstring records that an earlier version
*did* have the bug I suspected ("Measured 2026-09-07: unconditional 60s checkpoints of
5,324 tokens were ~18 GB/day, >90% identical to the prior row") and it is fixed. So the
remaining waste is purely the denormalization above, which `token_meta` already holds.

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

## 5. Recommended order

### CORRECTION — a local VACUUM of `zeus_trades.db` is impossible, not just "later"

I originally framed this as a sequencing problem. It is not. `diskutil` confirms
**one APFS container** (`disk3`, physical store `disk0s2`): `/`,
`/System/Volumes/Data` and every `disk3sN` mount share **one 44 GiB free pool**. There
is no second local volume, so "VACUUM INTO another path" draws from the same pool.

`VACUUM`/`VACUUM INTO` needs the original file intact until the copy is verified, so
peak = original + final = 199.5 GB + ~144 GB ≈ **344 GB against 44 GiB available**.
And because `auto_vacuum=NONE` means DELETEs never shrink the file, **no amount of
retention run first reduces the 199.5 GB original term.** Reclaiming the 55.7 GB
freelist in trades requires genuinely external storage (≥150 GB), exactly as
`vacuum_reset_trades_db.py`'s three-phase design already assumes. That is an infra
decision, not something DB-side ordering can solve.

The same arithmetic kills an in-place `family_books.db` VACUUM: 55.5 GB original +
~32.6 GB final ≈ 88 GB peak > 44 GiB. **Deleting that file outright returns all
55.5 GB immediately** with no scratch space at all — see step 3.

**Phase 1 — free space now, no code, no headroom needed**
1. Delete the dead MN2T6/MX2T6 dirs (**1.4 GB**) and `oracle_shadow_snapshots` (**76 MB**).
2. Sweep orphaned ECMWF `.partial` + `.ranges.json` (**4.1 GB**), then add the sweep to
   the existing retention so it stops recurring.
3. **`family_books.db` (55.5 GB) — operator decision, the single biggest lever on this
   host.** Zero confirmed readers anywhere in the repo; producer is on an unmerged branch
   running from an orphaned worktree scratchpad. Deleting the file reclaims **all
   55.5 GB instantly** — which alone takes the disk from 96% to ~90%. Either abandon the
   research capture or relaunch it properly from `live` with `auto_vacuum=INCREMENTAL`
   set from the start. Do not VACUUM it in place; the peak does not fit.
4. Decide on `family_books-*.db.zst` (**6.0 GB**) and the 1.017M `seed_processed` receipts.

**Phase 2 — run the retention that already exists (no headroom needed)**
5. Run `prune_terminal_opportunity_events.py` against world **without `--vacuum`**.
   Frees the `opportunity_events`/`opportunity_event_processing` dead weight into the
   freelist, which world's constant insert pressure recycles organically — no VACUUM
   needed to get the benefit. Safest high-value action available: the reactor reads these
   only by JOIN to a live `opportunity_event_processing` row
   (`src/events/reactor.py:7066,10389,10672,11416,14432`), which is exactly the script's
   KEEP set. Then wire it to a schedule.
6. Run the three dormant trades retention scripts once, manually, with `--apply`. This
   does **not** shrink the file (auto_vacuum=0) but stops the bleeding and shrinks the
   eventual VACUUM target. Close one gap first: none has an explicit WAL-size gate, and
   repo law requires bulk deletes be WAL-bounded (≤1 GB); the trades WAL was observed at
   998 MB during this audit.
7. Drop the 12.98M frozen world `execution_feasibility_evidence` rows — **after** the
   read-path check in §6.

**Phase 3 — latency + stop future growth (code, no headroom needed)**
8. The receipt-store path (§1, §1a): **p50 516 ms / p99 2.3 s sits before every submit.**
   Profile `_store_global_auction_receipt` internals first to split compression from
   DB I/O and delta-compute, then fix the dominant term. The codec swap (zlib-9+base64 →
   zstd-3 BLOB) is the known-good part: 46% smaller, 11.4x faster on that slice. Version
   it via the existing `"zlib+base64+canonical-json-v1"` tag and keep a decoder for both.
   Blast radius is confined to `decision_log.artifact_json` and 4 files.
9. Re-encode **new writes only** — no migration needed to get the benefit:
   `orderbook_depth_json` (3.49x), `opportunity_events.payload_json` (2.03x),
   `depth_before_json` (5.04x).
10. Add `ENTRY_ORDER_POSTED` to the existing partial index
    `idx_position_events_entry_execution_occurred_at`, which currently excludes it —
    cheap, additive, and it makes "when did we post for family X" answerable without a
    199 GB scan.
11. Retention owners for the true orphans: `book_hash_transitions` (10M rows, no script
    exists at all), `no_trade_regret_events` and `edli_no_submit_receipts`. RiskGuard
    reads the regret table only through a bounded recent window
    (`src/riskguard/riskguard.py:2416-2454`), so time-window retention is safe there.

**Phase 4 — blocked on external storage, not on sequencing**
12. Attach ≥150 GB of external/network storage, then
    `vacuum_reset_trades_db.py --check` → `--vacuum-into DEST` → `--swap`, converting to
    `auto_vacuum=INCREMENTAL` so the freelist problem cannot return. Never run live
    before; read `--check` output, take the backup it demands, fence the writers.
13. `provenance_json` offline rewrite (**46.8 GB → ~8 GB**) belongs here too — it
    rewrites already-written bytes, so it needs the same headroom.

Only step 3 and the encode change (daemon restart) touch running services.

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
- Latency beyond §1/§1a is **largely not instrumented**, and two columns that look like
  the answer are traps: `execution_feasibility_evidence.latency_ms` /
  `.order_intent_time` / `.submit_time` exist with 3 supporting indexes but are
  **0% populated across 47,220 rows in 24h**; and `edli_live_order_events.occurred_at`
  carries **one identical batch-write microsecond stamp across every lifecycle stage**,
  so it cannot time preflight. Fixing both is the prerequisite for further latency work.
- **A second, larger latency finding — now verified, and corrected down ~3x.**
  Attributed to `_PriceChannelRedecisionSink.__call__`
  (`src/events/price_channel_redecision_router.py:1071`) running synchronously on the WS
  asyncio event loop, opening 3 fresh connections per call
  (`reuse_read_connections=False` wired at `src/ingest/price_channel_ingest.py:5012`),
  yielding to WORLD-writer contention, and retrying only when the next quote for that
  same token arrives.

  I reproduced the reported query (n=320, p50 715 s, p90 2,332 s, max 5,932 s) — **but
  it overstates the delay.** The labelling artifact I suspected is real: rows are not
  one-per-observation. Those 320 rows cover only **119 distinct observations** (avg 2.7
  each, one re-queued **36 times**); a single `received_at` carries three different
  `available_at`, and one `available_at` recurs at three different `received_at`. So
  measuring across all rows times the last retry, not the wait.

  Honest metric — observation to **first** durable queue, grouped by `available_at`:
  **n=119, min 11.3 s, p50 224.6 s, p90 952.2 s, max 1,386.5 s.**

  Still a severe defect: **p50 3.7 minutes** to react to a price move on the
  probability-flip path, against a 90 s screen cadence whose comment claims it sits
  "well inside the executable-price freshness window" (`src/main.py:11038`). The
  re-queue count is a second finding in itself — 36 retries for one observation is the
  debounce-without-timer behaviour burning work.

  Caveat: zero `ENTRY_ORDER_POSTED` and 5/5 `SubmitRejected` in the window — genuinely
  no completed entries today, so this measures queueing, not fills.
- `state/zeus_world.db` and `state/zeus_forecasts.db` (underscore) are **0-byte decoys**;
  the live files are `zeus-world.db` and `zeus-forecasts.db` (hyphen). Do not measure the
  wrong file.
- The orphaned-worktree `family_book_capture.py` process (pid 90803, 531 CPU-min) is
  writing 55 GB from an unmerged branch. Decide: promote it to `live`, or stop it.
