# Code Review Graph Authority Book

**Recommended repo path:** `docs/reference/modules/code_review_graph.md`
**Current code path:** `.code-review-graph/** + graph protocol tooling`
**Authority status:** Dense system reference for Zeus's tracked structural-context engine. This is derived context, never semantic authority.

## 1. Module purpose
Explain what the tracked Code Review Graph is allowed to do for Zeus, what it cannot do, and why its presence requires additional textual extraction for online-only agents.

## 2. What this module is not
- Not a source of settlement, source-routing, or current-fact truth.
- Not an override for law, tests, manifests, or receipts.
- Not a human-readable module knowledge base in its current binary-only form.

## 3. Domain model
- Tracked `graph.db` as a SQLite structural map.
- MCP read-only facade and topology doctor graph checks.
- Semantic boot first, graph second protocol.
- Blast radius, impact radius, test relationships, and architecture overview.

## 4. Runtime role
Used during review, exploration, and context packing to reduce token waste and improve structural discovery.

## 5. Authority role
Derived structural context only. Zeus's own protocol forbids graph output from deciding authority rank, source truth, current-fact freshness, or settlement semantics.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `.gitignore` and `.code-review-graph/.gitignore` tracking rules
- `architecture/code_review_graph_protocol.yaml`
- `scripts/code_review_graph_mcp_readonly.py`
- `scripts/topology_doctor_code_review_graph.py` and `scripts/topology_doctor_context_pack.py`
- Upstream `code-review-graph` README/AGENTS/CLAUDE docs

### Non-authority surfaces
- Any semantic inference about source/date/station correctness drawn from graph edges alone
- Human guesses based on a non-rendered binary blob

## 7. Public interfaces
- Tracked `.code-review-graph/graph.db` artifact
- Read-only graph MCP facade
- Topology-doctor/context-pack graph-aware routes

## 8. Internal seams
- Semantic boot vs graph context stage
- Local graph build/update vs tracked online artifact
- Graph binary vs textual context-pack appendices

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `.code-review-graph/graph.db` | Tracked online structural artifact; 28 MB blob not renderable inline on GitHub. |
| `.code-review-graph/.gitignore` | Graph tracking policy surface. |
| `architecture/code_review_graph_protocol.yaml` | Current Zeus graph law. |
| `scripts/code_review_graph_mcp_readonly.py` | Zeus-safe read-only facade. |
| `scripts/topology_doctor_code_review_graph.py / topology_doctor_context_pack.py` | Checks and context extraction surfaces. |

## 10. Relevant tests
- tests/test_topology_doctor.py
- future dedicated graph-protocol tests once module-book integration lands

## 11. Invariants
- Graph requires semantic boot first.
- Graph is warning/fallback-friendly when stale or unavailable.
- Graph must never determine source validity, settlement truth, authority rank, or planning-lock waiver.

## 12. Negative constraints
- Do not untrack `graph.db` unless a concrete severe security/integrity blocker exists.
- Do not make graph output default-read semantic law.
- Do not expose write-capable graph refactoring tools in Zeus's read-only facade.

## 13. Known failure modes
- Agents overtrust structural proximity and ignore semantic boot.
- Tracked graph exists but online-only agents cannot directly inspect the binary, so its value is under-realized.
- Stale graph or missing `changed_files` makes review slower/noisier but not semantically safer.

## 14. Historical failures and lessons
- [Archive evidence] Older deep maps functioned as textual structural memory. Today's tracked graph is more powerful structurally, but without textual extraction it is less reviewer-readable.

## 15. Code graph high-impact nodes
- Confirmed by repo/docs: graph is intended to surface blast radius, callers, dependents, and tests.
- Upstream project additionally supports hub/bridge detection, architecture overview, and knowledge-gap analysis, but Zeus has not yet extracted those insights into text surfaces.

## 16. Likely modification routes
- Any change to graph protocol or tracking policy: review authority docs, topology doctor, and graph facade together.
- Module-book integration should add graph-derived appendices rather than broaden graph authority.

## 17. Planning-lock triggers
- Any change to tracking policy, graph protocol, read-only facade, or topology doctor graph behavior.

## 18. Common false assumptions
- If the graph knows who calls what, it knows which weather source is correct.
- A tracked blob on GitHub is sufficient onboarding context for online-only agents.
- Graph freshness is a semantic blocker rather than a structural-context quality issue.
- A repo-local MCP facade automatically means official upstream auto-refresh is installed and healthy.

## 19. Do-not-change-without-checking list
- Tracked/not-tracked policy for graph.db
- Read-only guarantee of the MCP facade
- Forbidden-inference rules in graph protocol

## 20. Verification commands
```bash
test -f .code-review-graph/graph.db
code-review-graph status --repo <repo-root>
code-review-graph update --repo <repo-root>
python -m py_compile scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py
pytest -q tests/test_topology_doctor.py
LOCAL_GRAPH_QUERY_REQUIRED: run local graph status/build/update checks during P3/P4
```

## 21. Rollback strategy
Rollback graph-policy packets with protocol/tooling changes together; never leave mixed graph authority assumptions in docs and scripts.

## 22. Open questions
- Should Zeus track a small human-readable graph sidecar (hotspots/tests/communities) alongside `graph.db`?
- Should topology doctor expose a `--module-books` or `--graph-appendix` lane?

## 23. Future expansion notes
- Add graph-derived textual appendices to module books and context packs.
- Add a machine-readable graph-hotspots summary generated locally and committed if it stays small and safe.

## 23a. Official Operation Order
- Prefer official upstream graph operations over repo-local refresh inventions.
- First check freshness with `code-review-graph status --repo <repo-root>`.
- If a one-shot refresh is needed, use `code-review-graph update --repo <repo-root>`.
- If continuous freshness is required, prefer official platform install hooks,
  then `code-review-graph watch`, then `code-review-graph daemon start`.
- Zeus guidance should integrate those commands; it should not replace them
  with custom watcher scripts or skill edits.

## 24. Rehydration judgement
This book is the dense reference layer for code review graph. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
