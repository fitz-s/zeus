# Source inventory and the probability race (2026-09-24)

Goal: every city, every lead, a probability that updates within seconds of any new
physical fact and reaches a decision before the market reprices. This document
records what each source physically is, how fast Zeus actually turns it into a
served probability (measured on live data, not configuration), which link in the
chain is the binding constraint, and what was fixed.

All numbers are read-only measurements from `state/zeus-forecasts.db`,
`state/zeus-world.db`, `state/openmeteo_quota.json` and `logs/zeus-ingest.*`
over 2026-09-19..24. Scripts live in the session scratchpad; queries are
reproducible from the column names cited.

---

## 1. Two different kinds of data

| | Forecast source | Observation source |
|---|---|---|
| What it is | A model's belief about a future state, issued at a cycle time | A measurement of a state that already happened |
| Identity | (provider, model, cycle `source_cycle_time`, valid time) | (station, report time `publish_ts_utc`, value) |
| Error structure | Grows with lead; biased per model/station/season; correlated across models of one provider family | Instrument + rounding + representativeness; revisable (SPECI, COR, provisional→final) |
| Role in q | Sets the centre and spread of the not-yet-observed part of the day | Truncates the outcome space: `H = max(H_obs, H_rem)`, `L = min(L_obs, L_rem)` |
| When it changes q | Once per model cycle (1 h–12 h cadence) | Every report (5 min–60 min); an absorbing extreme can move a bin to 0 or 1 at once |
| Race it creates | Forecast reversal: minutes to hours before the market reprices | Observation reversal: seconds |

Settlement truth is always an observation (NOAA WRH/Synoptic page for 48 cities,
WU ICAO history for 5, HKO official daily for Hong Kong). Forecasts never settle;
they only price what the observation has not yet fixed.

## 2. Forecast sources (live posterior inputs)

Horizon = `data_end_time − run` from Open-Meteo metadata on 2026-09-24. Lag = provider
availability − cycle, from `raw_model_forecasts.source_available_at` (7-day p50 / max).

| Model id | Provider / physics | Grid | Cycles | Horizon | Avail. lag p50 / max | Domain | Role |
|---|---|---|---|---|---|---|---|
| `ecmwf_ifs` (OM 9 km) | ECMWF IFS HRES | 0.1° | 00/06/12/18 | 145 h | 6.8 h / 11.8 h | global | anchor prior (μ0, τ0) |
| `ecmwf_open_data` ENS | ECMWF IFS ENS, 51 members, mx2t3/mn2t3 | 0.25° | 00/06/12/18 | 144 h used | +400–485 min | global | distribution shape (members) |
| `icon_global` | DWD ICON | 13 km | 00/06/12/18 | 181 h | 3.7 h / 10.9 h | global | likelihood instrument, DWD rep outside EU |
| `icon_eu` | DWD ICON-EU nest | 7 km | 3-hourly | 121 h | 3.6 h / 8.9 h | EU polygon | DWD rep in EU when icon_d2 ineligible |
| `icon_d2` | DWD ICON-D2 nest | 2 km | 3-hourly | 49 h | 1.5 h / 3.4 h | Central-EU polygon, lead ≤ 1 | DWD rep in-domain |
| `ukmo_global_deterministic_10km` | UK Met Office UM global | 10 km | 00/06/12/18 | 61 h | 7.7 h / 16.7 h | global | UKMO rep outside UK |
| `ukmo_uk_deterministic_2km` | UKV | 2 km | hourly | 13 h | 4.6 h / 7.3 h | UK (London) | UKMO rep in-domain |
| `ncep_nbm_conus` | NOAA National Blend | ~13 km per code comment (UNVERIFIED vs native 2.5 km) | hourly | 263 h | 1.1 h / 9.2 h | CONUS | NCEP rep when HRRR out of range |
| `gfs_hrrr` | NOAA HRRR | 3 km | hourly | 19 h (run 16Z) | 1.9 h / 2.3 h | CONUS | NCEP rep in-domain |
| `meteofrance_arome_france_hd` | Météo-France AROME HD | 1.3 km | 3-hourly | 52 h | 4.0 h / 5.8 h | France polygon | regional expert |
| `gem_hrdps_continental` | CMC HRDPS | 2.5 km | 00/06/18 seen | not in metadata cache | 6.8 h p50 | North America | CMC rep |
| `hko_fnd` | HKO 9-day station forecast | station | ~hourly refresh | 9 d | ≈0 | Hong Kong | station-calibrated likelihood |
| `cwa_township_hourly_{high,low}` | Taiwan CWA township forecast | township | irregular | 7 d | ≈0 | Taipei | station-calibrated likelihood |

