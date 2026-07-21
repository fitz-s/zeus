> **编辑注(Zeus 法)**:本文是外部 GPT-5.6 consult 的 verbatim 记录。其无下线切换段落的 "Shadow-read and compare" 提议常驻并行读比对——**Zeus 已根除 shadow modes**(operator 指令 2026-06-12,root AGENTS §2)。落地**删除该步、不改名保留**:切换正确性由 outbox 幂等 + 对账 query(一次性/读态,见 D 的 atomicity_outbox_contract)+ tests 兜底,live-direct 切,不实现常驻并行分层。

Verdict

Do not run a fleet-wide dbstat census, full ANALYZE, VACUUM, page-size change, or blocking checkpoint against these live files. The immediate problem is not that SQLite has crossed a nominal size limit. It is that three separate risks have accumulated in one failure domain:

Correctness/control-plane gaps: WAL-version exposure, non-atomic cross-database commits, incomplete writer enrollment, platform-dependent legacy locking, and fail-open path creation.

Observability and planner debt: no sqlite_stat1, misleading checkpoint health logic, no oldest-reader visibility, and no per-object write-amplification measurements.

A storage architecture mismatch: small mutable state and unbounded append-only history share the same pager, cache, checkpoint, backup, and compaction lifecycle.

The recommended end state is:

retain SQLite for small, authoritative, single-writer hot state;

move append-heavy histories into bounded epoch files, with old epochs sealed read-only;

optionally convert cold analytical epochs to Parquet queried through DuckDB;

use one unified per-database writer coordinator—or preferably one writer actor/queue per database—not independent LIVE and BULK advisory locks;

replace cross-file “atomic” operations with either co-location in one SQLite database or an authoritative transaction plus a durable outbox and reconciliation.

Confidence: high, about 0.9, on the source review and SQLite-internals conclusions; medium-high, about 0.75, on the exact operational urgency because the production SQLite source ID, WAL generation rates, query corpus, reader ages, APFS snapshot inventory, and durability pragmas were not available.

The strongest counterargument is legitimate: 40–100 GB is not itself too large for SQLite, and SQLite explicitly supports an application-specific server that serializes requests and shards data. A move to PostgreSQL would introduce service, network, deployment, and failover failure modes that the current embedded architecture avoids. That counterargument wins for the hot state and authoritative trade ledger. It does not justify leaving unbounded cold append data mixed into those files when the volume is 87% full and routine maintenance requires reading or rebuilding tens of millions of pages. SQLite+1

What the repository actually establishes

The standard connection path is fail-open for filename errors: _connect creates the parent directory and calls ordinary sqlite3.connect(path), which creates a missing database. It then requests WAL but does not check the returned journal mode. Both normal and read-only factories configure a 1 GiB pager-cache target and a 32 GiB mmap cap per connection. The read-only factory correctly uses mode=ro, but the normal factory should use mode=rw after an explicit provisioning step so a dash/underscore typo cannot silently initialize another complete schema. GitHub+2GitHub+2

write_class does not itself serialize a connection. Mutual exclusion exists only where a caller explicitly enters db_writer_lock(...). The legacy layer deliberately uses distinct .writer-lock.live and .writer-lock.bulk files, so LIVE and BULK do not exclude each other at that layer; they ultimately contend on SQLite’s one WAL writer lock. The module’s own comments also identify production raw-connection exceptions. GitHub+2GitHub+2

The repository already contains the correct direction: WriteCoordinator uses one shared gate per database for both classes, adds an in-process threading.Lock, preserves canonical multi-database lock order, and explicitly refuses to claim that independent database transactions become cross-file atomic. It is described and tested as an initial skeleton, not as a completed production migration. Complete this implementation rather than adding a third lock abstraction. GitHub+2GitHub+2

The old scheduler helper sets process-wide os.environ to convey a write class while describing the behavior as thread-local. Concurrent scheduled jobs can overwrite that value. BulkChunker also uses _thread.interrupt_main(), which targets the main interpreter thread rather than necessarily the worker running the SQL transaction. The existing watchdog test intentionally catches KeyboardInterrupt in pytest’s main thread and therefore does not prove correct behavior under the actual scheduler executor. GitHub+2GitHub+2

The typed connection layer is not yet an enforcement boundary. ConnectionPair obtains a read/write world connection for what comments call read-only intent, and the triple returns three read/write handles. Some comments and the README imply ATTACH-based atomicity, but SQLite in WAL mode guarantees atomicity only within each attached database, not across the attached set after a host or power failure. db.py itself contains the more accurate warning. SQLite+5GitHub+5GitHub+5

The ownership manifest and source comments document real ghost/misplaced-table hazards, including same-named tables with materially different populations in different files. This makes unqualified SQL after ATTACH, path aliases, and stale manifest labels correctness risks rather than housekeeping problems. GitHub+3GitHub+3GitHub+3

A. First-principles audit direction: failure modes and discriminating measurements

P0: correctness and recoverability

1. A production SQLite build in the WAL-reset corruption window. SQLite documents a rare corruption race affecting WAL databases with multiple connections and concurrent write/checkpoint activity in versions through 3.51.2, fixed in 3.51.3 with backports to 3.50.7 and 3.44.6. This fleet matches the relevant topology closely enough that the runtime build is the first discriminator, ahead of any deliberate checkpoint or scan work. Query the exact sqlite_source_id() from every production Python interpreter and subprocess; the system sqlite3 CLI is not evidence about the library linked into Python. SQLite

Bash

/path/to/production/python - <<'PY'
import sqlite3

con = sqlite3.connect(":memory:")
print("python sqlite3 module:", sqlite3.version)
print("SQLite runtime:", sqlite3.sqlite_version)
print("source id:", con.execute("SELECT sqlite_source_id()").fetchone()[0])
print("compile options:")
for row in con.execute("PRAGMA compile_options"):
    print(" ", row[0])
