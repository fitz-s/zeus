# 07 — Context Pack and Graph Integration Audit

## Ruling

Graph integration is conceptually safe but under-extracted. Context packs can already include module/context/graph/repo-health sections, but their value is capped by thin module books and a binary graph artifact.

## What is working

- The graph protocol says semantic boot comes before graph context.
- Graph output is derived structural context, not semantic authority.
- Missing or stale graph usually yields warnings rather than semantic blockers.
- Context packs understand module manifest, module books, repo health, route health, and graph-derived code impact.
- Graph status checks include missing DB, stale head/branch, partial coverage, missing changed files, and dirty file hash staleness.

## What is weak

### Binary graph is not online cognition

A tracked `graph.db` helps local tools but is a poor direct input for online-only agents. The graph module book acknowledges this. Zeus needs small textual extracts.

### Module context is warning-first but thin

Module manifest and module book checks are currently warning-first. That is appropriate during rollout, but it means context packs can generate despite thin cognition.

### Graph routes are not promoted into durable module books

The graph can surface hidden routes and high-impact nodes, but those need promotion as derived appendices, not authority.

### Context packs inherit registry compression

If the manifest says a module has high-risk files but the module book does not explain why, the context pack still leaves online-only agents under-informed.

## Required graph/text surfaces

Add generated, small, derived-not-authority sidecars:

- `docs/reference/modules/_generated/graph_hotspots.md`
- `docs/reference/modules/_generated/graph_module_routes.md`
- `docs/reference/modules/_generated/graph_test_routes.md`
- or equivalent context-pack-only generated appendices if committed sidecars are too risky.

Each must include:

- generation command,
- source graph freshness,
- baseline commit,
- limitations,
- “not authority” label,
- max size budget,
- module/test route excerpts.

## Graph must never do

- decide source validity,
- decide settlement truth,
- waive planning lock,
- override manifests,
- prove code semantics,
- replace tests,
- authorize runtime/source behavior changes.

## Context pack improvements

1. Include lane policy status: direct blockers vs repo-health warnings.
2. Include module book density status.
3. Include graph usability status and limitations.
4. Include hidden obligation summary for changed files.
5. Include repair group candidates.
6. Include explicit authority order reminder for task class.

## Validation commands

See `validation/graph_context_validation.md`.
