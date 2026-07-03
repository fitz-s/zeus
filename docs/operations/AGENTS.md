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
| `known_gaps.md` | Canonical home for active known gaps (per-task open work also under `current/`) |
| `current_data_state.md` | Active current-fact surface for audited data posture |
| `current_source_validity.md` | Active current-fact surface for audited source-validity posture |
| `packet_scope_protocol.md` | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |

### Standalone Reports / Evidence (top-level files, not packet-scoped)

Do not add standalone historical reports at the top level. Route active work to
`docs/operations/current/`; route closed historical reports to
`docs/archive/<YYYY>-Q<N>/`.

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
The closing agent must move the packet body to `docs/archive/<YYYY>-Q<N>/`,
record the move in `docs/archive_registry.md`, remove active pointers,
and promote any residual OPEN work into `docs/operations/known_gaps.md` or a
new admitted packet. Do NOT leave a stub file in `docs/operations/`.
Operator-only closeout is required only when the packet
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
below. Closed packet evidence is archived under `docs/archive/<YYYY>-Q<N>/` and
indexed in `docs/archive/<YYYY>-Q<N>/INDEX.md`; do not use archived packet folders as
active workflow defaults. Active archival rules: `docs/authority/ARCHIVAL_RULES.md`.
Note: ARCHIVAL_RULES.md lives at `docs/authority/ARCHIVAL_RULES.md`.

### Attached Package Inputs

Package-input directories are source material for a packet, not universal law.
Archived package inputs are historical lookup surfaces only. If an archived
packet still has unfinished content, summarize the live residue in
`active_unfinished_backlog.md` and point to the archived body there.

Use the active packet plan/work log to determine which package inputs matter.

## File Registry

Active routing summary (machine-routable via architecture/docs_registry.yaml):

- Active task ledger: `docs/operations/current/task.md`
- Active package: `docs/operations/current/package.yaml`
- Legacy packets: see operations packet inventory report (T4)
- Archive: `docs/archive/<quarter>/INDEX.md`
- Monitoring: `docs/operations/<*_observation>/`

Tracked top-level files (required for docs checks; class/purpose in docs_registry.yaml):

| File | Class |
|------|-------|
| `current_state.md` | live pointer |
| `current/` | active operations package directory (plans, evidence, reports, closeouts, task ledger) |
| `current_data_state.md` | current fact |
| `current_source_validity.md` | current fact |
| `known_gaps.md` | compatibility pointer |
| `packet_scope_protocol.md` | active support |
| `INDEX.md` | directory index |
| `POLICY.md` | operations policy |
| `activation/` | active support (src/engine/dispatch.py + scripts/produce_activation_evidence.py refs) |
| `live_egress/` | active support (src/venue/polymarket_v2_adapter.py ref) |
| `edge_observation/` | active monitoring |
| `attribution_drift/` | active monitoring |
| `ws_poll_reaction/` | active monitoring |
| `calibration_observation/` | active monitoring |
| `learning_loop_observation/` | active monitoring |
| `docs/archive/2026-Q2/` | closed packet archive (outside operations/) |
| `AGENTS.md` | operations router |

Archived packet evidence (physically moved to `docs/archive/<YYYY>-Q<N>/`) is
listed in `docs/archive/<YYYY>-Q<N>/INDEX.md`; do not re-list those packets here.
When a packet closes and is archived, move it to `docs/archive/<YYYY>-Q<N>/`
and add a row to `docs/archive_registry.md`. Do NOT leave a stub file in
`docs/operations/`. Active archival rules: see `docs/authority/ARCHIVAL_RULES.md`.

## Single Operations Home

All planning, goals, and ongoing-operation files must live under `docs/operations/current/`.
Nothing goes in `.omc/`, `.claude/`, `.omx/`, or the repo root.

| Artifact | Canonical location |
|---|---|
| Active session goal | `docs/operations/current/GOAL.md` |
| Plans (flat files) | `docs/operations/current/plans/<name>.md` |
| Scope sidecars | `docs/operations/current/plans/<name>/scope.yaml` |
| Evidence / reports | `docs/operations/current/evidence/`, `current/reports/` |

**H7 (`planning_file_outside_operations`)**: fires RED when any `*.md` plan/goal/scaffold
file exists in `.omc/plans`, `.omc/research`, repo-local `.claude/plans`, `.omx`, or the
repo root (`*PLAN*.md`, `*GOAL*.md`, `*SCAFFOLD*.md`). Fix: move to `docs/operations/current/`.

**H8 (`session_goal_not_in_current`)**: fires RED when `current/package.yaml` exists but
`current/GOAL.md` is absent, or when a `GOAL.md`/`OBJECTIVE.md` lives outside `current/`.
Fix: create `docs/operations/current/GOAL.md` seeded from `package.yaml` + `task.md`.

Use `python scripts/zpkt.py start <slug>` to create a new plan — it writes the flat
`current/plans/<slug>.md` and `current/GOAL.md` (if not present) automatically.

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
