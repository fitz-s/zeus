# P1.3 Unsafe Observation Quarantine Ralplan Packet

Date: 2026-04-24
Branch: `p1-unsafe-observation-quarantine`
Status: implementation closeout packet. The planning gate has been closed and
this packet authorized only read-only diagnostic/test changes in
`scripts/verify_truth_surfaces.py` and `tests/test_truth_surface_health.py`
plus closeout bookkeeping. No production DB, schema, current-fact,
calibration, replay, or live consumer change is authorized.

## Task

Freeze the next P1 provenance-hardening slice after P1.2 writer provenance
gates. P1.3 must make unsafe existing observation families fail closed before
eligibility views/adapters and calibration/training preflight cutover happen in
later P1.

This packet exists because restored `AGENTS.md`, topology, source rationale,
and test-trust policy changed the phase boundary: P1 cannot end with producer
labels only. However, the first post-P1.2 slice should not jump directly to
schema views or broad consumer rewiring. It should quarantine or mark unsafe
observation families and make readiness diagnostics expose those blockers.

## Required Phase Entry

Before every future phase, including P1.3 implementation:

1. Reread root `AGENTS.md`.
2. Run topology navigation for the phase task and candidate files.
3. Explore the important files routed by topology before editing.
4. Record topology/global-red issues as evidence, not as authority waivers.

P1.3 planning entry evidence:

- Reread `AGENTS.md` and `workspace_map.md`.
- Read `docs/operations/current_state.md` and `docs/operations/AGENTS.md`.
- Read `POST_AUDIT_HANDOFF_2026-04-24.md` sections 4.3 and 6.
- Read forensic `11_data_readiness_ruling.md`, `17_apply_order.md`, and
  `prompts/codex_p1_execute_provenance_hardening.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.3 unsafe observation quarantine planning packet" --files <candidate files> --json`
  returned restored global docs/source/history-lore red issues and gate-trust
  context. Those global issues remain routing debt; they do not authorize
  skipping this packet's scoped gates.

P1.3 implementation entry evidence:

- Reread current root `AGENTS.md`, `workspace_map.md`,
  `docs/operations/current_state.md`, and the active P1.3 packet.
- Read current fact companions:
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`, and
  `docs/operations/known_gaps.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.3 unsafe observation quarantine implementation" --files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py docs/operations/current_state.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json --json`
  returned known global docs/source/history-lore red issues and generic source
  modification warnings. Those are derived routing debt; this packet remains
  scoped to read-only diagnostics/tests and closeout bookkeeping.

## Decision

Chosen slice: **read-only quarantine / non-training policy planning for
existing unsafe observation families**.

P1.3 must default to fail-closed and avoid retroactive proof fabrication:

- WU daily rows with empty provenance are unsafe for training by default.
- Fallback evidence rows remain non-training even when present.
- HKO / Hong Kong remains cautionary without fresh audit evidence.
- Existing `observation_instants_v2` rows with missing role, fallback role, or
  non-OK causality must be reported as blockers or non-training, not silently
  promoted.
- Rows lacking payload hash, source URL/file, parser version, or station
  registry version remain unsafe for canonical training unless a later packet
  proves those fields or quarantines the row family.

P1.3 implementation should extend existing diagnostic/readiness surfaces first,
not mutate production DB rows. Production DB quarantine updates, schema/view
DDL, or current-fact promotion require a separate reviewed packet or explicit
operator decision.

Boundary for the following packets:

- P1.4 is legacy-settlement evidence-only / finalization policy for existing
  rows. It is not `settlements_v2` population, market-identity backfill, or
  new settlement DB authority.
- P1.5 is eligibility views/adapters plus calibration/training-preflight
  cutover. It may consume P1.3/P1.4 diagnostics only after their blockers are
  visible and fail-closed.
- P3 remains the broader replay/live consumer rewiring phase.

## Scope

Planning commit may change:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`
- `docs/AGENTS.md`
- `docs/README.md`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`

Allowed future implementation files after plan freeze and post-close review:

- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`

Implemented files:

- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`

