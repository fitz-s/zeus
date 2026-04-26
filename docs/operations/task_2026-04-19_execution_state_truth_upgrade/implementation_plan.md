# Implementation Plan — Execution-State Truth Upgrade

## 1. Mainline sequence

Unrestricted live entry is blocked until P0, P1, and P2 are implemented and verified.

1. **P0 — Immediate hardening**: stop unsafe confidence and unsafe new risk now.
2. **P1 — Durable command truth**: add pre-side-effect `venue_commands` and append-first `venue_command_events`.
3. **P2 — Semantic closure**: make unresolved command truth first-class and make RED command-authoritative.
4. **P3 — Outer containment**: add market eligibility/settlement containment and persistent alpha spending.

## 2. Global invariants for every phase

- DB/event truth outranks JSON/status projections.
- JSON/status files are projections only.
- No venue order side effect may occur without a persisted command record.
- No position authority may advance from order submission without a command event.
- Degraded projections must never export as `VERIFIED`.
- `UNKNOWN` / `REVIEW_REQUIRED` blocks new entries.
- `RED` must create cancel/de-risk/exit work, not only stop entries.
- Direct live placement outside the gateway/command boundary is forbidden.
- V2 preflight failure blocks live placement.
- Rollback is behavioral (`NO_NEW_ENTRIES`, `EXIT_ONLY`, recovery-only), not destructive.

## 3. P0 — Immediate hardening

### Objective

Remove known false confidence and block unsafe new entries without waiting for the schema refactor.

### In scope

1. Fix `_TRUTH_AUTHORITY_MAP["degraded"]` so degraded projection cannot be `VERIFIED`.
2. Ensure degraded loader state blocks new entries while monitor/exit/reconciliation continue.
3. Add an explicit entry gate for known unresolved execution-truth conditions available before P1, including quarantine without order authority, missing order id on pending live state, and future command-unknown hook points.
4. Add CLOB V2 preflight gate in the live order path.
5. Add static guard that only the approved gateway/command boundary may call `place_limit_order`.
6. Update/demote stale tests and comments that still claim commit-then-export or FDR split are missing.
7. Mark branch posture as `NO_NEW_ENTRIES` until P0 closes.

### Likely touched surfaces

- `src/state/portfolio.py`
- `src/engine/cycle_runner.py`
- `src/execution/executor.py`
- `src/data/polymarket_client.py`
- `src/riskguard/riskguard.py` comments if stale authority text remains
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- AST/static-rule surface used by current CI/topology checks
- targeted tests under `tests/`

### Acceptance tests / proofs

- degraded export never says `VERIFIED`
- degraded loader keeps monitor/exit/reconciliation alive and blocks entries
- V2 preflight failure prevents placement
- non-gateway direct `place_limit_order` calls fail static guard
- stale tests no longer encode false present-tense claims

### Stop conditions

- Stop if P0 needs a new DB schema.
- Stop if implementation requires choosing final command-event grammar.
- Stop if V2 compatibility cannot be proven from approved package/version evidence.

### Rollback

Keep entries paused. If a P0 gate is too strict, degrade to `MONITOR_ONLY` / `EXIT_ONLY` rather than bypassing degraded/V2/unknown guards.

## 4. P1 — Durable command truth

### Objective

Close the orphan-order window by making command intent durable before any network side effect.

### In scope

1. Add `src/execution/command_bus.py` with typed `VenueCommand` and command-state definitions.
2. Add `src/state/venue_command_repo.py` as the only DB API for command rows/events.
3. Add `venue_commands` and `venue_command_events` schema/API in `src/state/db.py` or the approved migration layer.
4. Split executor into request building and command submission:
   - build command/request without side effect
   - persist command
   - submit through gateway
   - append command event
5. Change `cycle_runtime.execute_discovery_phase()` to create/persist command before submit and materialize position only from durable command-event outcome.
6. Add `src/execution/command_recovery.py` to scan unresolved commands at startup/cycle boundary.
7. Add idempotency/replay key contract.
8. Add query/report surfaces for unresolved command counts.

### Likely touched surfaces

- New: `src/execution/command_bus.py`
- New: `src/execution/command_recovery.py`
- New: `src/state/venue_command_repo.py`
- Possibly new: `src/state/authority_state.py`
- Modified: `src/state/db.py`
- Modified: `src/engine/cycle_runtime.py`
- Modified: `src/execution/executor.py`
- Modified: `src/data/polymarket_client.py`
- Modified: tests and architecture manifests

### Command states

Minimum state grammar:

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

### Minimum command events

- `SUBMIT_REQUESTED`
- `SUBMIT_ACKED`
- `SUBMIT_UNKNOWN`
- `SUBMIT_REJECTED`
- `PARTIAL_FILL_OBSERVED`
- `FILL_CONFIRMED`
- `CANCEL_REQUESTED`
- `CANCEL_ACKED`
- `EXPIRED`
- `REVIEW_REQUIRED`

