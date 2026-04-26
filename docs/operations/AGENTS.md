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

Tracked packet evidence currently includes the active execution-state truth
upgrade, graph rendering integration, midstream remediation, packet runtime
(`zpkt`), and live-readiness completion packets. Closed packet evidence has
been archived to `docs/archives/packets/` and is indexed in
`docs/archive_registry.md`.

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
| `task_2026-04-13_remaining_repair_backlog.md` | packet evidence | Deferred backlog after non-DB small-package loop |
| `task_2026-04-19_execution_state_truth_upgrade/` | packet evidence | Execution-state truth upgrade planning packet present on disk |
| `task_2026-04-23_graph_rendering_integration/` | packet evidence | Graph deep-rendering remaining-value integration packet |
| `task_2026-04-23_midstream_remediation/` | packet evidence | Midstream remediation package; phase evidence lives under `phases/` and includes POST_AUDIT_HANDOFF_2026-04-24.md for post-compaction resumption |
| `task_2026-04-25_p2_packet_runtime/` | packet evidence | Packet Runtime (`zpkt`) implementation packet — CLI, soft-warn pre-commit hook, `scope.yaml` schema, and tooling_runtime test category |
| `task_2026-04-26_live_readiness_completion/` | packet evidence | Live-readiness completion planning packet (K=4 antibodies for 11 open B/G/U/N items); implementation lands in `claude/live-readiness-completion-2026-04-26` worktree |
| `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/` | package input | Attached reconstruction package input; not universal authority |
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
