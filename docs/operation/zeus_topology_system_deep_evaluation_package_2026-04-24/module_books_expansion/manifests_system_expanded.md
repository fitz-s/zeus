# Manifests System Expanded Reference Draft

Status: Proposed expansion. Reference-only cognition.

## 1. Module purpose

The manifests system explains what each `architecture/**` manifest owns, what it must not duplicate, and how topology_doctor should route repair.

## 2. What this module is not

- Not a new manifest.
- Not a second authority plane.
- Not permission to add YAML before deciding ownership.
- Not a replacement for existing manifests.

## 3. Domain model

A manifest owns a **fact type**. Fact types include:

- path coverage,
- doc classification,
- source rationale,
- test classification,
- script lifecycle,
- map companion rules,
- module cognition routing,
- graph protocol,
- context budget,
- artifact lifecycle,
- invariant/negative constraint law.

## 4. Runtime role

Manifests do not trade. They govern how agents change the repo safely.

## 5. Authority role

Machine manifests are high in routing/constraint authority, but they do not override executable behavior or constitutional law.

## 6. Read/write surfaces and canonical truth

| Manifest | Canonical fact type |
|---|---|
| `topology.yaml` | Root routing/path coverage/digest inputs |
| `docs_registry.yaml` | Docs classification/default-read/freshness/truth profile |
| `module_manifest.yaml` | Module-to-book/scoped AGENTS/current fact/test routing |
| `source_rationale.yaml` | Source file rationale/zone/write routes/hazards |
| `test_topology.yaml` | Test categories/law gates/skips/reverse antibodies |
| `script_manifest.yaml` | Script lifecycle/class/write safety |
| `map_maintenance.yaml` | Companion update rules |
| `context_budget.yaml` | Context size/read posture |
| `artifact_lifecycle.yaml` | Artifact/work-record classification |
| `code_review_graph_protocol.yaml` | Graph allowed/forbidden use |
| `history_lore.yaml` | Failure memory/antibodies |
| `invariants.yaml` | Invariant law IDs |
| `negative_constraints.yaml` | Forbidden moves |
| `topology_schema.yaml` | Compiled topology/issue schema if extended |

## 7. Public interfaces

- topology_doctor lanes,
- manifest YAML files,
- context packs,
- closeout.

## 8. Internal seams

### Canonical owner vs derived reference

A manifest may point to another fact but should not own it.

### Machine compactness vs cognition

Manifests should be compact enough for automation. Module books should carry explanation.

### Warning-first maturity

New manifest fields should be warning-first until repair routes and tests stabilize.

## 9. Source files and roles

See section 6.

## 10. Relevant tests

- strict manifest schema tests,
- docs/source/test/script lanes,
- map maintenance companion tests,
- module manifest/book checks,
- ownership conflict tests after P3.

## 11. Invariants

- One canonical owner per fact type.
- Every blocking issue names owner_manifest.
- A manifest entry must declare derivation if it repeats another manifest’s fact.
- Topology compiles, it does not duplicate every domain fact.

## 12. Negative constraints

- Do not create parallel registries.
- Do not hide prose cognition in YAML.
- Do not use module_manifest as a file-level source rationale.
- Do not let docs_registry own module-level law dependencies.
- Do not let script_manifest authorize unsafe writes by declaration alone.

## 13. Known failure modes

- Manifest sprawl.
- Conflicting ownership.
- Placeholder fields mistaken for mature law.
- Huge registries unreadable by online-only agents.
- Repair issues with no owner.

## 14. Historical lessons

Zeus has repeatedly solved drift by adding registries. The next step must be ownership normalization, not another registry.

## 15. Graph high-impact nodes

Graph can help identify high-centrality manifests, but ownership remains manifest/schema-based.

## 16. Likely modification routes

- Add new doc: docs_registry.
- Add module book: docs_registry + module_manifest.
- Add source file: source_rationale.
- Add test: test_topology.
- Add script: script_manifest.
- Add architecture manifest: architecture/AGENTS + workspace_map.

## 17. Planning-lock triggers

Any `architecture/**` manifest change.

## 18. Common false assumptions

- “If two manifests mention a file, both own the same fact.” False.
- “Machine data is enough for agents.” False.
- “A manifest entry permits the action.” False; it classifies and routes.

## 19. Do-not-change-without-checking list

- canonical owner table,
- issue owner_manifest mapping,
- map maintenance rules,
- module_manifest field maturity,
- docs_registry default-read rules.

## 20. Verification commands

```bash
python scripts/topology_doctor.py --strict --json
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --source --json
python scripts/topology_doctor.py --tests --json
python scripts/topology_doctor.py --scripts --json
python scripts/topology_doctor.py --map-maintenance --changed-files <files> --json
```

## 21. Rollback strategy

Rollback ownership schema and affected manifest edits together. Do not leave split ownership.

## 22. Open questions

- Should ownership be represented in `topology_schema.yaml`?
- Should every issue code have an owner table?

## 23. Future expansion notes

Add issue-code-to-owner manifest table after P1.

## 24. Rehydration judgment

This book should make manifest ownership obvious enough that agents stop adding duplicate registries.