PY

verify locally: run that check through the exact launch environment for the daemon, scheduler workers, migration commands, and every sidecar process that opens these files.

2. Cross-file partial commit after host failure. Normal exception rollback tests prove only that the process can roll back all attached databases while it remains alive. They do not test loss of power between independent WAL durability points. Confirm the exposure by assigning one global operation ID to every intended cross-file invariant, then comparing committed IDs and terminal states across databases. A disposable-VM power-cut or custom-VFS fault-injection test is the decisive test. The remediation is not a stronger Python lock: either move the invariant’s tables into one database or make one transaction authoritative and publish a durable outbox record for idempotent application to the other databases. SQLite+1

3. Legacy flock may not serialize threads inside the macOS daemon. The old helper has no per-path in-process mutex. Apple documents flock locks as file-scoped rather than ordinary descriptor-scoped, so Linux behavior or subprocess tests should not be accepted as proof that two threads, each opening the same lock path, block each other inside one process. The new coordinator’s threading.Lock removes the ambiguity. Apple Developer+1

verify locally: on the production macOS version, start two threads, have each separately open() the same lock file, hold LOCK_EX in the first, and assert that LOCK_EX|LOCK_NB in the second raises BlockingIOError. Treat a second successful acquisition as a P0 lock failure.

4. Split writer planes and unenrolled writers. Even when each class lock works, LIVE and BULK use different files. A bulk transaction that has not reached a BulkChunker yield point can contend directly with a live write. A raw sqlite3.connect, a new maintenance script, or an ATTACH path outside the wrapper can bypass both. The decisive measurement is runtime enforcement: attach a lease token to the execution context and reject or log every BEGIN, DML statement, schema write, and writable ATTACH executed without a valid per-database lease. Supplement it with an AST/SQL-literal inventory in CI; do not rely only on grep or an allowlist that can age. GitHub+2GitHub+2

verify locally: run one business cycle with enforcement in report-only mode and require zero unleased write statements before making enforcement fail-closed.

5. Process-wide scheduler classification race. Two overlapping scheduled jobs can overwrite the same environment variable and restore stale values out of order. Confirm with a deterministic barrier test that starts LIVE and BULK jobs simultaneously and records the resolved class at connection acquisition. Replace environment mutation with an explicit argument or contextvars.ContextVar; the writer coordinator should receive the class directly. GitHub

6. Cancellation interrupts the wrong execution context. _thread.interrupt_main() can raise in the main scheduling/control thread while the worker’s SQLite call continues. Exercise the actual APScheduler executor with a blocked bulk transaction, trigger the watchdog, and measure which thread exits, whether the connection rolls back, and when the lock is released. Replace it with Connection.interrupt() on the exact connection plus a cooperative cancellation flag checked between bounded chunks. GitHub+2GitHub+2

7. Path-identity split brain. The observed 1 MiB sibling with a full schema is the expected result of mkdir plus create-if-missing connection semantics. A second variant is worse: after an atomic rename, old connections continue writing the old inode while new connections open the new canonical path. At startup and after every cutover, record the canonical real path, device, inode, file owner/mode, a database-role marker, application_id, schema hash, and deployment generation. Reject symlinks, non-regular files, role mismatches, and unexpected filenames. Inspect lsof for old/deleted inodes before deleting or reusing a path. Use mode=rw; reserve mode=rwc for an explicit provisioning command. GitHub+1

8. Unqualified names resolve to the wrong attached schema. The code already documents same-named real and ghost tables. Inventory every ATTACH caller and fail CI on unqualified DML in an attached connection. At runtime, compare the attached database list, role marker, and manifest ownership before starting a write transaction. A typed wrapper is useful only after raw .execute() escape hatches are eliminated or audited. GitHub+2GitHub+2

9. Durability policy is implicit. The connection factory does not set synchronous, fullfsync, or checkpoint_fullfsync. In WAL mode, synchronous=FULL includes a WAL sync at commit; NORMAL remains corruption-safe but may lose recent transactions after operating-system or power failure. On macOS, fullfsync and checkpoint_fullfsync control use of stronger F_FULLFSYNC behavior and default off. Define separate policies for authoritative trade state and reconstructible telemetry rather than inheriting whatever the runtime or an earlier connection left behind. SQLite+3SQLite+3SQLite+3

verify locally: from every writer class, record journal_mode, synchronous, fullfsync, checkpoint_fullfsync, wal_autocheckpoint, locking_mode, and the returned value from setting WAL. Back the policy with a forced-power-loss recovery test, not only kill -9.

10. Backups may be nominal but unrestorable. A raw copy that omits the WAL can omit committed transactions or produce an inconsistent database. SQLite’s online backup API creates a consistent destination and can release the source read lock between steps, but active writes can restart its progress. Measure backup completion time, restart/rewind count, source-WAL growth during backup, restore time, and post-restore integrity. A backup has not succeeded until a separate process opens the restored database and verifies role, schema, sequence watermarks, and business invariants. SQLite+1

P1: availability, latency, and storage exhaustion

11. Checkpoint health is currently false-green. The repository interprets the first result from wal_checkpoint(PASSIVE) as a long-reader indicator. Under PASSIVE, SQLite does not wait for readers, does not invoke the busy handler, and normally returns zero in the first field even when not all frames could be checkpointed. The useful fields are log frames and checkpointed frames. Track max(0, log_frames-checkpointed_frames), its slope, the oldest reader age, and whether the floor regularly returns near zero. Current SQLite also has a side-effect-free NOOP checkpoint mode, but it must be version-gated. GitHub+2GitHub+2

