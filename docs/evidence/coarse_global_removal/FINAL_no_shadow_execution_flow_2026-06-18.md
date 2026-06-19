# FINAL no-shadow execution flow — RAW diagonal-precision fusion (line-level verified, 2026-06-18)

- Created: 2026-06-18
- Authority: operator (no-shadow, no-de-bias, settlement-only). Final ChatGPT consult REQ-...cf39b2 (read the
  REAL source via gist 56f64dbf) + local line-level verification (Claude Code = source of truth). Supersedes
  the shadow-bearing `consult_signoff_execution_flow_2026-06-17.md`.
- NOTE on consult line numbers: the consult cited gist-relative line numbers (e.g. center.py:1245) that
  exceed the real file lengths; every claim was re-verified locally against the actual files. Claims hold.

## THE CRUX — RESOLVED: RAW is deployable AND honest without de-bias or shadow
RAW center PIT cannot be calibrated without a center correction (the per-city LOCATION bias; survives
coord+land+lapse). BUT q_lcb is made honest by an **empirical out-of-fold reliability guard**, NOT by
correcting the center:
```
q_lcb_served[bin] = min( build_joint_q_band(...).q_lcb[bin],
                         empirical_lcb_95(city|domain, metric, lead, bin|bucket, OOF) )
refuse trading bin if the calibration bucket is thin (< predeclared min OOF n) OR empirical_lcb < threshold
```
This does NOT move μ (not a de-bias → law-compliant) and is the live serving rule (not a parallel product →
no shadow). It only serves a lower bound the realized frequency supports. If it rejects too many bins, the
correct DIRECT decision is "do not trade those bins" — never "quietly use EB". **Ship: RAW diagonal 1/E[r²]
center + this q_lcb guard. No EB. No shadow.**

## Line-level facts verified locally
- `center.build_center` is a DE-BIAS-CAPABLE seam: it applies `DebiasAuthority.apply` ONCE and the served
  `mu_consensus = weighted_huber_location(debiased_values, weights)` (center.py:330-376). `raw_consensus` is
  telemetry only. ⇒ RAW requires a NO-OP authority, not just precision weights.
- The LIVE spine injects `_NoOpDebiasAuthority` UNCONDITIONALLY (qkernel_spine_bridge.py:175) ⇒ spine path
  (build_center + PredictiveDistributionBuilder + joint_q_band) is RAW, zero-shift.
- `PredictiveDistributionBuilder` holds one `DebiasAuthority` and calls `build_center(..., self._debias_authority)`
  then re-applies it (predictive_distribution_builder.py:303,310) ⇒ RAW only with the no-op authority.
- EB lives ONLY in the forecast_posteriors path: `capture._eb_corrected` → `fuse_bayes_precision_posterior`,
  consumed by `event_reactor_adapter`/`monitor_refresh`/`position_belief` (the two-center incoherence).
- `walk_forward_model_weights` reads `member.walk_forward_se_native` (never set) → equal 1/n (dormant seam).
- `shrink_cov`/`diag_cov` use `np.cov`/`var` (DEMEANED) → discard bias² → NOT the deployable RAW basis.
- q_lcb Path A = `joint_q_band.build_joint_q_band` (4000 draws, OOF realized-floor σ, per-city preimage
  Normal-CDF bins) = honest. Path B = `_build_fused_q_bounds` (200 center-only, raw per-bin 5th-pct + clip)
  = modal-collapse; reachable from `replacement_forecast_materializer` — must be deleted/wrapped.

## VERIFIED LIVE STATE (the fact the consult said would change the sign-off — resolved locally)
- `config/settings.json:262 "qkernel_spine_enabled": true` — the Wave-5B cutover flag is **ON** (its note
  says "DEFAULT FALSE" but the live value is true). So the spine IS the live entry decision path.
- **ENTRY (reactor, flag ON) = the spine** = RAW `build_center` with `_NoOpDebiasAuthority` over
  `raw_model_forecasts` → RAW, zero-shift — but currently **EQUAL-WEIGHT** (the dormant precision seam).
  `family_decision_engine.decide(): predictive_distribution → joint_q → joint_q_band → ... → argmax`.
- **EXIT/MONITOR = EB.** `forecast_posteriors` is written by `replacement_forecast_materializer:336` via
  `fuse_bayes_precision_posterior` (EB-corrected z). `position_belief.BELIEF_SOURCE_TABLE="forecast_posteriors"`
  (the "strategy of record" for the belief), and `monitor_refresh` reads the same. So the live exit/monitor
  belief is EB-de-biased.
