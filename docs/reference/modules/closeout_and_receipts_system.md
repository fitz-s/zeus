# Closeout and Receipts System

> Status: reference, not authority. See `docs/authority/zeus_current_delivery.md`, `architecture/artifact_lifecycle.yaml`, and `architecture/change_receipt_schema.yaml` for authority.

## Purpose

The closeout and receipts system ensures a packet or explicit closeout claim
ends with scoped validation, companion updates, work evidence, and explicit
deferrals. It protects Zeus from both under-closing risky work and over-blocking
a packet on unrelated global drift.

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
- `risk_tier` and `gate_budget`: generated runtime metadata that explains why
  the packet needed light or heavy evidence.
- `claims_evaluated`, `claims_blocked`, and `claims_advisory`: claim-scoped
  gates requested with `--claim`; these can block the specific completion claim
  without making the same warning universal.
- `warning_lifecycle`: packet-local receipt deferrals. An expired deferral is
  promoted only when the same warning is still active in the matching changed
  file scope or explicitly requested claim scope.
- `migration_notes`: generated adoption guidance for the runtime path. Legacy
  commands remain supported; deprecation warnings are not emitted until runtime
  command usage is proven by two packet receipts.

## Hidden obligations

- Missing work records and missing receipts are real blockers for packet
  closeout or an explicit `packet_closeout_complete` claim. They are not a
  default artifact stack for direct T0/T1 edits.
- A completed operation should recycle context into a compact feedback capsule:
  what was promoted/summarized/discarded/left local, one to three Zeus
  improvement insights, and topology helped/blocked notes. Direct work records
  this in the final response; packet closeout records it only in an
  already-required work log or receipt.
- Planning-lock files need plan evidence before implementation closes.
- A deferral is only valid when recorded in the packet evidence; silent omission is not a deferral.
- Warning deferrals must name an owner, an invalidation condition, and a bounded
  date (`expires_at` or `deferred_until`). Open-ended “known issue” buckets are
  not valid closeout evidence.
- Global health must remain visible even when scoped closeout passes.
- Graph freshness is warning-only unless the packet requests a graph-impact
  claim such as `--claim graph_impact_validated`.
- Closeout must not mutate runtime truth or produce canonical DB facts.
- Receipts should prove the completion claim; they should not become long
  diaries for unrelated warnings.
- Feedback capsules should improve future work without widening the completed
  operation. Promote a note into code/docs/topology changes only through a new
  admitted route or when the current route already owns that surface.

## Failure modes

- A packet passes local tests but lacks a receipt/work log and becomes unreplayable.
- Closeout hides repo-wide drift after P0 scoping, making reviewers think the whole repo is green.
- An agent fixes unrelated docs/source registry failures to make closeout pass, widening the packet.
- A missing companion is treated as a warning even though changed-file law requires it.
- A stale graph or unrelated global warning is treated as a universal closeout
  blocker even when no completion claim depends on it.
- A warning deferral expires but the warning is unrelated to the changed files
  or the requested claim; it should stay visible and not block the packet.
- A well-intended improvement note becomes mandatory evidence or immediate
  follow-on work, turning context recovery into scope creep.

## Repair routes

- Use `update_companion` for missing scoped router/registry/map updates.
- Use `add_registry_row` for newly tracked docs/tests/scripts/source files.
- Use `propose_owner_manifest` when closeout reveals ambiguous ownership.
- For packet closeout, record severe blockers in packet work logs and receipts
  rather than deleting the gate.
- Record runtime-local artifact treatment: promoted, summarized, discarded, or
  left local.
- Keep operation feedback short and actionable. If a topology friction repeats,
  add a focused regression or route-card/doc repair; if it is a one-off, keep it
  as a closeout note.

## Cross-links

- `docs/reference/modules/topology_doctor_system.md`
- `docs/reference/modules/manifests_system.md`
- `docs/reference/modules/docs_system.md`
- `docs/operations/AGENTS.md`
- `docs/authority/zeus_current_delivery.md`
