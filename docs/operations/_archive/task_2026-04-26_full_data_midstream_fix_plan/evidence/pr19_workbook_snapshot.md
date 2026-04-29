# PR #19 Workbook Snapshot (vendored)

Created: 2026-04-26
Last audited: 2026-04-26
Authority basis: vendored copy of `docs/to-do-list/zeus_full_data_midstream_review_2026-04-26.md` from `origin/copilot/full-review-of-upstream-data-storage@6e2b4b8`
PR: https://github.com/fitz-s/zeus/pull/19
Author: `app/copilot-swe-agent`
Status: vendored read-only snapshot for offline reference; not authority. Primary source is the live workbook on PR #19 / `midstream_remediation` once merged.

When PR #19 merges into `midstream_remediation`, replace this file with a pointer to the canonical path.

---

# Zeus Full Data + Midstream Review

Created: 2026-04-26
Status: operational evidence / todo workbook, not authority
Scope: upstream data storage, daily collection, backfill, and midstream data-to-Polymarket-order flow

## Review boundary

- This was a read-only source/script review. No production DB rows were inspected or mutated.
- `python3 scripts/topology_doctor.py --navigation --task "full review of upstream data storage, daily collection backfill, and midstream conversion into Polymarket orders" --files src scripts docs architecture` was blocked by pre-existing registry/doc/lore issues unrelated to this review.
- Current control state still says P4 mutation is blocked and no active execution packet is frozen.
- `python3 scripts/verify_truth_surfaces.py --mode p4-readiness --json` could not run in this sandbox because `numpy` is not installed; the latest checked-in P4 packet already records `NOT_READY`.

## End-to-end map

### Part 1 — Upstream storage, daily collection, and backfill

| Stage | Main files | Store / artifact | Current finding |
|---|---|---|---|
| Source routing | `src/data/tier_resolver.py`, `config/cities.json`, `architecture/city_truth_contract.yaml` | source-role contract | Provider selection must stay audit-bound; `current_source_validity.md` is now historical planning context and needs refresh before current truth claims. |
| Daily settlement observations | `src/data/daily_observation_writer.py`, `scripts/backfill_wu_daily_all.py`, `scripts/backfill_hko_daily.py`, `scripts/backfill_ogimet_metar.py` | `state/zeus-world.db::observations` | Writer provenance and daily revision-history guardrails exist; WU collection remains operator-blocked when `WU_API_KEY` is absent. |
| Hourly / obs_v2 | `src/data/observation_instants_v2_writer.py`, `scripts/backfill_obs_v2.py`, `scripts/fill_obs_v2_meteostat.py`, `scripts/fill_obs_v2_dst_gaps.py` | `observation_instants_v2` | obs_v2 is populated, but hourly high/low metric-layer ownership is still undecided for full P4. |
| Forecast / ensemble ingest | `src/data/ensemble_client.py`, `src/data/forecasts_append.py`, `scripts/ingest_grib_to_snapshots.py`, `scripts/etl_tigge_ens.py` | `ensemble_snapshots`, `forecasts`, v2 snapshot tables | TIGGE assets are reportedly downloaded, but local parity/hash/source-time manifests and v2 population remain blocked by operator evidence. |
| P_raw backfill | `scripts/backfill_tigge_snapshot_p_raw.py`, `scripts/backfill_tigge_snapshot_p_raw_v2.py` | `ensemble_snapshots*.p_raw_json` | Replay-compatible materialization exists; v2 path cannot become authoritative while `ensemble_snapshots_v2` is empty. |
| Calibration rebuild | `scripts/rebuild_calibration_pairs_canonical.py`, `scripts/rebuild_calibration_pairs_v2.py`, `scripts/refit_platt_v2.py` | `calibration_pairs*`, `platt_models*` | Canonical/v2 rebuild is blocked until market/settlement v2 and verified TIGGE snapshot inputs exist. |
| Settlement learning | `src/execution/harvester.py`, `src/contracts/settlement_semantics.py` | `settlements`, `calibration_pairs` | Live harvester write path remains feature-flag/governance blocked; settlement writes must pass `SettlementSemantics`. |

### Part 2 — Midstream data to Polymarket orders

