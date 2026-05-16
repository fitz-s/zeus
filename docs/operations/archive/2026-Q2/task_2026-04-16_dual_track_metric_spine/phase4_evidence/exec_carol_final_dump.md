# exec-carol Phase 4 Final Knowledge Dump

Author: exec-carol (sonnet, retiring after 2+ compacts)
Date: 2026-04-16
Phase: 4C (rebuild_calibration_pairs_v2) + 4D (refit_platt_v2)

---

## 1. Architectural Gotchas — Zeus Data Layer

### ensemble_snapshots_v2 has NO source column (27 columns, confirmed)
The legacy `ensemble_snapshots` table had a `source` column. `ensemble_snapshots_v2` does not.
`_fetch_eligible_snapshots_v2` must never SELECT `source` — that was CRITICAL-1.
INV-15 (training gate) compensates by checking `data_version` prefix instead:
`_resolve_training_allowed` in `src/calibration/store.py` checks if `data_version` starts with
`'tigge'` or `'ecmwf_ens'`. This is the *only* whitelist mechanism. Source is irrelevant.

### members_json contains null entries for missing ensemble members
exec-bob confirmed: when a member slot is missing, the JSON array contains `null`, not a
missing index. `np.asarray(json.loads(...), dtype=float)` will silently convert `null` → `nan`.
`validate_members_unit_plausible` in `src/contracts/calibration_bins.py` must be
robust to nan-containing arrays. Check this before Phase 5 uses member arrays.

### members_unit column is degC by default (but Atlanta F-unit cities use degF)
The `members_unit` column was added via idempotent ALTER TABLE in `v2_schema.py:160-168`.
Default is `'degC'`. For F-unit cities, the ingest script must write `'degF'` explicitly —
`validate_members_unit_plausible` reads city.settlement_unit and checks range plausibility.
If members_unit is wrong, the plausibility check fires. This is a silent data-quality trap.

### calibration_pairs_v2 UNIQUE key is 7 columns wide
`UNIQUE(city, target_date, temperature_metric, range_label, lead_days, forecast_available_at, bin_source, data_version)`.
The `bin_source` field distinguishes v2 rebuild rows (`'canonical_v2'`) from legacy rows.
The delete-then-reinsert rebuild pattern keys the DELETE on `bin_source='canonical_v2'` only —
legacy rows in the same table are never touched. This is correct and intentional.

### platt_models_v2 model_key is PRIMARY KEY + UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)
The UNIQUE constraint includes `is_active`. This means a soft-deactivate (UPDATE is_active=0)
still leaves the old row in place, and a new INSERT with is_active=1 will succeed — but only
once. The second refit would fail: the old soft-deactivated row still occupies the
(metric, cluster, season, dv, input_space, is_active=0) slot. The clean pattern is DELETE
the old row first, then INSERT the new one. `deactivate_model_v2` in store.py:396 does this.

### decision_group_id requires timezone-aware issue_time
`compute_id` in `src/calibration/decision_group.py` raises `TypeError` if `issue_time` is a
naive datetime or an ISO string without UTC offset. All test fixtures must use `'Z'` suffix
or `+00:00`. Bare `'2025-06-13T00:00:00'` fails at hash time.

### SettlementSemantics.assert_settlement_value does range validation
`SettlementSemantics.for_city(city)` followed by `.assert_settlement_value(obs_temp)` raises
if the temperature is outside plausible range. This was added as a provenance guard.
For test fixtures, use temperatures in the 70-85°F range for US F-unit cities.

### cities_by_name is the single station truth
`src/config.py:cities_by_name` is built from `cities.json`. Any city name in
`ensemble_snapshots_v2.city` that doesn't match `cities_by_name` causes the snapshot to be
skipped in rebuild. With hard-failure policy, a nonzero missing_city_count aborts the
entire SAVEPOINT. This means cities.json drift is now a hard blocker, not a soft skip.

---

## 2. Phase 5 Forward Risks — Low Historical Lane

