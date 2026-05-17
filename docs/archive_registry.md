# Archive Registry

**DEPRECATED 2026-05-16.** Archive content moved to `docs/operations/archive/<YYYY>-Q<N>/`.
See `docs/operations/archive/2026-Q2/INDEX.md` for the 2026-Q2 migration index.
Active archival rules: `ARCHIVAL_RULES.md` (at `docs/authority/ARCHIVAL_RULES.md`).

This file is retained to preserve back-references. Do not update it; it is historical only.

---

This file is the visible historical interface for Zeus.

It is not authority. It does not turn archive bodies into default context.

## What this file is for

Use this file when you need to answer:

- when archive material is appropriate to read
- what kinds of archive categories exist
- how to label archive-derived claims
- what guardrails apply before promoting historical material into active docs

## Default rule

Archive bodies are historical cold storage.

- They are not peer authority to `architecture/**`, active packet docs, source
  code, tests, or canonical DB truth.
- They are not default-read boot surfaces.
- They may be consulted deliberately when a task needs historical evidence.

Visible historical protocol:

- `docs/archive_registry.md` - access and promotion rules
- `architecture/history_lore.yaml` - compressed durable lessons

Cold historical storage when present locally:

- `docs/archives/**`
- local archive bundles such as `docs/archives.zip`
- retired overlays, scratch packages, and archived work packets

Do not assume those cold bodies are reviewer-visible.

## When to use archives

Read archives only when the task explicitly needs one of these:

- prior-failure evidence
- old packet lineage or decision history
- proof that a proposed fix was already tried and rejected
- secret-contamination or artifact-provenance review
- historical context dense enough that `architecture/history_lore.yaml` is not
  sufficient

Prefer `architecture/history_lore.yaml` first. Only open raw archive material
when the dense lore card is insufficient.

## Retrieval Decision Tree

Use this order:

1. Start with current law: `AGENTS.md`, `workspace_map.md`, relevant
   `architecture/**` manifests, active packet docs, and source/tests when
   behavior is involved.
2. Check `architecture/history_lore.yaml` for a dense card matching the task.
3. If the lore card is enough, stop. Do not open archive bodies.
4. If a live question still needs historical proof, identify the narrow archive
   category and the smallest specific file or packet needed.
5. Before reading or promoting anything, assume contamination and scan for
   secrets, binary debris, local-only paths, and obsolete operating modes.
6. Promote only a rewritten, current-tense lesson into an active surface.

Stop immediately if the archive material would be used to override current
source, tests, manifests, or canonical DB truth. That requires a new packet, not
archive lookup.

## Archive categories

Typical categories include:

- work packets
- governance and design notes
- audits, findings, and investigations
- migration and rebuild material
- research, reports, and results
- overlay packages and local scratch residue
- binary or mixed artifacts such as `.db`, `.xlsx`, `.pyc`, and platform junk

These categories are evidence classes, not authority classes.

## Category Guide

| Category | Use | Do not use for |
|---|---|---|
| Work packets | Prior scope, decisions, and closeout evidence | Current active packet truth |
| Governance/design notes | Historical rationale and rejected alternatives | Present-tense authority without manifest backing |
| Audits/findings/investigations | Repeated failure modes and risk patterns | Runtime behavior claims without code/test proof |
| Migration/rebuild material | Provenance for data or schema decisions | Live DB mutation authority |
| Research/reports/results | Evidence and hypotheses | Strategy promotion by itself |
| Overlay/local scratch | Explaining drift or abandoned modes | Default onboarding or active law |
| Binary/mixed artifacts | Provenance only after explicit handling | Direct active docs authority |

## How to cite archive material

Any claim derived from archive material must be labeled:

`[Archive evidence]`

Use summaries, not long raw excerpts. Do not silently blend archive claims into
present-tense law.

## Promotion guardrails

Historical material may be promoted into active docs only when all of the
following are true:

1. it solves a still-live problem
2. it is consistent with current manifests and runtime truth, or an explicit
   packet is superseding them
3. it has been sanitized
4. the promoted result is rewritten into active form instead of copied
   wholesale

## Promotion Checklist

Before promoting any historical lesson, confirm:

- Live need: the lesson prevents a still-plausible failure.
- Current consistency: the lesson agrees with active manifests, source, tests,
  and packet state, or an explicit packet supersedes them.
- Sanitization: no secrets, credentials, private tokens, binary debris, or
  accidental local-only data are carried forward.
