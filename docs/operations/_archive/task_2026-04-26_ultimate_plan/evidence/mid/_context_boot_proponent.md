# Region-Mid solo context boot — proponent-mid

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (`main`)
Author: proponent-mid
Verification window: <10 min from cite to lock (Memory L20 grep-gate)

## Files read (path:line ranges)

| File | Lines read | Reason |
|---|---|---|
| `src/state/venue_command_repo.py` | 1-366 (full) | Repo API + state-transition table — INV-30 anchor |
| `src/execution/executor.py` | 1-1029 (full) | `_live_order` + `execute_exit_order` persist→submit→ack chain |
| `src/execution/command_bus.py` | 1-316 (full) | Closed enums (CommandState/CommandEventType/IntentKind) + IdempotencyKey |
| `src/execution/command_recovery.py` | 1-386 (full) | Recovery loop scanning IN_FLIGHT_STATES |
| `src/engine/cycle_runtime.py` | 1-1513 (full) | `execute_discovery_phase` materialize gate (INV-32) |
| `src/engine/cycle_runner.py` | 100-150, 340-400 | RED-force-exit-sweep + execution-truth warnings |
| `src/state/chain_reconciliation.py` | 1-120 | LEARNING_AUTHORITY_REQUIRED + position-vs-chain reconciliation |
| `src/riskguard/risk_level.py` | 1-32 (full) | RiskLevel enum + LEVEL_ACTIONS |
| `src/data/polymarket_client.py` | 200-300 | get_order/cancel_order/get_open_orders/get_positions_from_api |
| `docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md` | 280-460 | INV-30/31/32/NC-19 statements + acceptance gates |
| `docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/work_log.md` | 480-560 | "What lands in P2 (deferred)" enumeration |
| `docs/operations/task_2026-04-26_ultimate_plan/evidence/apr26_findings_routing.yaml` | full | All 31 findings + 17 §8.3 transitions |

## INV-30 / INV-31 / INV-32 / NC-19 grep-verification

### INV-30 (persist before submit)

`venue_command_repo.insert_command` → INSERT INTO venue_commands + APPEND
`INTENT_CREATED` event in same SAVEPOINT (`src/state/venue_command_repo.py:194-242`).
`_live_order` calls `insert_command` THEN `append_event(SUBMIT_REQUESTED)` THEN
`client.place_limit_order` (`src/execution/executor.py:814-922`). Same connection,
same process. SAVEPOINT-based atomicity composes with caller transactions
(`venue_command_repo.py:111-130`).

**Verdict**: F-001 (side-effect-before-durable) is structurally CLOSED on HEAD
874e00c. The durable command journal exists at venue_commands +
venue_command_events, with append-only event log and grammar-checked transitions.

### INV-31 (recovery scan)

`command_recovery.reconcile_unresolved_commands` scans
`IN_FLIGHT_STATES = {SUBMITTING, UNKNOWN, REVIEW_REQUIRED, CANCEL_PENDING}`
(`src/execution/command_bus.py:92-97`) and resolves each via venue lookup
(`src/execution/command_recovery.py:139-301`). Wired into cycle start
(referenced from cycle_runner and cycle_runtime).

**Verdict**: Recovery loop exists. Coverage gap: no PARTIAL_FILL_OBSERVED
emission, no CANCEL_FAILED emission, no chain-truth fallback (deferred to
P2/K4 per `command_recovery.py:9-13`).

### INV-32 (materialize after ACKED|FILLED)

cycle_runtime.execute_discovery_phase L1357-1426: materialize_position is
gated on `_cmd_durable = command_state in ("ACKED", "PARTIAL", "FILLED")`.
SUBMITTING/UNKNOWN paths skip materialization with warning log.

**Verdict**: F-006 position-vs-execution authority direction is structurally
CORRECT — execution truth (command_state) gates position truth (materialize).
Coverage gap: no exchange-side reconciliation loop comparing
exchange.get_positions vs venue_commands FILLED/ACKED set.

### NC-19 (idempotency lookup before place)

`_live_order` calls `find_command_by_idempotency_key` BEFORE `insert_command`
(`src/execution/executor.py:797-812`). If found, returns
`_orderresult_from_existing(...)` without invoking SDK. Race-condition safety
belt: IntegrityError handler retries lookup (L838-863).

**Verdict**: NC-19 grep-verifiably enforced. Idempotency key derives from
`H(decision_id, token_id, side, price, size, intent_kind)` via SHA-256
(`command_bus.py:164-218`). Key is local-canonical, not signed-order-hash.

