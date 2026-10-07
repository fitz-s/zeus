# Dense airport station → settled daily extreme: one method, applied per city

Read-only analysis. No DB writes, no `src/` or `config/` changes. Every DB was opened with `file:…?mode=ro`. Artifacts live in
`artifacts/fast_obs_audit/dense_station_model/`.

| file | role |
|---|---|
| `dense_model.py` | Items 1–4 mathematics: rounding R, the interval-censored MLE, floor margins, scoring |
| `run_dense_model.py` | Items 1–4 runner → `per_city/<city>.json`, `summary_table.md` |
| `state_space.py`, `ss_engine.py` | State-space settled-extreme model: filter, particle engine, grid oracle, estimation |
| `run_state_space.py` | A0 / A / B experiment → `per_city_ss/<city>.json`, `per_city_ss/<city>_decisions.csv.gz` |
| `build_ss_summary.py` | A0 / A / B table and gate → `ss_summary_table.md`, `ss_verdicts.json` |
| `test_dense_model.py` | 34 tests (synthetic data only) |
| `fetch_*.py`, `probe_*.py` | Data acquisition; raw responses kept in `raw/` |

## 0. Settlement law and notation

The WRH timeseries page renders every METAR and SPECI row of the station as an integer in the contract unit (°C for
every city here). For a local day d with settlement instants S_d:

- H_d = max_{t∈S_d} M_t
- L_d = min_{t∈S_d} M_t
- M_t = R(T_t)

R is `SettlementSemantics(rounding_rule="wmo_half_up").round_single`, i.e. R(v) = ⌊v + 0.5⌋. Examples: 2.5 → 3, −0.5 → 0,
−1.5 → −1, −2.51 → −3. The code calls the repo function itself, and the tests pin negatives against `round_single`.

Local days are [local midnight, next local midnight). Each boundary is constructed in the city timezone before conversion
to UTC, so DST days are 23 h or 25 h long.

Truth is `settlement_outcomes`, authority = VERIFIED. Eras are split by `provenance_json.data_version`:

- `noaa_wrh_timeseries_v1` is the NOAA era, from 2026-08-24 (Toronto from 08-23).
- `wu_icao_history*` is the WU era.

**The METAR integer used in fitting is the TT/dd body group** (M = minus) from IEM ASOS, report_type 3 + 4. IEM files
the second half-hourly routine report under type 4, so its labels are not used. A report is classed as routine if and
only if its minute is one of the station's dominant cadence minutes; anything else is a SPECI. All 8 stations parsed
with 0 rows lacking a TT group and 0 duplicate instants.

## 1. Measurement model at settlement instants

**Model.** For t ∈ S ∩ G: M_t = R(x_t + b + ε_t). The dense reading x_t is reported to 0.1 °C. ε is Gaussian
(baseline) or Student-t with ν chosen by profile likelihood.

**Interval-censored ML.** M = k means x + b + ε ∈ [k − ½, k + ½), so each pair contributes
log P(lo ≤ Z < hi) with lo, hi = (k ∓ ½ − x − b)/σ. Tails are computed stably via `log_ndtr`. Standard errors come
from the numerical Hessian.

The state-space estimation (§5) also integrates the dense reading's own 0.1 quantum: x is an interval, not a point.

**Residual autocorrelation.** Two estimates between consecutive routine instants:

- a pairwise bivariate-normal likelihood for the latent ρ of (ε_t, ε_{t+cad});
- the lag-1 correlation of the generalised residuals E[ε | M].

**Alignment check.** The same fit is repeated with the dense series shifted by ±10 min and ±20 min (±1..5 min for
1-min data). For every 10-min source, lag 0 is the unique minimum of nll per pair (§table). Timestamps are aligned.

**Findings (refinement the data forced).**

- **Tokyo (JMA 44166) and Toronto (ECCC 51459)** reproduce the METAR exactly: R(x) = M in 10 198 / 10 198 and
  7 416 / 7 416 pairs, including 198 Toronto cases at x = −k.5, all rounding half-up.
  - The likelihood is flat on b ∈ [0.0, 0.1) with σ → 0. Read the "b = +0.05, σ = 0.007" in the table as "identical
    reading" (the midpoint of the identified set), not as an estimate with that precision.
  - The METAR body group is literally R(national reading). With σ = 0 the floor margin is 0.
- **Helsinki, Singapore, Warsaw**: the Gaussian is rejected and Student-t wins on AIC.
  - χ² of the observed-vs-expected table of j = M − R(x): Helsinki 207 → 9, Singapore 199 → 9, Warsaw 352 → 33.
  - Helsinki is a two-population mixture: σ_core 0.084 and σ_outlier 0.41, outlier weight 0.28.
  - The operator's "229 of 240" is the last 240 pairs; over the full archive R(x) = M holds in 7 832 / 9 025 (86.8 %).
  - By month, R(x) = M runs from 83 % (May) to 95 % (October): Helsinki's agreement is non-stationary.
- **Munich**: b = +0.20 overall but 0.38 by day versus 0.09 by night. The DWD TT_10 sensor reads below the METAR in
  daytime.
  - The latent residual is strongly autocorrelated (ρ = 0.58). This is a slowly drifting sensor offset, not white noise.
- **Singapore**: NEA S24 versus WSSS gives σ = 0.46 (t: 0.34, ν = 5) and ρ = 0.54. NEA S24 is a different sensor and
  site from the METAR thermometer; its identity changed name mid-series (see the prior SUMMARY).
- **Warsaw**: hourly synop only. σ = 0.72 overall, 0.39 by day and 0.85 at night; the synop value is not the METAR
  instant reading at night.
- **Amsterdam**: KNMI ta at :20/:30 → :25 by midpoint interpolation, n = 383 (8 days, the 800-call cap).
  - The fit gives σ_total 0.162. The NEA 1-min series gives a midpoint-interpolation error sd of 0.164.
  - So the interpolation error alone accounts for all of σ_total: the KNMI sensor itself is indistinguishable from
    the METAR at the instants it hits.
