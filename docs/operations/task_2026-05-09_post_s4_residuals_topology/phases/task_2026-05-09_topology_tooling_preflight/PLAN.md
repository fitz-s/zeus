# Topology Tooling Preflight Deep Plan

Created: 2026-05-09
Last reused/audited: 2026-05-09
Authority basis: parent packet `docs/operations/task_2026-05-09_post_s4_residuals_topology/PLAN.md`; S1-S4 topology friction evidence; `docs/operations/AGENTS.md` phase-packet rule.

## Mission

Make the next implementation packet safer by turning topology friction into executable tooling/checklist structure.

This phase is tooling/docs only. It must not change Zeus runtime behavior, trading behavior, calibration promotion, source truth, settlement truth, production DBs, or live daemon state.

## Structural Decision

Topology should not merely say “admitted/advisory.” It should help the operator and agent answer:

1. Which profile was intended?
2. Which profile was actually selected?
3. Which file or phrase caused the mismatch?
4. Which companion files/checks are mandatory before PR?
5. Which high-fanout route card should be used for files that appear in multiple semantic surfaces?

## Candidate Deliverables

| Deliverable | Purpose | Candidate files | Risk |
|---|---|---|---|
| Preflight checklist command or helper | Emit intended files, selected profile, risk tier, companion files, and stop conditions. | `scripts/topology_doctor.py`, `scripts/topology_doctor_cli.py`, `scripts/topology_doctor_digest.py`, tests under `tests/test_topology_doctor.py` | T2/T3 tooling |
| Advisory explanation detail | For advisory-only route, show closest admitted profile, rejected files, missing/weak phrase signal. | `scripts/topology_doctor.py`, `scripts/topology_doctor_digest.py`, `architecture/topology.yaml`, route-card tests | T2/T3 tooling |
| New-test companion guard surfaced in route card | Make `architecture/test_topology.yaml` registration visible before PR review. | topology doctor, `scripts/topology_doctor_map_maintenance.py`, map-maintenance tests | T2 tooling |
| Generated high-fanout route hints | Generate candidate semantic routes, reasons, and required companion/proof from current topology metadata and dry-run fixtures. | `scripts/topology_doctor_digest.py`, topology metadata, focused tests | T2 tooling |
| Post-merge cleanup recipe | Standardize branch/worktree cleanup after merged PRs. | deferred docs/tooling lane, not first implementation slice | T1 docs |

## Relationship Tests First

Before implementation, write tests or fixture cases for these relationships:

1. Natural task wording plus exact file set routes to the intended profile, not `generic`.
2. Advisory route output identifies the closest admitted profile and the file/phrase reason for non-admission.
3. Adding a new `tests/test_*.py` without `architecture/test_topology.yaml` returns a companion warning in the preflight output, not only in a later PR comment.
4. High-fanout files produce generated semantic route hints instead of a single ambiguous “high-fanout” warning, and those hints agree with actual admission behavior.
5. Docs-only `task_*/PLAN.md` routes as `operation planning packet` with T1 risk.
6. Preflight output is compact: one primary blocker, top 1-3 route candidates, mandatory companion files, and no repeated explanatory prose.

## Preflight Needed Before Editing

1. Run topology navigation on candidate tooling files.
2. Read current `scripts/topology_doctor.py`, `scripts/topology_doctor_cli.py`, and `scripts/topology_doctor_digest.py` route-card/digest structures.
3. Read `scripts/topology_doctor_map_maintenance.py` before implementing companion warnings.
4. Read existing `tests/test_topology_doctor.py` map-maintenance and navigation tests.
5. Decide whether the first implementation is code or docs-only route-card guidance.
6. Name exact editable files and forbidden files before any source edit.

## Proposed Narrow First Slice

Preferred first slice: add a topology preflight output path that reports companion files for new tests and produces an explanatory advisory hint.

Candidate allowed files after topology admission:

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_digest.py`
- `scripts/topology_doctor_map_maintenance.py` only if companion warnings are implemented from existing map-maintenance logic
- `tests/test_topology_doctor.py`
- Optional docs/architecture metadata only if the route-card model needs declarative high-fanout hints.

Forbidden in this phase:

- `src/**` runtime/business logic
- `config/**` live settings
- `state/**` and production DBs
- calibration/source/settlement implementation files
- branch/worktree cleanup scripts that delete user data without explicit confirmation

## Acceptance Criteria

- Focused topology tests pass.
- Existing S1-S4 style packet examples can be checked with one preflight command and receive actionable route/companion guidance.
- The preflight first screen has a stable output contract: selected profile, admission status, one primary blocker if any, top 1-3 route candidates, mandatory companion files, and stop conditions.
- New-test companion requirements are surfaced before PR open.
- Generated high-fanout hints are tested against actual route/admission behavior rather than hand-maintained as a separate catalog.
- Docs-only packet routes remain admitted as `operation planning packet`.
- No runtime or money-path behavior changes are present in the diff.

## Stop Conditions

- Topology navigation for candidate tooling files is advisory-only after scope narrowing.
- The first slice requires changing runtime source files.
- The route-card output becomes broad/noisy enough that it would hide the actual blocker.
- Existing topology tests reveal unrelated failures not caused by this packet.