# FOUNDATION-MAINLINE-PLAN

```yaml
work_packet_id: FOUNDATION-MAINLINE-PLAN
packet_type: feature_packet
objective: Freeze the post-current-phase architecture mainline by extracting the stage map, workstreams, automation strategy, verification path, and team-opening gate from the tribunal overlay and mature foundation source package.
why_this_now: The current-phase `P-*` queue is closed. Without a frozen mainline plan, the next stage would start from memory and momentum rather than explicit authority.
why_not_other_approach:
  - Start team execution immediately | team lanes would open without a frozen mainline sequence or staffing gate
  - Drive foundation work from chat summaries only | stage boundaries, automation plan, and completion targets would drift
truth_layer: The next-stage stage map and goals come from `zeus_final_tribunal_overlay` for current-phase closure logic and `zeus_mature_project_foundation` for the durable architecture mainline.
control_layer: This packet plans the next phase only; it does not yet mutate runtime, schema, or authority law outside the planning artifact it freezes.
evidence_layer: Stage map, completion ladder, source-package crosswalk, team-opening gate, and rollback note.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - zeus_final_tribunal_overlay/ZEUS_FINAL_TRIBUNAL.md
  - zeus_final_tribunal_overlay/AGENTS.md
  - zeus_final_tribunal_overlay/docs/governance/zeus_first_phase_execution_plan.md
  - zeus_final_tribunal_overlay/docs/governance/zeus_foundation_package_map.md
  - zeus_mature_project_foundation/architecture/self_check/authority_index.md
  - zeus_mature_project_foundation/docs/architecture/zeus_durable_architecture_spec.md
  - zeus_mature_project_foundation/docs/governance/zeus_change_control_constitution.md
  - zeus_mature_project_foundation/architecture/maturity_model.yaml
files_may_change:
  - work_packets/FOUNDATION-MAINLINE-PLAN.md
  - architects_progress.md
  - architects_task.md
files_may_not_change:
  - src/**
  - migrations/**
  - architecture/**
  - docs/governance/**
  - docs/architecture/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - planning review only
parity_required: false
replay_required: false
rollback: Revert the planning packet file and paired Architects ledger updates together.
acceptance:
  - The next-stage task phases are explicitly named and ordered.
  - Stage goals and completion targets are mapped back to the tribunal overlay and mature foundation source package.
  - The team-opening gate is explicit and remains blocked until planning is complete.
  - If team staffing is not frozen here, the required successor packet is named explicitly.
  - The current-phase `P-*` queue remains closed and is not silently reopened.
evidence_required:
  - stage map
  - source-package crosswalk
  - completion ladder
  - team-opening gate note
  - rollback note
```

## Notes

- If planning reveals a contradiction between the tribunal overlay and the mature foundation package, record the contradiction explicitly instead of papering it over.
- If any future execution request is unclear, return to:
  - `zeus_final_tribunal_overlay`
  - `zeus_mature_project_foundation`

## Stage Map

### Stage 0 — Current-phase closure
- Status: complete
- Source basis:
  - `zeus_final_tribunal_overlay/docs/governance/zeus_first_phase_execution_plan.md`
  - `zeus_final_tribunal_overlay/docs/governance/zeus_foundation_package_map.md`
- Exit condition:
  - `P-BOUND-01`, `P-ROLL-01`, `P-STATE-01`, `P-OPS-01` all closed

### Stage 1 — Bearing-capacity completion
- Source basis:
  - `zeus_mature_project_foundation/docs/architecture/zeus_durable_architecture_spec.md` P0
- Work order:
  1. P0.2 attribution freeze
  2. P0.1 exit semantics split
  3. P0.3 canonical transaction boundary
  4. P0.4 data availability truth
  5. P0.5 implementation operating system completion
- Goal:
  - finish the remaining bearing-capacity work needed before broad canonical-authority rollout

### Stage 2 — Canonical authority rollout
- Source basis:
  - foundation spec P1
- Work order:
  1. add schema
  2. add append/project API
  3. dual-write in cycle runner/harvester/reconciliation
  4. projection parity tests
- Goal:
  - establish canonical lifecycle authority

