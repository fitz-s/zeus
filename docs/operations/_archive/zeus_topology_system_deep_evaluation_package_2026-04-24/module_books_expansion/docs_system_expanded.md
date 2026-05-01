# Docs System Expanded Reference Draft

Status: Proposed expansion. Reference-only cognition.

## 1. Module purpose

The docs system governs how Zeus distinguishes authority, durable reference, current operations, reports, artifacts, to-do inventories, and archives.

## 2. What this module is not

- Not executable truth.
- Not a place to hide current facts in reference docs.
- Not an archive default-read path.
- Not a replacement for docs_registry.

## 3. Domain model

Docs classes:

- authority
- reference
- module_reference
- operations
- runbook
- generated_report
- checklist_evidence
- evidence_artifact
- historical_archive
- archive_interface

## 4. Runtime role

Docs guide agents and operators but do not trade.

## 5. Authority role

Authority docs are law only where classified. Reference docs are durable explanations. Current operations docs point to live work. Reports/artifacts are evidence.

## 6. Read/write surfaces and canonical truth

| Surface | Role |
|---|---|
| `docs/AGENTS.md` | Docs router |
| `docs/README.md` | Docs index |
| `docs/archive_registry.md` | Visible archive interface |
| `docs/authority/**` | Active authority docs |
| `docs/reference/**` | Durable reference |
| `docs/reference/modules/**` | Dense module cognition |
| `docs/operations/current_state.md` | Live control pointer |
| `docs/reports/**` | Generated evidence |
| `docs/artifacts/**` | Evidence artifacts |
| `docs/archives/**` | Cold historical evidence |
| `architecture/docs_registry.yaml` | Machine classification |
| `architecture/artifact_lifecycle.yaml` | Artifact/work record rules |
| `architecture/reference_replacement.yaml` | Reference replacement matrix |

## 7. Public interfaces

- `python3 scripts/topology_doctor.py --docs --json`
- docs registry entries
- docs AGENTS file registries
- current_state active pointer

## 8. Internal seams

### Reference vs current operations

Reference docs should not carry active packet state or volatile metrics.

### Archive interface vs archives

`docs/archive_registry.md` is visible. Archive bodies are not default-read.

### Docs AGENTS vs docs_registry

AGENTS files route humans. `docs_registry.yaml` owns machine classification.

## 9. Source files and roles

See table in section 6.

## 10. Relevant tests

- Docs registry field/enums.
- Archive default-read behavior.
- Current_state receipt binding.
- Operations task folder registration.
- Direct-reference leak checks.
- Removed reference path leak checks.
- Docs subtree AGENTS checks.

## 11. Invariants

- Archives are evidence, not live guidance.
- Current facts live in operations/current fact docs, not durable reference.
- Reports/artifacts do not become authority by being tracked.
- Docs root allowed files remain narrow.
- Module books are reference-only cognition.

## 12. Negative constraints

- Do not place volatile metrics in reference.
- Do not cite archives as active peer docs.
- Do not let reports replace authority.
- Do not store packet progress in module books.

## 13. Known failure modes

- Active packet history masquerades as live guidance.
- Reference docs become stale current-fact stores.
- Archive-rich repo becomes impossible for online-only agents.
- Report artifacts are treated as proof rather than evidence.

## 14. Historical lessons

Docs cleanup without cognition rehydration creates a tidy but hollow repo. Docs registry must classify, while module books explain.

## 15. Graph high-impact nodes

Docs system routes graph/module references but graph cannot classify docs authority.

## 16. Likely modification routes

- Add module book: docs registry + module manifest + reference AGENTS.
- Add report/artifact: local AGENTS + artifact lifecycle if tracked.
- Change current_state: receipt/work-record/current delivery checks.
- Change authority docs: planning lock.

## 17. Planning-lock triggers

- Authority docs changes.
- Docs classification policy changes.
- Archive default-read policy changes.
- Current_state role changes.
- Reference replacement policy changes.

## 18. Common false assumptions

- “Tracked docs are authority.” False.
- “Reference-only means optional.” False for cognition; true for authority rank.
- “Archives are useful, so read them first.” False.
- “current_state can carry all active context.” It should point, not sprawl.

## 19. Do-not-change-without-checking list

- docs_registry classification enums,
- archive interface,
- reference replacement entries,
- current_state receipt binding,
- docs root allowed files.

## 20. Verification commands

```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --reference-replacement --json
python scripts/topology_doctor.py --map-maintenance --changed-files <files> --json
```

## 21. Rollback strategy

Rollback docs registry, AGENTS, and new docs together. Do not leave unregistered docs.

## 22. Open questions

- Should docs_system book own all docs class definitions?
- Should docs_registry include book density metadata or should module_manifest own it?

## 23. Future expansion notes

Add examples of each doc class and prohibited placement patterns.

## 24. Rehydration judgment

Docs system should explain classification and placement so agents stop rediscovering where knowledge belongs.
