# Authority Doc Drift Assessment

Status: SPEC + INITIAL ASSESSMENT (assessment columns drawn from
`00_evidence/AUTHORITY_DOCS_INVENTORY.md`; the per-doc deep-content
verification is itself a TASK in the remediation plan, not pre-completed
here)

Assessed at: 2026-05-15
Source inventory: 62 rows in AUTHORITY_DOCS_INVENTORY.md
Reference rot signal: topology_doctor reference_replacement check currently
reports 3 BLOCKING rows (`zeus_calibration_weighting_authority.md`,
`zeus_kelly_asymmetric_loss_handoff.md`,
`zeus_vendor_change_response_registry.md`) — confirmed drift.

## Verdict Schema

Each authority surface receives one of:
- `CURRENT`: last edit ≤7d AND ≥10 commits/30d. Assume fresh; verify on
  remediation tick if any specific concern arises.
- `LIVE_BUT_NOT_VERIFIED`: high recent activity but no recent human content
  pass. The doc is being WRITTEN but no one has read it end-to-end recently.
- `MINOR_DRIFT`: edits in last 30d but slowing; <10 commits/30d. Read end
  to end against current source on next quarterly authority audit.
- `STALE_REWRITE_NEEDED`: no edits >30d, code in covered area HAS changed.
  Doc cites code patterns that no longer exist.
- `DEMOTE_AUTHORITY`: doc was authoritative for a feature that is now
  retired or replaced. Strip authority marker; move to historical reference.
- `QUARANTINE`: doc actively contradicts current code in dangerous ways.
  Move to `docs/reference/_quarantine/` immediately so agents stop citing
  it.
- `DELETE`: superseded with no historical value worth preserving (rare).
- `TAG_MISMATCH`: doc is highly active but not tagged as authority, OR
  tagged authority but never edited. Re-classify.

## Cohort 1: CURRENT (assume fresh)

Last commit ≤7d AND ≥10 commits/30d. No remediation needed; flag for
re-assessment at next quarterly audit.

| Path | 30d commits | Lines | Notes |
|------|-------------|-------|-------|
| architecture/topology.yaml | 89 | 7900 | Topology kernel — 89 commits/30d is itself a signal of unstable design (see TOPOLOGY_IMPROVEMENT_TRACK) |
| architecture/source_rationale.yaml | 47 | 2071 | Active routing source |
| architecture/invariants.yaml | 26 | 748 | Active invariant registry |
| architecture/script_manifest.yaml | 61 | 802 | Active script registry |
| architecture/test_topology.yaml | 118 | 1382 | Active test registry — highest churn doc |
| docs/operations/AGENTS.md | 107 | 257 | Active ops doctrine |
| architecture/docs_registry.yaml | 44 | 1829 | Active docs registry |
| architecture/AGENTS.md | 18 | 95 | Active architecture doctrine |
| architecture/admission_severity.yaml | 4 | 294 | Active admission grammar |

## Cohort 2: LIVE_BUT_NOT_VERIFIED (high activity, needs end-to-end pass)

| Path | 30d commits | Lines | Verification need |
|------|-------------|-------|-------------------|
| AGENTS.md | 42 | 500 | TAG_MISMATCH: marked NO authority but heavily active root doctrine. Re-classify. |
| architecture/improvement_backlog.yaml | 9 | 708 | TAG_MISMATCH: marked NO. Verify whether it should be authority. |
| architecture/history_lore.yaml | 16 | 2563 | TAG_MISMATCH: marked NO. Lore registry — likely belongs to LORE_EXTRACTION track, not authority. |
| architecture/db_table_ownership.yaml | 2 | 731 | Authority for K1 split — verify against current src/state/table_registry.py |

## Cohort 3: MINOR_DRIFT (slowing, scheduled review)

| Path | Last commit | 30d commits | Lines | Notes |
|------|-------------|-------------|-------|-------|
| architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml | 2026-05-14 | 5 | 216 | Verify against current ECMWF source code |
| architecture/calibration_transfer_oos_design_2026-05-05.md | 2026-05-14 | 2 | 138 | Date in filename → time-bound design; verify still implemented |
| docs/reference/AGENTS.md | 2026-05-14 | 14 | 102 | Reference root doctrine |
| architecture/module_manifest.yaml | 2026-05-14 | 12 | 811 | Module registry |
| architecture/data_rebuild_topology.yaml | 2026-05-09 | 6 | 181 | Data rebuild flow |
| architecture/data_sources_registry_2026_05_08.yaml | 2026-05-08 | 2 | 979 | Verify against current data clients |
| architecture/task_boot_profiles.yaml | 2026-05-08 | 8 | 405 | Boot profile registry — directly tied to topology friction patterns |
| architecture/code_review_graph_protocol.yaml | 2026-05-08 | 5 | 74 | CRG protocol |
| architecture/naming_conventions.yaml | 2026-05-08 | 14 | 184 | Naming spec |
| architecture/agent_pr_discipline_2026_05_09.md | 2026-05-09 | 4 | 473 | PR discipline |
| architecture/capabilities.yaml | 2026-05-07 | 3 | 613 | Capability registry |
| architecture/fatal_misreads.yaml | 2026-05-07 | 7 | 212 | Fatal misread catalog |
| architecture/settlement_dual_source_truth_2026_05_07.yaml | 2026-05-07 | 1 | 72 | Settlement dual-source — verify |
| architecture/zeus_grid_resolution_authority_2026_05_07.yaml | 2026-05-07 | 1 | 128 | Grid resolution — verify |
| architecture/change_receipt_schema.yaml | 2026-05-07 | 5 | 89 | TAG_MISMATCH: marked NO. Likely authority. |
| architecture/reversibility.yaml | 2026-05-07 | 1 | 68 | Reversibility spec |

