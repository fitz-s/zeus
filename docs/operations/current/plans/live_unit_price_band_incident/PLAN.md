---
work_packet_id: BUG-2026-07-20-LIVE-UNIT-PRICE-BAND
packet_type: bugfix_packet
objective: "Restore a non-waivable inclusive [0.05, 0.95] unit-price boundary for every live BUY and SELL."
why_this_now: "A Hong Kong BUY NO order filled 20 shares at 0.999 despite the operator's absolute price directive."
why_not_other_approach:
  - "An entry-only filter is bypassable by alternate entry, exit, batch, recovery, or adapter paths."
  - "Rejecting market snapshots would hide real books; the boundary belongs on live commands and submit seams."
truth_layer: "venue command journal and VenueSubmissionEnvelope"
control_layer: "durable entries_paused override through exact-SHA deployment proof"
evidence_layer: "targeted tests, invariant checks, live process/config/DB/log proof, SDK-free runtime probe"
zones_touched: [K0_frozen_kernel, K2_runtime, docs]
invariants_touched: [INV-43]
required_reads:
  - AGENTS.md
  - architecture/self_check/zero_context_entry.md
  - architecture/self_check/authority_index.md
  - architecture/kernel_manifest.yaml
  - architecture/negative_constraints.yaml
  - docs/authority/zeus_current_architecture.md
  - docs/authority/zeus_current_delivery.md
  - docs/authority/zeus_change_control_constitution.md
  - docs/reference/zeus_execution_lifecycle_reference.md
files_may_change:
  - AGENTS.md
  - architecture/invariants.yaml
  - architecture/source_rationale.yaml
  - architecture/test_topology.yaml
  - docs/authority/zeus_current_architecture.md
  - docs/reference/zeus_execution_lifecycle_reference.md
  - docs/operations/current/plans/INDEX.md
  - docs/operations/current/plans/live_unit_price_band_incident/PLAN.md
  - docs/operations/current/plans/live_unit_price_band_incident/scope.yaml
  - src/contracts/venue_submission_envelope.py
  - src/engine/cycle_runner.py
  - src/execution/executor.py
  - src/state/venue_command_repo.py
  - src/venue/polymarket_v2_adapter.py
  - tests/test_riskguard_red_durable_cmd.py
  - tests/test_executor.py
  - tests/test_v2_adapter.py
  - tests/test_venue_command_repo.py
files_may_not_change:
  - state/zeus-world.db
  - state/zeus-forecasts.db
  - state/zeus_trades.db
schema_changes: false
ci_gates_required: [scripts/check_work_packets.py, scripts/check_kernel_manifests.py]
tests_required:
  - tests/test_riskguard_red_durable_cmd.py
  - tests/test_executor.py
  - tests/test_v2_adapter.py
  - tests/test_venue_command_repo.py
parity_required: true
replay_required: false
rollback: "Revert the repair commit and keep entries paused; no schema or DB rollback is required."
acceptance:
  - "0.05 and 0.95 remain valid when all other venue requirements pass."
  - "Anything below 0.05 or above 0.95 rejects for BUY/SELL, ENTRY/EXIT, single/batch."
  - "Order-creating persistence, envelope, and independent SDK-boundary checks all fail closed."
  - "CANCEL can still remove an existing tail-priced resting order because it creates no trade."
  - "Out-of-band rejection produces no SDK call even if the envelope guard is monkeypatched."
  - "Production boots the exact repair SHA and produces a current SDK-free rejection receipt."
evidence_required:
  - "manifest diff: source_rationale and test_topology updated; kernel manifest unchanged"
  - "schema diff: none"
  - "current control override, process, heartbeat, exit-monitor, boot SHA, and rejection evidence"
---

# Live unit-price band incident repair

Failure class: contradictory executable law removed an operator-mandated live-money boundary. The repair closes the category at three independent seams, not only the Hong Kong strategy path.

The reactor entered durable reduce-only containment before implementation. Existing positions continue through monitoring and exit evaluation; new entries remain paused through deployment proof.