- Density: the promoted result is a compact rule, guardrail, or lore card, not
  a chronological summary.
- Antibody: the promoted result names a test, manifest, checker, runbook, or
  explicit residual risk.
- Labeling: archive-derived claims are marked `[Archive evidence]`.
- Placement: durable law goes to manifests/tests/authority docs; compressed
  memory goes to `architecture/history_lore.yaml`; access policy stays here.

## Rehydration Extraction Ledger

The 2026-04-23 authority rehydration packet mined the following archive bodies
for durable lessons, then rewrote those lessons into active docs or lore rather
than promoting the bodies themselves:

| Archive evidence consulted | Durable load rehydrated into |
|---|---|
| `docs/archives/audits/legacy_audit_truth_surfaces.md` | `docs/reference/modules/state.md`, `docs/reference/modules/data.md`, `docs/reference/modules/observability.md`, `architecture/history_lore.yaml` |
| `docs/archives/findings/exit_failure_analysis.md` | `docs/reference/modules/engine.md`, `docs/reference/modules/execution.md`, `docs/reference/modules/riskguard.md`, `architecture/history_lore.yaml` |
| `docs/archives/traces/settlement_crisis_trace.md` | `docs/reference/modules/contracts.md`, `docs/reference/modules/state.md`, `docs/reference/modules/execution.md`, `architecture/history_lore.yaml` |
| `docs/archives/architecture/zeus_blueprint_v2.md` | `docs/reference/modules/engine.md`, `docs/reference/modules/execution.md`, `architecture/history_lore.yaml` |
| `docs/archives/reports/strategy_failure_analysis.md` | `docs/reference/modules/strategy.md`, `docs/reference/modules/engine.md`, `docs/reference/modules/execution.md` |
| `docs/archives/investigations/agent_edit_loss_investigation.md` | `docs/reference/modules/state.md`, `architecture/history_lore.yaml` |

This ledger documents extraction only. The archive bodies remain historical-only
and non-default.

## Contamination warning

Treat archive bodies as potentially contaminated until proven otherwise.

Known risks include:

- plaintext secret references
- local absolute paths
- binary debris and cache artifacts
- stale overlays that describe abandoned operating modes
- historical DBs, spreadsheets, and generated outputs that look factual but are
  only provenance/evidence

Before promoting any archive-derived content:

- scan for secrets
- redact sensitive lines
- remove laptop-specific details unless they are themselves the evidence
- rewrite into concise current-tense language

Known contamination examples from the reconstruction package review include
plaintext `WU_API_KEY` references in historical markdown and mixed `.db`,
`.xlsx`, `.pyc`, and `.DS_Store` debris. Treat these as examples of the class
of risk; do not copy those archive bodies into active docs.

## What not to do

- do not make archives default-read
- do not copy archive bodies wholesale into active docs
- do not promote `.db`, `.xlsx`, `.pyc`, `.DS_Store`, or scratch artifacts into
  authority
- do not let archive prose overrule manifests, tests, or present-tense source
  behavior

## 2026-04-24 closure archive — `docs/operations/` packet triage

A Sonnet-driven audit on 2026-04-24 classified 21 of 23 packets in
`docs/operations/` as CLOSED or CLOSED-with-lore-extracted. **All 21
were physically archived to `docs/archives/packets/` on 2026-04-24**
(operator chose Option A — move to `docs/archives/packets/` — as the
archive cold-storage path). Lore cards were extracted into
`architecture/history_lore.yaml` (13 new cards: see IDs in the
"Cards extracted 2026-04-24" section of that file) BEFORE archive
for Batch 2; the durable content rides forward in lore while the
raw bodies consult-on-demand from the archive path.

**Text references elsewhere in the repo (src/ comments, YAML
manifest `why:` pointers, test docstrings) that cite the OLD
`docs/operations/task_*` paths now point to archived locations —
readers should substitute `docs/operations/` → `docs/archives/packets/`
for any 2026-04-13 / -14 / -16 / -19 / -20 / -21 / -22 / -23 packet
in the list below. None of these refs are runtime-loaded (Python
imports / file readers); they are prose/YAML `why` pointers.**

