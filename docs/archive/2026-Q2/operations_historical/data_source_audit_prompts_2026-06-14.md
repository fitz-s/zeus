# Zeus Data-Source FULL AUDIT — Opus Workflow Prompt Library (2026-06-14)

```
Created: 2026-06-14
Authority basis: operator directive 2026-06-14 — after shipping the implementation, run (in parallel
  with the live-fix) a FULL data-source audit: every source's time-semantic injection, the per-city
  ("math cities") math, and more. Needed to trust the live fix. This is law-8 (correct metadata) at
  the SOURCE level: wrong source time-semantics -> wrong data -> wrong bin-belief -> wrong q_lcb,
  no matter how the calibration is fixed downstream.
Run timing: AFTER implementation ships; in parallel with the live-fixing phase. PLAN/AUDIT ONLY —
  read-only, no code edits, no live changes; every agent writes a full report under
  docs/evidence/data_source_audit_2026-06-14/ and returns prose.
```

## 0. Contract (every audit agent inherits)

**WHY THIS MATTERS (law 8 at the source):** edge = selecting the CORRECT BIN; the bin-belief q is integrated over the settlement preimage of a per-city local-day temperature distribution built from these sources. If a source injects the wrong run/cycle, the wrong valid-time/lead, a mis-converted local day, a DST error, or a wrong station, then the max-over-local-day the model believes is wrong, the winning bin is mis-identified, and q_lcb is confidently wrong. **A correct calibration on corrupt source-time-semantics still loses.** This audit must find every such corruption BEFORE the live fix relies on it.

**WHAT "CORRECT" MEANS per source:** the time quantities are unambiguous and right — `run_time`/cycle (the model run actually used, never a now−lag guess or a stale/previous run), `valid_time`/lead (the forecast hour mapped to the correct instant), `ingest_time`, `served_time`, the dissemination schedule, the watermark — AND they compose correctly into the **per-city local-day max window** (the settlement quantity), with DST and UTC↔local conversion exact, no off-by-one-cycle, no boundary leakage.

**READ-RULES:** cwd /Users/leofitz/zeus. DBs read-only (`file:state/<db>.db?mode=ro`, `timeout 25` per sqlite3, ISO-T bounds). OUTPUT-MANGLER: route raw dumps to /tmp, prose final reply, minimal colons. Every claim carries file:line or query+counts. Provenance-audit any code (git date + law regime). READ the time-semantics contract first: `src/contracts/time_semantics.py`, `src/contracts/dst_semantics.py`, `src/engine/time_context.py`, `src/data/source_time.py`, `src/data/dissemination_schedules.py`, `src/data/source_watermarks.py`, `src/data/temporal_provenance.py`, and the time-semantics registry + per-city DST property tests.

**FORCED-DEPTH:** a shallow pass is a failure. Per lens: map the path; read every file; trace ≥2 CONCRETE recent cases (a real source run / a real city-date) end-to-end with actual timestamps; verify with a probe (query/grep); quantify; enumerate every defect with file:line + repro + the bin-belief consequence. Substantial report required.

---

## A. PER-SOURCE TIME-SEMANTIC INJECTION (one deep opus lens per source-family)

**Per-source lens template:** *"Audit the time-semantic injection of {SOURCE}. Files: {FILES}. Establish, with file:line + a real recent example: (1) how `run_time`/cycle is determined and injected — is it the actual published run, or a now−lag/previous-run guess (the failure class of task #30)? (2) how `valid_time`/lead maps to the real forecast instant; (3) `ingest_time` / `served_time` / watermark; (4) the dissemination schedule used and whether it matches reality; (5) the UTC→per-city-local conversion and the local-day max window this source feeds; (6) DST handling. Then TRACE one real recent run for one real city end-to-end: does the value the model attributes to 'city C, local day D, max temp' actually correspond to the correct instants? Enumerate every defect (wrong run, off-by-one cycle, lead mis-map, stale watermark, DST error, local-day boundary leak) with the bin-belief consequence. Verdict per source: time-semantics CORRECT | CORRUPT(<how>)."*

Source-families (one lens each):
- **S-ECMWF-OPENDATA** — `src/data/ecmwf_open_data_ingest.py`, `ecmwf_open_data.py`, `forecast_fetch_plan.py`, `release_calendar.py`
- **S-ECMWF-AIFS-ENS** — `src/data/ecmwf_aifs_ens_request.py`, `ecmwf_aifs_grib_identity.py`, `ecmwf_aifs_grib_samples.py`, `ecmwf_aifs_sampled_2t_localday.py`, `ensemble_client.py`
- **S-OPENMETEO** — `src/data/openmeteo_client.py`, `openmeteo_ecmwf_ifs9_anchor.py`, `openmeteo_ecmwf_ifs9_bucket_transport.py`, `openmeteo_quota.py`
- **S-OBS-WU-METAR** — `src/data/wu_hourly_client.py`, `wu_scheduler.py`, `ogimet_hourly_client.py`, `observation_client.py`, `observation_instants_writer.py`
- **S-OBS-METEOSTAT-OGIMET** — `src/data/meteostat_bulk_client.py`, `daily_obs_append.py`, `daily_observation_writer.py`, `hourly_instants_append.py`
- **S-TIGGE** — `src/data/tigge_client.py`, `tigge_db_fetcher.py`, `tigge_pipeline.py`
- **S-DAY0-OBS-LANE** — `src/data/day0_fast_obs.py`, `day0_hourly_vectors.py`, `day0_observation_reader.py`, `day0_oracle_anomaly.py` (the nowcast/intraday obs lane + the WU/METAR matched-reading basis)
- **S-OTHER-NWP** (NCEP/GFS/ICON/UKMO single-rep) — `src/data/forecast_source_registry.py`, `mainstream_forecast_source.py`, `source_contracts.py`, `forecast_ingest_protocol.py`