- **Madrid**: two AEMET 24-h windows, n = 38. Descriptive only.

## 2. Hard-floor rule

**Floor.** At a settlement instant t: floor_t = R(x_t + b − m). If ε_t ≥ −m then M_t ≥ floor_t, hence H_d ≥ floor_t.
**Ceiling.** ceil_t = R(x_t + b + m); L_d ≤ ceil_t.

A false high floor occurs iff x + b − m ≥ H + ½. The set with zero false floors is therefore m > c*, where
c* = max(x + b − H − ½). For lows, a false ceiling occurs iff x + b + m < L − ½, so the zero-false set is m ≥ c*.
The asymmetry is R's half-up edge, and it is unit-tested.

m_c is the smallest margin on a 0.05 grid with zero false days over all history, found by applying R directly.
It is reported overall, per truth era, and out of sample: m from the first 70 % of days, false floors counted on the
last 30 %.

**Two margins are reported, and the difference matters.**

- m_c bounds the floor against the **settled day extreme**, which is what a trade needs.
- m_inst bounds it against **the same-instant METAR**. That bound is much wider: Helsinki 1.15, Munich 1.05,
  Singapore 4.5, Warsaw 3.65. The day extreme absorbs single-instant errors, and the same-instant margin is the one a
  per-instant "this METAR will be ≥ k" claim would need.

The model-implied margin at a 10⁻³ per-day risk is σ·z(10⁻³/n_inst). It is 0.98 (Helsinki), 1.13 (Munich) and
1.87 (Singapore) under the Gaussian, and 5.0, 1.45, 4.6 under the fitted t. **The empirical m_c is far smaller than the
model margin**: history (≤ 300 days) cannot certify a 10⁻³ tail. m_c is a sample statement, not a guarantee.

**Lead.** The lead is reported only where it is real.

- Lead vs the same-grid METAR instant: the floor reaches the settled value at a dense instant before any METAR shows it.
  For Tokyo and Toronto this is 0 by construction (same instants).
- Lead vs the actual AWC receipt, which is the decision-relevant one. Live where WORLD has the dense channel (n is
  small: live data only starts 09-27..10-01); otherwise modeled with the channel's p50 publication lag.

## 3. Nowcast of the next settlement integer

Target: P(M_{t1} = k | x(t0)), with t1 a routine settlement instant and Δ = t1 − t0 ∈ {10, 20, 30} min (KNMI: 5, 25).
Δ = 30 puts t0 on the previous settlement instant for half-hourly stations, so it is marked * in the table.
Days are split 70 / 30 by date.

Models compared:

- naive: the previous METAR carried forward, as a point mass;
- persistence-probabilistic: the empirical distribution of M_{t1} − M_prev;
- empirical-dense: the table of j = M_{t1} − R(x0 + b), conditioned on the position of x0 + b inside its rounding cell;
- ICM-dense: interval-censored N(x0 + β·1 + γ·trend [+ δ·(M_prev − x0)], σ).

All are scored on the held-out 30 % with multi-category Brier, log loss and reliability ECE.

Result: wherever the dense value arrives before the METAR, the dense nowcasts beat both baselines at Δ = 10 and 20.
The empirical table is usually best: it captures the non-Gaussian residual for free. Trend adds nothing material.
Availability uses live WORLD receipts where present (x(t0) arrives a median 9.2 min before AWC's t1 METAR at Δ = 10
for Helsinki, 9.3 min for Tokyo), otherwise the modeled lag.

## 4. Gap decomposition of dense-max vs settled H (and dense-min vs L)

Per day, on days with ≥ 80 % coverage of both grids:

- A_full = R(extreme over G of x)
- A_S = R(extreme over S ∩ G of x)
- B = extreme over S ∩ G of M
- C = extreme over all S of M (METAR + SPECI)
- H = settled value

The terms are a = A_full − A_S (cadence), b = A_S − B (measurement), c = B − C (coverage: SPECIs and off-grid
instants), and d = C − H (era / source). They telescope: a + b + c + d = A_full − H.

The table counts days with each term ≠ 0, and per-city histograms are in the JSON. Main readings:

- Helsinki and Tokyo highs are driven by cadence: Tokyo has 25 / 203 cadence days and 0 measurement days.
- Munich and Warsaw are driven by measurement.
- Toronto by coverage: 9 days on which a SPECI set the high.
- Singapore by both cadence and measurement.
- Era (d ≠ 0) is rare: Tokyo 1 WU day, Singapore 1 NOAA day, Toronto 1.

## 5. State-space model of the settled extreme (the main deliverable)

### 5.1 Model

On a 5-min grid from local 00:00 − 180 min to the end of the local day:

- T_t = m_t + a_t + c_t, with mean m_t = f_t + μ(h_t) + β(f_t − S₄ₕf_t).
  - f is the ECMWF IFS hourly forecast, linearly interpolated.
  - μ(h) is the forecast bias by local hour.
  - β shrinks the forecast's sub-4-h wiggle, where S₄ₕ is a centred 4-h running mean. β is chosen by walk-forward
    (§5.3).
- a and c are independent OU processes: slow (τ_s, s_s²) and fast (τ_f, s_f²).
  - One-scale model: s_f = 0. Both scales are kept in the state, because their sum is not Markov.
- Dense: the reading x_t is a quantised value of x*_t = T_t − b(h_t) + d_t + w_t.
  - d is an OU error drift (τ_e, a·σ²).
  - w is a white mixture (1 − π)N(0, s₁²) + πN(0, s₂²).
  - The quantum is 0.1: T + d + w ∈ [x + b − 0.05, x + b + 0.05).
