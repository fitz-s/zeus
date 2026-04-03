File: docs/governance/zeus_first_phase_execution_plan.md
Disposition: NEW
Authority basis: docs/governance/zeus_autonomous_delivery_constitution.md; docs/governance/zeus_foundation_package_map.md; docs/governance/zeus_top_tier_decision_register.md.
Supersedes / harmonizes: prior-session sequence hints and broad live-plan narratives.
Why this file exists now: Zeus needs a first-week, first-phase plan that is executable without pretending the end state already exists.
Current-phase or long-lived: Current-phase only.

# Zeus First-Phase Execution Plan

## Goal
Install authority, instruction loading, boundaries, and machine gates without falsely claiming full canonical runtime convergence.

## Phase 0 — bootstrap (Day 0)
- create branch for authority install
- snapshot current repo state
- read root authority files
- generate initial delta ledger
- confirm missing external workspace files are treated as unknown, not inferred

Deliverables:
- working branch
- runtime delta ledger started
- operator acknowledges current-vs-target split

## Phase 1 — authority installation (Day 1)
- install delivery constitution
- install root/scoped AGENTS
- install decision register
- install package map

Acceptance:
- root `AGENTS.md` exists
- scoped AGENTS exist in high-value directories
- `.claude/CLAUDE.md` no longer acts as primary authority in the proposed overlay

## Phase 2 — enforcement installation (Day 2)
- install boundary note
- install cookbook and runbook
- wire or patch authority/gate scripts as needed
- mark replay parity as advisory
- document missing foundation files that block “full foundation completeness” claims

Acceptance:
- operator can start and stop OMX/OMC using repo-local runbook
- gate severity is explicit
- repo/host boundary is no longer implicit

## Phase 3 — controlled cleanup (Day 3)
- patch highest-risk semantic drifts:
  - `src/state/strategy_tracker.py`
  - `src/data/observation_client.py`
- do not attempt full event/projection cutover
- add or update targeted tests as needed

Acceptance:
- no default strategy bucket fallback
- no implicit `date.today()` in authority-sensitive observation context
- no broadened schema/control scope

## Phase 4 — operator demotion and archive prep (Day 4)
- patch or stage demotion banners for historical docs
- patch `WORKSPACE_MAP.md` into orientation-only role
- install `.claude/CLAUDE.md` shim
- install archive/cutover plan

Acceptance:
- no current doc still claims “highest authority” without explicit demotion note
- old docs remain readable as rationale/history

## Phase 5 — dry-run and recovery rehearsal (Day 5)
- rehearse OMX primary path on one low-risk packet
- rehearse OMC secondary review lane
- rehearse team shutdown and state inspection
- verify git cleanliness and artifact handling

Acceptance:
- operators can start, stop, resume, and recover without ambiguity
- runtime state directories are treated as operator surfaces, not law

## Phase 6 — steady-state activation (Day 6–7)
- turn root/scoped AGENTS into daily default
- require planning lock for K0/K1/schema/control work
- require decision register updates for new irreversible choices
- keep replay parity advisory until canonical cutover packet exists

Acceptance:
- daily Zeus work can proceed under the new delivery constitution
- remaining open items are documented rather than hidden

## Non-goals for first phase
- full `position_current` rollout
- full historical state migration
- live cutover
- permanent `.claude` retirement
- outer-host automation expansion

## Blocking vs non-blocking remainder

### Blocking before live cutover
- canonical projection landing
- parity/replay proof
- human-approved cutover timing
- state mismatch explanation

### Non-blocking for authority install
- extra scoped AGENTS in low-value directories
- advanced hook automation
- wider team compositions
