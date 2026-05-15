# Current State

Role: single live control pointer for the repo.

## Status

Zeus live posture remains operator-controlled; this file only points agents at
active work surfaces and current-fact companions.

- Branch observed during 2026-05-02 cleanup: `live-unblock-ws-snapshot-2026-05-01`
- Runtime entry: `src/main.py` (code-authoritative live runtime state)
- Posture: live-capable; operator params live in `config/settings.json`

## Active packet control

- Active package source: none frozen; use rows below.
- Active execution packet: `docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md`.
- Receipt-bound source: `docs/operations/task_2026-05-15_live_order_e2e_goal/receipt.json`.
- Required evidence: `docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md`, packet-local critic approval, phase-specific focused tests, live deployed-code provenance, and real live order record-chain proof.
- Next action: implement the approved live-order E2E plan from a clean main-based branch. Do not claim completion until the live daemon places an expected accepted/resting order or stronger outcome and the canonical command/order/fill/position/reconciliation chain is verified.

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
| `docs/operations/task_2026-05-15_live_order_e2e_goal/` | ACTIVE execution packet | Frozen 2026-05-15 for the user `/goal`: prove real live daemon order placement and canonical record-chain continuity from the new main-based branch. |

## Current fact and checklist companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/to-do-list/known_gaps.md` — active known-gap worklist
- `docs/to-do-list/known_gaps_archive.md` — closed gap antibody archive

## Closed packet archive

Closed or superseded packet bodies from the 2026-05-02 cleanup were moved to
`docs/archives/packets/` and indexed in `docs/archive_registry.md`.

## Operations routing

- `docs/operations/AGENTS.md` — packet/package routing and closeout rules
- `docs/archive_registry.md` — archived packet lookup
- `architecture/history_lore.yaml` — durable lessons
