# Runtime Artifact Inventory

Date: 2026-04-16
Branch: data-improve
Task: Docs lifecycle cleanup and runtime artifact inventory.
Changed files: `architecture/artifact_lifecycle.yaml`, `architecture/history_lore.yaml`, `architecture/topology.yaml`, `architecture/topology_schema.yaml`, `docs/operations/AGENTS.md`, `docs/operations/current_state.md`, `docs/operations/runtime_artifact_inventory.md`, `docs/operations/phase1live_2026-04-11_plan.md`, `docs/operations/task_2026-04-16_k6_k7_k8_math_semantics/work_log.md`, `scripts/topology_doctor.py`, `scripts/topology_doctor_docs_checks.py`, `scripts/topology_doctor_registry_checks.py`, `tests/test_topology_doctor.py`
Summary: Removed stale/completed operations artifacts from the live control surface, indexed runtime-local `.omx/.omc` planning artifacts, and added docs-mode checks for unregistered operation task folders, runtime plan inventory coverage, and progress/handoff placement.
Verification: `python scripts/topology_doctor.py --docs --summary-only`; `python scripts/topology_doctor.py --artifact-lifecycle --summary-only`; `python scripts/topology_doctor.py --history-lore --summary-only`; `python scripts/topology_doctor.py --context-budget --summary-only`; `python scripts/topology_doctor.py --planning-lock --changed-files <changed files> --plan-evidence docs/operations/current_state.md --summary-only`; `python scripts/topology_doctor.py --work-record --changed-files <changed files> --work-record-path docs/operations/runtime_artifact_inventory.md --summary-only`; `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files <changed files> --summary-only`; `python -m pytest -q tests/test_topology_doctor.py -k 'docs_mode or runtime_plan or progress_handoff or operation_task or artifact_lifecycle or work_record or history_lore'`; `git diff --check`. Broad `--source`, `--tests`, `--scripts`, and `--strict` remain blocked by pre-existing Dual-Track refactor registry debt outside this cleanup package.
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
| `.omx/plans/zeus-data-improve-pre-tigge-repair-packet-plan-2026-04-11.md` | stale packet plan | Historical only. |

## Runtime Context Artifacts With Durable Lessons

| Runtime path | Useful lesson | Extraction target |
|---|---|---|
| `.omx/context/packet1_wmo_rounding_closeout_2026-04-13.md` through `.omx/context/packet9_full_strict_topology_closeout_2026-04-13.md` | Topology compiler packet closeouts; preserve as archive-only evidence. | `docs/archives/work_packets/...` if local archive is needed. |
| `.omx/context/topology_inventory_2026-04-13_baseline.md` | Baseline topology inventory before compiler hardening. | Historical topology archive only. |
| `.omx/context/topology-phase-improvements-20260415T060202Z.md` | Rationale for later topology enforcement phases. | Reflected in topology hardening manifests/tests; no default read. |
| `.omx/context/topology_provisional_context_ralplan-20260414T223842Z.md` | Context packets are starting assumptions, not fixed limits. | Already reflected in context-budget wording. |
| `.omx/context/core_map_compiler_ralplan-20260415T022243Z.md` | Core map must be proof-backed, not agent-authored prose. | Reflected in core claims/core-map tests. |
| `.omx/context/zeus_deferred_audit_backlog_20260412T174038Z.md` | Deferred audit backlog by wave. | Keep current deferred items in operations backlog before deleting local runtime state. |
