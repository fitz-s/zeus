# Post-merge realignment — PR #55 (TIGGE 12z + Phase 2/3) and PR #56 (oracle/kelly evidence)

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Authority basis:** PR #55 merge (#6770930a) on top of PR #56 (#9074b98f) — 2026-05-04.
**Purpose:** Single-page realignment of the unlock plan after both PRs landed
on main, so the next operator can see at-a-glance what's done, what was
superseded, and what is still pending before live unlock.

---

## What landed

| Phase | Plan target | PR | Status | Truth artifact |
|---|---|---|---|---|
| 1 | TIGGE 12z download + 90d backfill | #55 | Code merged; data backfill in flight on cloud (10 lanes, 5 ECMWF accounts × 2 metrics, 2024-01-01..2026-05-02) | `tigge-runner` GCE instance + `scripts/cloud_tigge_autochain.sh` |
| 2 | Platt cycle/source/horizon stratification | #55 | Schema + writer + reader merged; refit pending real 12z data | `platt_models_v2` ALTER, `calibration_pairs_v2` ALTER, `scripts/refit_platt_v2.py`, `manager.get_calibrator(cycle=, source_id=, horizon_profile=)` |
| 2.5 | Evidence-based calibration transfer | #56 (replaced #55's design) | Replaced by **MarketPhaseEvidence + oracle_evidence_status** on main | `src/strategy/market_phase_evidence.py`, `src/strategy/oracle_status.py`, `src/strategy/oracle_estimator.py` |
| 2.6 | MetricIdentity source_family + OpenData pair builder | #55 | Merged | `src/types/metric_identity.py`, `scripts/rebuild_calibration_pairs_v2.py`, `derive_phase2_keys_from_ens_result` helper |
| 2.75 | Robust lower-bound Kelly | #56 (replaced #55's design) | Replaced by **phase_aware_kelly_live + StrategyProfile** registry on main | `src/strategy/phase_aware_kelly_live.py` (PR #56), `src/strategy/strategy_profile.py` (PR #56), `src/strategy/kelly.py` extensions (PR #56) |
| 3 | ENSEMBLE_MODEL_SOURCE_MAP routing flip (`ecmwf_ifs025` → `ecmwf_open_data`) | #55 | Merged + entry_primary guard for all roles | `src/data/forecast_source_registry.py`, `src/data/ensemble_client.py` |

## What was superseded (do not re-add)

PR #55 implemented and PR #56 replaced — these are gone from main:

- `src/data/calibration_transfer_policy.py::evaluate_calibration_transfer` (the new OOS-evidence function) and `CalibrationTransferEvidence` dataclass.
- `validated_calibration_transfers` SQLite table + its migration script.
- `src/strategy/robust_kelly.py` module (`SizingEvidence`, `SizingUncertaintyInputs`, `robust_kelly_size`).
- Phase 2.5 `CALIBRATION_TRANSFER_SHADOW_ONLY` / `CALIBRATION_TRANSFER_BLOCKED` / `CALIBRATION_TRANSFER_FAULT` rejection stages in evaluator.

The legacy `evaluate_calibration_transfer_policy` (string-mapping
operator-opt-in policy used by `entry_forecast_shadow.py` and
`evaluator.py:891`) survives; it remains the live-eligibility gate for
OpenData transfer until PR #56's evidence stack covers that surface.

## What is still pending before unlock

### A. TIGGE 12z full-history backfill data
- [ ] All 10 lanes finish Phase A (2024-01-01..2026-05-02).
- [ ] `tigge-autochain` triggers Phase B (2023-01-01..2023-12-31 default).
- [ ] Rsync GRIBs cloud → local; run cycle-aware extract; ingest into
      `ensemble_snapshots_v2`.
- [ ] Verify: `SELECT cycle, COUNT(*) FROM ensemble_snapshots_v2 WHERE data_version LIKE 'tigge_%' GROUP BY cycle;` is non-zero for both `00` and `12`.
- [ ] Run `python scripts/refit_platt_v2.py --no-dry-run --force`; verify
      cycle-stratified rows in `platt_models_v2`.

See `POSTDOWNLOAD_CHAIN.md` for the full sequence.

### B. PR #56 replacement-design wiring (replaces PR #55's Phase 2.5/2.75 unlock blockers)
- [ ] `MarketPhaseEvidence.phase_source` is populated on every live
      candidate (entry path) — see PR #56 PLAN.md Phase B.
- [ ] `oracle_evidence_status` covers OpenData transfer — eliminate the
      legacy `evaluate_calibration_transfer_policy` gate when this
      coverage is verified.
- [ ] `phase_aware_kelly_live` flipped on at the evaluator call site
      (currently `kelly.py::kelly_size` is still called at
      evaluator.py:493).
- [ ] `StrategyProfile.kelly_for_phase(market_phase)` registry-driven
      sizing — see PR #56's strategy_profile_registry.

The previous PR #55 unlock blockers (real CIs for robust_kelly, OOS
evidence for validated_calibration_transfers) are no longer relevant —
PR #56 replaced them with this evidence stack.

### C. Live unlock gate
- [ ] precedence=200 NULL-expiry `control_overrides` row remains in
      place until both A and B above are checked.
- [ ] Critic-opus third-pass review after B-list completes.
- [ ] Operator-go: diagnostic green for ≥1 cycle on real 12z data with
      cycle-stratified Platt + phase_aware_kelly_live.

## Pointers

- `UNLOCK_SEQUENCE.md` — original PR #55 plan (Phase 1–4 sequencing); the
  Phase 2.5 / 2.75 sections in that doc are now historical.
- `POSTDOWNLOAD_CHAIN.md` — local rsync → extract → ingest → refit flow.
- `LIVE_TRADING_LOCKED_2026-05-04.md` (root) — operator-facing lock
  rationale + 6-step unlock checklist; update with item B-list above.
- `task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md` — PR #56's
  design doc + future work.
- `scripts/cloud_tigge_autochain.sh` — runs on `tigge-runner` GCE
  instance; auto-triggers Phase B when Phase A's 10 lanes complete.
- `scripts/cloud_tigge_autochain.sh` deploy:
  ```
  gcloud compute scp scripts/cloud_tigge_autochain.sh tigge-runner:'/data/tigge/workspace-venus/51 source data/scripts/' --zone=europe-west4-a --project=snappy-frame-468105-h0
  ```
