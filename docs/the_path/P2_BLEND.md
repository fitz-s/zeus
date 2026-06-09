# P2_BLEND — The superior blend for The Path's 0.1 forecast posterior

> Created: 2026-06-07
> Last reused/audited: 2026-06-07
> Authority basis: REALIGN_0_1_AUTHORITY.md (live authority = `replacement_0_1` = AIFS sampled-2t members + Open-Meteo ECMWF-IFS 0.1deg deterministic soft-anchor, flag `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled=true`); OBSERVE_BASELINE.md (the before-number / bin-selection failures: Tokyo 0/5, KL 0/4); QLCB_HONESTY.md (the q_lcb underdispersion track, which this doc composes with but does NOT duplicate); settlement truth = `zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'`.
> Method: READ-ONLY (sqlite `mode=ro`) on LIVE DBs in `/Users/leofitz/zeus/state`; construction code read at the live commits in `/Users/leofitz/zeus`. No code/config writes. Two measurement lenses (current-skill census + candidate-blends), each cross-checked against the DBs and an independent walk-forward reproduction by this consolidation pass.
> Goal frame: SKILL = does the predicted distribution match settlement (bin-hit, calibration/PIT, CRPS). SKILL is PATH-INDEPENDENT and validatable on VERIFIED history. q_lcb COVERAGE and after-cost win-rate are COHORT-DEPENDENT and are deliberately NOT claimed here — they gate on the post-redeploy live settled cohort (the q_lcb-floor cohort-mismatch trap is avoided by design).

> Verification log (this consolidation pass, live state 2026-06-07):
> - Truth: `settlement_outcomes` 6640 VERIFIED, 2024-01-01→2026-06-06. Confirmed.
> - Live posteriors: `forecast_posteriors` method `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` = 171 rows, ALL target 2026-06-08/09 (unsettled), q_lcb_json NULL on 171/171, 0 rows on any settled date (`target_date <= 2026-06-06`). Confirmed → live method is NOT directly history-validatable today.
> - Proxy: `ensemble_snapshots` model_version `ecmwf_ens` = 1,154,218 rows, 54 cities, 2024-01-01→2026-06-13 (zeus-forecasts.db). The legitimate 0.1-CENTER proxy (same ECMWF family).
> - Bias machinery already built: `zeus-world.model_bias_ens` (153 rows), `model_bias` (165 rows) — same EB statistic this doc validates.
> - Second model for blend candidate (b): `zeus-world.historical_forecasts` GFS (34,811) + ECMWF (34,237) point centers, **HIGH only** (`forecast_high`, no `forecast_low`).
> - Construction code: zero-prior veto at `openmeteo_ecmwf_ifs9_aifs_soft_anchor.py:197-198` confirmed; raw-vote prior `count/total_members` at `ecmwf_aifs_sampled_2t_probabilities.py:275` confirmed; EMOS single-sigma coupling docstring at `emos_q_builder.py:50` confirmed; replacement-path lcb at `event_reactor_adapter.py:5414/5424` (Wilson branch) confirmed; coverage helper called ONLY at `:5699` (canonical path), NOT inside the replacement path (`:5430`).
> - INDEPENDENT REPRODUCTION (this pass, walk-forward, ENS proxy, lead~24h, high, n=5285): RAW bin-hit 0.170 → EB-bias-corrected 0.235; meanPIT 0.697 → 0.546; global member-mean bias −1.010C. Reproduces candidate-blends headline from scratch.

---

## TL;DR

