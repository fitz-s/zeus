# 05 — Codex Execution Strategy

This is the handoff strategy for Codex or another implementation agent. It is deliberately topology-first and phase-scoped.

## Core Codex rules

1. Do not start from generic refactor.
2. Start from topology doctor.
3. Do not widen phase scope.
4. Do not modify live/prod/config/source-routing outside explicit phase authorization.
5. Do not create a new highest authority file.
6. Every phase must include tests or a reason why it is authority-only.
7. Every high-impact judgment must include the verification loop:
   - Did we preserve the target?
   - Did we miss authority conflict?
   - Did we present unknown as known?

## Initial commands

Run from repo root:

```bash
python3 scripts/topology_doctor.py --navigation \
  --task "pricing semantics authority cutover: physically isolate epistemic probability, microstructure CLOB facts, and execution/risk economics" \
  --files AGENTS.md architecture/invariants.yaml architecture/negative_constraints.yaml docs/reference/zeus_math_spec.md src/strategy/market_fusion.py src/engine/evaluator.py src/engine/cycle_runtime.py src/execution/executor.py src/engine/monitor_refresh.py

python3 scripts/topology_doctor.py --task-boot-profiles

python3 scripts/topology_doctor.py context-pack \
  --task "pricing semantics authority cutover" \
  --files AGENTS.md architecture/invariants.yaml architecture/negative_constraints.yaml docs/reference/zeus_math_spec.md src/strategy/market_fusion.py src/engine/evaluator.py src/engine/cycle_runtime.py src/execution/executor.py src/engine/monitor_refresh.py
```

If topology says different scoped files/tests/gates, obey topology. This package is strategy, not a file-touch permission grant.

## Planning lock

This work touches architecture, governance, runtime, state, execution, DB semantics, and cross-zone logic. Codex must use planning lock before source changes:

```bash
python3 scripts/topology_doctor.py --planning-lock \
  --changed-files <candidate changed files> \
  --plan-evidence <packet plan/work_log path>
```

## Phase execution protocol

Each phase must follow:

```text
1. Read scoped AGENTS.md.
2. Read topology returned required_law.
3. List allowed files and forbidden files.
4. State phase-specific goal in one paragraph.
5. Modify only that phase's allowed files.
6. Run focused tests.
7. Run architecture/map-maintenance if files added/renamed.
8. Write closeout: changed files, tests, unresolved blockers, next phase.
```

## Stop conditions

Stop and re-plan if:

- topology returns forbidden file needed for phase.
- more than four source zones are needed in a phase not planned as cross-zone.
- any live/prod side effect is required.
- any schema migration becomes non-additive.
- any old row needs relabeling to corrected semantics.
- executor cannot be made no-recompute without broad rewrite.
- snapshot producer cannot be found or built without source-routing changes.
- reports require mixing semantics to keep green.
- Codex wants to implement `yes_family_devig_v1_live`, negative-risk arbitrage, queue model, or post-only policy in the first packet.

## Recommended phase slicing for Codex

### Packet 1 — Authority + failing tests

Includes Phase 0 and Phase A only.

Deliverables:

- Authority diffs.
- Negative constraints.
- Invariant tests expected to fail against old code or pass if implemented as guard stubs.
- No behavior rewrite.

### Packet 2 — Contracts + import fences

Includes Phase B only.

Deliverables:

- Typed contracts.
- Import-fence tests.
- No live runtime change.

### Packet 3 — Microstructure snapshot/cost basis

Includes Phase C.

Deliverables:

- Snapshot producer or canonical owner.
- CLOB sweep.
- Cost basis builder.
- Tests for fee/tick/min-order/depth/freshness.

### Packet 4 — Epistemic posterior split

Includes Phase D.

Deliverables:

- `compute_posterior(MarketPriorDistribution | None)`.
- `model_only_v1` baseline.
- `legacy_vwmp_prior_v0` explicit legacy.
- Raw quote prior forbidden.

### Packet 5 — Executable hypothesis + FDR

Includes Phase E and Phase F.

Deliverables:

- Executable hypothesis family.
- Live economic edge.
- FDR identity includes token/snapshot/cost/order policy.
- Late reprice invalidation tests.

### Packet 6 — Runtime and executor

Includes Phase G and Phase H.

Deliverables:

- Snapshot/cost before FDR.
- No late reprice in corrected mode.
- FinalExecutionIntent.
- Executor no-recompute.
- Venue envelope identity.

### Packet 7 — Monitor/exit + persistence/reporting

Includes Phase I and Phase J.

Deliverables:

- Held-token exit quote basis.
- Partial fill exposure updates.
- Additive fields.
- Mixed-cohort report hard-fail.

### Packet 8 — Shadow evidence only

Includes Phase K/L.

Deliverables:

- Shadow run summary.
- No live submit.
- No strategy promotion.
- List remaining source/calibration/risk blockers.

## Exact Codex prompt pattern

Use this structure for each phase:

```text
You are working in fitz-s/zeus branch plan-pre5.
Task: <phase name>.
Read and obey root AGENTS.md and all topology-doctor output.
Do not make live/prod/config/source-routing changes.
Do not create a new highest authority.
Goal: <one paragraph>.
Required files: <from topology>.
Forbidden: <from topology>.
Must preserve: three-layer physical isolation.
Must not: let raw price-like scalar cross live-money boundary.
Tests to add/run: <phase tests>.
Closeout must include: changed files, tests, failures, unresolved uncertainty, next phase.
```

## Codex verification loop template

At the end of every phase, Codex must answer:

```text
1. Did we preserve the user's true target?
2. Did we accidentally replace the user's framework with a generic workflow?
3. Did we introduce a new authority surface?
4. Did any raw quote/VWMP/midpoint enter posterior or Kelly?
5. Did any executor path recompute final price?
6. Did any report mix legacy/corrected economics?
7. Which live-readiness blockers remain unrelated to this phase?
```
