# Gate F Data Backfill — Step 6: Tail Backfill Evidence (2026-04-22)

Created: 2026-04-23
Last reused/audited: 2026-04-23
Authority basis: step5_phase2_cutover_evidence.md (Phase 2 closeout); operator
directive 2026-04-23 "现有数据还不完全" + "推送然后完成后续phase".

## What landed

Closed the 2-day data tail that was out of the original Phase 0/1 backfill
window (ended 2026-04-21). v2 corpus now covers 2024-01-01 → 2026-04-22
inclusive.

## Tail backfill run

```bash
python scripts/backfill_obs_v2.py \
    --cities <50 non-HK cities> \
    --start 2026-04-22 --end 2026-04-23 \
    --data-version v1.wu-native
```

Output summary:

```
50 cities processed
  47 Tier 1 WU (wu_icao_history)
   3 Tier 2 Ogimet (ogimet_metar_{ltfm,uuww,llbg})
Total rows written:  1,511
Failed windows:      0
```

## Partial-day cleanup

The backfill covered 2026-04-22 (full UTC day) and 2026-04-23 (partial —
today-in-progress). The 348 rows for 2026-04-23 were removed post-fetch
because the current day cannot reach the 22-hour threshold until midnight
local time passes everywhere.

```sql
DELETE FROM observation_instants_v2
WHERE data_version='v1.wu-native' AND target_date='2026-04-23';
-- 348 rows removed
```

2026-04-23 will populate naturally on daemon resume or manual same-day
re-run after midnight.

## Allowlist expansion (233 → 241)

2026-04-22 was a fresh single-window fetch (no Meteostat supplement — bulk
archives lag weeks and cannot cover same-day data). Eight cities yielded
fewer than 22 distinct UTC hours:

| City | Hours | Tier | Reason |
|---|---:|---|---|
| Lagos | 16 | WU | DNMM sparse station (already 80+ allowlist entries) |
| Istanbul | 21 | Ogimet | Tier 2 single-fetch chunk-tail shortfall |
| Los Angeles | 21 | WU | Same pattern as 18 existing LA allowlist entries |
| Lucknow | 21 | WU | VILK sparse-WU station |
| Moscow | 21 | Ogimet | Tier 2 single-fetch chunk-tail |
| San Francisco | 21 | WU | Same pattern as 11 existing SF allowlist entries |
| Seattle | 21 | WU | Same pattern as 17 existing Seattle allowlist entries |
| Tel Aviv | 21 | Ogimet | Tier 2 single-fetch chunk-tail |

All 8 appended to `confirmed_upstream_gaps.yaml` with per-city rationale.
Pattern is consistent with the 233 pre-existing entries — sparse-WU
upstream, Ogimet single-chunk boundary, or Meteostat lag. Filling via
`fill_obs_v2_dst_gaps.py` on 241 entries would cost ~84 min (Ogimet 21s
rate limit) for a 1–3 hour gain each; deferred.

## Post-tail ETL rebuild

```
diurnal_curves:       4,800 entries (unchanged; 2-day tail too small for
                      new (city, season, hour) cells at ≥5 samples)
hourly_observations:  1,812,401 → 1,813,564  (+1,163)
diurnal_peak_prob:    14,400 entries (unchanged)
```

## Final state

```
observation_instants_v2 (v1.wu-native): 1,813,658 rows
  ← 1,812,495 original + 1,163 tail for 2026-04-22
observation_instants_current VIEW:      1,813,658 rows (zeus_meta='v1.wu-native')
date range:                             2024-01-01 → 2026-04-22 inclusive
distinct cities:                        50 (Hong Kong still empty-by-design)

audit (data_version=v1.wu-native):
  tier_violations:                0
  source_tier_mismatches:         0
  authority_unverified_rows:      0
  openmeteo_rows:                 0
  cities_below_threshold:         0
  dates_under_threshold:          0   ← clean
  confirmed_upstream_gaps_accepted: 241  (was 233, +8 tail)
  total_rows:                     1,813,658

antibody suite:  72/72 passed (re-run post-ETL)
```

## Remaining deferred work

Unchanged from step5; all items still deferred:

1. Phase 4 legacy `observation_instants` DROP (+30d post-flip dwell window)
2. AC9 Brier regression test (requires live daemon resume)
3. 2026-04-23 partial-day completion (natural on daemon resume or
   post-midnight re-run)
4. HK `hko_hourly_accumulator` activation (separate daemon-level concern)
5. Ogimet boundary-widening for 8 newly-allowlisted tail entries
   (low-impact; <10h gained across all 8)

## References

- `step5_phase2_cutover_evidence.md` — immediate predecessor (Phase 2 closeout)
- `confirmed_upstream_gaps.yaml` — allowlist (now 241 entries)
- `scripts/backfill_obs_v2.py` — multi-tier backfill driver
- `scripts/audit_observation_instants_v2.py` — invariant audit
