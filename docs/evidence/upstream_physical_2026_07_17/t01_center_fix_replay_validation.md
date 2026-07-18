# T0-1 center-fix (slice 1) — settlement-graded replay validation

Date: 2026-07-18. Read-only. Scope: replay-validate the slice-1 fix under
implementation in parallel — shift the Day0 remaining-window center by
`delta = whole-local-day extreme (ecmwf_ifs anchor) - remaining-window extreme`
before feeding it into the existing conditioner, and test whether that repairs
the post-peak/eve over-dispersion CONFIRMED at tier0 in
`day0_mechanism_first_principles_audit.md` §7 (HIGH post-peak served
P(new higher high)=0.314 vs realized 0.070, 4.50x; LOW eve served
P(new lower low)=0.409 vs realized 0.000).

Script: `t01_center_fix_replay.py` (shared scratchpad). Output:
`t01_replay_output.txt` (full run, incl. per-market delta detail for the
post/eve buckets).

## 0. A pipeline-mechanics correction found and fixed mid-task

The task brief's working assumption — that the served q feeds the
support-clamped `center_after_native = max(mu_before, obs)` into
`normal_cdf` — is **wrong**, and the first replay draft built on it produced
a large, stratum-concentrated self-consistency gap (mean |replay − served|
≈ 0.10 overall, up to 0.32 on individual post-peak/EOD rows) that looked at
first like "my Normal replay only weakly approximates a richer served
pipeline." Tracing the actual call path
(`replacement_forecast_materializer.py:3136` →
`_day0_conditioned_bin_probability:3369` → `_cdf(x) = _normal_cdf(mu=mu,
sigma=sigma, x=x)`) shows **`mu` is the raw, UNCLAMPED fused center** —
no `max(mu, obs)` is ever applied before the CDF call. The
`Day0Conditioning.center_after_native` field in `day0_conditioner.py` is a
**receipt-only** value; its own docstring says so explicitly ("the
conditioned probabilities above are exact regardless of where mu_before sits
relative to the observed extreme — the clamp records the support-corrected
center on the receipt"), which the first draft misread as "clamp before
calling normal_cdf." The three-branch transform
(impossible/straddle/ordinary) already performs the conditioning
structurally; no external clamp is needed or applied.

Fixed replay (`mu` fed unclamped): **self-consistency gap = 0.0000** across
all 145 used markets (maxdiff spot-checked at 0.0 across cycle phases
including near-end-of-day rows) — the single-Normal + exact preimage
transform reproduces the served `q_json` **byte-exact**, not merely
directionally. This also means the corrected-lane replay below is not an
approximation of the fix; it is the exact pipeline formula with `mu`
replaced by `mu - delta` (HIGH) / `mu + delta` (LOW), no re-clamp needed
(the branch logic handles obs-relative positioning regardless of where `mu`
sits).

## 1. Method

- **Join**: `forecast_posteriors` (latest `computed_at` per (city,
  target_date, metric), `day0_conditioning.active=1`,
  `observed_extreme_c` present) `JOIN settlement_outcomes` (`VERIFIED`,
  non-null `settlement_value`) — verbatim §7 dedup. Deduped HIGH n=795, LOW
  n=112 (same order of magnitude as §7's 795/112).
- **Provenance-sourced inputs** (no fitting): `mu = provenance.anchor_value_c`
  (the fused center — confirmed identical to `bayes_precision_fusion.
  anchor_value_c` in every row spot-checked), `sigma =
  provenance.settlement_sigma_floor_c` (fallback
  `bayes_precision_fusion.predictive_sigma_c` when the floor field is
  `None`, which is common — the floor is not always the binding
  constraint), `obs = day0_conditioning.observed_extreme_c`.
- **Bin integration**: `provenance.bin_topology` gives each bin's
  `(lower_c, upper_c, rounding_rule, settlement_step_c)` — the LABEL bounds,
  not yet the settlement PREIMAGE. Preimage offset computed per-bin via
  `settlement_preimage_offsets(rounding_rule, half_step=settlement_step_c/2)`
  (confirmed `oracle_truncate` on every Hong Kong bin, `wmo_half_up`
  elsewhere), then the exact `probability_high_day0_bin` /
  `probability_low_day0_bin` three-branch transform. This **sidesteps the
  q_json-label regex parser class of bug entirely** (§6/§7's C-unit
  plain-bin miss) since `bin_topology`'s bin_id strings are byte-identical
  to `q_json` keys and the classification is by provenance-carried numeric
  bounds, not text parsing — though the PLAIN-fixed regex (from
  `t01_fixed_corrected.py`) is still used downstream to classify "beyond
  obs" bins for both served and replayed columns identically (0/8,745 HIGH,
  0/1,232 LOW keys unparsed — reconfirmed here).
- **Anchor vector join**: `day0_hourly_vectors` model=`ecmwf_ifs`, latest
  `captured_at <= computed_at` for (city, target_date). `delta` = whole-local
  -day extreme minus remaining-window extreme of THIS SAME hourly vector,
  remaining = entries with local wall-clock `>= computed_at` (converted via
  `zoneinfo(timezone_name)`); if none remain, use the day's last entry as a
  single-point remaining set. `HIGH: mu_corr = mu - max(0, whole_max -
  remaining_max)`. `LOW: mu_corr = mu + max(0, remaining_min - whole_min)`.
  No re-clamp (see §0).
- **Stratification**: fixed lon-based solar-hour buckets, verbatim §7 (HIGH
  pre<12/peak 12-16/post>16; LOW overnight<5/trough 5-8/day 8-18/eve>18).
- **Three columns per market**: `served` (from `q_json` directly, PLAIN-fixed
  label regex), `replayed-uncorrected` (my own bin-transform with `mu`
  unmodified), `replayed-corrected` (same transform with `mu ± delta`).
  `realized` identical to §7 (`round(settle) vs obs_bin`).

## 2. Join coverage — the dominant limitation

**`day0_hourly_vectors` only has `ecmwf_ifs` rows for `target_date` 2026-07-15
through 2026-07-19** (10,621 rows total, 5 days) — the table appears to be a
recently-started capture, not a backfilled history. Settled Day0 markets with
`day0_conditioning.active=1` span 2026-06-19 through 2026-07-17. Consequence:

| metric | settled markets | usable vector join | coverage |
|---|---|---|---|
| HIGH | 795 | 126 | **15.8%** |
| LOW | 112 | 19 | **17.0%** |

Every usable market falls on target_date 2026-07-15/16/17 (the 3-day overlap
window). Zero markets were dropped for a parse or lon-lookup reason
(`n_no_lon=0`, `n_delta_lt2day=0` for both metrics) — 100% of the loss is
vector-table date-range coverage, not a data-quality defect in this analysis.
**This means the validation below rests on the last 3 days of the trading
history, not the full settled dataset §7 used.** It is real settlement-graded
evidence, but a materially smaller and more recent sample than §7's headline
numbers, and it does not per se speak to whether the same distribution held
across the whole 4-week window (though there's no structural reason it
wouldn't — `ecmwf_ifs` is present for 6/6 continuously-running cities and no
city was systematically excluded by the join).

## 3. HIGH — three-column stratified table

| stratum | n | served | replayed-uncorrected | replayed-corrected | realized | ratio_served | ratio_uncorr | **ratio_corrected** |
|---|---|---|---|---|---|---|---|---|
| pre (<12) | 3 | 0.9617 | 0.9617 | 0.8297 | 0.6667 | 1.44 | 1.44 | **1.24** |
| peak (12–16) | 31 | 0.4331 | 0.4331 | 0.2902 | 0.2903 | 1.49 | 1.49 | **1.00** |
| post (>16) | 92 | 0.3036 | 0.3036 | 0.0320 | 0.0326 | 9.31 | 9.31 | **0.98** |

Self-consistency: `served == replayed-uncorrected` in every stratum (gap =
0.0000, n=126) — the byte-exact match from §0.

**Accept criteria (per task brief): post-peak ratio should move toward 1 and
land ≤2.0, with pre-peak staying within [0.6, 1.4] of its own uncorrected
ratio.**
- post(>16): 9.31 → **0.98** — passes decisively, and lands almost exactly at
  1.0, not merely under the 2.0 bar.
- peak(12-16): 1.49 → **1.00** — also moves to near-perfect calibration
  (not required by the accept criteria, but a clean bonus result).
- pre(<12): ratio_corrected/ratio_uncorrected = 1.24/1.44 = **0.86**, inside
  [0.6, 1.4] — passes, though n=3 is too small to lean on.

## 4. LOW — three-column stratified table

No settled LOW markets landed in the overnight(<5) or trough(5-8) buckets
within the 3-day vector-coverage window (small-sample artifact of §2, not a
code finding).

| stratum | n | served | replayed-uncorrected | replayed-corrected | realized | ratio_served | ratio_uncorr | **ratio_corrected** |
|---|---|---|---|---|---|---|---|---|
| day (8–18) | 7 | 0.6647 | 0.6647 | 0.5823 | 0.2857 | 2.33 | 2.33 | **2.04** |
| eve (>18) | 12 | 0.2711 | 0.2711 | 0.0978 | 0.0000 | inf | inf | **inf** |

- **eve(>18)**: realized is exactly 0.000 across all 12 markets (the low
  never re-broke past the observed trough), so the ratio is undefined
  (`x/0`) regardless of correction — it cannot numerically satisfy a
  "ratio ≤ 2.0" criterion by construction. The informative number is the
  **absolute reduction in misplaced mass**: served/uncorrected 0.2711 →
  corrected 0.0978, a **64% cut** in probability mass wrongly placed on "new
  lower low" outcomes that in fact never happen in this sample. That is
  real, large, and in the right direction, but does not "pass" a
  ratio-based accept test — flagging this as the task brief's accept
  criteria didn't anticipate a realized-mean-exactly-zero stratum.
- **day(8-18)**: 2.33 → 2.04, n=7. Directionally correct but modest, and
  does not clear the ≤2.0 bar; n=7 is too small to treat as resolved either
  way.

## 5. Straddle-bin analysis (the other side of the same coin)

Mean served/replayed q on the bin containing the observed extreme, vs. that
bin's own realized win-rate (fraction of markets that actually settled
there) — over-dispersion manifests as this bin being under-weighted.

