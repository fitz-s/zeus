# config AGENTS

Runtime parameters — all configuration that controls Zeus behavior at runtime. Changes here affect trading behavior directly.

## File registry

| File | Purpose |
|------|---------|
| `settings.json` | Tunable runtime parameters — cycle intervals, thresholds, Kelly multipliers, risk limits |
| `cities.json` | 46 cities: coordinates (= settlement station lat/lon), station id, `settlement_source_type`, timezone, unit, peak hour, cluster; `_source_contract_pending_conversions` blocks config-only source migrations until release evidence exists. **Routine check needed** — see discipline note below |
| `station_precise_coords.json` | Per-city station/reference coordinates and elevation; optional typed `station_ground_proof` binds HKO/HOMR/WMD ground metadata to original entities, not sensor AGL or a blanket precision-PASS grant |
| `hko_station_metadata.html` | Original official HKO station-table response body referenced only by a content-hash-bound `station_ground_proof`; do not execute or normalize this source HTML |
| `noaa_homr_kord_station.json` | Original official NOAA HOMR response body referenced only by Chicago's content-hash-bound primary-DCP `station_ground_proof`; do not normalize or treat airport/barometric elevations as ground |
| `noaa_homr_katl_station.json` | Original official current NOAA HOMR KATL primary-temperature-DCP ground entity; Atlanta-only content-bound proof, never airport/barometric height or historical possession |
| `noaa_homr_kaus_station.json` | Original official current NOAA HOMR KAUS primary-temperature-DCP ground entity; Austin-only proof keeps DCP and forecast-query coordinates distinct |
| `noaa_homr_kdal_station.json` | Original official current NOAA HOMR KDAL primary-temperature-DCP ground entity; Dallas-only content-bound proof |
| `noaa_homr_khou_station.json` | Original official current NOAA HOMR KHOU primary-temperature-DCP ground entity; Houston-only proof requires actual ASOS DCP/GIS-ground binding, not a PLCD publication label |
| `noaa_homr_klax_station.json` | Original official current NOAA HOMR KLAX primary-temperature-DCP ground entity; Los Angeles-only content-bound proof |
| `noaa_homr_kmia_station.json` | Original official current NOAA HOMR KMIA primary-temperature-DCP ground entity; Miami-only content-bound proof |
| `noaa_homr_klga_station.json` | Original official current NOAA HOMR KLGA primary-temperature-DCP ground entity; NYC-only content-bound proof |
| `noaa_homr_ksfo_station.json` | Original official current NOAA HOMR KSFO primary-temperature-DCP ground entity; San Francisco-only content-bound proof |
| `noaa_homr_ksea_station.json` | Original official current NOAA HOMR KSEA primary-temperature-DCP ground entity; Seattle-only content-bound proof |
| `noaa_homr_zspd_station.json` | Original official NOAA HOMR ZSPD current station-reference GROUND entity; direct ICAO/NCDC/location binding, not primary temperature DCP, sensor AGL or historical possession |
| `noaa_homr_eglc_station.json` | Original official NOAA HOMR EGLC current station-reference GROUND entity; independent international kind preserves US ASOS/DCP requirements; POR is not individual ground validity |
| `noaa_homr_nzaa_station.json` | Original official NOAA HOMR NZAA station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_rkpk_station.json` | Original official NOAA HOMR RKPK station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_zuuu_station.json` | Original official NOAA HOMR ZUUU station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_zuck_station.json` | Original official NOAA HOMR ZUCK station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_zggg_station.json` | Original official NOAA HOMR ZGGG station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_ltfm_station.json` | Original official NOAA HOMR LTFM station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_wihh_station.json` | Original official NOAA HOMR WIHH station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_zsjn_station.json` | Original official NOAA HOMR ZSJN station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_dnmm_station.json` | Original official NOAA HOMR DNMM station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_mmmx_station.json` | Original official NOAA HOMR MMMX station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_eddm_station.json` | Original official NOAA HOMR EDDM station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_sbgr_station.json` | Original official NOAA HOMR SBGR station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_rksi_station.json` | Original official NOAA HOMR RKSI station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_zgsz_station.json` | Original official NOAA HOMR ZGSZ station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_nzwn_station.json` | Original official NOAA HOMR NZWN station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `noaa_homr_zhcc_station.json` | Original official NOAA HOMR ZHCC station-reference GROUND entity; explicit ICAO/NCDC/content binding, not temperature DCP, sensor AGL, forecast-query or historical possession |
| `awc_stationinfo_53_station.json` | Original official AWC current station-list entity shared by WMD proofs; exact ICAO/WMO/METAR identity bridge only, never ground authority from its generic elevation |
| `wmo_wmd_eham_station.xml` | Original official WMDR EHAM fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_cyyz_station.xml` | Original official WMDR CYYZ fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_epwa_station.xml` | Original official WMDR EPWA fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_fact_station.xml` | Original official WMDR FACT fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_lemd_station.xml` | Original official WMDR LEMD fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_limc_station.xml` | Original official WMDR LIMC fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_ltac_station.xml` | Original official WMDR LTAC fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_oejn_station.xml` | Original official WMDR OEJN fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_opkc_station.xml` | Original official WMDR OPKC fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_rjtt_station.xml` | Original official WMDR RJTT fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_rpll_station.xml` | Original official WMDR RPLL fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_saez_station.xml` | Original official WMDR SAEZ fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_vilk_station.xml` | Original official WMDR VILK fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_wmkk_station.xml` | Original official WMDR WMKK fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_wsss_station.xml` | Original official WMDR WSSS fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_lfpb_station.xml` | Original official WMDR LFPB fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `wmo_wmd_efhk_station.xml` | Original official WMDR EFHK fixed-land facility-ground entity; exact AWC/WMO binding and version intervals, not sensor-position/AGL authority |
| `city_monthly_bounds.json` | Generated monthly physical bounds used by ingestion guard; generated config, not hand-edited |
| `city_correlation_matrix.json` | Generated city correlation matrix for risk/data-rebuild work; generated config, not hand-edited |
| `provenance_registry.yaml` | INV-13 constant registration for Kelly cascade — every magic number traced to source |
| `reality_contracts/execution.yaml` | External assumption contract: Polymarket execution behavior |
| `reality_contracts/protocol.yaml` | External assumption contract: Polymarket protocol rules |
| `reality_contracts/economic.yaml` | External assumption contract: economic/market assumptions |
| `reality_contracts/data.yaml` | External assumption contract: data source availability and behavior |
| `data_availability_exceptions.yaml` | K2 hole_scanner whitelist: per-model retro-start dates, publication lag, onboarding floor, fill policy |
| `risk_caps.yaml` | R3 A2 engineering defaults for RiskAllocator/PortfolioGovernor capacity, drawdown, heartbeat/WS-gap, reconciliation, unknown-side-effect, and maker/taker thresholds |
| `risk_policy.yaml` | Tracked, content-addressed ceilings for every risk-increasing `sizing.*` lever the live entry path consumes (kelly_multiplier, max_correlated_pct, max_portfolio_heat_pct, max_single_position_pct). Boot guard `src/main.py::assert_risk_policy_artifact` fails closed on any live value exceeding its ceiling and logs `policy_version` + sha256 at every boot. Runtime/control-plane overrides may lower risk freely; they may never raise it above this file |
| `settings.example.json` | Template for config/settings.json with operator-specific values marked null; copy to settings.json to configure |
| `source_release_calendar.yaml` | Source release calendar for data-source availability windows and release schedules |
| `physical_current_sources.json` | Station-bound observation adapters, measured same-time rounded-value equality, and provider request budgets; settlement-grade samples are separate from final daily authority; process-lifetime snapshot applied on restart |

## Rules

- `settings.json` is the source for tunable runtime parameters. Other config files have scoped authority for cities, generated data bounds/correlation, provenance, and reality contracts.
- Reality contracts (INV-11) define what Zeus assumes about external systems — when assumptions break, contracts flag it
- Changes to `provenance_registry.yaml` require tracing to source literature/data
- `risk_caps.yaml` defaults must remain sane when absent; operator tuning is separate from engineering closeout and must not itself authorize live deployment.
- `risk_policy.yaml` has no absent-file default — boot fails closed (`RISK_POLICY_ARTIFACT_MISSING`) if it is missing. Raising a ceiling requires a reviewed commit that bumps `policy_version`; lowering `settings.json::sizing.*` below a ceiling never requires touching this file.

## cities.json — routine check discipline (ROUTINE CHECK NEEDED)

Every `cities.json` row must match the **current** Polymarket market description text for that city. Polymarket can change a city's settlement source, station, or unit at any time.

Volatile external city/station evidence lives under `docs/artifacts/polymarket_city_settlement_audit_*.md`. These artifacts explain why the audit cadence exists; they are not current authority.

**Audit cadence**: monthly, plus before any recalibration run or new-city onboarding.

**Single source of truth**: the `description` field of the most recent active Polymarket market for each city. The market wins when any mirror (cities.json, Wunderground page, code constants) disagrees.

**`settlement_source_type` values**: `wu_icao`, `hko`, `noaa`, `cwa_station`. Field semantics live in config/schema code and `cities.json`; do not update this routing file from a dated market snapshot.

**Downstream consequence**: any station-ICAO change invalidates prior `observations` rows for that city (wrong station = wrong temperatures). Delete the stale rows and re-backfill before running calibration.

**Coordinate invariant**: `lat`/`lon` MUST correspond to the same physical station as `wu_station` / `hko_station` / CWA id. Do not use city-center or approximate coordinates — this drives ENS grid-point selection.

**Pending conversion invariant**: a city listed in `_source_contract_pending_conversions` with `status="pending_release"` remains blocked for new entries even if its current market `resolutionSource` matches `cities.json`. Release requires complete `state/source_contract_block.json` transition-history evidence refs for config, source-validity, backfill, settlements, calibration, and verification.

Use generated/audit evidence for dated city exception lists. Do not encode volatile city/station snapshots directly in this routing file.
