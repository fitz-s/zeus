# P1 Work Log

## 2026-04-26 — P1.S1: Schema + Repo

### Scope landed

Created the durable command journal infrastructure: two new DB tables, an
append-only repo API, manifest law additions, a semgrep guard, and a test
suite. No live writers introduced (those land in P1.S3+).

### Sequence executed

1. Read `implementation_plan.md` §P1.S1 and `decisions.md` to fix exact schema
   and transition table before touching any file.
2. Extended `src/state/db.py::init_schema()` — appended `venue_commands` and
   `venue_command_events` `CREATE TABLE IF NOT EXISTS` blocks at the end of the
   existing `executescript("""...""")` body, plus 5 `CREATE INDEX IF NOT EXISTS`
   statements. Idempotent by design.
3. Created `src/state/venue_command_repo.py` — 6 public functions, 33 legal
   transitions encoded in `_TRANSITIONS` dict, atomicity via `with conn:`,
   positional row access inside `append_event` to be row_factory-agnostic.
4. Added INV-28 to `architecture/invariants.yaml` (exact plan wording).
5. Added NC-18 to `architecture/negative_constraints.yaml` (exact plan wording).
6. Added `zeus-no-direct-venue-command-update` to
   `architecture/ast_rules/semgrep_zeus.yml` (exact plan rule body).
7. Added FM-NC-18 entry to `architecture/ast_rules/forbidden_patterns.md`
   following FM-NC-16 pattern.
8. Updated `tests/test_architecture_contracts.py`:
   - `test_semgrep_rules_cover_core_forbidden_moves` now checks 5 rule IDs
     (added `zeus-no-direct-venue-command-update`).
   - `test_init_schema_creates_venue_command_tables` (new) verifies both tables
     and all 5 indexes on fresh in-memory DB.
9. Created `tests/test_venue_command_repo.py` — 44 tests across 8 test classes
   covering atomicity, grammar, idempotency, find_unresolved, list_events,
   payload round-trip, and AST-walk NC-18 enforcement.

### Verification commands

```
pytest tests/test_venue_command_repo.py -v
  → 44 passed

pytest tests/test_architecture_contracts.py::test_init_schema_creates_venue_command_tables \
       tests/test_architecture_contracts.py::test_semgrep_rules_cover_core_forbidden_moves -v
  → 2 passed

pytest tests/test_p0_hardening.py --tb=no -q
  → 25 passed, 1 skipped  (baseline parity)

pytest tests/test_phase5a_truth_authority.py tests/test_phase8_shadow_code.py \
       tests/test_executor_typed_boundary.py tests/test_pre_live_integration.py \
       tests/test_architecture_contracts.py tests/test_runtime_guards.py \
       tests/test_live_execution.py tests/test_dual_track_law_stubs.py --tb=no -q
  → 18 failed, 234 passed, 25 skipped  (≤18 baseline; same pre-existing failures)
```

### Touched files

- `src/state/db.py` — schema extension (venue_commands, venue_command_events + indexes)
- `src/state/venue_command_repo.py` — new module
- `tests/test_venue_command_repo.py` — new test file (44 tests)
- `architecture/invariants.yaml` — INV-28
- `architecture/negative_constraints.yaml` — NC-18
- `architecture/ast_rules/semgrep_zeus.yml` — zeus-no-direct-venue-command-update
- `architecture/ast_rules/forbidden_patterns.md` — FM-NC-18
- `tests/test_architecture_contracts.py` — 2 updates

### Commit

`0a7845f` — Land P1.S1: venue_commands schema + repo + INV-28 / NC-18

### State transition table

Implemented exactly as specified in `implementation_plan.md` §P1.S1.
No additions or removals. Table verified by parametrized illegal-transition
tests (23 parametrize cases) + 8 legal-transition positive tests.

---

## 2026-04-26 — P1.S1 critic followup (commit `6112d74`)

