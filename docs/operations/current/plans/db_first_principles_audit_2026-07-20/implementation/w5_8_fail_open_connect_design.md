# W5-8 fail-open connect — survey + fix design (sequenced AFTER writer-unification consult)

## Defect (connections_throughput.md W5-8)
`_connect` (src/state/db.py:234-295): line 249 `db_path.parent.mkdir(parents=True, exist_ok=True)`
+ line 259 `sqlite3.connect(str(db_path))` = default `rwc` mode → a wrong/missing canonical path
SILENTLY CREATES an empty DB instead of erroring. Plausible source of the 157 `no such table`
errors in zeus-live.err (a bare `main.ensemble_snapshots` etc. resolving against a freshly-created
empty wrong-path file). No corruption, but silent degradation.

## Survey — who relies on auto-create (grep-verified)
- **Runtime accessors** (get_trade/world/forecasts_connection → `_connect(<canonical>, ...)` at
  db.py:334/348/369/740/772/825/898/1034/1061; 43 files use get_*_connection): at RUNTIME the
  canonical DB MUST already exist; a `_connect` to a canonical path that is absent = a misresolved
  path = a bug → SHOULD fail-closed.
- **Legit creators** (must keep create): `init_schema` / `init_schema_trade_only` at BOOT
  (main.py:2887, 6558) create the canonical DBs' schema on first boot; tests create tmp DBs.
- Governance already exists: "Phase-1 staging allowlist for callers that may invoke `_connect()`"
  (db_writer_lock.py:911) + the `init_schema boot invariant` PLAN
  (docs/operations/task_2026-05-11_init_schema_boot_invariant/PLAN.md) — the fix MUST respect these.

## Fix design
Add `must_exist: bool = True` to `_connect` (and thread through the runtime factories):
- must_exist=True (default, runtime): if the canonical path is absent, RAISE (fail-closed) instead
  of creating. Open with `?mode=rw` (URI) so SQLite itself refuses to create; belt-and-suspenders
  with an explicit `db_path.exists()` check for a precise error message naming the misresolved path.
  Do NOT `mkdir` the parent in this path.
- create_ok / must_exist=False (boot init_schema, tests): keep today's `rwc` + mkdir behavior.
- The get_*_connection runtime factories pass must_exist=True; init_schema's connect passes False.

## Why sequenced AFTER the consult
The writer-unification consult (REQ-20260721-204133) is evaluating a "connection-factory pushdown"
that would put write-lease acquisition INTO the `_connect`/BEGIN-IMMEDIATE lifecycle — i.e. it
restructures the SAME function. W5-8 (open-mode/existence guard) and the pushdown (lock acquisition)
are different aspects of `_connect` and can coexist, but editing `_connect` twice risks conflict and
an incoherent design. Fold BOTH into one connection-layer pass once the consult's recommendation
lands. Design above is ready to implement then.

## Antibody (when implemented)
- must_exist=True + absent canonical path → raises a clear error (not an empty-DB create).
- must_exist=False + absent path → creates (boot path preserved).
- Existing get_*_connection against a present DB → unchanged.
- A wrong dash/underscore path (the stray-decoy class db_safety_gates already detects) → now also
  fails-closed at open instead of creating the decoy.