12. Physical WAL size can mislead in both directions. A large WAL with zero backlog may merely be preallocated/reused. Conversely, a modest WAL with steadily increasing uncheckpointed frames is an early failure. Correlate four signals: physical -wal bytes, log frames, checkpointed frames, and append/checkpoint rates. The code’s prior 810 MiB incident is evidence that this is not theoretical. GitHub+1

13. Unfinalized cursors can hold invisible read snapshots. A generator, partially consumed cursor, exception path, or cached statement can keep a read transaction alive even when application code has no explicit BEGIN. Instrument statement first-step, last-step/finalize, cursor close, connection close, query fingerprint, and owning job. For definitive state, expose sqlite3_txn_state() through APSW or a small binding; do not use only Python’s high-level transaction flag as the reader-age oracle.

14. Automatic checkpointing can create periodic commit-tail spikes. SQLite’s default automatic checkpoint threshold is 1,000 WAL pages unless changed. At 4,096-byte pages that is only about 4.1 MiB of WAL frames. The connection that crosses the threshold can perform PASSIVE checkpoint work as part of its commit path. Correlate commit p95/p99 with threshold crossings and checkpoint page counts before replacing autocheckpointing with a dedicated owner. SQLite+1

15. Connection-local cache targets multiply. cache_size is a target per open database, allocated on demand rather than an immediate reservation. Nevertheless, many active handles can accumulate large independent pager caches, while ATTACH and connection triples multiply exposure. Measure open connection count by role, actual RSS, pager-cache used/hit/miss/spill values, and cache miss latency. Python’s standard wrapper does not expose all sqlite3_db_status counters, so use APSW, a narrow C extension, or workload-level page-fault and I/O counters. SQLite+3GitHub+3GitHub+3

16. The 32 GiB mmap setting can be counterproductive for append-heavy files. SQLite maps the first N bytes of each database, not the most recently appended region. On 40–94 GB databases, a 32 GiB cap can map cold early history while the hot append tail still uses ordinary pager I/O. Mmap can improve some workloads but can also hurt, and an mmap I/O error may terminate the process. Compare mmap_size=0 and the current setting using hot-query latency, page faults, RSS, I/O latency, and writer commit tails. SQLite+2SQLite+2

17. Read-only analytical SQL can still exhaust temporary storage. A SELECT that chooses an automatic index, sort, GROUP BY, DISTINCT, or large materialization can create temp B-trees even when the main databases are query-only. Capture EXPLAIN QUERY PLAN markers for automatic indexes and temp B-trees, statement sort/autoindex counters, temp-file locations and high-water bytes, and free-space slope. A planner operating without statistics makes this failure more plausible.

18. Large dirty transactions can trigger cache spill and long exclusive phases. Measure transaction duration, WAL frames per transaction, dirty pages, pager spills, fsync time, and writer-lock hold time rather than only row count. Chunk limits should be expressed primarily in milliseconds and frames/bytes; “10,000 rows” has radically different cost for a narrow state row versus a multi-kilobyte JSON record.

19. APFS snapshots and clones can conceal effective free-space loss. Deleting or replacing a 94 GB file need not release equivalent physical blocks while snapshots or clones retain them. Record APFS snapshot inventory, allocated versus logical size, purgeable space, and free-space slope during a representative rewrite. Do not treat df free space as the only capacity input to a copy-on-write migration.

20. DDL or index maintenance can invalidate prepared statements and hold schema locks. Trace schema-version changes, migration duration, prepare/reprepare counts, and writer latency around deployments. Rebuilding one large index can be both a full b-tree read and a large write transaction; it belongs on a clone or in a bounded replacement-file migration, not an unmeasured live deployment.

P1/P2: hidden physical and planner amplification

21. Near-zero freelist does not mean compact storage. Freelist count measures wholly free pages. It does not expose unused space within live table and index pages, overflow fragmentation, low fanout, or duplicate indexes. dbstat leaf-page unused bytes and per-b-tree totals are the discriminators. SQLite+1

22. Large JSON can produce overflow chains at much smaller values than “one page.” With a usable 4,096-byte page, a table-leaf record has a maximum local-payload threshold of 4,061 bytes. The record payload includes its header and all columns, not just the JSON column. Index records begin overflowing above roughly 1,002 bytes because index b-trees use a lower local-payload maximum. Each overflow page carries at most 4,092 payload bytes, and SQLite’s local-payload formula can retain only about 489 bytes locally for some overflowing records. SQLite

23. Wide composite or WITHOUT ROWID keys can collapse index fanout. Inventory PRAGMA table_list, index_xinfo, key widths, collations, partial predicates, b-tree depth, interior-page fanout, and index overflow. A large JSON payload that is never indexed is different from a long natural key replicated into every secondary index of a WITHOUT ROWID table.

24. Index count hides write amplification and redundancy. The 194 indexes on 80 tables average about 2.4 indexes per table, which is not intrinsically excessive. The relevant measures are index bytes per table, exact and left-prefix overlap, maintained indexes per top-ingest table, frames per inserted logical byte, and whether representative queries actually choose an index. SQLite has no durable built-in “last used” timestamp for indexes, so absence from a short trace is not sufficient evidence for deletion.

25. Missing statistics can produce parameter-sensitive plan failures. No sqlite_stat1 means there are no persisted, data-derived cardinality estimates. That matters most for joins, competing indexes, ranges, skewed predicates, IN/OR, partial indexes, and correlated access—not for a simple unique-key lookup. Capture query fingerprints and parameter buckets, EXPLAIN QUERY PLAN, actual loop/row counts through scan-status APIs, full-scan steps, sort operations, automatic-index creation, and p95/p99 latency. SQLite+3SQLite+3SQLite+3

26. Rotation introduces late-write and global-uniqueness hazards. Before sharding, determine whether event time can be late or corrected, whether IDs are globally unique, and whether updates can target sealed history. Route by an immutable ingest sequence or a durable routing epoch, not only by event timestamp. Keep global idempotency in the hot authoritative database or use globally unique identifiers; SQLite cannot enforce one UNIQUE constraint across independent epoch files.

