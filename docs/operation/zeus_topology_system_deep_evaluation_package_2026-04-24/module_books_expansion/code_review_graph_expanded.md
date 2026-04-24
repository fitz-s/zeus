# Code Review Graph Expanded Reference Draft

Status: Proposed expansion. Derived context only. Not authority.

## 1. Module purpose

The Code Review Graph gives Zeus structural context: callers, dependencies, likely impacted tests, bridge files, hub nodes, and review order. It exists to reduce search cost and hidden-route misses.

## 2. What this module is not

- Not source truth.
- Not settlement truth.
- Not docs authority.
- Not planning-lock evidence.
- Not a reason to skip tests.
- Not a semantic proof engine.
- Not a write-capable refactoring tool in Zeus.

## 3. Domain model

- Tracked `.code-review-graph/graph.db`: SQLite structural map.
- Graph protocol: semantic boot first, graph second.
- Read-only facade: safe query interface.
- Topology doctor graph status: freshness/coverage/guard checks.
- Context-pack integration: derived appendices.

## 4. Runtime role

Graph does not affect runtime. It informs review and planning.

## 5. Authority role

Derived structural context only.

## 6. Read/write surfaces and canonical truth

| Surface | Role |
|---|---|
| `.code-review-graph/graph.db` | Tracked structural artifact, binary |
| `.code-review-graph/.gitignore` | Tracking policy |
| `architecture/code_review_graph_protocol.yaml` | Protocol/law for graph use |
| `scripts/topology_doctor_code_review_graph.py` | Health and impact extraction |
| `scripts/topology_doctor_context_pack.py` | Context-pack integration |
| `scripts/code_review_graph_mcp_readonly.py` | Read-only facade |
| `docs/reference/modules/code_review_graph.md` | Reference/cognition |

## 7. Public interfaces

```bash
code-review-graph status --repo <repo-root>
code-review-graph update --repo <repo-root>
python scripts/topology_doctor.py --code-review-graph-status --changed-files <files> --json
python scripts/topology_doctor.py context-pack --profile package_review --files <files> --json
```

## 8. Internal seams

### Semantic boot vs graph context

Always establish task class, authority surfaces, current facts, and fatal misreads before using graph routes.

### Local graph vs tracked graph

Tracked graph helps online/context workflows. Local graph status can be fresher. Always label freshness.

### Binary graph vs textual appendices

Binary graph is not enough for online-only agents. Commit or generate small textual extracts.

## 9. Source files and roles

| Surface | Role |
|---|---|
| `.code-review-graph/graph.db` | Derived structural context; not readable inline |
| `code_review_graph_protocol.yaml` | Allowed/forbidden use protocol |
| `topology_doctor_code_review_graph.py` | Status, limitations, impact extraction |
| `topology_doctor_context_pack.py` | Context-pack appendix integration |
| `tests/test_topology_doctor.py` | Graph protocol/status regression |
| module book | Durable explanation |

## 10. Relevant tests

Graph tests should cover:

- protocol required sections,
- semantic boot before graph,
- missing DB warning,
- untracked DB/ignore guard errors if policy requires tracking,
- stale graph warnings,
- changed file missing from graph warnings,
- derived-not-authority labeling,
- context-pack limitations.

## 11. Invariants

- Graph never decides authority.
- Graph never decides source/date/station truth.
- Graph stale/missing does not waive topology/semantic boot.
- Graph stale/missing should not block unrelated work.
- Graph-derived appendices must include limitations.

## 12. Negative constraints

- Do not untrack graph without packeted reason.
- Do not expose write-capable graph refactor tools.
- Do not create custom refresh machinery before official commands.
- Do not treat graph coverage as test coverage.

## 13. Known failure modes

1. Agent follows a graph edge and ignores source truth.
2. Online-only agent cannot read binary graph.
3. Stale graph is treated as semantic blocker.
4. Graph route hides missing manifest registration.
5. Graph output becomes unlabelled authority-like prose.

## 14. Historical lessons

Older textual deep maps were easier for online agents but less structurally precise. Current graph is structurally stronger but needs textual extraction.

## 15. Graph-derived textual appendices

Recommended appendices:

- `graph_hotspots`: top central files and why they matter.
- `graph_module_routes`: module clusters and bridge files.
- `graph_test_routes`: changed file to likely tests.
- `graph_limitations`: freshness, missing files, confidence.

Appendices should include:

```yaml
authority_status: derived_not_authority
generated_at: <timestamp>
repo_head: <sha>
graph_freshness: ok|stale|unknown
source_command: <command>
max_lines: <budget>
```

## 16. Likely modification routes

- Graph protocol changes require topology/graph book updates and tests.
- Context-pack graph changes require graph helper tests.
- Tracking policy changes require `.gitignore`, graph protocol, and review.

## 17. Planning-lock triggers

- Graph protocol changes.
- Graph tracking changes.
- Read-only facade changes.
- Topology doctor graph behavior changes.
- Any proposal to treat graph as blocking semantic evidence.

## 18. Common false assumptions

- “Graph says these files are near, so they share authority.” False.
- “Graph has tests_for, so no manual test reasoning required.” False.
- “Tracked graph.db is enough for online-only agents.” False.
- “Graph stale means task unsafe.” Not necessarily.

## 19. Do-not-change-without-checking list

- Read-only facade guarantees.
- Derived-not-authority label.
- Official graph command order.
- Context-pack limitation text.
- Graph freshness policy.

## 20. Verification commands

```bash
test -f .code-review-graph/graph.db
code-review-graph status --repo <repo-root>
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py context-pack --profile package_review --files <files> --json
pytest -q tests/test_topology_doctor.py -k "graph"
```

## 21. Rollback strategy

Revert graph protocol, helper, context-pack, and module book changes together.

## 22. Open questions

- Commit textual sidecars or generate on demand?
- What graph extract size is safe?
- How to represent graph confidence?

## 23. Future expansion notes

After P4, add sample graph appendices and issue metadata fields for graph freshness.

## 24. Rehydration judgment

Graph should become more useful to online-only agents through textual derived context, not through authority promotion.