Optional future closeout bookkeeping:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`

Forbidden files:

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

## Planned Implementation Semantics

P1.3 implementation should extend `build_training_readiness_report()` or its
equivalent diagnostics so the current world DB remains training-blocked when
unsafe observation rows exist.

Minimum planned blockers:

- `observations.verified_without_provenance`: `authority='VERIFIED'` rows with
  missing, SQL-empty, whitespace-only, or structurally empty JSON provenance
  metadata. Empty objects/lists such as `'{}'` and `'[]'` must block; P1.3
  implementation needs an explicit regression for that shape because the
  current diagnostic only catches SQL-empty/NULL fields.
- `observations.wu_empty_provenance`: WU daily rows that lack per-row payload
  or provenance evidence.
- `observation_instants_v2.training_role_unsafe`: rows marked
  `training_allowed=1` with missing, fallback, unknown, HKO-caution, or
  otherwise ineligible `source_role`.
- `observation_instants_v2.causality_unsafe`: rows marked training-allowed
  while `causality_status` is missing, empty, or not `OK`.
- `payload_identity_missing`: row families missing payload hash/source URL or
  file/parser version/station registry evidence where the table has enough
  columns to check this.

The implementation may report row counts and sample identifiers. It must not
write to `state/zeus-world.db` or change `current_data_state.md` truth claims
from diagnostics alone.

## Rejected Options

- Retroactively reconstruct provenance from `source` + `imported_at` +
  station fields: rejected for P1.3 because it risks implying forensic-grade
  evidence without payload/log proof.
- Mutate production DB rows to `QUARANTINED` in this slice: rejected because
  P1.3 is a read-only diagnostic/quarantine-policy slice unless a later packet
  explicitly approves DB mutation.
- Jump directly to `v_training_eligible_observations` in `src/state/schema`: rejected
  for P1.3 because schema/view DDL is K0 state-schema work and should follow
  unsafe-row quarantine policy.
- Move all calibration/replay/live consumers now: rejected because the restored
  law calls for a hybrid boundary. Calibration/training preflight cutover is
  P1.5; broad replay/live rewiring remains P3.

## Verification Plan

Future implementation must run:

- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --fatal-misreads --json`
- `python3 scripts/topology_doctor.py --navigation --task "P1.3 unsafe observation quarantine implementation" --files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py --json`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md --json`
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py`
- Audit `tests/test_truth_surface_health.py` under the restored test-trust
  policy before using full-file results as closeout evidence. The file has a
  lifecycle header and is categorized as a core law antibody, but it is not in
  `architecture/test_topology.yaml::test_trust_policy.trusted_tests` and has
  high-sensitivity skip debt; record the audit/skip assumptions instead of
  treating a full-file pass as automatically trusted.
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py -q`
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --world-db state/zeus-world.db --json`
  must be run read-only against the live world DB before implementation
  closeout. Closeout evidence must record the observed blocker codes/counts for
  the current unsafe families, including WU empty provenance and unsafe
  `observation_instants_v2` training-role/causality rows.
- Trusted compatibility checks:
  `.venv/bin/python -m pytest tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_tier_resolver.py tests/test_backfill_scripts_match_live_config.py -q`
- Receipt/work-record/current-state/map-maintenance/freshness gates and
  `git diff --check`.

## Acceptance

- P1.2 is closed in repo-facing control surfaces.
- `current_state.md` and `docs/operations/AGENTS.md` point to this P1.3
  planning packet.
- P1.3 plan explicitly freezes implementation until post-close review.
- Future implementation is read-only against production DB by default.
- Unsafe observation families are fail-closed, not retroactively promoted.
- Implementation extends training-readiness diagnostics without mutating
  production DB/state, schema, calibration, replay, or live consumers.
- Live read-only closeout reports current fail-closed blockers for WU empty
  provenance and unsafe `observation_instants_v2` training role rows.
- P1.4, P1.5, and P3 boundaries are explicit and do not smuggle
  `settlements_v2`, market-identity backfill, schema/view DDL, or broad
  replay/live consumer rewiring into P1.3.

## Stop Conditions

- If implementation needs production DB mutation, stop and open a DB/data
  quarantine packet.
- If implementation needs `src/state/**` schema/view DDL, stop and open P1.5
  or a state-schema packet.
- If HKO or Hong Kong rows need training eligibility, stop for fresh source
  audit evidence.
- If source-role registry semantics must change, stop and reopen a P1.1
  follow-up.
- If calibration/replay/live consumers must change, stop unless the packet is
  explicitly widened to P1.5 or P3.
