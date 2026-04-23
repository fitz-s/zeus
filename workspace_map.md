# Zeus Workspace Map

This is the root visibility and routing guide for zero-context agents.

Use it after `AGENTS.md` to answer two questions quickly:

1. what kind of surface am I looking at?
2. what should I read next?

## Default route

1. read `AGENTS.md`
2. read this map
3. read the scoped `AGENTS.md` for the directory you will touch
4. load the relevant manifest or active packet
5. read code or evidence only after the route is narrow

Default navigation command:

`python scripts/topology_doctor.py --navigation --task "<task>" --files <files>`

## Visibility classes

| Class | Meaning | Examples | Default posture |
|------|---------|----------|-----------------|
| tracked visible text | Tracked human-readable routing, law, plans, and docs | `AGENTS.md`, `workspace_map.md`, `docs/**`, `architecture/**` | Default-read when relevant |
| tracked derived context | Tracked artifacts that help review and retrieval but are not authority | `.code-review-graph/graph.db` | Read as derived context, never as law |
| historical cold storage | Historical bodies and bundles that may exist locally but are not part of the default boot path | `docs/archives/**`, local archive bundles | Do not default-read; route through `docs/archive_registry.md` |
| runtime-local scratch and control | Runtime state, DBs, locks, and ignored planning scratch | `state/**`, `.omx/**`, `.omc/**` | Treat as runtime context, not repo law |
| generated evidence sinks | Reports, checklists, workbooks, and raw captures | `docs/reports/**`, `docs/to-do-list/**`, `docs/artifacts/**`, `raw/**` | Evidence only unless promoted through a packet |

## Directory router

| Path | Role | Next read |
|------|------|-----------|
| `src/` | Runtime source code | `src/AGENTS.md`, then package `AGENTS.md` |
| `tests/` | Regression and law gates | `tests/AGENTS.md`, `architecture/test_topology.yaml` |
| `scripts/` | Operator, ETL, audit, and enforcement tools | `scripts/AGENTS.md`, `architecture/script_manifest.yaml` |
| `docs/authority/` | Current architecture and delivery law | `docs/authority/AGENTS.md` |
| `docs/reference/` | Domain, architecture, market/settlement, data/replay, failure-mode, and math references | `docs/reference/AGENTS.md` |
| `docs/operations/` | Live control pointer, active packets, package inputs, gap register | `docs/operations/AGENTS.md` |
| `docs/runbooks/` | Operator runbooks | `docs/runbooks/AGENTS.md` |
| `docs/reports/` | Generated diagnostic reports | `docs/reports/AGENTS.md` |
| `docs/to-do-list/` | Active checklist workbooks | `docs/to-do-list/AGENTS.md` |
| `docs/artifacts/` | Active evidence artifacts | `docs/artifacts/AGENTS.md` |
| `architecture/` | Machine-checkable workspace law | `architecture/AGENTS.md` |
| `config/` | Runtime settings and reality contracts | `config/AGENTS.md` |
| `.code-review-graph/` | Tracked derived online context | graph status via `python scripts/topology_doctor.py --code-review-graph-status --json` |
| `state/` | Runtime DBs and local control files | classify before treating as truth |
| `raw/` | Raw external evidence captures | `raw/AGENTS.md` |

## Machine manifests

Prefer these over prose when they exist:

| Manifest | Use |
|----------|-----|
| `architecture/invariants.yaml` | Current invariant IDs and enforcement intent |
| `architecture/negative_constraints.yaml` | Forbidden moves |
| `architecture/zones.yaml` | Canonical file-level zone ownership |
| `architecture/topology.yaml` | Coverage roots, docs registry, current-state contract |
| `architecture/source_rationale.yaml` | Per-file `src/**` rationale, hazards, and write routes |
| `architecture/script_manifest.yaml` | Script lifecycle and authority scope |
| `architecture/test_topology.yaml` | Test categories and law gates |
| `architecture/history_lore.yaml` | Dense historical lessons and antibodies |
| `architecture/artifact_lifecycle.yaml` | Artifact classes and evidence rules |
| `architecture/context_budget.yaml` | Boot-surface budget and maintenance cadence |
| `architecture/change_receipt_schema.yaml` | Route-receipt contract |
| `architecture/docs_registry.yaml` | Docs classification and default-read registry |
| `architecture/task_boot_profiles.yaml` | Question-first semantic boot profiles by task class |
| `architecture/fatal_misreads.yaml` | Machine-readable semantic shortcut antibodies |
| `architecture/city_truth_contract.yaml` | Stable city/source/date truth contract schema; not a current truth table |
| `architecture/code_review_graph_protocol.yaml` | Two-stage Code Review Graph use protocol; graph remains derived context |

## Do not default-read

- `docs/archives/**` and local archive bundles
- `.code-review-graph/graph.db` as if it were authority
- `docs/reports/**` as if placement made them law
- `.omx/context/**` and `.omc/**` runtime scratch
- long reference docs unless the digest or packet routes you there

## Maintenance rule

When adding, renaming, or deleting a file:

1. update the owning manifest when one exists
2. update the scoped `AGENTS.md` if the local router changed
3. update this file only when directory-level structure or visibility classes
   changed

Run:

`python scripts/topology_doctor.py --context-budget --json`

after a material boot-surface rewrite.
