# PRD — Execution-State Truth Upgrade

## 1. Problem statement

Zeus currently has strong authority law on paper and partial hardening in runtime, but it still lacks a durable execution command truth model. As a result, the system can still reach states where the venue has accepted or partially filled an order while local authority has not yet durably recorded the command and its state. In a live asynchronous order system, that gap is unacceptable.

The immediate product requirement is therefore not “better signals” or “smarter execution labels.” It is:

- truthful execution-state authority
- deterministic crash recovery
- explicit unknown-state handling
- authoritative de-risk behavior
- venue migration readiness
- removal of misleading authority artifacts

## 2. Product goal

Turn Zeus from a system that can sometimes **infer** what happened into a system that can **prove** what it knows, **admit** what it does not know, and **survive** restart/recovery without inventing truth.

## 3. Users

### Primary user

- Zeus operator responsible for live monitoring, incident response, and cutover readiness

### Secondary users

- Future Zeus implementers
- Reviewers/auditors of truth surfaces
- Any upstream/downstream automation that consumes operator-facing status

## 4. Non-goals

This packet does **not** target the following as first-order deliverables:

- posterior/model sophistication
- queue-position / impact simulation
- broad market expansion
- broad UI redesign
- broad stale-file cleanup unrelated to authority
- generalized multi-venue abstraction

Those may follow later, but only after execution truth is durable.

## 5. Success definition

The upgrade succeeds when all of the following are true:

1. No live order side effect occurs before a durable `VenueCommand` exists.
2. No authoritative position state changes merely because a local function attempted submission.
3. Restart/recovery can replay unresolved commands deterministically.
4. `UNKNOWN` / `REVIEW_REQUIRED` are treated as authority facts, not branch-local heuristics.
5. `RED` emits de-risking work, not merely advisory gating.
6. A degraded export can never present itself as verified truth.
7. Live order placement is blocked when Polymarket V2 readiness is not proven.
8. Operator-facing status clearly distinguishes canonical truth, degraded projection, and unresolved execution state.

## 6. Functional requirements

## FR-001 — Canonical truth hierarchy

The system shall preserve and reinforce the authority order:

`external venue/chain facts -> canonical DB command/events -> canonical position events/projections -> JSON/status projections -> reports`

Derived JSON/status/reporting surfaces shall never become canonical truth.

## FR-002 — Persisted command before side effect

The system shall persist a `VenueCommand` before any live order submission side effect is allowed.

Minimum persisted fields:

- `command_id`
- `command_kind`
- `decision_id` (nullable when not decision-originated)
- `linked_position_id` (nullable when entry command opens a not-yet-materialized position)
- `token_id`
- `side`
- `tif`
- `limit_price`
- `shares`
- `idempotency_key`
- `preflight_version`
- `state`
- `created_at`

## FR-003 — Command-event authority

The system shall record `venue_command_events` as append-first authority for execution-state change.

Minimum authoritative event classes:

- submit requested
- submit acknowledged
- submit unknown
- partial fill observed
- fill confirmed
- cancel requested
- cancel acknowledged
- reject
- expire
- review required

## FR-004 — Position truth does not advance from attempted submission alone

The system shall not move authoritative position state because a submit function returned successfully or because local code “assumes” the submission happened. A position-authority transition caused by order submission requires a durable `venue_command_event`.

## FR-005 — Crash recovery

The system shall provide a recovery path that scans unresolved commands on restart and attempts reconciliation against venue/chain facts without inventing certainty.

Recovery outcomes must include at least:

- resolved to acked/open
- resolved to partial
- resolved to filled
- resolved to cancelled/expired/rejected
- still unknown
- review required

## FR-006 — Entry blocking on unresolved command truth

The system shall block new entries whenever any of the following are true:

- unresolved `UNKNOWN` command exists
- unresolved `REVIEW_REQUIRED` command exists
- V2 preflight fails
- degraded truth surface is active in a way that defeats safe new-entry authority
- quarantine / unresolved execution fact policy says new risk is unsafe

## FR-007 — RED is authoritative de-risk

When overall risk is `RED`, the system shall emit authoritative cancel/de-risk/exit work through the command boundary. `RED` may not remain an entry-block-only or local-annotation-only behavior.

## FR-008 — Degraded export semantics