**HIGH**

| stratum | n | served_q | uncorrected_q | **corrected_q** | realized win-rate |
|---|---|---|---|---|---|
| pre (<12) | 3 | 0.0383 | 0.0383 | 0.1703 | 0.3333 |
| peak (12–16) | 31 | 0.3371 | 0.3371 | 0.4089 | 0.6774 |
| post (>16) | 92 | 0.6738 | 0.6738 | **0.9362** | 0.9565 |

Post-peak: the straddle bin was under-weighted by 0.283 absolute
(0.6738 vs 0.9565 realized) before the fix; after, the gap is 0.020 — a
**93% reduction** in the under-weighting. This is the mechanical mirror of
§3's post-peak "beyond obs" collapse: the same corrected mass that stopped
sitting on impossible-adjacent bins moved onto the bin that actually wins.

**LOW**

| stratum | n | served_q | uncorrected_q | **corrected_q** | realized win-rate |
|---|---|---|---|---|---|
| day (8–18) | 7 | 0.3353 | 0.3353 | 0.4177 | 0.7143 |
| eve (>18) | 12 | 0.7289 | 0.7289 | **0.9022** | 1.0000 |

Eve: gap shrinks from 0.271 to 0.098 (a 64% reduction, matching §4's
mass-reduction figure exactly, as it must — beyond-mass and straddle-mass
are complements up to the small below-obs tail).

