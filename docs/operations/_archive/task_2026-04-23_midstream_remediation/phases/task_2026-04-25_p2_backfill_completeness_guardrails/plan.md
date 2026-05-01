# P2 4.4.B-lite Backfill Completeness Guardrails Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Status: closed

## Task

Implement the first P2 backfill guardrail slice from POST_AUDIT 4.4.B:
script-level completeness manifests, expected-count checks, fail-threshold
checks, and non-silent partial-success exits for observation backfill tools.

This packet is deliberately **4.4.B-lite**. It does not implement 4.4.A
hash-checked upsert/revision history, does not mutate production DB rows, and
does not change `INSERT OR REPLACE` write semantics.

## Phase Entry Evidence

- Reread root `AGENTS.md`.
- Read `docs/operations/current_state.md`, `docs/operations/AGENTS.md`,
  `docs/operations/current_data_state.md`, and
  `docs/operations/current_source_validity.md`.
- Read POST_AUDIT handoff sections 4.3 and 4.4 plus forensic apply-order and
  backfill-script audit references.
- Ran semantic boot and fatal-misread checks: both passed.
- Ran topology navigation for the candidate files. Current topology admission
  is known-stale for this multi-script backfill guardrail slice and returns a
  generic/advisory profile; this packet keeps the stale admission result as
  evidence and uses planning-lock, script manifest, test topology, focused
  tests, and receipt gates as the closeout authority.
- Scout/architect consensus selected this package after confirming P1's
  remaining destructive/K0 work is not authorized by the current control
  pointer.

## Scope

Allowed files:

- `scripts/backfill_completeness.py`
- `scripts/backfill_obs_v2.py`
- `scripts/backfill_wu_daily_all.py`
- `scripts/backfill_hko_daily.py`
- `scripts/backfill_ogimet_metar.py`
- `architecture/script_manifest.yaml`
- `architecture/test_topology.yaml`
- `architecture/topology.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `tests/test_backfill_scripts_match_live_config.py`
- `tests/test_backfill_completeness_guardrails.py`
- this packet's `plan.md`, `work_log.md`, and `receipt.json`

Forbidden:

- `state/**` and production DB mutation
- `.code-review-graph/**`
- `src/state/**` schema/view work
- 4.4.A upsert/revision-history redesign
- P3 replay/live consumer rewiring
- P4 data population

## Implementation Semantics

- Add one shared script utility for completeness manifest evaluation and JSON
  emission.
- Each target script exposes:
  - `--completeness-manifest PATH`
  - `--expected-count N`
  - `--fail-threshold-percent P`
- Manifests are written in dry-run and apply modes.
- Default threshold is `0.0%`, so any observed failure/shortfall returns
  non-zero unless the operator explicitly permits a higher threshold.
- A failure rate equal to the threshold passes; only rates above the threshold
  fail.
- Existing write paths and SQL are left untouched.

## Verification Plan

- `.venv/bin/python -m py_compile` for touched scripts/tests.
- `.venv/bin/python -m pytest tests/test_backfill_completeness_guardrails.py tests/test_backfill_scripts_match_live_config.py -q`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --scripts --json`
- planning-lock, work-record, change-receipts, current-state receipt binding,
  map-maintenance, freshness metadata, and `git diff --check`.

## Stop Conditions

- Stop if a fix requires production DB mutation.
- Stop if a fix requires replacing `INSERT OR REPLACE` with revision tables.
- Stop if script guardrails require schema/view changes.
- Stop if current-source/current-data fact surfaces become stale for the
  backfill claim being made.