### Stage 3 — Execution + protection mainline
- Source basis:
  - foundation spec P2 + P3
- Work order:
  - P2 execution truth / exit lifecycle
  - P3 strategy-aware protective spine
- Goal:
  - make runtime semantics and protection behavior architecture-correct

### Stage 4 — Learning + phase-engine mainline
- Source basis:
  - foundation spec P4 + P5
- Work order:
  - learning facts
  - availability truth
  - lifecycle phase engine hardening
- Goal:
  - preserve point-in-time truth and authoritative phase behavior

### Stage 5 — Operator/control compression + migration
- Source basis:
  - foundation spec P6 + P7
- Work order:
  - operator/control/observability compression
  - migration phases M0-M4
- Goal:
  - move from governed runtime toward mature project state

## Source-package crosswalk

| Need | Tribunal overlay source | Mature foundation source |
|---|---|---|
| current-phase closure logic | `ZEUS_FINAL_TRIBUNAL.md`, `docs/governance/zeus_first_phase_execution_plan.md`, `docs/governance/zeus_foundation_package_map.md` | n/a |
| durable architecture sequence | n/a | `docs/architecture/zeus_durable_architecture_spec.md` |
| machine-governed end-state target | tribunal summary only | `architecture/maturity_model.yaml` |
| operator/runtime posture | `docs/governance/zeus_omx_omc_*`, tribunal summary | foundation spec P8 / authority stack |
| authority fallback when unclear | `zeus_final_tribunal_overlay/AGENTS.md` | `architecture/self_check/authority_index.md` |

## Mainline Workstreams

### Workstream A — Authority-bearing kernel completion
- attribution grammar freeze
- canonical transaction boundary
- canonical event/projection model

### Workstream B — Runtime semantics correction
- exit intent model
- pending exit handling
- economic close vs settlement separation

### Workstream C — Strategy-aware protection
- policy tables
- resolver
- evaluator/riskguard integration

### Workstream D — Learning truth
- opportunity / availability / execution / outcome facts
- analytics smoke queries

### Workstream E — Migration / parity / deletion
- dual-write
- parity
- DB-first reads
- cutover
- delete/demote shadow surfaces

## Automation Path

1. Freeze one bounded packet at a time from the stage map.
2. Prefer single-owner execution while work remains architecture- or law-heavy.
3. Keep packet grammar, architecture gates, and targeted regression checks active for every slice.
4. Promote automation only where the source package sequence already stabilizes semantics.
5. Open team execution only after the explicit team-opening gate below is satisfied.

## Verification Path

### Every packet
- `python3 scripts/check_work_packets.py`
- packet-scoped tests / evidence
- rollback note
- unresolved uncertainty note

### Architecture-bearing packets
- architecture contracts / manifest checks
- module-boundary checks
- replay/parity evidence or explicit staged waiver

### Stage exits
- Stage 1 exit must satisfy the maturity-model promotion inputs needed for `governed_runtime`
- Stage 5 exit must satisfy migration and parity conditions needed for `mature_project`

## Explicit Team-Opening Gate

Team execution remains blocked until all of the following are true:

1. `FOUNDATION-MAINLINE-PLAN` is executed and approved.
2. A team-opening packet is frozen after this plan.
3. That team-opening packet defines:
   - staffing map
   - lane ownership
   - write boundaries
   - verification path
   - shutdown / rollback / cleanup path
4. The first team-eligible work is selected from a bounded, non-contradictory slice.

## Staffing Decision

Team staffing is **not** frozen in this packet.

Required successor packet:
- `FOUNDATION-TEAM-GATE`

That packet must freeze:
- staffing
- lane model
- ownership boundaries
- acceptance / verification path
- when `$team` becomes allowed

## Completion Targets

- `foundation-planned`:
  - this packet executed and approved
  - stage map / workstreams / automation path / verification path explicit
- `team-ready`:
  - `FOUNDATION-TEAM-GATE` executed and approved
- `governed_runtime`:
  - maturity-model criteria for governed runtime satisfied
- `mature_project`:
  - maturity-model criteria for mature project satisfied
