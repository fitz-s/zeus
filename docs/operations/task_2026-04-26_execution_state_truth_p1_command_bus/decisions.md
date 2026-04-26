# P1 Operator Decision Brief — `venue_commands` schema and grammar

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: [project_brief.md](docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/project_brief.md), parent plan `task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md` §4.

Five decisions gate P1.S1 (schema + repo). Each carries options, recommendation, and rationale. **My recommendation marked ★.** I will proceed under the recommendations unless redirected.

---

## D-P1-1 — Where do `venue_commands` / `venue_command_events` live?

### Evidence

- Existing schema lives in [src/state/db.py:149](src/state/db.py:149) `init_schema()` — adds tables to the connection's DB
- `ZEUS_DB_PATH` resolves via `src/config.py`; current production path is `state/zeus-world.db`
- No migrations directory exists — schema is bootstrapped imperatively via `init_schema()`
- Existing canonical event tables (`position_events`, `position_current`) live in the same DB

### Options

| ID | Choice | Cost | Risk |
|----|--------|------|------|
| D-P1-1-a ★ | Add to existing `state/zeus-world.db` via `init_schema()` extension | One-line schema add per table; same connection model as existing tables | Low — co-locates command journal with position journal so cross-table joins work in single transaction |
| D-P1-1-b | New separate DB at `state/zeus-commands.db` | Connection plumbing; needs cross-DB `ATTACH` for joins | Higher — creates a second authority surface that complicates atomicity; violates the "single canonical truth" posture |
| D-P1-1-c | Use a real migration system (alembic, etc.) | Larger change; introduces a new convention | Distracts from P1 scope |

### Recommendation: **D-P1-1-a**

Add to `state/zeus-world.db` via `init_schema()`. Co-locating command and position journals lets us write commands + position events in the same transaction (which P1.S5 will require), preserves INV-08 ("Canonical write path has one transaction boundary"), and matches existing conventions.

---

## D-P1-2 — `venue_commands` schema columns

### Evidence

Looking at the `py_clob_client` SDK shape for `place_limit_order`, the fields needed for a full reconstruction of an order intent are: token_id, side, size, price, plus venue-side identity (orderID returned). To compose with chain × command UNKNOWN we also need `position_id`. To support idempotency we need a deterministic `idempotency_key`. To support recovery we need timestamps and current state.

### Recommended schema

```sql
CREATE TABLE IF NOT EXISTS venue_commands (
    command_id TEXT PRIMARY KEY,
    -- Identity
    position_id TEXT NOT NULL,                 -- foreign-style ref to position_events (logical, not enforced)
    decision_id TEXT NOT NULL,                 -- which decision spawned this command
    idempotency_key TEXT NOT NULL UNIQUE,      -- H(decision_id, token_id, side, price_units, intent_kind)
    intent_kind TEXT NOT NULL,                 -- "ENTRY" | "EXIT" | "CANCEL" | "DERISK"
    -- Order shape
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,                        -- "BUY" | "SELL"
    size REAL NOT NULL,
    price REAL NOT NULL,
    -- Venue identity (NULL until first ACK)
    venue_order_id TEXT,
    -- Lifecycle
    state TEXT NOT NULL,                       -- INTENT_CREATED | SUBMITTING | ACKED | UNKNOWN | PARTIAL | FILLED | CANCEL_PENDING | CANCELLED | EXPIRED | REJECTED | REVIEW_REQUIRED
    last_event_id TEXT,                        -- pointer to most recent venue_command_events row
    -- Timestamps
    created_at TEXT NOT NULL,                  -- ISO 8601 UTC
    updated_at TEXT NOT NULL,
    -- Optional review
    review_required_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_venue_commands_position ON venue_commands(position_id);
CREATE INDEX IF NOT EXISTS idx_venue_commands_state ON venue_commands(state);
CREATE INDEX IF NOT EXISTS idx_venue_commands_decision ON venue_commands(decision_id);
```

### Options for variations

| ID | Choice | Implication |
|----|--------|-------------|
| D-P1-2-a ★ | Schema as above | Sufficient for K4; sized for future event-sourced projection |
| D-P1-2-b | Drop `state` column; project from `venue_command_events` | Pure event-sourced — loses the index seek; recovery must scan events |
| D-P1-2-c | Add `attempt_count` + `next_retry_at` columns | Enables retry policy in-row; recommended P2 addition |

### Recommendation: **D-P1-2-a**

Approve the schema as above. `state` denormalization is intentional: recovery scans `WHERE state IN (SUBMITTING, UNKNOWN, REVIEW_REQUIRED)` need to be O(log n), not O(events). `attempt_count` is a P2 concern — defer.

---

## D-P1-3 — `venue_command_events` schema and grammar

### Recommended schema

```sql
CREATE TABLE IF NOT EXISTS venue_command_events (
    event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,              -- monotonically increasing per command_id
    event_type TEXT NOT NULL,                  -- see grammar below
    occurred_at TEXT NOT NULL,                 -- ISO 8601 UTC
    payload_json TEXT,                         -- raw venue response, error message, etc.
    state_after TEXT NOT NULL,                 -- the venue_commands.state value AFTER this event applies
    UNIQUE (command_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_venue_command_events_command ON venue_command_events(command_id);
CREATE INDEX IF NOT EXISTS idx_venue_command_events_type ON venue_command_events(event_type);
```

