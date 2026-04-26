# P3 4.5.A Settlement Metric-Read Linter Closeout Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: in progress

## Background

POST_AUDIT handoff item 4.5.A identified cross-table settlement reads that
could silently mix HIGH and LOW rows unless every consumer pins
`temperature_metric`. Current code already contains the four originally named
filters, and the H3 linter rule exists, but the rule still exempted `scripts/`
as a directory. That exemption let replay/training/live consumer scripts read
`settlements` by city/date without a metric predicate.

This packet closes the executable remainder of 4.5.A by tightening the H3
enforcement boundary for named consumer scripts and fixing the remaining bare
settlement reads it exposes. It also refreshes stale route/control surfaces
that still marked the closed P2 4.4.B-lite packet as active.

Phase-entry evidence:

- Reread root `AGENTS.md`.
- Read `docs/operations/current_state.md`, `docs/operations/AGENTS.md`,
  `docs/operations/current_data_state.md`, and
  `docs/operations/current_source_validity.md`.
- Read POST_AUDIT handoff 4.5.A and forensic P3 usage-path guidance.
- Read `scripts/AGENTS.md`, `tests/AGENTS.md`, and the active packet scope.
- Ran semantic boot and fatal-misread checks; both passed.
- Scout/architect review selected P3 4.5.A over P2 4.4.A because P2 4.4.A
  requires a dedicated write-history design, while this slice is bounded and
  non-mutating.

## Scope

_The machine-readable list lives in `scope.yaml`; this section is a
human-readable mirror._

### In scope

- `scripts/semantic_linter.py`
- `tests/test_semantic_linter.py`
- `scripts/backfill_ens.py`
- `scripts/backfill_observations_from_settlements.py`
- `scripts/bridge_oracle_to_calibration.py`
- `scripts/investigate_ecmwf_bias.py`
- `architecture/test_topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- this packet folder

### Out of scope
- production DB mutation
- P2 4.4.A upsert/revision-history redesign
- P3 4.5.B observation_instants_v2 reader gate design
- P3 4.5.C broad `hourly_observations` ban
- safe-view migration or consumer rewiring beyond H3 metric predicates
- diagnostic/audit scripts that intentionally inspect settlements cross-metric
- P4 data population

## Deliverables

- Promote H3 settlement metric-read linting into a named set of
  replay/training/live consumer scripts instead of exempting all `scripts/`.
- Add `temperature_metric='high'` predicates to the remaining consumer
  settlement reads exposed by the tightened boundary.
- Close review-found H3 bypass shapes: projected-only `temperature_metric`,
  schema-qualified `main.settlements`, quoted `"settlements"`, and
  other-table metric filters.
- Add tests proving enforced scripts fail on bare settlement reads and that the
  current enforced script set is clean.
- Update stale docs/control surfaces so closed P2 and active P3 routing agree.

## Verification

- `python3 -m py_compile scripts/semantic_linter.py tests/test_semantic_linter.py scripts/backfill_ens.py scripts/backfill_observations_from_settlements.py scripts/bridge_oracle_to_calibration.py scripts/investigate_ecmwf_bias.py`
- `python3 scripts/semantic_linter.py --check scripts/backfill_ens.py scripts/backfill_observations_from_settlements.py scripts/bridge_oracle_to_calibration.py scripts/investigate_ecmwf_bias.py scripts/etl_historical_forecasts.py scripts/etl_forecast_skill_from_forecasts.py scripts/validate_dynamic_alpha.py`
- `pytest tests/test_semantic_linter.py -q`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p3_settlement_metric_linter_closeout/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 scripts/topology_doctor.py --work-record --work-record-path docs/operations/task_2026-04-25_p3_settlement_metric_linter_closeout/work_log.md`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <packet scripts/tests>`
- `git diff --check -- <packet files>`

## Stop Conditions

- Stop if H3 enforcement requires changing diagnostic/audit scripts that
  intentionally inspect multiple metrics.
- Stop if the fix expands into P3 4.5.B/4.5.C reader-gate or safe-view work.
- Stop if a remaining bare read requires operator semantics for HIGH vs LOW.
