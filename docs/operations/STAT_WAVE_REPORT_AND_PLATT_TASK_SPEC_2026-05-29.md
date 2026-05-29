<!--
Created: 2026-05-29
Last reused or audited: 2026-05-29
Authority basis: stat-whole-refactor wave (8 commits, branch stat-whole-refactor);
  wave-critic verdict 2026-05-29; BIN_BIAS_VS_MULTISOURCE_2026-05-27.md;
  project_forecast_cold_bias_timezone_2026_05_22 (MEMORY).
-->

# Stat-Layer Wave — Results Report + Platt Task Spec

**Branch:** `stat-whole-refactor`  **Date:** 2026-05-29  **Status:** wave complete, critic-cleared, pre-PR.

This document has two parts:
- **Part 1** — results report of everything the wave added (for sign-off before PR + shadow).
- **Part 2** — the Platt task spec (the next structural gap the wave does *not* close), for your review before it is scheduled.

---

# PART 1 — Wave Results Report

## 1.1 What the wave is

A foundation-level redesign of Zeus's statistical layer, so that live trading runs on **statistically correct, OOS-validated** probabilities. The governing thesis (the "Tribunal" result): **raw ensemble dominates** — bias corrections must *prove* they beat raw out-of-sample before they reach live selection, or they are not applied.

8 commits, all in `stat-whole-refactor`, all test-backed:

| # | Commit | What |
|---|---|---|
| 1 | `37b1a75ff5` | D-S1: first-class `settlement_station` + `settlement_unit` columns (de-tautologize the pairing gate) |
| 2 | `addf6faaaf` | D-S1 critic SEV-3 follow-ups (unit-vocab doc, column-absent test) |
| 3 | `97d4963428` | P3 OOS scorer: offline date-blocked scoring half feeding `choose_candidate` (raw-dominant, no-leakage) |
| 4 | `9330127cc9` | OOS validation harness (equivalence + improvement before/after modes) |
| 5 | `50d339a5f3` | D1-LOW: product-derive aggregation window (3h for OpenData mn2t3/mx2t3) |
| 6 | `507314de49` | #26 dataset_id rename migration + fix ACTIVE read sites (Gap #2) |
| 7 | `2ba3ae2c49` | D1-LOW: drop >144h OpenData fetch tail (Polymarket 5-day cap), STEP_HOURS derived from the cap |
| 8 | `551e839ab3` | **Critic SEV-1 fix**: honest horizon requirement (kill Western-D+5 silent fail-open) + SEV-3 leakage-antibody hardening |

## 1.2 The five structural decisions (K≪N)

Per Fitz methodology — these 8 commits are symptoms of **5 structural decisions**, not 8 patches:

1. **Provenance-first pairing** (D-S1): forecast↔settlement pairing must verify station + unit from *stored truth*, not parse them from the forecast's own claim (which made degC/degF mismatches uncatchable).
2. **Promotion-by-proof** (P3 scorer + harness): a bias correction reaches live only if it beats raw on *date-blocked* OOS folds. No date-blocked win → raw.
3. **Exact probability** (P4, prior wave): analytic Gaussian-mixture CDF replaces 10k Monte-Carlo — exact, deterministic, noise-free.
4. **Product-derived semantics** (D1-LOW window): the aggregation window is keyed on the product token (3h vs 6h), making the wrong-window class unconstructable.
5. **Honest coverage** (drop-144 + SEV-1 fix): the fetch horizon, the coverage requirement, and the gate are three separate concerns; the requirement is never silently shrunk to fit the fetch, so an uncoverable window BLOCKS instead of reading complete.

## 1.3 Mathematical before/after

### P4 — analytic CDF (new) vs Monte-Carlo (before)
Swept MC sample count against the analytic answer (synthetic seeded fixture, 30 rows):

| MC samples n | max\|Δp_raw\| | max\|Δlogit\| |
|---|---|---|
| 2,000 | 2.76e-3 | 2.49e-2 |
| 10,000 *(old default)* | 1.04e-3 | 1.65e-2 |
| 50,000 | 5.07e-4 | 4.89e-3 |
| 200,000 | 1.73e-4 | 4.41e-3 |

