Verdict

High confidence, 0.91: reject the caller-by-caller union-lock bridge. The design that dominates it is a fleetwide dark compatibility deployment followed by a bounded, atomic, per-physical-DB quiescent-epoch handoff.

Your two central instincts are partly right:

Push enforcement down below the 336 call sites. Yes—but acquire at the write-transaction boundary, not for the lifetime of a connection.

Avoid a whole-fleet stop. Yes—but a brief per-DB write-admission fence is logically unavoidable. An already-running writer that opens no advisory lock cannot be made to respect any new advisory lock by bridge logic.

The union bridge is unsound for two independent reasons. Mutual exclusion is pairwise, not transitive: a migrated writer holding new+LIVE+BULK locks may exclude each old class individually, but an old LIVE writer and an old BULK writer still do not exclude each other, and an old no-lock writer conflicts with none of them. Worse, the pinned code already contains old-lock-then-new-lock nesting, while the proposed coordinator bridge would acquire new-lock-then-old-lock, creating same-thread deadlock and cross-process ABBA cycles. The coordinator’s existing one-file-per-DB principle is correct; the current implementation’s write_class is only metadata and its process lock is a non-reentrant threading.Lock. GitHub+2GitHub+2

There is no honest fully online rolling transition from unaware no-lock binaries. The actual semantic migration must switch all writers of one physical DB as one cohort. Code can be deployed incrementally and reversibly beforehand; runtime lock semantics cannot be changed writer by writer.

A literal reading of “mutual exclusion at every migration step” has one unavoidable implication: the current unsafe state cannot become safe without one quiescent instant. The dark deployment described below preserves existing behavior while preparing the fleet; the actual migration consists only of the atomic per-DB handoff. If even a bounded sub-200ms per-DB write-admission pause is prohibited, no advisory-lock design can satisfy the stated requirements.

Findings

[BLOCKER] migration safety — src/state/db.py:289-291,1710-1712; src/state/db_writer_lock.py:93-126; src/state/write_coordinator.py:135-143 — the union bridge cannot form total exclusion because legacy LIVE, legacy BULK, and old no-lock writers still lack one common admission edge — deploy a protocol-compatible binary everywhere, then switch all writers of one physical DB atomically under an exclusive epoch barrier. GitHub+1 — verify locally: reconcile rg -n 'sqlite3\.connect|apsw\.Connection' src scripts and lsof state/zeus_trades.db state/zeus-world.db state/zeus-forecasts.db against a service-manager PID, start-time, and binary-SHA manifest before permitting any cutover.

[BLOCKER] deadlock and ordering — src/ingest/price_channel_ingest.py:2661-2716; src/ingest_main.py:3111-3159; src/state/db.py:_GuardedWorldMutex.acquire — existing paths acquire the old world lock before WriteCoordinator.lease, so a coordinator that acquires .writer-lock before the old world/LIVE lock introduces G→L versus L→G ABBA, and reacquiring the non-reentrant world mutex in the same thread deadlocks immediately — replace both APIs with adapters into one reentrant lease token; never have the coordinator acquire legacy primitives underneath itself. GitHub+3GitHub+3GitHub+3

[HIGH] transaction lifecycle and reentrancy — src/state/write_coordinator.py:146-168,307-390; src/state/db.py:get_world_connection_with_trades_required — holding the gate for connection lifetime would starve writers on deliberately long-lived connections, while merely changing the coordinator lock to RLock would allow a second same-DB connection to self-contend inside SQLite — hold a structured lease token for exactly one SQLite write transaction, reuse the same connection for nested work, use a SAVEPOINT for same-connection nesting, and reject a second write connection or late DB-set expansion. The repository explicitly keeps one attached connection non-flocked because it lives across a long-running loop. GitHub+2GitHub+2

