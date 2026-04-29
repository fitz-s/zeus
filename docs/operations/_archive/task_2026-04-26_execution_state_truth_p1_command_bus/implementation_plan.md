# P1 Implementation Plan — Durable Command Truth

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: [project_brief.md](docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/project_brief.md), [decisions.md](docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/decisions.md), parent plan §4.

## Sequencing law

Each slice obeys the P0-proven order:

1. **Law first** — INV/NC allocated in manifests
2. **Relationship tests** — written and verified RED against pre-impl code
3. **Implementation** — minimal change to land the slice
4. **Critic** — adversarial closure pass
5. **Close** — work_log entry + receipt update

No slice starts until the previous one passes critic.

---

## P1.S1 — Schema + Repo

### Goal

Create the durable journal infrastructure with NO live writers. All callers in P1.S3+ will use this through the repo API.

### Files to add

- `src/state/venue_command_repo.py` — append-only API
- `tests/test_venue_command_repo.py` — round-trip tests

### Files to modify

- `src/state/db.py` — extend `init_schema()` with the two new tables (per [decisions.md](docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/decisions.md) D-P1-1-a, D-P1-2-a, D-P1-3-a)
- `architecture/invariants.yaml` — add INV-28
- `architecture/negative_constraints.yaml` — add NC-18 (no direct UPDATE/DELETE on event table)
- `architecture/ast_rules/semgrep_zeus.yml` — add `zeus-no-direct-venue-command-update` rule

### Repo API

```python
# src/state/venue_command_repo.py
from typing import Iterable, Optional
import sqlite3

def insert_command(conn: sqlite3.Connection, *, command_id: str, position_id: str,
                   decision_id: str, idempotency_key: str, intent_kind: str,
                   market_id: str, token_id: str, side: str, size: float,
                   price: float, created_at: str) -> None:
    """INSERT new venue_commands row in INTENT_CREATED state.
    Atomically appends the INTENT_CREATED event in the same transaction.
    Raises sqlite3.IntegrityError if idempotency_key already exists.
    """

def append_event(conn: sqlite3.Connection, *, command_id: str,
                 event_type: str, occurred_at: str,
                 payload: Optional[dict] = None) -> str:
    """Append a venue_command_events row and update venue_commands.state.
    Returns the new event_id. Atomic.
    Raises ValueError on illegal grammar transition (state machine enforced).
    """

def get_command(conn: sqlite3.Connection, command_id: str) -> Optional[dict]:
    """Return command row as dict, None if not found."""

def find_unresolved_commands(conn: sqlite3.Connection) -> Iterable[dict]:
    """Yield commands in {SUBMITTING, UNKNOWN, REVIEW_REQUIRED}."""

def find_command_by_idempotency_key(conn: sqlite3.Connection, key: str) -> Optional[dict]:
    """Lookup an existing command by idempotency_key. Used by submit retry path."""

def list_events(conn: sqlite3.Connection, command_id: str) -> list[dict]:
    """Return all events for a command in sequence_no order."""
```

### State transition table (enforced in `append_event`)

| from \\ event | INTENT_CREATED | SUBMIT_REQUESTED | SUBMIT_ACKED | SUBMIT_REJECTED | SUBMIT_UNKNOWN | PARTIAL_FILL_OBSERVED | FILL_CONFIRMED | CANCEL_REQUESTED | CANCEL_ACKED | EXPIRED | REVIEW_REQUIRED |
|---|---|---|---|---|---|---|---|---|---|---|---|
| (initial) | ✓ | | | | | | | | | | |
| INTENT_CREATED | | ✓ | | | | | | | | | ✓ |
| SUBMITTING | | | ✓ | ✓ | ✓ | | | ✓ | | | ✓ |
| ACKED | | | | | | ✓ | ✓ | ✓ | | ✓ | ✓ |
| UNKNOWN | | | ✓ | ✓ | | ✓ | ✓ | ✓ | | ✓ | ✓ |
| PARTIAL | | | | | | ✓ | ✓ | ✓ | | ✓ | ✓ |
| FILLED | | | | | | | | | | | ✓ |
| CANCEL_PENDING | | | | | | | | | ✓ | ✓ | ✓ |

`✓` = legal transition; blank = illegal (raises `ValueError`).

### INV-28

