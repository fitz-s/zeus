# Future Authority Order Rewrite

## Two axes must be separated

Zeus currently mixes two different questions:

1. **What is behavior truth right now?**
2. **What grants permission and routing for repo changes?**

V2 resolves this by separating:

- **Behavior-truth order** — runtime contracts, source code, DB truth, projections.
- **Workspace-authority order** — machine manifests, active packets, boot surfaces, derived context, archives.

## A) Behavior-truth order

1. **Live canonical truth surfaces and executable contracts**
2. **Source code that implements present-tense behavior**
3. **Runtime/generated projections explicitly classified as non-authority**
4. **Docs that describe the system**
5. **Historical evidence / archives**

## B) Workspace-authority order

1. **System / developer / user instructions**
2. **Machine-checkable workspace law** (`architecture/**`, topology schema, lifecycle, manifests, tests)
3. **Active packet control surfaces** (`docs/operations/current_state.md`, active packet docs, receipts)
4. **Human boot/routing surfaces** (`AGENTS.md`, `workspace_map.md`, `docs/README.md`, scoped `AGENTS.md`)
5. **Derived context engines** (topology_doctor digests, context packs, Code Review Graph, source rationale, history lore)
6. **Historical evidence / archives**

The mistake in the current system is not the existence of prose.
The mistake is that prose still over-narrates history and under-narrates derived context.

## Layer-by-layer rewrite

### Layer 1 — Root `AGENTS.md`

- **Current role:** repo operating brief and default start surface.
- **Problem with current role:** correct on non-authority graph policy, but still too procedural and insufficiently explicit about the runtime/workspace split and visibility classes.
- **Proposed role:** thin boot contract; define Zeus as runtime machine + workspace machine; define the read order; define authority vs context vs history; route to manifests and active packet.
- **Files affected:** `AGENTS.md`
- **Why this improves online Pro/Codex work:** cold-start agents get the right model in one file instead of inferring it from scattered docs.
- **Risk if changed:** some older prose routes may break and require scoped follow-up edits.
- **Risk if not changed:** every future agent keeps over-reading docs and under-using the actual kernel.

### Layer 2 — `workspace_map.md`

- **Current role:** placement guide and repo directory map.
- **Problem with current role:** it distinguishes archives and graph correctly, but still reads like a file-placement map more than a visibility/availability router.
- **Proposed role:** make it the repo's visibility matrix: tracked-visible text, tracked-derived artifacts, ignored-local history, runtime-local scratch, and generated evidence.
- **Files affected:** `workspace_map.md`
- **Why this improves online Pro/Codex work:** prevents hidden-local surfaces from masquerading as default repo context.
- **Risk if changed:** none beyond doc drift if not kept aligned with topology.
- **Risk if not changed:** online agents will continue to confuse “exists somewhere” with “visible and default-readable.”

### Layer 3 — `docs/operations/current_state.md`

- **Current role:** live status, packet registry, runtime pointer, branch diary, and partial handoff surface.
- **Problem with current role:** it is too thick and therefore too unstable; it tries to be both pointer and narrative.
- **Proposed role:** live control pointer only — active packet, branch/program, required evidence pointers, next action, and closeout state.
- **Files affected:** `docs/operations/current_state.md`, `docs/operations/AGENTS.md`
- **Why this improves online Pro/Codex work:** future agents can answer “what is active now?” without inheriting runtime PID noise or hidden archive routes.
- **Risk if changed:** some operators may miss old convenience notes until those are moved to the right surfaces.
- **Risk if not changed:** `current_state.md` continues to rot fastest and mislead the next packet.

### Layer 4 — Architecture manifests

- **Current role:** highest durable workspace law.
- **Problem with current role:** strong semantics, but not all manifests fully encode online visibility as a first-class rule.
- **Proposed role:** remain highest durable workspace law; explicitly encode visible vs hidden history, graph sidecar policy, current_state thinness, and map-maintenance companions.
- **Files affected:** `architecture/topology.yaml`, `architecture/topology_schema.yaml`, `architecture/artifact_lifecycle.yaml`, `architecture/context_budget.yaml`, `architecture/map_maintenance.yaml`
- **Why this improves online Pro/Codex work:** the repo stops depending on humans to remember what is default-visible.
- **Risk if changed:** topology schema and tests may need synchronized updates.
- **Risk if not changed:** repaired prose can drift back out of sync with machine law.

### Layer 5 — Scoped `AGENTS.md` files

- **Current role:** directory-level routing and file registry.
- **Problem with current role:** some are accurate (`runbooks/AGENTS.md`), but docs-root still routes to hidden archives as if they were a live sibling subtree.
- **Proposed role:** keep scoped AGENTS as local routers only; remove hidden archive routing from visible docs-root surfaces; do not widen them into constitutional files.
- **Files affected:** `docs/AGENTS.md`, `docs/operations/AGENTS.md`, later only targeted scoped AGENTS as needed.
- **Why this improves online Pro/Codex work:** directory routers become truthful, small, and stable.
- **Risk if changed:** low.
- **Risk if not changed:** local-only paths continue to leak into online boot flow.

