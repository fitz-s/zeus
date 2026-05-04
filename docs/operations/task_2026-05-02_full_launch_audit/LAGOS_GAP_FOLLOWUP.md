# Lagos Observation Gap Follow-up

Created: 2026-05-02 evening CDT
Authority: investigation note alongside DST gap mop-up #12
Status: structural problem; NOT addressed by `scripts/fill_obs_v2_dst_gaps.py`

## Finding

Lagos has **121 gap-days** in `observation_instants_v2` (data_version `v1.wu-native`),
spanning 2025-07-28 to 2026-05-02. This is far above any other city (next: Seattle 26, Denver 25).

Lagos was excluded from the 2026-05-02 DST mop-up batch because the failure mode is
different from a DST-spring-forward gap.

## Why the existing script can't fix Lagos

`scripts/fill_obs_v2_dst_gaps.py` falls back to Ogimet METAR for the city's settlement
ICAO. Lagos's ICAO is DNMM. Lagos itself does not observe DST (Africa/Lagos is UTC+1
year-round), so the spring-forward narrative does not apply. The "gaps" here are upstream
WU coverage holes (KIA01/DNMM hourly stream is intermittently thin), not DST holes.

Source-distribution evidence in the table:

| source | station | row count |
|---|---|---|
| `wu_icao_history` | DNMM | 17,559 |
| `meteostat_bulk_dnmm` | DNMM | 13,775 |
| `ogimet_metar_dnmm` | DNMM | 1,445 |

Ogimet has only 1,445 Lagos rows — 12% of what Meteostat already has on disk. Falling
back to Ogimet would not close the gap reliably, which is consistent with the operator's
"post Lagos batch failure" note in `REMAINING_TASKS.md` §A.

## Update 2026-05-02 evening: Meteostat bulk fill ATTEMPTED, INEFFECTIVE

Operator authorized re-running `scripts/fill_obs_v2_meteostat.py --cities Lagos` after
confirming today's timezone fix (commit `b1ce90d0`, PR #37 merge) is intact:

- AST guard `tests/test_hourly_local_time_contract_ast.py` covers the script (line 21 of TARGETS).
- Contract tests pass: 54 passed.
- `src/data/meteostat_bulk_client.py:274-301` does proper tz-aware conversion via `astimezone()`.

Result: `raw_row_count=158960`, `observations=13775`, **`written=0`**.

Reason: Meteostat's bulk archive at `https://bulk.meteostat.net/v2/hourly/65201.csv.gz`
**ends at 2025-07-27**, identical to what we already had on disk. Nine months later, the
bulk archive has NOT advanced for Lagos / DNMM. This is a vendor-side dropout, not a
script bug or coordinate error.

## Real status: 121 Lagos gap-days are vendor-blocked

All 3 existing fallback sources for Lagos have terminated or gone too thin to be useful:

| Source | Coverage | Status |
|---|---|---|
| `wu_icao_history` | through 2026-05-02 | active but THIN (3-22 hours/day; not 24) |
| `meteostat_bulk_dnmm` | through 2025-07-27 | FROZEN 9 months ago — bulk archive not advancing |
| `ogimet_metar_dnmm` | through 2026-03-18 | stopped 6 weeks ago — investigate ingest pipeline |

## Vendor-side investigation 2026-05-02 evening (haiku WebFetch probe)

**Ogimet IS alive and serving fresh Lagos METARs.** Direct probe at
`https://www.ogimet.com/cgi-bin/getmetar?icao=DNMM&begin=202605010000&end=202605021200`
returned current data including `METAR DNMM 020600Z 19003KT 8000 FEW011 27/25 Q1011 NOSIG=`.
Continuity exists across the 2026-03-18 boundary. **The 2026-03-18 stoppage is on our
ingest side**, not vendor-side. Our local pipeline that writes `ogimet_metar_dnmm` rows
broke or got disabled.

**Meteostat bulk is genuinely vendor-frozen.** Public UI claims hourly coverage through
2026-03-09 but the bulk CSV at `bulk.meteostat.net/v2/hourly/65201.csv.gz` still ends
2025-07-27. Likely upstream NOAA/DWD mirror staleness for Nigerian synoptic stations.
Not recoverable from our side.

**NOAA ISD does not include DNMM** in the public `isd-history.csv` synoptic directory.
ISD is not a viable 4th source for Lagos.

## Recovery paths in priority order

1. **Restart `ogimet_metar_dnmm` ingest** — Ogimet is serving fresh data; our pipeline died
   2026-03-18. Closes ~6 weeks of recent gaps (2026-03-18 → present). Investigate:
   - `crontab -l | grep -i ogimet`
   - `launchctl list | grep -i ogimet`
   - Last successful write to `observation_instants_v2 WHERE source='ogimet_metar_dnmm'` was 2026-03-18; check logs around that date for an exception or config change.
   - Station-list config files for any DNMM removal between 2026-03-17 and 2026-03-19.
2. **Accept the 2025-07-28 → 2026-03-18 window as permanent gap** unless a paid Meteostat
   API endpoint or alternate vendor (DWD direct, ECMWF reanalysis) is wired in.
3. **Retire Lagos from active settlement** until vendor coverage returns. Only if Lagos is
   not in the live trading portfolio.

## Verification questions for the next session

1. Does `daily_obs_append` / settlement reader degrade gracefully when
   `wu_icao_history` < 22 hours and no fallback is available for the same day?
2. Is Lagos actively settling in the live portfolio? If not, this whole gap is cosmetic.
3. Was there a Lagos-specific cron/launchd job for Ogimet that died on 2026-03-18?
   Check `crontab -l` and `launchctl list | grep -i ogimet` for evidence.

## Verification questions for the next session

1. Does `daily_obs_append` / settlement reader degrade gracefully when
   `wu_icao_history` < 22 hours but `meteostat_bulk_dnmm` is dense for the same day?
2. Are the 121 gap-days actually causing settlement failures, or just cosmetic?
3. Is Lagos a settlement-active city in the live portfolio, or has it been deprioritized?

If the answer to #2 is "cosmetic only", this can be deferred indefinitely. If real
settlement is failing for Lagos, the Meteostat-fallback script is the right path —
estimated 15-30 LOC plus a tier_resolver one-line addition.

## Cross-reference: settlement-pipeline gap is SEPARATE

The 18-day gap in recorded settlements for Lagos (last settled: 2026-04-14) is an **internal pipeline failure** caused by the `ZEUS_HARVESTER_LIVE_ENABLED` flag being disabled in the ingest daemon. It is independent of the 121-day observation gap described above. See the corrected settlement audit for details:
`/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-05-02_settlement_pipeline_audit/AUDIT.md`

## Out-of-scope of this note

- Does NOT investigate why the WU upstream gap is so wide for Lagos specifically
  (rate limits? station downtime? bot signature?). That is a data-vendor question.
- Does NOT touch the in-progress DST mop-up batch.
