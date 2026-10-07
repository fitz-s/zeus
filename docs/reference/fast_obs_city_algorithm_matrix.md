# Fast-observation city algorithm matrix

Reference, not authority. This file records which observation channels feed which Day0 algorithm for each of the
54 configured cities, and checks each city against code and live data. Code, `config/*.json` and the canonical DBs
win any disagreement. Probability law lives in `docs/authority/replacement_final_form_2026_06_09.md`.

checked=2026-W41; basis=code at origin/live bc024fedd + read-only WORLD/FORECASTS queries 2026-10-07T07:47Z–08:20Z;
until=recheck-on-use. The row counts and Gaps below are a snapshot of that date. Re-run §6 before you rely on them.
§5 G3/G4/G6/G7 resolutions: checked=2026-W41; basis=branch `fix/fast-obs-gaps-g3-g7` + read-only DB/Gamma/AWC queries
2026-10-07T09:47Z–10:20Z; until=recheck-on-use.

Line numbers cite bc024fedd.

## 1. How a channel reaches Day0 q

Every Day0 posterior is built from three facts, read at the decision instant:

- **Settlement fact.** `_latest_authorized_day0_fact(require_settlement_channel=True)`
  (`src/data/replacement_forecast_current_target_plan.py:931`). Allowed: the city's settlement product
  (`:1405-1438`) plus every registry route whose `settlement_authorized` is true (`:1463-1469`;
  `src/data/physical_current_sources.py:58-60`). On the NOAA branch, `observation_instants` Ogimet rows are
  excluded (`:1049-1056`). Opportunity events are admitted only from the settlement source (`:1232-1253`).
- **Physical fact.** The same function with `require_settlement_channel=False`. Adds AWC (margin-adjusted,
  `:1544-1584`), Ogimet, and the physical-only routes (`:1462`). For KMA stations, the KMA event replaces the
  AWC/Ogimet facts (`:1787-1826`). The reduction is MAX (high) / MIN (low) over all candidate facts (`:1886`).
  HKO selects its latest official snapshot instead (`:1841-1871`).
- **Current state.** `read_day0_current_temperature_state` (`src/data/day0_hourly_vectors.py:4450`) picks the
  latest causal same-station print over `day0_current_temperature_channels` (`:4427-4447`). On equal
  observation time, settlement-grade channels win (`:4665-4672`). It anchors the remaining-day path
  (`remaining_day_extremes_c_with_current_state`, `:4687`). It is never a boundary.

The seed producer then selects one observed extreme for the materialization request
(`src/data/replacement_forecast_seed_discovery.py:194-286`), checking in this order:

1. the qualified fast tail (`latest_fast_station_conditioning`, `src/data/day0_fast_obs.py:927`). Its source is
   `wu_api+same_station_fast_tail`;
2. otherwise the physical fact.

The selected source decides the q path. `day0_evidence_finality` (`src/events/day0_authority.py:275-323`)
classifies it, and `day0_is_carrier_source` (`:256-272`) routes it:

| Selected source | Finality | q path in the materializer | q_shape seen live |
|---|---|---|---|
| `noaa_wrh_<icao>` | MONOTONE_SETTLEMENT_BOUND | absorbing support truncation (`replacement_forecast_materializer.py:847-864`) | `fused_day0_conditioned_normal` |
| `aviationweather_metar`, `ogimet_metar_<icao>` | PROVISIONAL | remaining-path carrier + preliminary-report survival (`_day0_noaa_preliminary_carrier`, `:1526`) | `day0_remaining_shared_carrier_v2`/`v3` |
| `wu_api+same_station_fast_tail` | PROVISIONAL | carrier + fast residual likelihood (`:7612-7650`, `:8170-8196`) | `fused_day0_fast_residual_likelihood` |
| `hko_hourly_accumulator` | PROVISIONAL | carrier (`day0_is_carrier_source`) | `day0_remaining_shared_carrier_v3` |
| registry route channels: `jma_amedas_temperature`, `eccc_swob_temperature`, `mgm_metar_temperature`, `imd_olbs_metar_temperature`, `metaviatelecom_metar_temperature`, `fmi_airport_temperature`, `dwd_cdc_temperature`, `imgw_synop_temperature`, `knmi_station_temperature` | **UNKNOWN** (`day0_authority.py:316-320`) | none: not absorbing, not a carrier. Recorded as `day0_provisional_observation`, but no conditioning is applied (`replacement_forecast_materializer.py:7760`, `:7994-7998`) | **`fused_normal_direct`** |
| `wu_icao_history`, `wu_station_*` | PROVISIONAL (`day0_authority.py:297-301`) | same: provisional but not a carrier source, so q is unconditioned | `fused_normal_direct` (Jinan, 662 rows to 09-29) |

The last two rows are the main finding of this audit (Gap G1). A source that is not absorbing and not a carrier
source drops the observation from q entirely. Live, the posterior is still stamped `replacement_q_mode =
FUSED_NORMAL_FULL`/`PARTIAL` (1,075 rows in 48 h for the six fast-admission cities).

## 2. Algorithm classes

### 2.1 SETTLEMENT_CHANNEL (boundary B)

- **Definition.** The city's own settlement product, as the settlement fact reads it:
  - `noaa_wrh_<icao>` for `noaa`;
  - `wu_icao_history` for `wu_icao`;
  - `hko_hourly_accumulator` (intraday) and `hko_daily_api` (final) for `hko`. `hko_rhrread_spot` is not
    settlement: see §2.4.
- **Base channel sets.**
  - `day0_hourly_vectors.py:4482-4486` (current-state settlement grade);
  - `replacement_forecast_current_target_plan.py:1405-1438` (settlement/physical channel sets);
  - `day0_fast_obs.py:747-751` (residual-likelihood target).
- **Writers.** Each is checked live in §6.
  - **11 US °F cities.** `noaa_wrh_<icao>` is a `canonical_resolver` route (`config/physical_current_sources.json:854-1095`),
    polled by `_day0_fmi_temperature_tick` / `_day0_current_temperature_source_tick`
    (`src/ingest_main.py:2154`, `:2182`, `:2243`). The minimum poll is 60 s and the view is `hourly`.
  - **Other 37 NOAA cities.** Only `daily_tick` writes `noaa_wrh_<icao>` (`src/data/daily_obs_append.py:2603-2630`).
    It fetches once the city's local day has ended + 1 h (`_noaa_daily_target_dates_due`, `:1772`;
    `src/engine/time_context.py:130`). Gap G5 covers the consequence.
  - **WU cities.** `wu_icao_history` comes from `scripts/obs_live_tick.py` through `_k2_obs_tick` (:15 hourly) and
    `_k2_obs_fast_tick` (15 min) (`src/ingest_main.py:1869`, `:1953`, `:6245-6251`). Jinan additionally has the
    `wu_station_history_temperature` canonical_resolver route (`physical_current_sources.json:804`).
  - **HKO.** `_accumulate_hko_reading` writes the accumulator plus the `hko_rhrread_spot` print
    (`daily_obs_append.py:922`, `:1013`, `:1047`). It is called from `daily_tick` (`:2545`) and
    `scripts/hko_ingest_tick.py:860`. `_k2_hko_daily_final_tick` writes the final daily row.
