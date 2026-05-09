# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: root AGENTS.md object-meaning invariance goal; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md remaining-mainline ledger; PR97 mainline continuation

# Object-Meaning Invariance Remaining Mainline Closeout

Status: CLOSED FOR PR97 SOURCE/TEST SCOPE, NOT LIVE UNLOCK, NOT DATA MUTATION AUTHORITY
Date: 2026-05-08
Branch: `object-invariance-mainline-next-2026-05-08`
PR: `https://github.com/fitz-s/zeus/pull/97`

This packet closes the remaining source/test/audit work left by the object-
meaning invariance mainline ledger after Waves 27-30 and the PR97 RiskGuard
follow-up. It does not authorize live trading unlock, live venue/account
mutation, production DB writes, schema migration, backfill, relabeling,
settlement harvest, redemption, report publication, or legacy data rewrite.

## Remaining Ledger Items

| Item | Scope | Status | Stop boundary |
|---|---|---|---|
| R1 | Packetize Wave22/Wave23 evidence from source and test truth | CLOSED | No retroactive critic verdict invented; source/test evidence only |
| R2 | Sweep `venue_trade_facts` downstream consumers, including report/replay/learning paths | CLOSED FOR SCOPED SOURCE/TEST | Existing DB-row mutation requires operator decision |
| R3 | Sweep settlement/report/replay/learning consumers after environment-authority repairs | CLOSED FOR SOURCE/TEST | No settlement harvest, redemption, publication, relabel, or backfill |
| R4 | Read-only historical physical-DB contamination audit for repaired row classes | CLOSED READ-ONLY | Any cleanup beyond SELECT/read-only dry-run is OPERATOR_DECISION_REQUIRED |
| R5 | Front-of-pipeline source/calibration remaining pass | CLOSED READ-ONLY | Future current-fact staleness or unresolved source authority becomes BLOCKED/UNKNOWN, not guessed truth |

## Topology Notes

- `--task-boot-profiles` passed at packet start.
- Initial `operation planning packet` route admitted this `PLAN.md` and the
  mainline ledger, but not `docs/operations/AGENTS.md`.
- A second registry route still treated the missing new packet as unclassified.
  The safe sequence is: create this admitted packet first, then rerun
  map-maintenance/registry routing after the path exists.

## Working Map

Money path used for this closeout:

`forecast/source data -> calibration/belief -> executable market interpretation -> edge/sizing -> venue command/order intent -> venue order/trade facts -> position lots/current -> monitor/exit/risk -> settlement rows/events -> replay/report/learning`

Object authorities observed in source/test truth:

| Object class | Authority surface | Notes |
|---|---|---|
| Forecast/source object | `docs/operations/current_source_validity.md`, `src/data/ensemble_client.py`, source registry helpers | Current-fact surface is fresh for this task window but not durable law. `ecmwf_open_data` without ingest class fails closed instead of relabeling Open-Meteo broker payloads. |
| Calibration/belief object | `src/calibration/forecast_calibration_domain.py`, `src/strategy/market_fusion.py`, `src/execution/harvester.py` | Contract outcome domain, source/cycle/horizon identity, and verified authority gates protect p_raw/p_cal/p_posterior use. |
| Venue/execution object | `src/state/venue_command_repo.py`, `src/execution/fill_tracker.py`, `src/execution/exchange_reconcile.py`, `src/ingest/polymarket_user_channel.py` | `venue_commands` describe submit lifecycle; `venue_trade_facts` describe venue trade lifecycle; `position_lots` describe economic exposure. |
| Risk/exposure object | `src/risk_allocator/governor.py`, `src/state/db.py` schema triggers | Active exposure is now explicit: optimistic, confirmed, or exit-pending only. Quarantine is held/review state, not capacity exposure. |
| Settlement/report/replay object | `src/state/db.py::query_authoritative_settlement_rows`, `src/engine/replay.py`, `scripts/verify_truth_surfaces.py` | Canonical settlement rows/events carry env and verified authority; `outcome_fact` is legacy/diagnostic, not learning or promotion authority. |

Canonical truth hierarchy for this packet:

`venue/chain facts and canonical DB events > repository write seams and schema triggers > canonical read models > derived reports/replay diagnostics > archived packets/backlog/chat context`

## Wave22/Wave23 Evidence Packetization

Wave22 and Wave23 were implementation-complete before this packet but lacked a
reviewable evidence surface. This section records source/test truth without
inventing missing historical critic verdicts.

