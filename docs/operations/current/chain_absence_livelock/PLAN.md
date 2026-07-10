# chain_absence_livelock -- Bugfix packet

Status: landed

```yaml
work_packet_id: BUG-CHAIN-ABSENCE-LIVELOCK-2026-07-10
packet_type: bugfix_packet
objective: Preserve consecutive known-absence evidence across unrelated monitor refresh events.
why_this_now: Live global auction is fail-closed because one redeemed or exited position remains open forever when monitor events hide the prior chain-mirror review marker.
why_not_other_approach:
  - Do not weaken current-wealth chain verification; that would turn unknown exposure into spendable capital.
  - Do not mutate the live DB by hand; executable reconciliation law must repair the state from fresh Chain/CLOB facts.
truth_layer: Chain/CLOB facts first; canonical position_events plus position_current second.
control_layer: src.state.chain_mirror_reconciler
evidence_layer: Current live chain snapshot, canonical trade DB rows, targeted relationship tests, post-deploy receipts.
zones_touched: [K0_frozen_kernel, K2_runtime]
invariants_touched: [INV-03, INV-07, INV-08]
required_reads:
  - AGENTS.md
  - workspace_map.md
  - architecture/self_check/authority_index.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/source_rationale.yaml
  - docs/authority/zeus_current_architecture.md
  - docs/authority/zeus_current_delivery.md
  - docs/reference/zeus_execution_lifecycle_reference.md
  - docs/reference/modules/state.md
  - src/state/AGENTS.md
  - tests/AGENTS.md
files_may_change:
  - src/state/chain_mirror_reconciler.py
  - tests/test_reconcile_chain_mirror.py
  - architecture/test_topology.yaml
  - docs/operations/current/chain_absence_livelock/**
files_may_not_change:
  - state/**
  - architecture/2026_04_02_architecture_kernel.sql
  - architecture/kernel_manifest.yaml
  - src/state/lifecycle_manager.py
  - src/contracts/settlement_semantics.py
  - docs/authority/**
schema_changes: false
ci_gates_required:
  - topology planning lock
  - targeted state and lifecycle tests
  - independent critic and verifier
tests_required:
  - tests/test_reconcile_chain_mirror.py
  - tests/test_architecture_contracts.py::test_lifecycle_phase_kernel_accepts_current_canonical_builder_folds
  - tests/test_architecture_contracts.py::test_lifecycle_phase_kernel_rejects_illegal_fold
parity_required: false
replay_required: false
rollback: Revert the single source/test commit and redeploy through scripts/deploy_live.py; do not restore DB snapshots.
acceptance:
  - Unrelated MONITOR_REFRESHED events do not erase a prior known-absence marker.
  - A second fresh absent snapshot with no open venue order folds the position through the existing ADMIN_VOIDED path.
  - Token presence, size correction, settlement, or another chain-mirror terminal event still prevents a false consecutive-absence inference.
  - Live wealth witness advances beyond CURRENT_WEALTH_POSITION_CHAIN_TIME_INVALID or reports a new exact blocker.
evidence_required:
  - Failing-before and passing-after relationship test.
  - No schema diff and no manual DB mutation.
  - Current process, loaded SHA, canonical DB event/projection, and venue command evidence after deploy.
```

## Routing decision

- Authoritative truth: fresh Chain/CLOB snapshot; append-only `position_events` and the transactionally folded `position_current` projection.
- Change class: architecture bugfix because the defect affects lifecycle truth, but it does not change lifecycle grammar, DB schema, settlement semantics, or the two-independent-read policy.
- Allowed implementation surface: one reconciler helper plus its relationship test. Test registry freshness is a permitted companion.
- Explicit fatal misreads: absence is not inferred from an unknown/stale snapshot; a monitor event is not chain evidence; a local projection is not chain truth; collateral movement alone is not settlement proof.
- Semantic boot profile: execution/lifecycle; no source, settlement, calibration, or historical replay profile applies.

## Verification

1. Demonstrate the current failure with a first absence marker, intervening `MONITOR_REFRESHED`, and a second absent read.
2. Make the marker lookup select the latest chain-mirror evidence boundary rather than the latest arbitrary position event.
3. Prove positive chain evidence and open orders remain fail-closed.
4. Run targeted state/lifecycle tests, independent adversarial review, official deploy, and live observation.

## Pre-deploy evidence

- Failing-before proof: the monitor-noise relationship test classified the second absent read as `review_open_absent` instead of `closed_exited`.
- Passing-after proof: 41 targeted reconciler/lifecycle tests passed, including plain-monitor continuity, semantic-monitor reset, exact-size reappearance reset, repeated-present idempotency, open-order blocking, and legal phase folds.
- Planning evidence: `topology_doctor.py --planning-evidence` returned `ok=true` with no issues for the packet paths.
- Independent critic: the first version was rejected for two P1 counterexamples; the revised version closed both and received `DEPLOY-SAFE` with no P0/P1.
- Current live proof before deploy: PID 25474 is loaded at `a35caf0f9`; 12/12 reactor candidates retry with `CURRENT_WEALTH_POSITION_CHAIN_TIME_INVALID`; Kuala Lumpur position `6be10bfa-f2f` remains `day0_window/synced` with no `chain_seen_at`, while its latest canonical sequence is a chain-mirror `REVIEW_REQUIRED` followed only by plain cycle-runtime `MONITOR_REFRESHED` events.
- Current order proof before deploy: `venue_commands=982`, latest command creation `2026-07-08T23:22:51Z`; no new submit, ACK, or fill exists yet.
- Broader gate residue outside this packet: kernel-manifest check reports the existing `shoulder_sell` SQL-constraint drift; module-boundary check reports the existing `src.control.control_plane -> src.strategy.strategy_profile` violation. `ruff` is not installed; `py_compile` and `git diff --check` passed.
- Schema diff: none. Manual DB mutation: none. Runtime DB backup: none.

## Post-deploy proof

- Commit `16cec04f6` was pushed and loaded by live PID 98439 at 2026-07-10T17:27:48Z.
- The exact deploy restart guard was expired; current control state reported `entries_paused=false`.
- At 2026-07-10T17:29:52.818661Z, position `6be10bfa-f2f` appended canonical `ADMIN_VOIDED` with `chain_mirror_classification=closed_exited`; `position_current` folded to `phase=voided, chain_state=closed_exited`.
- At 2026-07-10T17:31:24Z, reactor no longer reported `CURRENT_WEALTH_POSITION_CHAIN_TIME_INVALID`; it advanced to `GLOBAL_WINNER_AWAITS_CLAIM`, and the following cycle advanced to the next independent live-health gate.
- No new venue command, submit, ACK, or fill was created by this packet. The next blockers are owned by a separate packet.
- Topology feedback: planning-evidence correctly admitted the K0 slice and the independent critic caught two continuity counterexamples; navigation misclassified the task as generic/T3 and `zpkt start` emitted a scope schema that `zpkt commit` could not read.
