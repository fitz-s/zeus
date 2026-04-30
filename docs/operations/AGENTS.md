# docs/operations AGENTS

Live control and packet-evidence surface for Zeus.

This directory is not a second authority plane. It routes current work,
attached package inputs, active packets, and operational evidence. Current law
still lives in `docs/authority/**`, `architecture/**`, tests, and executable
source.

## Surface Classes

### Live Pointer

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control pointer: current program, active packet, required evidence, freeze point, next action |

Keep this file thin. It should not become a runtime diary, historical packet
index, or archive catalog.

### Active Supporting Surfaces

| File | Purpose |
|------|---------|
| `known_gaps.md` | Active operational gap register; moved from docs root |
| `current_data_state.md` | Active current-fact surface for audited data posture |
| `current_source_validity.md` | Active current-fact surface for audited source-validity posture |
| `runtime_artifact_inventory.md` | Inventory for runtime-local planning artifacts mirrored into repo control |
| `data_rebuild_plan.md` | Upstream data-rebuild plan; not executable from topology packets |
| `packet_scope_protocol.md` | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |

Current-fact files must stay summary-only, receipt/evidence-backed,
expiry-bound, and fail-closed when stale. They are planning truth, not durable
law or implementation permission.

### Active Execution Packet

No active execution packet is frozen. The latest closeout evidence packet is
`task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/plan.md`.

Branch facts show the Immediate 4.1.A-C group and P0 4.2.A/B/C slices are
already landed and closed; do not reuse those slices as execution packets
without new evidence. The latest implementation packets closed P2 backfill
completeness, P2 4.4.A1/A2 revision history, P3 4.5.A metric-read linter
enforcement, the P3 residual replay usage-path guard, and P3 4.5.B-lite
obs_v2 reader-gate consumer hardening. The latest closeout evidence packet is
the operations package cleanup packet; the prior P4 readiness checker remains
read-only evidence that P4 mutation is blocked by operator evidence. This
router does not authorize production DB mutation, canonical v2 population,
market-identity backfill, live executor DB authority, legacy-settlement
promotion, broad P1 source-role/view work, live daily-ingest changes, row-level
quarantine, shared obs_v2 view redesign, hourly high/low metric placement, or
P4 data population. Freeze a new packet through `current_state.md` before any
next implementation slice.

### Packet Evidence

`task_*/**` folders and `task_*.md` files are packet evidence unless
`current_state.md` names one as the active execution packet. Read them only when
the active task routes you there.

Packet-local file names are not global workflow requirements. Files such as
`evidence.md`, `findings.md`, `work_log.md`, and `receipt.json` are required
only when the active packet, closeout gate, audit/review task, or future handoff
consumes them. Direct T0/T1 work should not create packet evidence just to make
the workflow look complete.

The operation-end feedback capsule is a closeout habit, not a packet filename.
For direct work, keep it in the final response. For packet closeout, append it
to an already-required work log or receipt. It should briefly capture context
recovery, Zeus improvement insights, and topology helped/blocked notes without
creating standalone evidence/findings files or widening the active packet. If
an agent needs a route card for this habit, use intent
`direct operation feedback capsule`; do not persist capsules under
`.omx/context/` or invent `handoff` files in packet folders.

Tracked packet evidence in this live router is limited to the current or
retained rows below. Closed packet evidence is archived under
`docs/operations/_archive/` or `docs/archives/packets/` and indexed in
`docs/archive_registry.md`; do not use archived packet folders as active
workflow defaults.

### Attached Package Inputs

Package-input directories are source material for a packet, not universal law.
For example:

- `docs/archives/packets/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/`
- `zeus_topology_system_deep_evaluation_package_2026-04-24/` — topology system
  assessment and P0–P5 reform roadmap; all recommendations remain unimplemented
  and valid as of 2026-04-24

For the 2026-04-23 Zeus world data forensic audit package, the canonical P1
route is the archived path registered below. If a local
`docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/` copy is
present, treat it as a duplicate scratch/input copy, not route authority.

