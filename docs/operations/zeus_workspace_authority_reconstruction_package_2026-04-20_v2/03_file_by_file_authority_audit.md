# File-by-File Authority Audit

This audit is intentionally decisive.
It does not treat “current file exists” as proof that the file is healthy.
It distinguishes between **keep**, **rewrite**, **split function**, **archive-only**, and **do not promote**.

## `AGENTS.md`
- **Current function:** Root boot contract and repo operating brief.
- **Authority level:** High human boot surface; subordinate to system/developer/user instructions and machine manifests.
- **Drift / debt:** Good core distinctions already exist, especially non-authority graph policy, but the file still needs a clearer runtime/workspace split and stronger visibility language.
- **Disposition:** KEEP + REWRITE
- **Exact proposed action:** Rewrite as a thin boot contract: what Zeus is, what the workspace is, read order, authority/context/history split, and explicit graph/context-engine framing.
- **Risk level:** High

## `workspace_map.md`
- **Current function:** Repo placement and routing map.
- **Authority level:** High human router; subordinate to manifests.
- **Drift / debt:** Already correctly states that archives are local historical evidence and graph.db is tracked derived context, but it still behaves more like a placement memo than a visibility matrix.
- **Disposition:** KEEP + REWRITE
- **Exact proposed action:** Keep the file; rewrite around visible/hidden/derived/runtime classes and default-read rules.
- **Risk level:** High

## `docs/README.md`
- **Current function:** Docs index for tracked documentation.
- **Authority level:** Medium router / docs index.
- **Drift / debt:** Current text still says the design principle is “active subdirectories plus archives,” which is not true for online readers.
- **Disposition:** KEEP + REWRITE
- **Exact proposed action:** Remove archives as a co-equal visible subtree; add explicit route to `docs/archive_registry.md` for historical access policy.
- **Risk level:** High

## `docs/AGENTS.md`
- **Current function:** Docs-root router and file registry.
- **Authority level:** Medium scoped router.
- **Drift / debt:** Routes to `archives/AGENTS.md` even though archives are not online-visible; overstates archive proximity to live docs mesh.
- **Disposition:** KEEP + REWRITE
- **Exact proposed action:** Keep as a thin docs-root router; remove live-sibling treatment of archives; route historical needs to `docs/archive_registry.md` instead.
- **Risk level:** High

## `docs/operations/AGENTS.md`
- **Current function:** Live operations docs router.
- **Authority level:** Medium scoped router for active control surfaces.
- **Drift / debt:** Conceptually sound, but should be tightened so `current_state.md` is treated strictly as a live pointer and packet closeout path, not a diary.
- **Disposition:** KEEP + MODIFY
- **Exact proposed action:** Clarify “live control only” and ensure archive references are phrased as closeout destinations, not default reading paths.
- **Risk level:** Medium

## `docs/operations/current_state.md`
- **Current function:** Current active-state pointer plus packet/program notes.
- **Authority level:** High live control pointer.
- **Drift / debt:** Overloaded with runtime-local detail, packet history, backlog, and hidden-history pointers.
- **Disposition:** KEEP + SPLIT FUNCTION
- **Exact proposed action:** Retain the file but radically slim it. Keep only branch/program, active packet, next packet, blockers, and required evidence links. Move narrative/history to packet docs or archive.
- **Risk level:** Critical

## `docs/archives/AGENTS.md`
- **Current function:** Historical-only archive instructions; present in uploaded archive evidence but absent from tracked online repo.
- **Authority level:** Historical-only; not active authority.
- **Drift / debt:** Current visible docs still refer to it, but online readers cannot load it from the repo.
- **Disposition:** DO NOT RE-TRACK AS ACTIVE DOC
- **Exact proposed action:** Do not restore `docs/archives/**` as a visible docs subtree just to satisfy stale references. Instead extract the access protocol into `docs/archive_registry.md` and leave archive AGENTS historical-only.
- **Risk level:** High

## `architecture/topology.yaml`
- **Current function:** Primary machine-readable topology and repo classification manifest.
- **Authority level:** Highest durable workspace law (with schema/tests).
- **Drift / debt:** Semantically knows archives are historical and graph is derived, but still encodes hidden archive structure too much like a visible docs subtree/registry.
- **Disposition:** KEEP + MODIFY
- **Exact proposed action:** P0: register `docs/archive_registry.md` and stop treating hidden archives as default visible docs mesh. P1: add explicit visibility semantics or exclusions if required by schema.
- **Risk level:** Critical

## `architecture/source_rationale.yaml`
- **Current function:** Curated file-role/write-route rationale map.
- **Authority level:** High durable support manifest.
- **Drift / debt:** Stronger than V1 assumed; already useful for zones, hazards, write routes, and downstream blast notes. Main debt is not quality but lack of explicit positioning as a graph-complement rather than graph-substitute.
- **Disposition:** KEEP
- **Exact proposed action:** No P0 change. Optional later add small notes connecting it to graph/context-pack usage.
- **Risk level:** Low