- **Effect on q.**
  - A NOAA page print is `MONOTONE_SETTLEMENT_BOUND`. It truncates support: q = 0 for bins the running extreme
    has passed. The adapter masks those bins (`event_reactor_adapter.py:50714-50760`).
  - WU history is `PROVISIONAL_CURRENT_SNAPSHOT` (`day0_authority.py:290-301`). WU intraday values can be revised,
    so they are statistical.
  - HKO is `PROVISIONAL_CURRENT_SNAPSHOT` (`:287-288`), priced by the carrier. Only `hko_daily_api` is
    `FINAL_DAILY_SETTLEMENT`.

### 2.2 FAST_ADMISSION (settlement-grade fast route)

- **Definition.** A registry row with `role: fast_admission` that passes `fast_admission_defect`
  (`src/data/physical_current_sources.py:98-147`) at load (`:206-240`). The proof must show:
  - same station, channel and unit;
  - exact value identity under the city's rounding (`_counts_proven`, `:63-66`);
  - a first proven lead whose latest receipt precedes the earliest receipt of AWC, the resolver and every other
    route at the station.

  An invalid row is omitted and logged `PHYSICAL_CURRENT_FAST_ADMISSION_OMITTED`. That line occurs 0 times in the
  current (unrotated) ingest logs, and a read-only load returns all six rows. `settlement_authorized` is true (`:58-60`).
- **Admitted rows.** Load verified by running the loader read-only.

  | City | Station | Channel | Registry line | Identity proof | Lead proof (vs AWC / resolver) |
  |---|---|---|---|---|---|
  | Tokyo | RJTT | `jma_amedas_temperature` | `physical_current_sources.json:272` | 152/152 (`round4_cumulative_identity.json`) | 2026-09-30T17:30Z |
  | Toronto | CYYZ | `eccc_swob_temperature` | `:357` | 57/57 | 2026-09-30T18:00Z |
  | Moscow | UUWW | `metaviatelecom_metar_temperature` | `:1096` | 4/4 (`round4_moscow_proof.json`); re-admitted 2026-10-06 by operator decision, plaintext HTTP accepted (294a7a67b) | 2026-09-30T23:00Z |
  | Lucknow | VILK | `imd_olbs_metar_temperature` | `:1204` | 2/2 (`round4_india_proof.json`) | 2026-09-30T23:30Z |
  | Ankara | LTAC | `mgm_metar_temperature` | `:1292` | 50/50 (`round4_mgm_proof.json`) | 2026-09-30T23:50Z |
  | Istanbul | LTFM | `mgm_metar_temperature` | `:1385` | 49/49 | 2026-09-30T23:50Z |

- **Inputs.** The channel's `observation_prints` rows. Each row is replayed through `valid_station_print`
  (`src/data/station_temperature_adapters.py:491`) by every reader.
- **Effect on q today.**
  - The route is in the settlement-fact channel set, so its value becomes the settlement fact. The adapter binds
    it as `settlement_source`, and the fast-tail test compares AWC against it. This is the intended early B.
  - It is also a current-state channel, with settlement-grade tie-break (`day0_hourly_vectors.py:4665-4672`).
  - However, when a route print is the selected Day0 source, `day0_evidence_finality` returns `UNKNOWN`. The
    materializer then builds an unconditioned q: B is not applied and the remaining-day carrier is not used
    (Gap G1).
- **Cadence caveat.** `jma_amedas` parses every 10-min AMeDAS sample (`station_temperature_adapters.py:421-427`).
  RJTT METARs are :00/:30 only, so 4 of every 6 Tokyo rows are not METAR instants. The identity proof covers the
  :00/:30 rows only (Gap G2).

  The other five routes publish only at METAR instants. Live minute histogram, last 48 h:
  - MGM :20/:50;
  - IMD and metaviatelecom :00/:30;
  - SWOB :00.

### 2.3 KMA_EVENT (Seoul RKSI, Busan RKPK)

- **Definition.** `KMA_PRIORITY_STATIONS = {RKPK, RKSI}` (`src/data/day0_fast_obs.py:87`). The KMA AMO raw METAR
  page is polled by `KmaMetarCursor` (`:1441`) inside the METAR emitter prefetch (`:4150-4223`). It is written as
  `DAY0_EXTREME_UPDATED` opportunity events with `observation_transport = kma_amo_raw_metar` and a
  `kma_report_window`.
- **Reader.** `_latest_kma_day0_event_state` (`:2871`) rebuilds the event from its raw window plus same-station
  ledger rows.
- **Precedence.**
  - It takes precedence over the ledger in the current state (`day0_hourly_vectors.py:4499-4517`), the fast-tail
    extreme (`day0_fast_obs.py:522-541`) and the physical fact (`replacement_forecast_current_target_plan.py:1787-1826`).
    In the physical fact it removes the AWC/Ogimet facts and applies `margin_units`.
  - It never enters the settlement fact.
  - Its `settlement_source` is `aviationweather_metar`, so its finality is PROVISIONAL.
- **When it runs.** The KMA poll covers only priority stations: held positions in `day0_window`/`pending_exit`
  (`src/ingest_main.py:322-342`; `replacement_forecast_seed_discovery.py:890`). Gap G4 covers the consequence.

### 2.4 HKO 1-min mean (Hong Kong)

- **Channel.** `hko_current_1min_mean` (`day0_hourly_vectors.py:4335`). The source is HKO `latest_1min_temperature.csv`,
  HQ row only (`:4339-4397`), with authority `CURRENT_ONLY`.
- **Writer.** `_k2_hko_tick` → `append_hko_current_temperature_print` (`src/ingest_main.py:2566`, `:2644`;
  `scripts/hko_ingest_tick.py:281`). After each commit it publishes a reactor wake
  (`src/ingest_main.py:2779-2850`).
- **Role.**
  - It is **current state only.** It is read by `read_day0_current_temperature_state` (`:4640-4651`). The reader
    rejects a value older than 25 min (`HKO_CURRENT_TEMPERATURE_MAX_AGE`, `:4336`) and replays the stored body
    (`replay_hko_current_temperature_print`, `:4400`).
  - It ranks as settlement-grade only for tie-breaking at an equal observation time (`:4671`).
  - It is not in any `_latest_authorized_day0_fact` channel set, so it never sets B.
  - The HKO boundary is the official since-midnight extrema (`hko_hourly_accumulator`). The materializer rebinds
    any `hko_rhrread_spot` request to that accumulator (`replacement_forecast_materializer.py:995-1050`).
  - `hko_rhrread_spot` is telemetry: the physical fact lists it (`replacement_forecast_current_target_plan.py:1409-1417`),
    but the HKO reduction returns only the official snapshot (`:1841-1871`).

### 2.5 AWC_METAR mirror

- **Channel.** `aviationweather_metar`. This is the same-station METAR that the settlement page also renders.
  The source registry is `fast_obs_source_for_city` (`day0_fast_obs.py:997-1122`). A city is excluded
  (`DAY0_FAST_OBS_CITY_EXCLUDED`) when `metar_margin_units_for_city` returns None (`day0_oracle_anomaly.py:168-213`).
  Today that excludes only Jinan, which has 4 matched pairs and provenance `thin_sample`
  (`config/wu_metar_divergence.json`).
- **Writer.**
  - `_day0_metar_source_clock_tick` → `Day0FastObsEmitter` → `_append_metar_prints_to_ledger`
    (`src/ingest_main.py:2035`; `day0_fast_obs.py:3197-3275`).
  - Interval job `ingest_day0_metar_source_clock` (`ingest_main.py:6252`).