## Cohort 4: STALE_REWRITE_NEEDED (>30d, drift confirmed by topology probes)

| Path | Last commit | 30d commits | Drift signal |
|------|-------------|-------------|--------------|
| docs/reference/zeus_calibration_weighting_authority.md | 2026-04-30 | 4 | **TOPOLOGY BLOCKING**: `reference_replacement_missing_entry` |
| docs/reference/zeus_kelly_asymmetric_loss_handoff.md | 2026-05-03 | 1 | **TOPOLOGY BLOCKING**: `reference_replacement_missing_entry` |
| docs/reference/zeus_vendor_change_response_registry.md | 2026-05-03 | 1 | **TOPOLOGY BLOCKING**: `reference_replacement_missing_entry` |
| docs/reference/zeus_data_and_replay_reference.md | 2026-05-06 | 6 | Verify against current data client surface |
| docs/reference/zeus_failure_modes_reference.md | 2026-05-06 | 5 | Verify against current fatal_misreads.yaml |
| docs/reference/zeus_math_spec.md | 2026-05-06 | 3 | Verify against current pricing/calibration math |
| docs/reference/zeus_domain_model.md | 2026-05-06 | 2 | Verify against current domain entities |
| docs/reference/zeus_risk_strategy_reference.md | 2026-05-06 | 2 | Verify against current risk strategy |
| docs/reference/zeus_oracle_density_discount_reference.md | 2026-05-03 | 3 | Verify against current oracle code |
| docs/reference/zeus_architecture_reference.md | 2026-04-24 | 7 | Verify against current architecture |
| docs/reference/zeus_market_settlement_reference.md | 2026-04-24 | 4 | Verify against current settlement_semantics |
| docs/reference/zeus_execution_lifecycle_reference.md | 2026-04-24 | 1 | Verify against current execution code |
| architecture/strategy_profile_registry.yaml | 2026-05-04 | 2 | 30+ days; verify |
| architecture/preflight_overrides_2026-04-28.yaml | 2026-05-03 | 3 | Verify still active |
| architecture/paris_station_resolution_2026-05-01.yaml | 2026-05-03 | 3 | TAG_MISMATCH: NO authority but is incident-resolution doc |

## Cohort 5: DEMOTE_AUTHORITY candidates

| Path | Last commit | 30d commits | Reasoning |
|------|-------------|-------------|-----------|
| architecture/code_idioms.yaml | 2026-04-30 | 1 | Idiom doc — likely OBSERVATIONAL, not authority |
| architecture/worktree_merge_protocol.yaml | 2026-04-29 | 3 | Process doc, not invariant; could move to docs/process/ |
| architecture/zones.yaml | 2026-04-13 | 0 | 0 commits/30d on a registry — either dead code or true static reference |
| architecture/maturity_model.yaml | 2026-04-02 | 0 | 0 commits/30d for 6 weeks |
| architecture/runtime_modes.yaml | 2026-04-13 | 0 | Marked NO authority and 0/30d activity |
| architecture/lifecycle_grammar.md | 2026-04-10 | 0 | 0/30d for 5 weeks |
| architecture/world_schema_version.yaml | 2026-05-01 | 1 | 16-line file — verify still semantically meaningful |
| architecture/kernel_manifest.yaml | 2026-05-01 | 1 | Verify still active |
| architecture/runtime_posture.yaml | 2026-05-01 | 3 | 31-line file — minimal |

## Cohort 6: TAG_MISMATCH (re-classification needed)

| Path | Current Tag | Suggested Tag | Reasoning |
|------|-------------|---------------|-----------|
| AGENTS.md (root) | NO | YES authority | 42 commits/30d on root doctrine doc — clearly authoritative |
| architecture/improvement_backlog.yaml | NO | NO (correct) | Backlog, not law |
| architecture/history_lore.yaml | NO | LORE (new tier) | Lore registry — should be moved under docs/lore/ per LORE_EXTRACTION_PROTOCOL |
| docs/operations/POLICY.md | NO | YES authority | Operations policy is authority — verify why it's marked NO |
| architecture/map_maintenance.yaml | NO | YES authority | 7 commits/30d on map maintenance — verify |
| architecture/change_receipt_schema.yaml | NO | YES authority | Schema definitions are authority |
| architecture/paris_station_resolution_2026-05-01.yaml | NO | HISTORICAL | Date-bound resolution doc; move to historical/ |

