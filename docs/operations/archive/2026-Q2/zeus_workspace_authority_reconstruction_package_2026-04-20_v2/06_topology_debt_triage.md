# Topology Debt Triage

This section classifies the major debt clusters that matter for authority reconstruction.

| Debt class | Severity | Why it matters | Blocker? | Proposed packet | Files affected | Tests / checks |
|---|---|---|---|---|---|---|
| Boot-surface visibility debt | Critical | Root/docs surfaces still blur visible tracked docs, hidden archives, and derived context. This directly harms online-only agents. | Blocks a trustworthy online boot path, but not runtime behavior. | P0 | `AGENTS.md`, `workspace_map.md`, `docs/README.md`, `docs/AGENTS.md` | `topology_doctor --docs`, map-maintenance, manual diff review |
| `current_state.md` overload debt | Critical | Live control pointer is diluted by narrative, runtime scratch, and historical residue. | Blocks clean packet handoff quality. | P0 | `docs/operations/current_state.md`, `docs/operations/AGENTS.md` | `topology_doctor --docs`, current-state related tests |
| Visible-history interface debt | High | Archives are hidden, but there is no tracked, truthful, visible interface file for online readers. | Does not block P0; solved in P0 itself. | P0 | `docs/archive_registry.md`, `docs/README.md`, `docs/AGENTS.md`, `workspace_map.md`, `architecture/topology.yaml` | docs lane, map-maintenance |
| Hidden archive registry debt in topology | High | `architecture/topology.yaml` still treats hidden archive structure too much like a visible docs subtree. | Non-blocker for runtime; blocker for full machine/prose coherence. | P0/P1 | `architecture/topology.yaml`, `architecture/topology_schema.yaml`, `architecture/map_maintenance.yaml` | strict/docs lanes, tests |
| Graph underpromotion debt | Critical | Repo already relies on graph lane and tests, but boot prose still frames graph as an appendage. | Blocks a correct external mental model. | P0 | `AGENTS.md`, `workspace_map.md`, `04_code_review_graph_policy.md` implementation files later | docs lane |
| Graph wrapper portability debt | Critical | MCP wrapper hardcodes a local repo root, undermining portability. | Blocks a clean P2 graph-upgrade path. | P2 | `scripts/code_review_graph_mcp_readonly.py`, graph-related tooling/tests | py_compile, pytest, graph status |
| Graph previewability debt | High | `graph.db` is tracked but opaque in normal browser review; no small sidecar summary exists. | Non-blocker, but hurts online review quality. | P2 | `.gitignore`, `.code-review-graph/.gitignore`, `.code-review-graph/graph_meta.json`, graph tooling | graph status/context-pack tests |
| Context-budget debt | Medium | Repaired entry surfaces are not all budget-protected, so drift can return. | Non-blocker for P0, important for P1. | P1 | `architecture/context_budget.yaml` | `topology_doctor --context-budget` |
| Map-maintenance debt | Medium | Companion rules do not yet encode the new visible archive interface. | Non-blocker for P0, important for making P1 sticky. | P1 | `architecture/map_maintenance.yaml`, `scripts/topology_doctor_map_maintenance.py` | map-maintenance lane, tests |
| Artifact-lifecycle debt | Medium | Lifecycle classification is strong, but may need explicit graph-meta and archive-interface classes. | Non-blocker. | P1/P2 | `architecture/artifact_lifecycle.yaml`, `architecture/topology.yaml` | strict/docs lanes |
| `source_rationale.yaml` relation-gap debt | Low | Not a quality problem; a positioning problem. It should be presented as graph complement, not replacement. | Non-blocker. | P3 or later | `architecture/source_rationale.yaml` | source checks only if modified |
| Stale authority-claim language | High | Docs-root language still sells the wrong shape of the repo. | Blocks online boot clarity. | P0 | `docs/README.md`, `docs/AGENTS.md` | docs lane |
| Stale comments/tests/doc references | Medium | Some references still assume archives are loadable peers or that current_state can stay thick. | Non-blocker. | P1/P3 | targeted docs/tests/comments | pytest/docs lane |
| Online-context gap for history | High | `history_lore.yaml` exists, but the boot surface does not explain when to use lore vs archive bodies. | Non-blocker but important. | P0/P3 | `AGENTS.md`, `workspace_map.md`, `docs/archive_registry.md`, `architecture/history_lore.yaml` | docs/history-lore lane |
| Archive hygiene / secret contamination debt | Critical | Archive bodies include secret references and junk artifacts. | Blocks any naive archive promotion. | Always | archive handling only, not active repo bodies | manual scan + future secret scan tooling |

## Debt priorities

### P0-critical debt

- boot-surface visibility debt
- current_state overload debt
- visible-history interface debt
- graph underpromotion debt
- stale authority-claim language

### P1-critical debt

- hidden archive registry debt in topology
- context-budget debt
- map-maintenance debt
- any docs/test enforcement needed to make P0 sticky

### P2-critical debt

- graph wrapper portability debt
- graph previewability debt
- artifact-lifecycle updates for graph metadata sidecar

### P3 / later debt

- lore compression improvements
- selective stale-comment cleanup
- source-rationale presentation improvements

## Final triage conclusion

The repo's biggest problem is not lack of machine law.
It is that **the visible surface still narrates an older, thicker, more bureaucratic version of the system than the kernel actually implements**.