[HIGH] LIVE latency — src/state/write_coordinator.py:186-246,402-435; tests/state/test_write_coordinator.py:617-646 — the current unified gate deliberately makes LIVE time out behind a held BULK lease, and max_hold_ms only reports an overrun after release — add a cross-process LIVE-intent turnstile and bounded BULK transactions so a registered LIVE request prevents the next BULK chunk, while every actual write still takes the same .writer-lock. GitHub+1

[HIGH] cancellation safety — src/state/db_writer_lock.py:493 — _thread.interrupt_main() always schedules the interruption on the main thread, so a BULK watchdog running beside a worker can interrupt the scheduler or daemon control thread while leaving the offending transaction alive — remove interrupt_main; use cooperative deadlines, SQLite’s progress handler or targeted Connection.interrupt(), explicit rollback, and process isolation for operator BULK work. Never force-release the file gate while conn.in_transaction remains true. Python documentation+3GitHub+3Python documentation+3

[HIGH] connection-factory enforcement — src/state/db.py:_connect/get_connection — a transparent first-DML design that misses cursor methods, executemany, executescript, writable BLOB handles, cached prepared statements, or a transaction already opened can execute outside the gate or release it prematurely — return a coordinated Connection/Cursor implementation in SQLite autocommit mode, intercept every transaction and mutation surface, reject executescript outside an explicit coordinated transaction, and use a fail-closed authorizer with statement caching disabled or otherwise made token-independent. SQLite+5Python documentation+5Python documentation+5

[HIGH] connection initialization — src/state/db.py:269-322 — ordinary opens run the mode-setting PRAGMA journal_mode=WAL before any proposed transaction hook, creating an unnecessary pre-gate lock-bearing operation — set WAL once in fenced bootstrap, then have normal connection creation query and assert journal_mode=wal; WAL mode persists across connection closes and reopens. GitHub+1

[HIGH] world transaction composition — src/state/db.py:world_write_mutex/world_write_lock — at the pinned ref the world primitive is already a thread mutex plus the old cross-process .writer-lock.live flock, and world_write_lock commits even when the caller entered with an existing transaction, so treating it as a process-only lock or transparently nesting it can either deadlock or commit an outer unit unexpectedly — make it a compatibility façade over the central token and use a SAVEPOINT or reject a foreign existing transaction rather than committing it. GitHub+4GitHub+4GitHub+4

[HIGH] cross-file crash consistency — src/state/write_coordinator.py:248-305; AGENTS.md:6,33 — a common admission lease over two DBs must not be represented as one crash-atomic transaction, because WAL makes an attached multi-file transaction atomic per file but not across the files as a set — retain the coordinator’s independent-connection rejection, restrict multi-DB writes to the sanctioned ATTACH helpers, and use a durable outbox, saga, or single physical DB for any invariant requiring host-crash atomicity. SQLite+3GitHub+3GitHub+3

[HIGH] SQLite runtime dependency — runtime-linked SQLite library — current SQLite documentation records a rare WAL-reset corruption bug affecting releases through 3.51.2, with fixes in 3.51.3 and named backports 3.50.7 and 3.44.6; a live system with concurrent writers or checkpoints must not assume it is unaffected — verify every daemon and operator environment’s linked SQLite version, upgrade if affected, and route explicit checkpoint operations through the coordinator. SQLite — verify locally: in every service virtual environment run python -c 'import sqlite3; print(sqlite3.sqlite_version, sqlite3.connect(":memory:").execute("select sqlite_version()").fetchone()[0])'.

[MEDIUM] metadata propagation — src/state/db_writer_lock.py:992 — ZEUS_DB_WRITE_CLASS is process-global state and concurrent thread-pool jobs can overwrite and restore each other’s values despite the comment describing thread-local behavior — pass WriteClass and the lease token explicitly through the scheduler invocation; use task/thread context only as a nested-call convenience, never as the authoritative classification. GitHub

