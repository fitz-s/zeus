# Implementation Plan — Execution-State Truth Upgrade

## 1. Phase order

- **P0:** immediate hardening / no-new-entry gates / degraded authority fix / V2 preflight / stale authority cleanup
- **P1:** `venue_commands` + `venue_command_events` + submit-before-side-effect + crash recovery
- **P2:** `UNKNOWN` integration with reconciliation + `RED` authoritative de-risk
- **P3:** market eligibility / settlement containment / persistent alpha budget

Unrestricted live entry is blocked until P0, P1, and P2 are complete and verified.

## 2. Global rules for every phase

All phases must preserve the following:

- DB / event truth outranks JSON/status projections
- JSON/status projections are never canonical truth
- no venue order side effect without persisted `VenueCommand`
- no authoritative position state change from order submission without `venue_command_event`
- degraded export never labeled `VERIFIED`
- `UNKNOWN` / `REVIEW_REQUIRED` block new entries
- `RED` produces cancel/de-risk/exit commands
- `positions.json` is projection only
- direct `place_limit_order` outside gateway blocked by AST/CI guard
- V2 preflight failure blocks live order placement
- unrelated dirty work is preserved; no reset/checkout/revert

## 3. P0 — Immediate hardening

### Objective

Stop authority inflation and unsafe new-risk creation immediately, without waiting for the full schema re-architecture.

### Touched files

Minimum expected scope:

- `src/state/portfolio.py`
- `src/engine/cycle_runner.py`
- `src/data/polymarket_client.py`
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- `architecture/ast_rules/forbidden_patterns.md` and/or the active Semgrep rule registry
- CI/workflow surface that enforces AST/CI rules
- stale authority-facing tests:
  - `tests/test_dt1_commit_ordering.py`
  - `tests/test_fdr_family_scope.py`
- new P0 tests:
  - `tests/test_degraded_export_never_verified.py`
  - `tests/test_v2_preflight_blocks_live_placement.py`
  - `tests/test_unknown_or_review_required_blocks_new_entries.py`
  - `tests/test_no_direct_place_limit_order_outside_gateway.py`

Authority-facing stale comments likely touched in:
- `src/riskguard/riskguard.py`
- `src/state/portfolio.py`

### Authority boundary

P0 does not yet create the new command journal. It hardens the current boundary by refusing unsafe authority claims and blocking unsafe entry.

### Invariants

P0 must lock the following behavior immediately:

- degraded export is never `VERIFIED`
- live placement requires V2 preflight success
- explicit unresolved execution-truth conditions block new entry
- stale authority artifacts may not contradict current runtime truth
- non-gateway order placement is CI/AST forbidden

### Tests to add/update

Add/update at least:

- update stale assertions in `tests/test_dt1_commit_ordering.py`
- update stale assertions in `tests/test_fdr_family_scope.py`
- `tests/test_degraded_export_never_verified.py`
- `tests/test_v2_preflight_blocks_live_placement.py`
- `tests/test_unknown_or_review_required_blocks_new_entries.py`
- `tests/test_no_direct_place_limit_order_outside_gateway.py`

### Verification commands

```bash
python scripts/topology_doctor.py --planning-lock --changed-files src/state/portfolio.py src/engine/cycle_runner.py src/data/polymarket_client.py architecture/invariants.yaml architecture/negative_constraints.yaml tests/test_dt1_commit_ordering.py tests/test_fdr_family_scope.py --plan-evidence docs/operations/task_2026-04-19_execution_state_truth_upgrade/project_brief.md
pytest -q tests/test_dt1_commit_ordering.py tests/test_fdr_family_scope.py tests/test_degraded_export_never_verified.py tests/test_v2_preflight_blocks_live_placement.py tests/test_unknown_or_review_required_blocks_new_entries.py tests/test_no_direct_place_limit_order_outside_gateway.py
python scripts/topology_doctor.py --tests --json
python scripts/check_kernel_manifests.py
```

### Rollback path

Rollback is behavioral, not destructive:

- keep `NO_NEW_ENTRIES`
- keep monitor / exit / reconciliation available
- do not remove stale-test cleanup or reintroduce false verified labeling
- if V2 preflight implementation is broken, fail closed on live placement rather than bypass it

Do **not** drop unrelated work or use destructive git cleanup.

### Blast radius

Low to medium:

- operator-facing truth labels
- live placement gating
- stale test/doc authority surfaces
- CI/AST enforcement

### Hard gates

P0 is not complete until all of the following are true:

- degraded truth never exports `VERIFIED`
- V2 preflight failure blocks placement
- direct non-gateway order placement is AST/CI guarded
- stale authority-facing tests no longer assert false current behavior
- runtime posture remains `NO_NEW_ENTRIES`

### What not to do in P0

- do not add the `venue_commands` schema yet
- do not refactor the full execution path yet
- do not redesign Kelly / evaluator / market selection in this phase
- do not perform broad stale cleanup beyond authority-misleading artifacts
- do not re-enable live entry

## 4. P1 — Durable command truth

### Objective

Introduce the missing durable execution truth layer so live side effects can be recovered deterministically.