- METAR / SPECI: T_t ∈ [M − ½, M + ½) exactly.
- H = max(B, max over pending scheduled routine instants of R(T_t)), and L is the min.
  - B is the extreme of the METAR / SPECI integers already **received**: obs time + AWC p50 lag ≤ τ0.
  - Pending means the routine clock schedule, never the realised future rows, so a future report's existence never
    leaks.
  - Future SPECIs are not modelled.

### 5.2 Inference (exact likelihoods; Monte Carlo only in the posterior representation)

`ss_engine.run_day` is a fully adapted particle filter with N = 20 000.

- **Observation steps.** At a step with observations, each particle's predictive for the observed linear functional
  (a + c for a METAR, a + c + d + w for a dense reading) is Gaussian. The functional is drawn from its
  **truncated** predictive, and the particle weight is the exact interval probability. Mixture components are drawn
  with their exact posterior odds. The state is then conditioned on the drawn value.
- **Exactness.** The interval likelihoods are exact (theory point 3), and the dense quantum enters as an interval
  (point 4). Multiple reports in one 5-min slot are merged into the hull of their cells.
- **Decision procedure.** Received information is processed in time order:
  - the main pass covers both channels up to min(receipt cut-offs);
  - the faster channel then continues alone, recording R(T) on the path at pending settlement instants. This is how
    **a dense reading at a still-pending METAR instant** makes that instant nearly known;
  - OU paths are then simulated to the day end.
- **Arms share everything.** A and B use the same parameters, the same random streams and the same decision rule. B
  adds only the dense likelihood factors (theory point 1). With no dense data, B is bit-identical to A (tested).

**Approximation arms**, reported as such:

- `A_tn` / `B_tn`: a Gaussian assumed-density main pass with an exact truncated-normal moment match for METAR;
- `A_g12` / `B_g12`: METAR treated as N(M, 1/12).

On NOAA test days they differ from the exact arm by ≤ 0.001 log-loss in A and ≤ 0.003 in the B−A difference (third
table). The moment-matching approximation is adequate here.

**Validation against an exact oracle.** `grid_oracle` is a finite-state forward recursion on a 401-cell residual
grid, with exact cell censoring and indicator columns for P(H ≤ k) and P(L ≥ k).

- On synthetic days the particle filter matches it within total variation < 0.03 at every decision for both arms
  (test). The typical difference is ≈ 0.003.
- The test suite also checks:
  - truncated-normal moments against quadrature;
  - MC pmfs against the exact independent-instant formula;
  - zero mass below a received high or above a received low;
  - recovery of OU (τ, s) from synthetic dense series (one and two scales);
  - recovery of b and σ under quantisation;
  - B beating A on held-out synthetic days, with a day-bootstrap 97.5 % bound < 0.

### 5.3 Estimation (training days only)

The split is by date: the first 70 % of usable days train, the last 30 % test. A day is usable when it has the forecast,
≥ 80 % METAR coverage and ≥ 80 % dense coverage.

- **b by 3-h local block, s₁, s₂, π**: ML on same-instant pairs with the quantised likelihood
  P(M | x) = Σ_j w_j ∫ uniform-quantum × Gaussian. The mixture is used if it wins AIC by > 2.
- **Error drift a, τ_e**: the latent correlation of the dense-vs-METAR error at 1–4 settlement cadences (pairwise
  likelihood), fitted to a·exp(−L/τ_e). Variance is conserved: drift a·s₁², core white (1 − a)·s₁².
- **μ(h), β**: least squares of M − f at METAR instants.
- **OU parameters**: weighted LS fit of the empirical autocovariance of the dense residual x + b − m.
  - One scale uses lags 10–180 min, as specified.
  - Two scales use lags 10–720 min, because the slow component is not identified inside 3 h.
  - Lag 0 is never used: it carries the dense white noise.
- **Model choice**: {one, two scales} × {plain, wiggle-shrunk mean}, by walk-forward held-out log-loss *inside the
  training days* (inner 70 / 30 split, hourly decisions). The pick minimises the mean of A and B, so it favours
  neither arm.
- **A0**: max(B, R(N(max remaining forecast + mean_h, sd_h))), with (mean_h, sd_h) the empirical remaining-extreme error
  by local decision hour on training days. There is no state conditioning.

### 5.4 Scoring

Decisions are taken every 10 min from local 06:00 to the day end on test days. Each decision is scored on the settled
value with:

- log loss, floored at 10⁻⁴;
- multi-category Brier;
- randomized categorical PIT;
- calibration of the top-bin probability (`reliability_top` in the JSON);
- **lead**: the first decision time with P(correct bin) ≥ 0.9, B − A in minutes, so positive = B earlier;
- **semantic violations**: mass on bins the received METAR extreme already excludes, reported separately (must be 0);
- **high-confidence calibration**: errors among decisions with top ≥ 0.95, alongside Σ(1 − q) and coverage;
- **false-certainty rate**: the share of all decisions with top ≥ 0.95 on a wrong bin.

The uncertainty of B − A is a **day-blocked** bootstrap, because decisions within a day are not independent.

**Source-incompatible days.** These are days where the settled value contradicts the received METAR record:
P_A(truth) < 10⁻³ at the day's last decision. They are listed and excluded from the gate.

The one instance is Singapore 2026-09-20 high. The METARs show 32 at 03:30–05:14 UTC, but those 5 rows are absent
from the WRH capture in WORLD, and the day settled 31. No observation model can score that; it is a settlement-source
issue (§7).

**Gate** (NOAA era, excluding incompatible days): B is usable iff all three hold.

1. The B − A log-loss day-block 95 % upper bound is < 0.
2. There are zero semantic violations.
3. B's errors at q ≥ 0.95 do not exceed the 95 % Poisson bound of B's own Σ(1 − q).

The operator's literal raw-count rule (false-certainty rate B ≤ A) is shown as STRICT next to it.

## 6. Results

### 6.1 Items 1–4 summary (verbatim from `summary_table.md`)