Critic verdict on P1.S1 was APPROVE-WITH-FOLLOWUP with 2 MAJORs + 2 MEDIUMs
+ 2 LOWs. Critic explicitly said: "MAJOR-1 (savepoint hazard) and MAJOR-2
(AST test is paper-only) must be closed before P1.S3 wires a real caller."
This commit closes all six.

### MAJOR-1 — savepoint composability

Project memory L30 (`feedback_with_conn_nested_savepoint_audit`) had already
flagged that Python sqlite3 `with conn:` commits + RELEASEs SAVEPOINTs.
P1.S1 used `with conn:` in `insert_command` and `append_event`, meaning
P1.S3 executor wrapping `_live_order` in its own SAVEPOINT would have lost
atomicity. Replaced with `_savepoint_atomic(conn)` context manager: explicit
named SAVEPOINT, RELEASE on success, ROLLBACK TO + RELEASE on exception.
SAVEPOINTs nest correctly so callers compose freely.

Regression test class `TestSavepointComposability` covers both
`insert_command` and `append_event` inside an outer SAVEPOINT, with ROLLBACK
TO undoing all repo writes (command row, auto-event, command state).

### MAJOR-2 — real AST walk for NC-18

`TestNoModuleOutsideRepoWritesEvents` was substring matching despite an
`import ast`. Critic confirmed bypasses: f-string templates, quoted
identifiers, double-space whitespace. Replaced with real `ast.walk` over
`Constant` string nodes, matched against a regex tolerant of: case
variations (`UPDATE` / `update`), quoting (`"venue_command_events"`,
`` `venue_command_events` ``), arbitrary whitespace, and either table
(`venue_commands` or `venue_command_events`). Two self-tests guarantee
the regex catches 8 known evasion shapes and does not false-positive on
benign `SELECT` statements.

### MEDIUM-1 — payload datetime/bytes coercion

`json.dumps(payload)` raised `TypeError` on `datetime`. P1.S4 recovery
loop will routinely attach `datetime` payloads. Added `_payload_default`
serializer: ISO-formats `datetime`/`date`, hex-encodes `bytes`, raises
`TypeError` with a clear pointer message for genuinely opaque types.
Three round-trip tests added.

### MEDIUM-2 — semgrep scope asymmetry

Semgrep rule covered events table only; AST test covered both events and
commands UPDATE/DELETE. Picked the broader scope: extended semgrep with
4 more `pattern-either` cases for `venue_commands` UPDATE/DELETE. NC-18
statement and `enforced_by.tests` updated to match (now lists three test
functions: the main AST walk + 2 regex self-tests).

### LOW-1 — row_factory swap encapsulation

Replaced ad-hoc `try/finally` swap pattern in 4 read functions with a
`_row_factory_as(conn, factory)` context manager. Same behavior; harder
to drift from in future edits.

### LOW-2 — schema-in-executescript polish

Deferred per implementation_plan stop conditions; not a blocker.

### Verification

```
pytest tests/test_venue_command_repo.py
  → 49 passed (was 44; +5 new for MAJOR-1 + MEDIUM-1)

pytest tests/test_p0_hardening.py tests/test_architecture_contracts.py
  tests/test_venue_command_repo.py
  → 149 passed, 23 skipped

pytest wide-sweep (8 files)
  → 18 failed, 234 passed, 25 skipped  (exact baseline parity with 2a8902c)
```

### Touched files

- `src/state/venue_command_repo.py` — `_savepoint_atomic`, `_row_factory_as`,
  `_payload_default` helpers; switch all mutators to `_savepoint_atomic`;
  switch all readers to `_row_factory_as`; `json.dumps(default=...)`
- `tests/test_venue_command_repo.py` — `TestSavepointComposability` (2 tests),
  `TestAppendEventPayloadCoercion` (3 tests), real-AST `TestNoModuleOutsideRepoWritesEvents`
  with 2 regex self-tests