## `architecture/script_manifest.yaml`
- **Current function:** Declared script inventory and intent.
- **Authority level:** High durable support manifest.
- **Drift / debt:** Good. Already classifies the Zeus read-only CRG wrapper as a safe utility and many topology_doctor helper modules as compiled checker family members.
- **Disposition:** KEEP
- **Exact proposed action:** Do not change in P0/P1. In P2, update only if graph sidecar or wrapper behavior changes.
- **Risk level:** Low

## `architecture/test_topology.yaml`
- **Current function:** Declared test coverage topology.
- **Authority level:** High durable support manifest.
- **Drift / debt:** Good base. Main future debt is adding explicit tests for graph repo-root portability and any new graph metadata sidecar.
- **Disposition:** KEEP
- **Exact proposed action:** P2 only: add or update entries if new tests are introduced.
- **Risk level:** Low

## `architecture/artifact_lifecycle.yaml`
- **Current function:** Lifecycle classes for runtime, scratch, tracked online context, and retired artifacts.
- **Authority level:** High durable support manifest.
- **Drift / debt:** Already classifies `graph.db` correctly. Needs only later extension if a tracked `graph_meta.json` sidecar is added or if archive registry is classified as the visible historical interface.
- **Disposition:** KEEP + LATER MODIFY
- **Exact proposed action:** No P0 change required. P1/P2 may add archive-interface/graph-meta classes.
- **Risk level:** Medium

## `architecture/context_budget.yaml`
- **Current function:** Budget and route limits for boot surfaces and routing reads.
- **Authority level:** High durable support manifest.
- **Drift / debt:** Protects AGENTS/workspace_map well, but does not yet explicitly harden the repaired `current_state` and visible archive interface.
- **Disposition:** KEEP + MODIFY
- **Exact proposed action:** P1: add budget expectations for `docs/archive_registry.md`, `docs/operations/current_state.md`, and optionally graph-summary sidecar.
- **Risk level:** Medium

## `architecture/map_maintenance.yaml`
- **Current function:** Companion-file maintenance rules.
- **Authority level:** High durable support manifest.
- **Drift / debt:** Useful but still oriented around the previous docs topology. It does not yet know about `docs/archive_registry.md` as the visible historical interface.
- **Disposition:** KEEP + MODIFY
- **Exact proposed action:** P1: add companion rules so edits that change history visibility or docs-root routing must update the archive registry and relevant boot docs together.
- **Risk level:** Medium

## `scripts/topology_doctor.py`
- **Current function:** Main facade for compiled topology, navigation, graph status, context packs, and closeout checks.
- **Authority level:** Executable workspace kernel.
- **Drift / debt:** This file is not the problem; the problem is that top-level docs do not describe it with the importance it already has.
- **Disposition:** KEEP
- **Exact proposed action:** No structural rewrite in P0. In P2, only adjust if graph meta or portability changes require new plumbing.
- **Risk level:** Low

## `scripts/topology_doctor_cli.py`
- **Current function:** CLI surface for topology_doctor lanes and subcommands.
- **Authority level:** Executable workspace kernel interface.
- **Drift / debt:** Already exposes graph status, context packs, packet, impact, closeout, and map-maintenance lanes. Needs no policy change until P2 if graph sidecar inspection is added.
- **Disposition:** KEEP
- **Exact proposed action:** No P0/P1 change. P2 only if new graph-meta/reporting lane is added.
- **Risk level:** Low

## `scripts/topology_doctor_docs_checks.py`
- **Current function:** Docs checker family module.
- **Authority level:** Executable checker family.
- **Drift / debt:** Good split direction. Future debt is only to encode new archive visibility invariants if topology/schema grow them.
- **Disposition:** KEEP
- **Exact proposed action:** Modify only in P1 if docs-hidden/visible policy becomes more explicit in machine checks.
- **Risk level:** Low

## `scripts/topology_doctor_registry_checks.py`
- **Current function:** Registry, root, docs-strict, and authority-reference checker family.
- **Authority level:** Executable checker family.
- **Drift / debt:** Already catches stale registry entries and shadow authority references. Good place for P1 visibility coherence checks.
- **Disposition:** KEEP + LATER MODIFY
- **Exact proposed action:** P1: extend only if needed to enforce visible history interface and current_state thinness.
- **Risk level:** Medium

## `scripts/topology_doctor_map_maintenance.py`
- **Current function:** Companion-file change checker family.
- **Authority level:** Executable checker family.
- **Drift / debt:** Small and clean. Needs only P1 expansion to understand archive registry companion rules.
- **Disposition:** KEEP + LATER MODIFY
- **Exact proposed action:** No P0 change. P1 update if new map-maintenance rules are added.
- **Risk level:** Low