- **Uses.**
  1. **Physical fact**, margin-adjusted toward the absorbing direction
     (`replacement_forecast_current_target_plan.py:1544-1584`).
     - Margins: 2.0 °F for the 11 US cities, 7.0 for Lucknow, 2.0 for Manila and Wellington, 0.0 elsewhere.
     - It is excluded from the settlement fact (`:1049-1056`, `:1232-1253`).
  2. **Current state** (`day0_hourly_vectors.py:4597-4616`).
  3. **Residual likelihood.** `build_fast_station_residual_likelihood` (`day0_fast_obs.py:694-909`) builds it
     from same-observation-instant pairs of (settlement channel − AWC):
     - 7-day causal window (`:306`, `:759`);
     - at least 20 pairs (`:307`, `:846`);
     - unknown mass 1 − 0.05^(1/n) (`:851`).

     `latest_fast_station_conditioning` (`:927-994`) returns it only when the AWC extreme strictly passes the
     settlement extreme (`fast_extreme_supersedes_settlement`, `:912`). The selected source is then
     `wu_api+same_station_fast_tail`. That name is used for NOAA cities too.

     The materializer applies the likelihood to the carrier (`replacement_forecast_materializer.py:8170-8196`).
     Live: 5,659 such posteriors in 48 h, across 45 cities.
- **°F T-group rule.**
  - For °F cities, an AWC print counts only if its raw METAR carries a T-group. The value is then T-group °C × 9/5
    + 32 (`day0_hourly_vectors.py:4606-4616`; `replacement_forecast_current_target_plan.py:1553-1565`).
  - The residual likelihood applies the same rule (`day0_fast_obs.py:680-683`).
  - A whole-degree-°C body temperature is never converted for a °F market.

### 2.6 PHYSICAL_ONLY dense national stations

- **Definition.** A registry row with `role: physical_only`, so `settlement_authorized` is false.
- **Rows.**

  | City | Station | Channel | Registry line | Cadence |
  |---|---|---|---|---|
  | Helsinki | EFHK | `fmi_airport_temperature` | `:13` | 10 min |
  | Warsaw | EPWA | `imgw_synop_temperature` | `:442` | hourly |
  | Munich | EDDM | `dwd_cdc_temperature` | `:487` | 10 min |
  | Amsterdam | EHAM | `knmi_station_temperature` | `:779` | 10 min; no writer, Gap G3 |
  | Jinan | ZSJN | `wu_station_current_temperature` | `:829` | ~10 min |

  The registry has no other physical-only rows.
- **How they enter.**
  1. **Physical fact.**
     - They are in the physical channel set and never in the settlement set (`replacement_forecast_current_target_plan.py:1462-1469`).
     - The adapter's `physical_only_statistical` branch (`src/engine/event_reactor_adapter.py:38504-38512`) uses
       the physical fact only when *no* settlement fact exists and the city is `noaa`.
     - That fact is stamped `evidence_finality = PROVISIONAL_CURRENT_SNAPSHOT` (`:38708-38712`) and
       `observation_authority_role = same_station_fast_statistical_only` (`:38760-38763`), and the payload carries
       `_edli_day0_physical_only_statistical_authority` (`:38926-38927`).
     - When a settlement fact exists, a physical-only value beyond it raises
       `GLOBAL_DAY0_PHYSICAL_FRONTIER_NOT_SETTLEMENT_CONFIRMED` (`:41110-41117`, `:37716-37745`, `:41543-41550`).
       The exception is a validated fast-residual or provisional posterior. The physical-only value never becomes
       B. That branch is read-only reference here.
  2. **Current state.** Each row is a current-state channel (`day0_hourly_vectors.py:4446`).
     - FMI rows must be ≤ 25 min old on the target day (`:4583-4595`).
     - The newest observation time wins, and grade only breaks ties (`:4668-4673`). A 10-min dense print
       therefore anchors the path between METARs. Live: at 08:20Z the Tokyo state was `jma_amedas_temperature`
       observed 08:10Z. Helsinki served AWC 08:20Z only because that METAR was the newest print.
  3. **Selected Day0 source.** In the physical fact's MAX/MIN, a dense value can beat AWC. Example: Helsinki
     10-07 high, FMI 11.2 vs AWC 11.0. The seed then selects `fmi_airport_temperature`, which has the same
     `UNKNOWN` finality as G1. Live: 11 Helsinki posteriors in 48 h were `fused_normal_direct` from FMI.
     Most of the time the fast tail outranks it (454 rows).