| Wave | Boundary | Material values | Source/test evidence | Status |
|---|---|---|---|---|
| 22 | M3 user-channel producer -> shared `venue_trade_facts` object | `trade_id`, `venue_order_id`, `command_id`, trade `state`, `filled_size`, `fill_price`, `source`, `source_trade_fact_id`, lot `state` | `src/ingest/polymarket_user_channel.py`; `tests/test_user_channel_ingest.py::{matched/mined/confirmed/failed/retrying}`; `tests/test_user_channel_ingest.py::test_failed_without_fill_economics_after_fill_observation_rolls_back_optimistic_projection` | Packetized here as source/test evidence. No retroactive critic verdict claimed. |
| 23 | M5/legacy polling producer -> shared `venue_trade_facts` object | same as Wave22 plus REST payload hash/timestamp, command event state, active lot materialization | `src/execution/fill_tracker.py`; `tests/test_command_recovery.py`; `tests/test_live_safety_invariants.py::test_legacy_polling_trade_lifecycle_requires_stable_fill_economics`; new failed rollback tests in this packet | Packetized here and extended by the FAILED lifecycle repair below. |

## Material Value Lineage

| Value | Real object denoted | Origin | Authority/evidence class | Unit/side/time basis | Transformation | Persistence | Consumers | Meaning verdict |
|---|---|---|---|---|---|---|---|---|
| `venue_trade_facts.trade_id` | Venue trade identity | WS user channel, REST poll, M5 reconcile | Venue trade observation | Venue lifecycle time: observed/venue timestamp | Must remain one real trade across state transitions | `venue_trade_facts` append-only | lot writer, calibration retrain, exchange journal, replay/readiness diagnostics | Preserved |
| `venue_trade_facts.state` | Trade lifecycle state, not command state | same producers | Venue lifecycle evidence | `MATCHED/MINED/CONFIRMED/RETRYING/FAILED` | Fill-progress states require positive fill economics; FAILED/RETRYING do not | `venue_trade_facts` | command events, position lots, calibration, risk allocation | Repaired for FAILED rollback |
| `venue_trade_facts.filled_size/fill_price` | Executable fill economics only for fill-progress/finality states | producer payload | Venue economics evidence | shares / price, positive finite for `MATCHED/MINED/CONFIRMED` | FAILED/RETRYING may materialize as `0` placeholders and cannot authorize exposure | `venue_trade_facts` | `position_lots`, calibration retrain, readiness | Explicitly transformed |
| `position_lots.state` | Economic exposure lifecycle append | repository lot writer | Canonical exposure evidence | position-local sequence time | `FAILED` source trade appends `QUARANTINED` rollback lot | `position_lots` append-only | risk allocator, downstream exposure readers | Repaired |
| `position_lots.shares` | Venue filled share count for lot exposure | source trade fact | Economic unit: shares | captured/state-changed time | Copied as text to preserve fractional shares | `position_lots` | risk allocation/capacity | Repaired fractional preservation |
| `risk_allocator.ExposureLot.state` | Capacity-active exposure class | `load_position_lots` | Risk read model | current latest lot per position | Admits only `OPTIMISTIC_EXPOSURE`, `CONFIRMED_EXPOSURE`, `EXIT_PENDING` | in-memory risk allocator | executor pre-submit gates | Repaired; `QUARANTINED` no longer active |
| `outcome_fact.*` | Legacy lifecycle projection / diagnostic actual-trade comparison | legacy table/producers | Diagnostic/legacy evidence | settlement/report time | Explicitly tagged non-learning/non-promotion in report/replay surfaces | `outcome_fact`, replay audit outputs | replay diagnostics, truth-surface report | Preserved as diagnostic only |
| `query_authoritative_settlement_rows.*` | Settlement/report/learning authority rows | `position_events` with env, then legacy decision-log fallback only if canonical absent | Canonical settlement authority or degraded row | env-gated settlement time | Normalizes payload and computes readiness flags | in-memory row dict/report outputs | riskguard, harvester, strategy tracker, verify_truth_surfaces | Preserved |

## Findings And Repairs

