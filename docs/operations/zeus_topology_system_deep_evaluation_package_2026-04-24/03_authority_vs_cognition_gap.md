# 03 — Authority vs Cognition Gap

## Mandatory ruling

Zeus now has enough machine routing to be dangerous if it is mistaken for enough understanding.

The repo has many machine-readable surfaces and a central topology doctor. That is good for routing and governance. But online-only agents also need dense explanatory surfaces. At present, some of the deepest Zeus knowledge is still trapped in places that are not safe, stable, or efficient as default reasoning inputs.

## What Zeus has enough of

### Machine routing

Zeus has meaningful machine routing through:

- `architecture/topology.yaml`,
- `architecture/docs_registry.yaml`,
- `architecture/module_manifest.yaml`,
- `architecture/source_rationale.yaml`,
- `architecture/test_topology.yaml`,
- `architecture/script_manifest.yaml`,
- `architecture/map_maintenance.yaml`,
- `architecture/context_budget.yaml`,
- `architecture/code_review_graph_protocol.yaml`,
- topology doctor lanes and context packs.

This is enough to classify surfaces, enforce some obligations, and generate task-shaped context.

### Authority separation

The repo repeatedly distinguishes:

- executable source/tests/DB truth,
- architecture and machine manifests,
- current operations docs,
- reference-only docs,
- reports/artifacts as evidence,
- graph/topology as derived context.

This is healthy and should be preserved.

## What Zeus does not have enough of

### Dense agent cognition

A zero-context agent needs more than classifications. It needs:

- why a rule exists,
- what historical failure it prevents,
- what companion surfaces must move together,
- what tests encode hidden law,
- what false shortcuts are fatal,
- what graph can and cannot prove,
- how a packet should close without widening scope.

Much of that is still not durable in module/system books.

## Knowledge trapped in tests

`tests/test_topology_doctor.py` carries hidden system law, including:

- archive interface/default-read behavior,
- docs registry parent coverage expectations,
- operations/current_state receipt-binding behavior,
- compiled topology output shape,
- graph protocol order and derived-not-authority status,
- graph status warning/error boundaries,
- module book required sections,
- module manifest required fields,
- context-pack route health semantics,
- freshness metadata rules,
- closeout lane summaries and telemetry.

These should not live only in tests. Tests should verify law; they should not be the only readable law.

## Knowledge trapped in packet plans

The uploaded plan and active operations docs contain major design decisions:

- lane policy abstractions,
- advisory-first navigation,
- typed issue fields,
- deterministic fixture split,
- repair drafts,
- graph hardening,
- output UX,
- rollout/promotion policy.

Those are not yet fully durable in authority/reference surfaces. They should be promoted into module/system books and then implemented packet-by-packet.

## Knowledge trapped in history_lore

`history_lore.yaml` is valuable, but lore is not a good default cognition layer by itself. It records failure modes, wrong moves, antibodies, residual risks, and task routing. For online-only agents, the durable module books should extract the recurring lessons and leave the detailed lore as a supporting registry.

Promote:

- recurring failure classes,
- named antibodies,
- route implications,
- “never do this” constraints,
- repair patterns.

Do not promote:

- dated narrative clutter,
- packet-by-packet history,
- stale operational details.

## Knowledge trapped in graph

The tracked Code Review Graph is correctly derived context, but a binary `graph.db` is not enough for online-only agents. Graph knowledge that should be extracted includes:

- high-centrality topology_doctor helpers,
- hidden dependent tests,
- route clusters around docs/source/test/script manifests,
- bridge/hub nodes,
- impacted modules for changed files,
- “graph says look here” appendices.

This should become small textual sidecars or context-pack appendices, labeled derived/not-authority.

## Knowledge trapped in source comments

Helper modules contain lifecycle headers and purpose comments that matter. Examples:

- docs checks own docs-tree, operations-registry, runtime-plan, and docs-registry checks;
- script checks validate top-level script lifecycle, naming, and write-target metadata;
- context-pack builder says generated context is provisional and graph/lore must not become authority.

These comments should inform module books and manifest ownership docs.

## Knowledge trapped in archives

Archives contain rich historical maps and deep restructuring context. They should remain cold evidence, not default-read surfaces. The right pattern is selective extraction:

1. identify recurring current law or durable lesson,
2. promote to authority/reference with provenance,
3. keep archive body non-default,
4. avoid treating old packet plans as live instructions.

## Required rehydration move

Create a durable cognition layer consisting of:

- `docs/reference/modules/topology_system.md` expanded,
- `docs/reference/modules/code_review_graph.md` expanded,
- new or expanded `docs_system.md`,
- new or expanded `manifests_system.md`,
- new `topology_doctor_system.md`,
- new `closeout_and_receipts_system.md`.

These should remain reference-only. They should explain, not outrank, machine manifests and executable truth.