- **Evidence.**
  - Daily-extreme overshoot (`artifacts/fast_obs_audit/daily_extreme_agreement/SUMMARY.md`). High over-rate:
    - FMI 38/177;
    - KNMI Tx 73/176;
    - IMGW 15/188;
    - DWD 7/203, plus 50 under, a cold bias.

    METAR-content channels are the only settlement-grade fast sources, so the physical-only posture is correct.
  - Helsinki same sensor (`docs/operations/current/plans/task_2026-10-06_fast_obs_closure.md`, "Helsinki root
    cause"). FMI 100968 is ~140 m from the 22L TDZ sensor, in the same measurement stream. METAR−FMI SD is 0.283,
    against 0.289 pure rounding noise. Same-instant agreement is 229/240 for the last 240 pairs, and 86.8 % over
    the full archive, non-stationary by month.

    The daily-high overshoot is cadence (13:10 reading between METARs), not disagreement. FMI is not faster for
    the METAR instant itself: delivery p50 is 150 s vs AWC 89 s.

### 2.7 NONE

A city with no fast channel beyond its settlement product and AWC. Its Day0 inputs are B, the post-day
statistical fallback (NOAA only), AWC residual likelihood (where ≥ 20 pairs) and the current state.

## 3. Evidence-linked notes

- **Dense-station overshoot.** `artifacts/fast_obs_audit/daily_extreme_agreement/SUMMARY.md`; see §2.6.
- **Helsinki same sensor.** `docs/operations/current/plans/task_2026-10-06_fast_obs_closure.md`; see §2.6.
- **REDEMET São Paulo (SBGR).**
  - The key is in keychain `zeus-obs/redemet/api-key`; its presence was checked, not its value.
  - Value match is 99/99 vs AWC.
  - Live race 12:54–14:10Z on 2026-10-06: at 14:00Z AWC was first by 112 s. No lead, so not admitted. No runtime
    code references REDEMET.
- **KNMI and Met Office.**
  - Both keys are present (`zeus-obs/knmi/api-key`, `zeus-obs/metoffice/api-key`). Neither is settlement-grade.
  - KNMI 10-min files sit on :00/:10/…, while EHAM METARs are :25/:55, so no instant is shared. The Tx
    overshoot is 73/176.
  - Met Office `gcpvj0` is an hourly :00 geohash, not station-bound to EGLC.
  - The KNMI adapter reads `os.environ["KNMI_API_KEY"]` (`station_temperature_adapters.py:546-548`). The
    data-ingest launchd environment does not set it, so every poll raises (Gap G3).
- **Planned dense-observation probability model (PLANNED, not live).**
  - Plan: `docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md`.
  - Theory: PR #536 `docs/authority/day0_dense_observation_probability.md` on branch
    `theory/dense-obs-settlement-20261007` (2ecf1de71, OPEN, unmerged). Its status line is "PROPOSED … no
    change to live probability authority".
  - Backtest: `artifacts/fast_obs_audit/dense_station_model/METHOD.md` §7.2 (bc024fedd).

  | City | Verdict | Basis |
  |---|---|---|
  | Toronto | FLOOR_USABLE | ECCC = METAR 7416/7416; SWOB +5.5 min vs AWC |
  | Tokyo | NOWCAST_ONLY | state-space B beats A out of sample |
  | Helsinki | NOWCAST_ONLY | state-space B beats A out of sample |
  | Singapore | NOWCAST_ONLY | state-space B beats A out of sample; Zeus has no NEA channel |
  | Munich | NOT_USEFUL | arrives 27 min after AWC |
  | Warsaw | NOT_USEFUL | arrives 13 min after AWC |
  | Amsterdam | NOWCAST_ONLY | on 8 days only |
  | Madrid | – | no dense archive |

  The ship gate is pending an operator decision: STRICT vs the model-expected high-confidence gate.

## 4. Per-city matrix

Legend:

- **Fast-channel classes.** FAST_ADMISSION, PHYSICAL_ONLY, KMA_EVENT, HKO_1MIN, AWC_METAR (§2). `pcs:N` =
  `config/physical_current_sources.json:N`.
- **Algorithm codes.**
  - B-abs: absorbing settlement boundary, available intraday.
  - B-post: absorbing boundary from the NOAA page, but the page is fetched only after the local day ends (G5).
  - B-stat: settlement channel as statistical evidence (WU, HKO).
  - FA: fast-admission route counts as settlement fact.
  - POS: `physical_only_statistical` fallback when no settlement fact exists.
  - RL: AWC residual likelihood (fast tail).
  - KMA: KMA priority event.
  - CS: current-state anchor.
  - DENSE PLANNED: §3 verdict.
- **Writer counts.** `observation_prints` rows with `fetched_at_utc` within 48 h before 2026-10-07T07:47Z, from
  §6 query 1. KMA counts are `DAY0_EXTREME_UPDATED` events with `kma_amo_raw_metar`, from §6 query 2.
- **Status.** OK, or MISMATCH plus the Gap ids (§5).

| City | Station | Settlement source type | Unit | Settlement channel | Fast channels (class) | Algorithms | Live writer, 48 h rows | Status |
|---|---|---|---|---|---|---|---|---|
| Amsterdam | EHAM | noaa (from wu_icao, 2026-08-24) `cities.json:6` | °C | `noaa_wrh_eham` after local day end only | `aviationweather_metar` AWC_METAR; `knmi_station_temperature` PHYSICAL_ONLY `pcs:779` | B-post; POS; RL; CS; DENSE PLANNED: NOWCAST_ONLY, 8 days only | N (noaa_wrh_eham 96, ogimet 96, AWC 160, knmi 0) | MISMATCH (G3, G5) |
| Ankara | LTAC | noaa (from wu_icao, 2026-08-24) `cities.json:33` | °C | `noaa_wrh_ltac` after local day end only | `aviationweather_metar` AWC_METAR; `mgm_metar_temperature` FAST_ADMISSION `pcs:1292` | B-post; FA (q unconditioned, G1); RL; CS | Y (noaa_wrh_ltac 96, ogimet 96, AWC 163, mgm 98) | MISMATCH (G1, G5) |
| Atlanta | KATL | noaa (from wu_icao, 2026-08-23) `cities.json:60` | °F | `noaa_wrh_katl` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_katl 68, ogimet 63, AWC 117) | OK |
| Auckland | NZAA | wu_icao `cities.json:94` | °C | `wu_icao_history` | `aviationweather_metar` AWC_METAR | B-stat; RL; CS | Y (wu_icao_history 100, AWC 173) | OK (G7) |
| Austin | KAUS | noaa (from wu_icao, 2026-08-23) `cities.json:119` | °F | `noaa_wrh_kaus` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_kaus 50, ogimet 78, AWC 86) | OK |
| Beijing | ZBAA | noaa (from wu_icao, 2026-08-24) `cities.json:153` | °C | `noaa_wrh_zbaa` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zbaa 97, ogimet 97, AWC 172) | OK (G5) |
| Buenos Aires | SAEZ | noaa (from wu_icao, 2026-08-23) `cities.json:181` | °C | `noaa_wrh_saez` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_saez 48, ogimet 48, AWC 85) | OK (G5) |
| Busan | RKPK | noaa (from wu_icao, 2026-08-24) `cities.json:210` | °C | `noaa_wrh_rkpk` after local day end only | `aviationweather_metar` AWC_METAR; KMA_EVENT | B-post; POS; RL; KMA (held-exposure only); CS | Y (noaa_wrh_rkpk 48, ogimet 48, AWC 82, KMA events 14 (target 10-05 only)) | OK (G4, G5) |
| Cape Town | FACT | noaa (from wu_icao, 2026-08-24) `cities.json:238` | °C | `noaa_wrh_fact` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_fact 53, ogimet 51, AWC 105) | OK (G5) |
| Chengdu | ZUUU | noaa (from wu_icao, 2026-08-24) `cities.json:265` | °C | `noaa_wrh_zuuu` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zuuu 48, ogimet 48, AWC 85) | OK (G5) |
| Chicago | KORD | noaa (from wu_icao, 2026-08-23) `cities.json:292` | °F | `noaa_wrh_kord` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_kord 50, ogimet 72, AWC 65) | OK |
| Chongqing | ZUCK | noaa (from wu_icao, 2026-08-24) `cities.json:326` | °C | `noaa_wrh_zuck` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zuck 48, ogimet 48, AWC 85) | OK (G5) |
| Dallas | KDAL | noaa (from wu_icao, 2026-08-23) `cities.json:353` | °F | `noaa_wrh_kdal` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_kdal 50, ogimet 81, AWC 72) | OK |
| Denver | KBKF | noaa (from wu_icao, 2026-08-23) `cities.json:387` | °F | `noaa_wrh_kbkf` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_kbkf 48, ogimet 70, AWC 81) | OK |
| Guangzhou | ZGGG | noaa (from wu_icao, 2026-08-24) `cities.json:421` | °C | `noaa_wrh_zggg` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zggg 96, ogimet 96, AWC 170) | OK (G5) |
| Helsinki | EFHK | noaa (from wu_icao, 2026-08-24) `cities.json:449` | °C | `noaa_wrh_efhk` after local day end only | `aviationweather_metar` AWC_METAR; `fmi_airport_temperature` PHYSICAL_ONLY `pcs:13` | B-post; POS; RL; CS; DENSE PLANNED: NOWCAST_ONLY, B beats A | Y (noaa_wrh_efhk 96, ogimet 96, AWC 169, fmi 294) | MISMATCH (G1, G5) |
| Hong Kong | HKO | hko `cities.json:476` | °C | `hko_hourly_accumulator` intraday; `hko_daily_api` final | `hko_current_1min_mean` HKO_1MIN | B-stat (HKO official extrema); CS (1-min mean) | Y (hko_current_1min_mean 283, hko_rhrread_spot 47) | OK |
| Houston | KHOU | noaa (from wu_icao, 2026-08-23) `cities.json:505` | °F | `noaa_wrh_khou` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_khou 55, ogimet 75, AWC 75) | OK |
| Istanbul | LTFM | noaa `cities.json:539` | °C | `noaa_wrh_ltfm` after local day end only | `aviationweather_metar` AWC_METAR; `mgm_metar_temperature` FAST_ADMISSION `pcs:1385` | B-post; FA (q unconditioned, G1); RL; CS | Y (noaa_wrh_ltfm 96, ogimet 96, AWC 175, mgm 98) | MISMATCH (G1, G5) |
| Jinan | ZSJN | wu_icao `cities.json:564` | °C | `wu_icao_history` + `wu_station_history_temperature` (canonical_resolver) | `aviationweather_metar` AWC_METAR configured, excluded (no writer); `wu_station_current_temperature` PHYSICAL_ONLY `pcs:829` | B-stat (q unconditioned, G1); CS | N (wu_icao_history 56, AWC 0, wu 282, wu_station_history 39) | MISMATCH (G1, G3, G7) |
| Jakarta | WIHH | wu_icao `cities.json:589` | °C | `wu_icao_history` | `aviationweather_metar` AWC_METAR | B-stat; RL; CS | Y (wu_icao_history 80, AWC 143) | OK (G7) |
| Jeddah | OEJN | noaa (from wu_icao, 2026-08-24) `cities.json:614` | °C | `noaa_wrh_oejn` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_oejn 48, ogimet 48, AWC 83) | OK (G5) |
| Karachi | OPKC | noaa (from wu_icao, 2026-08-24) `cities.json:642` | °C | `noaa_wrh_opkc` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_opkc 95, ogimet 96, AWC 162) | OK (G5) |
| Kuala Lumpur | WMKK | noaa (from wu_icao, 2026-08-24) `cities.json:669` | °C | `noaa_wrh_wmkk` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_wmkk 96, ogimet 96, AWC 163) | OK (G5) |
| Lagos | DNMM | wu_icao `cities.json:697` | °C | `wu_icao_history` | `aviationweather_metar` AWC_METAR | B-stat; RL; CS | Y (wu_icao_history 44, AWC 80) | OK (G7) |
| London | EGLC | noaa (from wu_icao, 2026-08-24) `cities.json:722` | °C | `noaa_wrh_eglc` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_eglc 95, ogimet 95, AWC 131) | OK (G5) |
| Los Angeles | KLAX | noaa (from wu_icao, 2026-08-23) `cities.json:749` | °F | `noaa_wrh_klax` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_klax 50, ogimet 48, AWC 66) | OK |
| Lucknow | VILK | noaa (from wu_icao, 2026-08-24) `cities.json:785` | °C | `noaa_wrh_vilk` after local day end only | `aviationweather_metar` AWC_METAR, margin 7; `imd_olbs_metar_temperature` FAST_ADMISSION `pcs:1204` | B-post; FA (q unconditioned, G1); RL; CS | Y (noaa_wrh_vilk 96, ogimet 101, AWC 163, imd 95) | MISMATCH (G1, G5, G6) |
| Madrid | LEMD | noaa (from wu_icao, 2026-08-24) `cities.json:812` | °C | `noaa_wrh_lemd` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS; DENSE PLANNED: no dense archive | Y (noaa_wrh_lemd 109, ogimet 98, AWC 205) | OK (G5) |
| Manila | RPLL | noaa (from wu_icao, 2026-08-24) `cities.json:839` | °C | `noaa_wrh_rpll` after local day end only | `aviationweather_metar` AWC_METAR, margin 2 | B-post; POS; RL; CS | Y (noaa_wrh_rpll 43, ogimet 48, AWC 96) | OK (G5) |
| Mexico City | MMMX | noaa (from wu_icao, 2026-08-23) `cities.json:866` | °C | `noaa_wrh_mmmx` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_mmmx 92, ogimet 88, AWC 118) | OK (G5) |
| Miami | KMIA | noaa (from wu_icao, 2026-08-23) `cities.json:895` | °F | `noaa_wrh_kmia` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_kmia 52, ogimet 50, AWC 72) | OK |
| Milan | LIMC | noaa (from wu_icao, 2026-08-24) `cities.json:929` | °C | `noaa_wrh_limc` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_limc 96, ogimet 96, AWC 134) | OK (G5) |
| Moscow | UUWW | noaa `cities.json:957` | °C | `noaa_wrh_uuww` after local day end only | `aviationweather_metar` AWC_METAR; `metaviatelecom_metar_temperature` FAST_ADMISSION `pcs:1096` | B-post; FA (q unconditioned, G1); RL; CS | Y (noaa_wrh_uuww 96, ogimet 97, AWC 213, metaviatelecom 39) | MISMATCH (G1, G5) |
| Munich | EDDM | noaa (from wu_icao, 2026-08-24) `cities.json:982` | °C | `noaa_wrh_eddm` after local day end only | `aviationweather_metar` AWC_METAR; `dwd_cdc_temperature` PHYSICAL_ONLY `pcs:487` | B-post; POS; RL; CS; DENSE PLANNED: NOT_USEFUL (+27 min after AWC) | Y (noaa_wrh_eddm 96, ogimet 96, AWC 165, dwd 284) | OK (G5) |
| NYC | KLGA | noaa (from wu_icao, 2026-08-23) `cities.json:1010` | °F | `noaa_wrh_klga` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_klga 53, ogimet 51, AWC 70) | OK |
| Panama City | MPMG | noaa (from wu_icao, 2026-08-23) `cities.json:1048` | °C | `noaa_wrh_mpmg` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_mpmg 69, ogimet 47, AWC 88) | OK (G5) |
| Paris | LFPB | noaa (from wu_icao, 2026-08-24) `cities.json:1076` | °C | `noaa_wrh_lfpb` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_lfpb 96, ogimet 96, AWC 172) | OK (G5) |
| Qingdao | ZSQD | noaa (from wu_icao, 2026-08-24) `cities.json:1103` | °C | `noaa_wrh_zsqd` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zsqd 48, ogimet 48, AWC 84) | OK (G5) |
| San Francisco | KSFO | noaa (from wu_icao, 2026-08-23) `cities.json:1131` | °F | `noaa_wrh_ksfo` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_ksfo 50, ogimet 48, AWC 85) | OK |
| Sao Paulo | SBGR | noaa (from wu_icao, 2026-08-23) `cities.json:1167` | °C | `noaa_wrh_sbgr` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_sbgr 60, ogimet 61, AWC 106) | OK (G5) |
| Seattle | KSEA | noaa (from wu_icao, 2026-08-23) `cities.json:1196` | °F | `noaa_wrh_ksea` intraday (60 s resolver route) | `aviationweather_metar` AWC_METAR (°F T-group only), margin 2 | B-abs; RL; CS | Y (noaa_wrh_ksea 75, ogimet 71, AWC 121) | OK |
| Seoul | RKSI | noaa (from wu_icao, 2026-08-24) `cities.json:1230` | °C | `noaa_wrh_rksi` after local day end only | `aviationweather_metar` AWC_METAR; KMA_EVENT | B-post; POS; RL; KMA (held-exposure only); CS | Y (noaa_wrh_rksi 96, ogimet 96, AWC 154, KMA events 96 (target 10-06 only)) | OK (G4, G5) |
| Shanghai | ZSPD | noaa (from wu_icao, 2026-08-24) `cities.json:1257` | °C | `noaa_wrh_zspd` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zspd 96, ogimet 96, AWC 175) | OK (G5) |
| Shenzhen | ZGSZ | noaa (from wu_icao, 2026-08-24) `cities.json:1284` | °C | `noaa_wrh_zgsz` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zgsz 48, ogimet 48, AWC 81) | OK (G5) |
| Singapore | WSSS | noaa (from wu_icao, 2026-08-24) `cities.json:1312` | °C | `noaa_wrh_wsss` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS; DENSE PLANNED: NOWCAST_ONLY, B beats A (no Zeus NEA channel) | Y (noaa_wrh_wsss 96, ogimet 96, AWC 180) | OK (G5) |
| Taipei | RCSS | wu_icao `cities.json:1340` | °C | `wu_icao_history` | `aviationweather_metar` AWC_METAR | B-stat; RL; CS | Y (wu_icao_history 91, AWC 160) | OK |
| Tel Aviv | LLBG | noaa `cities.json:1366` | °C | `noaa_wrh_llbg` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_llbg 96, ogimet 97, AWC 148) | OK (G5) |
| Tokyo | RJTT | noaa (from wu_icao, 2026-08-24) `cities.json:1393` | °C | `noaa_wrh_rjtt` after local day end only | `aviationweather_metar` AWC_METAR; `jma_amedas_temperature` FAST_ADMISSION `pcs:272` | B-post; FA (q unconditioned, G1); RL; CS; DENSE PLANNED: NOWCAST_ONLY, B beats A | Y (noaa_wrh_rjtt 96, ogimet 96, AWC 171, jma 293) | MISMATCH (G1, G2, G5, G8) |
| Toronto | CYYZ | noaa (from wu_icao, 2026-08-23) `cities.json:1420` | °C | `noaa_wrh_cyyz` after local day end only | `aviationweather_metar` AWC_METAR; `eccc_swob_temperature` FAST_ADMISSION `pcs:357` | B-post; FA (q unconditioned, G1); RL; CS; DENSE PLANNED: FLOOR_USABLE | Y (noaa_wrh_cyyz 48, ogimet 48, AWC 91, eccc 48) | MISMATCH (G1, G5, G8) |
| Warsaw | EPWA | noaa (from wu_icao, 2026-08-24) `cities.json:1448` | °C | `noaa_wrh_epwa` after local day end only | `aviationweather_metar` AWC_METAR; `imgw_synop_temperature` PHYSICAL_ONLY `pcs:442` | B-post; POS; RL; CS; DENSE PLANNED: NOT_USEFUL (+13 min after AWC) | Y (noaa_wrh_epwa 96, ogimet 96, AWC 175, imgw 48) | OK (G5) |
| Wellington | NZWN | noaa (from wu_icao, 2026-08-24) `cities.json:1476` | °C | `noaa_wrh_nzwn` after local day end only | `aviationweather_metar` AWC_METAR, margin 2 | B-post; POS; RL; CS | Y (noaa_wrh_nzwn 96, ogimet 96, AWC 174) | OK (G5) |
| Wuhan | ZHHH | noaa (from wu_icao, 2026-08-24) `cities.json:1503` | °C | `noaa_wrh_zhhh` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zhhh 48, ogimet 48, AWC 85) | OK (G5) |
| Zhengzhou | ZHCC | noaa (from wu_icao, 2026-08-23) `cities.json:1530` | °C | `noaa_wrh_zhcc` after local day end only | `aviationweather_metar` AWC_METAR | B-post; POS; RL; CS | Y (noaa_wrh_zhcc 48, ogimet 48, AWC 82) | OK (G5, G7) |

