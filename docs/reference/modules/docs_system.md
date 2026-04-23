# Docs System Authority Book

**Recommended repo path:** `docs/reference/modules/docs_system.md`
**Current code path:** `docs/**`
**Authority status:** Dense system reference for the docs mesh: authority, reference, operations, reports, runbooks, and archive interface.

## 1. Module purpose
Explain how Zeus documentation should be layered so online-only agents can understand the system without turning docs into a swamp or a second uncontrolled codebase.

## 2. What this module is not
- Not a license to put everything in `docs/authority/`.
- Not a permission slip for manual current-fact narratives to become hidden truth.
- Not a reason to reactivate archives as ambient context.

## 3. Domain model
- Root docs router and docs registry.
- Durable law under `docs/authority/`.
- Dense durable explanation under `docs/reference/`.
- Time-bound operations/current-fact surfaces under `docs/operations/`.
- Evidence/history under `docs/reports/` and `archive_registry.md`.

## 4. Runtime role
Docs do not trade, but they control how humans and agents form the mental model that then changes code and operations.

## 5. Authority role
Docs must separate law, explanation, current facts, and evidence. Current repo direction is better than before, but authority was compacted faster than cognition was rebuilt.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/authority/zeus_current_architecture.md`, `zeus_current_delivery.md`, `zeus_change_control_constitution.md`
- `docs/README.md`, `docs/AGENTS.md`, `docs/reference/AGENTS.md`, `docs/operations/AGENTS.md`, `docs/archive_registry.md`
- `architecture/docs_registry.yaml`

### Non-authority surfaces
- Closed packet docs
- Reports/history surfaces
- Archive bodies
- Derived graph or context-pack output

## 7. Public interfaces
- Root docs router (`docs/README.md` and `docs/AGENTS.md`)
- Authority docs
- Reference docs
- Operations current facts and packet folders
- Archive registry

## 8. Internal seams
- Authority vs reference vs operations vs reports/history
- Current-fact docs vs receipt-backed audit packets
- Archive interface vs archive bodies

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `docs/authority/*.md` | Core law; currently still contaminated by side-authority residue. |
| `docs/reference/*.md` | Current durable explanations; currently too thin except a few files. |
| `docs/operations/current_state.md / current_data_state.md / current_source_validity.md / known_gaps.md` | Current fact surfaces and live packet routing. |
| `docs/README.md / docs/AGENTS.md / docs/archive_registry.md` | Root routes and historical interface. |
| `docs/reports/authority_history/**` | Demoted historical governance evidence. |

## 10. Relevant tests
- docs lanes in topology doctor and `tests/test_topology_doctor.py`

## 11. Invariants
- `docs/authority/` should contain durable law only.
- Reference carries explanation, not current counts or stale packet truth.
- Operations current facts are receipt-backed, expiry-bound, and fail-closed when stale.
- Archives remain historical-only and non-default.

## 12. Negative constraints
- Do not leave `task_YYYY-MM-DD_*`, ADRs, or fix-pack notes in authority.
- Do not rely on hand-maintained current-fact numbers without reproducible audit evidence.
- Do not let root docs or AGENTS become bloated narrative tombs.

## 13. Known failure modes
- Authority is clean but too sparse, leaving zero-context agents unable to reason.
- Reference is too thin and points back into code without enough semantic explanation.
- current_state points at an old packet while repo reality has moved on.
- Current data/source docs become stale bombs because they are manually maintained.

## 14. Historical failures and lessons
- [Archive evidence] The deep map, legacy truth-surface audit, strategy/exit analyses, and position-centric blueprint contain durable lessons that were stripped out of active docs faster than they were re-extracted.
- [Archive evidence] Governance restructuring materials show that decontamination is necessary but insufficient; rehydration must follow.

## 15. Code graph high-impact nodes
- Docs are not a graph hub in the code sense, but the docs system is the human boot hub. Missing module books are therefore a first-order cognition gap.

## 16. Likely modification routes
- Authority cleanup: extract rules into core law/module books/manifests, then demote residue.
- Reference rehydration: add module books and enrich system references without bloating authority.
- Operations rewrite: current_state becomes receipt-bound pointer only; current facts become generated or strongly audit-backed.

## 17. Planning-lock triggers
- Any change to authority order, docs classification, demotion/promotion, or current-fact semantics.

## 18. Common false assumptions
- Smaller docs are automatically better docs.
- Root routers plus thin AGENTS are enough for online-only agents.
- Current facts can remain manually curated forever.

## 19. Do-not-change-without-checking list
- Authority order and doc classification without packeted governance
- Archive interface rules
- Current-fact fail-closed policy

## 20. Verification commands
```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --current-state-receipt-bound --json
python scripts/topology_doctor.py --context-budget --json
pytest -q tests/test_topology_doctor.py
```

## 21. Rollback strategy
Rollback docs-governance packets as routed bundles. Partial demotion or partial rehydration creates the exact ambiguity this package is trying to remove.

## 22. Open questions
- Should `docs/reference/zeus_*_reference.md` remain thin routers once module books exist, or should some be expanded into larger system references?
- Should current_state be generated from receipt/packet metadata rather than handwritten?

## 23. Future expansion notes
- Create `docs/reference/modules/` as the dense cognition layer beneath authority and above packet evidence.
- Add a docs-system context pack that surfaces the right law/reference/current-fact layers for a given task.

## 24. Rehydration judgement
This book is the dense reference layer for docs system. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