| Stage | Main files | Output | Current finding |
|---|---|---|---|
| Cycle sequencing | `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py` | discovery/evaluation/monitor/harvest cycle | Chain reconciliation and risk checks are orchestrated before new entries; RED sweep has a duplicated terminal-state list risk. |
| Candidate evaluation | `src/engine/evaluator.py` | `EdgeDecision` | Converts market candidate + observation + ENS into calibrated posterior, edge, confidence, and sizing. Snapshot metric stamping silently defaults to `high` if metric identity is missing. |
| Signal | `src/signal/ensemble_signal.py`, `src/signal/day0_signal.py`, `src/signal/diurnal.py` | P_raw / Day0 adjustment | Core ENS path uses settlement-aware semantics; Day0 weighting remains a known binary-switch quality gap. |
| Calibration | `src/calibration/manager.py`, `src/calibration/store.py`, `src/calibration/platt.py` | `P_cal`, maturity level | Legacy on-the-fly HIGH refit still reads metric-blind legacy pairs/counts; this is the highest-risk midstream data-use seam. |
| Market fusion / edge / Kelly | `src/strategy/market_fusion.py`, `src/strategy/kelly.py`, `src/contracts/decision_evidence.py` | posterior, edge, size | Typed atoms exist, but profit/objective-aware tail and entry/exit epistemic symmetry remain follow-up work. |
| Risk and execution | `src/riskguard/risk_level.py`, `src/riskguard/riskguard.py`, `src/execution/executor.py` | limit order intent / CLOB order | Risk levels are fail-closed; execution must remain limit-only and should not proceed through RED/YELLOW/ORANGE entry blocks. |
| Position truth / monitoring / settlement | `src/state/chain_reconciliation.py`, `src/state/lifecycle_manager.py`, `src/engine/monitor_refresh.py`, `src/execution/harvester.py` | lifecycle events, exits, settlement facts | Chain > event log > portfolio remains the law. Legacy missing `temperature_metric` falls back to `high` but is marked `UNVERIFIED`; strict consumers must enforce that authority tag. |

## Important findings affecting already-backfilled or replayed data

1. **P4 remains `NOT_READY`; do not run data population or canonical v2 mutation yet.**
   - Evidence: `docs/operations/current_state.md`, `docs/operations/task_2026-04-25_p4_readiness_checker/plan.md`.
   - Impact: `market_events_v2`, `settlements_v2`, `ensemble_snapshots_v2`, and `calibration_pairs_v2` remain blocked until operator/source/data evidence exists.

2. **TIGGE data can be downloaded but still not training-authoritative.**
   - Evidence: P4 packet requires parity/hash/source-time manifests and verified `issue_time` / `available_at`.
   - Impact: calibration rebuild must not treat TIGGE files as training-ready solely because they exist locally.

3. **HKO/Hong Kong settlement semantics remain a high-impact open gap.**
   - Evidence: `docs/operations/known_gaps.md` records HKO floor-containment evidence and early HK WU/VHHH source mismatch.
   - Impact: HK rows, especially 2026-03-13 and 2026-03-14, require explicit source/rounding review before use in settlement training.

4. **DST historical rebuild is still not live-certified.**
   - Evidence: `docs/operations/known_gaps.md` marks historical diurnal aggregate rebuild as open.
   - Impact: Day0 peak confidence and diurnal features for DST cities may be stale even if runtime clocks are DST-aware.

5. **Legacy calibration on-the-fly refit is metric-blind on read and count paths.**
   - Evidence: `src/calibration/manager.py` calls `get_decision_group_count(conn, cluster, season)` and `get_pairs_for_bucket(..., bin_source_filter="canonical_v1")`; `src/calibration/store.py` has no `temperature_metric` filter on those functions.
   - Impact: if legacy `calibration_pairs` contains both high and low canonical rows, HIGH on-the-fly refit can mix tracks and then save to metric-blind `platt_models`.

6. **Maturity gate and refit input use inconsistent subsets.**
   - Evidence: `get_decision_group_count()` counts all VERIFIED decision groups in a cluster/season, while `_fit_from_pairs()` reads only `canonical_v1` rows.
   - Impact: refit readiness can be over- or under-stated on mixed authority/bin-source backfills.