### 4.1 Cities per class

| Class | Cities | Count |
|---|---|---|
| SETTLEMENT_CHANNEL, absorbing intraday (B-abs) | Atlanta, Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Miami, NYC, San Francisco, Seattle | 11 |
| SETTLEMENT_CHANNEL, absorbing after local day end only (B-post) | the other 37 `noaa` cities | 37 |
| SETTLEMENT_CHANNEL, statistical (B-stat) | Auckland, Jakarta, Jinan, Lagos, Taipei (WU); Hong Kong (HKO) | 6 |
| FAST_ADMISSION | Tokyo, Toronto, Moscow, Lucknow, Ankara, Istanbul | 6 |
| KMA_EVENT | Seoul, Busan | 2 |
| HKO_1MIN | Hong Kong | 1 |
| AWC_METAR configured | every city except Hong Kong | 53 |
| AWC_METAR active (writer and residual-eligible) | the 53 minus Jinan | 52 |
| AWC residual likelihood actually served in 48 h | the 52 minus Auckland, Jakarta, Lagos, Zhengzhou (no markets) and Ankara, Istanbul, Lucknow (route value = AWC value, so the tail never supersedes) | 45 |
| PHYSICAL_ONLY configured | Helsinki, Warsaw, Munich, Amsterdam, Jinan | 5 |
| PHYSICAL_ONLY writing | the 5 minus Amsterdam | 4 |
| POS fallback reachable | `noaa` cities without an intraday settlement fact: 37 B-post minus the 6 FAST_ADMISSION | 31 |
| NONE (only settlement product + AWC) | 54 − 6 FA − 2 KMA − 1 HKO − 5 PHYSICAL_ONLY | 40 |
| DENSE PLANNED verdict recorded | Toronto, Tokyo, Helsinki, Singapore, Munich, Warsaw, Amsterdam, Madrid | 8 |
| Status MISMATCH | Amsterdam, Ankara, Helsinki, Istanbul, Jinan, Lucknow, Moscow, Tokyo, Toronto | 9 |