### Recommended event grammar

| `event_type` | When | `state_after` |
|--------------|------|---------------|
| `INTENT_CREATED` | Pre-submit row creation | `INTENT_CREATED` |
| `SUBMIT_REQUESTED` | Just before SDK submit call | `SUBMITTING` |
| `SUBMIT_ACKED` | SDK returned with venue_order_id | `ACKED` |
| `SUBMIT_REJECTED` | SDK returned an error | `REJECTED` |
| `SUBMIT_UNKNOWN` | SDK raised exception or timed out | `UNKNOWN` |
| `PARTIAL_FILL_OBSERVED` | Reconciliation found partial fill | `PARTIAL` |
| `FILL_CONFIRMED` | Reconciliation confirmed full fill | `FILLED` |
| `CANCEL_REQUESTED` | Operator/RED issued cancel | `CANCEL_PENDING` |
| `CANCEL_ACKED` | Venue confirmed cancel | `CANCELLED` |
| `EXPIRED` | Timeout passed without venue activity | `EXPIRED` |
| `REVIEW_REQUIRED` | Reconciliation cannot decide | `REVIEW_REQUIRED` |

### Options

| ID | Choice |
|----|--------|
| D-P1-3-a ★ | Schema + grammar as above |
| D-P1-3-b | Drop `state_after` (project state from events instead) — see D-P1-2-b implication |
| D-P1-3-c | Add `caused_by_event_id` to track event causality chains | P2 enhancement |

### Recommendation: **D-P1-3-a**

Approve schema and 11-event grammar as above. Append-only by design (no UPDATE on `venue_command_events`); enforce via repo API (no SQL UPDATE/DELETE statements emitted from `src/state/venue_command_repo.py`).

---

## D-P1-4 — Idempotency key format

### Evidence

Idempotency must survive: process restart, retry-after-UNKNOWN, replay scenarios. Per Polymarket SDK, there is no operator-supplied idempotency token at the API level — we must derive it deterministically from inputs.

### Options

| ID | Format | Stable across |
|----|--------|---------------|
| D-P1-4-a ★ | `sha256(decision_id \|\| token_id \|\| side \|\| f"{price:.4f}" \|\| f"{size:.4f}" \|\| intent_kind)[:32]` | Same decision + same token + same side + same price/size = same key |
| D-P1-4-b | `f"{decision_id}-{token_id}-{intent_kind}"` | Loses price/size variance — two distinct ENTRY commands at different prices share a key |
| D-P1-4-c | `uuid4()` per intent | NOT idempotent — every retry gets a new key, which defeats the purpose |

### Recommendation: **D-P1-4-a**

`sha256(...)[:32]` over the canonical fields. Size and price are formatted to fixed precision (4 decimal places) so floating-point representation noise doesn't change the key. The 32-char prefix gives 128 bits of distinct keys — collision-resistant for the operating regime.

---

## D-P1-5 — Append-only enforcement strategy

### Options

| ID | Choice | Cost |
|----|--------|------|
| D-P1-5-a ★ | Repo API only (`venue_command_repo.append_event()`); no SQL UPDATE/DELETE in any caller | Zero infrastructure; relies on grep/code-review discipline |
| D-P1-5-b | SQLite trigger that raises on UPDATE/DELETE of `venue_command_events` | One trigger per table; bullet-proof but adds schema complexity |
| D-P1-5-c | Both — trigger + repo discipline | Belt and suspenders; strongest enforcement |

### Recommendation: **D-P1-5-a** for P1; revisit after P1 closes

Repo-only enforcement is sufficient for the slice. Add a semgrep rule `zeus-no-direct-venue-command-update` (NC-18) that forbids any `UPDATE venue_command_events` or `DELETE FROM venue_command_events` outside the repo file. SQLite triggers (D-P1-5-c) are a nice harden-up later but not blocking for P1.S1.

---

## What I will do under recommendations

Unless redirected:

- **D-P1-1-a**: schema lives in `state/zeus-world.db` via `init_schema()` extension
- **D-P1-2-a**: `venue_commands` columns as listed above
- **D-P1-3-a**: `venue_command_events` schema + 11-event grammar as listed above
- **D-P1-4-a**: idempotency key = `sha256(...)[:32]`
- **D-P1-5-a**: repo-only enforcement + semgrep guard

Override any with the decision id (e.g. "D-P1-2-c") and I'll re-route.

## Operator gates not addressed in this brief

- **Schema migration safety**: existing zeus-world.db deployments will run `init_schema()` on next startup, which creates the new tables. Idempotent CREATE TABLE IF NOT EXISTS — no risk to existing data, but fresh environments will start with empty `venue_commands`. Pre-existing positions will not have command rows; recovery must handle that gracefully (treat as legacy, not as UNKNOWN).
- **Push to origin**: still deferred per operator directive; multiple co-tenant worktrees active.
- **`current_state.md` promotion**: P0 packet is not yet promoted; P1 would land downstream of that promotion.