## 6. Per-market spot checks (post-peak / eve, full list in
`t01_replay_output.txt`)

Representative rows (all 2026-07-15/16/17, all settle==obs, i.e. no new
extreme actually happened — exactly the regime the fix targets):

- **Tokyo 2026-07-15** (obs=33.0, settle=33.0, mu_before=31.10, delta=4.70,
  n_rem=1 hour left in the ecmwf vector): served/uncorrected beyond-obs mass
  0.043 (already thin here since mu_before < obs) → corrected 0.000.
- **Manila 2026-07-15** (obs=33.0, settle=33.0, mu_before=34.09 — forecast
  center still ABOVE the already-observed peak — delta=4.30): served 0.798
  (badly over-dispersed: the whole-day center hadn't caught up to the fact
  that the peak already happened) → corrected 0.000.
- **Shenzhen 2026-07-15** (obs=28.0, settle=29.0 — an actual new-high
  market, one of the few in this sample): served 0.859, corrected 0.000.
  This is a genuine miss by the corrected replay — a settle that DID move
  past obs, on a market the correction drove to near-zero. n_rem=1 (only
  the last vector hour counted as "remaining"), so the correction had
  almost no cushion. This is exactly the kind of case the aggregate ratio
  (0.98, near 1.0) already prices in: post-peak corrections will
  occasionally under-shoot on the rare actual-new-extreme market, and the
  aggregate is calibrated on average, not row-by-row.