## §8.3 17 transitions vs current CommandState/CommandEventType enums

**Already covered** by HEAD 874e00c grammar (8 of 17):

| §8.3 transition | Maps to | File:line |
|---|---|---|
| COMMAND_PERSISTED | INTENT_CREATED event + venue_commands row | `command_bus.py:66`, `venue_command_repo.py:194` |
| SUBMIT_TIMEOUT_UNKNOWN | SUBMIT_UNKNOWN event | `command_bus.py:70`, `executor.py:923-941` |
| PARTIALLY_FILLED | PARTIAL_FILL_OBSERVED → state=PARTIAL | `command_bus.py:71`, `venue_command_repo.py:54,63,69-74` |
| CANCEL_REQUESTED | CANCEL_REQUESTED → state=CANCEL_PENDING | `command_bus.py:73`, `venue_command_repo.py:50,56,65,72` |
| TRADE_CONFIRMED | FILL_CONFIRMED → state=FILLED | `command_bus.py:72`, `venue_command_repo.py:55,64,71` |
| REVIEW_REQUIRED | REVIEW_REQUIRED event | `command_bus.py:76`, `venue_command_repo.py:44,51,58,67,74,82` |
| RECONCILED_BY_OPEN_ORDERS | venue_resp inspection in command_recovery | `command_recovery.py:139-201` |
| RECONCILED_BY_POSITION (partial) | get_positions_from_api in chain_reconciliation | `chain_reconciliation.py:1-25` (legacy position-level) |

**Missing structurally** (the actual K-decisions, 4-5 of 17):

| §8.3 transition | Why it's a real gap |
|---|---|
| SIGNED_ORDER_PERSISTED | venue_commands schema has no `signed_order_hash` column; idempotency key is local-canonical, not the signed EIP-712 hash. Apr26 §8 wants signed-order provenance pre-post. **One column + one repo write**. |
| ACCEPTED vs RESTING | CommandState.ACKED is single state; doesn't distinguish "venue accepted but not on book" from "resting on book". Could be implicit via `venue_status` payload OR a state split. **Likely payload metadata, not enum split**. |
| CANCEL_FAILED | Recovery loop has CANCEL_PENDING → CANCEL_ACKED only (`command_recovery.py:266-301`). If cancel times out / errors, no event fires. Need CANCEL_FAILED event-type + cancel retry semantics. **One new event-type + recovery branch**. |
| REMAINING_CANCEL_REQUESTED | Sub-case of CANCEL_REQUESTED after PARTIAL. Current grammar already allows PARTIAL → CANCEL_PENDING (`venue_command_repo.py:72`). **Payload metadata; no schema change needed**. |
| CANCEL_REPLACE_BLOCKED | Composite gate: when cancel is unresolved, block new replace. **Application logic, not state-machine event**. |
| POSITION_CONFIRMED_FROM_EXCHANGE | exchange.get_positions snapshot table (F-006 ext). **Reconciliation-loop extension, NOT command-bus state**. |
| RECONCILED_BY_TRADES | get_trades query branch in recovery loop. **Recovery extension, not new state**. |
| CLOSED_MARKET_UNKNOWN | Discovery-side preflight (F-007 cluster). **Not in mid-region scope**. |
| REDEEM_REQUESTED | Settlement-side (post-fill). **Outside current state machine**. |

**K-decision count**: 17 transitions reduce to **3-4 real structural decisions**:

1. **D1**: signed-order-hash as durable artifact (one column + one event-type SIGNED_ORDER_PERSISTED).
2. **D2**: CANCEL_FAILED first-class (one event-type + recovery branch).
3. **D3**: Recovery-loop emission of PARTIAL_FILL_OBSERVED (logic only — schema already supports).
4. **D4 (cross-region)**: exchange-position-snapshot loop (F-006 reconcile extension; arguably belongs to a thin reconciliation-extension slice, NOT a NET_NEW umbrella).

The remaining 13 transitions are payload metadata, recovery branches, or out-of-region (discovery / settlement). The routing yaml's NET_NEW EXECUTION_STATE_MACHINE umbrella over-counts.

## Working hypothesis for Layer 1

**Mid-region scope as planned (PR18 P2 A1-A4 + Wave 2 B1/B3 + C1 + Apr26 §8 state-machine extension) is structurally sufficient with two amendments**:

1. **A1.5 (extension to PR18 P2 A1)**: signed_order_hash column on
   venue_commands + SIGNED_ORDER_PERSISTED event-type. Closes F-008
   (YES/NO outcome-token identity frozen via signed-order hash) + F-003
   (exchange-proven idempotency provenance). **Not a new umbrella.**