- ⇒ The two-center split (#135) is **LIVE-ACTIVE**: RAW-equal-weight entry vs EB exit. This REFINES the
  flow: the `build_center` debias concern is already mitigated on ENTRY (no-op authority); the ACTIVE
  blocker is step 4 — repoint `position_belief`/`monitor_refresh` off EB `forecast_posteriors` onto the
  spine RAW PD (or make the materializer write RAW posteriors by removing `_eb_corrected`). Step 2 (wire
  1/E[r²]) lands on the spine's `walk_forward_model_weights`, upgrading entry from equal-weight to precision.

## THE q_lcb RELIABILITY GUARD — exact form (canonical consult)
For every OOF prediction, build `JointQBand` with the production RAW diagonal center + train-RMSE normal
width, `n_draws=4000`, `alpha=0.05`. Reliability cells by `(metric, lead, bin_position, q_lcb bucket)`
(+ optional broad domain) — NOT per-city (per-city offsets would be a fitted de-bias). For a live candidate
in cell g: `L_g` = one-sided **Wilson / beta-binomial 95% lower bound** of the cell's realized hit rate;
`q_safe = min(band.q_lcb[bin], L_g)`. Trade only if ALL hold: `N_g ≥ N_min`; `L_g ≥ q_lcb_bucket_floor − ε`;
`q_safe − q_market − cost > edge_floor`; and no guard cell fails its block-bootstrap calibration test.
Otherwise `q_safe = 0` (abstain — publish point prob, do NOT trade). It does not move μ (not de-bias) and is
the live serving rule (not shadow). If it abstains globally, RAW cannot support q_lcb/EV trading without a
center correction — the correct direct decision is "do not trade", never "quietly use EB".

## NO-SHADOW EXECUTION FLOW (each step ships DIRECT, gated by an offline settlement check)
0. **Freeze the method contract + baseline.** Contract: center = RAW weighted-Huber over RAW members,
   `w_m ∝ 1/max(Ê[(x_m−Y)²], floor²)`; width = train-RMSE normal; q = `build_joint_q`; q_lcb =
   `build_joint_q_band` + empirical reliability guard; model set = committed fine (coarse/jma dropped,
   icon_seamless de-duped). One version flag controls the WHOLE belief path. Gate: reproduce live equal-weight
   bin-NLL 1.719 / cover90 0.910 from production code before any change.
1. **RAW second-moment history provider.** Keyed by city/metric/lead/model/target_date; residual = x_m−Y over
   settlements with target_date < decision date; SQUARE before averaging; date-aligned; carry n_train. Gate:
   shuffled-date trap + strict-prior test. Edge: no/low history → equal weights or frozen pooled prior (never
   EB, never demeaned var).
2. **Wire `walk_forward_model_weights` to raw second-moment.** precision = `1/max(raw_mse, floor²)` (basis =
   `Ê[(x−Y)²]`, NOT se, NOT var); do NOT reuse `shrink_cov`/`diag_cov`. Keep nonneg + sum-to-1 + shrink-to-
   equal at low n. Gate: golden weights == research script per city-date-model. Assert no np.var/np.std/demean
   in the production weight path.
3. **Make `build_center` RAW for the strategy.** Inject `_NoOpDebiasAuthority` across the whole RAW path (the
   spine already does; ensure the PD builder + any consumed builder do too) OR add a RAW branch that builds
   `mu_consensus` from raw_values + raw envelope. Receipt `debias_applied=false`. Gate: a case with nonzero
   historical EB shift still returns zero shift + raw-member-envelope center.
4. **Unify consumed posteriors on the RAW belief (BLOCKER).** Remove `_eb_corrected` from every consumed path
   (replace with a `_raw_instrument` that sets z=x, keeps raw residual history only for width/provenance);
   re-point `event_reactor_adapter`/`monitor_refresh`/`position_belief` to the RAW PredictiveDistribution +
   JointQBand. Gate: one `identity_hash` visible from entry → reactor → monitor → exit; reject any consumed
   record with `debias_applied=true`.
5. **Delete/replace q_lcb Path B.** Remove `_build_fused_q_bounds` reachability (or make it a thin wrapper over
   `build_joint_q_band`). Gate: grep proves no live caller computes q_lcb from raw per-bin percentiles; a
   modal-spike regression gives a non-collapsed q_lcb through Path A.
6. **Single q from one predictive distribution.** `PredictiveDistribution.mu_native` = RAW center,
   `sigma_native` = train-RMSE normal width; `build_joint_q` integrates per-city settlement-preimage bins
   (wmo_half_up / HK oracle_truncate) and normalizes once. Gate: 1°F/1°C/open-tail + HK rounding reproduce the
   offline settlement parser; `JointQ.predictive_distribution_id == pd.identity_hash`.
7. **Add the q_lcb empirical reliability guard.** Serve `min(PathA q_lcb, empirical_lcb_95(bucket))`; refuse
   thin/below-threshold buckets. Gate: historical application of the EXACT live trade-selection rule has no
   bucket with realized hit-rate < served q_lcb (block-bootstrap tolerance) AND nonnegative after-cost EV.
8. **Provenance fail-closed.** Persist center_method, `debias_applied=false`, weight basis, model_set_hash,
   training_cutoff, n_train_by_model, width_method, q source, q_lcb basis + guard bucket; DB rejects a
   live-eligible distribution if debias_shift≠0 / wrong center_method / q_lcb not Path-A-plus-guard.
9. **Ship direct + activation gate.** Production-code settlement replay: bin-NLL Δ vs live equal ≤ −0.035
   pooled, 95% CI upper bound < 0, cover90 ∈ [0.88,0.92], no stratum catastrophe, no q_lcb undercoverage
   after the guard. Deploy = operator daemon restart. **Rollback = one atomic version switch restoring the
   WHOLE belief path** (center + PD + consumed posterior + q + q_lcb + consumers) together — never split.

## Future re-decisions (NOT shadows — settlement-gated when data grows)
- full-Ω family-block GLS: re-test when effective-n > n*≈19–39 (measured g_Ω=0.10, K_eff=2.0); switch only on
  a bin-NLL CI-excl-0 win + q_lcb calibration intact.
- coarse re-add: under the deployed DIAGONAL center +coarse was ns (−0.026); revisit only if a future
  diagonal+coarse settlement gate is significant. jma stays dropped (ns/worse).

## Owed
- Local grep of `event_reactor_adapter`/`monitor_refresh`/`position_belief` bodies (not in the gist) to
  enumerate every forecast_posteriors reader for step 4.
- The empirical_lcb_95 calibration table (OOF reliability by city/domain/lead/bin bucket) for step 7.
