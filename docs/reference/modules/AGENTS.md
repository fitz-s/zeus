# docs/reference/modules AGENTS

Dense module-reference layer for Zeus.

This directory exists for module books that make zero-context work possible
without promoting those books into authority. Module books explain one module's
purpose, hazards, truth surfaces, tests, and change routes. They do not replace
authority docs, machine manifests, current-fact surfaces, tests, or source.

## Read order

1. root `AGENTS.md`
2. `workspace_map.md`
3. the scoped `AGENTS.md` for the touched module or system surface
4. `architecture/module_manifest.yaml`
5. the one routed module book in this directory
6. current-fact/test surfaces named by that module book

## File registry

P0 scaffolds this router first. Module books are added phase-by-phase and must
be registered here as they land.

## Rules

- One file per module or system surface.
- Follow the Module Authority Book Standard described in the active packet and
  later durable references.
- Do not store packet status, dated audits, row counts, live source health, or
  archive bodies here.
- If a claim is time-bound, point to `docs/operations/**` instead of embedding
  it here.
- If a graph appendix is useful, keep it derived-only and subordinate to the
  module book.