| city | station/source | n_pairs | b | σ | m_high / m_low | floor fires (days) | floor lead vs AWC (min) | nowcast Brier vs naive | gap (a)/(b)/(c)/(d) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Helsinki | EFHK / FMI fmisid 100968, 10-min | 9025 | +0.041 | 0.240 | 0.35 / 0.0 | H 107/178; L 47/47 | H live -90 (p10 -643, n=5); L live -2 (p10 -31, n=10) | Δ10: 0.319 vs 0.755; Δ20: 0.451 vs 0.755; Δ30*: 0.543 vs 0.755 | H 33/29/0/0 of 178; L 5/0/0/0 of 47 | NOWCAST_ONLY |
| Munich | EDDM / DWD 01262 TT_10, 10-min | 10525 | +0.200 | 0.277 | 0.45 / 0.0 | H 62/206; L 33/47 | H model -67 (p10 -153, n=20); L model -37 (p10 -126, n=33) | Δ10: 0.522 vs 1.128; Δ20: 0.648 vs 1.128; Δ30*: 0.711 vs 1.127 | H 32/77/0/0 of 204; L 4/5/0/0 of 45 | NOT_USEFUL |
| Warsaw | EPWA / IMGW synop 12375, hourly | 5276 | -0.057 | 0.720 | 0.25 / 0.6 | H 90/194; L 9/47 | H model -73 (p10 -165, n=30); L model -13 (p10 -163, n=9) | n/a | H 0/58/20/0 of 193; L 0/21/11/0 of 46 | NOT_USEFUL |
| Amsterdam | EHAM / KNMI 06240 ta, :20/:30/:50/:00 -> :25/:55 interp | 383 | +0.052 | 0.162 | 0.15 / 0.0 | H 7/8; L 8/8 | H model -16 (p10 -16, n=7); L model -16 (p10 -106, n=8) | Δ5: 0.211 vs 0.764; Δ25: 0.479 vs 0.722 | H 1/1/0/0 of 8; L 0/1/0/0 of 8 | NOWCAST_ONLY |
| Tokyo | RJTT / JMA AMeDAS 44166 Haneda, 10-min | 10198 | +0.049 | 0.007 | 0.0 / 0.0 | H 203/203; L 159/160 | H live -1 (p10 -2, n=6); L live +1 (p10 -1, n=5) | Δ10: 0.253 vs 0.535; Δ20: 0.346 vs 0.535; Δ30*: 0.430 vs 0.535 | H 25/0/0/0 of 203; L 10/0/0/1 of 160 | NOWCAST_ONLY |
| Singapore | WSSS / NEA S24 Changi, 1-min | 9806 | -0.046 | 0.457 | 0.9 / 0.85 | H 37/200; L 10/35 | H model -61 (p10 -109, n=19); L model -181 (p10 -286, n=10) | Δ10: 0.417 vs 0.603; Δ20: 0.459 vs 0.605; Δ30*: 0.509 vs 0.611 | H 76/61/0/1 of 199; L 4/13/2/0 of 35 | NOWCAST_ONLY |
| Toronto | CYYZ / ECCC 51459 hourly (live: SWOB) | 7416 | +0.051 | 0.007 | 0.5 / 0.0 | H 143/289; L 46/47 | H model +5 (p10 -114, n=41); L live +5 (p10 -9, n=5) | n/a | H 0/0/9/1 of 288; L 0/0/0/0 of 46 | FLOOR_USABLE |
| Madrid | LEMD / AEMET 3129 horario, hourly (2 x 24 h) | 38 | -0.097 | 0.486 | 0.0 / 0.0 | H 1/2; L 0/2 | H model n/a; L model n/a | n/a | H 0/0/0/0 of 1; L 0/1/0/0 of 1 | NOT_USEFUL |

Verdict definitions:

- **FLOOR_USABLE**: all of the following hold:
  - zero false floors at m_c over all history and on the held-out 30 %;
  - the floor reaches the settled value on ≥ 20 % of days;
  - median floor lead over the AWC receipt > 0;
  - the dense receipt of a settlement instant beats AWC's receipt of the same METAR.
- **NOWCAST_ONLY**: a dense nowcast from a non-settlement instant beats both baselines held-out, and is available
  before the AWC METAR.
- **NOT_USEFUL**: neither.

### 6.2 State-space A0 / A / B (verbatim from `ss_summary_table.md`)

