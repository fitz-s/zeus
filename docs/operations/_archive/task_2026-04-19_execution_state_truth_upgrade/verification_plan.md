# Verification Plan — Execution-State Truth Upgrade

## 1. Verification philosophy

This packet must be verified by **runtime evidence**, not by prose confidence.

The proof burden is:

1. static law/manifests updated
2. targeted tests added
3. failure-injection drills demonstrate truthful unknown handling
4. operator-facing status reflects the same truth the DB/event spine reflects
5. live-entry re-enable remains gated until the above converge

## 2. Evidence sources

Verification must explicitly reconcile three evidence sources:

- repo runtime reality
- packet law/manifests
- official Polymarket venue documentation

The supplied review is an evidence input, not a control source. When repo reality, review text, and official venue docs disagree, the verification report must name the conflict and say which source controlled the decision.

## 3. Verification matrix

## 3.1 P0 matrix

| Requirement | Proof |
|---|---|
| degraded export never verified | targeted unit test + operator payload snapshot |
| V2 preflight blocks placement | targeted unit/integration test |
| stale authority tests no longer lie | updated tests + freshness metadata |
| non-gateway live placement blocked | AST/CI gate + targeted rule test |
| `NO_NEW_ENTRIES` posture preserved | cycle summary / control-plane evidence |

## 3.2 P1 matrix

| Requirement | Proof |
|---|---|
| persisted command exists before submit | unit/integration test with injected submit spy |
| crash-after-submit-before-ack produces durable unresolved command | failure injection replay test |
| position authority does not move from attempted submit alone | relationship test |
| command recovery reconciles unresolved commands | recovery integration test |

## 3.3 P2 matrix

| Requirement | Proof |
|---|---|
| unknown/review-required block new entries | targeted control-path tests |
| empty-chain + mixed freshness no longer folds to false absence | reconciliation regression test |
| `RED` emits de-risk commands | targeted command-emission test |
| review-required workload is visible | status/report contract test |

## 3.4 P3 matrix

| Requirement | Proof |
|---|---|
| market eligibility rejects boundary ambiguity | policy test |
| station/finalization contract required | eligibility contract test |
| alpha budget persists across time | replay/DB persistence test |

## 4. Required verification commands

## 4.1 Planning-lock / topology / manifests

```bash
python scripts/topology_doctor.py --planning-lock --changed-files <files...> --plan-evidence docs/operations/task_2026-04-19_execution_state_truth_upgrade/project_brief.md
python scripts/topology_doctor.py --tests --json
python scripts/check_kernel_manifests.py
```

## 4.2 P0 commands

```bash
pytest -q tests/test_dt1_commit_ordering.py tests/test_fdr_family_scope.py tests/test_degraded_export_never_verified.py tests/test_v2_preflight_blocks_live_placement.py tests/test_unknown_or_review_required_blocks_new_entries.py tests/test_no_direct_place_limit_order_outside_gateway.py
```

## 4.3 P1 commands

```bash
pytest -q tests/test_command_persisted_before_submit.py tests/test_crash_after_submit_before_ack_write_replays_to_unknown.py tests/test_command_events_required_for_position_authority.py tests/test_no_direct_place_limit_order_outside_gateway.py
```

## 4.4 P2 commands

```bash
pytest -q tests/test_unknown_command_blocks_new_entries.py tests/test_empty_chain_with_mixed_freshness_marks_unknown.py tests/test_red_emits_derisk_commands.py tests/test_review_required_stops_new_entries.py
```

## 4.5 P3 commands

```bash
pytest -q tests/test_market_eligibility_blocks_boundary_ambiguous_markets.py tests/test_station_mapping_required_for_live_market.py tests/test_persistent_alpha_budget_across_snapshots.py
```

## 5. Required failure-injection drills

## Drill A — Crash after submit, before ack persistence

### Setup
- force submit call to succeed
- crash process before ack event is persisted

### Expected result
- unresolved command exists on restart
- command recovery scans it
- system ends in `UNKNOWN` or resolved state, never false-flat
- new entries remain blocked until resolution

## Drill B — Network timeout, venue may still have accepted order

### Setup
- submit path times out locally
- simulated venue lookup later shows open or partially filled order

### Expected result
- command moves to `UNKNOWN`, then to `ACKED` / `PARTIAL` on recovery
- no duplicate submit is emitted automatically
- operator workload is explicit if resolution remains incomplete

## Drill C — Empty-chain response with mixed freshness

### Setup
- chain API returns empty or incomplete response
- portfolio contains mixed freshness local positions
- unresolved command state exists for at least one relevant token

### Expected result
- system does not void by default
- affected positions/commands remain `UNKNOWN` or `REVIEW_REQUIRED`
- new entries blocked

## Drill D — RED with active positions and pending orders

### Setup
- at least one active position
- at least one pending live order
- risk escalates to `RED`

### Expected result
- cancel commands emitted for pending orders
- de-risk / exit commands emitted for active positions
- operator surface reflects unwind backlog

## Drill E — V2 cutover wipe rehearsal

### Setup
- simulate cutover where venue open orders are wiped
- preexisting commands/orders exist locally

### Expected result
- preflight and recovery logic detect cutover generation
- local command state is reconciled rather than assumed open
- operator sees explicit reconcile gap if any remain unresolved

## 6. Operator workflow verification

Verification must include human-visible proof that operators can tell:

- canonical DB-backed truth
- degraded projection
- unresolved execution truth
- review-required workload
- `RED` unwind backlog
- V2 preflight state

It is not enough for logs to contain these facts. They must surface where operators actually look.

## 7. Re-enable criteria for live entry

Live entry may not be re-enabled until a verification note proves all of the following:

1. P0 complete
2. P1 complete
3. P2 complete
4. no unresolved `UNKNOWN` / `REVIEW_REQUIRED` commands remain
5. V2 preflight passes against current official venue expectations
6. rollback posture tested
7. operator review workflow tested

## 8. Evidence artifacts to store

The closeout packet for each phase should store:

- changed files list
- tests run
- failure-injection results
- rollback note
- unresolved questions
- any official venue doc deltas checked during the phase
- explicit statement that unrelated dirty work was preserved

## 9. Verification anti-patterns

The following do **not** count as sufficient verification:

- “tests are green” without failure-injection drills
- “the venue probably rejected it” without durable command evidence
- “degraded but basically okay” operator wording
- “temporary bypass” around V2 preflight
- re-enabling entry before unresolved command count is zero
- deleting stale tests without replacing the authority they should enforce

## 10. Final proof standard

The upgrade is verified when Zeus no longer merely sounds confident. It must instead demonstrate, by replay and recovery, that when truth is incomplete it records that incompleteness durably, blocks new risk, preserves exit/recovery lanes, and never upgrades uncertainty into verified authority.