```yaml
- id: INV-28
  zones: [K0_frozen_kernel, K2_runtime]
  statement: Every venue order side effect (place_limit_order / cancel) must be
    preceded by a persisted venue_commands row + venue_command_events SUBMIT_REQUESTED
    event written through src/state/venue_command_repo.py.
  why: Without pre-side-effect persistence, a daemon crash between SDK submit
    and ack write produces an orphan order at the venue. The command journal is
    the only mechanism that survives the crash and lets the next cycle reconcile.
  enforced_by:
    schema:
      - src/state/db.py::init_schema (venue_commands, venue_command_events tables)
    tests:
      - tests/test_venue_command_repo.py::test_insert_command_is_atomic_with_intent_created_event
      - tests/test_venue_command_repo.py::test_append_event_state_transition_is_grammar_checked
      - tests/test_venue_command_repo.py::test_idempotency_key_uniqueness_enforced
```

### NC-18

```yaml
- id: NC-18
  statement: No direct UPDATE or DELETE on venue_command_events outside src/state/venue_command_repo.py.
    The events table is append-only; mutations are recorded as new events, not edits.
  invariants: [INV-28]
  enforced_by:
    semgrep_rule_ids:
      - zeus-no-direct-venue-command-update
    tests:
      - tests/test_venue_command_repo.py::test_no_module_outside_repo_writes_events
```

### Acceptance gates for P1.S1

1. `pytest tests/test_venue_command_repo.py -v` all green.
2. `pytest tests/test_architecture_contracts.py::test_init_schema_creates_venue_command_tables` (new) green.
3. `pytest tests/test_p0_hardening.py` — still 25/1 (no regression).
4. Wide-suite regression at parity with `2a8902c` baseline.
5. Semgrep rule from NC-18 fails on a synthetic violation, passes the existing tree.
6. INV-28 + NC-18 manifest pointers all resolve via `pytest --collect-only`.

### Stop conditions

- Existing `init_schema()` cannot be extended atomically (tests fail mid-bootstrap) → defer to migration package decision.
- State transition table reveals an illegal-but-needed transition the executor will require — surface back to operator for grammar revision.
- Idempotency key format collides with an existing key generation in the codebase — surface to operator.

---

## P1.S2 — Command Bus types

### Goal

Type contract for `VenueCommand` + state grammar enums; no runtime caller yet. Pure types-as-tests so P1.S3 has a contract to compile against.

### Files to add

- `src/execution/command_bus.py`

### Surface

```python
# src/execution/command_bus.py
from dataclasses import dataclass
from enum import Enum

class CommandState(str, Enum):
    INTENT_CREATED = "INTENT_CREATED"
    SUBMITTING = "SUBMITTING"
    ACKED = "ACKED"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"

class CommandEventType(str, Enum):
    INTENT_CREATED = "INTENT_CREATED"
    SUBMIT_REQUESTED = "SUBMIT_REQUESTED"
    SUBMIT_ACKED = "SUBMIT_ACKED"
    SUBMIT_REJECTED = "SUBMIT_REJECTED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    PARTIAL_FILL_OBSERVED = "PARTIAL_FILL_OBSERVED"
    FILL_CONFIRMED = "FILL_CONFIRMED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACKED = "CANCEL_ACKED"
    EXPIRED = "EXPIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"

class IntentKind(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    CANCEL = "CANCEL"
    DERISK = "DERISK"

@dataclass(frozen=True)
class IdempotencyKey:
    """Sha256-derived deterministic key. Constructed via factory for stability."""
    value: str

    @staticmethod
    def from_inputs(*, decision_id: str, token_id: str, side: str,
                    price: float, size: float, intent_kind: IntentKind) -> "IdempotencyKey":
        ...

@dataclass(frozen=True)
class VenueCommand:
    """In-memory representation of a venue_commands row.
    Constructed pre-submit; submitted to repo for persistence; never mutated."""
    command_id: str
    position_id: str
    decision_id: str
    idempotency_key: IdempotencyKey
    intent_kind: IntentKind
    market_id: str
    token_id: str
    side: str
    size: float
    price: float
    state: CommandState
    venue_order_id: str = ""
    created_at: str = ""
    updated_at: str = ""
```

### INV-29

Anchors that the type surface is stable.

```yaml
- id: INV-29
  zones: [K2_runtime]
  statement: VenueCommand and IdempotencyKey are frozen dataclasses; CommandState
    and CommandEventType are closed string enums; downstream code may not extend
    them at runtime.
  why: A drifting type surface breaks the command journal's grammar guarantees.
    Fixed type frozen at the kernel boundary lets all callers (executor, repo,
    recovery, reconciliation) trust the same definitions.
  enforced_by:
    tests:
      - tests/test_command_bus_types.py::test_venuecommand_is_frozen
      - tests/test_command_bus_types.py::test_idempotency_key_is_deterministic
      - tests/test_command_bus_types.py::test_command_state_grammar_is_closed
      - tests/test_command_bus_types.py::test_command_event_type_grammar_is_closed
```

