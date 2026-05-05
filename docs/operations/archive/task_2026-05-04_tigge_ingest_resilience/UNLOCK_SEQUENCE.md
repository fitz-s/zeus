# Unlock Sequence — End-to-End Order of Operations

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04 (post-merge — superseded by REALIGNMENT)
**Purpose:** Operator-facing single-page summary of every action that must happen between the 2026-05-04 lock and the live-trading unlock. Cross-reference for `LIVE_TRADING_LOCKED_2026-05-04.md`.

> **Status (2026-05-04 post-merge):** Phases 1, 2, 2.6, and 3 of this
> sequence landed on main via PR #55.  Phase 2.5 (calibration transfer
> policy) and Phase 2.75 (robust Kelly) were replaced by PR #56's
> `MarketPhaseEvidence` + `oracle_evidence_status` +
> `phase_aware_kelly_live` stack.  Read
> `POST_PR55_PR56_REALIGNMENT.md` for the current
> what-landed / what-still-pending split.

---

## Sequencing rationale

Order is non-arbitrary. Each phase depends on the previous:

```
Phase 1 (12z code + 90d data) → Phase 2 (Platt cycle-stratified refit) → Phase 3 (routing fix) → Unlock
                                                                                                    │
                                                                                                    ▼
                                                                                            Phase 4 (post-unlock)
                                                                                            full historical 12z backfill
                                                                                            (background, non-blocking)
```

Reordering breaks math:
- Routing fix without Platt → 12z forecasts get 00z-trained calibration → miscalibrated trades
- Platt refit without 12z data → still cycle-blind (no 12z pairs to train on)
- 90-day backfill without 12z code → no way to fetch 12z

## Phase 1 — TIGGE 12z support + 90d backfill

**Owner:** Sonnet executor agent (background, branch `tigge-12z-support-2026-05-04`)
**Status as of 2026-05-04:** in progress

