# Open Work Items (consolidated 2026-05-01)

Source: distilled from task_2026-04-26 through task_2026-04-30 dirs (archived to _archive/).

---

## CLOB V2 Migration — task_2026-04-26_polymarket_clob_v2_migration

R3 slices Z0–Z4 and U1 are DONE and in main. U2 is unfrozen and ready to start.
Live cutover remains blocked by operator gates Q1 (zeus egress verification), Q-FX-1 (pUSD redemption/FX classification), heartbeat gate, and collateral gate.

- [ ] R3 U2: start next R3 slice (venue command repo / related) — unfrozen, awaiting next phase boot
- [ ] Resolve Q1 (zeus egress check to clob-v2.polymarket.com) — hard gate on live cutover
- [ ] Resolve Q-FX-1 (pUSD redemption / FX classification, deferred to R1) — gate on live cutover
- [ ] R3 M1–M5 + T1 slices: venue state machine, reconciliation, and cutover runbook — all downstream of Q1/Q-FX-1
- [ ] Live V2 cutover: operator-authorized only after Q1/Q-FX-1/M-series/T1 gates pass

---

## Backtest First-Principles Review — task_2026-04-27_backtest_first_principles_review

Planning artifacts (01–04 docs) are DONE. Implementation slices S1, S2, S4, F11.1–F11.6 landed. Backtest `economics.py` is tombstoned until data gates clear.

- [ ] Operator decision Q3: Polymarket data ingestion source (subgraph A / websocket B / both C) — gates Economics-grade backtest; `economics.py` tombstone refuses to run until `market_events_v2 > 0`
- [ ] Operator decision Q5: empty-provenance WU observations (39,431 rows) — quarantine-all vs. partial oracle_shadow backfill vs. log-replay; gates training readiness
- [ ] `task_2026-04-XX_polymarket_websocket_capture`: scope and implement if Q3 answer includes option B/C
- [ ] LOW-track settlements writer for `market_events_v2` (Q4 decision): build parallel writer now or defer until V2 cutover

---

## F11 Forecast Issue Time — task_2026-04-28_f11_forecast_issue_time

All F11 slices (F11.1–F11.6) are DONE and in main. The writer now stamps `forecast_issue_time` and `availability_provenance` on new rows. 23,466 existing rows were backfilled.

- [ ] 1,683 recently-ingested rows still carry `availability_provenance = NULL` — these are rows written after the backfill window and before the live cron resumed; next cron tick will populate new rows going forward; a targeted cleanup script may be needed for the residual gap
- [ ] Q5 WU obs triage: separate packet planned (`F11 apply runbook` §8) — operator decisions Q5-A/B/C still open

---

## Settlements LOW Backfill — task_2026-04-28_settlements_low_backfill

DONE: 48 LOW rows inserted (4 VERIFIED + 44 QUARANTINED). Already in main.

- [ ] Daily obs catch-up: 43 QUARANTINED `no_observation_for_target_date` rows can be reactivated once `observations.low_temp` populates 2026-04-20 onward (ingest lag ~9 days as of 2026-04-28)
- [ ] Live LOW market scanner persistence: `src/data/market_scanner.py` in-memory cache should write to `market_events_v2`; currently 0 rows there
- [ ] Investigate Seoul 2026-04-15 1°C drift (obs=9°C, market settled 10°C) — same root cause pattern as HIGH KL/Cape-Town drift

---

## Settlements Physical Quantity Migration — task_2026-04-28_settlements_physical_quantity_migration

DONE: 1,561 HIGH rows migrated from `daily_maximum_air_temperature` to `mx2t6_local_calendar_day_max`. Already in main.

No open items — migration applied, antibody test live.

---

## Weighted Platt Precision Weight RFC — task_2026-04-28_weighted_platt_precision_weight_rfc

Status: DRAFT RFC, no implementation done. Pending operator decisions.

- [ ] Operator decision 1: approve or reject structural change (replace `training_allowed: bool` with `precision_weight: float`)
- [ ] Operator decision 2: approve weight function family (`D_softfloor` recommended per PoC v4 evidence)
- [ ] Operator decision 3: approve schema migration window (5 phases over ~3 weeks)
- [ ] If approved: implementation packet for schema change to `ensemble_snapshots_v2`, `calibration_pairs_v2`, Platt fit code path, and antibody tests

---

## Reality Semantics Refactor Package — task_2026-04-30_reality_semantics_refactor_package

IN-FLIGHT: active worktree branch `remotes/origin/worktree/reality-semantics-refactor`. Phase 0A guardrail slice landed. Merge into `plan-pre5` completed 2026-05-01. Several corrected-semantics slices landed (buy_no exit quote split, state-owned buy_no exit, native NO authority).

- [ ] IN-FLIGHT WORKTREE: `worktree/reality-semantics-refactor` — remaining corrected executor bridge slices; do not archive the task dir without confirming final merge state
- [ ] Remaining legacy runtime seams: monitor quote/probability coupling, mixed evidence cohorts, late executable repricing — see `PHASE_0A_PROGRESS.md` for current inventory
- [ ] Non-authorization surfaces still in place: live deploy, live venue submission, production DB mutation, config flips, schema migration apply, source-routing changes, strategy promotion — all require explicit operator sign-off per `START_HERE.md`

---

## Source Auto-Conversion — task_2026-04-30_source_auto_conversion

Phase A and Phase B both DONE and in main. Full apply automation landed including deterministic config writer, scoped backfill/rebuild/refit, Paris-style end-to-end test.

No open items — both phases complete.

---

## Source Contract Merge — task_2026-04-30_source_contract_merge

DONE: source-contract quarantine protocol merged into plan-pre5 (commit `76a388a1`). Critic verdict: APPROVE. M1 (station-proof hole) resolved before merge.

No open items — merge landed and reviewed.

---

## Two-System Independence — task_2026-04-30_two_system_independence

Phases 1, 1.5, 2, and 3 are DONE and in main (commit `a2c46cdd`). Phase 4 (strategy-as-process boundary) is explicitly deferred.

- [ ] Phase 4 revisit trigger (within 8 weeks of Phase 3 completion): if two enabled strategies in `KNOWN_STRATEGIES` have divergent restart-policy needs, escalate to architect for process split — see `design.md` §3.0 row 3
- [ ] Q3 (schema manifest versioning): pre-commit hook vs. manual bump — Phase 2 surfaces this; operator decision pending
- [ ] Q4 (quarantine routing destination): `data_quarantine` table vs. JSONL file at `state/quarantine/` — Phase 2 blocker
- [ ] Q5 (TIGGE / ECMWF fast-path for trading): confirm `ensemble_client.py` remains read-only from trading — Phase 2 confirmation needed
- [ ] `python -m scripts.ingest.backfill` unified orchestration: design doc §2.3 moved this to Phase 1, but `scripts/ingest/backfill.py` does not exist; the 16 ad-hoc `scripts/backfill_*.py` scripts remain; scope a follow-up package to unify them
