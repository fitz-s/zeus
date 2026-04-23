# Gate F Data Backfill — Step 5: Phase 2 Cutover Evidence

Created: 2026-04-23
Last reused/audited: 2026-04-23
Authority basis: step4_phase2_cutover.md (this packet's Phase 2 plan); operator
authorization 2026-04-23 ("走路径 A，现有数据还不完全，没有在 live 运行中").

## What landed

Phase 2 atomic cutover + Phase 3 ETL rebuild executed in the same session.
Daemon not live → no runtime risk, no Brier regression measurement possible
(deferred until daemon resume). Evidence captured here so the DB-side changes
(which are not git-tracked) have audit trail.

## Commit chain

| Step | Commit | Scope |
|---|---|---|
| Docs (PW0) | `a6d3711` | step3 Phase 1 closeout + step4 Phase 2 plan + current_data_state pre-flip refresh |
| PW1 pilot cleanup | (DB-only) | `DELETE FROM observation_instants_v2 WHERE data_version='v1.wu-native.pilot'` — 93 rows removed |
| PW2 ETL migration | `6d33d30` | `etl_diurnal_curves.py` + `etl_hourly_observations.py` switch to `observation_instants_current` with `COALESCE(temp_current, running_max)` |
| PW3 AC11 test | `baf9ad0` | `tests/test_diurnal_curves_empty_hk_handled.py` + `test_topology.yaml` registration |
| PW5 atomic flip | (DB-only) | `UPDATE zeus_meta SET value='v1.wu-native' WHERE key='observation_data_version'` — single statement, single transaction |
| PW6 ETL rebuild | (DB-only) | `python scripts/etl_diurnal_curves.py` + `python scripts/etl_hourly_observations.py` re-ran against the post-flip VIEW |
| Closeout (this doc) | (pending) | Step 5 evidence + current_data_state post-flip refresh |

## Pre-flip gate evidence

Captured immediately before `UPDATE zeus_meta`:

```
antibody suite:    72/72 passed (tier_resolver + obs_v2_writer + hk_rejects_vhhh_source
                                 + backfill_scripts_match_live_config + diurnal_curves_empty_hk_handled)
zeus_meta:         observation_data_version = 'v0'
observation_instants_current row count: 0
observation_instants_v2 row count (v1.wu-native): 1,812,495
observation_instants_v2 row count (v1.wu-native.pilot): 0   ← pilot cleaned
audit (data_version=v1.wu-native):
  tier_violations:                0
  source_tier_mismatches:         0
  authority_unverified_rows:      0
  openmeteo_rows:                 0
  cities_below_threshold:         0
  dates_under_threshold:          0
  confirmed_upstream_gaps_accepted: 233  (evidence-backed allowlist)
  total_rows:                     1,812,495
```

## Flip transaction

```sql
BEGIN;
UPDATE zeus_meta SET value='v1.wu-native' WHERE key='observation_data_version';
COMMIT;
```

Timestamp: 2026-04-23 (same session as this doc).

Pre/post snapshot (single transaction):

```
pre_flip_meta       = 'v0'
pre_flip_view_rows  = 0
--- flip ---
post_flip_meta      = 'v1.wu-native'
post_flip_view_rows = 1,812,495
post_flip_view_cities = 50
```

## Phase 3 ETL rebuild output

### `scripts/etl_diurnal_curves.py`

```
Source: 1,812,495 observation_instants_current
Using 1,812,404 non-missing, non-ambiguous observation_instants_current rows for diurnal aggregation

Verification - NYC DJF peak hours:
  Hour 14: avg_temp=38.4
  Hour 15: avg_temp=38.3
  Hour 13: avg_temp=38.1

Stored 4800 diurnal curve entries and 14400 monthly probability rows

Done: {'stored': 4800, 'monthly_rows': 14400}
```

NYC DJF peak at hour 14 @ 38.4°F is domain-plausible (mid-afternoon winter peak
in the eastern US).

### `scripts/etl_hourly_observations.py`

```
hourly_observations has 866601 existing rows. Rebuilding from observation_instants_current...
Source rows: 1,812,495
Canonical rows: 1,812,401, Rejected: 3, Collapsed ambiguous duplicates: 0, Excluded ambiguous rows: 91

Done: {'imported': 1812401, 'rejected': 3, 'collapsed_ambiguous': 0, 'excluded_ambiguous': 91}
```

91 DST-ambiguous excluded + 3 temperature-range rejected = 94 of 1,812,495
(0.005%) dropped cleanly.

## Post-flip gate evidence

```
derived table row counts:
  diurnal_curves:       4,800       (pre: 4,416)
  hourly_observations:  1,812,401   (pre: 866,601)
  diurnal_peak_prob:    14,400      (pre: 13,224)

distinct city count (post-rebuild):
  diurnal_curves:       50
  hourly_observations:  50
  HK diurnal_curves:    0   (accepted gap, AC11 condition active)

antibody suite (re-run):  72/72 passed

audit (data_version=v1.wu-native):  all counters 0 except accepted 233 gaps
```

### Live signal smoke test

```python
from src.signal.diurnal import get_peak_hour_context, post_peak_confidence

# HK: empty-diurnal graceful fallback (AC11 live-DB condition)
get_peak_hour_context('Hong Kong', date(2026, 4, 23), 14)
  → (None, 0.0, 'insufficient_diurnal_data_rows')
post_peak_confidence('Hong Kong', date(2026, 4, 23), 14)  → 0.0

# Non-HK cities: monthly_empirical (highest-resolution path) succeeds
get_peak_hour_context('Chicago', date(2026, 4, 23), 15)   → (15, 0.370, 'monthly_empirical')
get_peak_hour_context('London',  date(2026, 7, 15), 14)   → (16, 0.323, 'monthly_empirical')
get_peak_hour_context('Tokyo',   date(2026, 4, 23), 14)   → (14, 0.519, 'monthly_empirical')
```

Signal layer confirmed shape-correct post-flip. HK still NaN-free through
graceful fallback; all 50 populated cities reach the monthly-empirical path
because `diurnal_peak_prob` is dense (50 × 12 × 24 = 14,400 cells).

## Acceptance criteria (step4 matrix)

| AC | Pass | Actual |
|---|---|---|
| AC6 zeus_meta flipped | ✅ | `v1.wu-native` |
| VIEW returns 1.8M rows | ✅ | 1,812,495 |
| Pilot cleaned | ✅ | 0 rows |
| READER-DIURNAL | ✅ | no `FROM observation_instants` |
| READER-HOURLY | ✅ | no `FROM observation_instants` |
| AC11 antibody | ✅ | 4/4 tests pass, live smoke pass |
| Antibody suite | ✅ | 72/72 |
| REBUILD-DIURNAL | ✅ | 4,800 (> 0 and < 10,000) |
| REBUILD-HOURLY | ✅ | 1,812,401 (≥ 1,800,000) |

## Rollback readiness (still valid)

Single-SQL atomic rollback, <1s:

```sql
UPDATE zeus_meta SET value='v0' WHERE key='observation_data_version';
-- then re-run ETL which will fail-closed on empty VIEW; populate diurnal_curves
-- and hourly_observations by re-running against legacy observation_instants
-- via local WIP revert on scripts/etl_*.py (or use git revert PW2 commit).
```

Full-session rollback (all phases): `git reset --hard 4e99a51` + SQL rollback
+ ETL rebuild against legacy openmeteo_archive_hourly ≈ 5 min wall-clock.

## What is NOT yet done (deferred)

1. **Phase 4 legacy deprecation** (plan v3 L110–113): +30-day post-cutover DROP
   of `observation_instants` (the 867,489 openmeteo legacy rows). Separate
   packet when the dwell window elapses with zero reads from the legacy table.
2. **AC9 Brier regression test**: plan v3 L166 requires Platt Brier score ≤ v1
   baseline ± 10% on 30-day window. Daemon not live → no calibration path to
   measure. Carry into the live-resume gate.
3. **Tail backfill**: 2026-04-22 and 2026-04-23 are not in v2 yet (backfill
   window was 2024-01-01 → 2026-04-21 inclusive). Low-priority while offline;
   will pick up naturally when WU daemon resumes.
4. **HK accumulator activation**: HKO daemon path is a separate concern. HK
   will remain empty in `diurnal_curves` until hko_hourly_accumulator starts
   writing forward. AC11 keeps the signal layer safe during that window.
5. **current_data_state.md post-flip refresh**: folded into the closeout commit
   that carries this file.

## References

- `.omc/plans/observation-instants-migration-iter3.md` — plan v3 (master plan)
- `docs/operations/task_2026-04-21_gate_f_data_backfill/step4_phase2_cutover.md` — this step's predecessor (the plan)
- `docs/operations/task_2026-04-21_gate_f_data_backfill/confirmed_upstream_gaps.yaml` — 233 allowlisted gaps
- `scripts/audit_observation_instants_v2.py` — nightly invariant audit