B. Safe-audit risks and the operationally safest census

Quantified exposure

At 4,096-byte pages, 218 GB corresponds to approximately:

53.2 million pages if the sizes are decimal GB;

57.1 million pages if they are GiB.

With near-zero freelist, a complete per-b-tree dbstat traversal will visit approximately a file’s worth of table and index pages. At an unattainable perfectly sequential rate of 500 MB/s, 218 GB is about 7.3 minutes of raw reads; at 200 MB/s, about 18 minutes; at 100 MB/s, about 36 minutes. Real dbstat work will be longer because it walks b-tree structures, competes with the live workload, crosses many objects, and may fault pages into the operating-system cache. Aggregate mode reduces the number of result rows; it does not turn the underlying traversal into a metadata-only operation. SQLite+3SQLite+3SQLite+3

The read-mark risk can be expressed directly. A WAL frame is approximately page_size + 24 bytes, plus a small file header. While a reader prevents WAL reset or checkpoint advance past its snapshot, potential growth is:

additional_WAL_bytes ≈ sustained_WAL_append_bytes_per_second × reader_seconds

At sustained WAL generation of:

1 MiB/s: about 3.52 GiB/hour;

5 MiB/s: about 17.6 GiB/hour;

20 MiB/s: about 70.3 GiB/hour.

With only 119 GB free, 20 MiB/s can consume the nominal headroom in under two hours, and 5 MiB/s in under seven hours, before reserving capacity for normal database growth, temporary files, snapshots, or recovery. The acceptable scan duration must therefore be derived from measured WAL rate, not from a fixed “one table at a time” convention. WAL readers retain an end mark, and checkpointing cannot freely advance or reset around active readers. SQLite+1

A live scan’s private SQLite pager cache does not directly evict pages from another connection’s private pager cache. The larger risk is that reading tens or hundreds of gigabytes displaces the hot working set from the operating-system file cache and saturates the shared storage queue. The production read-only factory also grants the audit connection the same 1 GiB cache target and 32 GiB mmap cap, which is unnecessarily aggressive. GitHub+2SQLite+2

Safest procedure: audit a clone, one database at a time

First preference: an online SQLite backup to a different physical volume.

Use the SQLite backup API in small page batches. Sleep between steps and record restarts, progress, source WAL growth, and writer latency. Because a busy source can repeatedly restart the backup, allow it to make most progress under live load and use a brief unified writer gate only for final convergence. Place the destination on an external encrypted volume with enough room for the clone, validation artifacts, and index/statistics experiments. Do not use a same-volume APFS clone as a “free” copy unless copy-on-write capacity has been explicitly modeled. SQLite

After backup:

Open the clone with the same or newer patched SQLite library.

Confirm application_id, role marker, schema hash, user_version, page size, and sequence watermarks.

Run full dbstat, full integrity_check, foreign_key_check, query-plan replay, and index experiments there.

Perform a restore drill into a clean directory and start a read-only copy of the application against it.

Keep clone output and reports off the production volume.

quick_check is still O(N); it merely omits some index-consistency checks. It is appropriate on the clone, not as an allegedly cheap live health query. SQLite+3SQLite+3SQLite+3

Second preference: an atomic filesystem snapshot copied elsewhere

A tested APFS volume snapshot can capture the database, WAL, and shared-memory state together, but the snapshot and subsequent read must be validated with the exact deployment. Copy it to another physical volume before running the census. A snapshot retained on the same 87%-full volume can convert ordinary writes into copy-on-write amplification and reduce rather than improve safety.

Last resort: tightly budgeted live dbstat

Do not reuse the production read-only factory unchanged. Use a dedicated subprocess and one fresh connection per b-tree:

Python

Run

import sqlite3
import time
from pathlib import Path

path = Path("/absolute/path/to/state/zeus_trades.db").resolve(strict=True)

con = sqlite3.connect(
    f"file:{path}?mode=ro&cache=private",
    uri=True,
    timeout=0.25,
    isolation_level=None,
)
con.execute("PRAGMA query_only=ON")
con.execute("PRAGMA busy_timeout=250")
con.execute("PRAGMA cache_size=-16384")  # 16 MiB target
con.execute("PRAGMA mmap_size=0")

deadline = time.monotonic() + 20.0  # derived per object, not a fleet constant
con.set_progress_handler(
    lambda: 1 if time.monotonic() >= deadline else 0,
    10_000,
)

Use aggregate mode for the first pass:

SQL

SELECT
    name,
    pageno AS total_pages,
    pgsize AS total_bytes,
    ncell AS total_cells,
    payload AS payload_bytes,
    unused AS unused_bytes,
    mx_payload AS maximum_payload,
    100.0 * (pgsize - unused) / NULLIF(pgsize, 0) AS packing_pct
FROM dbstat('main', 1)
WHERE name = ?;

For each object:

Ensure there is no explicit encompassing BEGIN.

Execute exactly one bounded statement.

Consume its single aggregate row.

Finalize the cursor and close the connection immediately.

Wait for WAL backlog, disk latency, and hot-query latency to return to baseline before the next object.

Abort the subprocess if the SQLite progress handler does not return promptly; process termination is the final guarantee that the read mark is released.

Never ATTACH the other live databases to the audit connection.

Only shortlist objects for detailed mode:

SQL

SELECT
    count(*) AS pages,
    sum(pagetype = 'leaf') AS leaf_pages,
    sum(pagetype = 'internal') AS internal_pages,
    sum(pagetype = 'overflow') AS overflow_pages,
    sum(CASE WHEN pagetype = 'overflow' THEN pgsize ELSE 0 END)
        AS overflow_bytes,
    sum(payload) AS payload_bytes,
    sum(unused) AS unused_bytes,
    max(mx_payload) AS maximum_payload
