# Architecture Note — Execution-State Truth Re-Architecture

## 1. Current architecture problem

Zeus already states the correct authority model in multiple places:

- canonical truth belongs to repo-owned DB/event surfaces
- JSON/status/reporting outputs are derived
- `CHAIN_UNKNOWN` is first-class
- degraded authority should keep monitor/exit alive read-only
- `RED` must materially change behavior

However, the execution path still has a structural hole:

1. an execution intent is created in memory
2. live submission occurs
3. only after submission result returns does the runtime materialize and persist local authority around the trade

That ordering is survivable only in a synchronous, atomic RPC world. Polymarket is not that world. It has asynchronous order lifecycle, partial fills, authenticated user updates, and cutover-sensitive venue behavior. Therefore Zeus needs a durable execution truth surface that sits **before** side effects, not after them.

## 2. Target authority model

The target authority model for live execution becomes:

`venue/chain facts -> venue_commands -> venue_command_events -> position_events -> position_current -> projection exports/status`

Important consequences:

- `execution_report` is telemetry, not authority.
- `positions.json` is projection, not truth.
- a successful submit call is not by itself authoritative state.
- operator trust must follow the DB event spine, not convenience files.

## 3. New durable entities

## 3.1 `venue_commands`

`venue_commands` is the durable pre-side-effect journal.

Recommended minimal schema:

- `command_id` TEXT PRIMARY KEY
- `command_kind` TEXT CHECK in (`ENTRY`, `EXIT`, `CANCEL`, `DERISK`)
- `decision_id` TEXT NULL
- `linked_position_id` TEXT NULL
- `token_id` TEXT NOT NULL
- `side` TEXT NOT NULL
- `tif` TEXT NOT NULL
- `limit_price` REAL NOT NULL
- `shares` REAL NOT NULL
- `idempotency_key` TEXT NOT NULL UNIQUE
- `preflight_version` TEXT NOT NULL
- `venue_generation` TEXT NOT NULL
- `state` TEXT NOT NULL
- `state_reason` TEXT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

`venue_commands` is not the final truth of what the venue did. It is the durable local fact that Zeus decided to issue a specific command and recorded that fact **before** the network side effect.

## 3.2 `venue_command_events`

`venue_command_events` is the append-first truth of what Zeus later learned about that command.

Recommended minimal schema:

- `event_id` INTEGER PRIMARY KEY / monotonic key
- `command_id` TEXT NOT NULL
- `event_seq` INTEGER NOT NULL
- `event_type` TEXT NOT NULL
- `event_source` TEXT NOT NULL (`local_submit`, `venue_rest`, `venue_user_ws`, `chain`, `recovery`, `operator`)
- `authoritative` INTEGER NOT NULL
- `venue_order_id` TEXT NULL
- `venue_trade_id` TEXT NULL
- `payload_json` TEXT NULL
- `event_time` TEXT NOT NULL
- `recorded_at` TEXT NOT NULL

Important distinction:

- `venue_commands` answers **what Zeus intended and persisted**
- `venue_command_events` answers **what Zeus durably learned later**
- `position_events` answers **what position truth changed as a result**

## 3.3 `position_events` and `position_current`

These remain the canonical position truth surfaces. This upgrade does **not** demote them. It changes how execution-originated transitions are allowed to reach them.

New rule:

- order-submission-related changes to position authority must be justified by `venue_command_event`, not by local control flow alone

## 4. Target command state machine

Recommended command states:

- `INTENT_CREATED`
- `SUBMITTING`
- `ACKED`
- `UNKNOWN`
- `PARTIAL`
- `FILLED`
- `CANCEL_PENDING`
- `CANCELLED`
- `EXPIRED`
- `REJECTED`
- `REVIEW_REQUIRED`

Two design notes matter:

1. `UNKNOWN` is a real authority state, not a temporary local exception.
2. `REVIEW_REQUIRED` is stronger than a warning. It is an operator/escalation state that blocks new entry.

## 5. Submission discipline

## 5.1 Required sequence

The required sequence for any live order becomes:

1. Build command payload
2. Persist `venue_commands` row in `INTENT_CREATED`
3. Persist transition/event to `SUBMITTING`
4. Run venue preflight checks
5. Call execution gateway submit
6. Persist one of:
   - `ACKED`
   - `UNKNOWN`
   - `REJECTED`
7. Only after authoritative command event exists may position authority move because of the submit outcome

## 5.2 Why this matters

If the process dies after step 4 or during step 5, Zeus still has a durable command to recover from. Without that row, the system can only guess whether the venue saw the order.

## 6. Recovery model

