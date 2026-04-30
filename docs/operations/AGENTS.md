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

Tracked packet evidence in this live router is limited to the current rows
below. Closed packet evidence is archived under `docs/archives/packets/` and
indexed in `docs/archive_registry.md`; do not use archived packet folders as
active workflow defaults.

### Attached Package Inputs

Package-input directories are source material for a packet, not universal law.
Archived package inputs are historical lookup surfaces only. If an archived
packet still has unfinished content, summarize the live residue in
`active_unfinished_backlog.md` and point to the archived body there.

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
| `active_unfinished_backlog.md` | active support | Consolidated unfinished work extracted from archived/closed packet bodies; archived bodies are lookup only |
| `task_2026-04-26_polymarket_clob_v2_migration/` | packet evidence | Polymarket CLOB V1→V2 migration packet; now supporting R3 Z0 source-of-truth correction and later R3 CLOB V2 phases |
| `task_2026-04-26_ultimate_plan/` | packet evidence | R3 ultimate implementation packet for Zeus CLOB V2 live-money execution and dominance infrastructure; phase cards, boot notes, work records, reviews, and M3 user-channel ingest evidence live under `r3/` |
| `task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` | packet evidence | R3 Z0 packet-local live-money invariant summary for CLOB V2; not a durable authority doc |
| `task_2026-04-29_design_simplification_audit/` | packet evidence | First-principles audit plus phased repair evidence for Open-Meteo/TIGGE source authority, fallback semantics, paper/live/shadow residue, and live-flow simplification; no source-routing, live-deploy, production DB mutation, or calibration authority by presence alone |
| `task_2026-04-29_design_simplification_audit/README.md` | packet evidence | Local index for the design-simplification audit packet |
| `task_2026-04-29_design_simplification_audit/findings.md` | packet evidence | Ranked first-principles findings, repair flows, and phase status notes |
| `task_2026-04-29_design_simplification_audit/simplification_plan.md` | packet evidence | Phased live-flow simplification and repair plan |
| `task_2026-04-29_design_simplification_audit/evidence.md` | packet evidence | Command outputs, probes, reviews, and verification receipts for the packet |
| `task_2026-04-29_design_simplification_audit/native_multibin_buy_no_implementation_spec.md` | packet evidence | Native executable multi-bin buy_no implementation handoff; no live authorization or production mutation by presence alone |
| `task_2026-04-29_design_simplification_audit/probability_execution_split_spec.md` | packet evidence | Critic-approved sequencing spec for separating market-prior probability, executable token cost, live economic hypotheses, executor authority, and reporting cohorts; no live authorization or production mutation by presence alone |
| `task_2026-04-30_reality_semantics_refactor_package/` | package input | Compaction-safe full reality-semantics review plus mirrored pricing-semantics cutover package under `evidence/source_package/`; package input only, not source-routing, live-deploy, production DB mutation, config flip, schema migration apply, live venue submission, or strategy-promotion authority |
| `task_2026-04-30_reality_semantics_refactor_package/README.md` | package input | Local index for the compaction-safe reality-semantics refactor package |
| `task_2026-04-30_reality_semantics_refactor_package/START_HERE.md` | package input | Startup read order, commands, current decision, and non-authorization boundary for the refactor package |
| `task_2026-04-30_reality_semantics_refactor_package/WORKFLOW.md` | package input | Phase workflow, skill choices, execution mode, review gates, and stop conditions before source edits |
| `task_2026-04-30_reality_semantics_refactor_package/REFERENCED_FILES.md` | package input | Durable file reference map for package evidence, root authority, core source seams, tests, and current baseline |
| `task_2026-04-30_reality_semantics_refactor_package/ENGINEERING_ETHIC.md` | package input | Engineering ethic for a live quant-machine semantics refactor; no live or production authority by presence alone |
| `task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_EXECUTION_PLAN.md` | package input | Bounded Phase 0/A execution plan for topology admission and first guardrail tests before runtime source edits |
| `task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_PROGRESS.md` | package input | Progress evidence for the first guardrail slice; package evidence only, not live/prod authorization |

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