The live AIFS+0.1 method has **no settled history** to validate on (0 settled posteriors; recompute is bounded to ~205 cells at ~1-day lead, far short of the 2-3 day trade lead), so its skill cannot be measured directly today — but its bin-selection failure IS structurally diagnosable and is the same defect across both the live method and its ECMWF-family proxy. The defect is **systematic center BIAS** (member cloud runs ~−1.0C cold), made catastrophic by a **structural zero-prior veto** (`soft_anchor.py:197-198`: any bin with zero AIFS member votes is hard-set to −inf, so the soft-anchor cannot place mass where no member voted). The simplest method that improves settlement skill in a MAJORITY of cities is **per-city walk-forward Empirical-Bayes bias-correction of the center** — pooled bin-hit ~0.19→0.26 (+36% rel), meanPIT ~0.68→0.54, CRPS ~1.50→1.21, better in 35/50 cities, holds out-of-sample. It is FIRST-ORDER and reuses already-built machinery (`model_bias_ens`). Multi-model GFS+ECMWF blending is real but second-order (bias-correction alone beats raw blending); dispersion widening does NOT improve bin-hit (it is a q_lcb/coverage fix, not a skill fix). **Recommend bias-correction alone; never learned weights (B6/B7). No kitchen sink beats the first-order fix.** SHIP GATE = post-redeploy live settled-cohort skill+coverage, NOT the historical cohort.

---

## (1) Current 0.1 forecast-skill baseline + the bin-selection failure diagnosis

### 1a. There is NO directly-measurable skill number for the live method

`forecast_posteriors` carries 171 rows for the live method `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` (147 high + 24 low), and **every one targets 2026-06-08/09** (unsettled; VERIFIED truth ends 2026-06-06). `q_lcb_json` is NULL on 171/171. **Zero live-method posteriors target a settled date.** The only honest route to a history number is RECOMPUTE from raw inputs, and those exist only for a ~5-day window (AIFS GRIB cycles 2026-06-02 06z .. 06-05 06z; Open-Meteo 0.1 anchor cache same cycles, 49 cities), recompute ceiling ~179 high + 26 low cells (target 06-03..06-06), ALL at short lead (AIFS steps reach only 24-30h) — **NOT the 2-3 day lead the live system trades.** So the live method is honestly validatable only on ~205 cells at ~1-day lead. A true walk-forward AIFS+0.1 skill curve over months is not buildable from current data. There is NO AIFS-member settled archive anywhere.

The nearest persisted skill comparison (tournament `db_overlap_b0_r1_r2_skill_executive`, N=55) scores ECMWF-ENS dataset *variants*, not the AIFS soft-anchor, and its verdict was `NO_FORECAST_SKILL_WINNER` (replacement variants improved Top1 marginally but WORSENED Top3/Brier/LogLoss — sharper mode, worse tails).

### 1b. The honest "before" = the structurally-identical ECMWF-family proxy

Because the live method has no settled history, skill is measured on `ensemble_snapshots model_version='ecmwf_ens'` (TIGGE/OpenData ECMWF ENS, 51 members, 1.15M rows, 54 cities, 2024-01..2026-06), which is the same ECMWF family and exercises the identical member→bin→soft-anchor math. **Bias and scale findings TRANSFER (same family); a true cross-family AIFS-specific magnitude does NOT — it must be re-fit once June+ AIFS posteriors settle.**

Proxy "before" (walk-forward, lead~24h, high, joined to VERIFIED, this pass n=5285; candidate-blends n=4982): **raw ENS center bin-hit ≈ 0.17-0.19, meanPIT ≈ 0.68-0.70 (ideal 0.5), CRPS ≈ 1.50.** On the member-vote diagnostic (n=2141): argmax-bin Top1 = 17%; **zero-prior-trap (settle bin got 0 member votes) = 40%; settlement entirely outside the member cloud = 52%.** So ~half of all cells are structurally un-hittable before any calibrator runs.

### 1c. Bin-selection diagnosis: center BIAS + structural zero-prior veto (NOT dispersion, NOT wrong-model, NOT calibrator mis-map)

It is BOTH a directional center error AND a too-tight cloud, made catastrophic by a hard structural veto:

- **STRUCTURAL (the trap).** `openmeteo_ecmwf_ifs9_aifs_soft_anchor.py:197-198` sets `log_term=-inf` whenever `prior_probability <= 0.0`. The docstring (:178-181) is explicit: the anchor is "not allowed to create mass for bins that the AIFS posterior assigned zero probability." The AIFS prior is RAW member-vote frequency `count/total_members` (`ecmwf_aifs_sampled_2t_probabilities.py:275`) with NO noise/MC/kernel smoothing. So any bin with zero member votes is nailed to posterior 0 — no matter how close the 0.1 anchor center sits to it. This is why ~40-52% of cells are un-hittable.
- **BIAS (dominant in the misses).** member-mean − settlement = **−1.0C** (this pass: −1.010C; both lenses: −1.0C), persistent across all 29 months (−0.5 to −1.4C). PIT exposes it as LOCATION not scale: meanPIT 0.68-0.70 means the observation sits in the UPPER tail of the cloud. The loser cities are exactly the high-PIT cities — Tokyo 0.805, Kuala Lumpur 0.942, Singapore 0.877, Busan 0.960 — i.e. settlement lands ABOVE the whole cloud (a pure center miss). Among outside-cloud misses, 86% are ens-too-COLD.
- **DISPERSION (secondary, the q_lcb track).** the member cloud is ~2.75-3.24x too tight (within-ens spread ~0.67C vs realized residual sd ~1.9-2.2C). This is the SAME underdispersion QLCB_HONESTY owns; it governs q_lcb coverage/ruin, NOT which bin. Widening alone leaves the center wrong (Tokyo bin 0.222→0.206).

The Tokyo/KL/Shanghai failure mechanism = the cloud is shifted cold and too narrow to reach a warmer settlement, then the zero-prior law nails the posterior to 0 in the bin that actually settles. The 0.1 deterministic anchor cannot fix this because its likelihood is vetoed in zero-prior bins and `anchor_sigma_c=3.0` widens the POINT q only, among bins that already have member mass.

---

## (2) Candidate blends — before/after settlement-skill table

Measured on SKILL only (bin-hit, PIT calibration, CRPS), walk-forward (train-cutoff < target, zero lookahead), parsimony-bounded. q_lcb coverage / after-cost win-rate NOT claimed (cohort-dependent; gate on live cohort). All on the ECMWF-ENS 0.1-center proxy; AIFS-specific magnitude pending June settlements.

### POOLED (lead=1, walk-forward)
| candidate | bin-hit | CRPS | meanPIT | verdict |
|---|---|---|---|---|
| BEFORE — raw ENS center | 0.191 | 1.50 | 0.684 | baseline |
| **(a) per-city EB bias-correction** | **0.259** | **1.21** | **0.537** | **WINNER (first-order)** |
| (c) dispersion widening to realized residual | 0.190 | 1.33 | 0.626 | NO bin gain — coverage fix, not skill |
| (a)+(c) bias + widen | 0.259 | 1.17 | — | same bin as (a), best CRPS |

Independent reproduction this pass (n=5285, argmax-bin definition): RAW 0.170 → EB 0.235 bin-hit; PIT 0.697 → 0.546 — same direction and magnitude, robust to the bin-hit definition.

### Majority test (cities, n≥20)
| candidate | bin-hit better | CRPS better |
|---|---|---|
| (a) EB bias-corr | **35/50** | 41/50 |
| (c) widen | 8/50 | 47/50 |
| (a)+(c) | 35/50 | 47/50 |

### Per-city (BEFORE→EB bin-hit; meanPIT)
Tokyo 0.222→0.254 (PIT 0.805→0.587); Kuala Lumpur 0.097→0.129 (0.942→0.751); Singapore 0.094→0.415; San Francisco 0.000→0.146; Busan 0.061→0.152; Istanbul 0.118→0.176; Paris 0.257→0.338; London 0.306→0.366; NYC 0.080→0.128; Seoul 0.254→0.275.