Fetched but not in the live fusion basket: `jma_msm`, `nam_conus`, `dmi_harmonie_europe`,
`knmi_harmonie_netherlands`, `met_nordic`, `italiameteo_icon_2i` (candidate accrual /
history). `*_previous_runs` rows exist only to build the walk-forward bias history.
TIGGE is archive-only (48 h embargo); it trains the ENS calibrator, never serves.

Physical rules the fusion already enforces (`src/forecast/model_selection.py`): one
representative per provider family (ICON d2 > eu > global; HRRR > NBM; UKV > UKMO
global), regionals only inside their polygon, Ledoit–Wolf shrink toward a diagonal
Σ, EB bias shrinkage `λ = n/(n+8)`, anchor prior floor 0.8 °C.

## 3. Observation sources

| Source id | Physical measurement | Precision | Zeus poll | Measured report→Zeus (steady state) | Role |
|---|---|---|---|---|---|
| `aviationweather_metar` | METAR routine + SPECI (AWC API; NWS tgftp fallback; KMA AMO for RKPK/RKSI) | T-group 0.1 °C, else 1 °C; US F cities require T-group | 5 s | p50 0.3 min | Day0 conditioning for 53 cities |
| `wu_api+same_station_fast_tail` | WU current timeseries of the settlement station | 1 °F / 1 °C | 5 s lane | p50 0.3 min to posterior | Day0 conditioning, 5 WU cities |
| `hko_hourly_accumulator` | HKO since-midnight max/min of 1-min means | 0.1 °C | 2 s | p50 8.3 min to posterior (poll is 2 s, so the ~8 min is most likely HKO's own publication delay — UNVERIFIED) | Day0 conditioning, Hong Kong |
| `hko_rhrread_spot` | HKO hourly spot reading | 1 °C | hourly | 3 min | evidence only |
| `ogimet_metar_<icao>` | METAR archive | 0.1 / 1 °C | hourly :15 + 15 min | p50 11 h (archive lane) | history, training labels, NOAA fallback |
| `noaa_wrh_<icao>` | weather.gov WRH page (Synoptic) | whole unit as rendered | daily | p50 14 h | settlement truth, 48 cities |
| `wu_icao_history` | weather.com historical | 1 unit | hourly :15 | p50 18 min | settlement truth, 5 cities |
| `hko_daily_api` | HKO official daily extremes | 0.1 °C, floored at settlement | hourly + 300 s extract | days (#→C flips) | settlement truth, Hong Kong |

AWC's 24 h p90 of 97 min is not steady-state lag: 436 of its 454 late first-seen
reports were fetched in two bursts at 01:30Z and 08:27Z, the two data-ingest restarts,
as boot-time catch-up of already-past reports.

Day0 model in the materializer (`src/forecast/day0_conditioner.py`):
`Y = max(obs_high, X_rem)` with `X_rem ~ N(μ_rem, σ_rem)` for the remaining window,
centre shifted by the ECMWF hourly remaining-window delta. The provisional
NOAA/AWC/HKO carrier (`replacement_forecast_materializer.py:1307-1562`) uses complete
`day0_hourly_vectors` remaining-window members plus revision-survival scenarios.

## 4. What the race actually costs today (measured)

End-to-end latency from provider availability A to the first live posterior that
serves the new run, for scopes that were already live at A (onboarding excluded),
n = 15,256 (model, cycle, scope) events, 2026-09-19..24:

| | p10 | p50 | p75 | p90 | p99 |
|---|---|---|---|---|---|
| all forecast sources | 10.1 min | 15.8 min | 38.5 min | 114.6 min | 308.9 min |
| by day of A: 09-20 | | 15.3 | 27.8 | 68.3 | |
| 09-22 | | 17.6 | 48.5 | 88.6 | |
| 09-23 | | 17.7 | **103.0** | **266.0** | |
| 09-24 | | 14.6 | 69.0 | 148.4 | |

The p10 of ~10 min is the fixed `SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES = 10`
(`src/strategy/live_inference/source_clock_vnext.py:17`) plus one poll. The median is
~5 min above that floor. The tail is the problem, and it degraded sharply from 09-23.

Observations are already on a seconds clock: METAR/WU-fast report → Day0 posterior
p50 0.3–0.6 min, p90 ≤ 2.7 min.

Other measured facts:
- Live scopes are recomputed ~29 times/day; inter-posterior gap p50 61 min, p99 312 min.
- Around 18:00Z on 09-24, 71 of 152 ECMWF-served scopes were still on the 09-23 18Z
  ECMWF run while 06Z had been available since 12:21Z.
- Posterior triggers (24 h): instrument_set_expansion 3,879, day0_observation_advanced
  2,753, newer_cycle_ingested 640.

## 5. Root cause of the tail: the Open-Meteo quota burns on requests that can never succeed

`state/openmeteo_quota.json` (limit 10,000/day; source-clock priority lane capped at
9,000): the priority lane was exhausted at **05:31Z on 09-24** and at 12:40Z on 09-23.
Once it is exhausted, every newly published run returns
`source_clock_quota_abort:cooldown_seconds=46110`. The data-ingest scoped download for
**every** source reports `TRANSPORT_RETRYABLE` until the UTC reset. That is the 09-23
and 09-24 tail in §4.

Of today's metered cost, **92 % were repeats of byte-identical requests**. One request
identity was attempted 139 times and 7,006 of 7,625 units were repeats. What was
repeated, from the scoped download reports (09-23/24):

| Shape | Example | Repeats | Ever filled later |
|---|---|---|---|
| Older candidate run lacking late-day hours | `ukmo_global` 06Z for lead-3 targets (run superseded by 12Z) | 7,564 | 0 |
| Latest run of a short-horizon model beyond its metadata horizon | `gfs_hrrr` 03Z lead 0/1; `icon_d2` 03Z lead 2 (49 h); `arome` lead 2; `ukmo_uk` lead 2 (13 h) | 5,660 | 0 |

Of 201 distinct gap scopes (model, city, target date, exact run) logged 09-21..24,
**zero** were ever written later for the same run. These requests are structurally
impossible, not transient.

Why they repeat. Commit `e48103c3b` (09-23 02:14) made every parser gap retryable,
correctly for the latest run, which can still grow. Memoization then only covered
`metadata:data_end_time_before_target_end`, and only for the two backtrack models
(`icon_eu`, `ukmo_global`). Retryable gaps keep the per-source status
`TRANSPORT_RETRYABLE`. The source-clock cursor never advances (last advance
09-23 03:21 CDT), so the 15 s probe re-detects "new" runs forever and re-requests
the same impossible scopes. The cycle stops only when the quota runs out, and that
blocks the runs that do matter.

## 6. Fix landed in this task

`src/data/bayes_precision_fusion_download.py`, branch `task/source-clock-quota-burn-20260924`:

1. **Metadata horizon gate for every model.** When the run's own metadata
   `data_end_time` is known, the run can only serve a target whose local-day parser
   sample at `LOCALDAY_SPAN_LATE_HOUR` (20:00 local) exists. If
   `data_end_time <= target 20:00 local`, no request is sent. The scope stays a
   retryable structural gap, and the next metadata update makes a fresh decision.
   Before this, only `icon_eu`/`ukmo_global` had the check.
2. **A superseded run is final.** A horizon-shaped parser gap
   (`partial local-day coverage` / `insufficient hourly samples`) on a run strictly
   older than the model's current source-clock run is memoized as
   `superseded_run:…`. The provider has moved on, so those bytes will not change.
   Gaps on the latest run stay retryable.

Proof:
- Two new antibodies fail on the old code (`a proven out-of-horizon run must never
  hit the API`; `a superseded run's horizon gap must not be re-requested`) and pass
  on the fix.
- Two existing tests encoded the old retry-forever behaviour and were updated with
  the measured rationale.
- The 7-file affected suite has the same 17 baseline failures before and after
  (360 → 362 passed).
- The 23-file wide set of every test touching the module has the same 48 baseline
  failures before and after (972 passed).
- Ruff is clean.

Expected effect: removes the ~92 % repeat share of metered calls, so the
priority lane should no longer exhaust. Forecast availability→posterior p75/p90
should return from 69–103 / 148–266 min toward the 09-20 profile (28 / 68 min).
This needs the data-ingest daemon to restart onto the landed SHA. Measure it with
the §4 active-scope query after one full UTC day.

## 7. Known / unknown

**Known, with evidence**
- Provider dissemination dominates the forecast clock: ECMWF 6.8 h, UKMO 7.7 h, ICON
  3.7 h, NBM 1.1 h, HRRR 1.9 h after cycle. No pipeline change can beat these.
- Our own pipeline adds a fixed 10 min consistency wait plus a ~5 min median, and it
  had a quota-driven tail of hours (fixed above).
- Observations reach the Day0 posterior in 0.3–0.6 min p50 for METAR/WU, 8 min for
  HKO (provider-bound).

**Known unknowns: measurable next, ranked by expected alpha per effort**
1. *Is the 10 min consistency wait needed?* The replicas that caused it are real:
   Open-Meteo `meta.json` replicas report different `availability_time` for the
   same run (seen for 7 of 9 models on 09-24, up to 20 min apart). Candidate: accept
   the first replica that serves a **content-complete** payload for the exact run
   (hash-checked against the metadata horizon), instead of a fixed wait. Worth
   ≤ 10 min per run for every city. It needs a replica-consistency probe first.
2. *Scope-local recompute.* A new run of one model already reseeds only affected
   (city, metric, date) families, but the broad reseed still runs 14 s–4 min per
   batch (p50 61 s). Measure first-affected-posterior latency per source after the
   quota fix before touching it.
3. *Native vs API.* ECMWF Open Data arrives +400–485 min after cycle. Open-Meteo's
   IFS 9 km arrives +6.8 h p50. The anchor could be taken from whichever is first
   for the exact run, per lead. That needs a same-product equivalence check.
4. *Incremental information by city × metric × lead × source age.* Only after
   (1)–(3), because it needs a clean latency baseline to separate "arrived later"
   from "less informative".

**Unknown knowns (in code but not operating as intended)**
- The broad reseed cursor drain has produced `advanced_sources=()` on every drain
  since 09-23 08:52. The only drain that carried cursor candidates ran with
  `dirty=True`. This is independent of the quota fix and keeps the probe
  re-detecting every model. It is the next defect to fix (`src/ingest_main.py`
  `_enqueue_broad_reseed_batch` / `_run_broad_reseed_batches`).
- `source_health_open_meteo_archive` probes every 10 min on the maintenance lane
  (2,497 attempts on one request id). It is cheap per call, but it is a standing
  metered cost with no forecast value.

**Unknown unknowns: how we would find them**
- The active-scope latency table in §4, re-run daily by day of A, catches any new
  tail regression within a day.

## 8. Incident 2026-09-24: trading halt and Day0 losses

**Halt (17:41Z onward, 31/31 BUY submits rejected).** Commit `1cc60960c` edited only
prose in `architecture/capabilities.yaml`; `src/` and `config/` were unchanged since the
daemon booted at `4d5de532`. `deployment_freshness_mismatch` treated every
`architecture/` path as runtime code and revoked `live_venue_submit`. The same class
fired on 29 separate days since 2026-08-11 (1,247 rejections on 08-17 alone) because
each governance file had to be excluded by hand. Fixed in `ba4396a37`: only the seven
`architecture/` files that live code opens count as runtime, pinned by an antibody that
scans `src/` for loaded architecture paths.

**Day0 losses.** `day0_nowcast_entry`, 123 settled trades with target dates 09-17..09-23:
the model expected 85.7 wins, realized 37, PnL −135 USD. The entry price was the
better forecaster: at mean price 0.31 the realized win rate was 0.17 while the model
said 0.68. `forecast_qkernel_entry` over the same period was near-calibrated
(92 expected / 84 won, +25 USD).
- Observation/settlement channel mismatch: 3 of 123 (Beijing and Singapore on 09-20,
  HK 09-22). A METAR/WU body integer above the NOAA WRH `Math.round(Synoptic)` value
  was treated as an absorbing bound, giving p≈0.998 on the wrong side. Rare in count
  but unbounded per trade (−45 USD of the −135).
- The rest (83 losses, −262 USD) had observations consistent with settlement. The
  settled high ended on average 3.8 °C above the extreme observed at entry, and 61 of
  78 high losses were bins the remaining-day rise crossed. The remaining-day
  distribution is too narrow or too cold. Entries after local 12:00 were worst
  (12–20 h: 32 trades, 5 won, −110 USD).
- Action: `day0_nowcast_entry` disabled by operator strategy gate (precedence max,
  2026-09-24 20:12Z) until a settlement-graded fix. Forecast entries continue.

### 8.1 Day0 root cause (measured 2026-09-24, 45 surviving HIGH trades 09-22..23)

The Day0 remaining-day q (`_day0_remaining_p_raw_vector`, `src/engine/event_reactor_adapter.py`)
integrates `max(observed boundary, member path max + N(0, σ))` over the remaining-hour
member paths from `day0_hourly_vectors`, with `σ = hypot(σ_instrument, extra process σ)`.
Reconstructed from the stored vectors at each entry time:
- The remaining-day max comes from **3–4 deterministic model paths** (54–55 only where
  ENS members exist). Their spread was 0.2–0.7 °C in most cases.
- The settled high sat on average **+1.9 member-spreads above the member mean**, and
  36 % of cases were more than 2 spreads out. Taipei 09-22: three members 30.0 ± 0.2 °C,
  settled 33 → q(NO on 33 °C) = 1.000.
- σ is the instrument σ (~0.28 °C) plus a staleness margin. No term carries the
  models' own error for the remaining-day max at a station, which is ~1–2 °C with a
  warm residual. So a clustered, cold member set becomes near-certainty.

Required fix before re-enabling Day0, settlement-graded rather than tuned to these cases:
1. Add a per-city remaining-day-max forecast-error term, walk-forward fit on settled
   outcomes of the remaining-day max versus the member mean, by local hour remaining.
2. Add the matching bias correction.
3. Validate calibration (PIT / reliability) on held-out settled days before arming.
