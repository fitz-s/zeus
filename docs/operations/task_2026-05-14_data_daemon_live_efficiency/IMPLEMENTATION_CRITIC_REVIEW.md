# Implementation Critic Review

Date: 2026-05-14

Scope: implementation slice for `DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md`.

Verdict: APPROVE

Reviewer: Codex native critic `019e25bd-5818-7731-9c6d-eb0e94e4c509`

Incremental Phase 5 E2E smoke review after initial approval: APPROVE.

Incremental Phase 4 operator handoff review after `producer_readiness` key fix:
APPROVE.

## Review Criteria

- Topology admits descriptive packet plan names and critic evidence without weakening forbidden/no-echo semantics.
- Data-daemon refactor route stays scoped away from production DB mutation, venue actions, TIGGE activation, calibration refit, backfill, settlement source routing, and city config edits.
- ECMWF OpenData HTTP 429 handling honors numeric and HTTP-date `Retry-After` from response time, falls back only when absent or invalid, skips final-failure sleep, and reports fetch timing.
- `forecast_live_daemon` remains an OpenData HIGH/LOW producer plus heartbeat only, with shared OpenData lock mutual exclusion against legacy `ingest_main`.
- Live evaluator cutover consumes producer/source-run/readiness evidence and does not direct-fetch OpenData or hot-write legacy entry readiness in the live path.
- Tests cover cross-module boundaries, not only function outputs.

## Initial REVISE

The first implementation review returned `REVISE` for two blockers:

- `architecture/topology.yaml` negative phrases were too narrow; the route admitted task wording containing `with prod DB mutation`, `mutate production DB rows`, and bare `live venue submission`.
- `architecture/test_topology.yaml` had misindented OpenData rows, causing YAML to parse multiple intended test entries as one scalar and dropping the new forecast-live tests from the intended registry shape.

## Fix Evidence

- Added data-daemon forbidden-intent coverage in `tests/test_digest_profile_matching.py::test_data_daemon_live_efficiency_refactor_forbidden_intent_vetoes_profile`.
- Fixed `architecture/test_topology.yaml` OpenData row indentation and explicitly included the forecast-live/OpenData tests in `DATA_DAEMON_READINESS_CONTRACT`.
- Added registry-shape coverage in `tests/test_topology_doctor.py::test_data_daemon_test_registry_entries_are_flat_yaml_scalars`.
- Added no-network E2E smoke coverage in `tests/test_entry_forecast_evaluator_cutover.py::test_live_mode_actual_reader_consumes_daemon_readiness_before_signal`.

## Verification Snapshot

- `python3 scripts/digest_profiles_export.py --check`: pass.
- Focused implementation tests: `41 passed`.
- Readiness/profile tests: `27 passed`.
- Topology packet/registry tests: `5 passed`.
- `python3 scripts/topology_doctor.py --planning-lock ...`: pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...`: pass.
- `git diff --check`: pass.

Residual note: full `python3 scripts/topology_doctor.py --tests --json` still reports pre-existing repo-wide topology drift outside this slice; the added data-daemon registry-shape test covers the critic blocker directly.
