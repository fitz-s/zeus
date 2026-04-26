# PR 17 Review Fixes Packet

Date: 2026-04-26
Branch: `main`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: closed

## Background

PR #17 was merged before its safe review findings were repaired. This packet
closes the actionable review bugs on `main` without widening into runtime-state
governance or P4 mutation work.

## Scope

In scope:

- Compare replay market-event readiness by parsed bin semantics, with raw-label
  fallback only for unparsed legacy labels.
- Add `closed` to the packet scope schema status enum.
- Replace broad `TypeError` fallback in the Code Review Graph context-pack path
  with signature-based feature detection.
- Tighten the bare `test` digest-profile regression assertion.
- Clarify that the default `pytest` command intentionally excludes
  `live_topology`, and full-suite automation must override the marker filter.
- Register this packet and update the live control pointer.

Out of scope:

- Untracking or sentinel-resetting `state/daemon-heartbeat.json`.
- Production DB mutation, P4 population, source-role decisions, or launch
  environment repair.
- Reworking packet runtime lifecycle names beyond accepting the emitted
  `closed` status.

## Verification

- `python3 -m py_compile src/engine/replay.py scripts/topology_doctor_code_review_graph.py tests/test_run_replay_cli.py tests/test_topology_doctor.py tests/test_digest_profile_matching.py tests/test_zpkt.py`
- `pytest -q tests/test_run_replay_cli.py -k "market_events or semantic"`
- `pytest -q tests/test_topology_doctor.py -k "code_impact_graph"`
- `pytest -q tests/test_digest_profile_matching.py -k "generic_test_word"`
- `pytest -q tests/test_zpkt.py -k "scope_schema"`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <changed files> --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_pr17_review_fixes/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <changed files>`
- `python3 scripts/topology_doctor.py --change-receipts --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_pr17_review_fixes/receipt.json`
- `git diff --check -- <changed files>`

## Closeout

- PR #17 review threads for replay label semantics, packet scope status,
  Code Review Graph `TypeError` masking, digest-profile assertion strength, and
  `pytest.ini` live-topology guidance are addressed.
- The volatile heartbeat review remains a future governance decision because
  this repo currently tracks runtime projections and the operator has asked for
  those runtime snapshots to be preserved separately.