**Git-tracking split (per `.gitignore:11` `docs/archives/` + AGENTS.md
§History "raw archive bodies are local historical cold storage, not
peer authority"):**
- Packets that were git-tracked before the move remain git-tracked
  at the new archived path (20 of 21). `git log --follow` preserves
  history. `gitignore` does not un-track files that were tracked
  before the pattern was added.
- The forensic package (`zeus_world_data_forensic_audit_package_
  2026-04-23/`) was never git-tracked; it stays local-only under
  the new archived path. Not visible to `git clone` readers unless
  they reproduce the body manually from the lore cards.

This split matches the intended archive protocol: the durable content
rides forward via `architecture/history_lore.yaml` (in-git); the raw
bodies that were already git-tracked continue to be consultable via
git archaeology; bodies that were never tracked stay local-only cold
storage.

### Batch 1 — ARCHIVE_NOW (archived 2026-04-24; lore not extracted separately)

Explicit closure evidence + low durable-lore density. Bodies now at
`docs/archives/packets/{name}/`:

- `task_2026-04-13_topology_compiler_program.md` (4K)
- `task_2026-04-23_graph_refresh_official_integration/` (12K)
- `task_2026-04-16_function_naming_freshness/` (12K)
- `task_2026-04-19_code_review_graph_topology_bridge/` (12K)
- `task_2026-04-20_code_review_graph_online_context/` (12K)
- `task_2026-04-20_code_impact_graph_context_pack/` (12K)
- `task_2026-04-22_orphan_artifact_cleanup/` (12K)
- `task_2026-04-23_authority_rehydration/` (28K)
- `task_2026-04-23_guidance_kernel_semantic_boot/` (36K)
- `task_2026-04-22_docs_truth_refresh/` (28K)
- `task_2026-04-21_docs_reclassification_reference_extraction/` (40K)
- `task_2026-04-19_workspace_artifact_sync/` (28K)
- `task_2026-04-20_workspace_authority_reconstruction/` (32K)
- `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/` (184K)
- `task_2026-04-23_authority_kernel_gamechanger/` (28K)

**Batch 1 total**: 15 packets archived, ~480K.

### Batch 2 — ARCHIVE_AFTER_LORE_EXTRACT (archived 2026-04-24; lore cards extracted)

Closed packets with durable-lore density. 13 lore cards extracted into
`architecture/history_lore.yaml` BEFORE archive. Bodies now at
`docs/archives/packets/{name}/`:

- `task_2026-04-23_data_readiness_remediation/` (3.2M) —
  lore cards: `DB_TRIGGER_ENFORCED_AUTHORITY_MONOTONICITY`,
  `BULK_BATCH_WRITES_WITHOUT_PER_ROW_EVIDENCE_ARE_PROVENANCE_HOSTILE`,
  `INV_14_IDENTITY_SPINE_FOR_CANONICAL_ROWS`,
  `VERIFIED_WITHOUT_PER_ROW_EVIDENCE_IS_FALSE_CONFIDENCE`
- `task_2026-04-16_dual_track_metric_spine/` (1.3M) —
  lore cards: `BUG_DISPOSITION_TAXONOMY_TERMINATES_OPEN_LISTS`,
  `PERSISTENT_CRITIC_ROTATION_PREVENTS_RUBBER_STAMPING`
- `zeus_world_data_forensic_audit_package_2026-04-23/` (272K) —
  **local-only** (was never git-tracked; matches `.gitignore:11`
  `docs/archives/` pattern). Body lives on local disk at
  `docs/archives/packets/zeus_world_data_forensic_audit_package_
  2026-04-23/`; not committed. Lore cards preserve the durable
  content: `FORENSIC_DATA_AUDIT_TEMPLATE`,
  `VERIFIED_WITHOUT_PER_ROW_EVIDENCE_IS_FALSE_CONFIDENCE` (this
  was the forensic audit that produced the lore; `data_readiness_
  remediation` was its execution arm — they share an antipattern card)
- `task_2026-04-21_gate_f_data_backfill/` (204K) —
  lore cards: `DATA_COLLECTION_AND_TRADING_DAEMON_ARE_INDEPENDENT`
- `task_2026-04-23_live_harvester_enablement_dr33/` (24K) —
  lore cards: `FAIL_CLOSED_EXTERNAL_API_PARSING`,
  `FEATURE_FLAG_DEFAULT_OFF_FOR_BEHAVIOR_RISK`
- `task_2026-04-14_session_backlog.md` (20K) —
  lore cards: `RAINSTORM_DB_MIGRATION_WIPED_171K_FORECASTS_ROWS`

**Batch 2 total**: 6 packets archived, ~5.1M. Lore extraction complete;
bodies consultable on demand from `docs/archives/packets/`.

### Archive completion summary (2026-04-24)

- Archive destination: `docs/archives/packets/` (operator Option A).
- Total archived: 21 packets, ~5.5M freed from `docs/operations/`.
- Remaining in `docs/operations/`: 4 directories — 3 active
  (`task_2026-04-23_midstream_remediation/`,
  `task_2026-04-23_graph_rendering_integration/`,
  `task_2026-04-24_p0_data_audit_containment/`) + 1 NEEDS_OPERATOR_
  DECISION (`task_2026-04-19_execution_state_truth_upgrade/`, pending
  D3 ruling per `docs/to-do-list/zeus_operations_archive_deferrals_
  2026-04-24.md`).
- Plus top-level single-file docs (AGENTS.md, current_state.md,
  known_gaps.md, current_data_state.md, current_source_validity.md,
  data_rebuild_plan.md, runtime_artifact_inventory.md) unchanged.
- Plus 1 NEEDS_OPERATOR_DECISION single-file doc
  (`task_2026-04-13_remaining_repair_backlog.md`, pending D1+D2
  ruling).

### Pending operator decisions (NEEDS_OPERATOR_DECISION)

Not archive-ready — require explicit ruling on forward-vs-retire-vs-
defer. Tracked in `docs/to-do-list/zeus_operations_archive_deferrals_
2026-04-24.md`:

- `docs/operations/task_2026-04-13_remaining_repair_backlog.md` (8K) —
  TIGGE GRIB ingest items #52/#53 + source-attestation package are
  genuinely unresolved (D1 + D2 in operations archive deferrals doc).
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/` (76K) —
  planning-lock only; P1/P2 venue_commands spine never implemented
  (D3 in operations archive deferrals doc). Lore card
  `EXECUTION_STATE_TRUTH_FOUR_PHASE_PROGRESSION` extracted as `candidate`
  status pending P1/P2 implementation.

### Active (NOT archive candidates)

- `docs/operations/task_2026-04-23_midstream_remediation/` (380K) —
  W1–W4 closed per midstream fix plan; W5 substrate-blocked (see
  `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md §"Wave 5
  remaining blockers"`).
- `docs/operations/task_2026-04-23_graph_rendering_integration/` (12K) —
  implementation-prep stage; pre-implementation, not pre-archive.

### Live-docs cross-reference update owed

Once Batch 1 + Batch 2 archive completes (D4 resolved), trim
`docs/operations/current_state.md §"Other operations surfaces /
Visible non-default packet evidence"` list to only the 2 active
packets and redirect historical entries to this registry. Tracked as
D5 in `docs/to-do-list/zeus_operations_archive_deferrals_2026-04-24.md`.

---

## Batch 3 — 2026-05-01 workspace cleanup

**Source**: `docs/operations/_archive/` (a git-tracked intermediate staging
dir created 2026-04-29). Entire contents physically moved to
`docs/archives/packets/` and removed from git tracking 2026-05-01.

**Packets archived (40 entries)**:

- `task_2026-04-13_remaining_repair_backlog.md` — TIGGE GRIB + source-attestation residual
- `task_2026-04-19_execution_state_truth_upgrade/` — execution state truth P1/P2 (never implemented)
- `task_2026-04-23_graph_rendering_integration/` — graph rendering integration
- `task_2026-04-23_midstream_remediation/` — midstream remediation W1–W5
- `task_2026-04-25_p2_packet_runtime/` — packet runtime P2
- `task_2026-04-26_b4_physical_bounds/` — physical bounds antibody
- `task_2026-04-26_b5_dst_antibody/` — DST antibody
- `task_2026-04-26_execution_state_truth_p0_hardening/` — execution state P0
- `task_2026-04-26_execution_state_truth_p1_command_bus/` — command bus
- `task_2026-04-26_full_data_midstream_fix_plan/` — data midstream fix plan
- `task_2026-04-26_g10_helper_extraction/` — G10 helper extraction
- `task_2026-04-26_g10_ingest_scaffold/` — G10 ingest scaffold
- `task_2026-04-26_g10_nice_to_have_batch/` — G10 nice-to-have
- `task_2026-04-26_g6_live_safe_strategies/` — G6 live-safe strategies
- `task_2026-04-26_git_state_cleanup/` — git state cleanup
- `task_2026-04-26_live_readiness_completion/` — live readiness completion (G1)
- `task_2026-04-26_polymarket_clob_v2_migration/` — CLOB V2 migration (R3 Z0–Z4, U1)
- `task_2026-04-26_u1_hk_floor_antibody/` — HK floor antibody
- `task_2026-04-26_ultimate_plan/` — R3 ultimate plan (primary R3 plan source)
- `task_2026-04-27_backtest_first_principles_review/` — backtest first-principles review
- `task_2026-04-27_harness_debate/` — harness debate
- `task_2026-04-28_contamination_remediation/` — contamination remediation
- `task_2026-04-28_f11_forecast_issue_time/` — F11 forecast issue time (23,466 rows backfilled)
- `task_2026-04-28_obs_provenance_preflight/` — observation provenance preflight
- `task_2026-04-28_settlements_low_backfill/` — settlements LOW backfill (48 rows)
- `task_2026-04-28_settlements_physical_quantity_migration/` — physical quantity migration
- `task_2026-04-28_tigge_training_preflight/` — TIGGE training preflight
- `task_2026-04-28_weighted_platt_precision_weight_rfc/` — weighted Platt RFC (draft only)
- `task_2026-04-28_wu_observations_empty_provenance_triage/` — WU empty provenance triage
- `task_2026-04-29_design_simplification_audit/` — design simplification audit
- `task_2026-04-29_topology_graph_runtime_upgrade/` — topology graph runtime upgrade
- `task_2026-04-29_topology_profile_resolver_stability/` — topology profile resolver stability
- `task_2026-04-30_merge_protocol_conflict_first/` — merge protocol (conflict-first)
- `task_2026-04-30_reality_semantics_refactor_package/` — reality semantics refactor (superseded; pricing semantics cutover landed in main)
- `task_2026-04-30_source_auto_conversion/` — source auto-conversion (landed in main)
- `task_2026-04-30_source_contract_merge/` — source contract merge (landed in main)
- `task_2026-04-30_two_system_independence/` — two-system independence Phases 1–3 (landed)
- `zeus_topology_system_deep_evaluation_package_2026-04-24/` — topology deep evaluation
- `zeus_world_data_forensic_audit_package_2026-04-23/` — world data forensic audit
- `runtime_artifact_inventory.md` — runtime artifact inventory snapshot (stale)

**Batch 3 total**: 40 entries. `docs/operations/_archive/` directory removed
from git tracking; contents consultable from `docs/archives/packets/`.

---

## Batch 4 — 2026-05-02 operations cleanup

**Source**: `docs/operations/` live packet surface. The cleanup separated
closed/superseded packet evidence from still-open operation work, moved the
active known-gap worklist to `docs/to-do-list/known_gaps.md`, and left only
current-fact surfaces, active monitoring surfaces, active/deferred packets, and
compatibility pointers in `docs/operations/`.

**Packets archived (8 entries)**:

- `repo_review_2026-05-01/` — completed 4-lane repo review; follow-up work
  now lives in active remediation plans / known gaps.
- `task_2026-05-01_hko_backfill/` — closed no-write HKO opendata backfill;
  superseded by the later HK+Paris release packet.
- `task_2026-05-01_hko_release/` — closed no-write HKO release attempt;
  superseded by the later HK+Paris release packet.
- `task_2026-05-01_live_arming/` — completed live-arming runbook packet;
  operator-runbook residue is historical evidence.
- `task_2026-05-01_live_smoke_test/` — completed smoke-test evidence; open
  follow-ups remain as known-gap/remediation work, not live packet residency.
- `task_2026-05-01_paris_station_resolution/` — completed LFPG→LFPB station
  resolution packet; implementation landed in commit `e75f5237`.
- `task_2026-05-01_tigge_4_29_backfill/` — superseded/incomplete duplicate;
  the completed 2026-04-29 retry is recorded in
  `docs/operations/task_2026-05-01_tigge_5_01_backfill/work_log.md`.
- `task_2026-05-02_hk_paris_release/` — completed HK+Paris release packet;
  implementation landed in commit `c3f9814d`.

**Known-gaps relocation**:

- Active worklist: `docs/to-do-list/known_gaps.md`.
- Closed antibody archive: `docs/to-do-list/known_gaps_archive.md`.
- Compatibility pointer: `docs/operations/known_gaps.md`.

**Remaining live operations surfaces after cleanup** are listed in
`docs/operations/current_state.md`.