- `architecture/ast_rules/semgrep_zeus.yml` — semgrep rule scope expanded
- `architecture/negative_constraints.yaml` — NC-18 statement updated, 3 tests in enforced_by

### P1.S1 closure status

All critic findings closed. Slice is now P1.S3-ready. P1.S2 (command_bus types)
can start whenever operator confirms.

---

## 2026-04-26 u2014 P1.S3: Executor split build/persist/submit/ack

### Scope landed

Wired the P1.S1 repo + P1.S2 typed surface into the live order path. Both
`_live_order` (entry / IntentKind.ENTRY) and `execute_exit_order` (exit /
IntentKind.EXIT) now run 4-phase buildu2192persistu2192submitu2192ack before returning
OrderResult. INV-30 registered in invariants.yaml.

### File:line targets

- `src/execution/executor.py:13-36` u2014 added `import sqlite3` + module-level
  `from src.state.db import get_connection` for testability
- `src/execution/executor.py:214-476` u2014 `execute_exit_order` rewritten with
  build/persist/submit/ack phases; accepts `conn` and `decision_id` params
- `src/execution/executor.py:479-750` u2014 `_live_order` rewritten with 6 phases
  (ExecutionPrice guard, build, persist, V2 preflight gate, submit, ack);
  accepts `conn` and `decision_id` params
- `architecture/invariants.yaml` u2014 INV-30 added after INV-28 block
- `tests/test_executor_command_split.py` u2014 new file, 13 tests
- `tests/test_p0_hardening.py` u2014 `TestR2V2PreflightBlocksPlacement` extended
  with `_mem_conn` fixture; test methods updated to pass `conn=_mem_conn`
- `tests/test_live_execution.py` u2014 autouse `_mem_conn` fixture added
- `tests/test_executor_typed_boundary.py` u2014 autouse `_inject_mem_conn` fixture added

### INV-30 anchor

client.place_limit_order MUST be preceded by a venue_commands row persisted
with state=SUBMITTING within the SAME process invocation. Crash between submit
and ack leaves the row in SUBMITTING for recovery loop (P1.S4).

### Phase order (entry path)

1. ExecutionPrice validation (pre-persist guard)
2. build: IdempotencyKey.from_inputs + VenueCommand (pure)
3. persist: insert_command (INTENT_CREATED) + append_event (SUBMIT_REQUESTED)
4. V2 preflight (if fails: SUBMIT_REJECTED appended, return rejected)
5. submit: client.place_limit_order
6. ack: SUBMIT_ACKED / SUBMIT_REJECTED / SUBMIT_UNKNOWN based on result

Exit path omits V2 preflight (5 phases total).

### Verification commands

```
pytest tests/test_executor_command_split.py -v
  u2192 13 passed

pytest tests/test_p0_hardening.py tests/test_command_bus_types.py
       tests/test_venue_command_repo.py -q
  u2192 131 passed, 1 skipped  (no regression)

pytest tests/test_phase5a_truth_authority.py tests/test_phase8_shadow_code.py
       tests/test_executor_typed_boundary.py tests/test_pre_live_integration.py
       tests/test_architecture_contracts.py tests/test_runtime_guards.py
       tests/test_live_execution.py tests/test_dual_track_law_stubs.py --tb=no -q
  u2192 18 failed, 234 passed, 25 skipped  (exact baseline parity with 1453eaf)
```

### Crash-injection drill result

`test_submit_unknown_writes_event_with_state_unknown` (both entry and exit):
place_limit_order raises RuntimeError. venue_commands row present with
state=UNKNOWN; find_unresolved_commands returns 1 row; event chain contains
INTENT_CREATED + SUBMIT_REQUESTED + SUBMIT_UNKNOWN. Recovery loop can resolve.

### Touched files

