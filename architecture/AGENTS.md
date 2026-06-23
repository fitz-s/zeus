# architecture AGENTS

This directory contains machine-checkable law, registries, manifests, topology, and invariant surfaces. Changes here are architecture/governance changes, never "just docs."

Module books: `docs/reference/modules/topology_system.md`, `docs/reference/modules/manifests_system.md`.

---

## Required Before Edit

- Read root `AGENTS.md` and `workspace_map.md`.
- Read `docs/authority/zeus_current_architecture.md` and `docs/authority/zeus_current_delivery.md`.
- Identify touched manifests and their code/test/docs dependents.
- State what existing surface this change harmonizes, supersedes, or demotes.
- Plan validation commands and stale-route checks.

---

## Rules

- Prefer machine-readable manifest updates over prose claims.
- Keep manifests, authority docs, reference docs, tests, and source mutually consistent.
- Do not create parallel authority files or dated strategy-of-record Markdown.
- Do not copy historical rationale into active law without promoting and registering it.
- Do not claim runtime convergence unless current code/receipts prove it.
- Dated audits, critic passes, PR reviews, and integration plans are historical evidence unless a current manifest explicitly routes them.

---

## Core Active Manifests

| File | Purpose |
|---|---|
| `docs_registry.yaml` | docs classification and default-read contract |
| `reference_replacement.yaml` | canonical reference replacement/demotion map |
| `module_manifest.yaml` | module-book/router/dependency registry |
| `kernel_manifest.yaml` | kernel file ownership and protection rules |
| `invariants.yaml` | invariant definitions and enforcement intent |
| `negative_constraints.yaml` | forbidden seams and explicit carve-outs |
| `fatal_misreads.yaml` | semantic shortcut antibodies |
| `db_table_ownership.yaml` | canonical table-to-DB ownership |
| `runtime_modes.yaml` | discovery mode grammar and shared runtime path |
| `runtime_posture.yaml` | runtime posture grammar |
| `money_path_objects.yaml` | money-path economic object/state/source registry |
| `source_rationale.yaml` | source/package rationale, hazards, providers, write routes |
| `test_topology.yaml` | test-suite topology and law gates |
| `task_boot_profiles.yaml` | task-class semantic boot profiles |
| `city_truth_contract.yaml` | stable source-role schema; not current city truth |
| `code_review_graph_protocol.yaml` | graph protocol; graph remains derived context |
| `zones.yaml` | zone definitions and import rules |
| `topology.yaml` | compiled topology graph |
| `script_manifest.yaml` | script lifecycle and authority scope when present |
| `money_path_ci.yaml` | money-path invariant-to-test routing |
| `test_quality.yaml` | money-path test proof metadata |

Other dated Markdown/YAML files may be useful evidence/history, but do not place them in default boot unless this file, `docs_registry.yaml`, and task routing all require it.

---

## Subdirectory Navigation

| Subdirectory | Purpose |
|---|---|
| `ast_rules/` | AST-level enforcement rules |
| `packet_templates/` | Work packet templates |
| `self_check/` | Agent entry checklists |

Read the scoped `AGENTS.md` in a subdirectory before editing it.

---

## Validation

For architecture/docs routing changes, attempt:

```bash
python3 scripts/topology_doctor.py --strict
python3 scripts/topology_doctor.py --source
python3 scripts/topology_doctor.py --tests
python3 scripts/topology_doctor.py --fatal-misreads
```

Also search active default-read files for demoted terms and stale route references. Separate changed-surface failures from pre-existing repo drift.
