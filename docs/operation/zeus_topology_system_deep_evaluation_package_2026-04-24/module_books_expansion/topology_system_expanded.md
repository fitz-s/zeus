# Topology System Expanded Reference Draft

Status: Proposed expansion. Reference-only cognition. Not authority.

## 1. Module purpose

The topology system is Zeus's machine-readable governance and routing kernel. Its job is to help agents answer:

- What authority surfaces must be read?
- Which files are in scope?
- What companion updates are required?
- Which manifest owns a fact?
- Which tests and routes are relevant?
- Which failures block this mode?
- Which context is derived and non-authoritative?

It is not a replacement for executable source, blocking tests, DB truth, architecture law, or current operations pointers.

## 2. What this module is not

- Not runtime trading logic.
- Not semantic proof.
- Not a packet status log.
- Not an archive reader.
- Not a graph authority layer.
- Not a reason to skip planning lock.
- Not a justification to ignore source/tests.

## 3. Domain model

Topology consists of five interacting layers:

1. **Authority surfaces**: root AGENTS, architecture law, docs authority, executable source/tests.
2. **Routing manifests**: topology, docs registry, source rationale, test topology, script manifest, map maintenance, context budget, artifact lifecycle, module manifest, graph protocol.
3. **Validator lanes**: docs, source, tests, scripts, graph, reference, history lore, context packs, compiled topology, closeout.
4. **Mode policies**: navigation, closeout, strict/global health, packet prefill, context pack.
5. **Cognition surfaces**: module books and generated derived appendices.

## 4. Runtime role

Topology does not trade or mutate runtime truth. It reduces agent error by making hidden obligations explicit before code or docs changes are accepted.

## 5. Authority role

Topology indexes authority. It does not outrank authority.

When topology conflicts with source/tests/current authority, treat the conflict as a governance inconsistency requiring a packet, not as permission to ignore either side.

## 6. Read/write surfaces and canonical truth

### Machine surfaces

- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `architecture/docs_registry.yaml`
- `architecture/module_manifest.yaml`
- `architecture/source_rationale.yaml`
- `architecture/test_topology.yaml`
- `architecture/script_manifest.yaml`
- `architecture/map_maintenance.yaml`
- `architecture/context_budget.yaml`
- `architecture/artifact_lifecycle.yaml`
- `architecture/code_review_graph_protocol.yaml`

### Code surfaces

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_*.py`
- `tests/test_topology_doctor.py`

### Reference/cognition surfaces

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`
- `docs/reference/modules/code_review_graph.md`
- `docs/reference/modules/docs_system.md`

## 7. Public interfaces

- `python scripts/topology_doctor.py --navigation ...`
- `python scripts/topology_doctor.py closeout ...`
- `python scripts/topology_doctor.py --strict`
- `python scripts/topology_doctor.py --docs`
- `python scripts/topology_doctor.py --source`
- `python scripts/topology_doctor.py --tests`
- `python scripts/topology_doctor.py --scripts`
- `python scripts/topology_doctor.py --code-review-graph-status`
- `python scripts/topology_doctor.py context-pack ...`
- `python scripts/topology_doctor.py compiled-topology ...`

## 8. Internal seams

### Manifest compiler vs validators

Manifests define facts. Validators detect drift. Policy decides blocking.

### Route context vs repo health

Navigation route context must remain available even when unrelated global drift exists.

### Graph vs topology

Graph is structural context. Topology is manifest/router context. Neither is semantic proof.

### Reference cognition vs authority

Module books explain; manifests/authority decide.

## 9. Source files and their roles

| Surface | Role |
|---|---|
| `topology.yaml` | Root routing/index over authority surfaces |
| `docs_registry.yaml` | Machine docs classification |
| `module_manifest.yaml` | Module cognition router |
| `source_rationale.yaml` | Source file rationale/write routes |
| `test_topology.yaml` | Test classification/law gates |
| `script_manifest.yaml` | Script lifecycle and write safety |
| `map_maintenance.yaml` | Companion update rules |
| `context_budget.yaml` | Router/cognition density budget |
| `artifact_lifecycle.yaml` | Artifact/work-record classification |
| `code_review_graph_protocol.yaml` | Graph use protocol |
| `topology_doctor.py` | CLI facade and legacy policy hub |
| `topology_doctor_*` helpers | Validator/context/closeout subsystems |