A degraded export shall never be labeled `VERIFIED`.

Operator-facing truth metadata must distinguish at minimum:

- canonical DB-backed verified truth
- degraded best-known-state projection
- explicitly unverified output

## FR-009 — Projection demotion

`positions.json` and related JSON/status outputs shall be explicitly demoted to projection-only surfaces. They may support operator readability and compatibility paths, but they may not be used to upgrade truth back into authority.

## FR-010 — Gateway-only live placement

Direct `place_limit_order` calls outside the approved execution gateway / command boundary shall be blocked by AST/CI guard.

## FR-011 — CLOB V2 preflight gate

Before any live order placement, the runtime shall prove CLOB V2 readiness.

Minimum preflight checks:

- approved V2 SDK/client surface present
- version / endpoint expectations match current venue contract
- fee / market-info lookup path uses current V2-compatible semantics
- user/order update channel prerequisites are satisfied or explicitly waived in packet evidence
- cutover state is not stale

Any failed preflight shall block live order placement.

## FR-012 — Unknown-state integration with reconciliation

Chain reconciliation shall consume unresolved command truth and distinguish:

- chain empty with fresh authority
- chain unknown/incomplete
- venue unknown despite chain view
- mixed freshness cases that require review

The system shall not fold unknown command truth into normal absence.

## FR-013 — Authority-artifact cleanup

Authority-facing tests, docs, and comments that describe already-fixed behavior or false current-law claims shall be updated, demoted, or removed in the same phase where they would otherwise mislead implementation.

## FR-014 — Market eligibility and settlement containment

After the execution truth core is stable, the system shall add explicit market eligibility controls for:

- settlement boundary ambiguity
- station mapping confidence
- source finalization rules
- minimum liquidity/depth standards
- venue state stability during cutover windows

## FR-015 — Persistent alpha budget

After execution truth is stable, the system shall constrain repeated repeated-testing pressure across time with a persistent alpha-spending or equivalent durable budget, not only per-snapshot family logic.

## 7. Non-functional requirements

## NFR-001 — Auditability

Every authoritative execution-state change must be reconstructible from append-first durable records.

## NFR-002 — Determinism

Given the same DB state and external replay inputs, recovery should produce the same command-resolution outcomes.

## NFR-003 — Operator clarity

Operator status must prefer truthful uncertainty over polished confidence.

## NFR-004 — Safe degradation

Loss of entry authority may block new risk, but it must not automatically blind monitor/exit/reconciliation lanes.

## NFR-005 — Minimal authority inflation

This upgrade may add new durable truth only where necessary. It may not create shadow authority surfaces.

## 8. Acceptance criteria by phase

## P0 acceptance

- degraded export never reports verified authority
- live placement blocked when V2 preflight fails
- stale authority-facing tests/comments no longer contradict runtime truth
- new entries blocked under explicit unresolved/unknown gating conditions
- branch posture remains `NO_NEW_ENTRIES`

## P1 acceptance

- `venue_commands` and `venue_command_events` exist
- live submission requires persisted command first
- crash-after-submit-before-ack-write drill produces deterministic unresolved command recovery path
- direct non-gateway order placement is blocked by CI/AST rule

## P2 acceptance

- `UNKNOWN` / `REVIEW_REQUIRED` are first-class and visible
- unresolved commands feed reconciliation truth
- `RED` emits authoritative cancel/de-risk/exit commands
- mixed-freshness empty-chain cases do not silently void or silently clear risk

## P3 acceptance

- market eligibility rejects settlement-ambiguous or venue-unstable markets
- persistent alpha budget is durable across time
- repeated testing pressure is no longer reset per snapshot alone

## 9. Release gates

Unrestricted live entry is blocked until all of the following are true:

- P0 complete
- P1 complete
- P2 complete
- V2 readiness verified against current official venue docs
- no unresolved unknown/review-required commands remain
- rollback posture is documented
- operator workflow for review-required paths is validated

## 10. Explicit exclusions during implementation

Implementation packets under this PRD shall not:

- promote JSON/status projections back to authority
- bypass the command boundary for convenience
- re-enable live entry “temporarily” without the above gates
- use destructive git commands to discard unrelated work
- hide unresolved truth behind “best effort” verified labels