| city | metric | n test days (NOAA, excl. incompatible) | logloss A0 / A / B | Brier A0 / A / B | B−A logloss [95 % day-block] | lead B−A min p10 / p50 / p90 (n days; B-only / A-only / neither) | false-certainty A / B: rate (errors, Σ(1−q)) | semantic viol. A / B | verdict (STRICT) |
|---|---|---|---|---|---|---|---|---|---|
| Helsinki | high | 44 | 0.729 / 0.739 / 0.720 | 0.368 / 0.380 / 0.370 | -0.0196 [-0.0249, -0.0142] | 0.0 / 20.0 / 50.0 (42; 0 / 0 / 2) | 0.0000 (0, 7.9) / 0.0000 (0, 7.2) | 0 / 0 | B_USABLE (PASS) |
| Helsinki | low | 44 | 0.708 / 0.737 / 0.722 | 0.341 / 0.367 / 0.359 | -0.0151 [-0.0228, -0.0082] | 0.0 / 0.0 / 30.0 (29; 8 / 0 / 7) | 0.0000 (0, 17.4) / 0.0004 (2, 17.5) | 0 / 0 | B_USABLE (FAIL) |
| Munich | high | 42 | 0.886 / 0.896 / 0.896 | 0.428 / 0.420 / 0.420 | -0.0001 [-0.0008, +0.0005] | 0.0 / 0.0 / 0.0 (42; 0 / 0 / 0) | 0.0000 (0, 4.3) / 0.0000 (0, 4.3) | 0 / 0 | B_BETTER_NOT_SIGNIFICANT (PASS) |
| Munich | low | 42 | 0.611 / 0.586 / 0.585 | 0.302 / 0.291 / 0.291 | -0.0003 [-0.0008, +0.0001] | 0.0 / 0.0 / 0.0 (32; 0 / 0 / 10) | 0.0013 (6, 13.7) / 0.0013 (6, 13.6) | 0 / 0 | B_BETTER_NOT_SIGNIFICANT (PASS) |
| Tokyo | high | 44 | 0.966 / 0.774 / 0.738 | 0.426 / 0.353 / 0.337 | -0.0367 [-0.0515, -0.0241] | -10.0 / 10.0 / 50.0 (44; 0 / 0 / 0) | 0.0006 (3, 10.8) / 0.0006 (3, 10.9) | 0 / 0 | B_USABLE (PASS) |
| Tokyo | low | 44 | 0.689 / 0.582 / 0.564 | 0.317 / 0.299 / 0.288 | -0.0179 [-0.0300, -0.0075] | -10.0 / 0.0 / 40.0 (44; 0 / 0 / 0) | 0.0032 (15, 17.8) / 0.0044 (21, 17.7) | 0 / 0 | B_USABLE (FAIL) |
| Singapore | high | 42 | 0.810 / 0.628 / 0.613 | 0.394 / 0.332 / 0.325 | -0.0153 [-0.0218, -0.0090] | 0.0 / 20.0 / 77.0 (42; 0 / 0 / 0) | 0.0000 (0, 5.2) / 0.0002 (1, 5.5) | 0 / 0 | B_USABLE (FAIL) |
| Singapore | low | 32 | 0.508 / 0.496 / 0.486 | 0.218 / 0.217 / 0.214 | -0.0108 [-0.0209, -0.0024] | -20.0 / 0.0 / 20.0 (32; 0 / 0 / 0) | 0.0012 (4, 11.7) / 0.0003 (1, 12.1) | 0 / 0 | B_USABLE (PASS) |
| Toronto | high | 44 | 1.060 / 0.892 / 0.886 | 0.459 / 0.417 / 0.416 | -0.0056 [-0.0141, +0.0032] | 0.0 / 0.0 / 57.0 (44; 0 / 0 / 0) | 0.0006 (3, 6.6) / 0.0011 (5, 7.4) | 0 / 0 | B_BETTER_NOT_SIGNIFICANT (FAIL) |
| Toronto | low | 44 | 0.301 / 0.295 / 0.291 | 0.148 / 0.146 / 0.143 | -0.0035 [-0.0077, +0.0008] | 0.0 / 0.0 / 7.0 (44; 0 / 0 / 0) | 0.0000 (0, 28.3) / 0.0000 (0, 27.4) | 0 / 0 | B_BETTER_NOT_SIGNIFICANT (PASS) |
| Warsaw | high | 43 | 0.752 / 0.675 / 0.673 | 0.384 / 0.348 / 0.347 | -0.0024 [-0.0036, -0.0013] | 0.0 / 0.0 / 0.0 (43; 0 / 0 / 0) | 0.0006 (3, 5.0) / 0.0006 (3, 5.4) | 0 / 0 | B_USABLE (PASS) |
| Warsaw | low | 43 | 0.469 / 0.389 / 0.389 | 0.238 / 0.215 / 0.215 | -0.0003 [-0.0013, +0.0009] | 0.0 / 0.0 / 0.0 (43; 0 / 0 / 0) | 0.0009 (4, 6.5) / 0.0011 (5, 6.5) | 0 / 0 | B_BETTER_NOT_SIGNIFICANT (FAIL) |
| Amsterdam | – | – | – | – | – | – | – | – | SKIPPED: KNMI: 769 bracket files cover 8 days (operator cap 800 calls); no train/test split possible |
| Madrid | – | – | – | – | – | – | – | – | SKIPPED: no dense archive (AEMET public XML exposes 24 h only) |

| city | train / test days | picked model | τ_slow min | s_slow °C | τ_fast min | s_fast °C | b by 3-h block °C | σ Gauss °C | dense core / outlier sd, outlier w | drift a, τ_e min | forecast shrink β | lag METAR / dense p50 min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Helsinki | 125 / 54 | one|shrunk | 213 | 1.32 | 30 | 0.00 | +0.06, +0.02, +0.00, +0.01, +0.02, +0.03, +0.05, +0.09 | 0.254 | 0.083 / 0.413, 0.28 | 0.04, 10000 | -0.81 | 1.6 / 2.5 |
| Munich | 140 / 60 | one|shrunk | 248 | 1.88 | 30 | 0.00 | +0.01, -0.00, +0.11, +0.36, +0.42, +0.40, +0.29, +0.08 | 0.227 | 0.148 / 0.427, 0.05 | 0.45, 210 | -0.42 | 3.8 / 40.8 |
| Tokyo | 136 / 59 | two|shrunk | ∞ (day-level offset) | 0.26 | 354 | 1.21 | +0.05, +0.05, +0.05, +0.05, +0.05, +0.05, +0.05, +0.05 | 0.005 | 0.020 / 0.020, 0.00 | 0.50, 1 | -1.26 | 8.2 / 7.5 |
| Singapore | 133 / 57 | one|shrunk | 332 | 1.28 | 30 | 0.00 | -0.13, -0.21, -0.22, -0.01, +0.02, +0.09, -0.00, -0.19 | 0.438 | 0.218 / 0.858, 0.07 | 0.65, 97 | -1.58 | 1.2 / 1.7 |
| Toronto | 203 / 88 | one|shrunk | 591 | 1.96 | 30 | 0.00 | +0.05, +0.05, +0.05, +0.05, +0.05, +0.05, +0.05, +0.05 | 0.005 | 0.020 / 0.020, 0.00 | 1.00, 178 | -1.50 | 7.6 / 2.1 |
| Warsaw | 140 / 61 | one|shrunk | 302 | 1.39 | 30 | 0.00 | -0.21, -0.22, -0.11, -0.07, +0.02, +0.08, +0.17, -0.04 | 0.734 | 0.195 / 1.098, 0.34 | 0.72, 185 | -0.86 | 2.5 / 15.7 |

