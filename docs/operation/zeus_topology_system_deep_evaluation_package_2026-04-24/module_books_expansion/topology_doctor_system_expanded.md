# Topology Doctor System Expanded Reference Draft

Status: Proposed expansion. Reference-only cognition.

## 1. Module purpose

Topology doctor is the executable checker/context compiler for the topology system. It reads manifests, validates drift, generates route/context outputs, and supports closeout.

## 2. What this module is not

- Not runtime logic.
- Not a source of authority by itself.
- Not a replacement for tests.
- Not a universal strict gate for every task.
- Not a graph semantic authority engine.

## 3. Domain model

Topology doctor has:

- loaders,
- validators,
- issue factory,
- lane policy,
- CLI/renderers,
- context-pack builders,
- graph bridge,
- closeout compiler,
- repair draft generator (proposed).

## 4. Runtime role

No trading/runtime mutation. Repo governance and route safety only.

## 5. Authority role

It operationalizes existing authority and manifests. It must not invent new law.

## 6. Read/write surfaces and canonical truth

| Surface | Role |
|---|---|
| `scripts/topology_doctor.py` | CLI facade/main issue model |
| `topology_doctor_cli.py` | parser/renderers |
| `topology_doctor_closeout.py` | closeout compilation |
| `topology_doctor_docs_checks.py` | docs/current_state/docs_registry checks |
| `topology_doctor_registry_checks.py` | root/docs strict/archive interface |
| `topology_doctor_source_checks.py` | source_rationale/AGENTS coherence |
| `topology_doctor_test_checks.py` | test_topology/law gates |
| `topology_doctor_script_checks.py` | script_manifest safety |
| `topology_doctor_map_maintenance.py` | companion rules |
| `topology_doctor_context_pack.py` | context packs/module books |
| `topology_doctor_code_review_graph.py` | graph status/impact |
| `tests/test_topology_doctor.py` | regression suite |

## 7. Public interfaces

CLI flags and subcommands are the public interface. JSON output must remain backward-compatible.

## 8. Internal seams

### Validators vs policy

Validators report typed issues. Policy decides blocking per mode.

### Closeout vs global health

Closeout blocks packet scope. Global health reports full drift.

### Route context vs repair draft

Route context orients an agent. Repair draft proposes owner-manifest changes.

## 9. Source files and roles

See section 6.

## 10. Relevant tests

- issue JSON compatibility,
- navigation advisory behavior,
- closeout scoped behavior,
- strict global health behavior,
- repair draft output,
- helper lane fixtures,
- live repo health marker.

## 11. Invariants

- Every blocking issue has owner and repair route.
- Navigation can return context under unrelated drift.
- Closeout does not hide relevant changed-file obligations.
- Graph output remains derived.
- Tests distinguish deterministic behavior from live drift.

## 12. Negative constraints

- Do not string-match issue codes for policy after typed issues exist.
- Do not make severity alone decide mode blocking.
- Do not auto-apply repair drafts.
- Do not let helper modules import each other into cycles.

## 13. Known failure modes

- Raw issue floods.
- Global drift blocks local packet.
- Path-only scoping misses companion obligations.
- Live repo tests fail topology code changes.
- Thin issues prevent automation.

## 14. Historical lessons

Topology doctor became large because Zeus needed many governance checks. The fix is not deletion; it is typed policy and better module cognition.

## 15. Graph high-impact nodes

Graph can identify topology doctor helper centrality, but helper semantics must be documented here.

## 16. Likely modification routes

- Issue model changes: tests + renderer + all helpers.
- Lane policy changes: navigation/closeout tests.
- Graph behavior: graph book/protocol/tests.
- Docs/source/test/script validators: owner manifest and module book updates.

## 17. Planning-lock triggers

Any topology doctor behavior change affecting routing, blocking, graph authority, closeout, or issue schema.

## 18. Common false assumptions

- “Topology doctor failed, so task is impossible.” False.
- “Topology doctor passed, so semantics are proven.” False.
- “Severity error always blocks every mode.” Should become false.
- “Global health is closeout.” False.

## 19. Do-not-change-without-checking list

- issue JSON keys,
- CLI mode names,
- closeout receipt behavior,
- graph authority labels,
- map maintenance companion rules.

## 20. Verification commands

```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
pytest -q tests/test_topology_doctor.py -m "not live_topology"
python scripts/topology_doctor.py --navigation --task "x" --files scripts/topology_doctor.py --json
python scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py --summary-only
```

## 21. Rollback strategy

Keep behavior changes small and packeted. Revert issue model/policy/tests as a unit.

## 22. Open questions

- Should repair drafts be a subcommand or lane?
- Should live health be `--strict` or `--global-health`?

## 23. Future expansion notes

Add issue-code ontology and repair-kind table.

## 24. Rehydration judgment

This book should become the readable map of topology_doctor’s internal responsibilities and lane semantics.
