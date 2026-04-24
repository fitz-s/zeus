# Open Questions

Only questions that materially affect implementation are listed here.
Most obvious engineering details have already been decided.

## Blocks P0

None.
P0 is intentionally designed so it can proceed without waiting for unresolved graph-tooling or schema questions.

## Blocks P1

None strictly block P1, but one design choice may affect shape:

- whether `architecture/topology.yaml` can express hidden-vs-visible docs semantics cleanly without a schema extension, or whether `architecture/topology_schema.yaml` must grow a dedicated field.

## Does not block execution

- whether the visible history interface should remain exactly `docs/archive_registry.md` or later become a broader `docs/history_registry.md`; V2 recommends `archive_registry.md` now to stay concrete.
- whether `current_state.md` should have a strict section template enforced by tests or only a smaller semantic contract.
- whether `source_rationale.yaml` should later gain explicit graph-complement metadata.

## Human preference only

- whether P2 should actually commit a tracked `.code-review-graph/graph_meta.json` sidecar after local verification, or leave that as a ready-but-deferred change.
- how much historical category detail should be exposed in `docs/archive_registry.md` versus kept inside archive bundles.