- `src/execution/executor.py` u2014 entry + exit paths rewritten
- `architecture/invariants.yaml` u2014 INV-30
- `tests/test_executor_command_split.py` u2014 new (13 tests)
- `tests/test_p0_hardening.py` u2014 mem_conn fixture for R-2 tests
- `tests/test_live_execution.py` u2014 autouse mem_conn fixture
- `tests/test_executor_typed_boundary.py` u2014 autouse mem_conn fixture

### Commit

`4fcb2db` u2014 Land P1.S3: executor split build/persist/submit/ack u2014 INV-30

### Deviations

- `execute_exit_order` signature change extended to the P1.S5 wire-up surface
  (`conn`, `decision_id` params); existing callers that pass no conn still work
  via the `get_connection()` fallback.
- `test_executor_typed_boundary.py`, `test_live_execution.py`,
  `test_p0_hardening.py` required in-memory DB fixture additions because
  these pre-existing tests call `_live_order` without conn and the new persist
  phase otherwise hits a bare `get_connection()` against an uninitialized file
  DB. Fixes are minimal: autouse fixtures patching `get_connection` at the
  executor module level.
- `test_clob_raises_exception` expected `CLOB down` in result.reason; the new
  code returns `submit_unknown: CLOB down` which still satisfies `in` check.

## 2026-04-26 u2014 P1.S3 critic-followup: parallel reviewer + critic findings

### Findings closed

- **CRITICAL** (DB target, code-reviewer): `get_trade_connection_with_world()` already
  in working tree; confirmed correct in both paths.
- **HIGH / MAJOR-1** (connection leak): outer `try/finally` wrapping persist/submit/ack
  body; `conn.close()` fires only on `_own_conn=True`. Placement: `executor.py` `try:`
  block contains all phases through `return result_obj`; `finally:` is last statement.
- **MEDIUM-1** (idempotency collision retry): `IntegrityError` handler now looks up
  existing row via `find_command_by_idempotency_key` + `VenueCommand.from_row`; maps
  all CommandState values to appropriate OrderResult; external_order_id threaded through.
- **MEDIUM-2** (entry ACKED OrderResult): `_live_order` SUBMIT_ACKED result_obj now
  carries `external_order_id=order_id`, `venue_status`, `idempotency_key=idem.value`.
- **MEDIUM-3** (payload shapes): SUBMIT_UNKNOWN u2192 `{exception_type, exception_message}`;
  SUBMIT_REJECTED (None/missing_order_id) u2192 `{reason: clob_returned_none}`;
  SUBMIT_ACKED u2192 adds `venue_status`; applied in both paths.
- **MAJOR-2** (synthetic decision_id WARNING): `logger.warning` fires on both paths
  when `decision_id=""`; defers conn/decision_id threading to P1.S5.
- **MAJOR-3** (fixture patch target): `test_live_execution.py` and
  `test_executor_typed_boundary.py` now patch `get_trade_connection_with_world`.
- **MAJOR-4** (missing autouse fixture): `test_executor.py` and
  `test_polymarket_error_matrix.py` now have autouse `_mem_conn` fixture; resolved
  +5 regressions (5 failures u2192 0).
- **MAJOR-5** (INV-30 cross-DB doc): `architecture/invariants.yaml` INV-30 statement
  extended with cross-DB atomicity caveat.

### Verification commands run

```
pytest tests/test_executor_command_split.py -v  # 18 passed + 2 (db_target) = 20 passed
pytest tests/test_executor.py tests/test_polymarket_error_matrix.py  # 17 passed
pytest tests/test_p0_hardening.py tests/test_command_bus_types.py tests/test_venue_command_repo.py  # 118 passed
pytest [wide-sweep 8 files] --tb=no -q  # 18 failed (parity with baseline)
```

### Touched files

