# Current Data State

Status: CURRENT — live belief center is the fusion posterior `openmeteo_ecmwf_ifs9_bayes_fusion_v1`. Supersedes the 2026-06-07 AIFS shadow/veto refresh (that product is dead — see below).
Last audited: 2026-07-21
Max staleness: 14 days — re-query `state/zeus-forecasts.db` before trusting past that.
Authority status: not authority law; audit-bound current fact only. Full source detail: `docs/operations/current/plans/coarse_feed_retirement_2026-07-20.md` and `architecture/data_sources_registry_2026_05_08.yaml`.

## Live forecast authority

- **Live trade-authority product: `openmeteo_ecmwf_ifs9_bayes_fusion_v1`** — flag `openmeteo_ecmwf_ifs9_bayes_fusion_live_enabled=true` (`config/settings.json:264`); 44,925 `forecast_posteriors` rows, latest `computed_at` 2026-07-21T01:27Z. This is the belief center, not a shadow/veto lane.
- Fed by the multi-model fusion basket in `raw_model_forecasts` (ecmwf_ifs, icon_*, ukmo, arome, ncep_nbm, jma_msm, plus `cwa_township` / `hko_fnd` official station forecasts re-homed onto `ingest_main`). Re-audit members/freshness from `SELECT model, MAX(captured_at) FROM raw_model_forecasts GROUP BY model`.

## Retired / dead — do NOT cite as live

- **AIFS (`ecmwf_aifs_ens`) — RETIRED.** GRIB-ingest cluster deleted in commit `2764616bf` (2026-07-19); `src/data/forecast_source_registry.py` tier=disabled, product "A1" trade_authority=BLOCKED; zero `aifs` tables in `state/zeus-forecasts.db`.
- **`openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1` — DEAD** (frozen 2026-06-18T11:52Z, 3,610 rows, none since). This is the product the pre-2026-07-21 version of this file wrongly labelled `CURRENT_FOR_LIVE`.
- Coarse `ecmwf_open_data` (0.25°) — cold-lane fallback only (Day0 fail-closed fallback + legacy causal-cycle pin), NOT the belief center.

## Observation / settlement (ACTIVE as of 2026-07-21 audit)

- Observation: `wu_icao_history` (48-city primary), `hko_daily_api`/`hko_realtime_api`, `ogimet_metar_*` (Tel Aviv / Moscow / Istanbul). Settlement: `polymarket_gamma` → `settlements`.
- Source-freshness facts here come from a read-only audit of `state/zeus-forecasts.db` on 2026-07-21; re-query before trusting past the staleness ceiling.

## Notes

- Generated read-only; missing evidence remains false. Known coverage gap (LOW-track fusion): `docs/operations/known_gaps.md`.
