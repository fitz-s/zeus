# 09 — Module Book Density Audit

## Ruling

Module books are the right cognition layer, but the current system books are too thin for strong online-only reasoning.

The repo has many module books, including `topology_system.md`, `code_review_graph.md`, and `docs_system.md`. The presence of books is not enough. The system-level books must explain ownership, obligations, failure modes, validation, and modification routes in enough detail that an online-only agent does not have to reverse-engineer the topology doctor tests.

## Audit table

| Surface | Current adequacy | Missing density | Evidence elsewhere | Recommended home |
|---|---:|---|---|---|
| `docs/reference/modules/topology_system.md` | Medium-thin | Lane policy, issue model, ownership matrix, repair routes, closeout scoping, hidden obligations | topology_doctor code/tests, uploaded plan, map maintenance | Expand existing book |
| `docs/reference/modules/code_review_graph.md` | Medium-thin | Textual sidecar policy, graph extraction patterns, context-pack use, limitations, official operations | graph protocol, graph helper, context-pack helper, tests | Expand existing book |
| `docs/reference/modules/docs_system.md` | Present but needs topology-system alignment | Docs registry ownership, archive interface, current_state binding, reference replacement, operations docs lifecycle | docs checks/tests/current_state | Expand as system book |
| `docs/reference/modules/scripts.md` | Present but script system still needs manifest linkage | Script_manifest ownership, lifecycle classes, dangerous-write safety, repair routes | script_manifest, script checks | Expand or cross-link |
| `docs/reference/modules/tests.md` | Present but topology law needs extraction | Law-gate taxonomy, live-vs-fixture split, reverse antibodies, high-sensitivity skips | test_topology, tests | Expand or create topology-test section |
| Missing `manifests_system.md` | Missing | Ownership matrix for all manifests, duplication rules, canonical owner per fact type | architecture/AGENTS, manifests, topology_doctor | Add |
| Missing `topology_doctor_system.md` | Missing | Lane model, issue model, CLI contract, helper module ontology, repair drafts | scripts/topology_doctor*.py, tests | Add |
| Missing `closeout_and_receipts_system.md` | Missing | Changed-file gates, receipts, work records, planning lock, current_state binding | closeout helper, artifact_lifecycle, current delivery | Add |

## Scoped AGENTS density

Scoped AGENTS are useful routers but too thin to serve as standalone online-only onboarding. They should remain slim. Do not bloat AGENTS files. Route from AGENTS to dense module books.

## How much density should be restored?

For system books, target density should be enough to answer:

1. What does this system own?
2. What does it not own?
3. What are the public interfaces?
4. What hidden obligations exist?
5. What manifests/tests/scripts encode its rules?
6. What changes require planning lock?
7. What graph/context-pack support exists?
8. What common false assumptions are dangerous?
9. What commands validate it?
10. What repair routes exist?

A good system book can be 300–700 lines if needed. The context budget should keep routers thin, not keep cognition thin.

## Required module/system book additions

Use the six proposed expansions in `module_books_expansion/` as source material:

- `topology_system_expanded.md`
- `code_review_graph_expanded.md`
- `docs_system_expanded.md`
- `manifests_system_expanded.md`
- `topology_doctor_system_expanded.md`
- `closeout_and_receipts_system_expanded.md`

## Important distinction

Module books should be dense reference/cognition, not current packet state and not authority. Their job is to reduce zero-context reasoning failures.
