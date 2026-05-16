# Current State

Last updated: 2026-05-16

Role: single live control pointer for the repo.

## Status

Zeus live posture remains operator-controlled; this file only points agents at
active work surfaces and current-fact companions.

- Main HEAD: `a924766c8a` merge: PR #121 live continuous run (2026-05-16)
- Runtime entry: `src/main.py` (code-authoritative live runtime state)
- Posture: live-capable; operator params live in `config/settings.json`

### Completed infrastructure milestones (as of 2026-05-16)

| Milestone | Status | Reference |
|-----------|--------|-----------|
| K1 forecast DB split | COMPLETE | PR #114 merged; canonical schema registry in `src/state/table_registry.py` |
| K1 followup (cross-DB write seam, index gaps) | COMPLETE | PR #116 merged |
| Data daemon authority chain | COMPLETE | PR #117 merged |
| INV-37 antibody (cross-DB write seam audit) | IN FORCE | `src/state/db_writer_lock.py` canonical lock order |
| Operator script K1-broken paths | FIXED 2026-05-15 | `healthcheck.py`, `verify_truth_surfaces.py`, `venus_sensing_report.py` now route forecast-class tables to `zeus-forecasts.db` |
| PR #119 authority + topology v-next + maintenance worker | COMPLETE | 257-file / +50K LOC; topology v-next, maintenance worker scaffold, authority drift fixes |
| PR #120 live continuous run follow-up | COMPLETE | Boot authority review gaps, source health writer, schema readiness |
| PR #121 K1 live seam alignment | COMPLETE | `a924766c8a`; harvester/reader trio → `get_forecasts_connection`; settlement writer authority aligned |
| Flock gap on `get_trade_connection_with_world` | DEFERRED | Out of K1 followup scope; tracked in `docs/to-do-list/known_gaps.md` |

## Active packet control

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
| `docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md` | ACTIVE execution packet | Current `/goal` packet: prove and repair live forecast data -> live reader -> evaluator -> executor -> accepted/resting live limit order -> command/order/position/reconciliation evidence. |
| `docs/operations/task_2026-05-15_live_order_e2e_goal/` | ACTIVE execution packet | Frozen 2026-05-15 for the user `/goal`: prove real live daemon order placement and canonical record-chain continuity from the new main-based branch. |

## Current fact and checklist companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/to-do-list/known_gaps.md` — active known-gap worklist
- `docs/to-do-list/known_gaps_archive.md` — closed gap antibody archive

## Closed packet archive

Closed or superseded packet bodies from the 2026-05-02 cleanup were moved to
`docs/operations/archive/2026-Q2/` (see `docs/operations/archive/2026-Q2/INDEX.md`).
Historical registry: `docs/archive_registry.md` (deprecated forwarding doc).

## Operations routing

- `docs/operations/AGENTS.md` — packet/package routing and closeout rules
- `docs/archive_registry.md` — archived packet lookup
- `architecture/history_lore.yaml` — durable lessons
