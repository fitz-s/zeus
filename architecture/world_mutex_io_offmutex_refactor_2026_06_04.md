# Plan — Kill the "blocking I/O under the world write mutex" anti-pattern (K≪N)

Status: ACTIVE

Created: 2026-06-04
Authority basis: live incident 2026-06-04 (zeus-world.db WAL 488MB→1GB+ lock-starvation), feedback_liveness_first_health_antibody, Rule 3 (single mechanism) + Rule 4 (kill error category).

## The finding (one design flaw, N symptoms)
The process-global zeus-world.db write mutex (`src/state/db.py::world_write_mutex` / `world_write_lock`) is held **across blocking network/on-chain I/O** at MANY sites. Each holds the WAL write lock for the full I/O duration → every world write serializes behind it → lock-starvation wedge + unbounded WAL. Sites found so far:
- retired-reference forecast fetch — FIXED (STEP-7, warm-cache, commit 5638cf59c6)
- JIT `/book` HTTP + order POST — FIXED (#95)
- market-channel seed on **connect** — FIXED (0ee7a80dc5, pre-capture)
- market-channel seed on **reconnect** — FIXED (8215ab341e, pre-capture)
- M5 `fresh_reconcile_snapshot` — OPEN (guard tripping live)
- pUSD allowance chain fallback — OPEN (guard tripping live)
- (full audit pending — assume more)

The category antibody (`assert_no_world_mutex_held_for_io`, commit c1106b4461) correctly EXPOSED all of them. But flipping it **fatal in prod** with OPEN sites remaining turned the daemon into a guard-raise storm AND leaked the open write txn the raise unwinds (uncommitted BEGIN → WAL cannot checkpoint → unbounded bloat). Fatal-with-pre-existing-violations is strictly worse than the bounded slow-wedge.

## Phase 0 — STABILIZE (this change): WARN-in-prod, RAISE-in-CI
Gate `assert_no_world_mutex_held_for_io`: FATAL under pytest (`PYTEST_CURRENT_TEST`) and when `ZEUS_WORLD_MUTEX_IO_FATAL=1`; ADVISORY (warn-once-per-operation) in the live daemon otherwise. Rationale: keeps the antibody's full preventive value (any NEW instance fails CI, which runs under pytest) + prod observability (every live violation logged once), while letting the daemon complete its write txns (commit → WAL checkpoints → bounded) instead of thrashing. Then ONE clean restart clears the 1GB WAL. This is a guard-behavior change only; it does NOT touch any settlement/truth value, schema, or DB write semantics.

## Phase 1 — REFACTOR (the real fix): move ALL I/O off the lock
Audit every `world_write_mutex()` / `world_write_lock()` / `with _world_mutex` scope. For each that contains a blocking venue HTTP / on-chain RPC call: restructure so ALL fetches happen BEFORE acquiring the lock (pre-capture / fresh-snapshot pattern); the under-lock section is DB-write-only; a missing pre-fetched value fails-closed to a safe no-op (never an under-lock fetch). Per-site TDD relationship test: "this operation does not call a guarded I/O method while the mutex is held." Open sites: M5 `fresh_reconcile_snapshot`, pUSD allowance, + whatever the audit surfaces.

## Phase 2 — ARM (after audit proves zero remaining sites)
Set `ZEUS_WORLD_MUTEX_IO_FATAL=1` in the live daemon plist env so prod is fatal again — at that point fatal is safe (zero violations) and protects against regressions the CI test might miss. Add a static AST check (modeled on the writer-lock antibody) that flags a guarded-I/O call lexically inside a mutex scope, so a new instance fails CI without needing a runtime test to exercise it.

## Verification
- Phase 0: pytest `tests/test_world_mutex_io_guard.py` still RED→GREEN (fatal under pytest); live daemon WAL returns to <80MB after restart; `WORLD_MUTEX_IO_ADVISORY` warn-once lines replace the violation storm.
- Phase 1: per-site relationship test GREEN; live WAL stays bounded with zero advisory lines.
- Phase 2: daemon boots with `ZEUS_WORLD_MUTEX_IO_FATAL=1` and runs with zero violations.

## NOT doing
Not reverting the antibody (it is the immune system; reverting loses CI protection). Not weakening any trade/admission/settlement gate. Not arming prod-fatal before the audit proves zero sites.
