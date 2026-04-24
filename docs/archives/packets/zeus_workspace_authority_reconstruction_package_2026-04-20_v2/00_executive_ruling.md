# Executive Ruling

## What you were actually asking for

You were not asking for another “authority hardening” pass produced by the same internal loop that created the current authority sprawl.
You were asking for a reconstruction that starts from **objective system needs**:

1. What does a real prediction/trading machine need in order to stay correct?
2. What does a real agentic coding system need in order to stay understandable and editable?
3. Which current Zeus files serve those needs?
4. Which files exist mainly because prior agents got lost, then wrote more prose to compensate?

That is the frame used in this V2 package.

## Final ruling on current workspace authority health

**Ruling:** Zeus already has a strong kernel, but its boot surface is still partially bureaucratic.
The repo's durable center is **better than the repo's own prose implies**.
The machine manifests, topology_doctor lanes, artifact-lifecycle distinctions, and graph tests already form a serious workspace kernel.
What remains weak is the tracked human-facing surface that tells future agents how to use that kernel.

## Is current authority adequate for online Pro/review workflows?

**Not yet.**

The current repo is not a broken authority system.
It is a **mis-layered** authority system:

- the kernel is stronger than the boot docs;
- the graph is stronger than the prose admits;
- the history-compression substrate (`history_lore.yaml`) is stronger than the docs mesh suggests;
- `current_state.md` still carries too much live-operational residue;
- docs root still over-describes hidden archives as if they were a visible peer subtree.

## V2 ruling in one sentence

**Keep the machine-law spine, keep graph.db tracked, demote archive bodies to historical cold storage, slim the live control pointer, and explicitly elevate structural retrieval (topology_doctor + code-review-graph) into the default Zeus workspace model.**

## Top 10 authority/context problems

1. Root boot surfaces still describe Zeus mainly as a doc hierarchy instead of a runtime-plus-workspace system.
2. `docs/README.md` and `docs/AGENTS.md` still describe docs as “active subdirectories plus archives,” even though `docs/archives/` is ignored and not reviewer-visible.
3. `docs/AGENTS.md` routes readers to `archives/AGENTS.md`, but that file is not part of the online repo surface.
4. `docs/operations/current_state.md` is overloaded: control pointer, runtime scratch summary, packet history, and hidden-archive pointer all in one file.
5. `architecture/topology.yaml` semantically knows that archives are historical and the graph is derived, but it still treats hidden archive structure too much like a visible active subtree.
6. The repo already tests and checks Code Review Graph as a first-class lane, yet top-level authority prose still frames it as an appendage.
7. `scripts/code_review_graph_mcp_readonly.py` still hardcodes a local repo root, which is a portability defect in a repo that now claims online context portability.
8. `.code-review-graph/graph.db` is tracked and useful, but its 28 MB SQLite form is opaque in ordinary GitHub browsing; the repo lacks a lightweight human-visible summary sidecar.
9. `architecture/context_budget.yaml` defends AGENTS/workspace_map budgets but does not yet fully defend the repaired `current_state` / archive-interface / graph-summary surfaces.
10. Archive bodies contain mixed-value evidence, stale overlays, binaries, and even plaintext secret references; they cannot be treated as ordinary default context.

## Top 10 recommended changes

1. Rewrite root `AGENTS.md` around **two systems**: runtime truth and workspace change-control.
2. Rewrite `workspace_map.md` around **visibility classes**: tracked-visible, tracked-derived, ignored-local, runtime-local, historical-cold.
3. Add tracked `docs/archive_registry.md` as the single visible interface to hidden history.
4. Rewrite `docs/README.md` and `docs/AGENTS.md` to stop treating archives as a live co-equal docs subtree.
5. Slim `docs/operations/current_state.md` into a **live control pointer only**.
6. Update `architecture/topology.yaml` so hidden archives no longer behave like default visible docs mesh.
7. In P1, make archive visibility and current-state thinness more machine-checkable through topology/schema/map-maintenance updates.
8. In P2, keep `.code-review-graph/graph.db` tracked but pair it with a portable policy and, preferably, a small tracked `graph_meta.json` sidecar.
9. Refactor the Zeus CRG wrapper to honor repo-root portability and upstream tool-filtering capabilities without reintroducing source-writing tools.
10. Use `architecture/history_lore.yaml` plus `docs/archive_registry.md` as the visible historical layer instead of archive-body default reading.

## What should happen first

### First action

Execute **P0 — Online Boot Surface Realignment**.
That is the smallest safe packet that immediately improves online understandability without touching runtime behavior, graph binaries, or wide machine manifests.

### Why P0 first

Because the repo currently has a **kernel/prose mismatch**.
Before improving the machine checks further, the tracked reader-facing surface must stop lying about what is visible, what is authority, and what is merely evidence.

## What must not happen

- Do **not** widen this into source behavior changes.
- Do **not** run any data rebuild.
- Do **not** mutate live/runtime DBs.
- Do **not** make archives default-read.
- Do **not** make Code Review Graph authority.
- Do **not** untrack `graph.db` unless a concrete severe blocker appears.
- Do **not** rewrite all scoped `AGENTS.md` files at once.
- Do **not** merge P0, P1, P2, and P3 into one mega-patch.

## Final verdict on adequacy

### Current authority health

- **Kernel health:** good
- **Boot-surface health:** weak-to-moderate
- **Online reviewer fit:** insufficient
- **Historical memory hygiene:** weak
- **Graph/context maturity:** materially better than current prose admits

### Final disposition

**Preserve the kernel. Rewrite the bootloader. Promote graph/context to first-class derived status. Compress history. Refuse authority inflation.**
