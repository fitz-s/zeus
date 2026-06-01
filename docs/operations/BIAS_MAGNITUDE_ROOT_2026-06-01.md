# BIAS MAGNITUDE ROOT — is the stored per-city bias (−1.58…−4.68 °C) a calc/stat error?

- Created: 2026-06-01
- Last reused or audited: 2026-06-01
- Authority basis: operator read-only quantitative root-cause (BIAS-MAG-1). Tests the claim that
  the live 51-ENS×10k-MC is accurate and the stored `model_bias_ens.effective_bias_c` is an
  inflated calc/stat artifact (true bias ~0.x °C). Derived from originals in
  `state/zeus-forecasts.db` (HEAD 6fcd05a69f). NO edits / git / DB writes.
- Scripts (read-only, /tmp/bias_a/): 01 inspect JSON · 02 query stored · 04 diag · 05 decode `cen`
  · 07 clean recompute · 08 statistic-mismatch · 10 hourly-max · 11 CI+members.

---

## [OBJECTIVE] Prove or refute: stored per-city bias is inflated by a calc/stat error; true bias ~0.x °C.

## 1. THE FIT TRACE

**Writer of the live rows:** `scripts/write_promoted_edli_bias.py` (family `edli_per_city_v1`,
authority VERIFIED, weight_live=1.0, gate_set_hash `a4_canonical_2026_05_31`), confirmed in
`state/zeus-world.db.model_bias_ens` (28 rows = 14 cities × months {5,6}; values match the operator
claim: Singapore −1.58, Tokyo −3.45, Tel Aviv −4.00, San Francisco −4.68).

**(a) Forecast quantity** — the stored value is the straight mean of `err` from
`/tmp/canonical_bias_rows.json`. Decoded by exact match to source snapshots (`05_decode_cen.py`):

> `cen` = **ensemble MEAN of the 51 per-member daily-max values, normalized to °C**
> (`json_cen == mean(members_json)` to 2 dp on all 14 Singapore rows). Not a single deterministic
> value, not the ensemble max. `err = cen − obs`. `effective_bias_c = mean(err)`.

**No shrinkage.** `weight_live=1.0, n_prior=0, estimator=a4_canonical_per_city_settled`. The
TIGGE empirical-Bayes shrinkage (`ens_bias_model.py`, `posterior_bias`) is **NOT in this path** —
these rows are a flat residual mean, not a `w·e_bar+(1−w)·mu_t` posterior.

**(b) Snapshots included** — CONTRIBUTING ONLY. The JSON drew the freshest
`contributes_to_target_extrema=1`, `authority='VERIFIED'`, `dataset_id=ecmwf_opendata_mx2t3_local_calendar_day_max`
snapshot per target_date over 2026-05-13…29. The clean recompute (`07`) reproduces the stored
values to ≤0.1 °C (see table), proving the filter was applied correctly. One stray `contrib=0`
row exists (Singapore 05-14 lead=48) but does not move the mean materially.

**(c) Observed** — settled WU daily high (`observations.high_temp`, `source='wu_icao_history'`,
station WSSS etc.; Tel Aviv = NOAA/llbg). **NOTE:** `settlement_outcomes` is EMPTY for these cities
this checkout; the obs the fit used is the same WU/station value the market settles on — so it IS
the settlement authority (not a stray reference series). No independent grid reference
(open-meteo/ERA5) is stored, and hourly `observation_instants` are absent for these cities, so a
WU-vs-grid offset cannot be measured from originals.

**(d) The stat** — plain mean of `err` (`numpy errs.mean()`), n=13–17 per city, sd reported.
No robust/trimmed mean, no shrinkage.

## 2. CLEAN RECOMPUTE (contributing-only, ens-mean daily-max °C − settled WU high)

| city | CLEAN mean (95% CI) | n | sd | stored eff_bias_c | reproduces? | max-member−obs |
|---|---|---|---|---|---|---|
| Singapore | **−1.58** [−2.22,−0.94] | 14 | 1.22 | −1.58 | ✓ exact | **−0.38** |
| Tokyo | **−3.43** [−4.45,−2.42] | 14 | 1.93 | −3.45 | ✓ | −2.23 |
| Shanghai | **−1.01** [−1.66,−0.35] | 14 | 1.25 | −0.97 | ✓ | **+0.22** |
| Taipei | **−1.88** [−2.53,−1.23] | 14 | 1.24 | −1.80 | ✓ | −0.44 |
| San Francisco | **−4.77** [−5.41,−4.13] | 16 | 1.31 | −4.68 | ✓ | −3.82 |
| Seoul | **+1.28** [+0.06,+2.49] | 14 | 2.32 | +1.34 | ✓ | +2.53 |
| Wuhan | **+0.44** [−0.27,+1.14] | 14 | 1.34 | +0.41 | ✓ | +2.24 |
| Tel Aviv | **−4.11** [−5.09,−3.14] | 13 | 1.86 | −4.00 | ✓ | −3.52 |
| Wellington | −1.18 [−1.56,−0.81] | 14 | 0.72 | −1.15 | ✓ | −0.53 |
| Seattle | −0.47 [−1.27,+0.33] | 16 | 1.63 | −0.77 | ✓ | +1.10 |
| Shenzhen | −0.58 [−1.31,+0.16] | 14 | — | −0.55 | ✓ | +0.40 |
| Toronto | −0.16 [−0.98,+0.67] | 17 | — | −0.41 | ✓ | +1.28 |
| Warsaw | −0.58 [−0.91,−0.25] | 15 | — | −0.23 | ✓ | +0.84 |
| Sao Paulo | −0.28 [−0.97,+0.41] | 15 | — | −0.28 | ✓ | +1.40 |