| city | metric | WU-era test days: n, logloss A0 / A / B, B−A [95 %] | all VERIFIED test days: n, B−A [95 %] | approximation arms on NOAA days: B_tn−A_tn, B_g12−A_g12, A_tn−A, A_g12−A (mean logloss) | source-incompatible test days |
|---|---|---|---|---|---|
| Helsinki | high | 10, 0.736 / 0.812 / 0.796, -0.0160 [-0.0212, -0.0112] | 54, -0.0189 [-0.0235, -0.0144] | -0.0195, -0.0198, +0.0004, +0.0004 | none |
| Helsinki | low | 3, 0.169 / 0.253 / 0.236, -0.0176 [-0.0539, +0.0053] | 47, -0.0152 [-0.0225, -0.0086] | -0.0144, -0.0141, -0.0000, -0.0001 | none |
| Munich | high | 18, 1.198 / 1.034 / 1.035, +0.0009 [-0.0004, +0.0024] | 60, +0.0002 [-0.0004, +0.0009] | -0.0001, -0.0000, +0.0003, +0.0003 | none |
| Munich | low | 3, 1.306 / 1.459 / 1.457, -0.0020 [-0.0040, +0.0004] | 45, -0.0005 [-0.0009, +0.0000] | -0.0003, -0.0002, -0.0003, -0.0003 | none |
| Tokyo | high | 15, 0.833 / 0.922 / 0.885, -0.0368 [-0.0588, -0.0147] | 59, -0.0367 [-0.0478, -0.0253] | -0.0369, -0.0363, -0.0005, -0.0011 | none |
| Tokyo | low | 15, 0.568 / 0.635 / 0.618, -0.0168 [-0.0278, -0.0062] | 59, -0.0176 [-0.0267, -0.0095] | -0.0182, -0.0180, -0.0008, -0.0010 | none |
| Singapore | high | 14, 0.880 / 0.601 / 0.578, -0.0230 [-0.0318, -0.0140] | 56, -0.0172 [-0.0225, -0.0119] | -0.0151, -0.0151, -0.0003, -0.0000 | 2026-09-20 |
| Singapore | low | 3, 0.120 / 0.155 / 0.158, +0.0033 [-0.0075, +0.0151] | 35, -0.0096 [-0.0187, -0.0016] | -0.0117, -0.0122, +0.0003, +0.0005 | none |
| Toronto | high | 43, 1.190 / 0.929 / 0.919, -0.0095 [-0.0170, -0.0024] | 87, -0.0075 [-0.0135, -0.0019] | -0.0056, -0.0055, -0.0002, -0.0003 | none |
| Toronto | low | 2, 0.205 / 0.286 / 0.291, +0.0048 [+0.0034, +0.0062] | 46, -0.0031 [-0.0068, +0.0010] | -0.0036, -0.0036, +0.0001, -0.0000 | none |
| Warsaw | high | 18, 0.913 / 0.872 / 0.872, +0.0002 [-0.0028, +0.0046] | 61, -0.0016 [-0.0030, -0.0001] | -0.0012, -0.0012, -0.0001, -0.0003 | none |
| Warsaw | low | 3, 0.615 / 0.587 / 0.582, -0.0046 [-0.0081, -0.0001] | 46, -0.0006 [-0.0016, +0.0005] | -0.0001, +0.0000, -0.0002, -0.0002 | none |

## 7. Per-city verdicts and reasons

### 7.1 Answer to "how does a dense station relate to the settled value"

There is one relation, with three city-specific parameters:

1. **identity**: σ and b at settlement instants (§1);
2. **availability**: dense receipt time against AWC receipt of the same METAR;
3. **cadence gain**: dense instants between METARs (§4a).

The hard floor (§2) and the nowcast (§3) are special cases of the state-space posterior (§5):

- the floor is the σ → 0 limit at an instant whose METAR is pending;
- the nowcast is its one-step marginal.

The state-space B arm is the version that puts dense information into the bin probability, and the one to ship if
anything is shipped.

### 7.2 Per-city verdicts

The first verdict column is the items 1–4 verdict (FLOOR_USABLE / NOWCAST_ONLY / NOT_USEFUL). The second is the
state-space gate on NOAA-era test days.

| city | items 1–4 | state-space B vs A (high / low; STRICT) | reason |
|---|---|---|---|
| Toronto | **FLOOR_USABLE** | NOT SIGNIFICANT / NOT SIGNIFICANT (FAIL / PASS) | See note T. |
| Tokyo | NOWCAST_ONLY | **B_USABLE / B_USABLE** (PASS / FAIL) | See note K. |
| Helsinki | NOWCAST_ONLY | **B_USABLE / B_USABLE** (PASS / FAIL) | See note H. |
| Singapore | NOWCAST_ONLY | **B_USABLE / B_USABLE** (FAIL / PASS) | See note S. |
| Warsaw | NOT_USEFUL | B_USABLE (negligible) / NOT SIGNIFICANT (PASS / FAIL) | See note W. |
| Munich | NOT_USEFUL | NOT SIGNIFICANT / NOT SIGNIFICANT (PASS / PASS) | See note M. |
| Amsterdam | NOWCAST_ONLY (8 days) | not run | See note A. |
| Madrid | NOT_USEFUL | not run | See note D. |

**T, Toronto.**

- ECCC 51459 equals the METAR body group at every instant: 7 416 / 7 416 pairs, σ → 0.
- The SWOB receipt beats AWC for the same instant by a median 5.5 min (147 / 148 live instants).
- The floor at m = 0 is exact for every NOAA-era day and out of sample.
  - The all-history m_high = 0.5 is forced by one WU-era day (05-18: METAR 31, WU settled 30).
  - The floor fires on 143 / 289 days, with a modeled lead of +5 min.
