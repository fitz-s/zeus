# W1 writer-unification cutover — design of record (two independent reviews reconciled)

Lifecycle: created=2026-07-21; last_reviewed=2026-07-21. Unify the four write-intent lock
schemes + the ~336 no-intent-lock direct-connection writers onto one gate per physical DB, on
the 24/7 live-money fleet. This version reconciles TWO independent adversarial reviews of the
initial union-bridge design (`writer_unification_context.md`, scratchpad):
- **opus `cutover-critic`** (local) — found the two deadlocks; judged the bridge GO after fixes.
- **GPT-5.6 Pro consult** `REQ-20260721-204133` — full verdict in
  `consult_writer_unification_verdict.md` (492 lines). Confirmed every tactical finding AND
  **dominated the strategy: it rejects the union bridge as unsound and replaces it** with a
  fleetwide dark compatibility deployment + a bounded per-physical-DB quiescent-epoch handoff.

**The consult dominates and is the design of record.** The critic's "bridge GO after fixes" is
superseded: fixing the deadlocks makes the bridge deadlock-free but still does not make the
system safe (below). Both reviews AGREE on every tactical finding.

## Verdict: REJECT the union bridge. Adopt dark-deploy + per-DB epoch handoff.

**Why the union bridge is unsound (the insight the critic missed) — "a star, not a clique":**
a migrated writer holding {`.writer-lock` + `.writer-lock.live` + `.writer-lock.bulk`} excludes
old-LIVE (shared `.live`) and old-BULK (shared `.bulk`) *individually*, but **old-LIVE and
old-BULK still do not exclude each other** (different files), and the **~336 no-lock writers
hold nothing so they exclude nobody**. Mutual exclusion is pairwise, not transitive — the bridge
forms a star centered on migrated writers, never a complete clique. And **no advisory lock can
affect code that never opens it**, so the no-lock population cannot be brought in by any bridge;
it can only be brought in by pushing enforcement into the connection layer AND switching that
DB's whole writer cohort at once. Conclusion: there is no honest fully-online rolling transition
from unaware no-lock binaries. Code deploys incrementally (dark); **runtime lock semantics must
switch atomically per physical DB**, under a brief (<200ms) admission pause. A literal
"mutual-exclusion-at-every-step" with a live no-lock population is impossible without one
quiescent instant per DB.