### Acceptance tests / proofs

- command row is persisted before submit spy sees any side effect
- crash after submit before ack write recovers to `UNKNOWN` or resolved command state
- attempted submit alone does not create authoritative position state
- unresolved command survives restart
- gateway-only static guard remains enforced

### Stop conditions

- Stop if schema ownership is unresolved.
- Stop if command-event grammar cannot be frozen.
- Stop if idempotency key cannot be stable across restart.
- Stop if old execution report or trade decision tables are being overloaded as command authority.

### Rollback

Do not drop command schema. Disable new entry and leave recovery/query surfaces active for forensic reconciliation.

## 5. P2 — Semantic closure

### Objective

Make unresolved truth and RED de-risk authoritative behavior across reconciliation, risk, and operator workflow.

### In scope

1. Feed unresolved commands into chain classification/reconciliation.
2. Treat `UNKNOWN` / `REVIEW_REQUIRED` as first-class execution truth that blocks entries.
3. Replace empty-chain heuristic folding with command-aware review/unknown outcomes.
4. Remove or quarantine fabricated time fields such as `unknown_entered_at` from downstream temporal authority.
5. Change RED from local exit marking to durable cancel/de-risk/exit command emission.
6. Surface operator backlog: unknown command count, review-required count, pending de-risk count, RED unwind pending count.
7. Clarify `positions.json` and status outputs as projection-only.

### Likely touched surfaces

- `src/state/chain_state.py`
- `src/state/chain_reconciliation.py`
- `src/state/lifecycle_manager.py` only if enum/state grammar change is approved
- `src/riskguard/riskguard.py`
- `src/riskguard/risk_level.py`
- `src/engine/cycle_runner.py`
- `src/engine/cycle_runtime.py`
- `src/execution/exit_lifecycle.py`
- `src/execution/command_bus.py`
- `src/state/venue_command_repo.py`
- observability/status surfaces

### Acceptance tests / proofs

- unknown command blocks new entries
- empty-chain + mixed freshness + unresolved command does not void or certify flat
- RED emits durable cancel/de-risk/exit commands
- operator status exposes review workload and unwind backlog
- fabricated entry timestamps cannot drive hold-duration or exit-timing authority without explicit quarantine

### Stop conditions

- Stop if lifecycle enum grammar changes are needed but not planned.
- Stop if RED command emission requires unsupported venue cancel/exit semantics.
- Stop if operator workflow cannot represent review-required state.

### Rollback

Set runtime to `EXIT_ONLY` / recovery-only, keep command journal and review queues visible, and do not auto-clear unknowns.

## 6. P3 — Outer containment and decision-law governance

### Objective

Reduce settlement/venue and repeated-testing risk after execution truth is stable.

### In scope

1. Add market eligibility policy for settlement boundary ambiguity, station mapping, finalization contract, and depth/liquidity.
2. Add venue-state stability checks around CLOB V2/cutover generations.
3. Add persistent alpha-spending ledger or equivalent durable attempt budget across cycles/snapshots.
4. Add metrics for family/day repeated attempts and budget consumption.

### Likely touched surfaces

- `src/data/market_scanner.py`
- New `src/market/eligibility.py` or approved package location
- `config/market_eligibility.yaml` if approved
- `src/engine/evaluator.py`
- `src/strategy/selection_family.py`
- `src/strategy/market_analysis_family_scan.py`
- `src/state/db.py`

### Acceptance tests / proofs

- boundary-ambiguous markets are ineligible
- station/finalization contract required before live eligibility
- low-depth or venue-unstable markets are rejected before sizing
- repeated same-family/day attempts consume durable budget across snapshots
- alpha budget does not reset simply because the cycle reran

### Stop conditions

- Stop if P1/P2 are not closed.
- Stop if market eligibility needs unverified source-validity facts.
- Stop if alpha budget design would change strategy identity law without architecture approval.

### Rollback

Fallback to hard cooldown / one-attempt-per-family-day and keep stricter market ineligibility; do not loosen entry to compensate.

## 7. Required monitoring counters

- `unknown_venue_command_count`
- `review_required_count`
- `submitting_age_p95`
- `pending_no_order_id_count`
- `venue_order_unlinked_count`
- `degraded_export_count`
- `loader_degraded_cycles`
- `red_unwind_pending_count`
- `v2_preflight_ok`
- `post_cutover_order_reconcile_gap`
- `boundary_distance_ticks_exposure`
- `snapshot_count_per_family_day`
- `trade_attempts_per_family_day`
- `alpha_spend_remaining`

## 8. First coding packet after this planning package

Name: `task_2026-04-26_execution_state_truth_p0_hardening`

Goal: P0 only — fix degraded export labeling, freeze/verify no-new-entry posture, add V2 preflight gate, add direct-placement guard, and clean stale authority artifacts. Do not add command schema in that first packet.