- **Toronto 2026-07-15** (obs=28.0, settle=28.0, mu_before=33.72 — a large
  positive forecast-vs-obs gap, delta=3.80 only): served 0.933, corrected
  0.658 — biggest post-peak improvement in absolute terms, but still far
  from 0; delta (3.80) didn't fully close the mu_before-obs gap (5.72) here,
  because delta is bounded by what the SAME single ecmwf_ifs model's own
  trajectory implies, not by an arbitrary re-centering to obs.

`n_rem` is 1 for the large majority of post/eve rows — see §7 for why, and
the honesty note on what this does and does not imply.

## 7. Honesty: why `n_rem=1` is not a bug, and its implication

Per §6.1 the dedup rule picks the LAST `computed_at` per (city, target_date,
metric) — the materializer re-runs every cycle through the day, so the
"latest" row for a settled market is typically the **closing** row, minutes
to an hour before local midnight. That is why the remaining-window vector
slice is usually just the day's last hour: this is the correct,
harshest-possible test of the T0-1 defect (by definition almost nothing can
still change in the last local hour, so ANY served mass on beyond-obs bins
at that point is closer to indefensible than at an earlier cycle) — it is
also why §7's own headline post-peak ratio (4.5x) is so large in the first
place. The delta-fix's job at this specific cycle phase is close to "pull mu
all the way to what the model itself expects for the final hour," and the
replay shows it does that job well in aggregate (ratio 9.31→0.98). It does
NOT validate the fix's behavior at EARLIER cycle phases (mid-afternoon, 6+
remaining hours) as thoroughly, since the "latest computed_at" dedup
under-samples those — the peak(12-16) bucket (n=31, ratio 1.49→1.00) is the
best available evidence for a mid-day cycle phase and its correction also
looks clean, but n is modest.

## 8. Verdict

**Fix VALIDATED for HIGH, on this sample.** HIGH post-peak — the stratum §7
identified as the tier0 defect's sharpest expression (4.50x / 9.31x
depending on baseline) — collapses to 0.98 under the delta correction, and
peak(12-16) improves from 1.49 to 1.00 alongside it, with pre(<12) staying
inside the required stability band (0.86 of its own uncorrected ratio). The
straddle-bin under-weighting (the complementary defect) shrinks 93% in the
same stratum. Self-consistency between the replay and the live served q is
byte-exact (0.0000 gap) once the correct (unclamped-`mu`) pipeline mechanics
were used, so this is a direct test of the fix's arithmetic, not merely a
loose approximation.

**Fix PARTIALLY VALIDATED for LOW, sample too thin and one stratum has an
unmeasurable ratio.** LOW eve shows a real, large (64%) reduction in
misplaced mass and in straddle-bin under-weighting, but the realized rate is
exactly 0.000 across all 12 markets, so the ratio accept-criterion (≤2.0)
is structurally unsatisfiable regardless of how good the correction is —
report the absolute-mass reduction instead of forcing a ratio verdict here.
LOW day(8-18) improves only modestly (2.33→2.04, n=7) and does not clear the
≤2.0 bar; this cell is too small to call resolved in either direction.

**Coverage caveat (binding on both verdicts): the day0_hourly_vectors table
only covers 2026-07-15 through 07-19, giving 15.8%/17.0% join coverage and
restricting every result above to the last 3 days of the settled-market
history, not the full 4-week window §7 measured.** The direction and rough
magnitude of the fix's effect (order-of-magnitude ratio collapse in the
worst stratum, straddle-bin under-weighting closing by >90%) is unambiguous
on the data available, but a wider vector backfill (or waiting for more days
to accumulate under live capture) is needed before treating the LOW-day and
pre/peak HIGH cells, or the sample size generally, as settled.

Files: `/private/tmp/claude-501/-Users-leofitz-zeus/7589dc75-d443-4b7f-8e2b-24945ef3038c/scratchpad/t01_center_fix_replay.py`,
`.../t01_replay_output.txt`.