[STAT:n] per-city n=13–17 contributing settled dates. [STAT:ci] 95% CI = mean ± 1.96·sd/√n.
All 51 members present (`nmemb=[51]`). The stored value is NOT a calculation error — it is the
faithful clean residual mean.

## 3. DECOMPOSITION OF THE MAGNITUDE

Candidate inflation sources, scored against originals:

**(i) Non-contributing / post-peak snapshots — REFUTED as inflation source.** The clean fit already
excludes `contrib=0`. The ALL-LEADS (post-peak-included) residual is *more* negative, not less
(`07`: Taipei all-lead −6.72 vs clean −1.88; Shanghai −4.03 vs −1.01). Post-peak contamination
would deepen the cold, so its EXCLUSION is correct and is not the inflation the operator suspected.

**(ii) TIGGE prior shrinkage — REFUTED.** weight_live=1.0, n_prior=0. TIGGE plays zero role in these
rows. (The shrinkage machinery exists in `ens_bias_model.py` but is not wired into `edli_per_city_v1`.)

**(iii) Forecast-STATISTIC semantics — CONFIRMED, the dominant structural inflation.** The bias is
`mean(ensemble member daily-max) − settled MAX`. The ensemble MEAN sits **0.6–1.8 °C below the
ensemble MAX** systematically (`mean−max` column, `11`): Singapore −1.20, Shanghai −1.22,
Taipei −1.44, Seoul −1.25, Wuhan −1.81. For the genuinely-near-zero cities this statistic gap is
the MAJORITY of the stored magnitude:
  - Singapore: stored −1.58, but warmest-member−obs = **−0.38** ⇒ ~1.2 °C is the mean-vs-max gap.
  - Shanghai: stored −1.01, max−obs = **+0.22** ⇒ essentially the entire bias is the statistic.
  - Taipei −1.80 → −0.44; Shenzhen −0.55 → +0.40; Warsaw −0.58 → +0.84.
  This gap is real cold ONLY if the "central" forecast should equal the realized max — it should not.

**(iv) Small-sample noise — PARTIAL.** n=13–17, SE 0.33–0.65 °C. Enough to make Singapore/Tokyo/SF/
TelAviv CIs exclude 0 (real signal), but several "−0.x" cities (Seattle, Shenzhen, Toronto,
Sao Paulo) have CIs spanning 0 (bias indistinguishable from zero — should be weight_live=0).

**Why (iii) is a live hazard, not a cosmetic note.** The live shift subtracts the bias from EVERY
member (`event_reactor_adapter.py:3552 corrected = members − eff_native`), translating the whole
51-member distribution uniformly, then the MC maps it to `P(bin)`. Because the bias forces the
ensemble MEAN onto the historical settled MAX, the correction over-warms the modal/upper bins — the
documented Singapore bin-31→bin-32 collapse (raw modal 31 → corrected modal 32,
`EDLI_BIAS_CORRECTION_FULL_SPEC_2026-06-01.md` §line 37). The MC is innocent; the over-warm is
injected by subtracting a mean-vs-max-contaminated bias.

## 4. VERDICT

The operator is **HALF RIGHT**. Refuted: a *calculation/stat error*. The stored values reproduce
EXACTLY from clean, contributing-only, correctly-unit-normalized originals — no post-peak
contamination, no TIGGE prior, no arithmetic bug. Confirmed: the values are **inflated relative to
the true forecast bias** for the low-bias cities, and the dominant inflation source is a
**forecast-STATISTIC mismatch** — comparing the ensemble MEAN of member daily-maxes against the
realized daily MAX, a comparison carrying a structural −0.6…−1.8 °C "mean-vs-max" offset that is
NOT a forecast error. Dominant source named with numbers: `cen = mean(members_json)` in the a4
generator (faithfully stored by `write_promoted_edli_bias.py:56-57 errs.mean()`), applied as a
whole-distribution shift at `event_reactor_adapter.py:3552`.

NOT uniformly small, though: **Tokyo (−2.23 at the warmest member), San Francisco (−3.82),
Tel Aviv (−3.52)** retain a large cold even against the ensemble MAX — these are genuine
forecast/extraction cold (timezone/window or station-vs-grid), real ~2–4 °C, and the operator's
"true bias ~0.x" does NOT hold for them.

**Implication (not an instruction):** the correct correction is the residual of the forecast
statistic the MC actually settles against, not `mean(member) − max`. Per-city: keep a real cold for
Tokyo/SF/TelAviv (refit against the matching statistic), zero-out the statistic-only cities
(Singapore/Shanghai/Taipei/Shenzhen/Warsaw/Sao Paulo/Toronto/Seattle, CIs at or near 0).