- In the state space, B − A logloss is −0.006 (high) and −0.004 (low), but the 95 % bands include 0.
  - The 5-min lead touches only the last pending instant. Hourly METAR plus hourly SWOB gives little *between*
    instants.
  - The value is in timing, not probability. A FLOOR_USABLE rule (dense R(x) at a settlement instant is the METAR
    integer, received ~5 min earlier) captures it more simply than the filter.
  - Caveat: 9 / 288 days had a SPECI set the high, and future SPECIs are invisible to both.

**K, Tokyo.**

- JMA 44166 is identical to the METAR (10 198 / 10 198), but arrives at the same time (median −0.5 min; dense first
  on 46 % of instants). So the floor brings no time lead: NOWCAST_ONLY.
- The 10-min readings *between* METARs carry the largest gain of any city: high −0.037 [−0.052, −0.024],
  low −0.018 [−0.030, −0.008]. Both A and B beat A0 on both metrics.
- Lead to P ≥ 0.9: median +10 min (high).
- STRICT fails on low (21 vs 15 errors at q ≥ 0.95), but within the Poisson bound of 17.7 expected. The errors come
  from 2 days, mainly 08-27, a convective drop at 18 local; B led A by 60 min once it showed.

**H, Helsinki.**

- FMI is not identical to the METAR: a two-population error, σ_core 0.08 / σ_outlier 0.41, outlier weight 0.28, with
  agreement non-stationary by month.
- Same-instant receipt is a tie (median −0.8 min). The floor needs m = 0.35 and is never earlier than AWC.
- But the 10-min path between METARs informs the extreme. B − A is −0.020 [−0.025, −0.014] (high) and
  −0.015 [−0.023, −0.008] (low). The gain is concentrated at 13–16 local (high, up to −0.057) and 21–23 / 06 local
  (low).
- Lead to P ≥ 0.9: median +20 min (high).
- STRICT fails on low: 2 errors vs 0, both on one day (09-12 06:00, a radiative minimum the forecast missed). This is
  within the Poisson bound.

**S, Singapore.**

- NEA S24 is a different sensor (σ 0.44 Gauss / 0.22 core plus 0.86 outliers; drift ρ 0.65, τ_e 97 min). Its
  readings still sharpen the path.
- B − A is −0.015 [−0.022, −0.009] (high) and −0.011 [−0.021, −0.002] (low). Lead median +20 min (high).
- The measurement margin is large (m_high 0.9) and out-of-sample false floors occur, so a floor is not usable.
- STRICT fails on high by one error (1 vs 0).
- **The settlement-page dropout (09-20, §5.4) is the dominant risk**: it gives A and B 46–47 decisions at 100 %
  confidence on the wrong bin. That is not a dense-data problem.
- Live receipt is modeled from a 100-minute probe only; Zeus has no NEA channel.

**W, Warsaw.**

- Hourly IMGW synop is 15.7 min later than AWC and only roughly equals the METAR (σ 0.73).
- B − A is −0.002 on high: statistically significant but economically nil. Low is not significant.

**M, Munich.**

- DWD 01262 arrives 27 min after AWC (median 40.8-min publication lag), so by the time a dense reading exists, the
  METAR for that instant is already in hand.
- B − A is 0.000 on both metrics. The extra cadence never arrives in time to matter.
- The sensor also reads ~0.4 °C below the METAR by day.

**A, Amsterdam.**

- KNMI ta at :20/:30 interpolated to :25 matches the METAR to within the interpolation error. KNMI publishes 13.7 min
  after its stamp, so the :30 file arrives ~16 min after AWC's :25 METAR.
- The 800-call cap allowed 8 days, too few for a train/test state-space run.
- If KNMI is pursued, fetch every 10-min file. tx/tn bracket the instant exactly and gave m = 0 on all 9 days, but the
  publication lag makes it slower than AWC.

**D, Madrid.**

- No dense archive: the AEMET public XML keeps 24 h, and OpenData has no key. Nothing to evaluate.

### 7.3 Bottom line

- A dense series improves the settled-bin probability out of sample **when, and only when**, it is identical or
  near-identical to the METAR sensor **and** arrives no later than AWC: Tokyo, Helsinki, Singapore.
  - The improvement is −0.015 to −0.037 nats per decision. It concentrates in the hours the extreme is set, and moves
    P(correct) ≥ 0.9 a median 10–20 min earlier on highs.
- Where the dense value arrives after the METAR (Munich, Warsaw, Amsterdam), it adds nothing, whatever its cadence.
- No city shows semantic violations.
- High-confidence errors stay within B's own expected count everywhere. The literal raw-count rule (B ≤ A) fails on
  4 of the 8 usable metrics, by 1–6 errors concentrated in 1–2 days each. With 42–44 test days that rule cannot be
  certified either way.
- Given the operator's "B usable only if false-certainty is no worse" rule, **no city passes STRICT on both metrics**:
  - Tokyo high, Helsinki high and Singapore low pass STRICT and the log-loss gate;
  - Toronto passes on the floor rule only.

## 8. Data access, failures, and what is uncertain

**Data used.**

