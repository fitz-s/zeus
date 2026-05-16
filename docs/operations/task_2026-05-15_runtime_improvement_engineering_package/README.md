# Runtime Improvement Engineering Package

Created: 2026-05-15
Status: PLAN-ONLY (specs + audit; implementation packets enumerated in
`05_execution_packets/PACKET_INDEX.md`)

## What This Is

Four-track engineering package addressing compounding runtime entropy:

| Track | Purpose | Read first |
|-------|---------|------------|
| `00_evidence/` | Raw inventories grounding every claim | HIDDEN_BRANCH_INVENTORY, WORKSPACE_MESS_AUDIT, AUTHORITY_DOCS_INVENTORY, CRON_AND_DAEMON_INVENTORY |
| `01_topology_v_next/` | Project-agnostic topology redesign + Zeus binding + migration | UNIVERSAL_TOPOLOGY_DESIGN, then HIDDEN_BRANCH_LESSONS, ZEUS_BINDING_LAYER, MIGRATION_PATH |
| `02_daily_maintenance_agent/` | Scheduled hygiene worker design | DESIGN, then SAFETY_CONTRACT, TASK_CATALOG.yaml, DRY_RUN_PROTOCOL |
| `03_authority_drift_remediation/` | 62-doc drift assessment + repair playbook | DRIFT_ASSESSMENT, REMEDIATION_PLAN |
| `04_workspace_hygiene/` | Purge/archive/lore extraction rules | PURGE_CATEGORIES, ARCHIVAL_RULES, LORE_EXTRACTION_PROTOCOL |
| `05_execution_packets/` | Ordered follow-up implementation packets | PACKET_INDEX |
| `99_verification/` | Probes + regression suite | VERIFICATION_PLAN, REGRESSION_PROBE_SUITE |

## Top-level entry

Read `PLAN.md` first. It defines goals, non-goals, track dependencies,
acceptance criteria, and stop conditions for the parent packet.

## Reusability discipline

The topology v_next core, the maintenance agent core, the drift
remediation playbook, and the hygiene rules are PROJECT-AGNOSTIC. Zeus-
specific glue lives only in:
- `01_topology_v_next/ZEUS_BINDING_LAYER.md`
- `02_daily_maintenance_agent/TASK_CATALOG.yaml` (project field + Zeus
  paths)
- `bindings/` directory in implementation packets P6 and beyond

A second project can adopt this work by replacing only those bindings.

## Cross-iteration meta-finding

Across 7 prior iterations (5 topology + 2 hook redesigns in 4 days,
2026-05-05 → 2026-05-09), the admission decision unit
`(lexical-task-phrase, file-path-list)` was never structurally changed.
Every fix added a sidecar (severity registry, typed-intent enum,
companion-loop-break, hook telemetry) on top of the same frame. The one
subtractive iteration (`hook_redesign_v2`) retracted overbuilt
authorization that duplicated Claude Code native permissions.

V_next changes the admission unit to
`(typed-intent, file-path-list, profile-hint?)` where typed-intent
resolves before profile selection and the lexical phrase becomes a
disambiguation hint, not a routing key. This structurally eliminates
LEXICAL_PROFILE_MISS rather than papering over it with another sidecar.

Detail: `01_topology_v_next/HIDDEN_BRANCH_LESSONS.md` § Cross-Iteration
Meta-Pattern.