FROM dbstat('main')
WHERE name = ?;

Before the first scan, collect at least 10–15 minutes of:

WAL append frames/s and bytes/s;

log, checkpointed, and backlog frames;

physical WAL size;

oldest known reader age;

live and bulk writer queue depth;

writer lock hold time and commit p50/p95/p99;

storage latency and queue depth;

process RSS and host memory pressure;

free space, APFS snapshots, and non-database growth.

Use this duration budget:

maximum_scan_seconds =
    (free_bytes
     - emergency_reserve
     - current_WAL_bytes
     - forecast_non_DB_growth)
    / observed_WAL_append_bytes_per_second

The emergency reserve should be at least the larger of the organization’s normal volume reserve and the space needed for two worst-observed checkpoint/transaction incidents. It should not be defined as “whatever is left after the scan.”

Initial stop conditions:

projected reserve exhaustion within six hours;

uncheckpointed backlog over 256 MiB and rising for five minutes;

no meaningful backlog drain for five minutes after a scan;

oldest reader over 30 seconds on a hot database, or over 60 seconds with rising backlog;

writer p99 or critical query p99 above twice baseline, or any SLO breach;

storage latency above twice baseline;

host memory-pressure warning/critical state;

audit object exceeds its calculated deadline.

These are conservative initial controls, not universal SQLite thresholds. Rebase them after observing normal business-day and peak-market behavior.

Operations to prohibit on a live 100 GB WAL database

Do not run the following unbudgeted against the production writer:

full VACUUM;

same-volume VACUUM INTO;

page-size or auto-vacuum conversion;

no-argument full ANALYZE;

fleet-wide REINDEX or large index creation/rebuild;

sqlite3_analyzer, .dump, full integrity_check, or “quick” quick_check during active trading;

wal_checkpoint(FULL), RESTART, or TRUNCATE while readers or writers are active;

a journal-mode change;

immutable=1 against a changing database;

a raw file copy that does not atomically include WAL state;

manual deletion of -wal or -shm;

a single massive retention DELETE;

a table rebuild or schema migration that copies tens of gigabytes;

an audit that ATTACHes all three live databases;

a same-volume APFS snapshot retained without a capacity budget.

PASSIVE checkpointing is non-blocking with respect to readers, but it still performs I/O and is not a side-effect-free measurement. Use current SQLite’s NOOP mode where supported, or let one designated checkpoint owner perform and measure PASSIVE checkpoints. SQLite

C. Measurements and actionable thresholds

WAL and checkpoint health

Collect these per database every one to five seconds:

log_frames
checkpointed_frames
backlog_frames = max(0, log_frames - checkpointed_frames)
backlog_bytes  = backlog_frames × (page_size + 24)
WAL append frames/s
checkpoint frames/s
physical WAL bytes
oldest reader age
checkpoint duration and mode
writer queue wait, lock hold, transaction, and commit latency
free-space slope

A healthy database does not need a permanently tiny physical WAL. It does need backlog to drain regularly and checkpoint throughput to catch up whenever backlog exists.

Use these initial states:

Healthy: backlog routinely returns below one autocheckpoint interval or near zero; no reader exceeds the normal query budget; checkpoint throughput exceeds append throughput while draining.

Warning: backlog exceeds 64 MiB for more than two minutes or two expected checkpoint intervals.

High: backlog exceeds 256 MiB and rises for five minutes; no near-zero interval occurs for five minutes; or a hot reader remains active over 30 seconds.

Critical: backlog exceeds 512 MiB and rises, projected reserve exhaustion is under six hours, or writer/query p99 exceeds twice baseline.

The first PASSIVE result field is not the alert signal. The code should remove the current interpretation and alert on backlog, slope, reader age, and time-to-reserve. SQLite+1

Overflow at 4 KiB

At 4,096-byte pages:

table-leaf maximum local payload: about 4,061 bytes
index maximum local payload:      about 1,002 bytes
minimum local payload:            about   489 bytes
overflow payload per page:        about 4,092 bytes

Measure independently for each table and each index:

overflow_bytes / total_btree_bytes
overflow_pages / leaf_cells
p50 and p95 overflow-chain length
maximum record payload
bytes fetched per hot query
fraction of queries that actually project the large JSON

Initial action levels:

Hot table: investigate above 10% overflow bytes or 0.25 overflow pages per accessed row; treat above 25% or p95 chain length over two pages as high.

Hot index: investigate above 1% overflow bytes or 0.1 overflow pages per entry. Index overflow is a stronger smell because it usually means very wide indexed keys or duplicated primary-key material.

Cold append history: tolerate higher ratios when most queries filter on narrow columns and do not fetch the JSON. Page-size migration should be justified by end-to-end read and write measurements, not by overflow ratio alone.

At a 16 KiB page size, the corresponding table threshold is about 16,349 bytes, the index threshold about 4,086 bytes, and each overflow page carries about 16,380 bytes. That can substantially reduce chains for large records, but every WAL frame also grows from about 4,120 bytes to about 16,408 bytes. A small hot-state update can therefore dirty and checkpoint four times as many bytes per page. SQLite

Fill factor and internal slack

Calculate leaf packing separately from internal and overflow pages:

leaf_packing = 1 - leaf_unused_bytes / leaf_total_bytes
reclaimable_internal_slack = sum(unused bytes on live leaf pages)

Initial thresholds:

below 65% leaf packing: actionable;

65–75%: investigate when unused bytes exceed 1 GiB or 15% of the object;

above 80%: usually healthy for monotonically appended b-trees, subject to measured split and write behavior.

Do not average internal, leaf, and overflow pages into one fill metric. Rightmost append pages and newly split pages naturally have different occupancy, so inspect distributions and absolute bytes, not only one mean.

Index bloat and write cost