### Robustness + out-of-sample
- Across 9 param combos (eb_prior {4,8,16} × min_train {3,5,10}): bin-hit 0.256-0.265, PIT 0.53-0.55 — **insensitive = structural, not overfit.**
- Temporal holdout (test 2026 only, bias from prior): bin-hit 0.164→0.233 (+6.9pt), CRPS 1.75→1.35, PIT 0.700→0.562, majority 35/50 — **holds out-of-sample.**

### Candidate (b) — multi-model GFS+ECMWF center blend (n=2642, high, lead=1)
RAW: gfs 0.143, ecmwf 0.153, equal-blend 0.160. EB-corrected: gfs 0.196, ecmwf 0.194, **EB-blend 0.217**. The decisive comparison: **EB-single-ecmwf (0.194) > RAW-blend (0.160)** — bias-correction DOMINATES blending. EB-blend (0.217) beats EB-ecmwf (0.194) only in 26/51 cities (bare majority). Blending is real but SECOND-ORDER, and (b) is HIGH-only — `historical_forecasts` has no `forecast_low`, so the LOW metric multi-model blend is untestable today.

---

## (3) RECOMMENDED superior blend

**Per-city walk-forward Empirical-Bayes bias-correction of the forecast center, applied BEFORE the soft-anchor zero-prior veto. Nothing else.**

This is the simplest method that improves settlement skill in a MAJORITY of cities (35/50 bin-hit, 41/50 CRPS), holds across param choices and out-of-sample, and directly targets the diagnosed failure (the −1.0C cold center that pushes settlement into upper-tail / zero-prior bins). It is first-order; equal-weight GFS+ECMWF blending adds a bare-majority second-order increment and never beats bias-correction alone; dispersion widening does NOT improve bin selection (it belongs on the q_lcb track). **Do NOT add learned weights (B6/B7 proved they add ~nothing) and do NOT add a kitchen-sink blend** — per the parsimony memo, superior = simplest method that improves settlement skill.

One subtlety for the live path specifically: bias-correcting the center is necessary but the **zero-prior veto** (`soft_anchor.py:197-198`) will still nail un-voted bins to 0. The parsimonious companion is to inject a SMALL dispersion kernel into the AIFS raw-vote prior BEFORE the veto (so a bias-shifted-but-un-voted near-tail bin can receive mass), median miss is only ~1.06C beyond the cloud edge so a modest kernel recovers most. This is a structural prerequisite for the bias-correction to actually move the live posterior; it is NOT a second blend.

HONESTY CEILING: even bias-corrected, pooled bin-hit ~0.26 on 1C bins — many cities are near the irreducible-error floor (1C bin vs ~2.0-2.4C RMSE). The claim is "removes a measurable systematic miss," NOT "makes the forecast sharp."

---

## (4) Live-ship design (flag-gated, default-OFF, gated on the LIVE cohort)

### Exact change surface
- **Bias source (reuse, do not rebuild):** `zeus-world.model_bias_ens` (153 rows, per city/season/metric `posterior_bias_c` + sd) already holds the EB statistic this doc validates. The blend consumes it; it does not refit a parallel system.
- **Apply point:** correct the center fed into the soft-anchor in the replacement-path construction (`src/strategy/openmeteo_ecmwf_ifs9_aifs_soft_anchor.py` `build_soft_anchor_posterior` — shift `anchor_c` and the AIFS member values by the per-(city,season,metric) bias) and/or at the AIFS prior build in `ecmwf_aifs_sampled_2t_probabilities.py:275` before bins are counted. Companion: add a small dispersion kernel to the raw-vote prior at the same site so the zero-prior veto (`soft_anchor.py:197-198`) does not nail bias-shifted near-tail bins to 0.
- **New flag (default-OFF):** `feature_flags.replacement_0_1_eb_bias_correction_enabled = false`. When false the path is byte-identical to today. When true, the center is bias-corrected before the veto. Gate it the SAME way the live authority flag is gated (`event_reactor_adapter.py` `_replacement_authority_enabled` pattern at the replacement path entry ~`:5430`).

