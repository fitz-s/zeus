# Runtime Artifact Inventory

Date: 2026-04-16
Branch: data-improve
Task: Docs lifecycle cleanup and runtime artifact inventory.
Changed files: `architecture/artifact_lifecycle.yaml`, `architecture/history_lore.yaml`, `architecture/topology.yaml`, `architecture/topology_schema.yaml`, `docs/operations/AGENTS.md`, `docs/operations/current_state.md`, `docs/operations/runtime_artifact_inventory.md`, `docs/operations/phase1live_2026-04-11_plan.md`, `docs/operations/task_2026-04-16_k6_k7_k8_math_semantics/work_log.md`, `scripts/topology_doctor.py`, `scripts/topology_doctor_docs_checks.py`, `scripts/topology_doctor_registry_checks.py`, `tests/test_topology_doctor.py`
Summary: Removed stale/completed operations artifacts from the live control surface, indexed runtime-local `.omx/.omc` planning artifacts, and added docs-mode checks for unregistered operation task folders, runtime plan inventory coverage, and progress/handoff placement.
Verification: `python3 scripts/topology_doctor.py --docs --summary-only`; `python3 scripts/topology_doctor.py --artifact-lifecycle --summary-only`; `python3 scripts/topology_doctor.py --history-lore --summary-only`; `python3 scripts/topology_doctor.py --context-budget --summary-only`; `python3 scripts/topology_doctor.py --planning-lock --changed-files <changed files> --plan-evidence docs/operations/current_state.md --summary-only`; `python3 scripts/topology_doctor.py --work-record --changed-files <changed files> --work-record-path docs/operations/runtime_artifact_inventory.md --summary-only`; `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files <changed files> --summary-only`; `python -m pytest -q tests/test_topology_doctor.py -k 'docs_mode or runtime_plan or progress_handoff or operation_task or artifact_lifecycle or work_record or history_lore'`; `git diff --check`. Broad `--source`, `--tests`, `--scripts`, and `--strict` remain blocked by pre-existing Dual-Track refactor registry debt outside this cleanup package.
Next: Keep runtime-local planning artifacts indexed here or mirror them into a tracked packet before treating them as durable project evidence; resolve Dual-Track source/test/script registry debt in the owning refactor package.
Status: active routing inventory

This file is the repo-facing index for planning/progress artifacts that are
created in local runtime directories (`.omx/` and `.omc/`). It is not governing law.
It prevents useful packet knowledge from living only in ignored runtime state.

## Policy

- `.omx/state/**`, `.omx/logs/**`, `.omc/state/**`, and `.omc/sessions/**` are runtime-only.
- `.omx/artifacts/**` and `.omc/artifacts/ask/**` are prompt/review transcripts unless a packet explicitly cites them.
- `.omx/plans/*.md`, `.omc/plans/*.md`, and selected `.omx/context/*.md` can contain durable planning or closeout evidence. They need either a repo mirror, an archive pointer, or an explicit discard note here.
- Completed packet evidence archives are local/ignored under `docs/archives/**`; do not make them default-read authority.

## Current Runtime Plan Artifacts

