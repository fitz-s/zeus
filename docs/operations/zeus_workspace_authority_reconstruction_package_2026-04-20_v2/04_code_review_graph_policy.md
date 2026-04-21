# Code Review Graph Policy

## Final ruling

`.code-review-graph/graph.db` stays **tracked**.
It stays **non-authority**.
It is promoted to a **first-class derived context engine**.

That is the core V2 shift.

## Why the repo should treat CRG as more important than current prose does

The external CRG ecosystem has already moved beyond “cache + risk score.”
The program now supports persistent structural graph storage, incremental updates, blast-radius analysis, minimal-context retrieval, communities, flows, architecture overview, hotspot analysis, semantic search, and a growing MCP tool surface.
Recent upstream releases also added tool-surface expansion, explicit tool filtering, and environment-driven portability controls such as repo-root and data-dir overrides.

That means Zeus should stop describing the graph as if it were merely optional scratch evidence.
It is not law, but it **is** a major context subsystem.

## What CRG is inside Zeus

Inside Zeus, Code Review Graph is:

- a **tracked online context artifact**,
- a **structural retrieval substrate**,
- a **review and impact-analysis engine**,
- a **context-pack appendix source**,
- a **non-authority diagnostic plane**.

## What CRG is not

CRG is not:

- architecture law,
- runtime truth,
- planning-lock bypass,
- permission to edit source,
- receipt evidence by itself,
- proof that a packet is safe,
- replacement for manifests, tests, or code reading.

## Final policy answers

### Track it or not?

**Tracked.**
The repo has already committed to online-only agents being able to recover structural context from GitHub-visible artifacts.
Removing tracking would throw away a real advantage.

### Why keep it tracked?

Because online Pro/review agents may not have a local disk, worktree, or builder runtime.
A tracked graph lets them recover:

- which files are central,
- which symbols connect where,
- what the likely blast radius is,
- which tests or flows are likely relevant,
- where the hotspot nodes sit.

### Why not make it authority?

Because the graph is derived from source and builder logic.
It can be stale, partial, or path-contaminated.
Derived context can guide reading; it cannot outrank manifests or executable truth.

## What CRG is allowed to influence

CRG may influence:

- file/symbol discovery,
- blast-radius estimates,
- likely affected tests,
- hotspot identification,
- community/flow inspection,
- architecture overview generation,
- context-pack appendices,
- “what should I read next?” routing,
- “what changed around this area?” review assistance.

## What CRG must never influence

CRG must never:

- authorize a source edit,
- overrule `architecture/**`,
- overrule runtime/source truth,
- waive planning-lock,
- waive work-record or receipt requirements,
- declare a migration safe,
- convert a docs-only belief into behavior truth,
- promote archive content into current law.

## Freshness policy

### Distinguish three different questions

1. **Is the tracked graph contract present?**
2. **Is the graph usable right now?**
3. **May a packet rely on graph-derived claims?**

### Presence contract

- `graph.db` missing or untracked = **repo-integrity problem**.
- That should be treated as a **blocking defect** for packets that advertise or modify online context policy, graph tooling, topology, source, scripts, or tests.
- For a tiny unrelated local docs change, it can be tolerated temporarily only if the packet does not rely on graph claims and the defect is recorded — but the repo should not normalize this state.

### Usability / freshness

These are usually **warning-first** states:

- stale head
- stale branch
- dirty-file hash mismatch
- partial coverage
- empty postprocess / missing flows/communities
- path-mode awkwardness

Warnings do **not** automatically block a commit.
They **do** degrade the right to rely on graph-derived evidence.

### Reliance rule

If CRG is stale, partial, or otherwise unusable, then:

- graph-derived appendices must say so,
- review claims must downgrade confidence,
- local Codex must fall back to manifests, source rationale, and ordinary code reading.

## Stale graph: warning or blocker?

### Final answer

- **Stale or partial graph:** warning for repo health; blocker for graph-derived confidence claims.
- **Missing/untracked graph.db after the repo has chosen to track it:** blocker for graph contract integrity.

This keeps Zeus from becoming bureaucratic while still protecting the online-context promise.

## Local absolute paths

### Are they acceptable now?

**Yes, temporarily.**
They are acceptable as graph metadata only, because the first tracked online version was explicitly allowed to ship with absolute paths.

### Are they desirable long-term?

**No.**
The target state is repo-relative storage or, at minimum, explicit path-mode disclosure plus reliable read-time relativization.

## Future path policy

### Target state