| data | source | span / count |
|---|---|---|
| METAR + SPECI | IEM ASOS `asos.py`, report_type 3 + 4, one request per station, 2 s apart, all HTTP 200, 0 errors | EFHK 10 561, EDDM 10 566, EPWA 10 566, EHAM 10 527, RJTT 10 567, WSSS 10 783 (216 SPECI), CYYZ 9 255 (1 811 SPECI), LEMD 10 662; 2026-03-01 (CYYZ 2025-12-01) .. 2026-10-07 |
| FMI 100968 10-min | existing `fmi_efhk.csv.gz` plus live WORLD rows appended after 10-06 | 2026-04-02 .. 10-07 |
| DWD 01262 TT_10 | existing archive plus WORLD | 2025-04-04 .. 10-07 |
| IMGW 12375 hourly synop | existing archive (October not yet published by IMGW) plus WORLD | 2026-03-01 .. 10-07 |
| JMA 44166 10-min | **new**: past-weather `10min_a1.php`, 212 pages, 2.5 s apart, 0 errors, 30 521 rows | 2026-03-08 .. 10-06 |
| NEA S24 1-min | existing archive | 2026-03-12 .. 10-05 |
| ECCC 51459 hourly | existing archive. The live SWOB was used for latency only: it is a different product (its 19 off-hour rows all coincide with SPECI instants) | |
| KNMI 06240 | **new**: `ta`, `tx`, `tn` from 769 files at :20 / :30 / :50 / :00. 770 authenticated /url attempts (769 OK, 0 errors, 0 × 429), plus 1 listing call for publication lag: **771 of the 800-call cap** | Amsterdam local days 2026-09-29 .. 10-06 (8 days) |
| AEMET 3129 | public `horario` XML only, two captures (2026-10-06 13:42Z and 10-07 04:15Z) | |
| forecast path | Open-Meteo previous-runs `temperature_2m_previous_day1`, model `ecmwf_ifs`, 1 batched request (8 locations) | 2025-11-30 .. 10-08 hourly; null 2026-03-26 .. 04-10 |
| receipt clocks | WORLD `observation_prints` `fetched_at_utc` (`aviationweather_metar` and the dense live channels); NEA from a 100-min live probe of the data.gov.sg v1 latest endpoint, 192 polls; KNMI from `lastModified` of the newest 60 files | |

**Data-access failures (exact).**

- AEMET OpenData API (`opendata.aemet.es/opendata/api/observacion/convencional/datos/estacion/3129`): no key in the
  keychain (`security find-generic-password -s zeus-obs/aemet/api-key` → not found). A keyless call returns HTTP 200
  with an empty body. Madrid therefore has no dense archive; the state-space arm was skipped.
- IMGW archive `.../synop/2026/2026_10_s.zip`: HTTP 404 (October not yet archived, from the earlier fetch). WORLD rows
  fill October.
- data.gov.sg v1 latest endpoint: 2 of 192 polls failed with `URLError: <urlopen error _ssl.c:1063: The handshake
  operation timed out>`.
  - The endpoint exposes one 5-min stamp per poll, so the NEA lag (p50 1.7 min) is upper-bounded by the 30 s poll and
    measures the 5-min product, not the 1-min archive.
- KNMI: no failures. The cap was respected by counting every authenticated attempt, retries included.
- `day0_hourly_vectors` (the live ecmwf_ifs source named in the brief) starts 2026-10-04, so it could not supply a
  2026-03..10 back-test. On-disk ifs9 raw manifests cover only ~12 cycle dates, because most `artifact_path` files are
  gone (Helsinki: 93 on disk, 1 432 missing).
  - **Substitute**: the same model through Open-Meteo previous-runs at a fixed 24 h lead. It is causal for every
    decision, but less sharp than the live day0 vector. A0, A and B all share it; the absolute log-losses are therefore
    pessimistic for the live system, and the A/B difference is the estimand.

**What is uncertain.**

1. **The forecast lead differs from live.** previous_day1 (24–48 h old) replaces the live day0 vector. With a sharper
   forecast, A improves and the dense increment B − A could shrink, though dense-at-pending-instant information does
   not depend on the forecast.
2. **The latent model is mis-specified in one known way.**
   - On Helsinki training days the OU model predicts a remaining-max excess (max R(T) − max m) of +0.94 °C from 06:00,
     against a realised +0.39.
   - The cause is not marginal variance. Residual paths from *other* days give +0.92; the day's own path shifted by
     1 h gives +0.71. **The real residual is anti-aligned with the forecast's peaks**, which a stationary OU cannot
     represent. Observed peak time − forecast peak time = −1.45 h, sd 2.3 h.
   - The wiggle-shrink mean term (selected for every city by walk-forward) narrows the gap from 0.55 to 0.30 °C on
     Helsinki training days (4-h window: model 0.95 against realised 0.65 on the shrunk mean). It does not close it.
   - As a result A0, which carries an empirical remaining-extreme error, beats A on Helsinki and Munich highs. A beats
     A0 on Tokyo, Singapore, Toronto and Warsaw.
   - **This affects the level of A and B equally. It does not invalidate the A/B comparison**, which shares the model.
3. **Small n for the gates.** There are 42–44 NOAA-era test days per city.
   - High-confidence error counts come from 1–5 days each (clustered), so the Poisson calibration bound treats
     clustered errors as independent. That is anti-conservative.
   - The STRICT raw-count rule flips on ±2 errors.
4. **Live receipt windows are 6–10 days** (WORLD dense channels start 09-27 .. 10-01). Floor leads vs AWC with n < 5
   are labelled. Singapore and Amsterdam leads are modeled from provider publication lag, not Zeus receipts; there is no
   live WORLD channel for either.
5. **Tokyo and Toronto exactness** rests on 10 174 / 7 416 pairs with zero disagreement. A future sensor or software
   change would break m = 0. A live identity check (R(x) == METAR at every shared instant) is the cheap guard; the
   theory note's sensor-regime state is the principled one.
6. **Settlement-source gaps.** Singapore 2026-09-20: 5 METAR rows (32 °C) are absent from the WRH capture and the day
   settled 31. If WRH drops rows, H ≤ METAR max, and any floor (dense or METAR) can be false.
   - IEM instants missing from the WORLD WRH capture are common on other days too (e.g. Warsaw 09-20: 7 rows), but on
     those days the extreme survived.
   - The WRH capture in WORLD starts 09-13. Earlier NOAA days have no row-level check.
7. **Two-scale fit for Tokyo** put τ_slow at a numerical infinity (a constant day-level offset, s = 0.26) with
   τ_fast = 354 min. It is stable in scoring, but means the slow component is a per-day bias, not an OU.
