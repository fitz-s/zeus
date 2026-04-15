# Topology Context Efficiency Plan

Status: active evidence.

## Objective

Reduce topology-map context cost for debugging and refactoring by turning broad
navigation into query-driven outputs:

- targeted debug checks instead of full rule reading
- packet prefill for refactor front matter
- invariant slices by zone
- planning evidence stored in a maintained repo surface, not `/tmp`

## Current decisions

- Planning-lock evidence belongs in `docs/operations/`, `.omx/plans/`, or
  `.omx/context/`; reusable packet plans should live in `docs/operations/`.
- Temporary `/tmp` plans are intentionally rejected by planning-lock because
  they are not maintained repo evidence.
- Refactor packet front matter should be machine-prefilled from existing maps:
  `zones.yaml`, `source_rationale.yaml`, `test_topology.yaml`, and
  `invariants.yaml`.
- Single-file packet scopes must stay literal. Example:
  `src/calibration/platt.py`, not `src/calibration/platt.py/**`.
- Context budgets are provisional starting assumptions, not fixed file-count
  limits. The tool should warn when a starting packet may be insufficient and
  tell the agent when to expand context.
- Do not ask active agents to count reads or log telemetry. Topology should
  generate assistance from maintained manifests.
- `impact` is the reusable primitive for module-flow help; a future `debug`
  command should compose `impact` with symptom-specific read/run/watch hints.

## Implemented in this task

- `topology_doctor.py packet --packet-type refactor --scope <path> --task <task>`
  emits a prefilled packet draft.
- `topology_doctor.py --invariants --zone <zone> --json` emits a small invariant
  slice.
- `semantic_linter.py --check <file-or-dir>` runs targeted semantic checks.
- `topology_doctor.py --summary-only` improves issue readability.
- map maintenance uses git status and respects ignored scratch artifacts.
- `topology_doctor.py context-pack --pack-type package_review --task <task>
  --files <files...>` emits a reviewer-oriented context packet instead of an
  edit packet or ad hoc core map.
- `topology_doctor.py context-pack --pack-type debug --task <symptom>
  --files <files...>` emits a short debugging packet with suspected boundaries,
  red/green checks, provisional gaps, and context-expansion triggers.
- `architecture/context_pack_profiles.yaml` defines task-shaped generated packet
  contracts. These profiles compose existing topology evidence and are not new
  authority.
- `architecture/artifact_lifecycle.yaml` defines artifact classes and the
  minimum work-record contract for repo-changing agent work.
- `topology_doctor.py --work-record --changed-files <files>
  --work-record-path <record>` checks that non-trivial repo changes have a
  short factual record before closeout.

## Approved next slice

1. Add a shared `context_assumption` block to digest, navigation, and packet
   outputs.
2. Add minimal `topology_doctor.py impact --files <files...>` output from
   `source_rationale.yaml`, zones, write routes, and test topology.
3. Update `context_budget.yaml` semantics so blocking budget enforcement
   requires explicit governance promotion metadata.
4. Keep planning-lock independent from read-count or context-budget heuristics.

## Package Review Pilot

Package-level reviewers need a different packet from implementers or refactor
agents. The pilot packet must show changed files, zones touched, contract
surfaces, proof claims, coverage gaps, downstream risks, tests, static checks,
route health, repo health, and provisional-context expansion triggers. It must
not emit edit permission such as `files_may_change`.

Lore routing is tiered: both direct file-pattern evidence and broad task-term
matches include id, severity, digest, match reason, and expansion hint by
default. This keeps review attention on structural questions instead of dumping
all historical failure detail into every package review.

## Debug Pilot

Debug packets are symptom packets, not automatic diagnoses. They may list
suspected boundaries, red/green commands, proof claims, route health, repo
health, and provisional gaps, but must not emit `root_cause`,
`complete_understanding`, `files_may_change`, or `write_scope`.

`auto` selection stays conservative: package/cross-slice review wins over debug;
debug is selected only for clear symptom/debug/failing/regression language with
target files. Ambiguous tasks fail instead of guessing.

## Context assumption schema

Topology outputs should carry a generated warning block, not a hard read-count
limit:

```yaml
context_assumption:
  sufficiency: provisional_starting_packet
  authority_status: incomplete_context
  planning_lock_independent: true
```

Agents may start from this packet, but must expand context if ownership,
downstream behavior, truth authority, or verification scope becomes unclear.

## Required closeout

Before archiving this folder:

1. Run relevant topology gates and tests.
2. Run `python scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`.
3. Run `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout`.
4. Extract any durable failure lesson to `architecture/history_lore.yaml`.
5. Move this folder to `docs/archives/work_packets/branches/<branch>/<program_domain>/YYYY-MM-DD_slug/` and remove it from
   `docs/operations/AGENTS.md`.