### Acceptance gates

1. `pytest tests/test_command_bus_types.py -v` all green.
2. `IdempotencyKey.from_inputs()` produces stable keys across separate process invocations (golden-test).
3. `VenueCommand(...).command_id = "x"` raises `dataclasses.FrozenInstanceError`.
4. `CommandState("BANANA")` raises `ValueError`.

### Stop conditions

- Operator approves a different idempotency key format → grammar change before P1.S3 starts.
- A frozen dataclass blocks an unexpected runtime mutation pattern in P1.S3 — surface and re-grammar.

---

## P1.S3 — Executor split

### Goal

`_live_order` factored into 4 phases. Side effect must follow persistence.

### Files to modify

- `src/execution/executor.py` — `_live_order` rewrite
- `src/state/venue_command_repo.py` — used as the only DB path

### Phase order

```
build:    create VenueCommand + IdempotencyKey  [pure, no I/O]
persist:  insert_command(...)                    [DB write]
              ↓ INTENT_CREATED event auto-appended
          append_event(SUBMIT_REQUESTED)
              ↓ state → SUBMITTING
submit:   client.place_limit_order(...)          [SDK call]
              ↓ may raise / return None / return order
ack:      append_event(SUBMIT_ACKED  if order)
            or  append_event(SUBMIT_REJECTED if rejected response)
            or  append_event(SUBMIT_UNKNOWN  if SDK raised / None)
              ↓ state set accordingly
return:   OrderResult derived from final state
```

### INV-30

```yaml
- id: INV-30
  zones: [K2_runtime]
  statement: client.place_limit_order MUST be preceded by a venue_commands row
    persisted with state=SUBMITTING within the SAME process invocation.
  why: Pre-side-effect persistence is the only mechanism that survives a daemon
    crash between submit and ack. Without it, an orphan order at the venue has
    no local record to reconcile against.
  enforced_by:
    tests:
      - tests/test_executor_command_split.py::test_persist_precedes_submit
      - tests/test_executor_command_split.py::test_submit_unknown_writes_event_with_state_unknown
      - tests/test_executor_command_split.py::test_idempotency_key_collision_raises_before_submit
```

### Acceptance gates

1. `pytest tests/test_executor_command_split.py -v` all green.
2. `pytest tests/test_p0_hardening.py` — gateway-only (R-G), V2 preflight (R-2) still hold; this slice doesn't undo P0 guards.
3. Mock-based ordering test verifies persist → submit → ack sequence.
4. Crash-injection test (Mock raises mid-submit): `venue_commands.state == SUBMITTING` is preserved; recovery loop sees the row.

### Stop conditions

- The crash-injection drill cannot be expressed as a pytest fixture — escalate to integration test plan.
- Existing position-event writes conflict with command writes inside `commit_then_export` — re-route the transaction boundary.

---

## P1.S4 — Recovery loop

### Goal

At cycle start, scan unresolved commands and resolve them against venue + chain.

### Files to add

- `src/execution/command_recovery.py`

### Files to modify

- `src/engine/cycle_runner.py` — call `command_recovery.reconcile_unresolved_commands(conn, clob)` near the existing chain-sync block

### Recovery resolution table

| Command state | Venue says | Chain says | Action |
|---------------|------------|------------|--------|
| SUBMITTING | order found | n/a | append SUBMIT_ACKED, state → ACKED |
| SUBMITTING | order not found | flat | append EXPIRED, state → EXPIRED |
| SUBMITTING | order not found | filled | append FILL_CONFIRMED, state → FILLED |
| SUBMITTING | timeout | n/a | append REVIEW_REQUIRED |
| UNKNOWN | order found | n/a | append SUBMIT_ACKED |
| UNKNOWN | order not found | filled | append FILL_CONFIRMED |
| UNKNOWN | order not found | flat | append REVIEW_REQUIRED (cannot decide between never-placed and immediately-canceled) |

### INV-31