## 5. Gaps

**G1 — A fast-admission or physical-only route chosen as the Day0 source drops the observation from q.**

- **Affected cities.**
  - Tokyo, Toronto, Moscow, Lucknow, Ankara, Istanbul: the six FAST_ADMISSION cities.
  - Helsinki: an FMI value can win the physical MAX.
  - Jinan: `wu_icao_history` is selected because Jinan has no AWC.
- **Cause.**
  - `day0_evidence_finality` knows only `noaa_wrh_*` as a monotone settlement source (`src/events/day0_authority.py:316-320`).
    Every registry route channel returns `UNKNOWN`. `wu_*` returns PROVISIONAL.
  - `day0_is_carrier_source` (`:256-272`) admits none of these channels.
  - So the materializer finds no absorbing extreme (`replacement_forecast_materializer.py:847-864`) and no carrier
    (`:2376-2386`). It records `day0_provisional_observation` but builds `q_shape = fused_normal_direct` with
    `day0_obs_extreme_c = None` (`:7994-7998`).
  - The adapter mask also needs an absorbing finality (`event_reactor_adapter.py:50735-50738`).
- **Mismatch.** The registry declares these routes value-identical to the resolver (`settlement_authorized`). The
  settlement fact admits them (`replacement_forecast_current_target_plan.py:1463-1469`). Yet no consumer gives
  them boundary or carrier semantics. The routes were admitted to set B early. In practice they remove the
  observed extreme from q.
- **Live evidence** (FORECASTS read-only, 48 h to 2026-10-07T07:57Z):
  - FA-sourced posteriors: Tokyo 447, Lucknow 362, Toronto 209, Ankara 160, Istanbul 155, Moscow 44. All 1,075
    have `q_shape = fused_normal_direct` with `replacement_q_mode` FUSED_NORMAL_FULL (977) or PARTIAL (98).
  - Helsinki: 11 rows from `fmi_airport_temperature`.
  - Jinan: 662 rows from `wu_icao_history`, last 2026-09-29.
  - The same pattern holds every day from 2026-10-01 to 2026-10-07.
- **Example.** Tokyo 2026-10-07 HIGH, posterior 760918 at 07:59Z.
  - The observed extreme was 25.2 °C from `jma_amedas_temperature`, observed 06:00Z (a METAR instant).
  - The settlement bin is therefore ≥ 25.
  - The posterior still gave q = 0.64 to the bins ≤ 24 °C: q(24) = 0.29, q(23) = 0.22, q(22) = 0.10.
  - Forty minutes earlier, the AWC fast-tail posterior 759758 had q(25) = 0.99.

**G2 — Tokyo's fast-admission route publishes at 10-min cadence; its proof covers METAR instants only.**

- **Cadence.**
  - `jma_amedas` parses every AMeDAS sample (`station_temperature_adapters.py:421-427`). The live minute
    histogram (48 h) is 48 rows each at :00, :10, :20, :30, :40 and :50.
  - The RJTT METAR is :00/:30.
  - The identity proof (152/152) pairs METAR instants only.
