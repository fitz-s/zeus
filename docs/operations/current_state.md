# Current State

Role: single live control pointer for the repo.

## Status

Zeus live posture remains operator-controlled; this file only points agents at
active work surfaces and current-fact companions.

- Branch observed during 2026-05-02 cleanup: `live-unblock-ws-snapshot-2026-05-01`
- Runtime entry: `src/main.py` (code-authoritative live runtime state)
- Posture: live-capable; operator params live in `config/settings.json`

## Active packet control

- Active package source: `docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md`
- Active execution packet: `docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md`
- Receipt-bound source: `docs/operations/task_2026-05-15_live_order_e2e_verification/receipt.json`
- Required evidence: `LIVE_ORDER_E2E_VERIFICATION_PLAN.md`, `LIVE_ORDER_E2E_VERIFICATION_CRITIC_APPROVAL.md`, phase evidence bundles named by the active plan, and a later receipt/work log for implementation closeout.
- Next action: execute the live order end-to-end verification packet. First implementation slice is forecast-live health probe repair and read-only live-order evidence checker; live restart and live submit require the packet's `LIVE_RESTART_GO` and `LIVE_SUBMIT_GO` checklists.

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
| `docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md` | ACTIVE execution packet | Current `/goal` packet: prove and repair live forecast data -> live reader -> evaluator -> executor -> accepted/resting live limit order -> command/order/position/reconciliation evidence. |

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
