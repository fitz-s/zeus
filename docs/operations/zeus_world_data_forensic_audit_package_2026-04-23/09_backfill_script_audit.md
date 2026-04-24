# 09 Backfill Script Audit

## `src/state/db.py`

- **Purpose/source/target:** Defines schema. Legacy `settlements` unique city/date and v2 empty tables create a v1/v2 seam; schema alone does not prove data correctness.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; add migrations/views/readiness checks.

## `src/contracts/settlement_semantics.py`

- **Purpose/source/target:** Correctly encodes WMO half-up and HKO oracle truncation semantics. It is a strong contract, but DB rows must show which transform applies.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; force all settlement imports to call it and record rule version.

## `src/data/tier_resolver.py`

- **Purpose/source/target:** Defines source tiers and fallback allowance. Good intent, but DB rows lack source-role eligibility.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Harden with registry-backed eligibility and downstream views.

## `src/data/observation_instants_v2_writer.py`

- **Purpose/source/target:** Validates non-empty provenance, allowed authority, data version pattern, and source allowed for city. Best writer path found. Uses INSERT OR REPLACE, so replacement semantics need audit.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; add no-overwrite-without-identical-hash and source_role/training fields.

## `src/data/observation_client.py`

- **Purpose/source/target:** Day0 live observations prioritize WU then other fallbacks. Useful for monitoring, dangerous if downstream treats fallback as settlement authority.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep but tag runtime-only/fallback rows.

## `src/data/daily_obs_append.py`

- **Purpose/source/target:** Writes live daily WU/HKO observations and coverage. HK HKO special case is good.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Harden provenance and finalization status.

## `src/data/hourly_instants_append.py`

- **Purpose/source/target:** Writes legacy `observation_instants` from Open-Meteo Archive. This is gridded/archive evidence, not settlement truth.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Deprecate for canonical paths.

## `src/data/forecasts_append.py`

- **Purpose/source/target:** Open-Meteo previous-runs append path; issue-time preservation appears weak/missing.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Do not allow canonical training until true issue/available time captured.

## `src/data/rebuild_validators.py`

- **Purpose/source/target:** Encodes validation concepts such as VERIFIED as a contract.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; extend to source-role/provenance hash checks.

## `src/data/wu_hourly_client.py`

- **Purpose/source/target:** Uses WU private v1 style hourly endpoint and UTC-hour bucket extrema. Good for evidence if station endpoint stable; not a durable official contract alone.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Harden with endpoint version, payload hash, and station registry.

## `src/data/ogimet_hourly_client.py`

- **Purpose/source/target:** Ogimet METAR fallback with partial-row risk on chunk failure and rate limits.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep as fallback only; require completeness manifest.

## `src/data/meteostat_bulk_client.py`

- **Purpose/source/target:** Bulk static fallback with lag; useful for historical gaps.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep as evidence/fallback; not canonical settlement.

## `src/data/openmeteo_client.py`

- **Purpose/source/target:** Open-Meteo archive/previous-runs client.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep for model/fallback lanes only.

## `src/data/polymarket_client.py`

- **Purpose/source/target:** CLOB client; does not by itself capture weather market rules or settlement finalization.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Add explicit market rule ingestion.

## `src/data/ecmwf_open_data.py`

- **Purpose/source/target:** Empty/no-op in read version.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Deprecate or implement; do not cite as active forecast source.

## `src/data/ensemble_client.py`

- **Purpose/source/target:** Open-Meteo ensemble API path with issue_time none risk.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Runtime/model fallback only unless issue cycles are fixed.

## `src/data/ingestion_guard.py`

- **Purpose/source/target:** Good unit/physical/time/DST guards.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Extend to row eligibility and source-role.

## `src/data/hole_scanner.py`

- **Purpose/source/target:** Useful coverage scanner; expected tables omit v2 forecast/settlement families.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Extend to v2 canonical tables and require physical reconciliation.

