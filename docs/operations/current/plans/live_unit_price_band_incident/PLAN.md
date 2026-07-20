---
work_packet_id: BUG-2026-07-20-LIVE-UNIT-PRICE-BAND-V2
packet_type: bugfix_packet
objective: "Restore and make restart-blocking the non-waivable inclusive [0.05, 0.95] unit-price boundary for every live BUY and SELL."
why_this_now: "After the first repair, commit 4ab725e8e explicitly removed the three guards and their antibodies; the later live deployment filled Seoul NO orders at 0.997, 0.998, and 0.999."
why_not_other_approach:
  - "An entry-only strategy filter remains bypassable by alternate entry, exit, batch, recovery, or adapter paths."
  - "Tests alone were deleted together with the guards; the official live restart preflight must independently execute the required behavior and refuse a regressed deployment."
  - "Rejecting market snapshots would hide truthful books and could prevent cancellation; the boundary belongs on order-creating commands and submit seams."
truth_layer: "venue command journal and VenueSubmissionEnvelope"
control_layer: "indefinite entries_paused override through exact-SHA deployment proof; resume remains operator-gated"
evidence_layer: "targeted tests, invariant checks, live process/config/DB/log proof, restart-preflight antibody, SDK-free runtime probe"
zones_touched: [K0_frozen_kernel, K2_runtime, docs, config]
invariants_touched: [INV-43]
required_reads:
  - AGENTS.md
  - architecture/self_check/zero_context_entry.md
  - architecture/self_check/authority_index.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - docs/authority/zeus_current_architecture.md
  - docs/authority/zeus_current_delivery.md
  - docs/authority/zeus_change_control_constitution.md
  - docs/reference/zeus_execution_lifecycle_reference.md
files_may_change:
  - AGENTS.md
  - architecture/invariants.yaml
  - architecture/script_manifest.yaml
  - architecture/source_rationale.yaml
  - architecture/test_topology.yaml
  - config/settings.json
  - config/settings.example.json
  - docs/authority/zeus_current_architecture.md
  - docs/reference/zeus_execution_lifecycle_reference.md
  - docs/operations/current/plans/INDEX.md
  - docs/operations/current/plans/live_unit_price_band_incident/PLAN.md
  - docs/operations/current/plans/live_unit_price_band_incident/scope.yaml
  - scripts/check_live_restart_preflight.py
  - scripts/deploy_live.py
  - src/contracts/venue_submission_envelope.py
  - src/data/polymarket_client.py
  - src/engine/cycle_runner.py
  - src/engine/cycle_runtime.py
  - src/engine/lifecycle_events.py
  - src/execution/executor.py
  - src/state/venue_command_repo.py
  - src/venue/polymarket_v2_adapter.py
  - tests/test_ops_scripts_smoke.py
  - tests/test_check_live_restart_preflight.py
  - tests/test_riskguard_red_durable_cmd.py
  - tests/test_executor.py
  - tests/test_v2_adapter.py
  - tests/test_venue_command_repo.py
  - tests/test_runtime_guards.py
files_may_not_change:
  - state/zeus-world.db
  - state/zeus-forecasts.db
  - state/zeus_trades.db
schema_changes: false
ci_gates_required: [scripts/check_work_packets.py, scripts/check_kernel_manifests.py]
parity_required: true
replay_required: false
rollback: "Revert the repair commit and keep entries paused; no schema or DB rollback is required."
acceptance:
  - "0.05 and 0.95 remain valid when all other venue requirements pass."
  - "Anything below 0.05 or above 0.95 rejects for BUY/SELL, ENTRY/EXIT/DERISK, single/batch."
  - "Order-creating persistence, envelope, and independent SDK-boundary checks all fail closed."
  - "CANCEL can still remove an existing tail-priced resting order because it creates no trade."
  - "Official live restart preflight blocks if config, envelope, persistence, or SDK-boundary behavior drifts."
  - "Production boots the exact repair SHA while entries remain paused and produces a current SDK-free rejection receipt."
  - "Post-start monitoring refreshes every open position; a later non-transition event cannot hide an existing Day0 transition, while pending-exit and terminal lifecycle truth remain absorbing."
---

# Live unit-price band incident repair V2

This packet reopens the failed first repair. The causal chain is proven: `4ab725e8e` deleted the guards and antibodies; `4ca40025` was then loaded live; canonical commands subsequently filled outside the operator band.

The repair restores defense in depth and adds an independent restart admission check so deleting the same helpers no longer produces a deployable live commit. Existing positions continue through monitoring and exit evaluation; new entries remain paused until the operator explicitly resumes them.

## Local verification

- Canonical containment remains active with no expiry from `2026-07-20T15:27:19.924970+00:00`; no venue command was created after that timestamp at the verification cut.
- Focused guard, envelope-wiring deletion antibody, deploy-attestation, single/batch adapter, executor, persistence, and RED-cancel coverage passes `45/45`. The direct SDK-free probe accepts exactly `0.05`/`0.95` and rejects `0.049`, `0.951`, `0.997`, `0.998`, and `0.999` at all three independent guards.
- Module compilation, `git diff --check`, and planning lock pass. Broader legacy suites retain unrelated pre-existing fixture/manifest drift and are not represented as clean.
- Deployment must preserve the indefinite entry pause; resume remains a separate explicit operator action.
