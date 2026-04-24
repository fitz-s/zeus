# Workspace Artifact Sync Plan

Date: 2026-04-19
Branch: data-improve

## Objective

Sync safe non-Phase-10A workspace artifacts so the next mainline package starts
from a cleaner repo state.

## Scope

- Commit raw oracle shadow snapshot JSON evidence.
- Register raw evidence routing in topology and artifact lifecycle maps.
- Commit repo-local AI handoff skill and runbooks.
- Move the external reality review into `docs/reports/` with registry entry.
- Register existing to-do/audit evidence surfaces.
- Move the root dual-track package and stray root README into ignored archives
  locally; do not commit those historical/scratch packages.

## Non-Goals

- Do not touch Phase 10A runtime code, schema, script, or test changes.
- Do not stage state runtime files.
- Do not promote raw JSON into canonical truth.
