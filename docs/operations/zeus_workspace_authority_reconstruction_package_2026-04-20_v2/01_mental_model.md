# Zeus Mental Model

## 1) Interpreted expectation

The expectation behind this request is not “organize the docs better.”
It is:

- build a cold-startable mental model from outside the repo's self-story;
- decide what a system like Zeus objectively needs;
- compare that model against repo reality;
- then rebuild authority so future Pro/review/Codex work can follow reality instead of re-enacting old confusion.

That means V2 treats existing authority files as **evidence and current contract**, not as untouchable law.

## 2) External objective model

### 2.1 What a real prediction/trading machine needs

A serious prediction/trading machine needs five things before it needs nice prose:

1. **Canonical event truth** — one place where point-in-time facts become durable, queryable state.
2. **Projection discipline** — reports, JSON caches, exports, notebooks, and diagnostics must never silently outrank canonical truth.
3. **Typed semantic boundaries** — metric identity, settlement semantics, probability contracts, lifecycle rules, and risk behavior must be explicit.
4. **Temporal purity** — no hindsight leakage, no hidden fallback from missing historical truth to latest known truth.
5. **Auditable control** — operator actions, packetized changes, and runtime boundary changes must leave evidence.

This is why Zeus already has strong invariants around DB truth vs projection truth, point-in-time law, negative constraints, and packet discipline.

### 2.2 What a real agentic coding system needs

A serious agentic coding workspace needs a different but complementary stack:

1. **Thin boot path** — a new agent must know where to start in a handful of reads.
2. **Machine-routable law** — manifests, schemas, and tests must outrank prose when drift appears.
3. **Structural retrieval** — repo maps, code graphs, blast radius, flows, communities, test-coverage hints, and minimal-context tools must exist so agents do not scan blindly.
4. **Visibility classes** — the agent must know what is tracked-visible, tracked-derived, ignored-local, runtime-local, and historical-only.
5. **Compressed history** — past failures must survive as dense lessons, not as default-read narrative sediment.
6. **Verification kernel** — a single enforcement surface must check docs topology, route integrity, graph health, and packet closeout.

Zeus already has most of this kernel.
The reconstruction job is to make the boot surfaces finally reflect it.

### 2.3 External basis for this model

This V2 mental model is not invented in a vacuum.
It lines up with the way modern agentic coding systems are evolving:

- MCP turns external tools and context sources into a standard, composable substrate.
- Aider-style repo maps prove that thin structural summaries beat indiscriminate full-repo reading for many coding tasks.
- Sourcegraph-style MCP retrieval results show that better context retrieval can materially improve file recall, precision, and auditability.
- Code Review Graph pushes this further by persisting a structural graph and exposing minimal-context, blast-radius, and review-oriented tools.

That is why Zeus should think in terms of **context engines** rather than “extra docs.”

## 3) Why Code Review Graph matters in this external model

Code Review Graph is not just a “risk score helper.”
In the current external ecosystem, it behaves as a **persistent structural knowledge graph and context router**:

- incremental graph updates,
- minimal context extraction,
- blast-radius analysis,
- affected tests and flows,
- communities and architecture overview,
- semantic search,
- large-function and hotspot discovery,
- read-only review prompts through MCP.

That makes it a **context engine**, not authority.
A real agentic repo should explicitly say so.

## 4) Zeus runtime OS

Think of Zeus runtime as a four-layer machine.

### 4.1 Data layer

- Forecast feeds
- Settlement observations
- Market data
- DB/event truth surfaces
- Input provenance and missing-data semantics

### 4.2 Probability layer

- raw ensemble facts
- calibration
- posterior/edge logic
- uncertainty contracts
- metric-family separation (high vs low)

### 4.3 Execution layer

- candidate generation
- order creation
- open position monitoring
- settlement / harvest
- learning signal preservation

### 4.4 Control layer

- lifecycle law
- risk law
- operator runbooks
- daemon boundaries
- packet/change discipline
- truth/projection boundary enforcement

This request does **not** rewrite runtime behavior.
It rewrites the workspace layer that lets future agents understand and modify runtime safely.

## 5) Zeus workspace OS

The repo itself behaves like a second operating system.
That is the most useful way to think about authority reconstruction.

### 5.1 Bootloader

These are the first read surfaces:

- `AGENTS.md`
- `workspace_map.md`
- `docs/README.md`
- scoped `AGENTS.md`
- `docs/operations/current_state.md`

Current problem: the bootloader still over-narrates docs hierarchy and under-narrates visibility, context engines, and cold storage.

### 5.2 Kernel

This is the real authority kernel:

- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `architecture/artifact_lifecycle.yaml`
- `architecture/context_budget.yaml`
- `architecture/map_maintenance.yaml`
- `architecture/source_rationale.yaml`
- `architecture/script_manifest.yaml`
- `architecture/test_topology.yaml`
- `architecture/history_lore.yaml`
- `tests/test_topology_doctor.py`
- `scripts/topology_doctor*.py`

