# P0 Market Events Preflight Work Log

Date: 2026-04-25
Branch: `midstream_remediation`
Status: implementation verified; ready for critic closeout and commit.

Task: POST_AUDIT_HANDOFF 4.2.C market-events empty-table preflight for replay
consumers.

Changed files:

- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-25_p0_market_events_preflight/plan.md`
- `docs/operations/task_2026-04-25_p0_market_events_preflight/work_log.md`
- `docs/operations/task_2026-04-25_p0_market_events_preflight/receipt.json`
- `architecture/docs_registry.yaml`
- `src/engine/replay.py`
- `scripts/run_replay.py`
- `tests/test_run_replay_cli.py`

Summary:
- Commit `8e94f4a` created and pushed a docs-only 4.2.C planning packet after
  4.2.B commit `3e1bda7`.
- Open the implementation slice at the actual replay seam:
  `src/engine/replay.py`.
- Keep implementation scoped to replay-first fail-closed preflight plus CLI
  surfacing and focused antibodies, without P1/P3/P4 drift or executor-local
  DB authority.
- Implemented a strict replay/WU-sweep preflight that requires matching
  `market_events.range_label` coverage for every replayed `(city, target_date)`
  settlement subject unless the effective replay context is in diagnostic
  snapshot-only fallback.
- Added CLI surfacing for `ReplayPreflightError` with exit code 1.
- Updated focused replay CLI antibodies for strict empty-market blocking,
  unrelated-market false-pass protection, WU-sweep coverage, seeded strict
  replay, explicit fallback, and current settlement fixture metric law.

Verification:
- Planning and topology gates are rerun after moving the packet from planning
  to implementation-active.
- Source-code gates are required before closeout.
- `.venv/bin/python -m py_compile src/engine/replay.py scripts/run_replay.py`
  -> passed.
- `.venv/bin/python -m pytest -q tests/test_run_replay_cli.py`
  -> `17 passed`.
- `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py tests/test_run_replay_cli.py`
  -> `89 passed, 22 skipped`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <4.2.C implementation files> --plan-evidence docs/operations/task_2026-04-25_p0_market_events_preflight/plan.md --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --work-record --changed-files <4.2.C implementation files> --work-record-path docs/operations/task_2026-04-25_p0_market_events_preflight/work_log.md --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <4.2.C implementation files> --receipt-path docs/operations/task_2026-04-25_p0_market_events_preflight/receipt.json --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --receipt-path docs/operations/task_2026-04-25_p0_market_events_preflight/receipt.json --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <4.2.C implementation files> --json`
  -> `{"ok": true, "issues": []}`.
- `git diff --check -- <4.2.C implementation files>`
  -> passed.
- Critic review passed after confirming P0 scope, historical P1.5 anchor
  posture, replay-first boundary, and no executor-local DB authority.
- Topology planning-lock, work-record, change-receipts, map-maintenance, and
  current-state receipt binding returned ok.

Next:
- Run critic review on this docs-only 4.2.C plan.
- Commit and push only receipt-bound planning files after gates pass.

## Phase Entry

Context rebuilt:

- Reread `AGENTS.md`.
- Reread `docs/operations/current_state.md`.
- Reread POST_AUDIT_HANDOFF 4.2.C and the forensic apply-order file.
- Read scoped `src/AGENTS.md`, `src/engine/AGENTS.md`,
  `src/execution/AGENTS.md`, and `tests/AGENTS.md`.
- Ran topology navigation for candidate 4.2.C planning files. Initial result
  was not merge-ready because new task files were not registered yet and
  `current_state.md` no longer contained the required historical P1.5 topology
  anchor.
- Scout mapped current code facts:
  - real replay file is `src/engine/replay.py`;
  - replay currently reads legacy `market_events`;
  - `market_events_v2` is checked in readiness scripts but is not replay's
    active input;
  - `executor.py` does not currently own a world-data DB seam.
- Architect recommended closing 4.2.B first and creating a separate docs-only
  4.2.C planning packet before code changes.

## Planning Decision

