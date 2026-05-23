# Current State

Last updated: 2026-05-22

Role: single live control pointer for the repo.

## Status

Zeus live posture remains operator-controlled; this file only points agents at
active work surfaces and current-fact companions.

- Main HEAD: `d398213ece` merge: PR #297 negRisk basket exact-arb (2026-05-22)
- Runtime entry: `src/main.py` (code-authoritative live runtime state)
- Posture: live-capable; operator params live in `config/settings.json`

### Infrastructure archive

Completed infrastructure milestones through PR #297 are recorded in git history
and merged PR records. The dead 2026-05-16 milestone table is retired from this
pointer to keep it thin.

## Active packet control

Active short-term tracker: `docs/operations/current/task.md`. Live recovery workflow,
current analysis references, implementation status, and verification evidence.
Root `task.md` is a thin pointer to this location.

- Active package manifest: `docs/operations/current/package.yaml` (OperationPackage current_live_recovery)
- Active execution packet: `docs/operations/current/package.yaml` (see package.yaml for subtasks and frontier)
- Active package source: `docs/operations/current/package.yaml`
- Receipt-bound source: `docs/operations/current/receipt.json`
- Required evidence: see `docs/operations/current/task.md`
- Next action: see `docs/operations/current/task.md` (current live recovery workflow)

Freeze a new packet through this file before starting any implementation slice.

## Active monitoring surfaces

| Surface | Directory | Closeout rule |
|--------|-----------|---------------|
| Edge observation | `docs/operations/edge_observation/` | Operator-managed rolling evidence; keep active. |
| Attribution drift | `docs/operations/attribution_drift/` | Operator-managed rolling evidence; keep active. |
| WS/poll reaction | `docs/operations/ws_poll_reaction/` | Operator-managed rolling evidence; keep active. |
| Calibration observation | `docs/operations/calibration_observation/` | Operator-managed rolling evidence; keep active. |
| Learning loop | `docs/operations/learning_loop_observation/` | Operator-managed rolling evidence; keep active. |

## Active / deferred operation packets

| Surface | Status | Why it remains in operations |
|---------|--------|------------------------------|
| `docs/operations/task_2026-04-26_ultimate_plan/2026-05-01_live_alpha/evidence/tigge_ingest_decision_2026-05-01.md` | ACTIVE runtime-gating evidence | TIGGE `entry_primary` authorization depends on this evidence path; do not archive without a replacement operator-decision path. |

## Current fact and checklist companions

- `docs/operations/current_data_state.md` — data posture (STALE_FOR_LIVE; last audited 2026-04-28)
- `docs/operations/current_source_validity.md` — source posture (STALE_FOR_LIVE; last audited 2026-05-03)
- `docs/to-do-list/known_gaps.md` — active known-gap worklist
- `docs/to-do-list/known_gaps_archive.md` — closed gap antibody archive

## Closed packet archive

Closed or superseded packet bodies are in
`docs/operations/archive/2026-Q2/` (see `docs/operations/archive/2026-Q2/INDEX.md`).
Historical registry: `docs/archive_registry.md` (deprecated forwarding doc).

## Operations routing

- `docs/operations/AGENTS.md` — packet/package routing and closeout rules
- `docs/archive_registry.md` — archived packet lookup
- `architecture/history_lore.yaml` — durable lessons
