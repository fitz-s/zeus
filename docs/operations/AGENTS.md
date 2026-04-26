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

The active planning package is
`task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md`.
It authorizes planning and the next narrow P0 hardening packet only; it does
not authorize P1/P2 schema/lifecycle implementation without fresh topology and
planning-lock evidence. The latest prior closeout evidence packet is
`task_2026-04-25_p4_readiness_checker/plan.md`.

Branch facts show the Immediate 4.1.A-C group and P0 4.2.A/B/C slices are
already landed and closed; do not reuse those slices as execution packets
without new evidence. The latest implementation packets closed P2 backfill
completeness, P2 4.4.A1/A2 revision history, P3 4.5.A metric-read linter
enforcement, the P3 residual replay usage-path guard, and P3 4.5.B-lite
obs_v2 reader-gate consumer hardening. The latest closeout evidence packet is
the P4 readiness checker, which is read-only and records that P4 mutation
remains blocked by operator evidence. This
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

Tracked packet evidence currently includes topology, dual-track, graph,
workspace-artifact, code-impact, execution-state, and Gate F data-backfill
packets.

### Attached Package Inputs

Package-input directories are source material for a packet, not universal law.
For example:

- `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/`
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
| `current_data_state.md` | current fact | Current audited data posture; not authority law |
| `current_source_validity.md` | current fact | Current audited source-validity posture; not authority law |
| `runtime_artifact_inventory.md` | active support | Inventory for runtime-local planning artifacts mirrored into repo control |
| `data_rebuild_plan.md` | active support | Upstream data-rebuild plan; not executable from topology packets |
| `packet_scope_protocol.md` | active support | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |
| `task_2026-04-21_docs_reclassification_reference_extraction/` | packet evidence | Closed docs reclassification/reference extraction packet |
| `task_2026-04-22_docs_truth_refresh/` | packet evidence | Closed docs truth refresh / stale-reference purge packet |
| `task_2026-04-23_guidance_kernel_semantic_boot/` | packet evidence | Closed guidance-kernel semantic boot refactor packet |
| `task_2026-04-23_authority_kernel_gamechanger/` | packet evidence | Closed authority-kernel gamechanger packet |
| `task_2026-04-23_authority_rehydration/` | packet evidence | Closed dense module reference / manifest rehydration packet |
| `task_2026-04-23_graph_refresh_official_integration/` | packet evidence | Closed official-first graph refresh verification and usage-integration packet |
| `task_2026-04-23_graph_rendering_integration/` | packet evidence | Graph deep-rendering remaining-value integration packet |
| `task_2026-04-23_data_readiness_remediation/` | packet evidence | Local data-readiness remediation packet shell present on disk |
| `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/` | package input | Attached reconstruction package input; not universal authority |
| `zeus_topology_system_deep_evaluation_package_2026-04-24/` | package input | Topology system deep evaluation and P0–P5 reform roadmap; not universal authority |
| `task_2026-04-13_remaining_repair_backlog.md` | packet evidence | Deferred backlog after non-DB small-package loop |