[MEDIUM] lock-namespace integrity — src/state/write_coordinator.py:366-390; src/state/db_writer_lock.py:115-126 — opening predictable lock files with O_CREAT and mode 0644 is safe only when the parent directory and path identity are trusted, and unlinking or replacing a held lock file can split writers across inodes — use an owner-only local directory, O_NOFOLLOW|O_CLOEXEC, fstat validation, canonical physical DB identity, and never unlink lock sentinels while any compatible or legacy process may be running. GitHub+1 — verify locally: stat -c '%U %G %a %F' state state/*.writer-* and findmnt -T state; the state directory must be a local filesystem with working flock, not NFS.

The design that should replace the union bridge

Use one actual writer gate and three narrowly defined control-plane primitives per physical DB:

G = <db>.writer-lock is the only data-writer exclusion gate. Every LIVE and BULK transaction takes it exclusively.

E = <db>.writer-epoch is a shared/exclusive protocol-transition barrier. Every compatible write scope holds it shared. Only the operator takes it exclusive during a cutover. It does not serialize ordinary writers against one another.

P = <db>.writer-live-intent is a shared/exclusive priority turnstile. It contains no data-locking authority: no transaction may write merely because it holds P; it must still hold G.

B = <db>.writer-bulk-owner may be used to limit each DB to one operator BULK job. It is optional admission metadata and is not the writer gate.

For multi-DB sanctioned helpers, the outermost operation must declare the complete physical DB set. Acquire all E locks in canonical DB order, then all scheduler primitives in canonical order, then all G locks in canonical order, and only then enter SQLite. Release in reverse order. Never expand the DB set after acquiring any gate.

Lock identity should be based on the three registered canonical databases and checked against (st_dev, st_ino), not derived freely from caller-provided path strings. This prevents a hard-link or alternate-path alias from creating a second gate for the same physical DB.

(a) Bringing the no-lock writers under the gate

Yes: push enforcement into the connection/transaction layer. No: do not acquire at connection creation and hold until connection close.

The repository itself gives the counterexample: get_world_connection_with_trades_required intentionally returns a long-lived attached connection without lifetime flocks because that would starve other writers. GitHub

The safe boundary is:

Acquire or join the lease before executing BEGIN IMMEDIATE.

Hold it while conn.in_transaction is true.

Release only after a successful COMMIT or ROLLBACK has made conn.in_transaction false, or after the connection has been forcibly closed and the transaction is known to be gone.

A failed COMMIT does not imply the transaction ended; SQLite documents that a BUSY COMMIT can leave it active. SQLite

The preferred implementation is a CoordinatedConnection plus CoordinatedCursor, created by _connect. Run the underlying connection in SQLite autocommit mode so Python cannot invisibly issue a DEFERRED transaction before the gate. The wrapper then preserves the existing caller-visible transaction grouping by explicitly issuing BEGIN IMMEDIATE on the first mutating statement and retaining the transaction until the caller commits or rolls back. Python’s default legacy behavior otherwise opens implicit transactions for DML, while executescript can commit a pending transaction before executing its script. Python documentation+1

The wrapper must cover:

Connection.execute, executemany, executescript, and connection context-manager exit.

Cursor.execute, executemany, and executescript.

Explicit BEGIN, top-level SAVEPOINT, COMMIT, ROLLBACK, and ATTACH/DETACH.

DDL, mutating PRAGMAs, VACUUM, REINDEX, and checkpoint requests.

blobopen(readonly=False).

Backup/restore or deserialize APIs capable of writing a canonical DB.

Trigger-induced writes.

A connection authorizer is valuable as a fail-closed tripwire because SQLite invokes it while compiling a statement and can reject the statement before execution. It cannot itself acquire the lock because authorizer callbacks must not modify their connection. The wrapper can recognize its own denial, acquire the lease, issue BEGIN IMMEDIATE, and prepare the statement again. SQLite

There are two important qualifications:

First, Python caches prepared statements by default. A statement compiled while the token is active could otherwise be reused after release without a fresh authorization decision. Use cached_statements=0 on coordinated write-capable connections unless the guard is designed so its authorization result never depends on token state. Python documentation

Second, SQLite supports only one authorizer per connection. The coordinated connection must own or explicitly compose that callback; application code must not be able to replace it silently. SQLite

An already-open DEFERRED/read transaction must not be upgraded by acquiring the coordinator late. SQLite explicitly permits a read-to-write upgrade to return BUSY after another connection has written. Reject such a write with a distinct UngatedWriteInActiveTransaction error and restart the application-level unit under a coordinated IMMEDIATE transaction. SQLite

Unknown write_class must not mean “no gate.” Outside an existing token, classify an otherwise-unlabelled write as LIVE during transition so it cannot be trapped behind BULK. Require operator/backfill entry points to declare BULK explicitly. True read-only access should use mode=ro plus query_only, not a write-capable connection with write_class=None.

Reentrancy rules

Do not solve reentrancy by replacing threading.Lock with threading.RLock. Use a process-global lease registry keyed by physical DB identity and an explicit token containing:

Owning thread or task identity.

Monotonic protocol generation.

Complete ordered DB set.

LIVE or BULK classification.

Held epoch, scheduler, and gate descriptors.

Recursion depth.

The active write connection for each DB.

Owner/call-site metadata.

A nested call may join only when it is in the same owner context and requests the same DB set or a subset. It increments depth without reopening lock files.

A nested write on the same connection uses a SAVEPOINT.

A nested request for a second connection to a DB whose first connection has an active write transaction is rejected. Reentering the advisory gate would let the second connection bypass the process guard, but SQLite would still see two separate connections and one writer lock, producing self-contention.

A request to add another physical DB after the outer token was acquired is rejected, even when the new DB would happen to sort later. The sanctioned ATTACH helpers must declare their entire DB set at the outer boundary.

Keep normal check_same_thread=True. If any connection deliberately disables it, token ownership and transfer must be explicit; a thread-local depth counter alone is insufficient.

(b) Why the union bridge fails, and the cleaner transition

The union bridge forms a star, not a clique:

Migrated G+L+B versus old L: excluded.

Migrated G+L+B versus old B: excluded.

Old L versus old B: not excluded.

Any of those versus old no-lock N: not excluded.

Existing L→G callers versus a proposed G→L bridge: deadlock-capable.

Canonical DB ordering does not repair a lock-kind inversion. It orders world/trade/forecast; it does not make old-world-live → unified-world compatible with unified-world → old-world-live.

The cleaner bridge is API compatibility plus an epoch barrier, not union acquisition.

Deploy a compatibility release in which every write path—legacy db_writer_lock, bulk_lock_with_chunker, world_write_mutex, current coordinator calls, and connection-factory writes—enters the same central lease engine. Initially, each API uses its existing legacy backend after first acquiring E shared:

Legacy LIVE remains on .writer-lock.live.

Legacy BULK remains on .writer-lock.bulk.

Existing coordinator users remain on .writer-lock.

No-lock connections still have no data-intent gate, but their write transaction holds E.

World compatibility paths preserve their current legacy lock profile.

This dark mode does not claim to cure the existing storm. Its purpose is to make every future write transaction visible to E, establish one lock-ordering implementation, and prove that every writer can obey a later epoch switch. No operator takes E exclusive while old binaries remain.

For paths that currently combine primitives, such as world mutex followed by coordinator, define one explicit legacy compatibility profile with the existing order. Do not permit generic nested lock-set expansion.

Once every writer-capable process is compatible, the operator changes the per-DB protocol generation under E exclusive. After release, every API selects G. No writer can straddle the choice because its full write scope holds E shared and reads the generation only after acquiring it.

Do not cache a protocol generation beyond release of E.

A post-cutover sentinel may continue holding the old LIVE and BULK lock files to block stale explicit-lock binaries. That is defense in depth only: it cannot stop a stale no-lock binary. The process-launch fence remains load-bearing.

Hard-linking or symlinking old and new lock names is not a replacement. Existing open file descriptors may reference the old inode, and no-lock writers remain unaffected. Changing the filesystem namespace safely would itself require quiescence.

(c) Preserving BULK-yields-to-LIVE on one gate

LIVE acquisition is:

Acquire E shared.

Acquire P shared, thereby registering LIVE intent.

Acquire G exclusive.

Execute BEGIN IMMEDIATE, the transaction, and COMMIT or ROLLBACK.

Release G, then P, then E.

Multiple LIVE writers may hold P shared while queueing at G.

Each BULK chunk is:

Acquire E shared.

Try to acquire P exclusive.

While holding P, try G nonblocking.

If G is unavailable, release P and E, back off, and retry. BULK must never block while holding P.

If G is acquired, release P immediately, then execute one bounded BEGIN IMMEDIATE…COMMIT chunk while holding G and E.

Release G and E. The next chunk must obtain P exclusive again.

This produces the desired ordering:

A LIVE arrival during a BULK chunk obtains P shared and queues at G.

When the current chunk releases G, the BULK job cannot start its next chunk because the LIVE holder prevents P exclusive.

All writes, regardless of class, still take the same exclusive G; P is scheduler metadata, not a second writer lane.

Continuous LIVE demand may starve BULK. That is the correct policy for a money-path priority law. Surface starvation to the operator rather than weakening LIVE priority.

The quantitative condition is:

p99(current BULK chunk G-hold) + p99(LIVE transaction) + scheduling/storage margin < 200 ms.

The current 50ms chunk target is a reasonable initial budget but is not presently an enforced gate-hold limit. GitHub Measure BEGIN-to-transaction-end, including COMMIT, not only loop CPU time.

There is no safe watchdog that can “release” G in the middle of an active SQLite transaction. Use:

Row and monotonic-time limits at chunk boundaries.

No network or unrelated disk I/O while G is held.

set_progress_handler to abort long SQLite VM execution when the chunk deadline or cancellation event is reached.

Connection.interrupt() only against the offending connection, followed by explicit rollback.

A dedicated subprocess for operator BULK jobs so a wedged job can be terminated without taking down the trading scheduler.

Progress callbacks and interrupt() do not guarantee interruption of every filesystem or fsync stall. Therefore the lock protocol guarantees that LIVE waits behind no more than the current BULK chunk; the <200ms property still requires measured storage and commit tails. If those tails cannot meet the formula, BULK must remain disabled while trading.

After the unified gate is stable, treat a write-side SQLITE_BUSY after acquiring G as an invariant alarm, not routine backpressure. Keep only a small bounded busy timeout appropriate to the remaining latency budget; do not retain 30 seconds as the scheduling mechanism. SQLite documents a few non-writer reasons WAL operations can still return BUSY, so telemetry should distinguish BEGIN contention from recovery/connection-close cases. SQLite

(d) Folding the world lock into the per-DB gate

At the pinned ref, world_write_mutex is not merely process-local. _GuardedWorldMutex.acquire first takes a non-reentrant threading.Lock, then flocks the old world .writer-lock.live, and it tracks thread-local held depth for the no-I/O antibody. GitHub+1

Do not place that object underneath WriteCoordinator.

Instead, make world_write_mutex() a compatibility façade:

In LEGACY_COMPAT, it enters the central token and invokes the current world legacy profile in the existing order.

In UNIFIED_V1, it enters or joins the WORLD token, which obtains E, P if LIVE, and G. It does not obtain the old world lock.

world_mutex_is_held and assert_no_world_mutex_held_for_io should remain, but their truth should derive from central token depth. They remain semantic guards against holding the write scope over HTTP or chain RPC; they cease being an independent serialization mechanism.

world_write_lock(conn) must change its nested behavior. If the same coordinated connection already has the token, create a SAVEPOINT. If the connection has an unrelated existing transaction, fail closed. Do not commit a transaction that the helper did not open; the current implementation explicitly does that. GitHub

Ordered implementation and operator sequence

0. Immediate incident containment

Disable all BULK and backfill entry points for the three canonical DBs. Block ad hoc script launches except through an operator-controlled launcher. Do not add more isolated scheme-3 call sites while the protocol is being built.

This does not solve no-lock LIVE contention, but it removes avoidable long transactions and makes the handoff latency achievable.

1. Establish the protocol floor in code

Add the physical-DB registry, lease-token registry, E/G/P primitives, monotonic per-DB protocol state, and canonical layered ordering to write_coordinator.py.

Retain CrossDatabaseTransactionUnsupported for independent connections. Admission leases may cover multiple DBs for sanctioned ATTACH operations, but transaction() remains single-physical-DB.

Use a durable state such as:

LEGACY_COMPAT(generation=n)

ARMED(generation=n+1)

QUIESCING(generation=n+1)

UNIFIED_V1(generation=n+1)

UNIFIED_LIVE_ONLY(generation=n+2) as an emergency rollback mode that still uses G

Persist state with write-to-temp, file fsync, atomic rename, and parent-directory fsync. Generations only increase.

2. Refactor callers in this code order

First, convert the sanctioned multi-DB/ATTACH helpers. They must declare the complete DB set before acquiring any lock and reuse one attached connection.

Second, convert the known world-old-lock→coordinator nesting in price_channel_ingest.py and ingest_main.py to one central compatibility profile. This removes the ABBA edge before any new ordering is activated.

Third, turn world_write_mutex and world_write_lock into token façades while preserving their legacy backend in LEGACY_COMPAT.

Fourth, install the coordinated connection/cursor path in _connect and get_connection. This is what captures the no-lock population by construction.

Fifth, make legacy db_writer_lock, existing WriteCoordinator.lease, and other explicit wrappers adapters to the same engine. Remove the environment-based write-class propagation.

Sixth, replace BulkChunker’s lock handling and watchdog with the P/G chunk protocol. Keep BULK runtime-disabled.

These may be separate worktree commits, but they should deploy as a coherent compatibility artifact in which E is always the outermost coordination layer. A partial release that allows G→E in one path and E→G in another is not deployable.

3. Prove the compatibility artifact before activation

Required deterministic tests include:

Every Connection and Cursor mutation surface acquires the token before SQLite starts a write transaction.

Mutating CTEs, triggers, DDL, PRAGMAs, executescript, and writable BLOBs cannot bypass the token.

Cached statements cannot bypass the authorizer state.

COMMIT BUSY does not release G while in_transaction remains true.

Close and rollback release exactly once.

Nested same-connection writes use SAVEPOINT; second same-DB connections and DB-set expansion are rejected.

Mixed legacy API profiles preserve the old lock order in LEGACY_COMPAT.

Multi-process mixed APIs all share G after UNIFIED.

Process death releases E/P/G; inject death before state persistence, after persistence, during BEGIN, and during rollback.

A LIVE request arriving during a BULK chunk obtains G before the next BULK chunk.

Path aliases and hard links cannot produce separate gate identities.

Sanctioned ATTACH helpers obey canonical ordering without any cross-file crash-atomicity assertion.

Run these against the same local filesystem type as production. Thread-only tests are insufficient because the migration depends on flock and process death.

4. Deploy dark to every writer-capable process

Deploy and restart every daemon, worker, and operator environment with all DBs still in LEGACY_COMPAT.

Every process must publish:

Protocol version.

Binary SHA.

PID and process start identity.

DBs it may write.

Supported protocol generations.

Count of active coordinated write scopes.

Count of denied or observed raw/uncoordinated mutations.

The launcher must reject old operator scripts. Do not cut over merely because no incompatible process currently has a DB file open; an idle stale process could open or write later.

Post-cutover rollback requires a protocol-compatible N−1 binary. Therefore do not set the protocol floor until at least two deployable versions understand UNIFIED and will continue using G if rolled back.

5. Cut over per DB—not per writer

Recommended order is forecasts first, world second, trades last.

Forecasts is the canary for the connection wrapper and settlements/calibration paths without starting on the direct execution ledger. World is second because it exercises the special world façade and price-channel paths. Trades is last because positions/orders/execution are the most direct money boundary. This order assumes forecast writes are not currently the highest-latency money dependency; change the canary DB if measured criticality says otherwise.

For each DB:

Confirm BULK disabled and the process-launch fence active.

Set ARMED(n+1) and require an ACK from every process that may write that DB.

Enter QUIESCING: compatible processes stop admitting new write transactions for that DB, queue LIVE work, and allow existing holders of E shared to finish.

Apply a hard admission-pause deadline calculated as 200ms - p99(LIVE own transaction) - margin. If drain cannot complete within that budget, abort, resume admission, and do not alter the protocol generation.

Acquire E exclusive.

Acquire G exclusive nonblocking. Failure indicates a leaked or incompatible holder; abort.

Open a dedicated raw connection with no mode-setting PRAGMAs and busy_timeout=0, then execute an empty BEGIN IMMEDIATE.

If BEGIN is BUSY, an uncoordinated or straddling writer exists. ROLLBACK if necessary, release G/E, leave the DB in LEGACY_COMPAT, and identify the process.

While the empty SQLite write transaction and E/G are held, durably persist UNIFIED_V1(n+1).

ROLLBACK the empty transaction and release G and E.

Resume admission. Every newly admitted write reads generation n+1 while holding E and therefore takes G.

The empty BEGIN IMMEDIATE proves no residual writer is active at that instant because SQLite permits only one write transaction. It does not prove that an idle stale binary cannot write later; that is why capability inventory and the launch fence are mandatory. SQLite

The handoff is crash-safe without pretending the mode file and DB are one atomic transaction:

Crash before the mode state is durably replaced leaves the old generation.

Crash after replacement leaves the new generation.

The empty DB transaction contains no data changes and rolls back when the connection dies.

File locks release on process death.

Both durable mode outcomes are valid; there is no cross-file money-state commit to coordinate.

WAL readers can continue during this per-DB writer handoff. WAL permits readers alongside a writer, although all participating processes must be on the same host. SQLite

6. Prove each DB before moving on

First run a LIVE-only canary. Require:

Correct generation from every writer process.

Zero stale-generation admissions.

Zero authorizer denials from expected application traffic.

No cross-writer BEGIN IMMEDIATE BUSY after G.

LIVE gate-wait and end-to-end write p99 below 200ms.

No process holding a legacy lock in the active generation.

Then run one bounded BULK canary. Inject a LIVE request after the BULK transaction has acquired G. Prove that:

LIVE waits behind at most the current chunk.

The next BULK chunk cannot acquire P while LIVE intent exists.

Observed BULK G-hold plus LIVE own transaction remains below 200ms at the required percentile.

Cancellation rolls back before releasing G.

Only after both canaries pass should the operator authorize the next physical DB.

Useful mandatory metrics are write_epoch, write_gate_wait_ms, write_gate_hold_ms, live_intent_wait_ms, bulk_chunk_hold_ms, ungated_write_denied_total, stale_epoch_denied_total, and sqlite_busy_after_gate_total, all labelled by physical DB and owner.

7. Cleanup

After all DBs have remained stable for the rollback observation window:

Remove legacy lock acquisition internals from active modes.

Retain legacy API adapters so an N−1 compatible binary still routes to G.

Retain the old lock files themselves; do not unlink them during a live fleet.

Keep direct-connect and authorizer guards permanently.

Keep operator BULK disabled by default and enable it per DB only through the priority-aware launcher.

Rollback semantics

A rollback to the original fragmented behavior is not allowed after a DB reaches UNIFIED, because that would violate the same mutual-exclusion invariant the migration established.

Rollback has three forms:

Before durable generation persistence, simply abort the handoff. No semantic change occurred.

After persistence, move to a higher generation such as UNIFIED_LIVE_ONLY(n+2) under the same E-exclusive handoff. This mode keeps all writers on G, disables BULK, and may disable the new priority implementation while retaining safe serialization.

Roll back application binaries only to a version that understands the protocol floor and continues to honor E/G. That is why a compatible N−1 must exist before the first cutover.

For an emergency connection-wrapper defect, fence all but one coordinator-compatible writer service for the affected DB and keep other processes read-only. Do not restore unaware direct writers.

This is reversible at the implementation and per-DB rollout level, but the move from fragmented admission to one common gate is intentionally a one-way safety floor. Monotonic generations prevent ABA and stale cached-mode reuse.

Safety argument

Before handoff, the DB remains in the supplied legacy state; the dark deployment does not claim otherwise.

During handoff, E exclusive means no compatible write scope is active or can begin. The empty BEGIN IMMEDIATE verifies no residual active SQLite writer. The process/launch fence prevents an idle stale binary from appearing afterward.

After handoff, every mutation surface and compatibility API takes the same exclusive G, so mutual exclusion is pairwise and complete.

No writer straddles protocol generations because E is held for its complete write scope and mode is read after acquiring E.

Deadlock is prevented by complete DB-set declaration, canonical DB ordering, one global lock-layer order, no late expansion, and no legacy locks beneath the active unified coordinator.

LIVE priority is preserved because LIVE registers on P before waiting for G, and a BULK job cannot start another chunk while that intent exists.

INV-37 is preserved because a multi-DB lease is admission coordination, not transaction atomicity. Independent connections remain forbidden; sanctioned ATTACH operations use one connection but are not described as host-crash atomic under WAL. GitHub+1

Strongest alternative considered

The strongest long-term alternative is a single-writer broker/actor per physical DB over a Unix-domain socket, with a strict priority queue and all write SQL executed by the broker. It would centralize connection ownership, scheduling, observability, reentrancy, and BULK preemption policy more cleanly than advisory locks.

I do not recommend it as the incident cutover. It is a materially larger rewrite, adds a new critical daemon and RPC failure boundary, and still requires the same per-DB quiescent handoff because an old no-lock process can continue opening SQLite directly. It is a credible post-stabilization architecture.

A whole-fleet stop is the simplest proof but unnecessarily broad. The bounded per-DB epoch handoff achieves the required proof while leaving WAL readers and writers of the other two DBs operational.

The strongest argument for the proposed union bridge is that it avoids even a short admission pause. It fails because no advisory lock can affect code that never opens it. Even if the explicit lock portion were changed to a deadlock-free old-first order, the no-lock population still forces an atomic compatibility handoff; once that handoff exists, the union adds complexity and latency without providing additional safety.

Sources, unknowns, and load-bearing assumptions

I used the pinned implementations of src/state/write_coordinator.py, src/state/db_writer_lock.py, src/state/db.py, src/ingest/price_channel_ingest.py, src/ingest_main.py, tests/state/test_write_coordinator.py, root AGENTS.md, and architecture/invariants.yaml. I also used the official SQLite WAL, transaction, and authorizer documentation and the official Python sqlite3 and _thread documentation. Python documentation+12GitHub+12GitHub+12

I did not independently reproduce the supplied live-log counts, the exact 336-site total, the production process inventory, the linked SQLite versions, or the production state-directory filesystem and permissions.

The load-bearing assumptions are that all writers are on one host with reliable flock; every writer-capable daemon and operator script can be upgraded, restarted, or launch-fenced; the coordinated connection path can cover every canonical-DB mutation surface; sanctioned cross-DB paths can declare their full DB set; and active LIVE transactions can drain plus complete the mode handoff within the remaining 200ms budget.

The smallest fact that would change the verdict is the existence of a lower-level write-admission mechanism already traversed by every current writer—such as an already-loaded custom SQLite VFS—or, in the opposite direction, an unfenceable process that may continue writing a canonical DB. In the latter case, no safe advisory online cutover exists: that process must be stopped or denied write access before the DB can be unified.