## Tactical findings both reviews confirmed (all verified against code)
- **C1 deadlock** — folding the world in-process lock into the union deadlocks: `_GuardedWorldMutex.acquire()` takes the in-process `threading.Lock` THEN the world `.writer-lock.live` flock (db.py:526-535); plus `price_channel_ingest.py:2661-2716` / `ingest_main.py:3111-3159` already acquire old-world-lock BEFORE the coordinator → G→L vs L→G ABBA. **Fix:** never place a legacy primitive under the coordinator; scheme-4 is already subsumed by the world `.live` flock.
- **C2 re-entrancy** — the coordinator gate is non-reentrant on two axes (per-DB `threading.Lock` write_coordinator.py:166-168; fresh `os.open`+flock per lease → separate OFD self-blocks), and flocked helpers open a connection inside their locks (db.py:898, 1231). **Fix:** a re-entrant lease token (below), landed BEFORE any pushdown.
- **M1** — `_connect` cannot tell reads from writes (db.py:289-291); 73/154 `get_*_connection` sites pass no write_class; a first-DML hook also misses executemany/executescript/BLOB/cached-statements/already-open transactions. Write intent must be explicit + the interception complete.
- **M2** — migrate per-daemon-restart, not per-writer (one process = one code version); `deploy_live.py restart all` is rolling.
- **M3** — BULK-yield needs a separate LIVE-intent signal; flock is not FIFO. (The consult's P/G turnstile, below, is the precise mechanism.)
- **interrupt_main watchdog** (db_writer_lock.py:493) is unsafe (interrupts the main/scheduler thread, leaves the stuck txn alive); never force-release a file gate while `conn.in_transaction`.

## New findings the consult added (beyond the critic)
- **[HIGH] SQLite WAL-reset corruption bug** affects releases ≤3.51.2 (fixed 3.51.3; backports 3.50.7, 3.44.6). **CHECKED:** the worktree `.venv` SQLite is **3.53.2 — unaffected**. Operator must confirm the LIVE daemon + operator environments' linked SQLite ≥3.51.3 before cutover (likely the same .venv=3.53.2). Route explicit checkpoints through the coordinator.
- **[HIGH] connection init** — `PRAGMA journal_mode=WAL` runs on every open before any gate hook = a pre-gate lock-bearing op. Set WAL once in fenced bootstrap; normal opens assert `journal_mode=wal` (WAL persists across reopens).
- **[MEDIUM] lock-namespace integrity** — key lock/DB identity by `(st_dev, st_ino)`, NOT caller path strings (a hard-link/alias would mint a second gate for one physical DB); O_NOFOLLOW|O_CLOEXEC, owner-only dir; never unlink sentinels while any compatible/legacy process runs; require the state dir on a local fs with working flock (not NFS). **CHECKED:** state dir is local APFS.
- **[MEDIUM] write-class env race** — `ZEUS_DB_WRITE_CLASS` process-global (db_writer_lock.py:992) races under the ThreadPool; pass class+token explicitly, never via env.

## The design of record — one gate + three control primitives per physical DB
- **G = `<db>.writer-lock`** — the ONLY data-writer exclusion gate; every LIVE and BULK write transaction takes it exclusively. (Keeps the coordinator's correct one-gate-per-DB principle.)
- **E = `<db>.writer-epoch`** — shared/exclusive protocol-transition barrier; every compatible write scope holds it SHARED; only the operator takes it EXCLUSIVE during a cutover. Does not serialize ordinary writers.
- **P = `<db>.writer-live-intent`** — shared/exclusive LIVE-priority turnstile; NO data-locking authority (holding P never authorizes a write; you still take G).
- **B = `<db>.writer-bulk-owner`** — optional: one operator BULK job per DB.
Lock-layer order for a multi-DB sanctioned helper (declare the COMPLETE physical-DB set at the outer boundary; never expand later): all E in canonical DB order → all P/scheduler → all G → enter SQLite; release reverse. Retain `CrossDatabaseTransactionUnsupported` — a multi-DB lease is admission coordination, NOT cross-file crash atomicity (INV-37 preserved).

**CoordinatedConnection/CoordinatedCursor (captures the 336 no-lock writers by construction):**
`_connect` returns a wrapper on a connection in SQLite **autocommit mode**; the wrapper issues
`BEGIN IMMEDIATE` on the first mutating statement, joins/acquires the lease for exactly that ONE
write transaction, holds while `conn.in_transaction`, releases only after COMMIT/ROLLBACK makes
it false (a BUSY COMMIT can leave the txn active — do NOT release then). Intercept EVERY mutation
surface: execute/executemany/executescript, cursor variants, explicit BEGIN/SAVEPOINT/COMMIT/
ROLLBACK/ATTACH/DETACH, DDL, mutating PRAGMAs, VACUUM/REINDEX/checkpoint, `blobopen(write)`,
backup/deserialize, trigger-induced writes. A fail-closed **authorizer** is the tripwire (it
can't take the lock — authorizer callbacks must not mutate their connection; the wrapper catches
its own denial, takes the lease, re-prepares). Set `cached_statements=0` on write-capable
coordinated connections (else a statement compiled under the token reuses after release). An
already-open DEFERRED/read txn must NOT be upgraded late (SQLite returns BUSY on read→write after
another writer) — raise `UngatedWriteInActiveTransaction` and restart the unit under IMMEDIATE.
Unknown write_class ≠ "no gate": classify unlabelled writes as LIVE during transition; require
BULK to be declared; true read-only uses `mode=ro`+`query_only`, not a write handle.

**Re-entrancy = a process-global lease-token registry (NOT RLock, NOT thread-local depth alone).**
Keyed by physical DB identity `(st_dev,st_ino)`. Token carries: owner thread/task, monotonic
protocol generation, complete ordered DB-set, LIVE/BULK class, held E/P/G descriptors, recursion
depth, the active write connection per DB, owner/call-site. A nested call joins ONLY when
same-owner + DB-set is the SAME or a SUBSET (increments depth, reopens no lock files). Nested
write on the same connection = SAVEPOINT. A SECOND connection to a DB with an active write txn =
REJECTED (advisory re-entry would let the 2nd connection bypass the guard while SQLite still sees
two connections + one writer lock → self-contention). Adding a DB after the outer token = REJECTED.
Keep `check_same_thread=True` (thread-local depth alone is insufficient if it's ever disabled).
[The `lease-reentrancy` agent's thread-local per-DB depth is the depth-tracking CORE of this
token; the registry keying + connection tracking + SAVEPOINT/second-connection rules extend it.]

**BULK-yields-to-LIVE on one gate (the P/G turnstile):**
- LIVE: E-shared → P-shared (registers intent) → G-exclusive → BEGIN IMMEDIATE…COMMIT → release G,P,E. Many LIVE hold P-shared while queued at G.
- BULK chunk: E-shared → try P-EXCLUSIVE → while holding P try G nonblocking → if G unavailable release P+E and back off (never block holding P) → if G acquired release P immediately, run ONE bounded BEGIN IMMEDIATE…COMMIT chunk under G+E → release. Next chunk must re-take P-exclusive.
- Result: a LIVE arrival during a BULK chunk takes P-shared + queues at G; when the chunk frees G, BULK cannot start the next chunk (LIVE holds P-shared, blocking P-exclusive). All writes still take the same exclusive G; P is scheduler metadata. Continuous LIVE may starve BULK — correct money-path policy; surface to operator. Quantitative gate: p99(BULK chunk G-hold) + p99(LIVE txn) + margin < 200ms (measure BEGIN→COMMIT, not loop CPU). No watchdog can release G mid-transaction — use chunk-boundary row/time limits, `set_progress_handler`, `Connection.interrupt()` on the offending connection + rollback, and a subprocess for operator BULK.

**World primitive → compatibility façade** over the central token (not placed under the
coordinator): LEGACY_COMPAT enters the token + runs the legacy world profile in the existing
order; UNIFIED_V1 joins the WORLD token (E, P-if-LIVE, G) and does NOT take the old world lock.
`world_write_lock(conn)`: if the coordinated connection already holds the token → SAVEPOINT; if
it has an unrelated open txn → fail closed; never commit a txn the helper did not open (it does
today — db.py:699).

## Operator sequence (7 phases; the actual migration is only the per-DB handoff)
0. **Contain**: disable all BULK/backfill entry points for the 3 DBs; block ad-hoc script launches except via an operator launcher; add no new scheme-3 sites.
1. **Protocol floor in code**: physical-DB registry (`(st_dev,st_ino)`), lease-token registry, E/G/P primitives, monotonic per-DB generation with a durable state machine (LEGACY_COMPAT → ARMED → QUIESCING → UNIFIED_V1; UNIFIED_LIVE_ONLY as emergency rollback), persisted write-temp→fsync→atomic-rename→dir-fsync, generations only increase. Retain single-DB `transaction()`.
2. **Refactor callers in this order**: sanctioned ATTACH helpers first (declare full DB-set, one attached connection) → the world-lock→coordinator nesting in price_channel_ingest/ingest_main to one compat profile (kills the ABBA edge) → world_write_mutex/lock → façades → install the coordinated connection in `_connect`/`get_connection` (captures the no-lock population) → make legacy `db_writer_lock`/`WriteCoordinator.lease` adapters to the one engine, drop env write-class → replace BulkChunker with the P/G protocol (keep BULK disabled). Deploy as ONE coherent artifact where E is always outermost (a partial G→E-in-one-path / E→G-in-another release is NOT deployable).
3. **Prove the compatibility artifact** (fixtures on the production fs type, not thread-only): every mutation surface takes the token before SQLite starts a write; CTEs/triggers/DDL/PRAGMA/executescript/BLOB can't bypass; cached statements can't bypass the authorizer; COMMIT-BUSY doesn't release G while in_transaction; close/rollback release exactly once; nested same-conn=SAVEPOINT, 2nd-same-DB-conn + DB-set-expansion rejected; mixed legacy APIs keep old order in LEGACY_COMPAT; multi-process mixed APIs share G after UNIFIED; process death releases E/P/G (inject death before/after persistence, during BEGIN, during rollback); a LIVE arrival during a BULK chunk gets G first; path aliases/hard-links don't split gate identity; ATTACH helpers obey ordering with no cross-file crash-atomicity claim.
4. **Deploy dark** to every writer-capable process (all DBs LEGACY_COMPAT); each process publishes protocol version, binary SHA, PID/start-identity, writable DBs, supported generations, active-coordinated-scope count, denied/observed-raw-mutation count. Launcher rejects old operator scripts. Require a protocol-compatible N−1 binary to exist before the first cutover (rollback needs it).
5. **Cut over per DB** (forecasts → world → trades; forecasts is the connection-wrapper canary, trades last = most direct money boundary): confirm BULK disabled + launch fence; ARM(n+1) + require ACK from every writer of that DB; QUIESCING (stop admitting new writes, queue LIVE, let E-shared holders finish) under a hard pause = 200ms − p99(LIVE txn) − margin (abort+resume if drain overruns, do NOT bump generation); E-exclusive; G-exclusive nonblocking (failure = leaked/incompatible holder → abort); open a raw connection (no mode PRAGMAs, busy_timeout=0), empty `BEGIN IMMEDIATE` (proves no residual writer — SQLite allows one write txn; BUSY = a straddling writer → abort, stay LEGACY_COMPAT, identify the process); while E/G + the empty txn are held, durably persist UNIFIED_V1(n+1); ROLLBACK the empty txn, release G/E; resume admission (new writes read n+1 under E → take G). Crash-safe: crash before persist = old gen, after = new gen, the empty txn rolls back on death, locks release on death — no cross-file commit to coordinate. WAL readers continue throughout.
6. **Prove each DB**: LIVE-only canary (correct generation from all writers, zero stale-gen admissions, zero unexpected authorizer denials, no cross-writer BEGIN-IMMEDIATE-BUSY after G, LIVE p99 < 200ms, no legacy-lock holder in the active gen) → one bounded BULK canary (inject LIVE after BULK holds G; prove LIVE waits ≤ current chunk, next chunk can't take P while LIVE intent exists, BULK-G-hold+LIVE-txn < 200ms, cancellation rolls back before releasing G). Metrics: write_epoch, write_gate_wait_ms, write_gate_hold_ms, live_intent_wait_ms, bulk_chunk_hold_ms, ungated_write_denied_total, stale_epoch_denied_total, sqlite_busy_after_gate_total (per DB + owner). Only then authorize the next DB.
7. **Cleanup** (after a rollback observation window): remove legacy-lock acquisition from active modes; RETAIN legacy API adapters (an N−1 binary still routes to G) + the old lock files (don't unlink on a live fleet) + the direct-connect/authorizer guards permanently; keep operator BULK disabled by default (per-DB via the priority launcher).

**Rollback**: before persistence = abort (no change); after = move to a HIGHER generation (UNIFIED_LIVE_ONLY(n+2), still G, BULK off) under the same E-exclusive handoff; app-binary rollback only to a version that honors E/G. The move from fragmented → one gate is a one-way safety floor (monotonic generations prevent ABA / stale cached-mode reuse). Emergency wrapper defect: fence all but one coordinator-compatible writer for that DB, others read-only; never restore unaware direct writers.

**Strongest alternative** (consult, not recommended for the incident): a single-writer broker/actor per DB over a Unix socket — cleaner long-term, but a larger rewrite + a new critical daemon/RPC boundary, and STILL needs the same per-DB quiescent handoff (an old no-lock process can open SQLite directly). Credible post-stabilization architecture.

**The smallest fact that flips the verdict** (consult): a lower-level admission mechanism every writer already traverses (e.g. a loaded custom SQLite VFS) — then simpler; OR an UNFENCEABLE process that may keep writing a canonical DB — then NO safe online cutover exists and that process must be stopped/denied write first. Operator must confirm neither holds.

## Status
This is the largest W1 item and a **large operator-driven build** (7 phases, per-DB fenced
handoff), now validated by TWO independent reviews that converged on every tactical finding and
supplied a dominant architecture. The `lease-reentrancy` primitive (in flight) is the depth core
of phase-1's lease-token registry. W5-8 fail-open connect (`must_exist`) folds into the phase-2
`_connect` coordinated-connection work. Implementation ships worktree-authored + fixtured +
operator-gated per the phase sequence; it is NOT a blind flip and NOT a caller-by-caller bridge.
Raw consult verdict (authoritative source): `consult_writer_unification_verdict.md`.
