# Topology Doctor System

> Status: reference, not authority. See `AGENTS.md`, `workspace_map.md`, `architecture/**`, and `docs/authority/**` for authority.

## Purpose

The topology doctor is Zeus's executable routing and closeout compiler. It reads machine manifests, checks mesh drift, emits scoped navigation digests, and separates route blockers from global repository health so agents can change the right surface without hiding unrelated debt.

## Authority anchors

- `AGENTS.md` defines the mandatory topology-navigation workflow.
- `workspace_map.md` defines visibility classes and default read order.
- `architecture/topology.yaml` defines coverage roots and digest inputs.
- `architecture/topology_schema.yaml` defines compiled topology and issue JSON contracts.
- `architecture/map_maintenance.yaml`, `artifact_lifecycle.yaml`, and `change_receipt_schema.yaml` define closeout companions.
- `tests/test_topology_doctor.py` is the regression surface for topology-doctor behavior.

## How it works

Topology doctor has five layers:

1. **Loaders** read `architecture/**`, scoped `AGENTS.md`, docs registries, and current operation pointers.
2. **Validators** emit `TopologyIssue` objects with legacy fields and optional typed metadata.
3. **Mode policy** decides whether an issue blocks `navigation`, `closeout`, `strict_full_repo`, or remains `global_health` context.
4. **Runtime route cards** compile an operation vector from finite facts, then
   summarize admission, risk tier, dominant driver, next action, safe next
   files, gate budget, requested claims, artifact persistence target, merge
   evidence predicate, provenance notes, and expansion hints before appendices.
5. **Renderers** emit human and JSON outputs for navigation, strict lanes, context packs, and closeout.

The checker family is intentionally split across `scripts/topology_doctor_*.py`: registry/docs/source/test/script checks report facts; `scripts/topology_doctor.py` and `scripts/topology_doctor_cli.py` expose the public facade; `scripts/topology_doctor_closeout.py` compiles changed-file closeout.

`build_impact()` is adapter-backed. Source files still use
`architecture/source_rationale.yaml`; architecture manifests, scripts, docs, and
tests use their owning manifests or scoped routers so non-source work can get a
bounded impact summary without pretending every file is source.

## Hidden obligations

- Navigation must not treat unrelated repo-health drift as a direct blocker, but it must continue to expose that drift.
- When packet closeout or `packet_closeout_complete` is the active claim,
  closeout must block missing work records, missing receipts, changed-file
  companion failures, and changed-file law violations. Those artifact gates are
  not default prerequisites for direct T0/T1 edits.
- JSON issue compatibility is durable: `code`, `path`, `message`, and `severity` remain present in legacy output.
- Typed issue metadata is additive and exists to route repair work, not to invent new law.
- Every new top-level script/test/doc route needs its owning manifest updated when the manifest owns that class of fact.
- Typed `intent` may select a digest profile, but admission still reconciles
  files against `allowed_files` and forbidden patterns.
- Operation-vector fields are the preferred disambiguation layer for runtime
  facts: `operation_stage`, `mutation_surfaces`, `side_effect`,
  `artifact_target`, and `merge_state`. They prevent topology from depending on
  endless natural-language alias tables. Profile matching remains compatibility
  routing; admission still owns write authority.
- High-fanout file-only evidence is soft ambiguity: the doctor should return
  advisory-only/no-admission instead of making navigation look failed. Strong
  phrase ties and invalid typed intents remain hard ambiguous.
- `--route-card-only` is the lightweight first-screen path for T0/T1 work; it
  should not print appendices, repo-health lists, or unrelated drift. It should
  still show the fields a real runtime needs to avoid retry loops:
  `dominant_driver`, `why_not_admitted`, `suggested_next_command`,
  `persistence_target`, and `merge_evidence_required` when present.
- `--claim` turns optional evidence into claim-scoped gates. A stale graph
  blocks `graph_impact_validated`, not ordinary navigation or closeout.