Current problem: the kernel is better than the prose admits, but some visibility assumptions are still encoded in a pre-online worldview.

### 5.3 Context engines

These are not law, but they are essential:

- `scripts/topology_doctor.py` outputs and lanes
- context packs / impact graphs
- `.code-review-graph/graph.db`
- `scripts/code_review_graph_mcp_readonly.py`
- `architecture/source_rationale.yaml`
- `architecture/history_lore.yaml`

Current problem: Zeus still describes these as adjuncts when they should be modeled as first-class derived context.

### 5.4 Live control plane

These surfaces answer “what is active now?”

- `docs/operations/current_state.md`
- active packet folders under `docs/operations/`
- receipts / work logs / closeout evidence

Current problem: `current_state.md` still behaves like a hybrid of pointer, dashboard, runtime scratch mirror, and historical ledger.

### 5.5 Cold storage

This is the historical layer:

- uploaded `archives.zip`
- `docs/archives/**` in local-only flows
- historical overlay packages
- retired artifacts and work packets

Current problem: docs root still narrates cold storage as if it were part of the active visible docs mesh.

## 6) DB truth vs projection truth

Zeus already distinguishes these conceptually, and that distinction should become even clearer in workspace authority.

### Canonical truth

- durable DB/event truth
- executable contracts
- typed lifecycle/settlement/probability semantics

### Projection truth

- JSON exports
- reports
- notebooks
- spreadsheets
- packet evidence
- graph summaries
- code-review-graph outputs

Workspace implication:

- source/runtime contracts may define behavior truth;
- machine manifests may define change-law;
- derived context may guide navigation;
- projections may never silently become authority.

## 7) Docs authority vs machine manifests

The old bureaucratic failure mode is: when agents get lost, write more prose.

The objective-system replacement is:

- machine manifests own durable classifications;
- tests enforce them;
- boot prose routes agents into those manifests;
- current_state points to the active packet;
- graph/context tools answer structural questions;
- archives hold historical evidence off the default path.

That is the core replacement V2 is proposing.

## 8) Active operations packets

Active operations packets should be treated as **live change contracts**, not as permanent governance layers.

Their role is:

- define scope,
- define allowed files,
- define evidence,
- define closeout,
- then leave the active surface when complete.

What they must not become:

- permanent architecture law,
- long-lived runtime dashboards,
- archive indexes,
- substitute for boot routing.

## 9) Topology doctor role

Topology doctor is not merely a linter.
It is Zeus's **workspace kernel / verifier / router compiler**.

It already checks:

- docs integrity,
- registries,
- topology compliance,
- map maintenance,
- script/test/source registration,
- planning lock,
- receipts and work records,
- graph status,
- context packs.

That means the right move is not “replace topology_doctor with prose.”
It is “make the prose finally tell agents to trust the kernel correctly.”

## 10) Code Review Graph role

### Final role

Code Review Graph should sit in Zeus as:

- **tracked**,
- **derived**,
- **first-class context engine**,
- **non-authority**.

### What it contributes

- minimal review context
- blast radius
- affected tests/scripts/files
- hotspots and large functions
- flows and communities
- architecture overview
- semantic retrieval
- context-pack appendices

### What it does not contribute

- truth ownership
- planning-lock waivers
- receipt waivers
- legal permission to edit source
- authority to contradict manifests or code contracts

## 11) Archives role

Archives should remain **historical cold storage**.
They exist to answer:

- what failed before,
- what was tried before,
- what anti-patterns recurred,
- what should be compressed into lore.

They should not be default-read because they are mixed-density, high-noise, partially stale, and may contain sensitive leftovers.

The visible historical layer should therefore be:

- `architecture/history_lore.yaml` for dense lessons,
- `docs/archive_registry.md` for access protocol and index,
- archive bundles only when a task explicitly needs historical evidence.

## 12) Current dual-track refactor state

The repo visibly remains in a dual-track metric refactor context.
The important authority implication is not the implementation detail itself, but that Zeus is still carrying active architectural transition work.
That makes it even more important that workspace authority be thin, machine-routable, and explicit about what is current versus historical.

## 13) Execution-state truth upgrade context

Visible repo state suggests Zeus is still refining how current operational truth is represented to future agents.
`current_state.md`, runtime artifact inventories, and packet closeout checks all point to an unresolved question:

> how much present-tense execution state should live in tracked repo prose versus runtime-local artifacts?

V2 answer:

- only the minimum live control pointer belongs in the tracked repo;
- volatile runtime state belongs in runtime-local surfaces;
- durable lessons belong in lore or archive.

## 14) Final mental-model compression

### Zeus runtime

A point-in-time weather-trading machine with strict truth/projection boundaries.

### Zeus workspace

A machine-governed change system with:

- a thin bootloader,
- a strong manifest/test kernel,
- first-class context engines,
- active packet control,
- and historical cold storage.

### Reconstruction principle

**Anything that exists only because prior agents got lost should be either compressed into machine law, demoted to archive, or removed from the default read path.**
