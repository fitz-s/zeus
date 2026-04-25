# Docs System

> Status: reference, not authority. See `architecture/docs_registry.yaml`, `docs/AGENTS.md`, and `docs/authority/**` for authority.

## Purpose

The docs system explains how Zeus separates durable law, stable reference, active operations, reports, artifacts, checklists, and archives so agents can reason without turning docs into a second uncontrolled authority plane.

## Authority anchors

- `docs/AGENTS.md` and scoped docs routers define human routing.
- `architecture/docs_registry.yaml` owns machine classification for tracked docs.
- `docs/authority/**` contains durable law.
- `docs/operations/current_state.md` and current-fact companions carry active operational pointers.
- `docs/archive_registry.md` is the visible archive interface.

## How it works

Docs are classified by role, default-read posture, truth profile, freshness class, and replacement status. Durable references explain stable concepts. Current operations docs are receipt-backed and expiry-bound. Reports/artifacts are evidence. Archive bodies are cold historical storage.

## Hidden obligations

- Current-fact claims do not belong in durable reference unless promoted through authority.
- Packet folders are evidence unless `current_state.md` names one as active.
- Adding a doc usually requires both a scoped `AGENTS.md` registry entry and a `docs_registry.yaml` entry.
- `default_read` must stay narrow to protect context budget and authority clarity.
- Archive-derived claims must be marked `[Archive evidence]` when used.

## Failure modes

- Authority docs accumulate packet notes and become a side-control plane.
- Reference docs carry stale current facts or row counts.
- Root/scoped AGENTS route a file that docs_registry cannot classify.
- Archives leak into default-read paths and override current law by volume.

## Repair routes

- Use `add_registry_row` when a tracked doc is unregistered.
- Use `update_companion` when a docs change requires scoped router or registry maintenance.
- Use `extract_law_to_book` only when hidden durable cognition needs reference-only explanation.
- Use operations current-fact surfaces for time-bound state and receipts for evidence.

## Cross-links

- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`
- `docs/operations/AGENTS.md`
- `docs/archive_registry.md`