### low_temp observation field symmetry is not guaranteed
`_fetch_verified_observation` in rebuild selects `high_temp`. The Phase 5 low-track analog
must select `low_temp` instead. But the `observations` table has separate `low_temp` and
`low_raw_value` / `low_raw_unit` columns. Verify that historical backfill scripts actually
populate `low_temp` (not just `high_temp`) for all cities before running the low rebuild.
Gaps will show as `snapshots_no_observation` hits — check ratio against the 30% abort gate.

### Physical quantity for low is mn2t6_local_calendar_day_min, not mx2t6_...
`LOW_LOCALDAY_MIN` in `src/types/metric_identity.py` must have `observation_field='low_temp'`
and `data_version='tigge_mn2t6_local_calendar_day_min_v1'` (the exact tag). The refit script
for Phase 5 must read `temperature_metric='low'` from `calibration_pairs_v2` — not 'high'.
Any copy-paste from refit_platt_v2.py that forgets to change the metric filter will silently
fit a high-track model from low-track data.

### Quarantine resonance: if the low data_version tag gets quarantined, everything is blocked
`QUARANTINED_DATA_VERSIONS` in `src/contracts/ensemble_snapshot_provenance.py:80` is a
frozenset exact-match check. Adding the wrong tag or a typo blocks the entire low rebuild.
Verify the exact data_version string in `LOW_LOCALDAY_MIN` matches what the ingest writes.

### Low-track station identity may differ by city
Some cities have separate high/low observation sources (WU for highs, NOAA for lows).
The `observations` query selects `ORDER BY source DESC LIMIT 1` — this is arbitrary when
multiple sources exist. Phase 5 may need explicit source preference for low_temp.

---

## 3. Patterns a Fresh Agent Must Know

### Dry-run scaffolding in 4C/4D
Both rebuild_calibration_pairs_v2.py and refit_platt_v2.py use `--dry-run` as the default.
Live write requires `--no-dry-run --force` together. A single `--no-dry-run` without `--force`
raises RuntimeError. This is intentional — two separate flags prevent accidental live runs.

### SAVEPOINT rollback pattern
Both scripts use `conn.execute("SAVEPOINT <name>")` / `RELEASE` / `ROLLBACK TO SAVEPOINT`.
On any exception (including per-bucket failures), the SAVEPOINT is rolled back and released
before re-raising. `conn.commit()` is called after `RELEASE SAVEPOINT` only in the success path.
Do not use `BEGIN`/`COMMIT` directly inside these scripts — the connection may already be in a
transaction when called from tests.

### Wiring a new script into the observation→calibration→Platt chain
1. Write eligibility query against `ensemble_snapshots_v2` (filter temperature_metric, training_allowed, causality_status, authority).
2. Call `assert_data_version_allowed` on every snapshot's data_version before processing.
3. Match to `observations` via `(city, target_date, authority='VERIFIED')`.
4. Call `validate_members_unit_plausible`, `SettlementSemantics.assert_settlement_value`, `validate_members_vs_observation`.
5. Compute p_raw via `p_raw_vector_from_maxes`.
6. Call `add_calibration_pair_v2(metric_identity=<IDENTITY>, ...)` — never add_calibration_pair (legacy).
7. For refit: call `deactivate_model_v2` (DELETE) then `save_platt_model_v2` per bucket.

### MIN_DECISION_GROUPS wiring
Always use `calibration_maturity_thresholds()[2]` (level3). Never hardcode 15. The thresholds
are configurable and a hardcoded value silently drifts.

---

## 4. Anti-Patterns Narrowly Avoided

### Phantom-work disk-verify
After compaction, in-flight edits can appear complete in the session but never reach disk.
The only oracle is `git status --short` + grep for a known string. Always verify every edit
is on disk before reporting completion. This protocol was instituted after a phantom-work
incident that wasted a full review cycle.

### bucket_key city/date pollution
The first impl of `platt_models_v2` included `city TEXT` and `target_date TEXT` columns.
Critic-alice caught this as M3 (Phase 2). Platt models are bucket-keyed:
`(temperature_metric, cluster, season, data_version, input_space)` — no city, no date.
Adding city/date silently accumulates NULLs in production and breaks the bucket semantics.

