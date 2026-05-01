# 11 — Recommended Authority Layout

## Goal

Keep Zeus authority layered and machine-readable, while adding dense cognition where agents need understanding.

## Authority stack

### Tier 0 — Executable/runtime truth

- Runtime source code where behavior lives.
- Blocking tests where they verify executable law.
- Canonical DB/state truth where explicitly defined.

### Tier 1 — Constitutional and machine authority

- `AGENTS.md`
- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- `architecture/zones.yaml`
- `architecture/kernel_manifest.yaml`
- `docs/authority/**`
- Explicit current architecture/delivery authority docs.

### Tier 2 — Routing and governance manifests

- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `architecture/source_rationale.yaml`
- `architecture/test_topology.yaml`
- `architecture/script_manifest.yaml`
- `architecture/map_maintenance.yaml`
- `architecture/context_budget.yaml`
- `architecture/artifact_lifecycle.yaml`
- `architecture/module_manifest.yaml`
- `architecture/code_review_graph_protocol.yaml`

These should be machine-readable and normalized. They should not become encyclopedias.

### Tier 3 — Dense reference cognition

- `docs/reference/modules/*.md`
- `docs/reference/zeus_math_spec.md`
- proposed system books:
  - topology system,
  - code review graph,
  - docs system,
  - manifests system,
  - topology doctor system,
  - closeout and receipts system.

Reference cognition explains the system. It does not override Tier 0–2.

### Tier 4 — Current operations

- `docs/operations/current_state.md`
- current fact docs,
- active packet plans/work logs/receipts.

These point to active work and freshness. They do not rewrite durable law.

### Tier 5 — Derived evidence/context

- reports,
- artifacts,
- context packs,
- graph-derived appendices,
- compiled topology output,
- code-impact graphs.

They inform review and routing. They do not authorize semantic truth.

### Tier 6 — Archives

Archives are cold evidence. Default route is through `docs/archive_registry.md` and selective extraction.

## Proposed reference layout

```text
docs/reference/modules/
  topology_system.md
  code_review_graph.md
  docs_system.md
  manifests_system.md
  topology_doctor_system.md
  closeout_and_receipts_system.md
  state.md
  engine.md
  data.md
  ...
```

## Proposed machine layout additions

Do not add a new manifest yet. First add sections to existing surfaces:

- `topology_schema.yaml`: issue metadata schema if topology doctor output schema is formalized here.
- `module_manifest.yaml`: maturity/extraction status fields only if ownership is clear.
- `map_maintenance.yaml`: companion classes and repair_kind once typed issues exist.
- `docs_registry.yaml`: no broad expansion; keep classification.
- `test_topology.yaml`: add `live_topology` marker policy after test split.

## Do not create

- A second docs registry.
- A second source rationale manifest.
- A graph authority manifest.
- A packet-status reference book.
- Archive-as-default-read routing.
