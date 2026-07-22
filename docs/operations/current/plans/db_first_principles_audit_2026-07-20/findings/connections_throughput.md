# Lane W5 — Connections + Throughput + Error Archaeology

Read in full: `src/state/db_writer_lock.py` (1067 lines), `src/state/connection_pair.py`
(285 lines), `src/state/write_coordinator.py` (486 lines). `src/state/db.py` (13876
lines) read in the load-bearing ranges via targeted `Read` (too large for one call).
Plus `src/main.py` scheduler/checkpoint-cycle wiring, `src/ingest_main.py` +
`src/ingest/price_channel_ingest.py` + `src/data/substrate_observer.py` +
`src/data/day0_hourly_vectors.py` lock call sites, and `logs/*.err` / `logs/*.log`.

**WAL-file safety check (rule 5, live-oscillating).** All three canonical WALs
were re-`ls -l`'d before and after the DB-touching work; none exceeded 512 MiB, no
STOP triggered. But the trade WAL is not steady — it oscillates fast under live
load:

| DB WAL | audit start (01:07) | audit end (01:12) | prior-lane draft (00:12) |
|---|---|---|---|
| `zeus_trades.db-wal`   | 99,489,792 (95 MB)  | 241,345,512 (230 MB) | 373 MB |
| `zeus-world.db-wal`    | 5,949,312 (5.7 MB)  | 8,895,112 (8.5 MB)   | 16–24 MB |
| `zeus-forecasts.db-wal`| 2,027,072 (2.0 MB)  | 2,204,232 (2.1 MB)   | 4–7.7 MB |

The trade WAL moved 95 → 230 MB in ~5 min and had been 373 MB an hour earlier — so
PASSIVE checkpoints DO copy frames (373 → 95 MB reclaim happened), but the file
never truncates and re-grows continuously. That oscillation is the live fingerprint
of findings W5-2 and W5-3 below.

---

## HEADLINE: write-intent locking is fragmented across FOUR incompatible schemes

Every "writer-lock" mechanism in the tree guards the SAME three physical DB files,
but through four different, mutually-invisible lock objects. Two writers of the
same DB using different schemes do NOT mutually exclude — they fall through to
SQLite's own `busy_timeout`, which is exactly the `database is locked` storm in
part (c).

| Scheme | Lock object per DB | Callers (verified file:line) |
|---|---|---|
| `db_writer_lock(db, LIVE)` | flock on **`<db>.writer-lock.live`** (`db_writer_lock.py:73-81`) | `decision_events.py:249`, `no_trade_events.py:248`, `day0_metric_fact_store.py:187`, `day0_hourly_vectors.py:377` |
| `db_writer_lock(db, BULK)` / `bulk_lock_with_chunker` | flock on **`<db>.writer-lock.bulk`** (`db_writer_lock.py:73-81, 545`) | `tigge_pipeline.py:379`, ~40 backfill scripts (allowlist) |
| `WriteCoordinator.lease(...)` | flock on **`<db>.writer-lock`** — unified, NO live/bulk suffix (`write_coordinator.py:135-143`) | `ingest_main.py:548,1724`, `price_channel_ingest.py:297,377`, `substrate_observer.py:135` |
| `world_write_mutex()` / `world_write_lock` | in-process **`threading.Lock`** (world only) (`db.py:648-708`) | EDLI reactor, world event writers, price_channel WORLD scope (`price_channel_ingest.py:277`) |

