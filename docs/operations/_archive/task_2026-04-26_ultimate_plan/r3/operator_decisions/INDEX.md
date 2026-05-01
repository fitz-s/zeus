# Operator Decision Register — Zeus R3

This file lists every point where engineering MUST stop and wait for operator
action. Each gate has its own dossier in `operator_decisions/<gate_id>.md`.

| Gate ID | Phase blocked | Type | Decision | Artifact path | Status |
|---|---|---|---|---|---|
| Q1-zeus-egress | Z2 (cutover) | host-probe | Verify clob-v2.polymarket.com reachable from daemon | `evidence/q1_zeus_egress_2026-04-26.txt` | OPEN |
| Q-HB-cadence | Z3 (heartbeat tuning) | inquiry | Polymarket support: confirm/deny mandatory heartbeat cadence | `evidence/q_hb_cadence_2026-04-26.md` | OPEN |
| Q-FX-1 | Z4 (pUSD redemption) | classification | USDC.e ↔ pUSD PnL classification: TRADING_PNL_INFLOW / FX_LINE_ITEM / CARRY_COST | `evidence/q_fx_1_classification_decision_2026-04-26.md` | OPEN |
| INV-29 amendment | M1 (state grammar) | governance | Approve closed-law amendment for grammar-additive CommandState changes | `architecture/invariants.yaml` + `docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/inv_29_amendment_2026-04-27.md` | CLOSED: incorporated 2026-04-27; no live venue/cutover authorization |
| TIGGE-ingest go-live | F3 (TIGGE active) | data-source | Approve activating TIGGE ECMWF data ingest into ensemble pipeline | `evidence/tigge_ingest_decision_*.md` + `ZEUS_TIGGE_INGEST_ENABLED=1` + operator-approved local JSON payload path (`payload_path:` in artifact or `ZEUS_TIGGE_PAYLOAD_PATH`) | OPEN |
| Calibration retrain | F2 (Platt re-fit) | training-trigger | Approve recalibrating Extended Platt against new corpus | `evidence/calibration_retrain_decision_2026-04-26.md` | OPEN |
| CLOB v2 cutover | Z1 (LIVE_ENABLED transition) | go/no-go | Final flip from V1 → V2; operator drives the runbook | `evidence/cutover_runbook_<date>.md` | OPEN |
| Impact-report rewrite | Z0 critic gate | doc | Rewrite `v2_system_impact_report.md` with marketing-label disclaimer + 8 falsified premises corrected | `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/v2_system_impact_report.md` | CLOSED: pre-close critic+verifier approved 2026-04-27 |

## How to use

When implementing a phase whose `operator_decision_required` block names
a gate ID:
1. Check this INDEX for the artifact path.
2. If artifact does not exist on disk, FREEZE the phase and notify
   operator via session message.
3. Engineering may proceed to the point of the runtime gate but must
   not cross it. The runtime gate raises an exception if the operator
   evidence file is missing AND the relevant env flag is unset.

## Decision deadlines

Some decisions have implicit deadlines:
- Q1-zeus-egress: must clear before Z1 CutoverGuard can transition to
  POST_CUTOVER_RECONCILE.
- Q-FX-1: must clear before any pUSD redemption call, but redemption is
  not urgent (USDC stays claimable indefinitely per Polymarket docs).
- TIGGE-ingest: not on critical path. F1/F2 can be wired without TIGGE.
- Calibration retrain: can wait until live data accumulates a fresh
  corpus (≥4 weeks of trades).

## Default behaviors when gate is open

Every gate has a fail-closed default:
- Q1-zeus-egress: Z2 adapter `preflight()` returns failure; live
  placement raises CutoverPending.
- Q-HB-cadence: Z3 supervisor uses default 5s cadence with caveat in log.
- Q-FX-1: down-07 redemption path raises FXClassificationPending.
- INV-29: M1 may move past COMPLETE_AT_GATE only when the incorporated
  `architecture/invariants.yaml` amendment and planning-lock receipt are both
  cited. This gate is now closed for the M1 grammar-additive values only; it
  does not authorize M2 runtime semantics, live venue submission, or cutover.
- TIGGE: closed gate raises `TIGGEIngestNotEnabled`; open gate without a local
  operator payload raises `TIGGEIngestFetchNotConfigured`; open gate with a
  local JSON payload routes `ensemble_client.fetch_ensemble(..., model="tigge")`
  through `TIGGEIngest` without Open-Meteo HTTP. External TIGGE archive HTTP/GRIB
  remains unimplemented and requires a later operator/data-source packet.
- Calibration retrain: engine reads frozen Platt params from disk;
  no retrain attempted.
- Cutover: CutoverGuard stays in PRE_CUTOVER_FREEZE.
- Impact-report: critic-opus blocks Z0 sign-off.