The count of 194 is secondary. For each table collect:

table-b-tree bytes;

each index’s bytes, depth, cells, overflow, and unused bytes;

exact and left-prefix duplicate definitions;

uniqueness, partial predicate, collation, sort direction, and covering columns;

inserts/updates per second;

WAL frames and commit latency per representative ingest batch;

query-plan selection across a full business cycle.

Investigate when:

total secondary-index bytes exceed the table b-tree on an append-heavy lane;

a top-ingest table maintains more than five non-constraint secondary indexes;

indexes are exact duplicates or one is a true redundant left prefix after accounting for uniqueness, collation, ordering, and partial predicates;

an index exceeds 1 GiB and has no demonstrated role in the representative query corpus;

workload replay shows index maintenance dominates WAL generation or commit time.

Drop an index only after clone replay shows at least a 15–20% improvement in the targeted write/WAL metric with no material p99 read regression. Never remove a uniqueness, idempotency, foreign-key support, reconciliation, or emergency-operations index merely because a short trace did not use it.

Missing ANALYZE / sqlite_stat1

On a 94 GB database, the absence of sqlite_stat1 is a high-priority planner risk, but not a reason to execute unrestricted ANALYZE live. Full ANALYZE reads every relevant table/index and can change plans in either direction. Since SQLite 3.46, PRAGMA optimize can bound analysis automatically; current SQLite also supports a dry-run form. SQLite+3SQLite+3SQLite+3

Prioritize queries with:

joins where the outer-loop choice matters;

inequalities and range predicates;

highly skewed symbols, venues, statuses, or time ranges;

several competing indexes;

partial indexes;

OR/IN branches;

large differences between parameter buckets.

Use these action criteria:

actual loop or row counts differ by more than 10× between expected and observed behavior;

a query returns under 1,000 rows but scans over 100,000;

steady-state production generates an automatic index;

a temp sort/materialization is created despite an intended supporting index;

p95 differs by more than 2× between common parameter buckets;

a plan changes from an indexed lookup to a broad scan after statistics rollout.

Check whether the runtime was compiled with ENABLE_STAT4. STAT4 can materially help skewed multi-column predicates, but bounded analysis with analysis_limit does not generate STAT4 samples. Persisting a fixed sqlite_stat1 set from a representative clone provides plan stability; it does not reproduce STAT4 histograms. SQLite+2SQLite+2

D. Prioritized remediation map and operational playbook

P0 — before any heavy audit or maintenance

1. Gate the SQLite runtime. Block production startup or at least maintenance/checkpoint operations on an unapproved source ID. Upgrade vulnerable builds to a fixed line before introducing deliberate checkpoint concurrency. Record the source ID in telemetry for every process.

2. Establish a tested external backup and restore. Back up one database at a time through the SQLite API to a separate physical volume. Validate and restore it. Preserve database, WAL semantics, role identity, schema, sequence watermarks, and business invariants.

3. Make path identity fail-closed. Separate provisioning from ordinary opening:

provisioning may create a file and initialize its role;

production opening uses mode=rw;

no parent-directory creation in the low-level live connector;

compare canonical path, device/inode, owner, mode, role marker, application ID, and schema hash;

check that the returned journal_mode is wal;

reject all unexpected sibling names.

Quarantine the stray dash/underscore databases only after confirming no process has them open and after extracting any unique rows for reconciliation. Restrict backup and database directories to the daemon account; do not inspect an unknown sibling schema inside the privileged trading process.

4. Correct checkpoint telemetry. Replace the PASSIVE “busy” test with log/checkpoint/backlog values, append rate, oldest reader age, and time-to-reserve. Add writer queue and transaction metrics.

5. Define durability by data class. Authoritative orders, fills, idempotency, reservations, and financial state should use an explicitly tested durability setting. Reconstructible market telemetry may accept a different policy, but the choice must be deliberate and observable.

6. Remove false cross-file atomicity claims. Classify every multi-database operation:

must survive a host crash atomically;

can be eventually consistent with reconciliation;

purely analytical.

Co-locate the first class in one database. Implement the second as one authoritative commit plus a durable outbox, idempotent application, retry state, and reconciliation query.

P1 — unify writing and transaction lifecycle

Complete the existing WriteCoordinator migration.

The safest cutover is one fenced restart in which every writer changes from legacy lock paths to the unified lock. Where old and new processes must overlap, a transitional coordinator should acquire the unified gate and both legacy class gates in one documented canonical order. Remove the legacy acquisitions only after runtime telemetry proves no old writer remains; otherwise old and new namespaces do not interoperate.

The target writer design should be:

one per-database in-process mutex;

one per-database interprocess lock where subprocess writers remain;

LIVE and BULK sharing the same gate;

explicit priority in a queue, not separate locks;

short transactions;

BULK work chunked by elapsed hold time and WAL frames;

exact-connection cancellation;

a lease token required for writable SQL;

canonical database order for operations that acquire more than one gate;

no claim that a multi-gate lease creates cross-file durability.

A stronger realization for this topology is one writer actor/thread per database, owning the writable connection and accepting queued commands. It serializes by construction, gives LIVE requests explicit priority, centralizes checkpoint policy, reduces write-connection proliferation, and makes transaction lifetime observable. The coordinator remains useful for subprocess fencing and migration commands.

Replace the environment-variable write class with an explicit argument or context variable. Replace interrupt_main with exact-connection interruption and cooperative chunk cancellation.

After reader-age telemetry and the SQLite version gate are in place, designate one checkpoint owner per database. Benchmark either disabling per-connection autocheckpoints or moving the threshold upward, then let the owner issue PASSIVE checkpoints based on backlog and latency. Use RESTART or TRUNCATE only in a maintenance window after draining writers and readers. Do not change checkpoint ownership and page size in the same deployment.