**S-CROSS-CONSISTENCY lens:** across ALL sources, is the time-semantic vocabulary CONSISTENT (does every source mean the same thing by run/valid/local-day), or do sources disagree on what "the day's max for city C" is? This is the cross-source contradiction that corrupts fusion.

---

## B. PER-CITY ("math cities") MATH (deep opus lenses, each across ALL traded cities + spot-check)

- **C-STATION-IDENTITY** — per city: the official settlement station / WMO id, elevation/representativeness, station migrations. Files: `src/data/station_migration_probe.py`, `anchor_city_elevation.json`, `anchor_cross_check.json`, `forecast_target_contract.py`, `forecast_target.py`. Verdict per city: settlement station identity CORRECT?
- **C-SETTLEMENT-PREIMAGE-BINS** — per city: the bin boundaries + the settlement preimage (which temperature range maps to which bin/YES), boundary rounding (the shifted-rounding class, task #41), WMO rounding authority. Files: `src/contracts/settlement_semantics.py`, `settlement_resolution.py`, `settlement_outcome.py`, `calibration_bins.py`, `boundary_policy.py`, `tick_size.py`. Does the system's preimage match the venue's actual settlement rule per city?
- **C-LOCAL-DAY-DST** — per city: the local-day definition (the window over which max is taken), DST transitions, UTC offset. Files: `src/contracts/dst_semantics.py`, `time_semantics.py`, `season.py`, `src/engine/time_context.py`. Spot-check cities across DST boundaries + southern hemisphere.
- **C-PER-CITY-CALIBRATION** — per city/era: the σ-scale k/w (sigma_scale_fit.json), bias/scale (per-model walk-forward debias), the EMOS HIGH-metric params, era-EB pooling (#59). Files: `state/sigma_scale_fit.json`, `src/strategy/probability_uncertainty.py`, `src/data/replacement_forecast_*`, calibration artifacts. Is each city's calibration fitted from sufficient, correctly-attributed settled data (no leakage, no cross-city contamination)? Does any per-city mis-calibration cause the q_lcb≈0 collapse the implementation targets?

---

## C. CROSS-CUTTING (deep opus lenses)

- **X-COVERAGE-FRESHNESS-HEALTH** — source coverage, freshness gating, holes, the source-health probe + lock-starvation class (#77). Files: `src/data/source_health_probe.py`, `collection_frontier.py`, `hole_scanner.py`, `data_coverage`, `producer_readiness.py`, `ingestion_guard.py`. Are there silent coverage holes corrupting fusion for specific cities/days?
- **X-FUSION-WEIGHTS-DEDUP** — the T2 precision fusion: inverse-variance weights from walk-forward residual variance, Ledoit-Wolf Σ, source-family single-rep (ICON/NCEP/UKMO most-specific-first), correlation. Files: `src/strategy/market_fusion.py`, `correlation_shrinkage.py`, `correlation.py`, `bayes_precision_fusion_*`. Are weights/dedup correct, or does a mis-weighted/duplicated source distort the fused center+spread (and thus the bin-belief)?
- **X-MISSING-MEMBERS** — the MISSING_EXPECTED_MEMBERS / masked-downstream-drop class (#70). Files: `src/data/executable_forecast_reader.py`, `forecast_completeness.py`, `forecast_extrema_authority.py`. Does a partial ensemble silently bias the max-over-day / the tail (relevant to far-bin q_lcb≈0)?
- **X-METADATA-TO-BELIEF END-TO-END** — the integrating lens (law 8): trace ONE settled city-date from every source's raw injection → fusion → μ*,σ → settlement-preimage bin integration → q on the winning bin. Did the correct bin get honest mass, and if not, WHICH source/time/city defect upstream caused it? This lens connects the data audit to the implementation's q_lcb≈0 fix.

---

## D. SYNTHESIS

Opus assembler: reads all lenses → writes `docs/evidence/data_source_audit_2026-06-14/DATA_SOURCE_AUDIT.md` (FULL, untrimmed): per-source time-semantics verdicts; per-city math verdicts; the cross-cutting defects; and — critically — the ranked list of **data-foundation defects that would corrupt the correct-bin belief**, each with the fix required BEFORE the live flip can be trusted, and which (if any) explain the q_lcb≈0 collapse independently of the calibration layer. Plus a clean PASS-list (sources/cities verified correct) so the live fix knows what it can trust.

---
Status: PROMPTS READY. Materialize as one opus workflow (per-source ‖ per-city ‖ cross-cutting → synthesis) and launch in parallel the moment implementation ships, per operator. Read-only; informs the live fix.
```
