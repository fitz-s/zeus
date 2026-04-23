# Topology System Authority Book

**Recommended repo path:** `docs/reference/modules/topology_system.md`
**Current code path:** `architecture/** + topology_doctor family`
**Authority status:** Dense system reference for Zeus's machine-readable governance kernel.

## 1. Module purpose
Explain how the architecture manifests, topology doctor, invariants, and self-check files govern routing, constraints, and machine enforcement—while also explaining why the current kernel is too compressed to carry full system context alone.

## 2. What this module is not
- Not a substitute for module-level dense reference.
- Not semantic proof of runtime/source truth by itself.
- Not a place to encode current packet status or historical commentary.

## 3. Domain model
- Zone boundaries and negative constraints.
- Invariants and history lore.
- Docs registry, task boot profiles, fatal misreads, city truth contract, graph protocol.
- Source rationale, test topology, script manifest, context budget, map maintenance.
- Topology doctor as compiled enforcement layer.

## 4. Runtime role
Topology system doesn't trade, but it constrains how repo truth, planning, routing, and derived context are allowed to work.

## 5. Authority role
This is the machine kernel beneath human authority docs. It should stay primary for enforceable routing/constraint surfaces, but it is currently too thin to serve as deep onboarding for online-only agents.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `architecture/invariants.yaml`, `negative_constraints.yaml`, `zones.yaml`
- `architecture/topology.yaml`, `docs_registry.yaml`, `task_boot_profiles.yaml`, `fatal_misreads.yaml`, `city_truth_contract.yaml`, `code_review_graph_protocol.yaml`
- `scripts/topology_doctor.py` and its helper modules
- `tests/test_topology_doctor.py`

### Non-authority surfaces
- Current packet docs
- Archive deep maps unless re-extracted
- Graph output without semantic boot

## 7. Public interfaces
- Topology doctor CLI lanes
- Machine-readable manifests under `architecture/**`

## 8. Internal seams
- Law manifests vs routing manifests vs current-fact docs
- Human authority docs vs machine manifests
- Graph protocol vs topology routing

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `invariants.yaml / negative_constraints.yaml / zones.yaml` | Hard semantic kernel. |
| `topology.yaml / docs_registry.yaml / map_maintenance.yaml / context_budget.yaml` | Routing and hygiene layer. |
| `task_boot_profiles.yaml / fatal_misreads.yaml / city_truth_contract.yaml / code_review_graph_protocol.yaml` | Semantic boot and anti-misread layer. |
| `source_rationale.yaml / test_topology.yaml / script_manifest.yaml` | Current thin catalogs that need expansion. |
| `scripts/topology_doctor*.py` | Enforcement engine. |
| `tests/test_topology_doctor.py` | Primary regression suite for this kernel. |

## 10. Relevant tests
- tests/test_topology_doctor.py
- plus any architecture/law test that asserts manifest-backed behavior

## 11. Invariants
- Machine manifests outrank reference prose for routing/constraint decisions.
- Graph/topology are derived context, not semantic proof.
- Authority hygiene, planning lock, and map maintenance are enforceable concerns.

## 12. Negative constraints
- Do not compress manifests so far that only the author understands them.
- Do not hide module knowledge exclusively in packet docs or graph blobs.

## 13. Known failure modes
- Kernel becomes a perfect router into a void because module cognition is missing.
- Compressed manifests become unreadable checkboxes instead of useful machine context.
- Current-state/doc registry drift lets packet history masquerade as live guidance.

## 14. Historical failures and lessons
- [Archive evidence] `zeus-architecture-deep-map.md` shows how much richer system memory once existed than today's compressed manifests provide.
- [Archive evidence] Authority-governance restructuring materials prove that cleaning surfaces without rehydrating context creates a new failure mode: a tidy but cognitively hollow repo.

## 15. Code graph high-impact nodes
- `scripts/topology_doctor.py` and helpers are the operational heart of the topology system.
- The next missing node is `architecture/module_manifest.yaml`, which should exist but does not yet.

## 16. Likely modification routes
- Any manifest or topology doctor change should be packeted, tested, and paired with docs updates.
- Module-book support should be added here before broad module rehydration lands.

## 17. Planning-lock triggers
- Any change under `architecture/**` or `scripts/topology_doctor*.py`.
- Any authority-order, docs-classification, or routing change.

## 18. Common false assumptions
- A thin machine manifest is automatically superior to a thick human reference.
- Topology doctor can replace module understanding.
- If the machine lane passes, a zero-context agent can reason safely.

## 19. Do-not-change-without-checking list
- Invariant IDs and meanings without explicit migration
- Planning-lock criteria
- Graph protocol authority boundaries

## 20. Verification commands
```bash
pytest -q tests/test_topology_doctor.py
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <plan> --json
python -m py_compile scripts/topology_doctor*.py
```

## 21. Rollback strategy
Rollback topology-manifest packets atomically; partial routing/doctor changes can strand the repo in an inconsistent state.

## 22. Open questions
- How should `architecture/module_manifest.yaml` interact with existing source_rationale/test_topology/script_manifest files?
- Should topology doctor gain a dedicated `--module-books` lane?

## 23. Future expansion notes
- Create `architecture/module_manifest.yaml` and teach topology doctor/context-pack to surface module books and graph appendices.
- Expand source_rationale/test_topology/script_manifest into real catalogs instead of ultra-thin summaries.

## 24. Rehydration judgement
This book is the dense reference layer for topology system. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