- `src/execution/executor.py` u2014 try/finally, collision retry, WARNING, payload shapes, ACKED result
- `architecture/invariants.yaml` u2014 INV-30 cross-DB sentence + 6 new test citations
- `tests/test_executor_command_split.py` u2014 +5 new tests (collision retry u00d73, warning, payload)
- `tests/test_executor_db_target.py` u2014 new file (2 tests: DB target regression)
- `tests/test_executor.py` u2014 autouse `_mem_conn` fixture
- `tests/test_polymarket_error_matrix.py` u2014 autouse `_mem_conn` fixture
- `tests/test_live_execution.py` u2014 patch target + `clob_returned_none` assertion
- `tests/test_executor_typed_boundary.py` u2014 patch target

### Commit

`f7fc9be` u2014 P1.S3 followup: critic + reviewer fixes u2014 DB target, close, collision-retry, payload, fixtures

## 2026-04-26 u2014 P1.S4: Command recovery loop u2014 INV-31

### Scope landed

New recovery loop that runs at cycle start, scanning venue_commands for rows in
IN_FLIGHT_STATES and reconciling each against venue truth via `get_order` lookup.
Appends durable events per the u00a7P1.S4 resolution table. Opens its own trade conn
internally (P1.S5 will thread from cycle_runner).

### Sequence executed

1. Read `implementation_plan.md` u00a7P1.S4, `command_bus.py`, `venue_command_repo.py`,
   `polymarket_client.py`, and `cycle_runner.py` for full context.
2. Extended `src/data/polymarket_client.py`: added `get_order(order_id)` u2014 wraps
   `_clob_client.get_order`, normalizes to `{orderID, status}`, returns `None` on
   404/not-found (catches `httpx.HTTPStatusError` + "not found" text heuristic).
3. Created `src/execution/command_recovery.py`: `reconcile_unresolved_commands(conn, client)`
   with lazy-init for both args; `_reconcile_row()` applies the resolution table;
   per-row try/except so a single bad row cannot kill the loop.
4. Wired into `src/engine/cycle_runner.py`: after chain-sync/orphan-cleanup block,
   before entry-bankroll / entry-decision blocks; wrapped in try/except that records
   `summary["command_recovery"]` on both success and error paths.
5. Added INV-31 to `architecture/invariants.yaml` (YAML-safe u2014 removed `):` pattern
   that triggered ScannerError on the `caveat):` parenthetical in earlier draft).
6. Created `tests/test_command_recovery.py` u2014 11 tests (8 INV-31 anchor + 3 supplementary).

### Grammar deviation from plan

The plan specified SUBMITTING-without-venue_order_id u2192 EXPIRED. `_TRANSITIONS` has
no `("SUBMITTING", "EXPIRED")` edge (operator decision; plan explicitly forbids
modifying `_TRANSITIONS`). Recovery emits `REVIEW_REQUIRED` instead (legal from
SUBMITTING) with `payload["reason"] = "recovery_no_venue_order_id"`. Documented in
module docstring and test comment. Test name kept as `test_submitting_without_order_id_resolves_to_expired`
per INV-31 manifest but assertion checks `REVIEW_REQUIRED`.

### Cross-DB handling

Recovery opens its own `get_trade_connection_with_world()` connection when `conn=None`
(try/finally close). Cycle_runner passes no conn today and calls
`reconcile_unresolved_commands()` with no args; P1.S5 will thread the trade conn.

### Verification commands

```
pytest tests/test_command_recovery.py -v
  -> 11 passed

pytest tests/test_executor_command_split.py tests/test_p0_hardening.py \
       tests/test_command_bus_types.py tests/test_venue_command_repo.py -v
  -> 136 passed, 1 skipped  (no regression vs P1.S3 baseline)

pytest tests/ -q [wide sweep]
  -> 129 failed, 2837 passed  (baseline b5fffb6: 130 failed -- no regression)
```

### Touched files

- `src/execution/command_recovery.py` u2014 new module (INV-31 recovery loop)
- `src/data/polymarket_client.py` u2014 `get_order()` SDK passthrough
- `src/engine/cycle_runner.py` u2014 recovery call after chain-sync
- `architecture/invariants.yaml` u2014 INV-31
- `tests/test_command_recovery.py` u2014 new (11 tests)