7. **Snapshot metric stamping silently defaults missing identity to `high`.**
   - Evidence: `src/engine/evaluator.py::_store_ens_snapshot()` uses a fallback of `"high"` when `ens.temperature_metric` is missing.
   - Impact: older or malformed LOW snapshot paths can be hidden as HIGH instead of failing closed.

8. **Legacy position metric fallback needs strict downstream authority filtering.**
   - Evidence: `src/state/chain_reconciliation.py::resolve_rescue_authority()` returns `("high", "UNVERIFIED", ...)` when `temperature_metric` is missing.
   - Impact: analytics or settlement consumers that ignore `authority != VERIFIED` can misclassify legacy LOW/missing rows as HIGH.

9. **RED force-exit sweep duplicates terminal lifecycle state names.**
   - Evidence: `src/engine/cycle_runner.py` mirrors terminal states from `src/state/portfolio.py` in a local frozenset.
   - Impact: if lifecycle terminal states evolve, sweep logic can drift and act on states that should be terminal.

10. **Harvester Stage-2 learning remains structured-skip capable, not fully canonical.**
    - Evidence: `docs/operations/known_gaps.md` records Stage-2 DB-shape preflight behavior and residual canonical-only gap.
    - Impact: settlement detection can run while calibration-pair creation remains skipped on incomplete DB substrate.

## Todo list

### P0 — block unsafe mutation/training

- [ ] Re-run P4 readiness in a dependency-complete environment and attach current JSON output before any P4 mutation.
- [ ] Produce or verify TIGGE parity/hash/source-time manifests for both high and low tracks before training.
- [ ] Refresh source validity for current provider status; do not rely on stale source facts for live source routing.
- [ ] Resolve HK/HKO settlement source and rounding policy through a settlement/governance packet before using HK rows in training.
- [ ] Keep `ZEUS_HARVESTER_LIVE_ENABLED` off until P4 blockers and Stage-2 canonical substrate are closed.

### P1 — code packets needed before calibration trust

- [ ] Add metric-aware filters to calibration pair retrieval/count APIs, then update `src/calibration/manager.py` so maturity gates and refit inputs use the same subset.
- [ ] Make missing `temperature_metric` on new ensemble snapshots fail closed or emit a hard readiness blocker instead of silently defaulting to `high`.
- [ ] Add data-version / causality filtering to any live or replay Platt refit path that can consume backfilled pairs.
- [ ] Replace duplicated terminal-state sets with one lifecycle-owned terminal predicate or shared exported constant.
- [ ] Add strict tests that `UNVERIFIED` rescue/legacy metric authority cannot feed settlement learning or training consumers.

### P2 — data repair / verification packets

- [ ] Audit existing `calibration_pairs` for mixed high/low, mixed `bin_source`, mixed `data_version`, and missing `decision_group_id` in any bucket that can be refit.
- [ ] Audit existing `ensemble_snapshots` for NULL/missing `temperature_metric` and decide whether to quarantine, backfill with proof, or exclude.
- [ ] Audit legacy `platt_models` for possible metric contamination before allowing legacy fallback to remain in live routing.
- [ ] Run the DST historical rebuild validation for NYC, Chicago, London, and Paris before certifying Day0 diurnal features.
- [ ] Verify WU/HKO/Ogimet daily observation backfills produce completeness manifests and revision records on payload drift.

### P3 — midstream trust improvements

- [ ] Replace Day0 binary observation dominance with a continuous weighting design after data trust gates close.
- [ ] Close entry/exit epistemic symmetry so production entry and exit consume the same evidence burden.
- [ ] Extend typed execution-price / tick-size / slippage contracts through the CLOB-send and realized-fill boundary.
- [ ] Add operator-visible alerting when calibration falls back from v2 to legacy models.

## Proposed next packet

Start with a narrow calibration-read hardening packet:

1. Scope only `src/calibration/store.py`, `src/calibration/manager.py`, and focused tests.
2. Add `temperature_metric`, `bin_source`, `data_version`, and causality-aware retrieval/count options without mutating DB data.
3. Fail closed or refuse on-the-fly refit if a required identity column is absent or mixed.
4. Validate with the calibration manager/store tests plus topology gates.

This is safer than starting with DB mutation because it prevents already-backfilled mixed data from becoming live model authority.

---

End of vendored snapshot.