The kicker: `write_coordinator.py`'s own module docstring (lines 4-9) and
`unified_writer_lock_path` (lines 135-143, comment: *"intentionally omits LIVE/BULK
from the filename … it must not create a separate same-file writer lane"*) state
the CORRECT model — one gate per DB, shared by LIVE+BULK. The authors knew the
`db_writer_lock` `.live`/`.bulk` split was wrong. But WriteCoordinator was added
(commit `337be9885`, 2026-06-27) as a **third** file convention rather than
replacing the split, so it made the fragmentation worse. The prior W5 draft and the
GPT-5.6 consult both call WriteCoordinator a "skeleton" — **that is stale**: it is
live in three ingest modules today (price_channel wiring last touched
`ca16a7a7d`, 2026-07-19), and it is the write gate for the exact process
(`zeus-price-channel-ingest`) emitting 10,004 `database is locked` errors.

Concrete collision, straight from the logs (`zeus-live.err`, latest 2026-07-20
15:04:08): `day0_hourly_vectors` takes `db_writer_lock(FORECASTS, LIVE)` on
`zeus-forecasts.db.writer-lock.live` and misses with `BlockingIOError [Errno 35]
… contended on …writer-lock.live`, while `ingest_main` writes the same
`zeus-forecasts.db` through `WriteCoordinator.lease(FORECAST)` on
`zeus-forecasts.db.writer-lock` (a different file it cannot see) and `tigge` writes
it through `.writer-lock.bulk` (a third file). All three collide at the single
SQLite WAL write-lock → `database is locked`.

---

## (a) Connection-level PRAGMA config

Values set in `_connect()` (`db.py:234-295`, canonical write factory), mirrored in
`_connect_read_only()` (`db.py:298-327`) and the legacy `get_connection()` factory
(`db.py:1668-1713`).

| PRAGMA | Value | Set at | Notes |
|---|---|---|---|
| `journal_mode` | `WAL` | `db.py:262`, `:1689` | |
| `foreign_keys` | `ON` | `db.py:263`, `:315`, `:1690` | |
| `cache_size` | `-1048576` (1 GiB) | `db.py:270`, `:317`, `:1697` | env `ZEUS_DB_CACHE_KB`; 2026-05-12 cold-cache antibody |
| `mmap_size` | `32 GiB` | `db.py:281`, `:321`, `:1703` | env `ZEUS_DB_MMAP_BYTES` |
| `busy_timeout` | write **30000 ms**; RO **1000 ms** | `_apply_busy_timeout` `db.py:172-196` (write); `:323` (RO) | env `ZEUS_DB_BUSY_TIMEOUT_MS` / `ZEUS_DB_READ_BUSY_TIMEOUT_MS`; re-applied by name after every `executescript()` at `db.py:1909-1910, 3000, 5074-5075, 6259-6260, 6421` because `executescript` nulls the C-level busy handler (Fitz #5 antibody) |
| `query_only` | `ON` | `db.py:314` (RO only) | |
| **`synchronous`** | **NEVER set** | — | `grep -rn "PRAGMA synchronous\|synchronous=" src/` → **0 hits** |
| **`wal_autocheckpoint`** | **NEVER set** | — | `grep -rn "wal_autocheckpoint\|autocheckpoint" src/` → **0 hits** |

### FINDING W5-1 (durability, CONFIRMS consult "durability pragmas unset")

`PRAGMA synchronous` is never issued on any connection anywhere in `src/`, so every
handle — including all trade-authoritative writes to `zeus_trades.db`
(position_current, position_events, orders, execution records) — runs at SQLite's
compiled default **`FULL` (2)** under WAL.

- **Correctness**: `FULL` under WAL is corruption-safe; there is no data-loss law
  violation. It is the *implicit* policy the consult flagged: the durability level
  of the money-path DB is decided by a compiled default nobody wrote down, not by a
  reviewed line of code. WAL's own default is `NORMAL`, so the code is actually
  *stricter* than SQLite's WAL default (not laxer).
- **Throughput cost**: `FULL` fsyncs the WAL on every commit AND at every
  checkpoint; `NORMAL` (the standard WAL recommendation) fsyncs only at checkpoint,
  is still crash-safe on a WAL DB (worst case: lose the last few committed txns on
  power loss, never corruption). Every one of the hundreds of small per-cycle writes
  currently pays a full fsync.
- **HYPOTHESIS** (not benchmarked): setting `synchronous=NORMAL` would materially
  cut per-write latency under the demonstrated contention. Flagged as the single
  most concrete unused throughput lever, plus a durability-policy line that should
  be *explicit* whichever value is chosen. Not a measured claim.

### FINDING W5-4 (no connection pool; per-call connect cost)

No pool anywhere (`grep "pool\|Pool\|_conn_cache\|lru_cache"` in `db.py` → none).
`get_trade_connection()` / `get_world_connection()` / `get_forecasts_connection()` /
`_connect_read_only()` each do a fresh `sqlite3.connect()` + 5-6 PRAGMAs +
`_install_connection_functions` + busy-timeout re-apply, at **336 static call sites
across 43 files** in `src/` (`main.py` alone: 66). `cycle_runner.py` is thin (opens
at `:1061`, closes at `:1167`) and fans out into that call graph. Exact
connects-per-cycle is not runtime-measured (out of read-only scope) — **HYPOTHESIS
bound**: at least dozens per cycle given how many of the 336 sites sit on the hot
`run_cycle → monitor/exit/candidate/execute` path.

### FINDING W5-8 (writable RO-intent handles — CONFIRMS consult #6)

`_connect()` opens with `sqlite3.connect(str(db_path))` (`db.py:259`) — default
`rwc` mode, plus `db_path.parent.mkdir(...)` (`:249`) — so a wrong/missing canonical
path silently *creates an empty DB* (fail-open; consult #1) rather than erroring.
`get_connection_pair().world_conn` (`connection_pair.py:261-265`) is a *full RW*
handle despite the class docstring calling it "world_conn: RO-intent … RO for
trading lane" — the docstring itself admits it (`connection_pair.py:256-259`: *"uses
standard _connect(), not ?mode=ro URI … Phase 3 will enforce URI-level RO"*).
`ConnectionTriple` / `get_connection_triple()` likewise return three RW handles.
The RO intent is a naming convention only; nothing enforces it. `TypedConnection`
(`connection_pair.py:43-129`) tags a `db_identity` but the `raw` write path is fully
open. (Provenance-lane relevance: the fail-open `rwc` create is the plausible source
of the 157 `no such table` in `zeus-live.err` — a wrong `main` schema resolves to a
freshly-created empty file.)

### Periodic WAL-checkpoint backstop + FINDING W5-2 (PASSIVE false-green)

`checkpoint_world_wal()` (`db.py:711-749`) and `checkpoint_trades_wal()`
(`db.py:752-780`) each run `PRAGMA wal_checkpoint(PASSIVE)` (`db.py:742`, `:774`) on
a dedicated short-lived `_connect()` handle (no write mutex — a checkpoint is not a
write txn). Scheduled in `main.py:6954-6969` at **90 s**, offset 120 s / 135 s after
boot, `max_instances=1, coalesce=True`. **No forecasts-DB checkpoint backstop
exists** (`grep checkpoint.*forecast` → 0 hits); forecasts relies solely on the
default `wal_autocheckpoint` (1000 pages ≈ 4 MB) — currently harmless (forecasts WAL
is the smallest) but structurally unguarded against the same reader-pinning
starvation world/trades were patched for.

**The `busy` first-field is misread — CONFIRMS the consult's "false-green"
telemetry, exact lines:**

- Code reads `busy = int(row[0])` at `db.py:744` (world) and `db.py:775` (trades).
- Callers branch on it: `main.py:5437` (`if busy == 0:` → INFO "OK"), else
  `main.py:5442-5449` WARNING "a reader is pinning the WAL floor"; trades twin
  `main.py:5470` / `5475-5480`.
- **SQLite semantics**: in PASSIVE mode `sqlite3_wal_checkpoint_v2()` never invokes
  the busy handler and never returns `SQLITE_BUSY`; the first column is 1 ONLY for a
  blocked RESTART/FULL/TRUNCATE checkpoint. **For PASSIVE, `row[0]` is always 0.**
- Therefore `busy == 0` is a constant: the INFO "OK busy=0" branch fires on every
  90 s tick and the WARNING branch (`main.py:5442-5449`, `:5475-5480`) is **dead
  code**. The reader-pinning-the-floor condition the backstop was built to alert on
  actually shows up as `checkpointed_frames < log_frames` (`row[2] < row[1]`) — both
  are logged but never compared, so the alert never fires.
- The docstrings compound it: `db.py:719-720` and `main.py:5420, 5427-5428` all
  assert PASSIVE "returns BUSY `(1,-1,-1)`" when a reader pins the floor — factually
  wrong for PASSIVE. The live evidence (trade WAL oscillating 95→230→373 MB while
  the job logs "OK busy=0") is exactly a false-green: partial drain, no truncation,
  no alert.

### FINDING W5-3 (intended TRUNCATE silently downgraded to PASSIVE)

The scheduler-registration comments say the backstop runs **`PRAGMA
wal_checkpoint(TRUNCATE)`** (`main.py:6948` "PRAGMA wal_checkpoint(TRUNCATE) on
zeus-world.db"; `:6959` "zeus_trades.db WAL TRUNCATE backstop"), but the
implementation runs **PASSIVE** (`db.py:742, 774`). This is not just a stale comment
— it is the concrete reason the `-wal` files never shrink to ~0. TRUNCATE blocks
until it can checkpoint and then truncates the file (bounding it); PASSIVE never
blocks and never truncates. The function docstrings deliberately justify PASSIVE
(`db.py:727-731`: don't sit in the busy handler ahead of live monitor writes), so
there is a genuine unreconciled design tension: the *goal* recorded at the
registration site (bound the file) is unmet by the *mechanism* actually shipped
(PASSIVE), and the false-green monitor (W5-2) hides the gap. Net: the trade WAL
floats at 95-373 MB indefinitely, which is precisely the 2026-06-16 `810 MB`
incident regime the backstop was written to prevent.

---

## (b) Writer-lock arbitration — the four named consult claims, confirmed with file:line

**Model / cost.** `db_writer_lock(db, class)` (`db_writer_lock.py:89-140`) is a plain
blocking `fcntl.flock(LOCK_EX)` on a sentinel file — no polling/backoff of its own
(non-blocking mode raises `BlockingIOError` and bumps
`db_writer_lock_contended_total`). It serializes write *intent across processes*;
it does not touch the SQLite connection. `BulkChunker`
(`db_writer_lock.py:157-497`) is the only component with real backoff: a
dual-channel 30 s watchdog forcing BULK writers to `commit_chunk()` → release bulk
fcntl → `sleep(0.05 s)` → re-acquire when a LIVE waiter appears
(`db_writer_lock.py:339-378`). `WriteCoordinator` (`write_coordinator.py:186-246`)
adds a per-DB `threading.Lock` + a *spin-with-`time.sleep(0.01)`* non-blocking flock
loop (`write_coordinator.py:377-390`) with deadline support.

**CLAIM 1 — split `.writer-lock.live`/`.writer-lock.bulk` do not mutually exclude:
CONFIRMED.** `_LOCK_FILE_SUFFIX` maps `LIVE→".writer-lock.live"`,
`BULK→".writer-lock.bulk"` (`db_writer_lock.py:73-76`); `_lock_file_path`
(`:79-81`) picks per class. A LIVE holder flocks one file, a BULK holder flocks a
different file → both proceed concurrently against the same physical SQLite DB.
Same-class writers across processes DO exclude; cross-class do not. The only cross-
class coupling is K3's `BulkChunker._is_live_contended()` (`:311-337`) probing the
`.live` file — a cooperative yield, not mutual exclusion. And per the HEADLINE, this
is now a *three*-file split once WriteCoordinator's unified `.writer-lock` is added:
none of the three flock namespaces exclude each other.

**CLAIM 2 — env-var write-class is a process-wide `os.environ` race: CONFIRMED.**
`_resolve_write_class` reads `os.environ.get("ZEUS_DB_WRITE_CLASS")`
(`db.py:218`). `add_job_with_write_class._wrapped` sets
`os.environ["ZEUS_DB_WRITE_CLASS"] = resolved.value` (`db_writer_lock.py:992`) then
restores (`:997-1000`). `os.environ` is **process-global, not thread-local**, but
the docstring claims "thread-local-restoration semantics … concurrent threadpool
jobs do not stomp each other" (`db_writer_lock.py:975-977`). Under the ingest
daemon's `ThreadPoolExecutor(max_workers=10)` (`ingest_main.py:3654,3691`) two
concurrent jobs racing the same env var will read each other's class — the
snapshot/restore of the *local* `prior` cannot protect the *shared* global. NB: in
practice the write-class currently only increments a counter (see CLAIM-adjacent
note below), so the race mis-labels telemetry rather than mis-routing a lock today —
but it is a live latent bug the moment write_class gates a real flock.

**CLAIM 3 — `_thread.interrupt_main()` targets the wrong thread: CONFIRMED.**
`BulkChunker._watchdog_run` calls `_thread.interrupt_main()` unconditionally on
timeout (`db_writer_lock.py:493`) with **no check that the chunker runs on the main
thread** (`grep current_thread|main_thread` in the module → only interrupt_main
itself). `interrupt_main()` always raises `KeyboardInterrupt` in the *main
interpreter thread*. But the sole production caller,
`bulk_lock_with_chunker(...)` in `tigge_pipeline.py:379`, runs under
`_scheduler_job("ingest_tigge_…")` on the ingest `BlockingScheduler` +
`ThreadPoolExecutor` (`ingest_main.py:2028-2073, 3509-3512, 3691`) — a **worker
thread**. So on a stuck-BULK timeout: (a) the actually-stuck tigge worker is NOT
interrupted by channel 2 (only the cooperative flag would catch it — the exact
C-level-blocked case channel 2 exists for is uncovered), and (b) a spurious
`KeyboardInterrupt` lands in the ingest daemon's main thread sitting in
`BlockingScheduler.start()`, which is the standard signal to shut the whole
scheduler down. A watchdog meant to unstick one BULK job can instead kill the ingest
daemon and leave the stuck job running.

**CLAIM 4 — WriteCoordinator "skeleton": REFUTED (now the more important finding).**
It is a complete, live implementation (`write_coordinator.py`, 486 lines, born
`337be9885` 2026-06-27), the runtime write gate for the ingest + price-channel +
substrate-observer processes (call sites above). Its own docstring prescribes the
correct one-gate-per-DB model but it ships as a THIRD lock-file namespace alongside
the two it was meant to obsolete — see HEADLINE. `WriteCoordinator.transaction`
(`:248-305`) correctly refuses fake multi-DB atomicity
(`CrossDatabaseTransactionUnsupported`), which is a genuine improvement; the problem
is purely that the migration off `db_writer_lock` never happened, so both coexist.

Adjacent confirmed fact: `_resolve_write_class` in `_connect` / `get_connection`
only **increments a counter** (`db.py:289-291`, `:1710-1712`) — it acquires **no
flock**. So `get_trade_connection(write_class="live")` (e.g.
`substrate_observer.py:2381,2600`) and the ~336 direct connection calls take the
SQLite write lock with **no write-intent lock at all**; those writers serialize only
via `busy_timeout`. Only the explicit `db_writer_lock(...)` / `WriteCoordinator.lease`
/ `world_write_mutex` call sites hold any intent lock — a minority of trade-DB
writers.

---

## (c) Error archaeology (logs LOCAL time; DBs UTC — 5 h offset)

`grep -c -i` over the seven patterns across `logs/*.err` + `logs/*.log`. **Two
prior-draft "zero" claims were substring artifacts and are corrected here.**

| File | `database is locked` | `no such table` | latest `database is locked` (local) |
|---|---|---|---|
| `zeus-ingest.err` | 13,749 | 57 | 2026-07-21 00:10:00 |
| `zeus-price-channel-ingest.err` | 10,004 | 0 | **2026-07-21 01:12:57** (during this audit) |
| `zeus-live.err` | 3,906 | 157 | 2026-07-20 22:43:31 |
| `zeus-ingest.log` | 2,759 | 0 | (INFO deferrals) |
| `zeus-substrate-observer.log` | 1,030 | 0 | 2026-07-21 00:47:37 |
| `zeus-live.log` | 463 | 0 | |
| `zeus-substrate-observer.err` | 382 | 0 | 2026-07-21 00:47:37 |
| `riskguard-live.err` | 214 | 0 | 2026-07-20 21:44:25 |
| `zeus-post-trade-capital.err` | 33 | 55 | |
| `zeus-forecast-live.err` | 1 | 1 | |

**Corrections to the prior W5 draft (which claimed zero for these):**
- **`SQLITE_BUSY`**: the C-symbol never appears, BUT `grep -i` finds **1,859** hits
  in `zeus-ingest.log` of Zeus's own token `reason=sqlite_busy` — INFO-level
  `DAY0_METAR_COMMIT_DEFERRED` / `DAY0_METAR_SOURCE_CLOCK_DEFERRED` deferrals, each
  wrapping `OperationalError('database is locked')` (latest 2026-07-20 21:00:06,
  `pending_reports=11`). These are the ingest lane's fail-soft response to the same
  lock storm, not a distinct error.
- **`IOERR`**: 165 in `zeus-price-channel-ingest.err` + 7 in `zeus-live.err` — but
  these are `grep -i` matches on **`BlockingIOError`**, i.e. `[Errno 35] Resource
  temporarily unavailable` = `EWOULDBLOCK` from a non-blocking `fcntl.flock`. The
  7 in `zeus-live.err` are literally `db_writer_lock(write_class=live) contended on
  …/zeus-forecasts.db.writer-lock.live` (`day0_hourly_vectors`, latest 2026-07-20
  15:04:08). **No genuine `SQLITE_IOERR` / disk-I/O error exists** — the 87%-full
  disk has NOT produced a real I/O fault.
- **`malformed`**: 3 in `riskguard-live.err` are app-level `runtime exposure
  authority malformed: field=fill_authority value=''` (2026-07-11), **NOT** SQLite
  "database disk image is malformed". **No DB corruption anywhere.**
- **`disk I/O`** and **`attempt to write a readonly database`**: genuinely **0** in
  all files.

**Interpretation.** The entire error surface is lock contention (`database is
locked` + its deferral/`BlockingIOError` derivatives) plus a cross-schema
`no such table`. No corruption, no disk-I/O fault, no readonly-write violation.

- The single largest live source is `zeus-price-channel-ingest.err` (10,004,
  recurring at 01:12:57 *during this audit*): `EDLI market-channel quote projection
  backpressure; socket retained pending={'lossless': 0, 'market': 1000}: database
  is locked` — the market backpressure queue is pinned at its 1000 cap. This process
  writes via `WriteCoordinator.lease(TRADE/WORLD)` (unified `.writer-lock`), colliding
  at SQLite with the trade/world writers that use the other three schemes.
- `no such table` is a distinct cross-DB defect (out of this lane's remit to
  root-cause): the dominant signature is `no such table: main.ensemble_snapshots`
  (`zeus-live.err` latest 2026-07-20 23:00:06; `zeus-post-trade-capital.err` ×5+) —
  `ensemble_snapshots` lives in `zeus-forecasts.db`, so a bare reference resolving
  against a `main` schema that is `zeus_trades.db` (no ATTACH) fails soft while
  silently degrading trade-lifecycle logging. Also `main.settlement_outcomes`
  (`zeus-forecast-live.err`), `calibration_pairs`, `alpha_overrides`
  (`zeus-ingest.err`, daily `drift_refit_arm`). Escalate to a provenance/cross-DB
  lane; plausibly the W5-8 fail-open `rwc` create producing an empty wrong-path DB.

---

## Summary (most-severe first)

1. **Write-intent locking is fragmented across four incompatible schemes**
   (`.writer-lock.live`, `.writer-lock.bulk`, unified `.writer-lock`, in-process
   `threading.Lock`) guarding the same three DBs; cross-scheme writers do not
   exclude and fall through to `busy_timeout` → the live `database is locked` storm
   (13,749 + 10,004 + 3,906, recurring at 01:12:57 today). `WriteCoordinator` is
   NOT a skeleton — it is the live third scheme; its own docstring prescribes the
   correct single-gate model it failed to migrate to.
2. **PASSIVE-checkpoint telemetry is false-green** (`db.py:744,775` →
   `main.py:5437,5470`): PASSIVE's `busy` is always 0, so the "reader pinning the
   floor" WARNING (`main.py:5442-5449,5475-5480`) is dead code; the real signal
   (`checkpointed < log`) is logged but never alerted on. Live trade WAL oscillates
   95→230→373 MB while the job prints "OK busy=0".
3. **Intended `TRUNCATE` shipped as `PASSIVE`** (`main.py:6948,6959` comment vs
   `db.py:742,774` impl): the `-wal` files never truncate, floating at 95-373 MB —
   the 2026-06-16 810 MB-incident regime the backstop was built to end.
4. **`synchronous` never set** → money-path writes run at default `FULL` under WAL;
   corruption-safe but an implicit durability policy and the top unused throughput
   lever (`NORMAL` recommended). **`wal_autocheckpoint` never set** (default 1000
   pages). **No forecasts-DB checkpoint backstop.**
5. **`_thread.interrupt_main()` targets the wrong thread** (`db_writer_lock.py:493`):
   BulkChunker runs under the ingest ThreadPool (`tigge_pipeline.py:379`), so its
   watchdog can inject `KeyboardInterrupt` into the `BlockingScheduler` main thread
   (daemon shutdown) while leaving the stuck BULK worker running.
6. **`ZEUS_DB_WRITE_CLASS` env-var race** (`db_writer_lock.py:992` vs docstring
   `:975-977`): `os.environ` is process-global, not thread-local; concurrent
   ThreadPool jobs stomp each other. Latent (write_class only counts today).
7. **RO-intent handles are writable** (`connection_pair.py:261-265`, admitted at
   `:256-259`); `_connect` fail-opens `rwc` and `mkdir`s the path
   (`db.py:249,259`), silently creating an empty DB on a wrong path — plausible
   source of the `no such table` cross-schema errors.
8. **No connection pool**; 336 fresh-connect sites across 43 files, each paying the
   full 5-6 PRAGMA setup (HYPOTHESIS on per-cycle count).
9. **Error surface is lock-only** — no corruption, no `SQLITE_IOERR`, no readonly-
   write, no `disk I/O` despite the 87%-full disk. Prior draft's "zero SQLITE_BUSY /
   IOERR" claims were substring artifacts (`sqlite_busy` token; `BlockingIOError`);
   corrected here.