### Stale CITY_STATIONS registry divergence
Phase 3 scope: `daily_obs_append.py` previously maintained a local CITY_STATIONS map
parallel to cities.json. These can drift. cities.json is the single source of truth.
Any local parallel map is an anti-pattern.

### deactivate_model_v2 DELETE-not-UPDATE surprise
The first implementation used `UPDATE platt_models_v2 SET is_active=0`. This leaves the
old row in place. When the new INSERT runs with is_active=1, the UNIQUE constraint on
`(temperature_metric, cluster, season, data_version, input_space, is_active)` blocks it
on the *second* refit because the is_active=0 slot is still occupied. Test
`test_refit_twice_leaves_exactly_one_active_row` caught this. Fix: DELETE the old row first.

---

## 5. File Map for exec-carol Successor

| File | Lines | What's There |
|---|---|---|
| `scripts/rebuild_calibration_pairs_v2.py` | 112-136 | `_fetch_eligible_snapshots_v2` — the SELECT that CRITICAL-1 lived in |
| `scripts/rebuild_calibration_pairs_v2.py` | 172-273 | `_process_snapshot_v2` — snapshot→pairs pipeline including `source=""` and decision_group_id |
| `scripts/rebuild_calibration_pairs_v2.py` | 366-379 | Hard-failure policy comment + check |
| `scripts/refit_platt_v2.py` | 60 | `MIN_DECISION_GROUPS` wired to `calibration_maturity_thresholds()[2]` |
| `scripts/refit_platt_v2.py` | 81-95 | `_fetch_buckets` — the bucket query against calibration_pairs_v2 |
| `scripts/refit_platt_v2.py` | 117-203 | `_fit_bucket` — per-bucket fit, deactivate→save pattern |
| `src/calibration/store.py` | 126 | `add_calibration_pair_v2` — requires metric_identity, enforces INV-15 |
| `src/calibration/store.py` | 350 | `save_platt_model_v2` — plain INSERT, requires metric_identity |
| `src/calibration/store.py` | 396 | `deactivate_model_v2` — DELETE (not UPDATE), with audit-trail docstring |
| `src/calibration/store.py` | ~440 | `_resolve_training_allowed` — INV-15 whitelist check on data_version prefix |
| `src/contracts/ensemble_snapshot_provenance.py` | 80 | `QUARANTINED_DATA_VERSIONS` frozenset — exact-match quarantine |
| `src/contracts/calibration_bins.py` | — | `validate_members_unit_plausible`, `validate_members_vs_observation`, `grid_for_city` |
| `src/contracts/settlement_semantics.py` | — | `SettlementSemantics.for_city`, `assert_settlement_value` |
| `src/calibration/decision_group.py` | 140 | `compute_id` — requires tz-aware issue_time |
| `src/types/metric_identity.py` | — | `HIGH_LOCALDAY_MAX`, `LOW_LOCALDAY_MIN` — the two track identities |
| `src/state/schema/v2_schema.py` | 112-168 | `ensemble_snapshots_v2` DDL (27 cols, no source, members_unit added at 160) |
| `src/state/schema/v2_schema.py` | 174-214 | `calibration_pairs_v2` DDL + UNIQUE key |
| `src/state/schema/v2_schema.py` | 219-248 | `platt_models_v2` DDL + UNIQUE on (metric, cluster, season, dv, input_space, is_active) |
| `src/config.py` | 62-96 | `City` dataclass — settlement_unit, cluster, lat, lon |
| `src/config.py` | 242 | `cities_by_name` dict — single station truth |
| `tests/test_phase4_rebuild.py` | 295 | `TestRebuildV2PipelineIntegration` — end-to-end antibody for CRITICAL-1 |
| `tests/test_phase4_platt_v2.py` | — | 8 tests covering platt_models_v2 isolation + double-refit contract |
