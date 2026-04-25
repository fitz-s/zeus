# P1.5 Eligibility Views And Training Preflight Ralplan Packet

Date: 2026-04-24
Branch: `post-audit-remediation-mainline`
Status: planning-only packet. P1.4 implementation closed at `df9ece5`; P1.4
control surfaces closed at `50cd713`. No implementation, schema, DB, replay,
live, or settlement-v2 population change is authorized by this planning commit.

## Task

Freeze the P1.5 contract for eligibility views/adapters plus
calibration/training-preflight cutover.

P1.5 exists because P1.1-P1.4 now expose fail-closed provenance, observation,
and settlement blockers, but training/calibration entry points still need a
single contract they must consume before writing or fitting training artifacts.
This packet plans that contract; it does not implement it.

## Required Phase Entry

Before every future phase:

1. Reread root `AGENTS.md`.
2. Run topology navigation for the phase task and candidate files.
3. Explore important routed files before editing.
4. Lock schema/field/row-class contracts before code edits.
5. Record topology/global-red issues as evidence, not authority waivers.

P1.5 planning entry evidence:

- Reread `AGENTS.md`, `workspace_map.md`, `docs/operations/current_state.md`,
  and `docs/operations/AGENTS.md`.
- Read current fact companions:
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`, and
  `docs/operations/known_gaps.md`.
- Read P1.1-P1.4 packet boundaries:
  `task_2026-04-24_p1_source_role_registry/plan.md`,
  `task_2026-04-24_p1_writer_provenance_gates/plan.md`,
  `task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`, and
  `task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`.
- Read forensic ruling and sequencing:
  `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/11_data_readiness_ruling.md`,
  `17_apply_order.md`, and `12_major_findings.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.5 eligibility views adapters training preflight planning" --files <planning files> --json`
  returned known global docs/source/history-lore red issues while routing this
  packet's planning changes to the docs/control files named below.
- Map-maintenance precommit required companion registry updates in
  `docs/AGENTS.md`, `docs/README.md`, `architecture/topology.yaml`, and
  `architecture/docs_registry.yaml` for the new packet directory. These are
  planning/control-surface updates only, not implementation scope.
- Read relevant existing implementation seams:
  `scripts/verify_truth_surfaces.py`,
  `scripts/rebuild_calibration_pairs_v2.py`,
  `scripts/refit_platt_v2.py`,
  `src/state/schema/v2_schema.py`,
  `src/calibration/store.py`, and `src/calibration/manager.py`.
- Scout review mapped likely future implementation files and contract
  questions. Architect review returned PASS only for planning-only P1.5 and
  blocked implementation-first, K0 schema-first, P3-widened, or P4-widened
  variants.

## Decision

Chosen path: **planning-only now; future implementation starts with
script-side preflight/adapters, not K0 state-schema views**.

Rationale:

- `scripts/verify_truth_surfaces.py` already owns the read-only fail-closed
  training-readiness diagnostic surface for P0/P1 blockers.
- `scripts/rebuild_calibration_pairs_v2.py` is the first training artifact
  writer in scope. It filters `ensemble_snapshots_v2` by metric,
  `training_allowed`, `causality_status`, and authority, but still reads daily
  labels directly from `observations`.
- `scripts/refit_platt_v2.py` reads `calibration_pairs_v2` directly and should
  remain blocked behind the same preflight contract before live fitting.
- `src/state/schema/v2_schema.py::observation_instants_current` is currently a
  data-version cutover view, not an eligibility view. Changing its semantics
  would alter a shared K0/K1 state-schema surface and requires a separate
  approved state-schema packet.
- Forensic P0-P4 sequencing keeps broad replay/live consumer rewiring in P3
  and canonical v2 settlement/market population in P4.

## Contract To Lock

P1.5 must avoid a circular "one readiness check blocks its own inputs" design.
The future implementation must separate preflight modes by phase:

- **Full training readiness**: proves the DB is safe to claim
  training/calibration readiness end to end. This may continue to require
  populated `calibration_pairs_v2` and `platt_models_v2`.
- **Calibration-pair rebuild preflight**: proves inputs to
  `scripts/rebuild_calibration_pairs_v2.py` are safe before live writes. It
  must not require `calibration_pairs_v2` or `platt_models_v2` to already be
  populated.
- **Platt-refit preflight**: proves `calibration_pairs_v2` is safe for
  `scripts/refit_platt_v2.py` before live fitting. It must not require
  `platt_models_v2` to already be populated.

Minimum future preflight dimensions:

- Snapshot inputs: `ensemble_snapshots_v2` rows must carry explicit
  `temperature_metric`, `physical_quantity`, `observation_field`,
  canonical metric data version, `training_allowed=1`,
  `causality_status='OK'`, `authority='VERIFIED'`, and non-reconstructed
  issue/available/fetch time.
- Observation labels: `observations` rows used as labels must be
  `authority='VERIFIED'` with non-empty provenance metadata. WU empty
  provenance, missing provenance columns, and HKO current-truth ambiguity
  remain fail-closed.
- Existing hourly evidence: `observation_instants_v2` must not be treated as
  training evidence unless `source_role='historical_hourly'`,
  `training_allowed=1`, and `causality_status='OK'`.
- Settlement evidence: legacy `settlements` remains evidence-only for exact
  market replay/training unless market identity and finalization policy are
  proven. `settlements_v2` and market-event population remain P4.