## `scripts/backfill_obs_v2.py`

- **Purpose/source/target:** Writes v2 hourly observations through writer; continues after failed windows; no full resume proof.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep with manifest, expected counts, no silent partial success.

## `scripts/fill_obs_v2_dst_gaps.py`

- **Purpose/source/target:** Fills WU DST gaps with Ogimet fallback. Good repair as evidence; hazardous if fallback eligible silently.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep with fallback role tags.

## `scripts/fill_obs_v2_meteostat.py`

- **Purpose/source/target:** Fills sparse WU gaps with Meteostat bulk. Bulk lag and aggregation caveats.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep evidence only; require training_allowed=0 by default until reviewed.

## `scripts/backfill_hko_daily.py`

- **Purpose/source/target:** HKO daily backfill; good source-specific handling.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; record oracle transform/finalization.

## `scripts/backfill_ogimet_metar.py`

- **Purpose/source/target:** Daily extrema reconstruction from METAR/SYNOP. Cadence limitations and station assumptions explicit.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Fallback/evidence only.

## `scripts/backfill_wu_daily_all.py`

- **Purpose/source/target:** Static city-station WU daily backfill, claims settlement-like data. Actual DB WU provenance empty.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Harden or quarantine pre-retrofit rows.

## `scripts/backfill_hourly_openmeteo.py`

- **Purpose/source/target:** Open-Meteo archive to legacy hourly/instant tables.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Do not use as settlement/training label truth.

## `scripts/backfill_tigge_snapshot_p_raw.py`

- **Purpose/source/target:** Legacy p_raw derivation.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Deprecate for v2 canonical training.

## `scripts/backfill_tigge_snapshot_p_raw_v2.py`

- **Purpose/source/target:** Metric-specific v2 p_raw with dry-run/force and causality filters.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; populate only after v2 snapshots validated.

## `scripts/rebuild_calibration_pairs_v2.py`

- **Purpose/source/target:** Good v2 filters on snapshots; observation selection still needs source-role and settlement alignment guards.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Harden before use.

## `scripts/rebuild_calibration_pairs_canonical.py`

- **Purpose/source/target:** Legacy high-only rebuild from ensemble_snapshots/observations.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Deprecate or quarantine.

## `scripts/etl_historical_forecasts.py`

- **Purpose/source/target:** Can reconstruct available_at from delays; DB forecasts empty.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Mark reconstructed rows non-canonical unless independently verified.

## `scripts/etl_hourly_observations.py`

- **Purpose/source/target:** Lossy ETL from v2 current view to legacy hourly; uses COALESCE(temp_current,running_max).
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Compatibility only; ban from canonical consumers.

## `scripts/etl_tigge_ens.py`

- **Purpose/source/target:** Empty/no-op in read version.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Implement or remove from manifest.

## `scripts/extract_tigge_mn2t6_localday_min.py`

- **Purpose/source/target:** Strong low-track local-day extractor with causality/boundary concepts.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; verify against ECMWF/TIGGE files and populate v2.

## `scripts/extract_tigge_mx2t6_localday_max.py`

- **Purpose/source/target:** Strong high-track local-day extractor with causality/boundary concepts.
- **Authority level:** script-dependent; must be made explicit in written rows.
- **Idempotence:** acceptable only where row replacement is content-identical or revision state is recorded. `INSERT OR REPLACE` paths require hash comparison.
- **Failure modes:** silent partial windows, quota/rate-limit skips, station unsupported, source drift, DST day incompleteness, fallback contamination.
- **Quota/rate assumptions:** WU private endpoint and Ogimet rate limits require manifests; Meteostat bulk lag requires expected-date bounds.
- **Station/timezone/unit assumptions:** must be joined through registry, not hard-coded city maps only.
- **Proof of correctness:** insufficient unless DB row provenance plus source-specific tests prove station, unit, local day, and finalization.
- **Disposition:** Keep; verify and populate v2.