# Midstream Remediation Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: runtime / test-currency / typed-contract
Phase: W0 packet open; W1 executing

## Objective

Execute the 36-slice joint midstream implementation plan produced by
the 2026-04-23 pro/con Opus debate (con-nyx × pro-vega). Deliver
midstream CONDITIONAL status at end of W4 (T1+T2+T3+T4.2-Phase1+T5
green) and TRUSTWORTHY at end of W5 (T6+T7+N1+T4.2-Phase2 close).

## Authority source

The slice catalog, acceptance criteria, sequencing, and pre-mortem live
in the joint fix plan workbook:

- `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`

This packet's plan.md intentionally does NOT duplicate that workbook.
Refer to the workbook for per-slice details.

## Out of scope

- Upstream data collection (`src/data/*`, `scripts/ingest/*`,
  launchd plists) — owned by a parallel upstream-repair agent.
- Downstream position management, storage, settlement reconciliation,
  PnL attribution — separate future packet.

## Wave 0 scope (packet open, this commit)

Allowed:
- `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/receipt.json`
- `docs/operations/current_state.md` (swap active program pointer)

Forbidden at W0:
- `src/**`, `state/**`, `config/**`, `architecture/**`, `tests/**`,
  `.code-review-graph/graph.db`, runtime DBs, other packet files,
  broad `docs/authority/**` rewrites.

## Wave 1 scope (T1.a + T1.b + T3.1 + T3.3 + T7.b + T4.0)

Allowed:
- `docs/operations/task_2026-04-23_midstream_remediation/**`
- `docs/operations/current_state.md`
- `docs/operations/known_gaps.md` (residual annotations only; slice-scoped edits)
- 15-file T1.a panel:
  - `tests/test_dual_track_law_stubs.py`
  - `tests/test_cross_module_invariants.py`
  - `tests/test_architecture_contracts.py`
  - `tests/test_live_safety_invariants.py`
  - `tests/test_provenance_enforcement.py`
  - `tests/test_kelly_cascade_bounds.py`
  - `tests/test_kelly_live_safety_cap.py`
  - `tests/test_execution_price.py`
  - `tests/test_ensemble_signal.py`
  - `tests/test_calibration_bins_canonical.py`
  - `tests/test_alpha_target_coherence.py`
  - `tests/test_fdr.py`
  - `tests/test_entry_exit_symmetry.py`
  - `tests/test_day0_exit_gate.py`
  - `tests/test_platt.py`
- T1.b audit artifacts:
  - `config/provenance_registry.yaml` (content audit + additions only;
    file already exists at 516 lines)
  - `tests/test_provenance_enforcement.py` (remove redundant skipif markers)
- T3.1 signature-drift caller patches (tests only):
  - `tests/test_phase10e_closeout.py`
  - `tests/test_day0_runtime_observation_context.py`
  - `tests/test_runtime_guards.py`
  - `tests/test_architecture_contracts.py` (already in T1.a panel)
- T3.3 schema bootstrap alignment (fixture-only; production DB already
  canonical per independent probe):
  - `src/state/ledger.py` (function `apply_architecture_kernel_schema()`
    at `:117`, bringing its column-set parity with
    `CANONICAL_POSITION_CURRENT_COLUMNS` at `:68`)
- T7.b new guard test:
  - `tests/test_no_deprecated_make_family_id_calls.py` (new)

Forbidden at W1:
- `src/**` except `src/state/ledger.py` specifically scoped by T3.3.
- `state/**` (runtime).
- `.code-require-graph/graph.db`.
- Other packets' files.
- `docs/authority/**`.
- `architecture/**` (W1.e-sequence `architecture/test_topology.yaml`
  is W2, not W1).
- py-clob-client / external SDK modification.

## Executor / critic protocol

- Executor: team-lead (this session).
- Long-lasting critic: `con-nyx` (Opus architect, durable team member).
- Every slice: executor prepares diff + runs targeted acceptance
  commands; critic re-runs full regression against pre-slice baseline
  (per memory rule L28) and reports delta-direction; on CLEAR,
  executor commits + attempts push; on REGRESSION, executor runs
  wide-review-before-push (L22) and fixes on top before re-dispatch.
- No autocommit without critic review.

## Upstream co-tenant coordination

A parallel upstream-repair agent is active on `data-improve`. Every
slice commit is preceded by `git pull --rebase origin data-improve`
and followed by an immediate push attempt. Shared-file discipline:
- `docs/operations/current_state.md`, `docs/operations/known_gaps.md`,
  `architecture/source_rationale.yaml`,
  `architecture/script_manifest.yaml` are touched only at slice
  boundaries, with fresh pull-rebase immediately before the edit.
- Overlapping semantic writes (e.g. both agents editing known_gaps.md)
  are resolved surgically; never use `git add -A` or `git add .`.

## Acceptance (packet-level)

Packet closes when the joint fix plan workbook lists all BLOCKER items
(B1–B5, G5–G9 of live-readiness are separate; this packet carries
T1+T2+T3+T4+T5 as BLOCKERs, T6+T7 as CONDITIONAL, N1 as NICE-TO-HAVE)
in "CLOSED" state with slice-scoped receipts, and
`docs/operations/known_gaps.md` is updated per the plan's closure path.

## Verification commands

Run after each slice and at each wave boundary:
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/plan.md --json`
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path docs/operations/task_2026-04-23_midstream_remediation/work_log.md --json`
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/topology_doctor.py --change-receipts --changed-files <files> --receipt-path docs/operations/task_2026-04-23_midstream_remediation/receipt.json --json`
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest -q tests/` (regression baseline + post-slice)

## Receipt binding

Closure evidence for each slice:
- row in `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
- entry in `docs/operations/task_2026-04-23_midstream_remediation/receipt.json`
- commit hash + critic-CLEAR attestation