- Calibration pairs: `calibration_pairs_v2` rows used by Platt refit must be
  metric-scoped, `training_allowed=1`, `authority='VERIFIED'`,
  `causality_status='OK'`, and have non-empty `decision_group_id`.

## Scope

Planning commit may change:

- `docs/AGENTS.md`
- `docs/README.md`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/work_log.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`

Allowed future implementation files only after this plan is reviewed, pushed,
post-close reviewed, and a fresh phase-entry is completed:

- `scripts/verify_truth_surfaces.py`
- `scripts/rebuild_calibration_pairs_v2.py`
- `scripts/refit_platt_v2.py`
- `tests/test_truth_surface_health.py`
- focused calibration/rebuild tests routed by topology, such as
  `tests/test_phase7a_metric_cutover.py` and
  `tests/test_calibration_bins_canonical.py`

Optional future closeout bookkeeping:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/work_log.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`

Forbidden files for this planning packet:

- `state/**`
- `.code-review-graph/graph.db`
- `src/**`
- `src/state/**`
- `src/calibration/**`
- `src/engine/**`
- `src/execution/**`
- `docs/authority/**`
- `architecture/**` except `architecture/topology.yaml` and
  `architecture/docs_registry.yaml` companion registry updates named above
- production DBs, generated runtime JSON, and graph artifacts

## Planned Implementation Shape

Future P1.5 implementation should:

1. Extend `scripts/verify_truth_surfaces.py` with phase-specific, read-only
   preflight reports for calibration-pair rebuild and Platt refit.
2. Make `scripts/rebuild_calibration_pairs_v2.py` consume the rebuild
   preflight before any live write path. Dry-run may report blockers, but live
   writes must fail closed.
3. Make `scripts/refit_platt_v2.py` consume the refit preflight before any
   live write path, or explicitly leave refit blocked behind a documented
   preflight command if implementation review narrows the slice.
4. Add tests that prove unsafe observations, unsafe snapshots, unsafe
   calibration pairs, and legacy settlement evidence-only rows block the
   correct preflight mode without creating circular blockers.
5. Preserve existing `training-readiness` behavior as the full end-to-end
   readiness verdict. New mode names must make phase scope explicit.

## Rejected Options

- Start with `src/state/**` schema/view DDL: rejected because
  `observation_instants_current` is a shared data-version cutover view and
  state schema is K0/K1 planning-locked.
- Change `observation_instants_current` to filter eligibility: rejected for
  P1.5 first implementation because it would widen into ETL/current-view
  semantics beyond calibration preflight.
- Rewire runtime/replay/live consumers now: rejected because P3 owns
  safe-view-only consumer hardening.
- Populate `settlements_v2`, `market_events_v2`, or market-price history now:
  rejected because P4 owns canonical v2 truth population after P0-P3 pass.
- Treat full `training-readiness` as the only gate for rebuild/refit: rejected
  because it would circularly block the creation of the very artifacts it
  checks.

## Verification Plan

This planning packet must run:

- `python3 scripts/topology_doctor.py --planning-lock --changed-files <planning files> --plan-evidence docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md --json`
- `python3 scripts/topology_doctor.py --work-record --changed-files <planning files> --work-record-path docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/work_log.md --json`
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <planning files> --receipt-path docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json --json`
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --receipt-path docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json --json`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <planning files> --json`
- `python3 -m json.tool docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`
- `git diff --check`

Future implementation must run at minimum:

- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --fatal-misreads --json`
- targeted topology navigation/digest for every code/test file touched
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py`
- focused pytest for `tests/test_truth_surface_health.py::TestTrainingReadinessP0`
  plus rebuild/refit contract tests routed by topology
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --world-db state/zeus-world.db --json`
  as read-only evidence; current production DB is expected to remain
  `NOT_READY` until later P/P4 work clears blockers.
- `python3 scripts/semantic_linter.py --check <touched scripts/tests>`
- receipt/work-record/current-state/map-maintenance gates and `git diff --check`

## Acceptance For This Planning Packet

- `current_state.md` and `docs/operations/AGENTS.md` point at this P1.5
  planning packet; docs/architecture companion registries route the packet.
- The receipt names exactly the planning-only changed files.
- The plan states script-side preflight/adapters as the first future
  implementation boundary.
- The plan does not authorize code, schema, DB, live/replay, or P4 data work.
- Architect PASS is recorded, and critic/verifier review is required before
  any future implementation packet.

## Stop Conditions

- If implementation needs `src/state/**` schema/view DDL, including changing
  `observation_instants_current`, stop and open a separate state-schema packet.
- If implementation needs `src/calibration/**`, `src/engine/**`, or
  `src/execution/**` runtime/replay/live consumer rewiring, stop and move the
  work to P3.
- If implementation needs `settlements_v2`, `market_events_v2`,
  `market_price_history`, market-identity backfill, finalization-policy
  storage, or legacy-settlement promotion, stop and move the work to P4.
- If HKO or Hong Kong rows need training eligibility, stop for fresh
  source-validity audit evidence.
- If source-role registry semantics need changes, stop and open a P1.1
  follow-up rather than editing registry behavior inside P1.5.
- If production DB mutation or existing-row quarantine is needed, stop and open
  a DB/data packet.