P1 — clone, census, and planner remediation

Run the full physical census on the external clone:

per-b-tree aggregate dbstat;

detailed leaf/overflow analysis for the largest and hottest objects;

table/index size attribution;

duplicate-index analysis;

complete query-plan corpus with representative parameters;

integrity and foreign-key checks;

backup/restore timing;

4 KiB versus 16 KiB workload replay for candidate archive tables.

For planner remediation:

On a patched SQLite 3.46+ runtime, run PRAGMA optimize(-1) on the clone to see proposed work without changing the database. SQLite

Run full ANALYZE on the clone and compare plans and actual row/loop counts.

Capture before/after p50, p95, p99, full-scan steps, sorts, automatic indexes, and writer impact under replay.

Decide between:

a single bounded PRAGMA optimize(0x10002) on one dedicated production admin connection; or

a vetted fixed sqlite_stat1 set derived from a representative clone when plan stability is more important.

Roll out one database at a time.

Do not put PRAGMA optimize(0x10002) blindly into every connection factory; on a fresh database connection that flag deliberately considers all tables.

Keep a rollback snapshot of statistics and the query-plan corpus. If a regression occurs, restore the old statistics—or clear the newly created statistics when the prior state was none—reload the schema statistics, and reopen/reprepare long-lived statements.

For index remediation, alter one object at a time. On production, prefer stopping maintenance of an index in a newly built epoch or replacement database over dropping and reconstructing multi-gigabyte indexes in place.

P1 — stop growth before trying to reclaim old space

Retention must precede compaction conceptually, but it should be implemented through new bounded targets, not through an initial giant DELETE.

With auto_vacuum=OFF, deletes place pages on the freelist for reuse but do not shrink the file. Row-by-row deletion can also generate a large WAL and create precisely the capacity incident it is intended to solve. Converting to FULL or INCREMENTAL auto-vacuum requires a rebuild/VACUUM. SQLite

The lowest-risk first step is:

Create a new active epoch database for the append-only lane.

Add a durable catalog recording role, schema version, epoch ID, sequence/time bounds, state, checksum, and physical path.

Under a brief writer gate, record a sequence watermark and route all new append records to the new epoch.

Make readers consult old history plus the active epoch.

Leave the old monolith readable but stop its append growth.

Handle late arrivals through a designated late-arrival delta epoch or immutable ingest-epoch routing.

Seal epochs at a bounded size or time interval.

Delete whole expired epoch files after closing handles and accounting for APFS snapshots.

Start with an epoch target of roughly 5–20 GB, then tune it so backup, integrity validation, and replacement fit the operational window. The governing threshold is not a calendar month; it is the largest unit that can be copied, checked, restored, and retired safely.

Where an in-place delete is temporarily unavoidable, chunk by primary-key range and enforce maximum transaction hold time and WAL frames. Pause when LIVE queueing, WAL backlog, or disk latency rises. Do not expect the filesystem to regain space afterward.

P2 — VACUUM and page-size migration

A full same-volume VACUUM is unsafe with the current headroom. SQLite may require additional temporary space approximately equal to the original database. For the 94 GB database, that can leave only about 25 GB from the stated 119 GB before accounting for live WAL growth, APFS copy-on-write blocks, temp data, and the other databases. The 84 GB case leaves about 35 GB. Even the 40 GB case should not consume shared emergency capacity while the other two writers remain live. VACUUM INTO preserves the source and produces a compact copy, but an interrupted output can be incomplete and a same-volume copy has the same capacity problem. SQLite+2SQLite+2

Do one of the following instead:

run VACUUM INTO on an external clone, with output on an external volume;

logically rebuild a new database on an external volume;

avoid rebuilding the old monolith at all by arresting growth and moving active/hot data into small new databases.

Changing page size is not available while the source remains in WAL mode. Build a new database with the target page size before creating/loading its schema, then switch it to WAL after validation. The backup API is not a page-size conversion mechanism for this WAL source. SQLite+1

For archive candidates, benchmark 16 KiB first. Compare:

total file and index bytes;

overflow pages and p95 chain length;

b-tree depth and fanout;

point-read p99;

range-scan throughput;

WAL bytes per inserted record;

checkpoint throughput and duration;

cache hit rate;

recovery and backup duration.

Keep 4 KiB as the default for small, frequently updated hot-state databases unless the workload proves otherwise. A mixed hot/cold monolith cannot have one page size optimal for both.

A logical rebuild must explicitly handle:

tables without an explicit INTEGER PRIMARY KEY, because rowids can change during VACUUM/rebuild;

WITHOUT ROWID tables;

sqlite_sequence;

generated columns;

triggers, views, and partial indexes;

FTS, RTree, and other virtual tables;

collations and application-defined functions;

application_id and user_version;

foreign-key ordering;

ghost and legacy tables;

exact schema ownership;

chunk checksums, row counts, and min/max sequence watermarks.

No-downtime cutover and rollback

A naïve synchronous dual write to two independent databases merely moves the cross-file atomicity problem. Use a durable outbox:

Commit the authoritative state change and outbox item in one source database.

Apply the change idempotently to the target.

Record target acknowledgement and continuously reconcile.

Backfill historical rows by bounded sequence ranges.

Shadow-read and compare target results.

Under a short unified writer gate, establish a final watermark and drain the outbox.

Close every old connection; checkpoint and fsync according to the durability policy.

Switch the catalog/configuration to the new file.

Reopen and verify path, role, schema, device/inode, and watermark.

Keep the old database read-only through the rollback interval.

After cutover, rollback is safe only if changes made on the new authority can be replayed back to the old one. Retain a reverse outbox or define rollback as restoring the old snapshot plus replaying the new operation log.