- **Effect.** Non-METAR readings enter the settlement fact.
- **Evidence.** Replay of `_latest_authorized_day0_fact(Tokyo, 2026-10-04, low, require_settlement_channel=True)`
  at 2026-10-04T15:30Z returns `jma_amedas_temperature` 18.4 observed 20:40Z, a non-METAR instant.
  - R(18.4) = 18.
  - The VERIFIED settlement is 19 (WRH; `settlements` id 1777161).
  - Every Tokyo 10-04 LOW posterior computed after 20:47Z used 18.4 as the observed extreme (144 rows).
- **Context.** The dense-station audit measures the same cadence effect: Tokyo high cadence days 25/203; JMA daily
  table over 71/202. G1 hides the error from q today. Fixing G1 alone would make it a false absorbing low.

**G3 — Configured channels with no live writer.**

- **Amsterdam `knmi_station_temperature`.**
  - The channel has 0 rows ever in `observation_prints`.
  - `zeus-ingest.err` has 3,781 `PHYSICAL_CURRENT_FETCH_FAILED station=EHAM error=ValueError` lines. They span the
    whole retained log, 2026-10-04 00:11 to 2026-10-07 03:08 local.
  - The adapter raises `ValueError("KNMI_API_KEY_UNAVAILABLE")` when the environment variable is absent
    (`station_temperature_adapters.py:546-548`). The data-ingest launchd environment sets no `KNMI_API_KEY`
    (variable names checked, values not read). The key exists only in the keychain.
  - The log keeps only the error class, so this cause is inferred, not printed.
- **Jinan `aviationweather_metar`.**
  - The channel is configured in the WU channel sets (`day0_hourly_vectors.py:4437-4438`;
    `replacement_forecast_current_target_plan.py:1406-1408`).
  - But `fast_obs_source_for_city` excludes Jinan (thin divergence sample, 4 pairs), so no writer runs: 0 rows in
    48 h.
- **Non-gaps.** Intermittent fetch failures do not stop other routes. Over the same log, there are 525 VILK failures
  and about 280–303 per US resolver route. Their 48-h row counts are all non-zero.
- **Resolution G3a (Amsterdam), `fix/fast-obs-gaps-g3-g7` ee8d369da.**
  - `resolve_knmi_api_key` reads env `KNMI_API_KEY` first, then the gitignored `config/knmi_secret.json`
    (`{"knmi_api_key": ...}`). This is the `resolve_cwa_api_key` pattern. The key never enters a log, error or
    print. Tests pin the order and cover the adapter and ingest failure logs.
  - The parse path imports `netCDF4` (`station_temperature_adapters.py:458`), and the live `.venv` lacks it.
    `requirements.txt` now pins `netCDF4==1.7.4` and `cftime==1.6.6`. **Operator action:** install both into
    `.venv`. Until then the route still fails soft as `SOURCE_UNAVAILABLE`, now with `ModuleNotFoundError` instead
    of `ValueError`.
  - Role unchanged, `physical_only`. KNMI's 10-min grid shares no instant with EHAM METARs (:25/:55), so it is
    dense physical evidence, never a settlement fact. G1 does not apply: G1 needs a route the registry calls
    `settlement_authorized`.
- **Resolution G3b (Jinan): neither (a) nor (b). No writer is added and the config is unchanged; the reasons follow.**
  - (a), enabling a Jinan AWC writer, is refuted.
    - The AWC poll is one batched request (`fetch_metar_reports`, `ids=…`), so adding the station costs nothing.
    - But the writer runs only where `fast_obs_source_for_city` returns a source, and that needs an empirical
      margin (≥ 40 pairs). Forcing Jinan in would serve margin 0.0 on 4 pairs, the fail-open removed on
      2026-07-26 (Shenzhen class).
    - ZSJN also publishes no METAR to the global feed (checked 2026-10-07T09:59Z):
      - AWC returned 0 ZSJN reports over 168 h; ZHCC in the same request returned 36;
      - the NOAA `ZSJN.TXT` station file was last written 2026-08-24;
      - the IEM archive has 13 ZSJN reports in August and none after;
      - the last Jinan AWC print in WORLD is 2026-07-25.

      A writer would write nothing and accumulate no pairs.
  - (b), removing `aviationweather_metar` from the WU channel sets, is refuted by the code's contracts.
    - The current-state set (`day0_current_temperature_channels`) is a reader admission list, never a boundary,
      and it applies no margin (§1).
    - `test_day0_remaining_day_pricing.py::test_current_temperature_selects_latest_causal_observation` pins AWC
      admission for a wu_icao city with no margin entry. Gating the set on the writer predicate failed all 8
      cases (draft branch, not landed).
    - The target-plan physical set already drops AWC wherever the margin is None
      (`replacement_forecast_current_target_plan.py:1577-1584`), so Jinan AWC can never form a physical fact.
  - Net effect: a listed channel with no rows is inert. The cause is an upstream outage at ZSJN, not a Zeus
    writer gap. If ZSJN reappears on AWC, Jinan still needs an empirical margin refit before its writer runs.
    That refit is G6's producer, which today carries Jinan's WU-era 4-pair entry.

**G4 — KMA_EVENT runs only for held exposure.**

- **Mechanism.** The KMA poll covers priority stations only. Those are held families in `day0_window` or
  `pending_exit` (`src/ingest_main.py:322-342`; `replacement_forecast_seed_discovery.py:890`).
- **Evidence.** Within 48 h, KMA events exist only for:
  - Busan target 2026-10-05 (14 events, last received 2026-10-05T14:01Z);
  - Seoul target 2026-10-06 (96).

  Seoul 10-07 and Busan 10-06/10-07 carry only legacy AWC transport.
- **Status.** Unclear class: the "priority path" is a held-position path, not a per-city path. Entry decisions in
  Seoul/Busan use AWC and Ogimet.
- **Resolution, `fix/fast-obs-gaps-g3-g7` 9c8a58613.**
  - `_reports_with_status` now polls KMA for every eligible fast-lane station in `KMA_PRIORITY_STATIONS`
    (RKSI, RKPK), held or not.
  - Quota:
    - The AMO endpoint is a public keyless form. No quota or terms are recorded in code, `architecture/` or docs.
    - `KmaMetarCursor` already limits each station to one request per 60 s, with two workers. The ceiling is
      2 POSTs/min, the rate a held Seoul+Busan pair already drew.
  - Emitted events still pass family admission: a listed market or held exposure.
  - Tests fail on 24d65cb46 and pass on the fix: entry-only and held rounds both poll both stations; a non-KMA
    station never reaches the cursor; an unheld Seoul prefetch reaches KMA.
  - Existing transport noise is unchanged by this fix: 68 `KMA_METAR_FETCH_FAILED` in the retained log
    (ConnectError/TLS hostname mismatch 40, ConnectTimeout 15, ReadTimeout 13).

**G5 — For the 37 non-US NOAA cities, B is absent during the Day0 window.**

- **Mechanism.** `noaa_wrh_<icao>` is written only by `daily_tick`, after the local day ends + 1 h
  (`daily_obs_append.py:1772`, `:2607-2630`).
  - Example: Tokyo's last page row was fetched 2026-10-06T19:05Z, for the day that ended 15:00Z.
  - Only the 11 US routes poll the page every 60 s.
- **Evidence.** Absorbing `day0_conditioning` posteriors in 48 h exist only in the 11 US cities, plus Manila
  (27 rows, 2026-10-05T17:06–17:59Z, after that local day ended).
