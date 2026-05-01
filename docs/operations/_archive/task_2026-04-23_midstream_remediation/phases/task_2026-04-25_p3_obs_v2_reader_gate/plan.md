# P3 4.5.B-lite observation_instants_v2 Reader Gate Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: closed

## Background

POST_AUDIT_HANDOFF 4.5.B requires readers of `observation_instants_v2`
to fail closed unless rows are version-current, provenance-bearing,
training-eligible, causally safe, and source-role eligible. Earlier P1 packets
made the writer stamp authority, data version, provenance identity,
`training_allowed`, `causality_status`, and `source_role`. The remaining seam
is that the canonical analytics consumer still relies on the data-version-only
view and does not apply the rest of the reader gate.

The forensic handoff also raises a separate metric-layer decision: whether
hourly instants should carry high/low metric identity or whether that identity
belongs only at a daily aggregate layer. This packet does not decide that
question. It only gates consumers using fields already populated by the writer.

## Phase Entry Evidence

- Reread root `AGENTS.md`, `docs/operations/current_state.md`,
  `docs/AGENTS.md`, `docs/operations/AGENTS.md`, `architecture/AGENTS.md`,
  `src/state/AGENTS.md`, `scripts/AGENTS.md`, and `tests/AGENTS.md`.
- Ran `python3 scripts/topology_doctor.py --task-boot-profiles --json` and
  `python3 scripts/topology_doctor.py --fatal-misreads --json`; both passed.
- Scout found only two active `observation_instants_current` consumers:
  `scripts/etl_hourly_observations.py` and `scripts/etl_diurnal_curves.py`.
- Architect review narrowed this packet to the canonical analytics consumer
  and readiness diagnostics. `scripts/etl_hourly_observations.py` remains out
  of this first slice because it is a legacy compatibility writer, not a
  canonical training/analytics reader.
- Scout confirmed 4.5.C's canonical `hourly_observations` ban is already
  covered by the prior P3 residual packet.
- Topology impact for the revised slice stays out of K0 schema changes but
  still requires planning-lock evidence because the packet spans scripts,
  tests, docs, and registries.

## Design

Keep `observation_instants_current` unchanged. It remains the atomic
data-version cutover view. Add consumer-local predicates in
`scripts/etl_diurnal_curves.py` using only already-populated non-metric fields:

- authority is one of the writer-approved reader authorities
- `training_allowed = 1`
- `source_role = 'historical_hourly'`
- `causality_status = 'OK'`
- `provenance_json` is valid JSON and carries payload, parser, source, and
  station identity

The readiness diagnostic also gains an explicit non-metric reader identity
check for training-allowed rows: authority must be reader-safe and data version
must be in the v1 observation family. This keeps reader behavior conservative
without mutating production DB rows, without widening compatibility-table
behavior, and without deciding the high/low metric-layer question.

## Scope

_The machine-readable list lives in `scope.yaml`; this section is a
human-readable mirror._

### In scope
- `scripts/etl_diurnal_curves.py`
- `scripts/verify_truth_surfaces.py`
- `scripts/semantic_linter.py` small false-positive fix for
  `calibration_pairs_v2`
- `tests/test_obs_v2_reader_gate.py`
- `tests/test_truth_surface_health.py`
- `tests/test_semantic_linter.py`
- packet/control docs and companion registries

### Out of scope
- production DB mutation
- `src/state/schema/v2_schema.py` and `observation_instants_current` view policy
- `scripts/etl_hourly_observations.py` compatibility-table behavior
- `observation_instants_v2` metric identity population or rollback
- `daily_observations_v2`
- writer/source-routing/station/fallback changes
- row-level quarantine/backfill of historical rows
- P4 data population or training kickoff
- canonical `hourly_observations` ban work already closed by P3 residual guard

## Deliverables
- Add local reader-gate predicates to `scripts/etl_diurnal_curves.py`.
- Add readiness checks for training-allowed observation rows with unsafe
  authority or data-version identity.
- Tighten K2 legacy `calibration_pairs` lint matching so v2 table tests can
  pass static analysis without masking actual legacy-table reads.
- Add tests proving diurnal ETL excludes unsafe current rows and readiness
  reports unsafe reader identity.
- Close A2 control surfaces and make this packet the live active pointer.

## Verification
- `python3 -m py_compile scripts/etl_diurnal_curves.py scripts/verify_truth_surfaces.py scripts/semantic_linter.py tests/test_obs_v2_reader_gate.py tests/test_truth_surface_health.py tests/test_semantic_linter.py`
- `pytest -q tests/test_obs_v2_reader_gate.py tests/test_truth_surface_health.py tests/test_semantic_linter.py`
- `pytest -q tests/test_architecture_contracts.py tests/test_truth_surface_health.py`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --scripts --json`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files scripts/etl_diurnal_curves.py scripts/verify_truth_surfaces.py scripts/semantic_linter.py tests/test_obs_v2_reader_gate.py tests/test_truth_surface_health.py tests/test_semantic_linter.py`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p3_obs_v2_reader_gate/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 scripts/topology_doctor.py --change-receipts --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p3_obs_v2_reader_gate/receipt.json`
- `git diff --check -- <packet files>`

## Closeout

- Implementation commit: `cdec77d` pushed to `origin/midstream_remediation`.
- Runtime heartbeat follow-up: `c653d03` pushed separately.
- Reviewer loop: scout and architect narrowed the scope before implementation;
  code reviewer requested freshness-header and predicate-coverage repairs; the
  repairs were applied and verifier passed.
- Outcome: consumer-local non-metric obs_v2 reader gates and readiness
  diagnostics are closed. The hourly high/low metric-layer decision remains
  out of scope and unresolved.

## Stop Conditions

- Stop if correctness requires assigning high/low metric identity to hourly
  instants.
- Stop if the fix needs to change `observation_instants_current` shared view
  semantics.
- Stop if live or historical production DB rows must be mutated.
- Stop if consumers outside the canonical diurnal analytics path need semantic
  redesign.
- Stop if the fix requires source-role registry or station/fallback routing
  changes.
