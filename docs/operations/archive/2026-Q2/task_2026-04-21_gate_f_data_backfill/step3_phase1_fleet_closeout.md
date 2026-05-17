# Gate F Data Backfill — Step 3: Phase 1 Fleet Closeout (retroactive documentation)

Created: 2026-04-23
Last reused/audited: 2026-04-23
Authority basis: `.omc/plans/observation-instants-migration-iter3.md` Phase 1; step2_phase0_pilot_plan.md predecessor; commit series `183404f…4e99a51`.

## Scope

Retroactive documentation of Phase 1 fleet backfill execution (2026-04-21 → 2026-04-22). Records the actual 50-city backfill posture committed at `4e99a51`, closing the gap between the pilot-scope packet (step 2) and the Phase 2 cutover packet (step 4). Phase 1 work landed outside the original packet scope; this document brings it under audit.

## Execution summary

| Phase slice | Commit | Notes |
|---|---|---|
| Phase 0 pilot (5 cities) | `4d542a7` → `92c6ea8` | Chicago / London / Tokyo / Sao Paulo / Moscow under `data_version='v1.wu-native.pilot'` (later re-run under `v1.wu-native`) |
| Critic REJECT fixes (C1/C2/C3/M1/M6) | `183404f`, `4d542a7`, `92c6ea8` | Extremum-preserving aggregation; 60-day chunk overlap; per-day 22–25 hour gate; time_basis `utc_hour_bucket_extremum`; confirmed-upstream-gaps allowlist |
| Meteostat bulk-CSV client | `575f435` | 12 h Ogimet serial → ~2 min parallel CDN; 46 stations via WMO map |
| Phase 0→1 Meteostat residual fill + final audit | `4e99a51` | Full fleet READY; 50 cities × 2024-01-01 → 2026-04-21 |

## Final Phase 1 posture (as of `4e99a51`)

```
observation_instants_v2 (data_version='v1.wu-native') = 1,812,495 rows
  cities: 50 distinct
  sources (by row count):
    wu_icao_history       931,677 rows  (47 cities primary)
    meteostat_bulk_*      815,585 rows  (46 cities; extremum preservation)
    ogimet_metar_*         65,233 rows  (3 primary: Istanbul/Moscow/Tel Aviv + DST/gap supplements)
    hko_hourly_accumulator      0 rows  (accepted gap; accumulator-forward-only from daemon start)

observation_instants_v2 (data_version='v1.wu-native.pilot') = 93 rows
  residual pilot data: Chicago 46 + London 46 + Sao Paulo 1
  disposition: cleanup in step4 pre-flip (pilot rows are superseded by v1.wu-native)

audit flags (scripts/audit_observation_instants_v2.py --json):
  tier_violations:              0
  source_tier_mismatches:       0
  authority_unverified_rows:    0
  openmeteo_rows:               0
  cities_below_threshold:       0
  dates_under_threshold:        0
  confirmed_upstream_gaps:    233 (allowlist, all evidence-backed)
```

## Key differences from plan v3 Phase 1 description

1. **Meteostat bulk added** as supplemental per-station layer (not in original plan v3). Replaces 12 h Ogimet serial fill with 2 min parallel CDN. Source tag `meteostat_bulk_<icao>`. Provenance JSON: `tier=METEOSTAT_BULK_FALLBACK`, `fallback_reason=sparse_wu_plus_slow_ogimet`. Per-city allowed-sources set extended to include this tag.

2. **Ogimet narrower role**: Reduced from "per-city primary for 3 cities" to "per-city primary for 3 cities + DST-day/gap-fill supplement for WU cities". Rate-limit 21s/IP + IPv4-only transport.

3. **233 confirmed upstream gaps allowlisted**: Specific (city, target_date) tuples where WU+Ogimet+IEM ASOS all return <22 hours — evidence-backed in `confirmed_upstream_gaps.yaml`. Example: Chicago 2026-04-03 (3-hour KORD sensor outage across all 3 sources).

4. **ETL reader modification deferred**: Plan v3 line 76 says readers should be pre-modified in Phase 1 ("they now read observation_instants_current"). This did NOT land in Phase 1 commits and is carried into Phase 2 pre-work (step 4). Impact: pure Phase 1 had no runtime effect anyway because `observation_instants_current` VIEW returns 0 rows while `zeus_meta.observation_data_version='v0'`.

5. **AC11 test not written**: Plan v3 marks this as "must run BEFORE Phase 2 cutover". Carried into Phase 2 pre-work.

## Gate 1→2 status (final)

| Check | Pass condition | Actual | OK |
|---|---|---|---|
| All cities above 20k threshold | `COUNT > 18000` per city | min = Tel Aviv 20,154 | ✅ |
| Zero openmeteo rows | `COUNT(*) WHERE source LIKE '%openmeteo%'` == 0 | 0 | ✅ |
| Zero UNVERIFIED rows | `COUNT(*) WHERE authority='UNVERIFIED'` == 0 | 0 | ✅ |
| Per-day hour count in [22,25] | no `dates_under_threshold` outside allowlist | 0 unaccounted | ✅ |
| Audit clean | `tier_violations==0 AND source_tier_mismatches==0` | 0 / 0 | ✅ |
| Pilot data separated | `v1.wu-native.pilot` isolated by data_version | 93 rows isolated | ✅ |

## Files touched during Phase 1 (registered in manifests)

### Created
- `src/data/wu_hourly_client.py`
- `src/data/ogimet_hourly_client.py`
- `src/data/meteostat_bulk_client.py`  (not in plan v3; step3 records it)
- `src/data/observation_instants_v2_writer.py`
- `src/data/tier_resolver.py`
- `scripts/backfill_obs_v2.py`
- `scripts/audit_observation_instants_v2.py`
- `scripts/fill_obs_v2_dst_gaps.py`  (Tier 2 boundary + DST-day supplement)
- `scripts/fill_obs_v2_meteostat.py` (Meteostat bulk CLI wrapper)
- `docs/operations/task_2026-04-21_gate_f_data_backfill/confirmed_upstream_gaps.yaml`
- `tests/test_tier_resolver.py`, `tests/test_obs_v2_writer.py`, `tests/test_hk_rejects_vhhh_source.py`, `tests/test_backfill_scripts_match_live_config.py`

### Modified
- `src/state/schema/v2_schema.py` (added `zeus_meta` + `observation_instants_current` VIEW + provenance_json column)

## Out of scope (deferred to step 4)

- ETL reader migration (`etl_diurnal_curves.py`, `etl_hourly_observations.py` → read `observation_instants_current`)
- AC11 test (`tests/test_diurnal_curves_empty_hk_handled.py`)
- Pilot leftover cleanup (93 rows `v1.wu-native.pilot`)
- Current-fact surface refresh (`docs/operations/current_data_state.md`)
- Atomic cutover (`UPDATE zeus_meta SET value='v1.wu-native'`)
- Phase 3 ETL rebuild from v2 data

## References

- Plan v3 lines 78–103 (phase descriptions)
- Plan v3 lines 154–168 (acceptance criteria AC1–AC11)
- step2_phase0_pilot_plan.md (predecessor scope + pilot boundary)
- confirmed_upstream_gaps.yaml (233 allowlisted evidence-backed gaps)
