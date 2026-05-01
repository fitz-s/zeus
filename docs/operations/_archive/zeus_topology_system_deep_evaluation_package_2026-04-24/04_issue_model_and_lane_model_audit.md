# 04 — Issue Model and Lane Model Audit

## Ruling

The current lane model is operationally useful but semantically under-modeled. The issue model is not rich enough for a machine-routable governance kernel.

## Current issue model

Current effective shape:

```text
code
path
message
severity
```

This is insufficient because mode-specific blocking currently has to infer meaning from severity, lane, path, or ad hoc filtering.

## Required issue fields

Minimum future shape:

```yaml
code: string
path: string
message: string
severity: error|warning|advisory
lane: navigation|closeout|strict|docs|source|tests|scripts|graph|...
scope: requested_file|required_companion|packet|global|derived_context|fixture|live_repo
owner_manifest: architecture/docs_registry.yaml|architecture/source_rationale.yaml|...
repair_kind: register_doc|add_source_rationale|classify_test|refresh_graph|...
blocking_modes: [strict, closeout]
related_paths: [...]
companion_of: optional path
maturity: provisional|candidate|promoted|deprecated
expires_at: optional date
confidence: high|medium|low
authority_status: authority|reference|derived_not_authority|evidence
repair_hint: concise human action
```

## Lane audit

| Lane | Current role | Current failure mode | Desired role | Desired blocking policy | Missing typed metadata | Needed improvements |
|---|---|---|---|---|---|---|
| navigation | Start agent route/context for task/files | Aggregates docs/source/history/reference health; error severity blocks route | Generate usable route digest; direct blockers only | Block only unreadable required authority, no route, requested-file obligation failure | lane, scope, blocking_modes, requested_paths, owner_manifest, repair_kind | Advisory-first, --strict-health opt-in |
| closeout | Packet closeout gate | Partial scoping; always-on lanes can still overblock; companion expansion weak | Changed-file + required-companion gate with global health sidecar | Block scoped issues and undeferred required companions | related_paths, companion_of, packet_scope, defer_allowed, receipt_required | Use typed issue policy and companion graph |
| strict | Full repo health gate | Useful but can be conflated with navigation | Explicit full-repo audit mode | Block all severity=error and selected warnings if promoted | mode=strict_full_repo, promotion_status | Keep separate from normal navigation |
| docs | Docs classification/default-read/current-state checks | Docs drift can block unrelated topology navigation | Docs system health and scoped docs closeout | Block docs packet or strict; warn in unrelated navigation | doc_class, truth_profile, freshness_class, registry_owner | Group by registry action |
| source | Source rationale/scoped AGENTS coherence | Missing rationale appears global; source knowledge thin | Source ownership/hazards/write routes | Block changed source; strict full source audit | source_role, write_route, hazard_badge, module_owner | Propose source_rationale stubs |
| tests | Test topology/law gates/skips/reverse antibodies | Live repo classifications and fixture laws mixed | Test classification + law-gate proof | Block test changes and strict; fixture tests deterministic | test_category, law_gate, live_health, fixture | Split live_topology marker |
| scripts | Script lifecycle/write-target safety | Good safety metadata but huge manifest; failures not repair-grouped | Script registry and dangerous-write gate | Block changed scripts and strict; warn unrelated navigation | lifecycle, class, target_db, apply_flag, write_target | Repair drafts for missing/expired scripts |
| graph | Derived structural context | Binary graph under-realized; stale/missing can confuse agents | Advisory impact/test route context | Never semantic blocker unless task explicitly requires graph evidence | authority_status, graph_freshness, graph_usability, limitation | Add textual sidecars/context appendices |
| reference replacement | Bulky reference replacement/removal hygiene | Hidden until docs/reference work; can block broadly | Prevent stale references and removed support doc leaks | Block docs/reference packets and strict | replacement_target, source_doc, direct_reference_allowed | Integrate docs_system book |
| history lore | Failure memory/antibodies | Lore can become default encyclopedia or noisy drift | Extract durable lessons; keep lore non-default | Block only declared fatal-misread/lore schema gates | lore_id, extraction_status, antibody_status | Promote recurring rules to module books |
| context packs | Task-shaped generated context | Can inherit manifest thinness and graph unreadability | Route-aware context with limitations and module cognition | Block only when route health for requested input is unusable | pack_profile, route_health, repo_health, limitation | Use module books and graph text sidecars |
| compiled topology | Generated normalized view | Shape encoded in tests more than docs | Stable derived topology output contract | Block strict/compiled lane when schema broken | compiled_field, authority_status, source_manifest | Document output contract |
| repair drafts | Proposed future lane | Missing | Generate stubs/patch proposals, non-authority | Never auto-block except missing draft generator in its packet | repair_kind, confidence, required_human_fields | Add after issue model |

## Blocking-mode policy

Use issue `blocking_modes`, not severity alone.

Examples:

```yaml
- code: docs_registry_unclassified_doc
  path: docs/reports/new_report.md
  severity: error
  lane: docs
  scope: global
  owner_manifest: architecture/docs_registry.yaml
  repair_kind: register_doc
  blocking_modes: [strict]
```

Same issue becomes closeout-blocking only if the changed file is `docs/reports/new_report.md` or if a changed companion requires docs registry update.

## Desired lane taxonomy

- `navigation_route`: route generation and required boot surfaces.
- `navigation_repo_health`: warnings, not blockers by default.
- `closeout_changed_file_gate`: changed-file issues and companion obligations.
- `strict_global_health`: full repo health.
- `manifest_schema`: manifest syntax/enum/ownership correctness.
- `derived_graph_context`: graph health and impact appendices.
- `repair_draft`: generated proposals.
- `live_repo_health`: current branch drift checks.
- `fixture_regression`: deterministic behavior checks.

## Priority

Implement the typed issue model and lane policy before broad manifest cleanup.