### Touched files

New files:

- `src/execution/command_bus.py`
- `src/execution/command_recovery.py`
- `src/state/authority_state.py`
- `src/state/venue_command_repo.py`
- migration file, e.g. `migrations/2026_04_19_execution_command_journal.sql`

Modified files:

- `src/state/db.py`
- `src/engine/cycle_runtime.py`
- `src/execution/executor.py`
- `src/data/polymarket_client.py`
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- AST/CI rule surface
- new P1 tests:
  - `tests/test_command_persisted_before_submit.py`
  - `tests/test_crash_after_submit_before_ack_write_replays_to_unknown.py`
  - `tests/test_command_events_required_for_position_authority.py`
  - `tests/test_no_direct_place_limit_order_outside_gateway.py`

### Authority boundary

P1 creates the new boundary:

`decision -> VenueCommand persisted -> submit through gateway -> venue_command_event -> position authority`

Local control flow and convenience reports are no longer allowed to stand in for execution truth.

### Invariants

P1 must implement and prove:

- no venue side effect without persisted `VenueCommand`
- no position-authority change from submit without `venue_command_event`
- idempotent replay key survives restart
- gateway is the only live placement surface
- unresolved command state is durable across restart

### Tests to add/update

Add at minimum:

- `tests/test_command_persisted_before_submit.py`
- `tests/test_crash_after_submit_before_ack_write_replays_to_unknown.py`
- `tests/test_command_events_required_for_position_authority.py`
- `tests/test_no_direct_place_limit_order_outside_gateway.py`
- integration coverage for new DB tables and repo API

### Verification commands

```bash
python scripts/topology_doctor.py --planning-lock --changed-files src/execution/command_bus.py src/execution/command_recovery.py src/state/authority_state.py src/state/venue_command_repo.py src/state/db.py src/engine/cycle_runtime.py src/execution/executor.py src/data/polymarket_client.py migrations/2026_04_19_execution_command_journal.sql --plan-evidence docs/operations/task_2026-04-19_execution_state_truth_upgrade/architecture_note.md
pytest -q tests/test_command_persisted_before_submit.py tests/test_crash_after_submit_before_ack_write_replays_to_unknown.py tests/test_command_events_required_for_position_authority.py tests/test_no_direct_place_limit_order_outside_gateway.py
python scripts/check_kernel_manifests.py
python scripts/topology_doctor.py --tests --json
```

### Rollback path

Do not roll back by deleting schema. Roll back by narrowing behavior:

- keep schema in place
- switch runtime to `EXIT_ONLY` / recovery-only mode
- disable new entry submission through control-plane gate
- leave recovery tooling active
- preserve accumulated command journal for forensic recovery

### Blast radius

High:

- DB schema
- execution path
- restart behavior
- live order placement
- reconciliation inputs
- CI/AST enforcement

### Hard gates

P1 is not complete until all of the following are true:

- live placement cannot happen without persisted command
- crash-after-submit drill produces durable unresolved command
- non-gateway live placement is blocked
- command journal and event spine are queryable and replayable
- rollback posture is documented without dropping schema/data

### What not to do in P1

- do not add complex execution tactics (slicing/repricing/iceberg semantics)
- do not widen into market eligibility redesign
- do not solve persistent alpha budget in the same packet
- do not let telemetry tables masquerade as canonical command truth

## 5. P2 — Semantic closure

### Objective

Make unresolved execution truth and forced de-risk truly authoritative system behavior.

### Touched files

Expected scope:

- `src/state/chain_reconciliation.py`
- any chain-state helper surface that owns truth classification
- `src/riskguard/riskguard.py`
- `src/engine/cycle_runner.py`
- `src/execution/exit_lifecycle.py`
- `src/engine/monitor_refresh.py`
- `src/state/portfolio.py`
- `architecture/invariants.yaml`
- new P2 tests:
  - `tests/test_unknown_command_blocks_new_entries.py`
  - `tests/test_empty_chain_with_mixed_freshness_marks_unknown.py`
  - `tests/test_red_emits_derisk_commands.py`
  - `tests/test_review_required_stops_new_entries.py`

### Authority boundary

P2 makes unresolved command truth part of the canonical runtime control boundary.

New rule:

- reconciliation cannot conclude absence/void/full safety without considering unresolved command state
- `RED` cannot remain a local position annotation; it must emit durable command work

### Invariants

P2 must prove:

- `UNKNOWN` and `REVIEW_REQUIRED` are first-class blocking states
- empty-chain + mixed freshness does not silently void or silently bless positions
- fabricated execution timestamps are removed, nullable, or typed as unresolved rather than smuggled into normal timestamps
- `RED` emits durable cancel/de-risk/exit commands

### Tests to add/update

Add at minimum:

- `tests/test_unknown_command_blocks_new_entries.py`
- `tests/test_empty_chain_with_mixed_freshness_marks_unknown.py`
- `tests/test_red_emits_derisk_commands.py`
- `tests/test_review_required_stops_new_entries.py`

### Verification commands

