# Topology Doctor System

> Status: reference, not authority. See `AGENTS.md`, `workspace_map.md`, `architecture/**`, and `docs/authority/**` for authority.

## Purpose

The topology doctor is Zeus's executable routing and closeout compiler. It reads machine manifests, checks mesh drift, emits scoped navigation digests, and separates route blockers from global repository health so agents can change the right surface without hiding unrelated debt.

## Authority anchors

- `AGENTS.md` defines the mandatory topology-navigation workflow.
- `workspace_map.md` defines visibility classes and default read order.
- `architecture/topology.yaml` defines coverage roots and digest inputs.
- `architecture/topology_schema.yaml` defines compiled topology and issue JSON contracts.
- `architecture/map_maintenance.yaml`, `artifact_lifecycle.yaml`, and `change_receipt_schema.yaml` define closeout companions.
- `tests/test_topology_doctor.py` is the regression surface for topology-doctor behavior.

## How it works

Topology doctor has four layers:

1. **Loaders** read `architecture/**`, scoped `AGENTS.md`, docs registries, and current operation pointers.
2. **Validators** emit `TopologyIssue` objects with legacy fields and optional typed metadata.
3. **Mode policy** decides whether an issue blocks `navigation`, `closeout`, `strict_full_repo`, or remains `global_health` context.
4. **Renderers** emit human and JSON outputs for navigation, strict lanes, context packs, and closeout.

The checker family is intentionally split across `scripts/topology_doctor_*.py`: registry/docs/source/test/script checks report facts; `scripts/topology_doctor.py` and `scripts/topology_doctor_cli.py` expose the public facade; `scripts/topology_doctor_closeout.py` compiles changed-file closeout.

## Hidden obligations

- Navigation must not treat unrelated repo-health drift as a direct blocker, but it must continue to expose that drift.
- Closeout must block missing work records, missing receipts, changed-file companion failures, and changed-file law violations.
- JSON issue compatibility is durable: `code`, `path`, `message`, and `severity` remain present in legacy output.
- Typed issue metadata is additive and exists to route repair work, not to invent new law.
- Every new top-level script/test/doc route needs its owning manifest updated when the manifest owns that class of fact.

## Failure modes

- Global docs/source/test drift blocks a narrow route and causes agents to either stop prematurely or over-edit unrelated files.
- Strict repo-health output is mistaken for scoped closeout and hides packet evidence gaps.
- Topology issues stay too flat to route repair ownership, causing manifest fixes to become manual guesswork.
- A graph or context-pack appendix is treated as authority rather than derived context.

## Repair routes

- Use `repair_kind: add_registry_row` when a tracked file lacks its owning registry row.
- Use `repair_kind: update_companion` when a file change requires a scoped AGENTS, registry, or map update.
- Use `repair_kind: refresh_graph` only for graph freshness/coverage debt; do not use it for semantic proof.
- Use `repair_kind: propose_owner_manifest` when ownership is ambiguous and P3/P4-level planning is required.
- Run `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>` before edits and `closeout` with changed files before closure.

## Cross-links

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/code_review_graph.md`