## `scripts/topology_doctor_source_checks.py`
- **Current function:** Source-rationale / scoped AGENTS checker family.
- **Authority level:** Executable checker family.
- **Drift / debt:** No obvious authority debt from inspected surface; keep stable.
- **Disposition:** KEEP
- **Exact proposed action:** No change in this program unless a later manifest addition requires it.
- **Risk level:** Low

## `scripts/topology_doctor_script_checks.py`
- **Current function:** Script-manifest checker family.
- **Authority level:** Executable checker family.
- **Drift / debt:** No immediate issue; keep untouched unless P2 graph wrapper changes require manifest enforcement updates.
- **Disposition:** KEEP
- **Exact proposed action:** No change in P0/P1.
- **Risk level:** Low

## `scripts/topology_doctor_test_checks.py`
- **Current function:** Test-topology checker family.
- **Authority level:** Executable checker family.
- **Drift / debt:** No immediate issue; future only if new tests added.
- **Disposition:** KEEP
- **Exact proposed action:** No change until P2/P3 if new tests land.
- **Risk level:** Low

## `scripts/topology_doctor_code_review_graph.py`
- **Current function:** Graph status, freshness/usability, and code-impact context builder.
- **Authority level:** Executable derived-context gate.
- **Drift / debt:** Already stronger than docs admit. Main debt is portability/path-policy hardening and optional graph-meta sidecar support.
- **Disposition:** KEEP + MODIFY IN P2
- **Exact proposed action:** Do not touch in P0/P1. In P2, add repo-root/path-mode clarity, optional sidecar checks, and stronger online summary output.
- **Risk level:** High

## `scripts/topology_doctor_context_pack.py`
- **Current function:** Context-pack builder.
- **Authority level:** Executable derived-context packer.
- **Drift / debt:** Useful place to expose graph usability and summary metadata more explicitly.
- **Disposition:** KEEP + MODIFY IN P2
- **Exact proposed action:** Only change if P2 adds graph-meta/path-mode/freshness summary to context packs.
- **Risk level:** Medium

## `scripts/code_review_graph_mcp_readonly.py`
- **Current function:** Zeus-safe read-only MCP facade for Code Review Graph capabilities.
- **Authority level:** Derived-context gateway; never authority.
- **Drift / debt:** Most important debt file in the graph lane. It hardcodes a local repo root and therefore leaks workstation assumptions into a portability-sensitive repo.
- **Disposition:** KEEP + REWRITE IN P2
- **Exact proposed action:** Preserve read-only safety boundary, but replace hardcoded root resolution with env/repo-relative discovery; optionally use upstream tool filtering instead of bespoke safety wiring where equivalent.
- **Risk level:** Critical

## `tests/test_topology_doctor.py`
- **Current function:** Primary enforcement test suite for workspace topology and graph status behavior.
- **Authority level:** Machine enforcement guardrail.
- **Drift / debt:** Already enforces graph-status nuances and many docs/current_state invariants. Needs future cases for graph repo-root portability, path mode, and any new graph meta sidecar.
- **Disposition:** KEEP + EXPAND IN P1/P2
- **Exact proposed action:** Add only targeted tests that protect new policy claims.
- **Risk level:** High

## `.gitignore`
- **Current function:** Root ignore policy.
- **Authority level:** Repo hygiene / artifact lifecycle support.
- **Drift / debt:** Current behavior is correct for tracked graph.db and ignored archives. Will require a narrow edit only if a tracked `graph_meta.json` sidecar is added.
- **Disposition:** KEEP
- **Exact proposed action:** No P0/P1 change. P2 only: unignore sidecar if adopted.
- **Risk level:** Medium

## `.code-review-graph/.gitignore`
- **Current function:** Local scratch guard within graph directory.
- **Authority level:** Repo hygiene / artifact lifecycle support.
- **Drift / debt:** Correctly tracks only `.gitignore` and `graph.db` today. Will require a narrow edit if `graph_meta.json` becomes tracked.
- **Disposition:** KEEP
- **Exact proposed action:** No P0/P1 change. P2 only if sidecar adopted.
- **Risk level:** Medium

## `.code-review-graph/graph.db`
- **Current function:** Tracked SQLite structural graph cache for online context.
- **Authority level:** Tracked derived context artifact; explicitly non-authority.
- **Drift / debt:** Valuable and intentionally tracked, but currently underexplained and not browser-friendly. May carry absolute paths as metadata only.
- **Disposition:** KEEP
- **Exact proposed action:** Keep tracked. Do not hand-edit. In P2, optionally pair with a truthfully generated `graph_meta.json` sidecar and stronger freshness/path policy.
- **Risk level:** Critical
