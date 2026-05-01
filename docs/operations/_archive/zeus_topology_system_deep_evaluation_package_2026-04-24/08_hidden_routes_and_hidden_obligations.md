# 08 — Hidden Routes and Hidden Obligations

## Ruling

Zeus has many hidden obligations that are partially encoded in tests, helper modules, map maintenance, current_state checks, and graph protocol. They need to be surfaced in durable module/system books and typed issue metadata.

## Files whose change should imply companion updates

| Changed file class | Required companions | Current source of rule | Problem |
|---|---|---|---|
| `src/**/*.py` added/deleted | `architecture/source_rationale.yaml` | `map_maintenance.yaml`, source checker | Does not automatically explain module book/source route impact. |
| `tests/test_*.py` added/deleted | `architecture/test_topology.yaml` | `map_maintenance.yaml`, test checker | Test category/law role hidden unless reading test_topology/tests. |
| `scripts/*.py`, `scripts/*.sh` added/deleted | `architecture/script_manifest.yaml` | `map_maintenance.yaml`, script checker | Lifecycle and write-target safety hidden in huge manifest. |
| `docs/reference/*.md` added/deleted | `docs/reference/AGENTS.md`, `architecture/reference_replacement.yaml`, `architecture/docs_registry.yaml` | `map_maintenance.yaml`, docs checks | Reference replacement law under-explained. |
| `docs/reference/modules/*.md` added/deleted | `docs/reference/AGENTS.md`, `docs/reference/modules/AGENTS.md`, `architecture/docs_registry.yaml`, `architecture/module_manifest.yaml` | `map_maintenance.yaml`, module book checks | Module book density and module-manifest route must move together. |
| `architecture/module_manifest.yaml` modified | `architecture/AGENTS.md`, `workspace_map.md`, `architecture/docs_registry.yaml`, `docs/reference/AGENTS.md` | `map_maintenance.yaml` | High-centrality manifest; ownership effects underdocumented. |
| operations task docs added/deleted | `docs/operations/AGENTS.md`, `docs/operations/current_state.md` | `map_maintenance.yaml`, docs checks | Active task visibility can silently break online boot. |
| docs artifacts/reports added | local AGENTS and/or `artifact_lifecycle.yaml` | map/docs/artifact checks | Evidence vs authority classification can drift. |

## Machine manifests that silently depend on each other

- `topology.yaml` depends on `docs_registry.yaml` for docs classification and default-read behavior.
- `module_manifest.yaml` depends on `docs_registry.yaml` for module book registration.
- `module_manifest.yaml` depends on `source_rationale.yaml`, `test_topology.yaml`, and current fact docs for route accuracy.
- `map_maintenance.yaml` depends on canonical owner manifests being known.
- `script_manifest.yaml` depends on `naming_conventions.yaml` and required tests.
- `context_budget.yaml` depends on module books existing and being dense enough.
- `code_review_graph_protocol.yaml` depends on graph tooling and context-pack behavior remaining derived-only.

## Docs/current_state dependencies that are underdocumented

`docs/operations/current_state.md` is not just a status note. It binds:

- active package source,
- active execution packet,
- receipt-bound source,
- required evidence,
- active anchors,
- current fact companions,
- next action surface.

The docs checker enforces receipt binding and registered operations surfaces. This is a hidden law that should be explained in `closeout_and_receipts_system.md`.

## Topology doctor helper modules with hidden centrality

| Helper | Hidden centrality |
|---|---|
| `topology_doctor_docs_checks.py` | Docs registry, operations registry, runtime plan inventory, current_state receipt binding, stale truth/path leaks. |
| `topology_doctor_registry_checks.py` | Root/docs strict checks, archive interface, WMO gate, active pointers, shadow authority leaks. |
| `topology_doctor_source_checks.py` | Source rationale, state-role split, write routes, AGENTS zone coherence. |
| `topology_doctor_test_checks.py` | Test categories, law gates, high-sensitivity skips, reverse antibodies, relationship manifests. |
| `topology_doctor_script_checks.py` | Script lifecycle, dangerous writes, apply flags, target DB, canonical write-helper misuse. |
| `topology_doctor_map_maintenance.py` | Companion update gate. |
| `topology_doctor_context_pack.py` | Module books, module manifest, route health, repo health, context-pack profiles. |
| `topology_doctor_code_review_graph.py` | Graph health, derived code impact, graph limitations. |
| `topology_doctor_closeout.py` | Changed files, selected lanes, always-on lanes, closeout telemetry. |

## Graph-derived routes topology does not yet surface enough

- topology_doctor helper dependency clusters,
- high-impact modules whose tests are not obvious from manifests,
- bridge files connecting source modules,
- graph-derived impacted tests for changed files,
- graph-derived module hotspots,
- graph-derived “review order” routes.

These should be derived appendices, not authority.

## Laws enforced in tests but under-expressed in manifests/books

- Graph semantic boot before graph context.
- Graph status warning vs error boundaries.
- Archive registry as visible interface; archives not default-read.
- Compiled topology output contract.
- Current_state receipt binding.
- Module book required section headings.
- Module manifest required fields.
- Docs registry parent coverage and direct-reference leakage.
- Freshness metadata requirements for changed scripts/tests.
- Reverse-antibody quarantine expectations.

## Repair routes currently in human memory or archives

- How to turn a topology issue into owner-manifest edits.
- How to classify a new module book.
- How to extract archive evidence without making archives default-read.
- How to refresh/use graph safely without custom watchers.
- How to close a packet with scoped gates and receipts.
- How to defer unrelated global drift without hiding it.

## Recommendation

Create a generated “hidden obligations” section in context packs and a durable `topology_doctor_system.md` section that maps issue codes to owners, repair kinds, and companion surfaces.