| Runtime path | Status | Repo disposition |
|---|---|---|
| `.omc/plans/open-questions.md` | stale planning backlog | Historical only; extract unresolved live decisions into `docs/operations/current_state.md` before deleting local runtime state. |
| `.omc/plans/city_truth_sweep.md` | local city-truth sweep planning | Local planning evidence for a separate data/truth sweep; summarize in its owning packet before implementation. |
| `.omc/plans/observation-instants-migration-iter2.md` | current ralplan for observation-instants migration | Local planning evidence for a separate high-risk data/schema migration; summarize in its owning packet before any implementation. |
| `.omc/plans/observation-instants-migration-iter3.md` | superseding observation-instants migration plan | Local planning evidence for a separate high-risk data/schema migration; supersedes iter2 and must be summarized in its owning packet before implementation. |
| `.omc/plans/zeus-fix-engineering.md` | stale engineering plan | Historical only; superseded by current operations packets and commit history. |
| `.omx/plans/daily-loss-24h-repair.md` | stale plan | Archive/mirror only if daily-loss work reopens. |
| `.omx/plans/daily-low-support-2026-04-15.md` | recent plan | Keep as runtime-local until dual-track low-support packet decides whether to mirror it. |
| `.omx/plans/data-hole-closure-to-tigge-only-2026-04-11.md` | stale plan | Historical only; data rebuild plans now live under `docs/operations/data_rebuild_plan.md`. |
| `.omx/plans/datafix_2026-04-12_dual_lane_backtest_plan.md` | stale plan | Historical only; no governing role. |
| `.omx/plans/datafix_2026-04-12_wu_settlement_backtest_plan.md` | stale plan | Historical only; no governing role. |
| `.omx/plans/prd-datafix_2026-04-11_probability_trace_foundation.md` | stale PRD | Historical only; migrate only if probability trace work reopens. |
| `.omx/plans/prd-datafix_2026-04-12_dual_lane_backtest.md` | stale PRD | Historical only. |
| `.omx/plans/prd-p3-ralph-loop.md` | closed packet PRD | Archive-only provenance. |
| `.omx/plans/prd-p4-ralph-loop.md` | closed packet PRD | Archive-only provenance. |
| `.omx/plans/prd-p5-ralph-loop.md` | closed packet PRD | Archive-only provenance. |
| `.omx/plans/prd-p6-ralph-loop.md` | closed packet PRD | Archive-only provenance. |
| `.omx/plans/prd-post-p7-external-reality-mainline.md` | stale planning PRD | Historical only unless post-P7 external reality work reopens. |
| `.omx/plans/prd-reality-contract-mainline.md` | stale planning PRD | Historical only. |
| `.omx/plans/prd-reality-grounding-mainline-spec.md` | stale planning PRD | Historical only. |
| `.omx/plans/prd-wave1-1a-read-authority.md` | closed wave PRD | Archive-only provenance. |
| `.omx/plans/prd-wave1-1b0-canonical-token-identity.md` | closed wave PRD | Archive-only provenance. |
| `.omx/plans/prd-wave1-1b1-json-bankroll-cleanup.md` | blocked/stale PRD | Historical only; blockers should live in current backlog if reopened. |
| `.omx/plans/prd-zeus-data-improve-pre-tigge-repair-packet-2026-04-11.md` | stale PRD | Historical only. |
| `.omx/plans/ralplan_2026-04-13_topology_compiler_program.md` | completed topology program plan | Reflected by completed topology packet history; archive-only. |
| `.omx/plans/ralplan_2026-04-13_topology_hardening_rounding_incident.md` | completed topology planning | Reflected by topology history/lore and current manifests. |
| `.omx/plans/ralplan_pre_merge_high_value_2026-04-12.md` | stale planning | Historical only. |
| `.omx/plans/refit-preflight-six-packet-plan.md` | stale planning | Historical only unless refit preflight reopens. |
| `.omx/plans/open-questions.md` | runtime planning scratch | Local runtime-only; not durable repo evidence for the authority reconstruction packet. |
| `.omx/plans/test-spec-datafix_2026-04-11_probability_trace_foundation.md` | stale test spec | Historical only. |
| `.omx/plans/test-spec-datafix_2026-04-12_dual_lane_backtest.md` | stale test spec | Historical only. |
| `.omx/plans/test-spec-p3-ralph-loop.md` | closed packet test spec | Archive-only provenance. |
| `.omx/plans/test-spec-p4-ralph-loop.md` | closed packet test spec | Archive-only provenance. |
| `.omx/plans/test-spec-p5-ralph-loop.md` | closed packet test spec | Archive-only provenance. |
| `.omx/plans/test-spec-p6-ralph-loop.md` | closed packet test spec | Archive-only provenance. |
| `.omx/plans/test-spec-post-p7-external-reality-mainline.md` | stale test spec | Historical only. |
| `.omx/plans/test-spec-reality-contract-mainline.md` | stale test spec | Historical only. |
| `.omx/plans/test-spec-reality-grounding-mainline-spec.md` | stale test spec | Historical only. |
| `.omx/plans/test-spec-wave1-1a-read-authority.md` | closed wave test spec | Archive-only provenance. |
| `.omx/plans/test-spec-wave1-1b0-canonical-token-identity.md` | closed wave test spec | Archive-only provenance. |
| `.omx/plans/test-spec-wave1-1b1-json-bankroll-cleanup.md` | blocked/stale test spec | Historical only. |
| `.omx/plans/test-spec-zeus-data-improve-pre-tigge-repair-packet-2026-04-11.md` | stale test spec | Historical only. |
| `.omx/plans/workspace-authority-reconstruction-p2-2026-04-20-initial-deliberate.md` | superseded ralplan draft | Superseded by revised P2 sequencing plan; keep local only unless packet evidence is explicitly archived. |
| `.omx/plans/workspace_authority_reconstruction_p1_vs_p2_2026-04-20_revised.md` | current ralplan sequencing plan | Active local planning evidence for P2 sequencing; summarize in active packet before deleting local runtime state. |
| `.omx/plans/p2b-graph-meta-sidecar-ralplan.md` | superseded P2B ralplan draft | P2B planning evidence only; superseded by revised/final P2B plans and not authority. |
| `.omx/plans/p2b-graph-meta-sidecar-ralplan-revised.md` | superseded P2B ralplan draft | P2B planning evidence only; superseded by final P2B plan and not authority. |
| `.omx/plans/p2b-graph-meta-sidecar-ralplan-final.md` | final P2B ralplan | Records local live graph sidecar decision; summarize in active packet before deleting local runtime state. |
| `.omx/plans/workspace-authority-reconstruction-p3-2026-04-21-ralplan.md` | current P3 ralplan | Active local planning evidence for P3 historical compression; summarize in active packet before deleting local runtime state. |
| `.omx/plans/docs-reclassification-p0-ralplan.md` | superseded docs reclassification P0 plan | Planning evidence only; superseded by revised P0 plan and not authority. |
| `.omx/plans/docs-reclassification-p0-ralplan-revised.md` | current docs reclassification P0 ralplan | Active local planning evidence for docs reclassification P0; summarize in active packet before deleting local runtime state. |
| `.omx/plans/docs-reclassification-p1-concise-ralplan-2026-04-21.md` | superseded docs reclassification P1 draft | Planning evidence only; superseded by revised P1 plan and not authority. |
| `.omx/plans/docs-reclassification-p1-ralplan-2026-04-21.md` | superseded docs reclassification P1 draft | Planning evidence only; superseded by revised P1 plan and not authority. |
| `.omx/plans/docs-reclassification-p1-ralplan-revised.md` | current docs reclassification P1 ralplan | Active local planning evidence for P1 extraction/demotion; summarized in the active packet before P1 implementation. |
| `.omx/plans/docs-reclassification-p2-ralplan-2026-04-21.md` | current docs reclassification P2 ralplan | Active local planning evidence for P2 runbook/operations routing normalization; summarize in active packet before deleting local runtime state. |
| `.omx/plans/docs-reclassification-p3-ralplan-2026-04-21.md` | current docs reclassification P3 plan | Active local planning evidence for P3 reference-fragment freezing and enforcement tightening; summarize in active packet before deleting local runtime state. |
| `.omx/plans/docs-reclassification-closeout-plan-2026-04-21.md` | current docs reclassification closeout plan | Active local planning evidence for final package closeout; summarize in active packet before deleting local runtime state. |
| `.omx/plans/docs-truth-refresh-p0-ralplan-2026-04-22.md` | current docs truth refresh P0 ralplan | Active local planning evidence for P0 stale-truth purge and current-fact install; summarize in the 2026-04-22 packet before deleting local runtime state. |
| `.omx/plans/guidance-kernel-semantic-boot-ralplan-2026-04-23.md` | active guidance-kernel ralplan | Planning evidence for the active guidance-kernel semantic boot packet. |
| `.omx/plans/kernel-gamechanger-ralplan-2026-04-23.md` | active authority-kernel ralplan | Planning evidence for the active authority-kernel gamechanger packet. |
| `.omx/plans/topology_system_deep_improvement_plan_2026-04-24.md` | topology-system improvement plan | Runtime-local planning evidence for the topology deep-evaluation package; mirror into an active packet before implementation. |
| `.omx/artifacts/topology_graph_investigation_2026-04-29/*.md` | topology/graph agent-runtime investigation artifacts | Mirrored by `docs/operations/task_2026-04-29_topology_graph_runtime_upgrade/evidence/analysis_index.md`; durable claims must be extracted into module books, manifests, source, or tests before local scratch is deleted. |
| `.omx/plans/zeus-data-improve-pre-tigge-repair-packet-plan-2026-04-11.md` | stale packet plan | Historical only. |
| `.omx/plans/post-p1-forensic-mainline-ralplan-2026-04-24.md` | active post-audit mainline ralplan | Mirrored into `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p0_data_audit_containment/plan.md`; use as local Ralph planning evidence only. |
| `.omx/plans/prd-p0-data-audit-containment.md` | active P0 Ralph PRD | Mirrored into `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p0_data_audit_containment/plan.md`; use as local planning evidence only. |
| `.omx/plans/test-spec-p0-data-audit-containment.md` | active P0 Ralph test spec | Mirrored into `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p0_data_audit_containment/plan.md`; use as local planning evidence only. |