### Commit

`828319d` u2014 Land P1.S4: command recovery loop u2014 INV-31

---

## 2026-04-26 u2014 P1.S5: Discovery integration + idempotency lookup (P1 FINAL)

### Scope landed

Final P1 slice. Closes four open items: NC-19 pre-submit idempotency gate, INV-32
materialize_position authority gate, `_orderresult_from_existing()` helper
(eliminates 4-way drift deferred from P1.S3 critic review), and `command_state`
field on `OrderResult` propagating durable-ack signal to cycle_runtime.

### Sequence executed

1. Added `command_state: Optional[str] = None` field to `OrderResult` dataclass
   in `src/execution/executor.py`.
2. Extracted `_orderresult_from_existing()` helper (single definition, 4 call
   sites: pre-submit lookup + IntegrityError handler in both `_live_order` and
   `execute_exit_order`). Eliminates drift that P1.S3 critic flagged as MAJOR.
3. Added pre-submit idempotency lookup (`find_command_by_idempotency_key` call
   before INSERT) in both `_live_order` and `execute_exit_order`. IntegrityError
   handler remains as race-condition safety belt.
4. Added `command_state="ACKED"` to success result in both `_live_order` and
   `execute_exit_order`.
5. Updated `execute_intent()` signature: `conn=None, decision_id=""` kwargs;
   threads both to `_live_order`.
6. Updated `cycle_runtime.execute_discovery_phase`: threads
   `decision_id=str(d.decision_id) if d.decision_id else ""` into
   `execute_intent` call. Deliberately does NOT pass `conn` (P2 concern: cycle
   conn targets zeus.db, venue_commands live in zeus_trades.db).
7. Added INV-32 materialize gate in `execute_discovery_phase`: only calls
   `materialize_position` when `result.command_state in ("ACKED",
   "PARTIAL", "FILLED")`. SUBMITTING/UNKNOWN logs WARNING per INV-32 and skips.
8. Fixed YAML formatting in `architecture/invariants.yaml` INV-32 `why:` block
   (YAML scalar colon issue; used block scalar `>`).
9. Added `command_state="ACKED"` to stubs in `test_architecture_contracts.py`
   and `test_runtime_guards.py` (INV-32 gate requires durable ack for materialize
   path in existing tests).
10. Created `tests/test_discovery_idempotency.py` (7 tests, P1.S5 spec).
11. Added INV-32 to `architecture/invariants.yaml`.
12. Added NC-19 to `architecture/negative_constraints.yaml`.

### Verification commands

```
pytest tests/test_discovery_idempotency.py -v
  -> 6 passed, 1 xpassed (decision_id threading xfail now passes)

pytest tests/test_executor_command_split.py tests/test_command_recovery.py \
       tests/test_p0_hardening.py tests/test_command_bus_types.py \
       tests/test_venue_command_repo.py tests/test_architecture_contracts.py \
       tests/test_runtime_guards.py tests/test_discovery_idempotency.py -q
  -> 16 failed, 329 passed, 23 skipped, 1 xpassed
     (baseline: 16 failed, 323 passed, 23 skipped; parity confirmed)
```

### Touched files

- `src/execution/executor.py` u2014 `command_state` field, `_orderresult_from_existing()`,
  pre-submit lookup, `conn`/`decision_id` threading
- `src/engine/cycle_runtime.py` u2014 `decision_id` wiring, INV-32 materialize gate
- `architecture/invariants.yaml` u2014 INV-32
- `architecture/negative_constraints.yaml` u2014 NC-19
- `tests/test_discovery_idempotency.py` u2014 new (7 tests)
- `tests/test_architecture_contracts.py` u2014 `command_state="ACKED"` stub fix
- `tests/test_runtime_guards.py` u2014 `command_state="ACKED"` stub fix