| `task_2026-04-16_dual_track_metric_spine/` | packet evidence | Dual-track metric spine refactor packet and evidence |
| `task_2026-04-16_function_naming_freshness/` | packet evidence | Governance package for naming and freshness metadata |
| `task_2026-04-19_code_review_graph_topology_bridge/` | packet evidence | Code Review Graph topology-first integration package |
| `task_2026-04-19_execution_state_truth_upgrade/` | packet evidence | Local execution-state truth upgrade packet present on disk |
| `task_2026-04-19_execution_state_truth_upgrade/task_packet.md` | packet evidence | Downstream handoff packet for execution-state truth upgrade |
| `task_2026-04-19_execution_state_truth_upgrade/work_log.md` | packet evidence | Planning refresh evidence log for execution-state truth upgrade |
| `task_2026-04-19_execution_state_truth_upgrade/receipt.json` | packet evidence | Scope/route receipt for execution-state truth planning refresh |
| `task_2026-04-19_workspace_artifact_sync/` | packet evidence | Workspace artifact synchronization package |
| `task_2026-04-20_code_impact_graph_context_pack/` | packet evidence | Code impact graph context-pack package |
| `task_2026-04-20_code_review_graph_online_context/` | packet evidence | Tracked Code Review Graph online-context package |
| `task_2026-04-20_workspace_authority_reconstruction/` | packet evidence | Workspace-authority reconstruction execution packet |
| `task_2026-04-21_gate_f_data_backfill/` | packet evidence | Local Gate F data backfill packet present on disk |
| `task_2026-04-22_orphan_artifact_cleanup/` | packet evidence | Cleanup of stale root scratch files and inactive workbook artifacts |
| `task_2026-04-23_midstream_remediation/` | packet evidence | Midstream remediation packet (test-currency + D3/D4/D6 antibody wave + data-readiness-tail follow-ups); includes POST_AUDIT_HANDOFF_2026-04-24.md for post-compaction resumption |
| `task_2026-04-23_live_harvester_enablement_dr33/` | packet evidence | DR-33-A live-harvester enablement packet (code-only scaffold behind ZEUS_HARVESTER_LIVE_ENABLED default OFF) |
| `task_2026-04-23_data_readiness_remediation/` | packet evidence | Data-readiness workstream CLOSED 2026-04-23 (8/8 packets APPROVED; settlements canonical-authority-grade) |
| `task_2026-04-24_p0_data_audit_containment/` | packet evidence | Closed P0 post-audit containment packet for read-only training-readiness blockers and legacy-hourly static lint |
| `task_2026-04-24_p1_source_role_registry/` | packet evidence | Closed P1.1 source-role registry packet (plan `2424264`, implementation `af7dd52`; post-close critic/verifier PASS) |
| `task_2026-04-24_p1_writer_provenance_gates/` | packet evidence | Closed P1.2 writer provenance gates packet (plan `e498b0d`, implementation `16292e2`; post-close reassessment closed the writer-local slice and corrected future P1/P3 boundaries) |
| `task_2026-04-24_p1_unsafe_observation_quarantine/` | packet evidence | Closed P1.3 unsafe-observation quarantine diagnostics packet (implementation `7a3524e`; read-only training-readiness blockers for unsafe observation rows) |
| `task_2026-04-24_p1_legacy_settlement_evidence_policy/` | packet evidence | Closed P1.4 implementation packet for read-only legacy settlement evidence-only / finalization policy blockers before eligibility-view adoption and v2 population (`df9ece5`) |
| `task_2026-04-24_p1_eligibility_views_training_preflight/` | packet evidence | Closed P1.5/P1.5a packet for script-side eligibility/preflight adapters before any calibration/training cutover (`07c86d8` planning, `99c4ac3` implementation) |
| `task_2026-04-25_p0_legacy_hourly_evidence_view/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.2.B schema-view packet for `v_evidence_hourly_observations`, bare-table lint proof, and required state-gate test-surface fix (`3e1bda7`) |
| `task_2026-04-25_p0_market_events_preflight/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.2.C implementation packet for market-events empty-table replay preflight |
| `task_2026-04-25_p1_daily_observation_writer_provenance/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.3.B-lite packet for WU/HKO daily observation writer provenance identity |
| `task_2026-04-25_p1_obs_v2_provenance_identity/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.3.B packet for observation_instants_v2 provenance identity and active obs_v2 producer stamps |
| `task_2026-04-25_p2_packet_runtime/` | packet evidence | Packet Runtime (`zpkt`) implementation packet — CLI, soft-warn pre-commit hook, scope.yaml schema, and tooling_runtime test category |
| `task_2026-04-25_p2_backfill_completeness_guardrails/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.4.B-lite packet for script-level backfill completeness manifests and fail-threshold guardrails |
| `task_2026-04-25_p2_obs_v2_revision_history/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.4.A1 packet for schema-backed observation_instants_v2 hash-checked revision history |
| `task_2026-04-25_p2_daily_observation_revision_history/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.4.A2 packet for daily WU/HKO observations backfill revision history |
| `task_2026-04-25_post_p3_p4_preflight/` | packet evidence | Closed post-P3/P4 preflight evidence packet for remaining blocker and operator-decision gates |
| `task_2026-04-25_p4_readiness_checker/` | packet evidence | Closed read-only P4 readiness-checker packet for blocker status reporting |
| `task_2026-04-25_p3_obs_v2_reader_gate/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.5.B-lite packet for obs_v2 reader-gate consumer hardening |
| `task_2026-04-25_p3_settlement_metric_linter_closeout/` | packet evidence | Closed POST_AUDIT_HANDOFF 4.5.A closeout packet for settlement metric-read lint enforcement |
| `task_2026-04-25_p3_usage_path_residual_guards/` | packet evidence | Closed P3 residual usage-path guard packet for replay settlement metric predicates and hourly canonical-ban proof |
| `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/` | archive evidence | Adversarial forensic audit package (20 major findings + P0→P4 apply order ruling); DO NOT MODIFY — evidence at specific timestamp |

## Rules

- `current_state.md` stays thin: current program, active packet, required
  evidence, freeze point, next action, and compact references to other
  registered surfaces.
- Non-trivial repo changes update a short work record in the active task folder.
- New multi-file execution packets use `task_YYYY-MM-DD_name/`.
- Do not leave completed packet material in the live pointer after closeout.
- Runtime-local `.omx/.omc` planning artifacts must be inventoried or mirrored
  before they are treated as durable work evidence.
- Current-fact surfaces require fresh packet/operator evidence. Do not update
  them from memory, and re-audit if they are older than their refresh protocol
  allows for the task at hand.
- Current-fact surfaces must state Status, Last audited, Max staleness,
  Evidence packet, Receipt path, stale do-not-use policy, and refresh trigger.
- Dense module rehydration packets may change routing, manifests, module books,
  and scoped routers, but they must not silently widen into runtime/source
  semantics without an explicit packet scope change.