## Runtime Context Artifacts With Durable Lessons

| Runtime path | Useful lesson | Extraction target |
|---|---|---|
| `.omx/context/packet1_wmo_rounding_closeout_2026-04-13.md` through `.omx/context/packet9_full_strict_topology_closeout_2026-04-13.md` | Topology compiler packet closeouts; preserve as archive-only evidence. | `docs/archives/work_packets/...` if local archive is needed. |
| `.omx/context/topology_inventory_2026-04-13_baseline.md` | Baseline topology inventory before compiler hardening. | Historical topology archive only. |
| `.omx/context/topology-phase-improvements-20260415T060202Z.md` | Rationale for later topology enforcement phases. | Reflected in topology hardening manifests/tests; no default read. |
| `.omx/context/topology_provisional_context_ralplan-20260414T223842Z.md` | Context packets are starting assumptions, not fixed limits. | Already reflected in context-budget wording. |
| `.omx/context/core_map_compiler_ralplan-20260415T022243Z.md` | Core map must be proof-backed, not agent-authored prose. | Reflected in core claims/core-map tests. |
| `.omx/context/zeus_deferred_audit_backlog_20260412T174038Z.md` | Deferred audit backlog by wave. | Keep current deferred items in operations backlog before deleting local runtime state. |
| `.omx/context/docs-reclassification-p2-20260421T223156Z.md` | P2 context snapshot for runbook/operations routing normalization. | Summarized in the active docs reclassification packet. |
| `.omx/context/docs-reclassification-p3-20260421T231323Z.md` | P3 context snapshot for reference-fragment freezing and enforcement planning. | Summarized in the active docs reclassification packet. |
| `.omx/context/docs-reclassification-closeout-20260421T233946Z.md` | Closeout context snapshot for final docs reclassification package planning. | Summarized in the active docs reclassification packet. |
| `.omx/context/docs-truth-refresh-p0-20260422T084000Z.md` | P0 context snapshot for docs truth refresh stale-reference purge. | Summarize in the 2026-04-22 docs truth refresh packet. |
| `.omx/context/guidance-kernel-semantic-boot-20260423T005005Z.md` | Guidance-kernel semantic boot context snapshot. | Summarized in the active guidance-kernel packet. |
| `.omx/context/kernel-gamechanger-20260423T035846Z.md` | Authority-kernel gamechanger context snapshot. | Summarized in the active authority-kernel packet. |
| `.omx/context/post-p1-forensic-mainline-20260424T025628Z.md` | Post-P-1 forensic mainline context snapshot for P0-P4 sequencing. | Summarized in `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p0_data_audit_containment/plan.md`. |
