# Problematic-20 v4 best-source reconciliation

Created: 2026-06-17
Last audited: 2026-06-17
Authority basis: operator `zeus_problematic20_best_sources_v4.csv` (per-city best-source doctrine for
the 20 access-capped cohort) reconciled against live Zeus source routing.

## Verdict

**All 20 cities are already wired to their v4 best PUBLIC source.** v4 is authoritative confirmation
that AWC-METAR-exact + WU-final + ECMWF/GFS is the public ceiling — there was no missing source. Every
layer below is proven from live source, not asserted.

| v4 layer | v4 prescription (all 20) | Zeus live state (file:line) | Match |
|---|---|---|---|
| settlement anchor | WU exact ICAO history | `config/cities.json` settlement_source = WU/<ICAO> (20/20 present, all wu_icao tier) | ✓ |
| best live 0D obs | **AWC METAR exact ICAO** + WU final | `day0_fast_obs.py:59,93` endpoint `aviationweather.gov/api/data/metar`, same ICAO as WU settlement page, "all 50 wu_icao cities incl. international", measured 3-6 min, source_id `aviationweather_metar` | ✓ |
| settlement-final reconcile | WU final | `observation_client.py:406` WU coverage-prefix + fresh METAR tail fusion; station-identity gate | ✓ |
| forecast prior | ECMWF + GFS at the PRECISE (9km/0.1°) resolution | **LIVE center anchor = `ecmwf_ifs` 9km/0.1°** (`model_selection.py:58,284` ANCHOR_MODEL; `forecast_source_registry.py:523` "ecmwf_ifs 9km/0.1 deterministic anchor"; `openmeteo_ecmwf_ifs9_anchor.py:33` `MODEL="ecmwf_ifs"`, 9km) + gfs_global + 2026-06-17 precision regionals (gfs_hrrr 3km, gem_hrdps 2.5km, icon_d2 2km, arome, ukmo_uk 2km). The 0.25° `ecmwf_open_data`/`ecmwf_ifs025` is `allowed_roles=("diagnostic",)` — previous-runs walk-forward + legacy Platt baseline ONLY, NOT the live center. | ✓ at 0.1°, NOT 0.25° |
| history / backtest | NCEI ISD / Meteostat + WU final | meteostat lane + WU final | ✓ |
| source-health gate | live only if exact station/rounding/date/coverage/proof match | `day0_source_health.py` (8 states) + `day0_admission.py` (9 breakers) | ✓ |
| METAR divergence guard | — | `config/wu_metar_divergence.json` — station with >±1C METAR-vs-WU divergence must not drive bin-kill | ✓ (defensive) |

## Net-new actionable (free public, not yet wired)

- **Singapore NEA** (`data.gov.sg` real-time station API) — VERY_HIGH, minute/5-min cadence, free
  commercial reuse. NOT wired. It is a Changi-area network, NOT WSSS-exact → wire as a **high-frequency
  day0 SHADOW/covariate**, calibrated against WSSS METAR; never a settlement replacement. The single
  clear free-source build in the cohort.
- Lower-value free SHADOWS (official, not exact-station → shadow-only, need calibration before any
  weight): São Paulo INMET (hourly, 90-day), Buenos Aires SMN (hourly official backfill), Jakarta BMKG
  (aviation web page, scraper+terms gated, not an API).

## Operator-credential-gated (cannot build — weight=0 in live until credentials)

- **Walled, no public no-key live API** (METAR-exact is the public ceiling): China CMA/AMSC AWOS — Beijing,
  Chengdu, Chongqing, Guangzhou, Qingdao, Shanghai, Wuhan (7); NCM Jeddah; PMD Karachi; NiMet Lagos.
- **Auth-onboarding gated**: IMD Lucknow (JWT/key), PAGASA Manila (token/contract).
- **Paid**: MetService Wellington (1-min official API — best paid NZ upgrade), SAWS Cape Town (AfriGIS
  commercial), METMalaysia (token API is forecast/warnings, not exact WMKK obs).

## Flag for operator decision

- **Resolution law (operator 2026-06-17): the ECMWF/regional inputs are 9km/0.1° and finer, never
  0.25°/0.15°.** The live center already obeys this — anchor `ecmwf_ifs` 9km/0.1°, regionals at 2-3km.
  The 0.25° ECMWF lives ONLY in the diagnostic previous-runs/Platt baseline (single-q regime =
  diagnostics-only). Any future re-introduction of a 0.25°/0.4° feed into the LIVE center is a regression.
- **Open-Meteo trading licence (v4 forecast_prior note).** The live 9km/0.1° anchor + ultra-fine
  regionals are served via open-meteo (`api.open-meteo.com` / `single-runs-api.open-meteo.com`); no raw
  no-licence substitute exists for the 9km IFS or the 2-3km regional nests. v4 flags open-meteo needs a
  commercial/licensed cache for trading. Operator decision: confirm the licensed cache/API covers these.