Both errors fall ~∝1/√n toward zero → **the analytic CDF is the exact n→∞ limit of the old sampler.** The new method removes the MC sampling noise the old 10k carried, and is deterministic + faster.

### P3 — date-blocked OOS scoring (new) vs shrinkage-target (before)
Synthetic fixture (40 rows, k=5):

| candidate | logloss | rps | brier | verdict |
|---|---|---|---|---|
| **raw** | **1.6543** | **0.6141** | **0.8407** | chosen |
| opendata_bias | 1.7604 | 0.6772 | 0.8650 | rejected (worse OOS) |
| tigge_prior | 1.7604 | 0.6772 | 0.8650 | rejected (worse OOS) |

Both bias candidates score **worse** than raw OOS → gate refuses to promote → raw chosen. The old shrinkage-target had no date-blocked proof and could promote an overfit bias.

### D1-LOW — data change (4-city real-GRIB sample, not a full re-extract)
- **HIGH (traded):** zero value change (daily max is deep-inner, unaffected by edge misclassification).
- **LOW (not traded):** selected daily-min un-warm-biased up to **−2.63°C**; ~quarantined boundary-ambiguous rows recovered (Qingdao L1/L2 `None → 14.33 / 16.29°C`).
- **Correctness:** confirmed against ground truth — ECMWF Open Data step144 `mn2t3` GRIB field declares `lengthOfTimeRange=3`, paramId 228027 ("Minimum temperature at 2m in the last 3 hours"). The fix matches the data's own provenance.

## 1.4 Bias on the temperature-SELECTION data — vs the prior report

Anchored to `BIN_BIAS_VS_MULTISOURCE_2026-05-27.md` (the prior measurement of bias as it lands on bin/edge selection). That report's bias was dominated by the live `full_transport_v1` `bias_c` correction, which it proved is **wrong-direction** for East-Asia (root cause: timezone daily-max under-extraction → corrupted training pairs → spurious positive `bias_c` → cooling a cold ensemble).

