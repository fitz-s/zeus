# 06 — Topology Doctor Architecture Audit

## Ruling

`topology_doctor` is valuable but too central. It has evolved from a checker into a routing compiler, context-pack generator, closeout engine, graph bridge, docs auditor, source/test/script manifest validator, and current-state receipt checker.

This centrality is not automatically bad. The problem is that policy, issue shape, lane aggregation, and rendering still leak through the same facade.

## Current architecture strengths

- Helper modules exist for docs, registry, source, tests, scripts, map maintenance, context packs, graph, closeout, data rebuild, artifact/work-record, freshness, reference checks, and policy checks.
- The CLI has many lanes and subcommands.
- Graph status is warning-first in most stale/missing cases.
- Closeout already performs some changed-file lane selection.
- Context pack builder understands module books/module manifest and graph appendices.

## Current architecture weaknesses

### Issue shape is central and too small

All helpers report the same thin issue shape. Because issues lack scope and owner metadata, higher-level gating has to infer meaning.

### Navigation uses health aggregation

Navigation currently runs a broad set of validators and treats error severity as blocking. That makes route discovery hostage to unrelated global drift.

### Closeout scoping is path-based, not obligation-based

Filtering by issue path is not enough. The correct model is:

- changed file,
- required companions,
- companion of companion if explicitly declared,
- lane policy,
- allowed deferral with receipt,
- direct vs unrelated global drift.

### Helper modules encode law but do not expose ontology

Docs checks, script checks, source checks, test checks, graph checks, and context-pack checks all have distinct hidden domain models. Those models should be promoted to the topology_doctor_system module book and issue metadata.

### Output is not repair-first

The doctor should group by:

- owner manifest,
- repair kind,
- blocking mode,
- affected changed files,
- generated draft availability.

## Recommended internal architecture

### Layer 1 — Manifest loaders

Pure readers and schema normalizers.

### Layer 2 — Validators

Return typed issues only. Validators do not decide final blocking except for local severity defaults.

### Layer 3 — Policy engine

Maps typed issues to modes:

- `navigation`
- `navigation --strict-health`
- `closeout`
- `strict`
- `global-health`
- `packet-prefill`
- `context-pack`

### Layer 4 — Repair planner

Groups issues and emits repair draft proposals.

### Layer 5 — Renderers

Human compact, human full, JSON, and context-pack appendices.

## Specific refactors

1. Add `TopologyIssueV2` dataclass, keeping JSON backward-compatible.
2. Add issue factory helpers: `blocking`, `warning`, `advisory`, `global_drift`, `expired_advisory`.
3. Add `LanePolicy` and `ModePolicy`.
4. Change navigation to produce route digest plus `repo_health.warnings`.
5. Change closeout to evaluate typed `blocking_modes` and companion paths.
6. Add a repair draft grouping function.
7. Move compiled-topology output contract into a durable module book and tests.

## Do not refactor first into many packages

Do not split the codebase into a new framework before fixing semantics. First add typed issues and lane policy in place. Then split if needed.