```yaml
- id: INV-31
  zones: [K2_runtime]
  statement: Cycle start must scan venue_commands for unresolved states and
    apply reconciliation events; an unresolved command surviving N cycles
    transitions to REVIEW_REQUIRED.
  why: Without active reconciliation, UNKNOWN/SUBMITTING rows accumulate
    indefinitely. The recovery loop is the only path that turns runtime crash
    debris into a clean operator surface.
  enforced_by:
    tests:
      - tests/test_command_recovery.py::test_unresolved_submitting_resolves_to_acked_when_venue_has_order
      - tests/test_command_recovery.py::test_unresolved_unknown_with_filled_chain_resolves_to_filled
      - tests/test_command_recovery.py::test_unresolved_with_indeterminate_signals_marks_review_required
```

### Acceptance gates

1. `pytest tests/test_command_recovery.py -v` all green.
2. Integration test: persist a SUBMITTING command, mock venue + chain, run cycle, assert state advances correctly.
3. No regression in wide-suite.

### Stop conditions

- Venue REST API for "find order by id" needs operator endpoint approval — surface.
- Chain reconciliation already-existing logic conflicts with command-aware path — re-architect.

---

## P1.S5 — Discovery integration + idempotency

### Goal

`cycle_runtime.execute_discovery_phase` materializes position only from durable command-event outcome, and idempotency keys prevent duplicate submission on retry.

### Files to modify

- `src/engine/cycle_runtime.py`
- `src/execution/command_bus.py` — wire idempotency key construction
- `src/state/venue_command_repo.py` — `find_command_by_idempotency_key` used at submit retry

### Flow

```
discovery_phase loop:
  for each candidate that passes evaluator:
    decision_id = ...
    intent = create_execution_intent(...)
    idem_key = IdempotencyKey.from_inputs(decision_id, token_id, side, price, size, ENTRY)
    existing = repo.find_command_by_idempotency_key(idem_key)
    if existing and existing.state in TERMINAL_STATES:
        skip — already done
    elif existing and existing.state in IN_FLIGHT_STATES:
        skip — recovery loop will resolve
    else:
        result = executor._live_order(intent=intent, idempotency_key=idem_key, ...)
        if result.status == "ack":
            materialize_position(...)  # only after durable ack
        elif result.status == "rejected":
            log + skip
        else:
            # status == "unknown" — recovery will resolve next cycle
            log + skip materialization
```

### INV-32

```yaml
- id: INV-32
  zones: [K2_runtime]
  statement: Position authority advances (materialize_position) only AFTER the
    venue command has reached state=ACKED or state=FILLED.
  why: Pre-P1, materialize_position fired on `result.status == "pending"`. That
    races the command journal: a position appeared as "active" while the venue
    might never have received the order. Tying materialization to durable ack
    closes that window.
  enforced_by:
    tests:
      - tests/test_discovery_idempotency.py::test_materialize_skipped_for_unknown_command
      - tests/test_discovery_idempotency.py::test_idempotency_key_skips_duplicate_submit
      - tests/test_discovery_idempotency.py::test_retry_after_unknown_does_not_double_place
```

### NC-19

```yaml
- id: NC-19
  statement: Discovery phase must not call client.place_limit_order or
    materialize_position bypassing IdempotencyKey lookup.
  invariants: [INV-32]
  enforced_by:
    tests:
      - tests/test_discovery_idempotency.py::test_no_place_limit_order_without_idempotency_lookup
```

### Acceptance gates

1. `pytest tests/test_discovery_idempotency.py -v` all green.
2. End-to-end test: simulate a process restart between SUBMIT_REQUESTED and ack, verify next cycle's recovery resolves correctly without double-placing.
3. Wide-suite regression at parity.

### Stop conditions

- Existing `materialize_position` callers cannot be coordinated with command-driven flow — defer to slice 6.
- Tracker / strategy_tracker writes need re-ordering — surface to operator.

---

## After P1.S5 closes

- **K4 lands as P2** — small follow-up using the now-existing command journal:
  - chain × command UNKNOWN composition (already half-supported via INV-31)
  - remove fabricated `unknown_entered_at` (replace with command-event `created_at`)
  - RED → durable cancel/derisk/exit command emission

- **Operator gates re-evaluated** — V2 preflight endpoint-identity (O3) becomes addressable once command-bus has a known good ack response shape; full live re-enable becomes possible once P0 + P1 + K4 are all green.

---

## File registry for P1 packet

- `project_brief.md` — why and what
- `decisions.md` — five operator decisions (D-P1-1 .. D-P1-5)
- `implementation_plan.md` — this file
- `work_log.md` — to be appended as each slice closes
- `receipt.json` — to be added when P1.S1 starts
