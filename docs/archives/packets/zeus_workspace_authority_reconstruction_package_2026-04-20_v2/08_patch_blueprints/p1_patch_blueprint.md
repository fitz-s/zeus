# P1 Patch Blueprint

## Intent

Encode the new visible-history and thin-current-state policy into machine checks so the repo cannot silently drift back.

## Exact target state

### `architecture/topology.yaml`

Refine docs topology so hidden archive bodies are no longer treated like a standard visible docs subtree.
If needed, add an explicit visibility/availability field or exclusion pattern rather than relying on prose interpretation.

### `architecture/topology_schema.yaml`

Only if required by the topology change:

- allow visibility/availability metadata or archive-interface semantics
- add issue codes for hidden-vs-visible drift if useful

### `architecture/map_maintenance.yaml`

Add companion rules so changes to docs-root/archive-history routing require coordinated updates to:

- `docs/archive_registry.md`
- `docs/README.md`
- `docs/AGENTS.md`
- `architecture/topology.yaml` where relevant

### `architecture/context_budget.yaml`

Add explicit budget expectations for:

- `docs/archive_registry.md`
- `docs/operations/current_state.md`
- any new graph-summary file if already adopted later

### `architecture/artifact_lifecycle.yaml`

If P1 touches lifecycle semantics, classify the visible archive interface distinctly from hidden archive bodies.

### `scripts/topology_doctor_registry_checks.py`

Add only the minimum logic needed to keep docs-visible history routing coherent.
Do not widen into unrelated policy work.

### `scripts/topology_doctor_map_maintenance.py`

Update only if the new companion rules require code support.

### `tests/test_topology_doctor.py`

Add tests for:

- hidden archive bodies not acting like visible docs registry peers
- archive registry companion enforcement
- current_state thinness / required labels if policy is encoded that way

## Acceptance criteria

- P0 wording is now backed by machine checks.
- A future stale reintroduction of archive-as-peer-docs would fail or warn clearly.
- Current-state drift pressure is reduced.