| City | BEFORE (FT promoted, cal-offset vs OM) | AFTER (raw, OOS-gated; projected from report's own uncorrected figures) |
|---|---|---|
| Shanghai | −3.8°C | ens 25.9 vs OM 26.8 ≈ **−0.9°C** |
| Qingdao | −2.0°C | uncorrected ≈ OM 28 ≈ **0°C** |
| NYC | +3.0°C | pre-warming ≈ **−0.5°C** |

**The headline:** the wave does not "tune" the −3.8°C smaller — its OOS gate makes the wrong-direction correction **unpromotable** (it cannot beat raw OOS), so it never reaches selection. The extraction fix (D1-LOW window + the timezone-extrema fix verified present, task #20) removes the defect that manufactured the spurious `bias_c` in the first place.

**Caveat — measured vs projected:** the AFTER column is *projected* from the report's own raw/uncorrected numbers + the demonstrated OOS verdict. The **measured** per-city AFTER selection bias is produced by the shadow run (task #24, "shadow p_raw vs online forecast, bin bias ≤1"), which runs after merge + migrate + restart. Not yet run.

## 1.5 Critic verdict + remediation

Wave-critic (opus, adversarial mode) verdict: **REVISE → one SEV-1 blocked PR.** Both findings fixed in `551e839ab3`:

- **SEV-1 (fixed):** `required_period_end_steps` silently truncated the coverage *requirement* at the 144h cap. Western-hemisphere D+5 *local* days extend to ~151-153h UTC (negative UTC offset), so the truncated requirement read ≤144 and the coverage gate reported `COMPLETE/LIVE_ELIGIBLE` on an uncovered window — silent fail-open, 16 America/* cities at D+5. **Fix:** the requirement is now always honest; the cap lives only on the fetch list + the gate; an over-horizon window BLOCKS (`SOURCE_RUN_HORIZON_OUT_OF_RANGE`). New antibody: Western D+5 must BLOCK, Eastern D+5 LIVE_ELIGIBLE.
- **SEV-3 (fixed):** the temporal-leakage antibody only detected dataset-sensitivity (stayed green under the `train=all` leak mutation). **Fix:** anti-correlated per-fold bias makes a leak distinguishable (folds cancel → cand≈raw) from correct OOS (other-fold opposite bias mis-corrects → cand strictly worse). Sed-break verified RED-under-leak.

Critic PASS items: dataset_id migration (INV-37-safe, idempotent, drift-guard genuine), D-S1 NULL-fallback correct + vocab-consistent, OOS scorer leak-free, all named antibodies sed-break-verified genuine.

## 1.6 Test status
- **320** affected tests pass post-fix.
- **1 pre-existing failure** (NOT from this wave; verified failing at base HEAD): `tests/test_opendata_writes_v2_table.py::test_collect_open_ens_cycle_writes_authority_chain_readable_by_live_reader` = `NON_CONTRIBUTING_EXTREMA` (unrelated executable-reader surface).

## 1.7 OPEN DECISION — OpenData cap value (144 vs ~156)
The SEV-1 fix makes Western D+5 **fail-closed (BLOCKED)** at the current 144h cap. This is *safe* (no wrong-pricing) but means 16 America/* cities cannot trade their furthest (D+5) market.

- 144h covers D+5 for Eastern/Central cities (Tokyo D+5 ends ~135h UTC).
- Western D+5 needs ~151-153h UTC. ECMWF disseminates 3h steps only to 144h, then **6h** steps (150, 156, …). Covering Western D+5 means adding back **two** 6h steps (150, 156) — i.e. cap ≈ 156h, not a pure 3h grid.
- The daily extremum is usually still captured at 144h (HIGH afternoon peak ≈ local 14-16h = ~141-143h; LOW pre-dawn min ≈ local 04-06h = ~131-133h), so the *practical* loss is small — but the gate is honestly conservative and blocks the whole window.

**Decision needed before shadow:** keep 144 (Western D+5 blocked, simplest) **or** raise to ~156 (add 150+156, Western D+5 tradeable). Recommendation: keep **144 for the initial shadow/live** (fail-closed, fewer moving parts), revisit 156 as a post-launch alpha-recovery item once shadow confirms the rest.

---

# PART 2 — Platt Task Spec (REVISED 2026-05-29 — identity-default + OOS promotion gate)

## 2.0 Conclusion (结论先给)

**Do NOT "re-run Platt and promote" now.** Correct action: **bring Platt into the same OOS promotion gate as `bias_c`, and set identity-Platt as the live fail-closed default.** A Platt candidate may enter selection ONLY when, within the same ForecastObject domain, on date-blocked OOS, over the full `p_raw → p_cal → bin selection` chain, it beats identity/raw.

```text
We do NOT currently confirm "redoing Platt will make the data better".
We DO confirm: with identity default + OOS gate, a redo cannot hurt live;
only when a candidate Platt passes the OOS proper-score gate is it confirmed to improve the probability layer.
```

Platt does NOT improve the *weather data*. It only improves *probability calibration*. It cannot fix the OpenData window, lead/product mixing, bias_c temperature-domain mis-shift, σ under-dispersion, or station mismatch. PR #361 remains a raw-dominant foundation PR (FT/bias correction dormant; near-term live runs on raw). This is the same principle as the whole wave: **correction is a candidate, raw/identity is the default, promotion requires OOS proof.**

## 2.1 Immediate live policy — identity authority

```text
Post-PR #361 shadow/live:
  selection authority = p_raw / identity-Platt
  current Platt = shadow-observed only
  no current/refit Platt may affect live bin selection until full-chain OOS gate passes
```

`identity-Platt` ≙ `A=1, B=0, C=0 → p_cal = p_raw`. If the engineering path must emit `p_cal`, set `p_cal := p_raw` and record `platt_decision = identity_fallback`, `platt_reason = no_oos_full_chain_win`.

## 2.2 Why NOT a naive refit now

Current Platt is `sigmoid(A·logit(p_raw) + B·lead_days + C)` — **lead_days is an input FEATURE, not a bucket dimension** (confirmed in calibration-keying audit). That alone is not wrong, but the current Platt also carries these risks:

```text
1. TIGGE and OpenData may share one Platt training scope.
2. data_version/product lead-skill curves differ.
3. OpenData-transfer assumption is empirically refuted (TIGGE→OpenData invalid;
   ~ -0.9°C to -1.4°C systematic OpenData-vs-TIGGE gap on overlap).
4. p_raw itself was just rewritten (analytic CDF + source semantics + bias_c gate).
5. old slope A may already be compensating old p_raw's wrong σ / wrong bias / wrong transfer.
```

A refit on the *wrong/mixed domain* only re-bakes the old errors.

## 2.3 Can we confirm a refit will be better?

Strict answer: **not unconditionally.** Three things ARE confirmable:

```text
A. naive refit is NOT guaranteed better — may be worse.
B. gated refit cannot make live worse (failure → identity).
C. only a candidate passing full-chain OOS proper-score is confirmed "better".
```

Define "better" as a machine verdict, not a vibe — for each Platt bucket / ForecastObject domain, on date-blocked OOS folds grouped by target_date / decision_group_id, score identity / current / refit / clamped / lead-domain Platt and **accept only if**:

```text
candidate beats identity on ≥2/3 proper scores {logloss, RPS, Brier}
AND bootstrap LCB(improvement) > 0
AND no catastrophic cohort regression
AND selection peak shift ≤ 1 bin unless OOS score proves otherwise
AND ECE / reliability does not materially degrade.
```

## 2.4 Seven deficiencies the prior plan had (now fixed)

1. **Default not explicit** → identity is not merely a candidate; it is the **default production authority**; all non-identity Platt are unpromoted until proven.
2. **Current Platt's live authority not cut first** → before anything, `platt_mode = identity` (or `if no promoted_platt_oos_decision: return p_raw`). PR #361's raw-dominant posture must extend through Platt: un-gated Platt ≡ identity.
3. **ForecastObject domain not bound into the Platt key/hash** → a `platt_models_v3` / `platt_decision` row must carry `p_raw_domain_hash`, `forecast_object_schema_version`, `extraction_semantics_hash`, `error_model_gate_hash`, `bin_schema_hash`, `training_pair_batch_id`, `oos_decision_hash`. Without these, no one can tell which p_raw domain (MC vs analytic, dirty vs clean extraction, raw vs corrected bias) a Platt row was fit on. This is the same root cause (no typed object binding city×metric×date×product×cycle×lead×window to one RV) one layer down.
4. **`A ≤ 2.0` too magic** → `A_clamped_2p0` is a *candidate*; `A_hard_max` is a *safety fuse*; the OOS gate decides promotion. Live hard cap may be 2.0 as a **capital-preservation fuse, not a statistical optimum**; reject any A>2.5 unless explicit override + OOS proof + manual signoff.
5. **σ / dispersion not addressed first** → Test B showed `none`-distribution LogLoss 11–25 (severe under-dispersion); a too-sharp p_raw makes Platt learn A<1 or odd B/C, a too-flat p_raw makes Platt learn A>2.5 — both are Platt wiping σ's mess. Add **P-0 p_raw reliability/PIT/dispersion audit** before any refit.
6. **Scoring must be full-vector** → score the whole normalized `p_cal` bin vector against the settled bin (multinomial logloss / RPS / Brier), NOT per-bin one-vs-rest AUC/ECE (which can look better per-bin while the distribution's RPS/peak gets worse).
7. **Fold unit = target_date / decision_group, not row** → all rows sharing `target_date, city, metric, decision_group_id, market/bin family, forecast_object_id` must be in the SAME fold (adjacent bins of one weather event are label-correlated; calibration leakage is especially dangerous). Reuse the wave's temporal-leakage sed-break antibody at equal strength.

## 2.5 Revised flow — P0 → P7 (domino order)

### P0 — Downgrade Platt live authority (stop the bleeding FIRST)
```text
platt_mode = identity | shadow | gated   (default: identity)
  identity : p_cal = p_raw
  shadow   : compute current/refit Platt, do NOT use for selection
  gated    : use promoted candidate from the OOS decision table
```
Acceptance: with no promoted OOS decision, `p_cal == p_raw` (float-tolerance). Probability trace writes `p_raw`, `p_cal_identity`, `p_cal_current_shadow`, `platt_decision_reason`.

### P1 — Current-state audit (NO model writes)
Score identity vs current Platt by city/cluster, metric, season, data_version, cycle, lead_bucket, shoulder-vs-interior. Metrics: logloss, RPS, Brier, ECE, peak-bin shift, true-bin prob, entropy delta, top/bottom-bin mass delta. Output `PLATT_CURRENT_AUDIT_YYYY_MM_DD.csv` with verdicts {CURRENT_WINS_OOS, IDENTITY_WINS, OVER_SHARPENING, UNDER_SHARPENING, INSUFFICIENT_N, SHOULDER_GRANULARITY_NOT_PLATT}. Directly answers "is current Platt already worse than identity"; if so, live = identity.

### P2 — Freeze the p_raw domain
Preconditions: D1 extraction semantics fixed (or scope explicitly excludes affected path); bias_c gate outcome fixed (raw or selected candidate); analytic CDF semantics fixed; settlement unit/station pairing fixed; coverage gate fixed. Emit:
```text
p_raw_domain_hash = hash(forecast_object_version, extraction_semantics,
                         analytic_cdf_version, bin_schema, error_model_decision,
                         settlement_pairing_contract)
```

### P3 — Generate clean analytic calibration pairs
Per settled event: ForecastObject → member_extrema → selected error-model candidate or raw → analytic p_raw vector → settled bin → pair row. Store decision_group_id, forecast_object_id, p_raw_domain_hash, p_raw_vector, settled_bin, lead_days, lead_bucket, cycle, product, data_version, bin_schema_hash. Do NOT fit on old-MC / old-extraction / old-bias domain.

### P4 — Candidate set (upgraded — not just {current, clamped, refit, identity})
```text
candidate_0 = identity
candidate_1 = current_platt_shadow_only
candidate_2 = refit_same_domain_unclamped
candidate_3 = refit_same_domain_A_clamped_2p0
candidate_4 = refit_same_domain_A_clamped_1p7
candidate_5 = shrinkage_platt_to_identity
candidate_6 = lead_nonlinear_or_bucketed   # current Platt is linear B·lead_days;
                                           # all models are horizon_profile=full (no lead strata)
```
candidate_6 compares: linear-lead vs coarse lead_bucket [0-1d,1-3d,3-5d,5-7d] vs spline/shrinkage-lead. Insufficient N → identity. Do not explode into 7 raw lead buckets.

### P5 — OOS full-chain scorer
Per fold: fit candidate on train folds; on test fold compute `p_cal = candidate(p_raw, lead_days)`, normalize the vector, score against the settled bin. Accept iff: beats identity on ≥2/3 {logloss,RPS,Brier} AND bootstrap LCB(identity−candidate) > 0 AND BH/FDR pass (many buckets) AND no catastrophic cohort regression AND ECE non-worse AND peak shift >1 bin only if proper scores clearly improve. Catastrophe = any city/metric/lead_bucket where logloss/RPS worsens > threshold, OR true-bin prob halves, OR selected bin moves >1 bin from settlement on holdout.

### P6 — Promotion decision table (do NOT overwrite platt_models directly)
Tables `platt_candidate_scores` + `platt_oos_decisions`. Promotion row carries bucket_key, p_raw_domain_hash, candidate_name, A/B/C, score_identity, score_candidate, improvement_lcb, fdr_q, catastrophe_flags, decision∈{PROMOTE, IDENTITY, INSUFFICIENT_N, REJECT}, recorded_at, fit_dataset_hash. Live reader: `if no PROMOTE row matching p_raw_domain_hash → identity; else promoted candidate`. Prevents stale-Platt misuse.

### P7 — Shadow acceptance (did it change the RIGHT thing?)
For Platt-cohort cities: trace p_raw / identity p_cal / promoted-or-shadow p_cal; compare selected bin + market edge; verify no unapproved live selection change; bin bias ≤ 1 on the measured shadow cohort. If Atlanta/Seattle (HOLD) still show large peak shift in shadow without OOS proof → stay identity.

## 2.6 Revised authority rule (the spec block)

```text
Platt Task — authority rule
Decision:
  Do not refit/promote Platt directly.
  Install identity-Platt as default live authority.
  Score current/refit/clamped/shrinkage Platt as candidates only.
  Promote non-identity only after a full-chain date-blocked OOS win.
Hard precondition (p_raw domain frozen):
  extraction semantics fixed · analytic p_raw fixed · bias/error-model decision fixed
  · settlement station/unit pairing fixed · p_raw_domain_hash emitted.
Candidate set: identity, current_shadow, refit_same_domain, refit_A_clamped_2p0,
  refit_A_clamped_1p7, shrink_to_identity, lead_nonlinear_or_bucketed.
Accept: beats identity on ≥2/3 proper scores · LCB>0 · FDR controlled ·
  no catastrophic city/lead/shoulder regression · ECE non-worse · peak shift bounded unless proven.
Default: identity.
```

## 2.7 Scope, dependencies, expected outcome
- **IN:** Platt over-sharpening cohort (Atlanta, Seattle, Tel Aviv, Mexico City, Chicago, NYC, Denver, Dallas); full-chain OOS gate; identity default; promotion table; slope fuse.
- **OUT:** bin-granularity shoulder-pile (Jeddah, Karachi, Guangzhou) — separate bin-structure / tail-mass review, NOT a Platt defect.
- **OUT:** ENS bias_c layer (D5 = #29) — its own task.
- **DEPENDENCY:** P2 freeze needs re-extract #10 + bias_c (#29) + analytic-coverage (#31) decisions; never fit Platt on uncorrected p_raw.
- **Expected outcome (prediction, not promise):** high-A cohort (Atlanta/Seattle/Mexico City/Tel Aviv) → gated/clamped likely safer than current; vs identity = OOS-decided. Normal A≈1.3–2.1 → small gain or identity-tie / insufficient-N. Shoulder-pile cities → Platt must NOT be the fix (bin structure). Net: the main payoff is *reducing over-sharpen + wrong peak shift*, not improving the forecast.

## 2.8 Files the implementer will touch
- `src/calibration/platt.py` (identity-mode + slope fuse boundary); Platt store → new `platt_decision`/`platt_oos_decisions` tables (NOT a direct `platt_models` overwrite).
- runtime p_cal reader (apply `platt_mode` / promotion-table lookup; identity fallback).
- `scripts/score_error_model_candidates.py` (or sibling) extended to the p_cal chain + `scripts/oos_validation_harness.py` (p_cal improvement mode); new `scripts/platt_current_audit.py` (P1).
- `tests/` new: `test_platt_identity_default.py`, `test_platt_oos_full_chain_gate.py` (sed-break), `test_platt_fold_unit_decision_group.py`, `test_platt_slope_fuse.py`.
- Authority/registry: gate-hash re-bump (#11) once Platt enters the gate; register new scripts/tests in manifests.

## 2.9 Operator-level next action (sequence)
```text
1. Merge/review PR #361 as foundation only.
2. Post-merge shadow: Platt live authority = identity (P0).
3. Platt current-state audit: identity vs current, no writes (P1).
4. Freeze p_raw_domain after D1 / bias / analytic-CDF decisions (P2).
5. Generate clean analytic p_raw calibration pairs (P3).
6. Run Platt candidate OOS scorer (P4-P5).
7. Promote only the OOS winner; otherwise identity (P6).
8. Shadow with p_raw / identity / candidate traces (P7).
9. Only after measured bin-bias + proper-score improvement, allow gated Platt in selection.
```

One-line verdict: **don't "redo Platt" — make Platt re-compete for the job. It wins → it improved the probability layer; it doesn't → identity takes over.**

---

## Appendix — current task-list position
- Wave (this doc, Part 1): **done, critic-cleared, pre-PR.**
- Next: PR → merge → migrate (#21) → shadow restart (#22) → verify (#24) → live (#25).
- Re-extract (#10 tail): operator-gated, ETA ~2-3hr to isolated DB.
- **Platt task (this doc, Part 2): proposed; schedule AFTER re-extract + bias_c refit.**