Code:
- [ ] `51 source data/scripts/tigge_mx2t6_download_resumable.py` — add `--cycle {00,12}`
- [ ] `51 source data/scripts/tigge_mn2t6_download_resumable.py` — add `--cycle {00,12}`
- [ ] `51 source data/scripts/extract_tigge_mx2t6_localday_max.py` — cycle-aware step selection
- [ ] `51 source data/scripts/extract_tigge_mn2t6_localday_min.py` — cycle-aware step selection
- [ ] `src/data/tigge_pipeline.py` — orchestrate per-cycle
- [ ] `scripts/ingest_grib_to_snapshots.py` — verify issue_time reflects cycle
- [ ] `src/ingest_main.py` — coverage-aware boot freshness guard (mirroring PR #42)
- [ ] `src/ingest_main.py` — per-track isolation in catch-up loop
- [ ] `src/data/tigge_pipeline.py` — busy_timeout=600000 + per-snapshot SAVEPOINT in `_ingest_track`

Tests:
- [ ] `tests/test_tigge_dual_cycle_support.py` (4)
- [ ] `tests/test_tigge_per_track_isolation.py` (2)
- [ ] `tests/test_tigge_pipeline_lock_handling.py` (2)
- [ ] `tests/test_tigge_freshness_guard.py` (4)

Data:
- [ ] 90-day 12z backfill: issue_dates 2026-02-01 → 2026-05-02 × {mx2t6, mn2t6}
- [ ] Verify: `SELECT cycle, COUNT(*) FROM ensemble_snapshots_v2 WHERE data_version LIKE 'tigge_%' GROUP BY cycle;` shows non-zero 12z

## Phase 2 — Platt cycle stratification + retrain

**Owner:** Subsequent sonnet task (after Phase 1 merge)
**Doc:** `DESIGN_PHASE2_PLATT_CYCLE_STRATIFICATION.md`

Code:
- [ ] Migration 1: ALTER `platt_models_v2` ADD COLUMN `cycle TEXT NOT NULL DEFAULT '00'`
- [ ] Migration 2: ALTER `calibration_pairs_v2` ADD COLUMN `cycle TEXT NOT NULL DEFAULT '00'`
- [ ] Migration 2: backfill `cycle` from `snapshot_id → ensemble_snapshots_v2.issue_time`
- [ ] `scripts/refit_platt_v2.py` — group by `(metric, city, cycle, season, data_version, input_space)`
- [ ] `src/calibration/manager.py:get_calibrator` — accept `cycle` param
- [ ] `src/engine/evaluator.py` — derive cycle from forecast issue_time, pass to get_calibrator

Tests:
- [ ] `test_platt_v2_schema_has_cycle_column`
- [ ] `test_calibration_pairs_v2_schema_has_cycle_column`
- [ ] `test_legacy_rows_default_to_cycle_00z`
- [ ] `test_refit_groups_by_cycle_with_dual_cycle_pairs`
- [ ] `test_refit_produces_distinct_models_per_cycle`
- [ ] `test_evaluator_routes_00z_forecast_to_00z_bucket`
- [ ] `test_evaluator_routes_12z_forecast_to_12z_bucket`
- [ ] `test_evaluator_does_not_fall_back_across_cycles`

Action:
- [ ] Run `python scripts/refit_platt_v2.py --no-dry-run --force`
- [ ] Verify: `SELECT cycle, COUNT(*) FROM platt_models_v2 WHERE is_active=1 GROUP BY cycle;` has rows for both '00' and '12'
- [ ] Verify: parameter divergence between cycles for at least 80% of (city, metric, season) triples
- [ ] Verify: in-sample Brier score for cycle-stratified ≤ Brier of legacy combined-cycle fit

## Phase 3 — Live entry routing fix (#136)

**Owner:** Final sonnet task before unlock
**Doc:** `DESIGN_PHASE3_LIVE_ROUTING_FIX.md`

Code:
- [ ] `src/data/forecast_source_registry.py` — add `ecmwf_open_data` profile with `entry_primary` role
- [ ] Locate + change `ENSEMBLE_MODEL_SOURCE_MAP[ecmwf_ifs025]` to `"ecmwf_open_data"`
- [ ] `src/engine/evaluator.py` — verify forecast source resolution downstream

Tests:
- [ ] `test_ensemble_model_source_map_for_ifs025`
- [ ] `test_ecmwf_open_data_authorized_for_entry_primary`
- [ ] `test_openmeteo_remains_unauthorized_for_entry_primary`

## Phase 3.5 — Stale readiness purge

- [ ] `DELETE FROM readiness_state WHERE status='BLOCKED' AND strategy_key='entry_forecast'`
   - Authority basis: writer-flag-OFF since PR #54; rows are ghost-state
   - 100 rows expected (verified 2026-05-04)
- [ ] Verify post-purge: `SELECT status, COUNT(*) FROM readiness_state GROUP BY status;` shows BLOCKED=0

## Phase 4 — Critic-opus comprehensive review

**Owner:** critic-opus subagent (Opus tier)
**Authority basis:** Operator directive 2026-05-04 — review must cover data accuracy/quality, time semantics ("最重要每次都搞错"), data daemon, multi-source consistency. NOT just diff.

Brief scope:
- [ ] **Time correctness**: every UTC vs local conversion in tigge_pipeline, extract scripts, evaluator. ECMWF embargo timing. Cycle vs target-day semantics.
- [ ] **Data accuracy**: cycle stratification math; member identity assumptions; window aggregation step indices for 00z/12z.
- [ ] **Data daemon end-to-end**: ingest_main scheduler topology; freshness guard pattern; per-track isolation; lock-handling.
- [ ] **Multi-source consistency**: TIGGE vs ECMWF Open Data alignment claim; openmeteo authority stripping; provenance propagation through ensemble_snapshots_v2 → calibration_pairs_v2 → platt_models_v2 → opportunity_fact.
- [ ] **Math architecture**: Platt cycle stratification correctness; level=4 maturity threshold appropriateness; spread/CI inflation risk if cycles mixed.

## Unlock — operator-only

**Final go/no-go.** All boxes above must be checked AND critic-opus approval explicit.

```bash
# Step 1: lift control-plane lock
python -c "
from src.control.control_plane import resume_entries  # verify exact API
resume_entries(
    reason_code='UNLOCK_2026_MM_DD_TIGGE_12Z_PLATT_CYCLE_STRATIFIED',
    issued_by='operator_<your_name>',
)
"

# Step 2: restore plist + bootstrap launchd
mv ~/Library/LaunchAgents/com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak \
   ~/Library/LaunchAgents/com.zeus.live-trading.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zeus.live-trading.plist
launchctl list | grep com.zeus.live-trading

# Step 3: smoke test (wait one opening_hunt cycle, ~15 min)
sqlite3 state/zeus_trades.db "SELECT COUNT(*) FROM venue_order_facts WHERE recorded_at > datetime('now','-1 hour');"
# Expected: > 0 within an hour

# Step 4: log unlock
git commit --allow-empty -m "unlock(live): 2026-MM-DD operator-authorized — TIGGE 12z + Platt cycle-stratified"
```

## Phase 5 — Background full backfill (post-unlock)

After unlock confirmed, schedule background catch-up of 17-month historical 12z:

- [ ] Issue dates: 2024-01-01 → 2026-02-01 (the pre-90d period)
- [ ] ~50h MARS download time at 4min/call
- [ ] Run as a separate, lower-priority job; rate-limit MARS calls
- [ ] When done: re-run `refit_platt_v2.py` to incorporate full data
- [ ] Compare new fits to current; document any material parameter shifts

---

## Time-correctness checklist (operator-printable)

The "every-time-wrong" failure mode (operator quote 2026-05-04). Before unlocking, verify each of these explicitly:

| Check | Verified? |
|---|---|
| `ecmwf_open_data` cycle 00z release time = ~07:00 UTC (per ECMWF docs) — our 07:30 UTC ingest leaves margin | [ ] |
| `ecmwf_open_data` cycle 12z release time = ~19:00 UTC — our 19:30 UTC ingest leaves margin | [ ] |
| TIGGE archive embargo = 48h **from model run time** (not from now, not from issue date) | [ ] |
| `_tigge_archive_backfill_cycle` cron 14:00 CDT = 19:00 UTC; today−2d issue date 00z lifted at today 00:01 UTC, 12z lifted at today 12:01 UTC — both retrievable by 19:00 UTC ✓ | [ ] |
| `extract_tigge_mx2t6_localday_max.py` 00z target-UTC-day uses steps {6,12,18,24}; 12z uses steps {12,18,24,30} | [ ] |
| `local_day_start_utc` correctly offsets for non-UTC city timezones (e.g., London BST = UTC+1, target-day window is 23:00 UTC prev day → 23:00 UTC current day) | [ ] |
| Platt fit `season` boundary uses local-time, not UTC (DJF = local-time December-January-February) | [ ] |
| `opportunity_fact.target_date` is the local calendar day in the city's timezone, not UTC | [ ] |
| `readiness_state.recorded_at` interpreted with timezone (the SQL string-comparison bug from earlier this session — `'T' > ' '` lexicographic — must not recur) | [ ] |
| Settlement times match Polymarket UMA resolution timestamps (10:00 UTC mythology, 20h vacuum framing per recent strategy doc) | [ ] |