Use the active packet plan/work log to determine which package inputs matter.

## File Registry

This explicit registry keeps docs checks machine-routable. Entries here do not
make a surface default-read unless `current_state.md` routes it.

| Path | Class | Purpose |
|------|-------|---------|
| `AGENTS.md` | operations router | Registry for live operations surfaces in this directory |
| `current_state.md` | live pointer | Single current program, active packet, required evidence, freeze point, next action |
| `known_gaps.md` | active support | Active operational gap register |
| `known_gaps_archive.md` | archive interface | Closed gap antibody archive; historical immune-system record, not active gap authority |
| `current_data_state.md` | current fact | Current audited data posture; not authority law |
| `current_source_validity.md` | current fact | Current audited source-validity posture; not authority law |
| `runtime_artifact_inventory.md` | active support | Inventory for runtime-local planning artifacts mirrored into repo control |
| `data_rebuild_plan.md` | active support | Upstream data-rebuild plan; not executable from topology packets |
| `packet_scope_protocol.md` | active support | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |
| `task_2026-04-13_remaining_repair_backlog.md` | packet evidence | Deferred backlog after non-DB small-package loop |
| `task_2026-04-23_graph_rendering_integration/` | packet evidence | Graph deep-rendering remaining-value integration packet |
| `task_2026-04-23_midstream_remediation/` | packet evidence | Midstream remediation package; phase evidence lives under `phases/` and includes POST_AUDIT_HANDOFF_2026-04-24.md for post-compaction resumption |
| `task_2026-04-26_polymarket_clob_v2_migration/` | packet evidence | Polymarket CLOB V1→V2 migration packet; now supporting R3 Z0 source-of-truth correction and later R3 CLOB V2 phases |
| `task_2026-04-26_ultimate_plan/` | packet evidence | R3 ultimate implementation packet for Zeus CLOB V2 live-money execution and dominance infrastructure; phase cards, boot notes, work records, reviews, and M3 user-channel ingest evidence live under `r3/` |
| `task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` | packet evidence | R3 Z0 packet-local live-money invariant summary for CLOB V2; not a durable authority doc |
| `task_2026-04-28_contamination_remediation/` | packet evidence | Codex drift self-audit and contamination remediation packet; temporary evidence only, not live-deploy authority |
| `task_2026-04-28_weighted_platt_precision_weight_rfc/` | packet evidence | Weighted Platt precision-weight RFC packet preserved as evidence |
| `task_2026-04-28_tigge_training_preflight/` | packet evidence | TIGGE training preflight packet preserved as evidence; no external fetch/live training authorization by merge alone |
| `task_2026-04-28_settlements_physical_quantity_migration/` | packet evidence | Settlements physical_quantity migration packet preserved as evidence; no new DB mutation authorized by merge alone |
| `task_2026-04-28_settlements_low_backfill/` | packet evidence | LOW settlements backfill packet preserved as evidence; no new DB mutation authorized by merge alone |
| `task_2026-04-28_obs_provenance_preflight/` | packet evidence | Observation provenance preflight packet; apply-capable scripts require explicit operator approval and do not authorize live/prod DB mutation by presence alone |
| `task_2026-04-28_wu_observations_empty_provenance_triage/` | packet evidence | WU observations empty-provenance triage packet preserved as evidence |
| `task_2026-04-28_f11_forecast_issue_time/` | packet evidence | Forecast issue_time hindsight-antibody packet, apply runbook, and consumer audit evidence |
| `task_2026-04-29_topology_graph_runtime_upgrade/` | packet evidence | Topology/graph agent-runtime upgrade packet for route cards, typed intent, role context packs, claim-scoped graph degradation, and artifact lifecycle; no live trading, source-routing, production DB mutation, or authority promotion by presence alone |
| `task_2026-04-29_topology_profile_resolver_stability/` | packet evidence | Topology profile resolver stability packet for semantic-vs-companion file evidence, changed-files navigation input, and task-scoped runtime blocker output; no live trading, source-routing, production DB mutation, or authority promotion by presence alone |
| `task_2026-04-30_merge_protocol_conflict_first/` | packet evidence | Merge protocol correction packet for replacing unconditional cross-worktree critic gate with conflict-first escalation; no live trading, source-routing, production DB mutation, or merge execution |
| `task_2026-04-29_design_simplification_audit/` | packet evidence | First-principles audit plus phased repair evidence for Open-Meteo/TIGGE source authority, fallback semantics, paper/live/shadow residue, and live-flow simplification; no source-routing, live-deploy, production DB mutation, or calibration authority by presence alone |
| `task_2026-04-29_design_simplification_audit/README.md` | packet evidence | Local index for the design-simplification audit packet |
| `task_2026-04-29_design_simplification_audit/findings.md` | packet evidence | Ranked first-principles findings, repair flows, and phase status notes |
| `task_2026-04-29_design_simplification_audit/simplification_plan.md` | packet evidence | Phased live-flow simplification and repair plan |
| `task_2026-04-29_design_simplification_audit/evidence.md` | packet evidence | Command outputs, probes, reviews, and verification receipts for the packet |
| `task_2026-04-29_design_simplification_audit/native_multibin_buy_no_implementation_spec.md` | packet evidence | Native executable multi-bin buy_no implementation handoff; no live authorization or production mutation by presence alone |
| `task_2026-04-27_harness_debate/` | packet evidence | Harness debate packet evidence and implementation errata preserved from worktree merge |
| `task_2026-04-27_backtest_first_principles_review/` | packet evidence | Backtest first-principles review packet preserved from worktree merge; planning/evidence only, not live authority |
| `zeus_topology_system_deep_evaluation_package_2026-04-24/` | package input | Topology system deep evaluation and P0–P5 reform roadmap (P0–P5 implementation landed via PR #15 + #13/#14 + commits `c495510`..`0ca6db9`); package preserved as historical evaluation evidence |

Archived packet evidence (physically moved to `docs/archives/packets/`) is
listed in `docs/archive_registry.md`; do not re-list those packets here. When
a packet closes and is archived, remove its row from this registry and the
archive_registry entry becomes its single source of historical truth.

## Rules

- `current_state.md` stays thin: current program, active packet, required
  evidence, freeze point, next action, and compact references to other
  registered surfaces.
- Non-trivial repo changes update a short work record in the active package or
  phase folder.
- New independent multi-file packages use `task_YYYY-MM-DD_name/`.
- New phases of an existing package live under that package, usually
  `task_YYYY-MM-DD_package/phases/task_YYYY-MM-DD_phase/`; do not create
  sibling top-level folders for phases of the same package.
- Do not leave completed packet material in the live pointer after closeout.
- Runtime-local `.omx/.omc` planning artifacts must be inventoried or mirrored
  before they are treated as durable work evidence.
- `state/daemon-heartbeat.json` and `state/status_summary.json` are live
  runtime projections. Treat them as interference for ordinary docs/source/test
  packets; exclude them from non-runtime-governance receipts and closeout diffs
  unless the packet explicitly owns runtime state policy.
- Current-fact surfaces require fresh packet/operator evidence. Do not update
  them from memory, and re-audit if they are older than their refresh protocol
  allows for the task at hand.
- Current-fact surfaces must state Status, Last audited, Max staleness,
  Evidence packet, Receipt path, stale do-not-use policy, and refresh trigger.
- Dense module rehydration packets may change routing, manifests, module books,
  and scoped routers, but they must not silently widen into runtime/source
  semantics without an explicit packet scope change.
- When closeout reports unrelated global script/docs/test drift, phrase the
  result as changed-surface status plus repo-wide drift summary. Do not repair
  unrelated weekly diagnostics just to make a narrow packet look globally clean.
