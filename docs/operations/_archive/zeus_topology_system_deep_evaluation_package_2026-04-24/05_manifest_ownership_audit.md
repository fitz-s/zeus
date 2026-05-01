# 05 — Manifest Ownership Audit

## Ruling

Ownership is good enough to be useful, but not clean enough to support future strictness. Zeus should write an explicit ownership matrix and teach topology_doctor to detect duplicate/conflicting ownership.

## Ownership matrix

| Surface | Should own | Should not own | Drift/duplication risk |
|---|---|---|---|
| `topology.yaml` | Root/zone/path coverage, top-level docs subroots, active pointer requirements, digest/core-map profiles, derived compiled topology inputs. | Per-file source rationale, test categories, script lifecycle, module book content, docs truth details. | Duplicates docs_registry and module_manifest if it tries to catalog every file; stale coverage roots can overroute. |
| `docs_registry.yaml` | Docs classification: doc_class, default_read, truth_profile, freshness_class, lifecycle, direct-reference behavior, parent coverage. | Module ownership, source write routes, test law gates, script safety, current packet status beyond classification. | Can become huge and duplicative with docs AGENTS and topology docs_subroots. |
| `module_manifest.yaml` | Module-to-book/scoped-AGENTS router; module hazards; high-risk files; law/current-fact/test links at module level; graph appendix status. | File-level source rationale, docs classification, script lifecycle, test categories. | Risks duplicating source_rationale/test_topology/docs_registry; placeholder fields can look authoritative before being mature. |
| `source_rationale.yaml` | Tracked src file rationale, zone, authority role, hazards, write routes, package defaults, source/scoped AGENTS coherence. | Module book prose, test classification, script metadata, docs classification. | May become an exhaustive source encyclopedia; missing upstream/downstream makes hidden routes. |
| `test_topology.yaml` | Test category, law-gate tests/protects, high-sensitivity skips, reverse-antibody status, relationship test manifests. | General module ownership, docs registry, source write routes. | Law gates can carry hidden architecture law without reference extraction. |
| `script_manifest.yaml` | Top-level script lifecycle/class/safety/write targets/apply flags/target DB/required tests. | Source code behavior, module books, docs classification. | Huge manifest can hide operational facts; script helper centrality not obvious. |
| `map_maintenance.yaml` | Companion update rules for added/deleted/renamed/modified surfaces; narrow registry ownership gate. | Broad prose rewrite obligations or global health. | Can overblock if mode/scoping not typed; required companions need repair hints. |
| `context_budget.yaml` | Default-read/budget posture, thin routers vs dense module books, advisory context bloat warnings. | Suppression of required authority reads or content classification. | Can be mistaken for hard read limits; should remain advisory unless promoted. |
| `artifact_lifecycle.yaml` | Artifact classes, liminal artifact roles, work-record contract, evidence vs authority behavior. | Docs registry classifications and packet status details. | Can overlap with docs_registry/artifacts AGENTS; closeout integration must be scoped. |
| `code_review_graph_protocol.yaml` | Graph use protocol, semantic-boot-before-graph, forbidden uses, verification gates, official graph commands. | Graph-derived findings, source truth, authority rank, settlement/source correctness. | Graph facts can be overtrusted unless output remains labeled derived. |
| `history_lore.yaml` | Failure modes, wrong moves, antibodies, residual risks, task routing lessons. | Active law that should be in authority, module book explanations that should be default discoverable. | Lore can become a noisy encyclopedia if not extracted into module books. |
| `invariants.yaml` | Invariant definitions and law IDs. | Implementation-specific route advice or current packet state. | Tests may enforce invariant meanings more concretely than docs. |
| `negative_constraints.yaml` | Forbidden moves/constraints. | Operational current state or packet exceptions. | May need mapping from negative constraint to repair route. |

## Clean ownership contract

### Topology compiles; it should not duplicate

`architecture/topology.yaml` should be the compiled/root routing index and digest profile owner. It should not become the canonical owner of every domain-specific detail.

### Domain manifests own domain facts

- Docs facts → `docs_registry.yaml`
- Source file rationale/write routes → `source_rationale.yaml`
- Test categories/law gates → `test_topology.yaml`
- Script lifecycle/write safety → `script_manifest.yaml`
- Module cognition routing → `module_manifest.yaml`
- Companion rules → `map_maintenance.yaml`
- Graph protocol → `code_review_graph_protocol.yaml`

### Module manifest routes cognition

`module_manifest.yaml` should answer: “which module book/scoped AGENTS/current facts/tests/high-risk files help me reason about this module?” It should not become a second source rationale or docs registry.

## Normalization moves

1. Add `canonical_owner` definitions to a new section in `module_books_expansion/manifests_system_expanded.md`.
2. Add validator checks for duplicate ownership claims.
3. Replace repeated lists with references to owner manifests where possible.
4. Keep `topology.yaml` as the compiler input/index, not the canonical owner of every path-level fact.
5. Require every blocking issue to name exactly one `owner_manifest`.

## Specific duplication risks

### `docs_registry.yaml` vs docs AGENTS

Docs AGENTS files are human local routers. `docs_registry.yaml` is machine classification. Both may list paths, but only docs_registry should own truth profile/default-read/current-tense classification.

### `module_manifest.yaml` vs `source_rationale.yaml`

Module manifest may list high-risk source files, but source rationale owns per-file why/zone/write-route facts. Module manifest should link to source rationale, not copy it.

### `module_manifest.yaml` vs `test_topology.yaml`

Module manifest may list required tests for module boot. Test topology owns law gate classification and test categories.

### `script_manifest.yaml` vs `source_rationale.yaml`

Script manifest owns top-level script lifecycle and safety. Source rationale owns `src/**` runtime/executable file rationale, not `scripts/**`.

### `map_maintenance.yaml` vs closeout policy

Map maintenance owns companion rules. Closeout decides whether a missing companion blocks a packet in the current mode.

## Recommendation

Create a manifest ownership ADR before broad manifest edits. Then implement a topology_doctor ownership validator that catches:

- two manifests claiming canonical ownership for the same fact type,
- a blocking issue without owner_manifest,
- a manifest entry that repeats data owned elsewhere without a reference/derivation marker.