- **Status.** Not a code defect: it follows from the per-IP volume law in `_daily_coverage_row_needs_fetch`
  (`daily_obs_append.py:1812-1826`). But the class
  definition "the settlement product sets B" holds intraday for 11 cities only. Elsewhere, Day0 runs on AWC, the
  fast tail and the POS fallback.

**G6 — Lucknow AWC margin 7.0 contradicts the later agreement audit.**

- `config/wu_metar_divergence.json` (2026-09-17): 48 pairs, max |Δ| 6, `settlement_faithful: false`, threshold 7.0.
- The physical fact shifts every Lucknow AWC print 7 °C toward the absorbing side
  (`replacement_forecast_current_target_plan.py:1570-1584`). AWC therefore never wins the Lucknow physical fact.
- The daily-extreme agreement audit shows AWC vs settled Lucknow high 62/63 equal (one −2) and low 46/46.
- **Impact today.** The IMD route supplies the Lucknow settlement and physical facts, so the margin only matters
  when IMD is missing. The fast tail compares raw AWC, without margin, against the IMD value. They are equal, so it
  never fires: 0 Lucknow fast-tail posteriors in 48 h.
- **Status.** Unclear: the margin measurement needs re-running on the current window.
- **Resolution: re-measured. The margin stays 7.0, which is correct. The regenerated artifact is not committed.**
  - Canonical producer: `scripts/measure_settlement_page_metar_divergence.py`. It wrote the current artifact at
    755dad929 and is listed in `db_writer_lock.py:832`. It was re-run on 2026-10-07T10:17Z with `--since 2026-08-23`,
    reading the forecasts DB `mode=ro` and writing to scratch.
  - Lucknow: 88 pairs, p99 |Δ| 6, threshold 7.0, `settlement_faithful` false. All three are unchanged.
  - The single driver is 2026-09-06 HIGH: the page shows 31 (VERIFIED settlement 31) and the Ogimet daily shows 37.
    - The 37 is a real report, `VILK 060730Z … 37/27`, published by AWC at 07:36Z.
    - AWC republished the same 060730Z report as 27/27 at 07:56Z. Ogimet first wrote 27, then 37 on 09-09.
  - At n ≤ 100, p99 is the sample maximum, so one corrupt print sets the margin. The margin is doing its job: a raw
    37 would have been an absorbing false HIGH.
  - Why the agreement audit disagrees: it collapses each instant to its last version
    (`awc_metar_baseline.py`, "Duplicates by instant collapse (last wins)"). It therefore saw 31 for 09-06. Live
    readers do not collapse that way, so the audit does not refute the margin.
  - Not committed: the script rewrites `carried_from_method` on the five carried wu_icao cities (Auckland, Jakarta,
    Jinan, Lagos, Taipei). It replaces the WU-era method string with its own page-vs-Ogimet `method`. A second run
    carries forward the previous run's `method`, so those entries would claim a measurement that never covered
    them. Their numbers are unchanged.
  - Served-margin changes the re-run would make (NOAA cities, 45-day window):

    | City | before | after | p99 driver (page vs Ogimet, rounded Δ) |
    |---|---|---|---|
    | Denver | 2.0 | 5.0 | 2026-09-23 LOW 58.1 °F vs 54.32 °F (Δ 4) |
    | Beijing | 0.0 | 3.0 | 2026-09-20 HIGH 28 vs 30 |
    | Madrid | 0.0 | 3.0 | 2026-09-20 LOW 16 vs 14 |
    | Ankara | 0.0 | 2.0 | 2026-09-20 LOW 14 vs 13 |
    | Guangzhou | 0.0 | 2.0 | 2026-09-20 HIGH 36 vs 37 |
    | Qingdao | 0.0 | 2.0 | 2026-09-20 HIGH 29 vs 30 |
    | Singapore | 0.0 | 2.0 | 2026-09-20 and 09-23 HIGH, Δ −1 |
    | Tel Aviv | 0.0 | 2.0 | 2026-09-20 LOW 23 vs 22 |

    Every other city keeps its served margin. Pair counts rise from 48–52 to 88–90.
  - Seven of the eight flips sit on one target date, 2026-09-20, in seven unrelated countries. That pattern
    suggests a one-day Ogimet or page ingest artifact rather than station divergence. It is UNVERIFIED and was not
    traced here.
  - Because p99 equals the maximum at n < 101, each changed threshold rests on one or two days. Committing a refit
    needs two things first: the carry-provenance fix in the script, and a check of the 2026-09-20 rows.

**G7 — No live market, so the matrix row cannot be verified by posteriors.**

- Auckland: no `market_events` rows.
- Jakarta: last market 2026-05-21.
- Lagos: last market 2026-05-15.
- Jinan: last market 2026-09-29.
- Zhengzhou: last market 2026-10-01.

Their channels write (except Jinan AWC, G3), but no Day0 posterior exists to confirm the algorithm.

**G8 — Toronto and Tokyo: a raw-precision comparison fires the fast tail on equal settlement integers.**

- Replay at 2026-10-07T08:05Z:
  - settlement fact `eccc_swob_temperature` 12.5 at 05:00Z;
  - physical fact `aviationweather_metar` 13.0 at the same 05:00Z.
- Both round half-up to 13. But `fast_extreme_supersedes_settlement` compares raw °C values
  (`day0_fast_obs.py:912-924`), so the AWC tail "advances" the settlement value. Live: 26 Toronto fast-tail
  posteriors in 48 h.
- Tokyo shows the same thing. Replay at 2026-10-07T05:17Z:
  - settlement fact `jma_amedas_temperature` 24.6 at 05:00Z;
  - fast tail AWC 25.0 for the same 05:00Z METAR.

  Both are 25 after rounding. The tail produced the 05:05–05:17Z posteriors in the G1 example (115 Tokyo fast-tail
  posteriors in 48 h).
- The same holds wherever a 0.1 °C route and a whole-degree METAR share an instant. The MGM, IMD and
  metaviatelecom routes carry METAR integers.
- **Status.** Unclear class: the "advances settlement" test is not in settlement-integer space. Today it partly masks
  G1 for these two cities.

## 6. Reproduce

All queries are read-only (`?mode=ro`, `PRAGMA query_only`).

1. Writers, 48 h: `SELECT source_channel, station_id, count(*), max(fetched_at_utc) FROM observation_prints
   WHERE city=? AND publish_ts_utc >= <now−9d> AND fetched_at_utc >= <now−48h> GROUP BY 1,2` on
   `state/zeus-world.db`. The plan uses `idx_observation_prints_city_publish`.
2. KMA events: `SELECT json_extract(payload_json,'$.city'), json_extract(payload_json,'$.target_date'), source,
   count(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED' AND available_at >= <now−48h> AND
   json_extract(payload_json,'$.observation_transport')='kma_amo_raw_metar' GROUP BY 1,2,3`.
3. Served Day0 source and shape: group `forecast_posteriors` (`state/zeus-forecasts.db`, `runtime_layer='live'`)
   over the last 48 h by `json_extract(provenance_json,'$.day0_provisional_observation.source')`,
   `json_extract(provenance_json,'$.day0_conditioning.source')` and `q_shape`.
4. Route load: `load_physical_current_sources()` and `physical_current_sources_for_city(city)`, then
   `day0_current_temperature_channels(city)` per city.
5. Fact replay: `_latest_authorized_day0_fact(conn, …, require_settlement_channel=True/False)` and
   `latest_fast_station_conditioning` on a read-only WORLD connection.