This packet keeps 4.2.C in P0 and corrects the next-code boundary:

- replay preflight first;
- no P1 provenance work;
- no P3 safe-view migration;
- no P4 market-event population;
- no executor-local DB reads without a separate explicit scope expansion.

## Verification Plan

- Topology navigation after packet registration.
- Planning-lock with this plan as evidence.
- Work-record gate for the changed file set.
- Change-receipts gate using this packet receipt.
- Current-state receipt binding.
- Map-maintenance precommit gate.
- `git diff --check` for the planning files.

## Final Review

Critic verdict: PASS.

Evidence:

- 4.2.C remains active P0 planning after 4.2.B closeout at `3e1bda7`.
- P1.5 remains a historical topology anchor only, not active work.
- The real replay seam is `src/engine/replay.py`, where current replay reads
  legacy `market_events`.
- The plan excludes executor-local DB authority and P1/P3/P4 implementation.
- Topology gates for planning-lock, work-record, change-receipts,
  current-state receipt binding, and map-maintenance returned ok.

Closeout verification:

- `python3 scripts/topology_doctor.py --navigation --task "P0 4.2.C market-events empty-table preflight planning packet" --files <4.2.C planning files>`
  -> navigation ok.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <4.2.C planning files> --plan-evidence docs/operations/task_2026-04-25_p0_market_events_preflight/plan.md --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --work-record --changed-files <4.2.C planning files> --work-record-path docs/operations/task_2026-04-25_p0_market_events_preflight/work_log.md --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <4.2.C planning files> --receipt-path docs/operations/task_2026-04-25_p0_market_events_preflight/receipt.json --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <4.2.C planning files> --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --receipt-path docs/operations/task_2026-04-25_p0_market_events_preflight/receipt.json --json`
  -> `{"ok": true, "issues": []}`.
- `git diff --check -- <4.2.C planning files>`
  -> passed.
- `python3 -m json.tool docs/operations/task_2026-04-25_p0_market_events_preflight/receipt.json`
  -> passed.

## Next Steps

- Run planning-lock with this implementation packet as evidence.
- Implement replay preflight and CLI blocker surfacing.
- Add focused replay CLI antibodies and freshness headers.
- Run critic/verifier review, topology closeout, commit, and push.

Implementation notes:

- The preflight checks the legacy `market_events` surface because replay
  currently consumes legacy `market_events`, not `market_events_v2`.
- The check is subject-specific, not a global row count: an unrelated
  market-event row does not satisfy a Paris replay subject.
- Critic found a same-city/date wrong-label false pass in the first
  implementation. The fix now requires exact `(city, target_date,
  winning_bin)` coverage when `winning_bin` is present, and at least
  `(city, target_date)` market-event coverage when legacy settlement rows lack
  `winning_bin`. A wrong-label WU sweep antibody was added.
- Critic re-review verdict: PASS. The prior false-pass repro now fails closed
  with `missing=Paris:2026-04-03:12°C`; no P1/P3/P4 or executor drift found.
- The bypass uses `ReplayContext.allow_snapshot_only_reference`, preserving
  the current non-audit/fallback behavior without redefining replay modes.
- The test file had stale fixtures after the post-audit settlement metric law
  made `settlements.temperature_metric` non-null; this packet updated only the
  reused fixtures in `tests/test_run_replay_cli.py`.
- The old unpriced-PnL test also asserted a removed `kelly_size` call and a
  `replay_results` table absent from current schema. Those stale assertions
  were narrowed back to the active contract: no market-price linkage means no
  replay trade and no PnL promotion.

## Implementation Reentry

Fresh phase-entry after planning commit:

- Reread `AGENTS.md`, `docs/operations/current_state.md`, and this packet.
- Reread scoped `src/engine/AGENTS.md`, `scripts/AGENTS.md`, and
  `tests/AGENTS.md`.
- Reran implementation topology navigation, which correctly required packet
  scope expansion before source edits because the prior receipt was docs-only.
- Updated the receipt/control surfaces to authorize the replay-first
  implementation file set.