- builder prefers repo-relative file paths;
- readers remain backward-compatible with legacy absolute-path rows;
- path mode is surfaced in graph status and graph metadata;
- context packs state whether graph paths are absolute, repo-relative, or mixed.

### Why this matters

Because an online-tracked graph must survive movement across worktrees, machines, and reviewers.
A graph that only works on one laptop is not good enough for Zeus's stated online-context goal.

## Online Pro usage policy

Online Pro should use the graph in this order:

1. inspect human-visible graph policy and metadata summary;
2. inspect graph status / usability if available;
3. use graph-derived claims only as **derived context**;
4. cross-check against manifests and source when the decision matters.

If the blob is too large or ordinary GitHub preview is useless, online Pro should still use:

- `architecture/source_rationale.yaml`,
- `architecture/script_manifest.yaml`,
- `architecture/test_topology.yaml`,
- `scripts/topology_doctor.py` and its graph lane,
- any tracked graph meta summary added in P2.

## Local Codex update policy

Local Codex should update CRG only when one of these is true:

- a packet changed structural code under `src/**`, `scripts/**`, or `tests/**`;
- the packet explicitly changes graph policy/tooling;
- a graph-derived review artifact is intended to ship with the packet.

Local Codex should **not** update the graph for P0 or P1.

### Update posture

- build/update from repo root;
- prefer a clean worktree or a clearly scoped packet worktree;
- never hand-edit SQLite;
- record git head, branch, builder version, path mode, and usability state;
- stage graph artifacts only after local verification.

**LOCAL_VERIFICATION_REQUIRED:** if the local environment does not have a trustworthy graph build/update flow installed, do not fabricate graph artifacts in this program.

## Should Zeus add a graph metadata sidecar?

### V2 recommendation

**Yes, in P2.**
Add a small tracked `.code-review-graph/graph_meta.json` sidecar containing at least:

- graph schema/version
- builder version or provenance
- git head
- git branch
- generated_at timestamp
- file/node/edge counts
- flows/communities counts
- path mode (`absolute`, `repo_relative`, `mixed`)
- overall usability summary
- warning codes if present

### Why

Because a 28 MB SQLite blob is useful to automated consumers but weak for human inspection on GitHub.
The sidecar gives online reviewers a quick trust read before they rely on the graph.

## How the Zeus MCP wrapper should evolve

### Current problem

`scripts/code_review_graph_mcp_readonly.py` hardcodes a local repo root and therefore bakes one workstation into a repo-level context engine.
This is especially unnecessary now that upstream CRG already supports repo-root and data-dir overrides and tool allow-listing.

### Final policy

- keep a Zeus-owned read-only safety boundary;
- remove the hardcoded path;
- prefer repo-root discovery and environment overrides;
- where upstream CRG already supports tool filtering / repo-root configuration, use those capabilities instead of carrying unnecessary bespoke policy logic.

### Safety boundary

Zeus should continue omitting source-writing tools from its default MCP-facing surface.
Preview-only refactor analysis may stay if it is genuinely non-writing and clearly labeled as such.

## What topology_doctor should enforce

Topology doctor should continue to enforce:

- `graph.db` tracking contract,
- warning-first freshness semantics,
- unusable-graph downgrades for context packs,
- dirty-file stale warnings,
- postprocess-empty warnings,
- code-impact payloads labeled `derived_code_impact_not_authority`.

In P2 it should additionally enforce or expose:

- path mode,
- graph-meta parity with `graph.db`,
- wrapper repo-root portability expectations,
- clearer online summary output.

## What tests should enforce

`tests/test_topology_doctor.py` should continue covering:

- untracked graph.db blocking behavior,
- dirty-file mismatch as warning only,
- docs-only graph impact unusability.

P2 additions should cover:

- absolute vs repo-relative vs mixed path mode,
- graph_meta sidecar presence/shape/parity,
- repo-root discovery behavior in the MCP wrapper,
- context-pack disclosure of graph usability and path mode.

## Final one-page policy summary

| Question | Final answer |
|---|---|
| Track `graph.db`? | Yes |
| Authority? | No |
| Role? | First-class derived context engine |
| Missing/untracked DB | Blocking contract defect |
| Stale graph | Warning for repo health; blocker for graph-confidence claims |
| Absolute paths acceptable now? | Yes, as temporary metadata only |
| Long-term path target | Repo-relative or mixed-mode with explicit disclosure |
| Sidecar recommended? | Yes, `graph_meta.json` in P2 |
| Wrapper safety boundary | Keep read-only; remove hardcoded repo root |
