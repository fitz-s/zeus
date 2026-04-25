# Manifests System

> Status: reference, not authority. See `architecture/**` for machine authority and `docs/authority/**` for durable law.

## Purpose

The manifests system explains which machine-readable file owns which governance fact so agents do not create parallel registries or duplicate truth. It is a reference map for ownership; the canonical machine matrix now lives in `architecture/topology_schema.yaml` under `ownership.fact_types`, and enforcement remains in topology-doctor checks/tests.

## Authority anchors

- `architecture/topology.yaml` owns root coverage, digest inputs, current-state contract, and workspace routing facts.
- `architecture/docs_registry.yaml` owns docs classification, default-read posture, freshness class, and replacement eligibility.
- `architecture/module_manifest.yaml` owns module-book routing and module-level dependency pointers.
- `architecture/source_rationale.yaml`, `test_topology.yaml`, and `script_manifest.yaml` own source/test/script classification respectively.
- `architecture/map_maintenance.yaml` owns companion-update rules.
- `architecture/code_review_graph_protocol.yaml` owns graph-use protocol.
- `architecture/topology_schema.yaml` owns compiled topology and typed issue contracts.

## How it works

Each manifest should own a fact type, not merely a path prefix. Reference books may explain those facts but must not become alternate registries. When two manifests appear to describe the same fact, the intended repair is to identify the canonical owner, leave cross-references as pointers, and add a validator only after ownership is explicit.

The canonical ownership matrix is the `ownership.fact_types` section in `architecture/topology_schema.yaml`. This book intentionally does not duplicate the matrix rows; it explains how to use that schema without becoming a second registry.

## Hidden obligations

- Adding, renaming, or deleting files can require manifest, scoped router, and workspace-map companions.
- A module book can route cognition but cannot authorize runtime/source behavior changes.
- `default_read: true` is a context-budget and authority decision, not a convenience flag.
- Current facts belong in operations current-fact surfaces, not durable reference manifests.
- Ownership enforcement is schema-driven; warnings versus errors are policy decisions in topology-doctor mode handling, not prose in this book.

## Failure modes

- A new YAML row is added to the easiest manifest rather than the owning manifest.
- A reference book repeats machine rows and drifts from the manifest.
- Agents fix live repo-health drift by rewriting broad registries outside packet scope.
- A generated graph/context-pack fact is promoted into manifest authority without semantic boot.

## Repair routes

- `add_registry_row`: add the missing row to the manifest that owns the fact type.
- `update_companion`: update scoped `AGENTS.md`, module manifest, docs registry, or map-maintenance companion as required.
- `propose_owner_manifest`: stop and plan when no manifest clearly owns the fact.
- `none`: use when the correct repair is documentation/read-order clarification only.

## Cross-links

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/tests.md`
- `docs/reference/modules/scripts.md`
