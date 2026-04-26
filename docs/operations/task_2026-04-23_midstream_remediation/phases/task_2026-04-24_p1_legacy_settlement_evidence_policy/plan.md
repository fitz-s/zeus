# P1.4 Legacy Settlement Evidence Policy Ralplan Packet

Date: 2026-04-24
Branch: `post-audit-remediation-mainline`
Status: closed. Planning commit `da1662f`; implementation commit `df9ece5`.
P1.4 implemented only the read-only diagnostic/test files named below. No
schema, DB, current-fact, calibration, replay, live consumer, or
`settlements_v2` population change was authorized.

## Task

Freeze the next P1 provenance-hardening slice after P1.3 unsafe observation
quarantine diagnostics.

P1.4 must make the legacy `settlements` table's status explicit: existing rows
are evidence-only until market identity, source finalization policy, revision
policy, and market-rule provenance are proven. This slice must not backfill
`settlements_v2`, infer market identity from city/date, or mutate production
settlement rows.

## Required Phase Entry

Before every future phase:

1. Reread root `AGENTS.md`.
2. Run topology navigation for the phase task and candidate files.
3. Explore important routed files before editing.
4. Record topology/global-red issues as evidence, not authority waivers.

P1.4 planning entry evidence:

- Reread `AGENTS.md` and `workspace_map.md`.
- Read `docs/operations/current_state.md`, `docs/operations/AGENTS.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`, and
  `docs/operations/known_gaps.md`.
- Read P1.2 and P1.3 packet boundaries.
- Read forensic settlement/data-readiness surfaces:
  `07_settlement_alignment_audit.md`, `11_data_readiness_ruling.md`,
  `17_apply_order.md`, `03_table_by_table_truth_audit.md`,
  `08_provenance_and_authority_audit.md`, and
  `validation/required_db_queries.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.4 legacy settlement evidence-only finalization policy planning" --files docs/operations/current_state.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md docs/operations/known_gaps.md --json`
  returned known global docs/source/history-lore red issues. Those are derived
  routing debt and do not authorize skipping this packet's scoped gates.
- Scout review mapped the settlement/finality anchors across P1.3, the
  forensic package, v2 schema expectations, and readiness tests. Architect
  review returned PROCEED only as planning-only first, citing unresolved storage
  and consumer-boundary questions.

## Decision

Chosen slice: **read-only settlement evidence policy planning**.

P1.4 must default to fail-closed:

- Legacy `settlements` rows may support audit/evidence exploration only.
- `settlements` rows must not be treated as exact Polymarket market replay,
  calibration labels, or live trading settlement truth without market identity
  and finalization policy.
- City/date is not a market identity. Do not infer `market_slug`,
  `condition_id`, token identity, rule version, or low/high market coverage
  from city/date alone.
- Existing high-only settlement rows do not prove dual-track low settlement
  truth.
- Settlement finalization policy is missing until source URL/page, revision
  behavior, late-update policy, finalization timestamp, and oracle transform
  are explicit.
- `current_data_state.md` says legacy `settlements` is
  canonical-authority-grade for the current data posture. P1.4 must preserve a
  narrower distinction: that status does not make legacy settlements safe for
  exact market replay or calibration/training labels without market identity
  and finalization policy.

P1.4 implementation should extend diagnostic/readiness surfaces first. It may
report legacy settlement evidence-only blockers, but it must not mutate
`state/zeus-world.db`, write quarantine flags, change schemas/views, populate
`settlements_v2`, or change calibration/replay/live readers.

## Boundary

- P1.4 is not P4. `settlements_v2` population from market rules remains P4
  after P0-P3 guardrails pass.
- P1.4 is not P1.5. Eligibility views/adapters and training-preflight cutover
  remain P1.5.
- P1.4 is not P3. Calibration/replay/live safe-view-only consumer rewiring
  remains P3.