A full 94 GB replacement cannot simultaneously provide brief downtime, same-volume atomic replacement, ample rollback headroom, and only 119 GB free unless the replacement is dramatically smaller. One constraint must change: add storage, first split out a small active/hot database, or accept a longer copy window. Epoch rotation avoids that physical impossibility because the first cutover creates only a small new file and leaves the historical monolith in place.

E. Is one large SQLite file still the correct substrate?

Hot mutable state: yes

SQLite remains a strong substrate for:

authoritative order/fill and trade-state transitions;

idempotency keys;

reservations and locks;

current positions and balances;

current market/forecast heads;

small calibration and configuration tables;

durable outboxes;

reconciliation state.

These objects benefit from in-process access, a simple single-writer model, transactions, constraints, and low operational surface area. Keep each crash-atomicity domain in one database.

Unbounded append-only event/fact logs: no, not in the same files

The superior low-risk realization is:

one small active WAL epoch;

sealed SQLite epoch files for recent history or point-lookups;

a catalog/router in the application;

cold conversion to Parquet for analytical scans;

file-level expiration.

This dominates the current monolith because it gives:

bounded checkpoint, backup, audit, and corruption blast radius;

O(1) retention through whole-file deletion;

independent indexes and page sizes for active versus archive data;

reduced cache pollution of hot state;

no need for a 94 GB in-place VACUUM;

much smaller maintenance and restore windows;

simple movement of sealed epochs to another volume.

Do not permanently ATTACH dozens of epochs to the writer. Route at application level and attach at most a bounded set on a read-only analytical connection. An ATTACH federation does not recover cross-file atomicity and multiplies schema, cache, and lifecycle complexity. SQLite

A likely split, subject to the ownership manifest and actual query graph, is:

zeus_trades.db: retain the authoritative trade ledger, idempotency, current orders/fills/positions, reservations, and outbox. Rotate bulky evidence, execution telemetry, historical snapshots, and append-only evaluation logs.

zeus-world.db: retain current market/world heads and small mutable calibration state. Rotate opportunity/event histories and other fact streams.

zeus-forecasts.db: retain current forecast heads, active coverage, and mutable serving state. Rotate raw observations, historical forecast facts, traces, and evaluation records.

The documented ghost/misplaced tables mean the manifest is an input to this classification, not sole proof. Validate every table through writers, readers, row counts, schema, and business ownership before moving or dropping it. GitHub+3GitHub+3GitHub+3

For cold append data:

Parquet plus DuckDB dominates when access is mostly time-range scans, aggregation, projection of a few columns, and long-term retention. Normalize frequently filtered fields out of JSON before archival.

Partitioned PostgreSQL dominates when operational SQL needs multiple concurrent writers, server-side replication, cross-client transactions, built-in partition management, or online retention.

ClickHouse dominates when high-volume analytical aggregation and compression are the primary requirement, but it should not become the sole authority for money-state transitions without a separate transactional system.

Per-epoch SQLite has the lowest migration risk and should be the first architectural step. It preserves SQL, schemas, libraries, backup knowledge, and local operation while removing the unbounded-file failure mode.

Immediate execution order

The concrete order should be:

Obtain and gate the production SQLite source IDs.

Freeze heavy live audit, VACUUM, full ANALYZE, and manual blocking checkpoints.

Establish and restore-test external backups.

Make database identity/path opening fail-closed and reconcile stray files.

Fix checkpoint telemetry and add oldest-reader, transaction, WAL-rate, and writer-queue instrumentation.

Define and test durability pragmas.

Complete the unified WriteCoordinator cutover, including the in-process mutex and all writer enrollment.

Replace environment classification and main-thread interruption.

Remove cross-file atomicity assumptions; introduce co-location or an outbox.

Back up one database externally and perform the first full physical/query-plan census on the clone.

Roll out bounded planner statistics one database at a time, with a measured rollback.

Start new append epochs so the monoliths stop growing.

Split small hot mutable state from old historical files.

Prune by retiring whole sealed epochs.

Rebuild or page-size-migrate old history externally only where the measured benefit justifies it.

Consider PostgreSQL or an analytical engine only after the hot/append split exposes the remaining workload requirements.

Sources, unavailable evidence, and load-bearing assumptions

I used the pinned repository snapshot’s connection factory, writer-lock layer, typed connection wrappers, unified coordinator skeleton, ownership manifest, cross-database tests, and adjacent README claims. The key source-proven conclusions are the fail-open connection behavior, per-connection cache/mmap configuration, opt-in and split legacy locks, process-wide environment mutation, main-thread cancellation, incomplete coordinator migration, writable “RO intent” handles, ghost tables, and contradictory cross-file atomicity comments. GitHub+8GitHub+8GitHub+8

The external technical basis is SQLite’s official WAL/checkpoint, dbstat, ANALYZE/optimize, VACUUM, backup, page-format, cache/mmap, durability, URI, and appropriate-use documentation, plus Apple’s macOS flock documentation. Apple Developer+11SQLite+11SQLite+11

I could not run the repository, enumerate every runtime writer dynamically, inspect the three live databases, determine the production Python/SQLite build, observe APFS snapshots, or measure workload and WAL rates. Therefore, table-specific move/drop recommendations remain classifications to validate rather than asserted final ownership decisions.

Load-bearing assumptions are that the reported sizes and free space represent the same APFS volume; the daemon can tolerate a brief writer gate; external storage can be added for a safe backup/rebuild; large JSON and historical facts are mostly colder than current state; and at least some cross-database operations can be reconciled rather than requiring true host-crash atomicity. If cross-file host-crash atomicity is mandatory, the affected tables must be co-located or moved to a transactional server.

The single smallest fact that changes the first operational priority is the exact production sqlite_source_id(). A vulnerable build makes the runtime upgrade the immediate P0 action before additional checkpoint or live-audit concurrency. A fixed build leaves writer enrollment, path identity, checkpoint telemetry, and external backup as the leading actions.