```bash
python scripts/topology_doctor.py --planning-lock --changed-files src/state/chain_reconciliation.py src/riskguard/riskguard.py src/engine/cycle_runner.py src/execution/exit_lifecycle.py src/engine/monitor_refresh.py src/state/portfolio.py architecture/invariants.yaml --plan-evidence docs/operations/task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md
pytest -q tests/test_unknown_command_blocks_new_entries.py tests/test_empty_chain_with_mixed_freshness_marks_unknown.py tests/test_red_emits_derisk_commands.py tests/test_review_required_stops_new_entries.py
python scripts/check_kernel_manifests.py
```

### Rollback path

If semantic closure causes unstable automation:

- keep new-entry block in place
- demote automated unwind to operator-reviewed de-risk execution
- keep unresolved states explicit
- do not re-collapse unknown truth into normal absence

### Blast radius

Medium to high:

- reconciliation truth
- operator workflow
- risk control semantics
- exit automation
- incident response

### Hard gates

P2 is not complete until:

- unresolved command truth is visible and blocking
- mixed-freshness empty-chain cases no longer auto-fold into simple absence
- `RED` produces durable de-risk work
- operators can see review-required workload explicitly

### What not to do in P2

- do not use heuristics to hide unresolved truth
- do not relabel degraded/unknown states as verified for operator convenience
- do not couple this phase to market eligibility or alpha-budget redesign

## 6. P3 — Market eligibility / settlement containment / persistent alpha budget

### Objective

Reduce outer exposure to settlement-boundary risk and repeated-testing pressure once execution truth is durable.

### Touched files

Likely scope:

- `src/data/market_scanner.py`
- `src/engine/evaluator.py`
- `src/strategy/selection_family.py`
- new `src/market/eligibility.py`
- config surface for eligibility policy
- DB surface for persistent alpha ledger
- new P3 tests:
  - `tests/test_market_eligibility_blocks_boundary_ambiguous_markets.py`
  - `tests/test_station_mapping_required_for_live_market.py`
  - `tests/test_persistent_alpha_budget_across_snapshots.py`

### Authority boundary

P3 may add policy truth, but it may not backdoor execution authority.

Execution-state truth remains upstream and canonical. Eligibility and alpha budget consume truth; they do not redefine it.

### Invariants

P3 must enforce:

- settlement-ambiguous markets are ineligible for live entry
- station/finalization confidence is explicit
- repeated testing pressure is accounted for durably across time
- eligibility does not override unresolved execution truth

### Tests to add/update

Add at minimum:

- `tests/test_market_eligibility_blocks_boundary_ambiguous_markets.py`
- `tests/test_station_mapping_required_for_live_market.py`
- `tests/test_persistent_alpha_budget_across_snapshots.py`

### Verification commands

```bash
python scripts/topology_doctor.py --planning-lock --changed-files src/data/market_scanner.py src/engine/evaluator.py src/strategy/selection_family.py src/market/eligibility.py --plan-evidence docs/operations/task_2026-04-19_execution_state_truth_upgrade/prd.md
pytest -q tests/test_market_eligibility_blocks_boundary_ambiguous_markets.py tests/test_station_mapping_required_for_live_market.py tests/test_persistent_alpha_budget_across_snapshots.py
python scripts/check_kernel_manifests.py
```

### Rollback path

- keep core execution truth untouched
- revert new eligibility policy to stricter static denylist / cooldown if needed
- keep persistent alpha ledger read-only if budget logic needs correction
- do not reopen live-entry breadth through silent policy bypass

### Blast radius

Medium:

- candidate selection
- acceptance rate
- trade frequency
- research expectations

### Hard gates

P3 is not complete until:

- settlement boundary ambiguity is explicitly handled
- station/finalization contract is explicit for live-eligible markets
- persistent alpha budget survives multi-snapshot replay

### What not to do in P3

- do not reopen the command-truth design
- do not add complex queue-position simulator work
- do not widen into unrelated model sophistication work
- do not make P3 a substitute for incomplete P1/P2 truth work

## 7. Cross-phase hard gates

The following conditions must remain active across phases:

- V2 preflight failure -> no live order placement
- any unresolved `UNKNOWN` or `REVIEW_REQUIRED` command -> no new entry
- degraded authority -> no verified export + no new entry
- `RED` -> cancel/de-risk/exit path required
- non-gateway `place_limit_order` -> CI/AST fail
- preserve unrelated dirty work; never recommend reset/checkout/revert

## 8. First recommended implementation packet after planning

### Packet

`P0 hardening — authority label + V2 preflight + stale authority cleanup`

### Why this first

It delivers the fastest real reduction in live risk with the smallest blast radius, while preparing the repo for the bigger P1 schema packet.

### Exact initial deliverables

1. fix degraded truth labeling
2. add V2 preflight gate
3. add unresolved/unknown entry block
4. update stale authority tests/comments
5. add AST/CI guard for direct live placement outside approved boundary
6. keep branch in `NO_NEW_ENTRIES`

### Exit criteria for the first packet

- all P0 tests green
- manifests updated
- operator truth surface no longer inflates degraded state
- live placement cannot bypass V2 preflight
- no destructive git actions used