## 10. Relevant tests

- Topology doctor lane tests.
- Docs registry tests.
- Source rationale tests.
- Test topology tests.
- Script manifest tests.
- Graph protocol/status tests.
- Context-pack tests.
- Closeout tests.
- Compiled topology contract tests.

## 11. Invariants

- Topology indexes authority; it does not create semantic truth.
- Route discovery must not be blocked by unrelated global drift.
- Closeout must block relevant changed-file obligations.
- Every blocking issue must have owner and repair path.
- Graph context is derived.
- Archives remain cold evidence.

## 12. Negative constraints

- Do not make topology doctor a universal strict gate for every task.
- Do not compress domain cognition solely into YAML.
- Do not treat module books as current packet state.
- Do not use graph to waive tests, planning lock, or current-fact reads.
- Do not bypass companion manifests for added/deleted files.

## 13. Known failure modes

1. Perfect router into a void: machine routes exist, but books are too thin.
2. Global-drift blockade: navigation fails on unrelated health debt.
3. Manifest duplication: two registries own the same fact.
4. Test-only law: a rule is enforced but not explainable.
5. Graph overtrust: structural proximity mistaken for semantic truth.
6. Archive leakage: cold historical evidence becomes live guidance.

## 14. Historical failures and lessons

Historical deep maps and packets contained richer system memory. The correct response is not archive default-read. The response is selective extraction into durable reference books and normalized manifests.

## 15. Graph high-impact nodes

Treat topology doctor helpers, module manifest, docs registry, map maintenance, and graph context-pack integration as high-impact topology nodes. Graph may help discover impact, but review must confirm semantics through authority and tests.

## 16. Likely modification routes

- Change under `architecture/**`: planning lock, manifest ownership, topology tests.
- Change topology doctor behavior: lane tests, issue model tests, module book updates.
- Add module book: docs registry, module manifest, docs/reference AGENTS.
- Add source/test/script: owner manifest and map maintenance companion.
- Change graph protocol: graph book, graph helper, context pack tests.

## 17. Planning-lock triggers

- `architecture/**`
- `scripts/topology_doctor*.py`
- docs authority changes
- route/blocking policy changes
- graph protocol/tracking changes
- module manifest ownership changes

## 18. Common false assumptions

- “Machine-readable” means “understandable.” False.
- “Graph knows impact” means “graph knows correctness.” False.
- “Navigation failed” means “task cannot start.” Not necessarily.
- “A passing closeout means global repo health is clean.” False.
- “Archives contain useful history, so read them by default.” False.

## 19. Do-not-change-without-checking list

- Issue schema
- Lane policy
- manifest ownership matrix
- graph authority boundaries
- archive default-read contract
- current_state receipt binding
- module book registration rules

## 20. Verification commands

```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -m "not live_topology"
python scripts/topology_doctor.py --navigation --task "route check" --files scripts/topology_doctor.py --json
python scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py --summary-only
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --source --json
python scripts/topology_doctor.py --tests --json
python scripts/topology_doctor.py --scripts --json
python scripts/topology_doctor.py --code-review-graph-status --json
```

## 21. Rollback strategy

Rollback topology changes atomically by packet. Never leave issue schema, lane policy, and tests out of sync.

## 22. Open questions

- Should issue schema be formalized in `topology_schema.yaml`?
- Which module-book checks should become blockers?
- How much graph text should be committed?

## 23. Future expansion notes

Add issue-code ownership tables, lane policy examples, and generated graph-sidecar examples after P0/P1.

## 24. Rehydration judgment

This module should become the durable explanation of the topology system. Keep it reference-only but dense enough that online-only agents can reason safely.