### Layer 6 — Code Review Graph (`.code-review-graph`)

- **Current role:** tracked derived online context artifact, explicitly non-authority.
- **Problem with current role:** formally classified correctly, but culturally underpromoted; one Zeus wrapper is also non-portable because it hardcodes a local repo root.
- **Proposed role:** first-class derived context engine; keep `graph.db` tracked; add optional tracked `graph_meta.json` sidecar in P2; keep read-only safety boundary; require freshness/usability labeling.
- **Files affected:** `.code-review-graph/graph.db`, `.code-review-graph/.gitignore`, `.gitignore`, `scripts/code_review_graph_mcp_readonly.py`, `scripts/topology_doctor_code_review_graph.py`, `scripts/topology_doctor_context_pack.py`, `architecture/artifact_lifecycle.yaml`
- **Why this improves online Pro/Codex work:** gives future agents structural retrieval without pretending the graph is law.
- **Risk if changed:** graph tooling/testing complexity increases slightly.
- **Risk if not changed:** Zeus keeps paying the cost of tracked graph data without fully cashing in its value.

### Layer 7 — `docs/archives` and historical bundles

- **Current role:** historical evidence only; ignored locally; partially still referenced by visible docs prose.
- **Problem with current role:** policy is conceptually right, but visible interface is missing; archive bodies are noisy and unsafe as default context.
- **Proposed role:** remain historical-only; never default-read; visible interface becomes `docs/archive_registry.md` plus `architecture/history_lore.yaml`.
- **Files affected:** `docs/archive_registry.md`, `docs/README.md`, `docs/AGENTS.md`, `workspace_map.md`, `architecture/history_lore.yaml` (later)
- **Why this improves online Pro/Codex work:** history remains available without corrupting the boot path.
- **Risk if changed:** low, as long as archive bodies remain untouched.
- **Risk if not changed:** online readers will keep being routed toward invisible or unsafe history.

### Layer 8 — Runtime/generated artifacts

- **Current role:** mixed set of state, reports, evidence, and local scratch.
- **Problem with current role:** docs sometimes still blur runtime-local, tracked-derived, and historical-retired artifacts.
- **Proposed role:** keep explicit lifecycle classes: runtime-local, tracked-derived, evidence-only, retired-historical.
- **Files affected:** `architecture/artifact_lifecycle.yaml`, `workspace_map.md`, targeted docs references.
- **Why this improves online Pro/Codex work:** agents stop treating local scratch or reports as stable authority.
- **Risk if changed:** low.
- **Risk if not changed:** projection surfaces can still creep upward in authority.

### Layer 9 — Active packet docs

- **Current role:** task-scoped change contracts.
- **Problem with current role:** sometimes too much permanent routing falls into packet prose and later leaks into `current_state.md`.
- **Proposed role:** packet docs own temporary scope/evidence only; completed packets leave the active surface.
- **Files affected:** `docs/operations/task_*`, `docs/operations/current_state.md`, archive packet routes
- **Why this improves online Pro/Codex work:** active change control becomes legible and temporary again.
- **Risk if changed:** minimal.
- **Risk if not changed:** packet sediment continues to thicken the active docs mesh.

### Layer 10 — Tests

- **Current role:** enforce topology_doctor and graph policy behavior.
- **Problem with current role:** already good, but missing future coverage for graph portability/sidecar/visibility semantics.
- **Proposed role:** remain the final workspace-law guard; expand only where new policy is introduced.
- **Files affected:** `tests/test_topology_doctor.py`
- **Why this improves online Pro/Codex work:** new authority claims gain machine protection.
- **Risk if changed:** low; only test maintenance burden.
- **Risk if not changed:** policy remains prose-only and regresses.

### Layer 11 — Source code

- **Current role:** executable truth for runtime behavior.
- **Problem with current role in this reconstruction:** none; the risk is widening authority cleanup into behavior changes.
- **Proposed role:** leave source behavior untouched in this program; let source remain behavior truth, not workspace law prose.
- **Files affected:** none in P0/P1/P3; only graph wrapper/tooling in P2.
- **Why this improves online Pro/Codex work:** keeps authority cleanup bounded and auditable.
- **Risk if changed:** very high because it would mix governance work with runtime behavior.
- **Risk if not changed:** none for this program.

## Final future order summary

### Default read order for online Pro/Codex

1. `AGENTS.md`
2. `workspace_map.md`
3. scoped `AGENTS.md` in touched subtree
4. relevant machine manifest(s)
5. `docs/operations/current_state.md`
6. active packet plan/work log/receipt if the task is active
7. derived context engines as needed (`topology_doctor`, Code Review Graph, source rationale, history lore)
8. archives only by explicit historical need

### Final conceptual stack

- **Law:** manifests + tests
- **Control:** current_state + active packet docs
- **Boot routing:** AGENTS + workspace_map + scoped AGENTS
- **Context engines:** graph + topology_doctor + rationale + lore
- **History:** archive registry + archive bundles