2. **A4.5 (extension to PR18 P2 deferred)**: CANCEL_FAILED event-type
   + recovery loop emission of PARTIAL_FILL_OBSERVED. Closes F-005
   cancel-failure first-class. **Not a new umbrella.**

The 3 proposed NET_NEW umbrellas (EXECUTION_ORDER_COMMAND_JOURNAL /
EXECUTION_STATE_MACHINE / EXECUTION_RECONCILIATION_LOOP) are routing-yaml
artifacts of NOT verifying against HEAD 874e00c. The journal exists; the
state machine exists; the reconciliation loop exists. What remains is
EXTENSION (a few columns + a few event-types + a few recovery branches),
NOT INVENTION.

**RED authority direction (F-010)**: cycle_runner L352-373 RED→force_exit_sweep
sets `pos.exit_reason = "red_force_exit"` on positions but does NOT emit
durable cancel commands through venue_commands. PR18 P2 A1 (K4 RED→durable-cmd)
plugs this gap by routing RED-triggered cancellations through
insert_command/append_event(CANCEL_REQUESTED). Authority direction (risk →
command, never inverted) is preserved.

## State-space distinction (path-correction note 2026-04-26)

Two disjoint state spaces, NOT to conflate:

- **Command-bus states** (`src/execution/command_bus.py:44-77`): CommandState
  {INTENT_CREATED, SUBMITTING, ACKED, UNKNOWN, PARTIAL, FILLED, CANCEL_PENDING,
  CANCELLED, EXPIRED, REJECTED, REVIEW_REQUIRED}. Closed grammar over
  venue-order lifecycle; transitions in `venue_command_repo.py:41-83`.

- **Position runtime states** (`src/contracts/semantic_types.py:17,39`):
  LifecycleState (PENDING_TRACKED, ENTERED, HOLDING, DAY0_WINDOW, …) +
  ExitState (SELL_PLACED, SELL_PENDING, SELL_FILLED, …). Position-level
  runtime classifications.

INV-32 is the bridge: command_state ∈ {ACKED, PARTIAL, FILLED} is a
prerequisite for materializing a Position into LifecycleState. This is
the correct authority direction (command truth → position truth). F-006
position-level reconcile is closed at the AUTHORITY-DIRECTION level; the
remaining gap is exchange-side reconcile (open_orders + trades + positions
sweep against journal) which is a thin extension of `command_recovery`,
not a new umbrella.

## Test coverage anchors (verified to exist on HEAD 874e00c)

| Test file | Covers |
|---|---|
| `tests/test_command_bus_types.py` | Closed-enum grammar, IdempotencyKey factory invariants, IN_FLIGHT_STATES ↔ repo unresolved filter parity |
| `tests/test_executor_command_split.py` | INV-30 build/persist/submit/ack ordering + crash-injection |
| `tests/test_command_recovery.py` | INV-31 recovery resolution table |
| `tests/test_discovery_idempotency.py` | INV-32 + NC-19 materialization gate + duplicate-submit suppression |
| `tests/test_p0_hardening.py` | Pre-existing P0 guards still hold post-P1 |

These are the antibody anchors. New mid-region slices (A1.5, A4.5,
exchange-reconcile-extension) MUST land with antibody tests in the same
files OR new sibling files following the same naming convention.

## Counter-position to anticipate from opponent-mid

Opponent will likely argue:
- (Strong) F-006 reconciliation needs an exchange-side LOOP comparing
  open_orders + trades + positions vs venue_commands, not just venue.get_order
  per-row. This is a real gap; counter that it's a **thin extension** to
  command_recovery.py, not a NET_NEW slice (current loop is row-by-row;
  add a periodic "scan exchange, diff against journal" sweep).
- (Medium) §8.3 17 transitions count is misleading; the SIGNED_ORDER_PERSISTED
  + CANCEL_FAILED pair indicates the closed enum is incomplete. Counter that
  closed-enum extension via 2 event-types is small structural work, not
  umbrella-worthy.
- (Weak) PR18 P2 deferred A1-A4 was already "deferred", so it's NET_NEW
  for ULTIMATE_PLAN scope. Counter that "deferred but enumerated and scoped
  in work_log.md L505+" is materially different from "no decision exists".

## ACK message to judge

Solo context boot complete; INV-30/31/32/NC-19 grep-verified close F-001/F-006
modulo a 3-4 K-decision extension to PR18 P2; routing yaml NET_NEW count
overstated by ~6-8. Ready for R1L1 A2A on greenlight.
