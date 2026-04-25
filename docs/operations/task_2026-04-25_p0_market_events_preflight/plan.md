# P0 Market Events Preflight Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Status: implementation-active packet for POST_AUDIT_HANDOFF 4.2.C.

## Task

Close POST_AUDIT_HANDOFF 4.2.C by adding a replay-first market-events preflight
before replay can produce diagnostic outcomes from absent market identity.

4.2.C is a P0 containment slice. It must fail closed when market-event truth
needed for replay is absent, without drifting into P1 provenance hardening, P3
safe-view migration, or P4 data population.

## Phase Entry Context

Completed before this implementation packet:

- Reread root `AGENTS.md`.
- Reread `docs/operations/current_state.md` and the POST_AUDIT handoff.
- Reread forensic apply order:
  `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/17_apply_order.md`.
- Read scoped guidance for `src/`, `src/engine/`, `src/execution/`, and tests.
- Ran topology navigation for 4.2.C candidate planning files. It surfaced two
  planning facts:
  - new task files must be registered before topology can classify them;
  - `docs/operations/current_state.md` must retain the historical P1.5
    topology anchor even though P1.5 is not active.
- Scout mapped the actual replay path to `src/engine/replay.py`; the handoff's
  replay-path wording is stale.
- Architect reviewed the seam and recommended a docs-only planning commit
  before implementation because 4.2.C mixes replay, market-event source
  identity, and a live-money executor seam.
- Commit `8e94f4a` closed and pushed the docs-only planning/control packet.
- Reread root `AGENTS.md`, `docs/operations/current_state.md`, this plan,
  `src/engine/AGENTS.md`, `scripts/AGENTS.md`, and `tests/AGENTS.md` before
  opening implementation.

## RALPLAN-DR Summary

Principles:

- Keep 4.2.C in P0 containment; do not implement P1/P3/P4 work inside it.
- Fail closed before replay produces diagnostic claims from missing market
  identity.
- Respect execution boundaries: do not make `executor.py` open world DBs unless
  a later packet explicitly widens scope.
- Prefer existing blocker semantics such as `no_market_events ->
  no_active_market` over inventing new status grammar.
- Keep planning and implementation separable so code work starts with a clean
  receipt and fresh topology gate.

Decision drivers:

- The handoff names a stale replay path, but the real file is
  `src/engine/replay.py`.
- Current replay reads legacy `market_events`, not `market_events_v2`.
- `src/execution/executor.py` is a live CLOB actuation boundary and currently
  has no world-data authority seam.

Viable options:

- Option A: replay-core preflight only in `src/engine/replay.py`.
  - Pros: smallest code boundary, directly blocks the real consumer.
  - Cons: does not solve live no-market actuation posture.
- Option B: shared preflight/report helper plus replay-core enforcement.
  - Pros: reuses existing fail-closed reporting style and creates one semantic
    surface for replay readiness.
  - Cons: touches both `src/engine` and `scripts`, so gates are wider.
- Option C: direct executor DB preflight.
  - Pros: literal reading of the handoff's live-path sentence.
  - Cons: introduces DB authority into a live-money order executor that does
    not currently own market-event discovery; rejected for 4.2.C unless later
    evidence proves the upstream active-market gate is insufficient.

Chosen planning direction: Option B for implementation planning, with
`executor.py` excluded unless a later packet deliberately adds an upstream
actuation gate instead of an executor-local DB read.

## Scope

Allowed implementation/control files:

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

Deferred unless a later packet explicitly widens scope:

- `architecture/topology.yaml`
- `scripts/audit_city_data_readiness.py`
- `tests/test_audit_city_data_readiness.py`
- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`
- `src/execution/executor.py`
- `tests/test_executor.py`

Forbidden for this implementation packet:

- production DB mutation or generated runtime JSON
- market-event backfill or v2 population
- safe-view-only consumer migration
- provenance/source-role/eligibility hardening
- settlement identity repair
- live executor DB reads or actuation-boundary changes

## Implementation Plan

1. Add a replay-readiness preflight before replay starts producing diagnostic
   outcomes. It should detect an empty market-event surface and fail closed with
   an explicit blocker such as `no_market_events`.
2. Prefer checking the surface actually consumed by replay today:
   `market_events`. Treat `market_events_v2` as a training-readiness signal,
   not a replay input, unless the code is first migrated to consume it.
3. Preserve existing snapshot-only diagnostic behavior only when the caller has
   explicitly opted into a diagnostic fallback and the replay output carries
   limitations that make the missing market linkage obvious.
4. Reuse existing `audit_city_data_readiness.py` semantics if a shared blocker
   surface is needed: `no_market_events -> no_active_market`.
5. Keep `executor.py` out of the initial code diff. If live no-market gating is
   still required, plan an upstream evaluator/cycle active-market gate rather
   than adding world-DB reads to order placement.
6. Add focused antibodies that prove:
   - empty `market_events` blocks strict replay;
   - seeded `market_events` allows replay to proceed to the existing decision
     logic;
   - CLI output surfaces the blocker cleanly;
   - existing city readiness `no_market_events` behavior remains pinned.

## Verification Plan For Implementation

- `.venv/bin/python -m py_compile src/engine/replay.py scripts/run_replay.py`
- `.venv/bin/python -m pytest -q tests/test_run_replay_cli.py`
- If shared blocker scripts are touched:
  `.venv/bin/python -m pytest -q tests/test_audit_city_data_readiness.py tests/test_truth_surface_health.py`
- If execution is explicitly widened:
  `.venv/bin/python -m pytest -q tests/test_executor.py tests/test_exit_authority.py`
- Engine/source rationale gate:
  `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py tests/test_run_replay_cli.py`
- Cross-module smoke if replay/live seam widens:
  `.venv/bin/python -m pytest -q tests/test_cross_module_invariants.py tests/test_cross_module_relationships.py tests/test_bug100_k1_k2_structural.py`
- Topology:
  `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-25_p0_market_events_preflight/plan.md --json`
- Packet closeout:
  work-record, change-receipts, current-state-receipt-bound,
  map-maintenance, freshness checks, and `git diff --check`.

## Acceptance

- 4.2.B is recorded closed at commit `3e1bda7`.
- 4.2.C is the active P0 implementation packet.
- P1.5 remains a historical topology anchor in `current_state.md`, not active
  work.
- The implementation packet has a bounded replay-first plan and does not
  authorize executor-local DB authority.
