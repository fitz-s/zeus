# Closeout and Receipts System

> Status: reference, not authority. See `docs/authority/zeus_current_delivery.md`, `architecture/artifact_lifecycle.yaml`, and `architecture/change_receipt_schema.yaml` for authority.

## Purpose

The closeout and receipts system ensures a packet ends with scoped validation, companion updates, work evidence, and explicit deferrals. It protects Zeus from both under-closing risky work and over-blocking a packet on unrelated global drift.

## Authority anchors

- `docs/authority/zeus_current_delivery.md` defines delivery and packet discipline.
- `architecture/artifact_lifecycle.yaml` defines work-record requirements.
- `architecture/change_receipt_schema.yaml` defines route/change receipt shape.
- `architecture/map_maintenance.yaml` defines required companions.
- `scripts/topology_doctor_closeout.py` compiles closeout lanes.
- `docs/operations/current_state.md` points to the active packet and required evidence.

## How it works

Closeout starts from changed files, then expands them through map-maintenance companions. It selects relevant docs/source/tests/scripts/data/context lanes, runs always-on evidence lanes, scopes drift to changed files where appropriate, and reports full repo health separately under `global_health`.

A closeout payload has three distinct concepts:

- `blocking_issues`: packet-scope failures that must be resolved or explicitly deferred through authorized evidence.
- `warning_issues`: scoped advisory findings.
- `global_health`: full-lane counts for visibility; not a scoped blocker by itself.

## Hidden obligations

- Missing work records and missing receipts are real closeout blockers for repo-changing work.
- Planning-lock files need plan evidence before implementation closes.
- A deferral is only valid when recorded in the packet evidence; silent omission is not a deferral.
- Global health must remain visible even when scoped closeout passes.
- Closeout must not mutate runtime truth or produce canonical DB facts.

## Failure modes

- A packet passes local tests but lacks a receipt/work log and becomes unreplayable.
- Closeout hides repo-wide drift after P0 scoping, making reviewers think the whole repo is green.
- An agent fixes unrelated docs/source registry failures to make closeout pass, widening the packet.
- A missing companion is treated as a warning even though changed-file law requires it.

## Repair routes

- Use `update_companion` for missing scoped router/registry/map updates.
- Use `add_registry_row` for newly tracked docs/tests/scripts/source files.
- Use `propose_owner_manifest` when closeout reveals ambiguous ownership.
- Record severe blockers in packet work logs and receipts rather than deleting the gate.

## Cross-links

- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/operations/AGENTS.md`
- `docs/authority/zeus_current_delivery.md`