A new recovery worker or startup recovery path shall:

1. load unresolved commands (`SUBMITTING`, `UNKNOWN`, `ACKED`, `PARTIAL`, `CANCEL_PENDING`)
2. query venue facts using available identifiers and idempotency information
3. consume user websocket/history if available
4. reconcile against chain only where chain authority is meaningful
5. append recovery events
6. escalate to `REVIEW_REQUIRED` where truth remains unresolved

Recovery must never synthesize “flat” just because the venue is temporarily incomplete.

## 7. Unknown/review-required semantics

`UNKNOWN` and `REVIEW_REQUIRED` are not just risk labels. They are execution-truth facts.

### `UNKNOWN`

Use when Zeus cannot currently prove whether a command was accepted, filled, cancelled, or absent.

### `REVIEW_REQUIRED`

Use when:

- command truth remains unresolved after bounded recovery
- contradictory authority sources exist
- external responses are malformed or stale in a way that prevents safe automation
- operator decision is required before new risk is allowed

### Required control consequence

Any unresolved `UNKNOWN` or `REVIEW_REQUIRED` command blocks new entry.

## 8. RED authoritative de-risk architecture

Current branch behavior marks active positions with `exit_reason="red_force_exit"` and lets the normal exit machinery pick them up later. That is better than entry-block-only, but it is not yet the target architecture.

Target behavior:

- `RED` produces durable `CANCEL` and `DERISK` / `EXIT` commands through the same command boundary
- pending orders are explicitly cancelled through command truth
- active positions receive de-risk or exit commands as durable work items
- operator state reflects pending unwind work

`RED` therefore becomes a truth-bearing execution mode, not only a local control flag.

## 9. Projection and export demotion

The upgrade must explicitly demote the following to projection-only status:

- `positions.json`
- status summaries
- any compatibility file that can be rebuilt from DB truth

Special rule:

- degraded projection can be **useful**, but it can never be **verified**

## 10. CLOB V2 readiness architecture

P0/P1 must account for official Polymarket V2 migration facts:

- V2 testing uses `https://clob-v2.polymarket.com`
- cutover moves V2 to the production URL `https://clob.polymarket.com`
- open orders are wiped at cutover
- old SDK integrations are not backward compatible
- fee/order construction semantics change in V2
- user/order/trade updates remain necessary for authoritative lifecycle handling

Therefore the runtime needs:

1. a preflight that verifies approved V2 integration state
2. a venue-generation stamp on command records
3. a cutover-recovery path for wiped open orders
4. a post-cutover reconcile drill

## 11. Manifest changes to add

This packet should add new architecture law rather than leave it implicit.

Recommended new invariants:

- **INV-23** — No venue side effect without a persisted `VenueCommand`
- **INV-24** — Order-submission-driven position authority requires `venue_command_event`
- **INV-25** — `UNKNOWN` / `REVIEW_REQUIRED` commands block new entries
- **INV-26** — degraded export is never `VERIFIED`
- **INV-27** — live order placement requires successful V2 preflight
- **INV-28** — direct `place_limit_order` outside execution gateway is forbidden

Recommended new negative constraints:

- **NC-16** — no direct venue placement outside execution gateway
- **NC-17** — no memory-only submit state for live commands
- **NC-18** — no verified label on degraded export payloads

## 12. Cross-file conflict rulings carried into implementation

This architecture note explicitly resolves the following conflicts:

### Conflict A — commit ordering

Old tests/comments claim JSON can still lead DB truth.  
Ruling: false current-law claim. `commit_then_export` and current cycle behavior control.

### Conflict B — FDR family split

Old tests/comments claim scope-aware family helpers do not exist.  
Ruling: false current-law claim. current code controls.

### Conflict C — RED behavior

The review states RED is entry-block-only.  
Ruling: partially stale. Runtime now sweeps by marking exit intent, but target architecture still requires stronger command-authoritative de-risk.

### Conflict D — degraded truth labeling

Current runtime mapping can still label degraded export as verified.  
Ruling: current code is wrong; active authority law controls; fix required in P0.

### Conflict E — V2 cutover date

Review states 2026-04-22. Official docs state 2026-04-28 (~11:00 UTC).  
Ruling: official venue docs control.

## 13. Implementation posture

This architecture note authorizes a staged change, not a single broad rewrite.

- P0 hardens unsafe surfaces immediately
- P1 introduces durable command truth
- P2 closes semantics
- P3 handles outer containment and persistent decision-law governance

The sequencing matters. Zeus should not add more execution cleverness, market breadth, or alpha-spending sophistication before command truth is durable.
