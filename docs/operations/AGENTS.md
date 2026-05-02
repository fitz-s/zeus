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
| `known_gaps.md` | Compatibility pointer; active known gaps now live at `docs/to-do-list/known_gaps.md` |
| `current_data_state.md` | Active current-fact surface for audited data posture |
| `current_source_validity.md` | Active current-fact surface for audited source-validity posture |
| `packet_scope_protocol.md` | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |

Current-fact files must stay summary-only, receipt/evidence-backed,
expiry-bound, and fail-closed when stale. They are planning truth, not durable
law or implementation permission.

### Active Execution Packet

No active execution packet is frozen. Freeze a new packet through
`current_state.md` before starting any implementation slice.

### Packet Evidence

`task_*/**` folders and `task_*.md` files are packet evidence unless
`current_state.md` names one as the active execution packet. Read them only when
the active task routes you there.

Packet-local file names are not global workflow requirements. Files such as
`evidence.md`, `findings.md`, `work_log.md`, and `receipt.json` are required
only when the active packet, closeout gate, audit/review task, or future handoff
consumes them. Direct T0/T1 work should not create packet evidence just to make
the workflow look complete.

Discrete `task_*` packet folders are agent-closeable by default once their
work log, report, or committed code proves the task is complete or superseded.
The closing agent must move the packet body to `docs/archives/packets/`, add or
update the `docs/archive_registry.md` closeout entry, remove active pointers,
and promote any residual OPEN work into `docs/to-do-list/known_gaps.md` or a
new admitted packet. Operator-only closeout is required only when the packet
itself says `awaiting operator`, `operator-deferred`, `STAGED, NOT COMMITTED`,
or carries an active runtime-gating artifact.

The operation-end feedback capsule is a closeout habit, not a packet filename.
For direct work, keep it in the final response. For packet closeout, append it
to an already-required work log or receipt. It should briefly capture context
recovery, Zeus improvement insights, and topology helped/blocked notes without
creating standalone evidence/findings files or widening the active packet. The
topology note should name the route/admission/risk outcome, whether it matched
the semantic task, one help, one friction, and one next topology delta or
`none_observed`. If an agent needs a route card for this habit, use intent
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
| `known_gaps.md` | compatibility pointer | Redirects to `docs/to-do-list/known_gaps.md`; no active gap content belongs here |
| `docs/to-do-list/known_gaps.md` | active checklist | Active known-gap worklist outside operations |
| `docs/to-do-list/known_gaps_archive.md` | archive interface | Closed gap antibody archive; moved to to-do-list 2026-05-01 |
| `current_data_state.md` | current fact | Current audited data posture; not authority law |
| `current_source_validity.md` | current fact | Current audited source-validity posture; not authority law |
| `packet_scope_protocol.md` | active support | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |
| `observations_k1_migration.md` | packet evidence | Design doc for K1 dual-atom observations migration — operator directive 2026-05-01 |
| `tigge_daemon_integration.md` | packet evidence | Design doc for TIGGE retrieval inside the data-ingest daemon — operator directive 2026-05-01 |
| `edge_observation/` | active monitoring | Operator-managed edge trajectory observation surface |
| `attribution_drift/` | active monitoring | Operator-managed strategy attribution drift observation surface |
| `ws_poll_reaction/` | active monitoring | Operator-managed WebSocket/poll reaction observation surface |
| `calibration_observation/` | active monitoring | Operator-managed calibration stability observation surface |
| `learning_loop_observation/` | active monitoring | Operator-managed learning-loop health observation surface |
| `task_2026-04-26_ultimate_plan/` | active packet container | Contains live-alpha runtime-gating TIGGE authorization evidence |
| `task_2026-04-26_ultimate_plan/2026-05-01_live_alpha/evidence/tigge_ingest_decision_2026-05-01.md` | active runtime-gating evidence | TIGGE entry-primary operator authorization evidence; do not archive without replacement |
| `task_2026-05-01_tigge_5_01_backfill/` | deferred packet container | Contains the deferred/open TIGGE 2026-05-01 backfill work log |
| `task_2026-05-01_tigge_5_01_backfill/work_log.md` | deferred packet evidence | 2026-05-01 TIGGE issue remains embargoed until 2026-05-03T00:00Z |
| `task_2026-05-01_ultrareview25_remediation/` | deferred planning container | Contains operator-deferred ultrareview remediation planning residue |
| `task_2026-05-01_ultrareview25_remediation/PLAN.md` | deferred planning packet | Operator-deferred governance/remediation residue |
| `task_2026-05-02_review_crash_remediation/` | plan packet | Dedupe and remediation plan for crashed review findings (#38 merged 2026-05-02) |
| `task_2026-05-02_review_crash_remediation/PLAN.md` | active planning packet | Crashed-review remediation plan |

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
- Do not leave completed or superseded packet folders in `docs/operations/`;
  archive them and leave only active packets, active monitoring surfaces,
  current-fact surfaces, and compatibility pointers.
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