| ID | Severity | Classification | Boundary | Why it matters | Active status | Repair |
|---|---|---|---|---|---|---|
| RMI-1 | S0 | FAILED trade lifecycle evidence could become a non-economic fact while prior optimistic exposure stayed active | `venue_trade_facts` -> `position_lots` | Portfolio/risk could keep exposure that venue has reported failed | Active across legacy polling and exchange reconcile; WS path already repaired | `append_trade_fact(state='FAILED')` now rolls back open optimistic lots by appending `QUARANTINED`; helper is idempotent and preserves fractional shares |
| RMI-2 | S0 | Legacy polling skipped FAILED/RETRYING trade facts without fill economics before the lifecycle fact could roll back exposure | Poll payload -> `venue_trade_facts` | Missing size/price in failure messages could block the rollback fact and leave stale exposure | Active in legacy polling | Polling now records non-fill-progress trade lifecycle states without requiring positive fill economics and fail-closes with `REVIEW_REQUIRED` |
| RMI-3 | S0 | Exchange reconcile treated FAILED state with changed/missing economics as economic drift instead of lifecycle transition | M5 reconcile -> `venue_trade_facts` | A valid FAILED transition could be rejected because failure payloads omit economics | Active in M5 reconcile | Fill-economics drift check is restricted to `MATCHED/MINED/CONFIRMED`; FAILED can append and trigger central rollback |
| RMI-4 | S0 | Risk allocator counted latest `QUARANTINED` lots as active capacity exposure | `position_lots` -> risk allocator | Corrected rollback lots still affected sizing/risk capacity as if active exposure | Active downstream bypass | `load_position_lots` now admits only `OPTIMISTIC_EXPOSURE`, `CONFIRMED_EXPOSURE`, and `EXIT_PENDING`; `QUARANTINED` remains a literal but not active |

## Downstream Contamination Sweep

| Surface | Evidence | Result |
|---|---|---|
| Monitor / exit | Grep for `venue_trade_facts`, `position_lots`, `QUARANTINED`, and monitor probability paths | No direct use of failed trade facts as fill economics found. Existing monitor probability residuals were repaired in Waves 28-30 and PR97 follow-up. |
| Risk allocation | `src/risk_allocator/governor.py` | Active bypass found and repaired as RMI-4. |
| Calibration / learning from trade facts | `src/state/venue_command_repo.py::load_calibration_trade_facts`; `src/calibration/retrain_trigger.py` | CONFIRMED-only and positive fill-economics gates remain. MATCHED/MINED/FAILED/RETRYING cannot train. |
| Backtest/economics readiness | `src/backtest/economics.py` | Requires CONFIRMED trade facts with positive fill economics, confirmed position lots, and outcome provenance columns; readiness still appends `economics_engine_not_implemented`. |
| Replay/report outcome facts | `src/engine/replay.py`; `scripts/verify_truth_surfaces.py`; `scripts/venus_sensing_report.py` | `outcome_fact` remains legacy/diagnostic with `learning_eligible=False` and `promotion_eligible=False`; no live authority promotion found. |
| Settlement/report/learning env | `src/state/db.py::query_authoritative_settlement_rows`; `src/state/strategy_tracker.py`; `src/state/edge_observation.py`; `src/state/attribution_drift.py` | Readers use env-gated authoritative settlement rows and readiness flags. No repair needed in this packet. |
| Front-of-pipeline calibration | `src/data/ensemble_client.py`, `src/calibration/forecast_calibration_domain.py`, `src/strategy/market_fusion.py`, `src/execution/harvester.py` | Source identity, data_version, contract-domain, authority, training_allowed, and causality guards are explicit. Current-fact surfaces are within 14-day max staleness on 2026-05-08. |

## Read-Only Physical DB Audit

Read-only policy: no migrations, no relabeling, no cleanup, no canonical DB
writes. Queried only local worktree state DBs.

| DB | Audit result |
|---|---|
| `state/zeus_trades.db` | Tables present. Read-only SELECT found `bad_confirmed_trade_economics=0`, `latest_active_lots_source_trade_failed=0`, `settled_events_missing_or_nonlive_env=0`, `outcome_fact_rows=0`, `validated_calibration_transfers_rows=0`. |
| `state/risk_state-live.db` | `alert_cooldown` present, row count `0`. |
| `state/risk_state.db` | `risk_state` present, row count `0`; `sqlite3 -readonly` could not open this local DB, so normal sqlite read was used without writes. |
| `state/zeus-world.db` | Empty/schema-less local worktree DB; `sqlite3 -readonly` could not open. No settlement/world-row audit claim is made for this file. |

Historical production/canonical DB cleanup remains **not authorized**. If a
future operator wants physical contamination cleanup, required plan:

1. snapshot the target DB and checksum it;
2. run the SELECT probes above plus per-row detail queries;
3. produce a dry-run diff grouped by row authority and reason;
4. get explicit operator approval for any append-only remediation, relabel, or
   quarantine write;
5. verify rollback from snapshot before applying.

## Front-Of-Pipeline Source/Calibration Pass

Semantic boot note: this repo's current `topology_doctor.py` exposes
`--task-boot-profiles` but not the documented `semantic-bootstrap` subcommand.
`--task-boot-profiles` passed before this packet. The calibration boot profile
requires current source/data reads and proof of training source identity plus
HIGH/LOW separation; those were satisfied by read-only inspection only.

Current-fact posture on 2026-05-08:

- `docs/operations/current_source_validity.md`: latest audited entry
  2026-05-03, max staleness 14 days. Usable for planning context in this pass.
- `docs/operations/current_data_state.md`: latest audit 2026-04-28, max
  staleness 14 days. Usable for planning context in this pass but not live
  mutation authority.
- Hong Kong remains a caution path requiring fresh audit before any source or
  settlement change; no such change was attempted.
- No source/calibration source mutation, rebuild, promotion, or live-entry
  authorization was performed.

## Verification

Focused checks run:

- `pytest -q tests/test_provenance_5_projections.py::test_optimistic_exposure_rolled_back_on_FAILED_trade tests/test_live_safety_invariants.py::test_legacy_polling_failed_without_fill_economics_rolls_back_optimistic_lot tests/test_live_safety_invariants.py::test_legacy_polling_failed_trade_status_is_not_fill_progress_authority tests/test_user_channel_ingest.py::test_failed_without_fill_economics_after_fill_observation_rolls_back_optimistic_projection tests/test_exchange_reconcile.py::test_failed_trade_fact_rolls_back_existing_optimistic_lot tests/test_exchange_reconcile.py::test_failed_or_retrying_trade_fact_does_not_advance_command_fill_state tests/test_risk_allocator.py::test_position_lots_reader_uses_latest_append_only_state_and_counts_guards` -> passed, 8 tests, 4 sqlite datetime warnings.
- `python3 -m py_compile src/state/venue_command_repo.py src/execution/fill_tracker.py src/execution/exchange_reconcile.py src/risk_allocator/governor.py` -> passed.
- Wider local collection attempt:
  `pytest -q tests/test_provenance_5_projections.py tests/test_user_channel_ingest.py tests/test_exchange_reconcile.py tests/test_live_safety_invariants.py tests/test_risk_allocator.py`
  -> 229 passed, 13 failed due local `ModuleNotFoundError: No module named
  'sklearn'` while importing `src/calibration/platt.py`. `requirements.txt`
  declares `scikit-learn==1.8.0`; this packet did not change dependency
  management or install local packages.

Critic verdict:

- `APPROVE` for the FAILED trade lifecycle rollback wave, including the risk
  allocator downstream fix. No S0-S3 findings.

## Closeout Status

| Item | Status | Closeout evidence |
|---|---|---|
| R1 | CLOSED | Wave22/23 source/test evidence packetized above without invented verdicts. |
| R2 | CLOSED FOR SCOPED SOURCE/TEST; PHYSICAL DB CLEANUP NOT AUTHORIZED | Downstream sweep found and repaired risk allocator bypass; read-only DB audit found no failed-trade active lot contamination in local `state/zeus_trades.db`. |
| R3 | CLOSED FOR SOURCE/TEST; PHYSICAL DB CLEANUP NOT AUTHORIZED | Settlement/report/replay/learning consumers use authoritative/degraded rows or diagnostic-only outcome_fact. |
| R4 | CLOSED READ-ONLY | Local DB audit complete; no writes performed. |
| R5 | CLOSED READ-ONLY | Front-of-pipeline source/calibration pass found existing fail-closed guards; no mutation attempted. |

## Stop Conditions

Stop with `OPERATOR_DECISION_REQUIRED` before any action that would:

- write canonical live trade/world/risk databases;
- run migrations, backfills, relabeling, settlement harvest, redemption, or
  report-publication jobs;
- mutate venue/account state or use credentials for venue mutation;
- promote replay, report, backtest, diagnostic, or legacy rows into live truth
  or learning authority;
- silently rewrite legacy data as corrected truth;
- claim a live unlock or global proof beyond the scoped invariants repaired or
  audited here.