- If a future implementation discovers that finalization policy requires
  storage fields, schema changes, or consumer interpretation changes, stop and
  widen the packet. Current settlement schemas do not carry a complete
  `finalization_policy` / `rule_version` contract.

## Scope

Planning commit may change:

- `docs/AGENTS.md`
- `docs/README.md`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`

Implementation files after plan freeze and post-close review:

- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`

Optional future closeout bookkeeping:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`

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

Future P1.4 implementation should extend `build_training_readiness_report()` or
an equivalent read-only diagnostic so current world data remains blocked when
legacy settlement evidence lacks market/finality proof.

Minimum planned blockers:

- `settlements.legacy_market_identity_missing`: legacy `settlements` rows
  lacking `market_slug` or equivalent market identity.
- `settlements.legacy_finalization_policy_missing`: rows or table shape lacking
  explicit finalization/revision policy, finalization timestamp, source URL or
  page, rule version, or oracle transform evidence.
- `settlements.legacy_value_incomplete`: legacy settlement evidence rows with
  missing `settlement_value`, `winning_bin`, metric identity, unit, or
  provenance fields.
- `settlements.legacy_evidence_only`: a summary check making clear that
  populated legacy rows do not satisfy canonical replay/training readiness
  while `settlements_v2` remains empty or market-identity incomplete.

The implementation may report row counts and sample identifiers. It must not
promote legacy rows, infer missing market identity, or write derived claims into
current-fact surfaces from diagnostics alone.

Before implementation, define the accepted column/alias contract for
finalization-policy evidence in the script/tests. The diagnostic must not infer
finalization policy from ad hoc field-name guessing.

## Rejected Options

- Populate `settlements_v2` now: rejected because P4 requires verified market
  rules/source payloads after P0-P3 guardrails pass.
- Mutate legacy `settlements` rows to add inferred market identity: rejected
  because city/date is not a market identity and low/high market identity is
  not derivable from the legacy key.
- Treat `settlements` as canonical because `current_data_state.md` calls it
  canonical-authority-grade: rejected for exact replay/training; the active
  forensic packet and P1.3 boundary keep it evidence-only until market identity
  and finalization policy are proven.
- Add schema/view DDL for eligibility surfaces now: rejected because P1.5 owns
  eligibility views/adapters.
- Rewire calibration/replay/live consumers now: rejected because P3 owns
  safe-view-only consumer hardening.

## Verification Plan

Future implementation must run:

- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --fatal-misreads --json`
- `python3 scripts/topology_doctor.py --navigation --task "P1.4 legacy settlement evidence policy implementation" --files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py --json`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md --json`
- `python3 scripts/topology_doctor.py impact --files <files>`
- `python3 scripts/semantic_linter.py --check <files>` when the implementation
  touches scripts or source-like diagnostics
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py`
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py::TestTrainingReadinessP0 -q`
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --world-db state/zeus-world.db --json`
  must be run read-only and record current settlement blocker codes/counts.
- Receipt/work-record/current-state/map-maintenance/freshness gates and
  `git diff --check`.

## Acceptance

- P1.3 is closed in repo-facing control surfaces.
- `current_state.md` and `docs/operations/AGENTS.md` point to this P1.4
  planning packet.
- P1.4 plan explicitly freezes implementation until post-close review.
- Future implementation is read-only against production DB by default.
- Legacy `settlements` rows are explicitly evidence-only for exact
  replay/training until market identity and finalization policy are proven.
- P1.5, P3, and P4 boundaries are explicit and do not smuggle eligibility
  views, consumer rewiring, or `settlements_v2` population into P1.4.

## Stop Conditions

- If implementation needs production DB mutation, stop and open a DB/data
  quarantine packet.
- If implementation needs `src/state/**` schema/view DDL, stop and open P1.5
  or a state-schema packet.
- If implementation needs market-rule source files or settlement-source
  scraping, stop and open P4/data-backfill planning.
- If calibration/replay/live consumers must change, stop unless the packet is
  explicitly widened to P3.