### SHIP GATE (the cohort-mismatch trap, avoided by design)
**The blend may NOT be promoted from shadow to live on the historical-cohort skill numbers above.** Those numbers are the ECMWF-family proxy at ~1-day lead — they justify BUILDING the blend, not SHIPPING it. The q_lcb-floor lesson (the floor validated on the OLD buy_yes lottery cohort and did NOT transfer) is the exact trap to avoid: a historical-cohort win does not license a live edge.

Ship sequence:
1. **Shadow first.** Run the bias-corrected path in shadow (flag-on in a shadow run only). It writes the bias-corrected posterior alongside the live one WITHOUT affecting trades. Log how many bins move and by how much.
2. **Settle the live cohort.** Wait for ≥30 post-redeploy live `replacement_0_1` markets to settle against VERIFIED truth (markets 06-08+ settle 06-09+; the AIFS-specific bias magnitude is unknown until then).
3. **PASS bar on the LIVE settled cohort** (NOT the historical proxy):
   - bin-hit (and/or CRPS) of the bias-corrected posterior ≥ the un-corrected posterior on the live settled cohort, in a MAJORITY of cities with n≥ a per-city floor;
   - meanPIT of the bias-corrected posterior closer to 0.5 than the un-corrected (the direct test that the −1.0C center error transferred and was removed);
   - AND the q_lcb coverage condition (§5) must not regress.
4. **Antibody (mandatory).** A relationship test asserting that, on the FULL VERIFIED settled population, the bias-corrected center's mean PIT ∈ [0.45, 0.55] per (city, season, metric) with n≥30, and that no bin with zero member votes can be assigned posterior 0 if the bias-corrected anchor center falls inside it (makes the un-hittable-bin category unconstructable). A failing test = stage-1 antibody; the deployed bias-correction + kernel = full antibody.

Only after (3) passes on the LIVE cohort is the default flipped ON. Until then the historical numbers are a build justification, never a ship license.

---

## (5) Composition with the q_lcb floor (calibration) + the evidence gate (authority)

These are THREE orthogonal layers; this doc owns ONLY skill (which bin). They must be layered in the right order.

- **vs the q_lcb floor (QLCB_HONESTY's track).** Bias-correction fixes SKILL (the center / which bin); the settlement_sigma_floor + K3 settlement-coverage shrink fix HONESTY (q_lcb coverage / over-confidence). They are ORTHOGONAL but ORDER-COUPLED: a correctly-widened q_lcb on a still-biased center just covers the WRONG location (QLCB open-Q #3). So **bias-correction MUST precede or accompany any widening.** The EMOS builder couples them through ONE sigma — `emos_q_builder.py:50` docstring: "the point q and the q_lcb derive from ONE sigma" — so once the center is bias-corrected, the same (mu, sigma) that feeds the point q feeds the lcb, and the q_lcb floor (median 3.18C, `settlement_sigma_floor.json`, already flag-on) caps any single 1C-bin lower bound at the honest ~0.12 ceiling. The blend changes mu; the floor governs sigma. Do not let the blend touch sigma.
- **vs the evidence gate (authority, REALIGN_0_1).** Live authority today is granted by flag ALONE (`_replacement_authority_enabled` returns the flag with no settlement-validated evidence check). The new blend flag rides UNDER the authority gate: even with the blend on, the path only trades when authority is granted. The ship gate's "≥30 live settled markets" requirement is itself an evidence condition — the blend's promotion is the same evidence-before-license discipline REALIGN_0_1 demands of the authority path. The blend never widens authority; it only changes the center of a posterior that authority has already licensed.
- **Net layering order:** evidence gate (may we trade this cell at all?) → bias-correction (which bin is the center, this doc) → dispersion / q_lcb floor (how wide / how honest is the lower bound, QLCB_HONESTY). The blend slots strictly in the middle and must not reach across into either neighbor's sigma or authority decision.