- Workflow, skill-use, and work-ethic guidance belongs in generated context
  packs, not new authority documents.
- Generated runtime packets should remind agents to close an operation with a
  compact feedback capsule: context recovery, Zeus improvement insights, and
  topology helped/blocked notes. The topology note should name route/admission/risk,
  semantic match, concrete help, concrete friction, and next topology delta or
  `none_observed`. This is a final-response or consumed-packet habit, not an
  extra closeout gate.

## Failure modes

- Global docs/source/test drift blocks a narrow route and causes agents to either stop prematurely or over-edit unrelated files.
- Strict repo-health output is mistaken for scoped closeout and hides packet evidence gaps.
- Topology issues stay too flat to route repair ownership, causing manifest fixes to become manual guesswork.
- A graph or context-pack appendix is treated as authority rather than derived context.
- Process controls become bureaucratic when every stale warning blocks every
  task instead of only the claim that depends on it.
- Generic fixes become bureaucratic when file-only uncertainty is reported as
  a topology failure instead of advisory no-admission.
- Feedback loops become bureaucratic when they require a new file per task or
  turn every observation into immediate scope expansion.

## Repair routes

- Use `repair_kind: add_registry_row` when a tracked file lacks its owning registry row.
- Use `repair_kind: update_companion` when a file change requires a scoped AGENTS, registry, or map update.
- Use `repair_kind: refresh_graph` only for graph freshness/coverage debt; do not use it for semantic proof.
- Read `code_review_graph_status.details.graph_health` to see which graph
  freshness/coverage facts invalidate graph claims; it is generated health
  context, not semantic authority.
- Use `repair_kind: propose_owner_manifest` when ownership is ambiguous and P3/P4-level planning is required.
- Run `python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>` before edits and `closeout` with changed files before closure.
- Use `--route-card-only` for quick orientation and `--claim <claim_id>` when
  the completion statement depends on graph, repo-health, semantic boot, packet
  closeout, or live authorization evidence.
- Start future runtime-oriented work with the composed command when the caller
  needs one packet rather than separate digest/bootstrap/context invocations:
  `python scripts/topology_doctor.py runtime --task "<task>" --files <files> --intent "<intent>" --task-class <class> --write-intent <intent>`.
- When wording is ambiguous, provide typed operation facts instead of inventing
  another phrase: `--operation-stage closeout --artifact-target final_response`
  for a direct feedback capsule, `--operation-stage merge --merge-state clean`
  for clean merge closeout, or `--mutation-surface source_behavior` for a
  source-control edit with admitted files.
- Use `python3 scripts/topology_doctor.py runtime ...` when a caller needs the
  composed agent-runtime packet: route card, optional semantic boot, optional
  role context, claims, dispatch guidance, gate budget, and artifact-treatment
  hints.
- Use role context packs (`explorer`, `executor`, `critic`, `verifier`) when the
  next agent needs a bounded runtime contract rather than the full packet.
- Treat `provenance_notes` as first-layer routing evidence. Script manifest
  lifecycle/status, docs path status, and runtime scratch classification should
  appear in the route card before an agent has to read broad reference docs.
- Use operation feedback to improve topology only after separating a one-off
  annoyance from repeatable routing friction. Repeatable friction should become
  a focused test, manifest repair, or reference-doc adjustment; one-off notes
  stay in the closeout capsule.
- For direct feedback tasks, prefer
  `--intent "direct operation feedback capsule"` with no files for final
  response only, or with an already-required packet `work_log.md`/`receipt.json`
  for packet closeout. `.omx/context/*handoff*` remains forbidden for repo
  persistence unless a separate route admits it.
- When script/docs/test health emits unrelated global drift, preserve both
  facts: the changed-surface verdict and the repo-wide drift summary. Do not
  let the global summary become a claim blocker unless the claim depends on it.

## Cross-links

- `docs/reference/modules/topology_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/closeout_and_receipts_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/reference/modules/code_review_graph.md`