### Commits

- `30c63b8` — Land P1.S5: discovery integration + idempotency lookup — INV-32 / NC-19
- `7211f5b` — Record P1.S5 closeout — P1 packet complete

---

## 2026-04-26 — P1.S4 critic-followup: status-aware recovery (commit `973d23c`)

Closes the parallel code-reviewer findings on commit `828319d` (P1.S4 recovery loop):

- **HIGH-1 (symmetric grammar fix)**: `SUBMITTING + venue returns None`
  emitted illegal `EXPIRED` event. Symmetric to the no-venue_order_id branch
  that already routed to `REVIEW_REQUIRED`; fix mirrors that path. Row no
  longer gets stuck looping in `summary["errors"]`.
- **HIGH-2 (status-aware recovery)**: `SUBMITTING + venue_resp` unconditionally
  emitted `SUBMIT_ACKED` even when `status="REJECTED"`. Now branches:
  - `REJECTED` → `SUBMIT_REJECTED`
  - `CANCELLED/CANCELED/EXPIRED` → `REVIEW_REQUIRED` (terminal-no-fill)
  - `LIVE/MATCHED/active/...` → `SUBMIT_ACKED` (status captured in payload)
  Same logic mirrored for `UNKNOWN` state.
- `_TRANSITIONS` unchanged — recovery only uses existing legal grammar paths.
- INV-31 unchanged.

Verified: `tests/test_command_recovery.py` 11/11 pass.

---

## 2026-04-26 — P1 PACKET COMPLETE

All five slices shipped + critic/reviewer-follow-up close:

| Slice | Commits | INV/NC |
|-------|---------|--------|
| P1.S1 schema + repo | `0a7845f` `7ebed4e` `6112d74` `904a989` | INV-28 + NC-18 |
| P1.S2 typed surface | `447ed3c` `7d48eb5` `1453eaf` | INV-29 |
| P1.S3 executor split | `4fcb2db` `f0783f8` `f7fc9be` `b5fffb6` | INV-30 |
| P1.S4 recovery loop | `828319d` `6f567ae` `973d23c` | INV-31 |
| P1.S5 discovery + idempotency | `30c63b8` `7211f5b` | INV-32 + NC-19 |

### Final test counts

```
pytest tests/test_command_bus_types.py tests/test_venue_command_repo.py \
       tests/test_executor_command_split.py tests/test_command_recovery.py \
       tests/test_discovery_idempotency.py tests/test_p0_hardening.py
  → 153 passed, 1 skipped, 1 xpassed
```

P0 + P1 invariants summary:

- Manifest invariants added: INV-23, INV-24, INV-25, INV-26, INV-27 (P0) +
  INV-28, INV-29, INV-30, INV-31, INV-32 (P1).
- Negative constraints added: NC-16, NC-17 (P0) + NC-18, NC-19 (P1).
- New runtime modules: `src/runtime/posture.py`, `src/state/venue_command_repo.py`,
  `src/execution/command_bus.py`, `src/execution/command_recovery.py`.

### What lands in P2 (deferred)

- **K4** chain × command UNKNOWN composition + remove fabricated
  `unknown_entered_at` + RED → durable cancel/derisk/exit command emission.
- **Cross-DB threading**: `cycle_runtime.execute_discovery_phase` should pass
  `trade_conn` and `clob` into `reconcile_unresolved_commands(...)` and
  `execute_intent(..., conn=trade_conn)` to avoid the dual-connection cost.
- **SDK 404 detection**: replace text-heuristic in `client.get_order` with
  typed exception path (per P1.S4 critic MAJOR-1 noted; not blocking P1).
- **Response normalization**: add `orderId` (camelCase) to `get_order` fallback
  chain (per P1.S4 reviewer MINOR-1).

### Operator gates

- Push to origin: pending operator decision.
- Promote `current_state.md` to point at this packet: operator-only.