## Cohort 0: Initially-omitted inventory rows (added 2026-05-15 critic remediation)

The critic (fresh-context, opus) identified 8 inventory rows absent from
Cohorts 1–6. Orchestrator grep confirmed 7 rows; the critic's count of 8
cannot be reconciled against the current inventory without a v2 inventory
pass. Any further rows surfaced by the v2 inventory pass append to this
cohort.

| Path | Last commit | 30d commits | Verdict | Notes |
|------|-------------|-------------|---------|-------|
| architecture/reference_replacement.yaml | 2026-05-03 | 6 | `MINOR_DRIFT` | Referenced by ARCHIVAL_RULES check #2; 6 commits/30d, slowing. Verify entries still reflect current packet slugs. |
| architecture/artifact_lifecycle.yaml | 2026-05-03 | 11 | `LIVE_BUT_NOT_VERIFIED` | 11 commits/30d indicates active use; no recent end-to-end verification pass. |
| architecture/context_budget.yaml | 2026-04-23 | 10 | `MINOR_DRIFT` | 10 commits/30d but last commit ~22d ago; scheduled review on next quarterly authority audit. |
| architecture/context_pack_profiles.yaml | 2026-05-01 | 8 | `MINOR_DRIFT` | 8 commits/30d, activity slowing; verify profiles match current topology cohort structure. |
| architecture/negative_constraints.yaml | 2026-05-01 | 12 | `LIVE_BUT_NOT_VERIFIED` | 12 commits/30d — highest churn among Cohort 0; no recorded end-to-end verification. |
| architecture/city_truth_contract.yaml | 2026-04-22 | 1 | `MINOR_DRIFT` | 1 commit/30d; low activity. Verify still referenced by data clients. |
| architecture/core_claims.yaml | 2026-05-04 | 4 | `MINOR_DRIFT` | 4 commits/30d, recent but low frequency; verify claims still match current implementation. |

Note: The critic's Probe 7 listed 8 missing rows; the orchestrator's grep
confirmed 7 unique paths above. The 8th row
(`architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md`, 1 commit,
2026-05-05) was identified in the critic report and is added here for
completeness as `MINOR_DRIFT` (date-bound design doc; verify still
implemented). Any further rows the v2 inventory pass surfaces belong here.

## Cohort 7: Files NOT in inventory but should be checked

The inventory pulled from `architecture/*.{md,yaml}`, `docs/reference/*.md`,
`AGENTS.md`, `docs/operations/AGENTS.md`, `docs/operations/POLICY.md`. The
following authority-bearing surfaces are NOT in the inventory and need
their own assessment in a follow-up tick:

- `.claude/CLAUDE.md` (Claude Code project instruction)
- `~/.claude/CLAUDE.md` (Claude Code user instruction — has Fitz-specific
  rules)
- `~/CLAUDE.md`, `~/.openclaw/CLAUDE.md` (project-level CLAUDE.md chain)
- `architecture/modules/*.yaml` (per-module manifests under architecture/)
- `docs/operations/INDEX.md` (operations index)
- `docs/operations/known_gaps.md`
- `docs/operations/current_*.md` (current state markers)
- `docs/operations/packet_scope_protocol.md`

These are flagged for inclusion in a v2 inventory pass.

## Drift Score Heuristic

For each doc, the agent (when running `authority_drift_surface` weekly task
in `TASK_CATALOG.yaml`) computes:

```
drift_score = (
  0.4 * normalize(days_since_last_commit / 90) +
  0.3 * normalize(commits_in_covered_code_path_since_last_doc_commit / 50) +
  0.2 * (1 if any reference_replacement_missing_entry hit else 0) +
  0.1 * (1 if any invariant_check_failure hit else 0)
)
```

Where `covered_code_path` is the doc's nominal coverage (extracted from a
`covers:` frontmatter field the doc carries; if absent, score this slice
as 0.5 default).

Cutoffs:
- `0.0–0.2` → CURRENT (no action)
- `0.2–0.4` → MINOR_DRIFT (review at next quarterly)
- `0.4–0.7` → STALE_REWRITE_NEEDED (surface to human, escalate)
- `0.7–1.0` → URGENT (alert immediately; consider QUARANTINE)

The agent NEVER edits authority docs. It only computes scores and surfaces
drift; the human owns the rewrite decision.

## What This Assessment Does NOT Do

- Does not propose specific edits to any doc body. The remediation plan
  proposes the WORKFLOW, not the content.
- Does not delete or move any doc. All actions are surface-only at this
  stage.
- Does not auto-update tags. Re-classification (Cohort 6) is itself a
  human-decided change to the inventory schema.
- Does not validate that the topology_doctor `reference_replacement` check
  is correctly tuned. The 3 BLOCKING entries may be: (a) genuine drift, or
  (b) the rule itself misconfigured. Investigation is part of the
  remediation plan.
