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

Topology doctor has five layers:

1. **Loaders** read `architecture/**`, scoped `AGENTS.md`, docs registries, and current operation pointers.
2. **Validators** emit `TopologyIssue` objects with legacy fields and optional typed metadata.
3. **Mode policy** decides whether an issue blocks `navigation`, `closeout`, `strict_full_repo`, or remains `global_health` context.
4. **Runtime route cards** summarize admission, risk tier, next action, gate
   budget, requested claims, and expansion hints before appendices.
5. **Renderers** emit human and JSON outputs for navigation, strict lanes, context packs, and closeout.

The checker family is intentionally split across `scripts/topology_doctor_*.py`: registry/docs/source/test/script checks report facts; `scripts/topology_doctor.py` and `scripts/topology_doctor_cli.py` expose the public facade; `scripts/topology_doctor_closeout.py` compiles changed-file closeout.

`build_impact()` is adapter-backed. Source files still use
`architecture/source_rationale.yaml`; architecture manifests, scripts, docs, and
tests use their owning manifests or scoped routers so non-source work can get a
bounded impact summary without pretending every file is source.

## Hidden obligations

- Navigation must not treat unrelated repo-health drift as a direct blocker, but it must continue to expose that drift.
- Closeout must block missing work records, missing receipts, changed-file companion failures, and changed-file law violations.
- JSON issue compatibility is durable: `code`, `path`, `message`, and `severity` remain present in legacy output.
- Typed issue metadata is additive and exists to route repair work, not to invent new law.
- Every new top-level script/test/doc route needs its owning manifest updated when the manifest owns that class of fact.
- Typed `intent` may select a digest profile, but admission still reconciles
  files against `allowed_files` and forbidden patterns.
- `--route-card-only` is the lightweight first-screen path for T0/T1 work; it
  should not print appendices, repo-health lists, or unrelated drift.
- `--claim` turns optional evidence into claim-scoped gates. A stale graph
  blocks `graph_impact_validated`, not ordinary navigation or closeout.
- Workflow, skill-use, and work-ethic guidance belongs in generated context
  packs, not new authority documents.

## Failure modes

- Global docs/source/test drift blocks a narrow route and causes agents to either stop prematurely or over-edit unrelated files.
- Strict repo-health output is mistaken for scoped closeout and hides packet evidence gaps.
- Topology issues stay too flat to route repair ownership, causing manifest fixes to become manual guesswork.
- A graph or context-pack appendix is treated as authority rather than derived context.
- Process controls become bureaucratic when every stale warning blocks every
  task instead of only the claim that depends on it.

## Repair routes

- Use `repair_kind: add_registry_row` when a tracked file lacks its owning registry row.
- Use `repair_kind: update_companion` when a file change requires a scoped AGENTS, registry, or map update.
- Use `repair_kind: refresh_graph` only for graph freshness/coverage debt; do not use it for semantic proof.
- Read `code_review_graph_status.details.graph_health` to see which graph
  freshness/coverage facts invalidate graph claims; it is generated health
  context, not semantic authority.
- Use `repair_kind: propose_owner_manifest` when ownership is ambiguous and P3/P4-level planning is required.
- Run `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>` before edits and `closeout` with changed files before closure.
- Use `--route-card-only` for quick orientation and `--claim <claim_id>` when
  the completion statement depends on graph, repo-health, semantic boot, packet
  closeout, or live authorization evidence.
- Start future runtime-oriented work with the composed command when the caller
  needs one packet rather than separate digest/bootstrap/context invocations:
  `python scripts/topology_doctor.py runtime --task "<task>" --files <files> --intent "<intent>" --task-class <class> --write-intent <intent>`.
- Use `python3 scripts/topology_doctor.py runtime ...` when a caller needs the
  composed agent-runtime packet: route card, optional semantic boot, optional
  role context, claims, dispatch guidance, gate budget, and artifact-treatment
  hints.
- Use role context packs (`explorer`, `executor`, `critic`, `verifier`) when the
  next agent needs a bounded runtime contract rather than the full packet.

## Cross-links

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/code_review_graph.md`
