# Draft-2 Response — accept the spine, one correction, implementation-grade additions

- Created: 2026-05-29
- Author: session (Opus, max effort), worktree `stat-whole-refactor`
- Inputs: operator Draft 2 + this session's verified findings. Feasibility grounded in `tribunal_verification_2026-05-29/D_impl_feasibility.md`.
- Purpose: turn Draft 2 (correct diagnosis) into an executable spec, and design the operator's new mandate — **before/after data comparison as final validation**.

---

## 0. Verdict on Draft 2

**Spine accepted.** Draft 2 reached the correct diagnosis: the root failure is the missing Forecast Random Variable Contract; D5 keying is the strongest confirmed defect (above D1); raw OpenData is the safe *default*, not a profit proof; next step is the contract + product/cycle/lead-keyed evidence + scorer, not another sd3 MC. No disagreement on direction.

What Draft 2 is NOT yet: an executable spec. It names the objects but leaves six implementation decisions and the operator's new validation mandate unspecified. Those are below — and three of them are feasibility-verified, so they're assertions, not hopes.

---

## 1. One correction to feed back into Draft 2

**D1-LOW is RESOLVED — it is NOT blocking.** Draft 2 §5.2 still says "D1 is blocking-first for LOW" and §13 Phase 1 says "if LOW value impact is material → blocking-first." The disaggregation landed after Draft 2 was written:

- Of 9,314 LOW rows: **5,607 (60%) DROP** (`boundary_ambiguous`→`training_allowed=0`, fail-closed, safe); **752 (8%) CONTAMINATE** (`training_allowed=1`, 1-4°C warm min); rest unaffected.
- **LOW is NOT traded live** — `settings.json:65 apply_to_metrics:["high"]`; `manager.py:958` returns RAW_UNCALIBRATED for LOW.

→ D1 blocks **nothing now** (HIGH immaterial, LOW not live). Source-fix is a **parallel track** that must precede (a) any LOW market launch and (b) any LOW evidence rebuild in the new ledger (else the 752 contaminated + UTC+9 dropout rows poison LOW training). Update Draft 2 §5.2/§13 accordingly.

---

## 2. The operator's new mandate: before/after data comparison as final validation

This is the antibody for the whole reshape. A foundation change that "looks cleaner" but silently moves probabilities is the worst outcome. The harness must answer two *different* questions with two modes:

### 2a. EQUIVALENCE mode — refactors that must NOT change behavior
For any change that is supposed to be behavior-preserving (analytic p_raw replacing 10k MC; wrapping serving in the ForecastObject contract; the dataset_id/_v2 rename), the before/after delta on identical inputs MUST be ≈0.
- Metric: per-bin |p_after − p_before| and proper-score delta on a frozen replay window.
- Pass bar: analytic-vs-MC within MC sampling noise (the existing two scorers already cross-reconcile to |Δ|≤2e-4 — that is the precedent bar); contract-wrap byte-identical.
- A non-zero delta here = a refactor bug, not an improvement. Fail-closed.

### 2b. IMPROVEMENT mode — keying changes that must EARN it
For any change that is supposed to help (city×season bias → product×cycle×lead hierarchical bias; new candidate selection), the new model is adopted per-bucket ONLY if it beats the old on settlement-truth OOS:
- Metric: `LCB(proper_score_old − proper_score_new) > 0` via bootstrap CI on the score difference, blocked-by-date OOS, scored vs **settlement** (not vs another forecast).
- Else: keep the old/raw for that bucket. Correction is never an entitlement.

### 2c. The frozen "before" fixture (capture NOW, immutable)
Before any code changes, snapshot the current production pipeline's output (raw + city×season bias + Platt + 10k MC → p_raw/p_cal) for a fixed replay window (recommend last 60-90 days OpenData live HIGH + a TIGGE historical slice) joined to settlement truth. Freeze it as an immutable fixture under `docs/operations/before_after_fixture_2026-05-29/`. Every later phase compares against this same frozen "before" → reproducible across sessions (the cross-session antibody).

### 2d. Build = EXTEND, not rebuild
`scripts/audit_refit_proper_scores.py:1-858` already computes Brier/LogLoss/RPS/PIT/ECE across `error_model_family` slices with `lead_bucket` and `cycle` cohort splits, and the two scorers cross-reconcile to |Δ|≤2e-4. Add: (1) a `family` tag per pipeline version, (2) equivalence assertion (|Δ|≈0), (3) bootstrap-LCB on the score difference (~20 LOC) for improvement mode. No new scoring engine.

**Every phase cutover below is gated by this harness's verdict.** That is the operator's "保留前后改动的数据对比作为最终验证," made executable.

---

## 3. Implementation-grade omissions Draft 2 leaves (the superior specifics)

### 3a. Sample-depth × fine-keying tension → EXTEND the EB estimator, do NOT build flat buckets  [load-bearing]
Re-keying by product×cycle×lead_bucket multiplies buckets ~28× (2 products × 2 cycles × 7 lead buckets). OpenData LOW has only **7,858 pairs total** — flat fine-keying would starve almost every bucket → nothing fits → everything falls to raw, and the lead-varying signal Draft 2 correctly identifies (Busan −2.97 @0-12h → +0.16 @24-48h) is lost again.

Resolution (verified feasible): `ens_bias_model.py:1-325` is **already an empirical-Bayes shrinkage estimator** (TIGGE prior → live posterior, `w = V0/(V0+V_O)`, group offset `delta_g`) at city×season×month. Add lead_bucket/cycle/product as **additional hierarchy levels with partial pooling** (~50 LOC, calling-convention not new math). This makes the *correct* keying *data-feasible* — flat buckets would re-create the starvation that produced sd3's noise. σ (`total_residual_sd_c`) gets the same hierarchical re-key (don't add a new σ layer — Draft 2 §6.3 is right).

**CRITICAL — the leaf fallback is RAW, not the TIGGE prior.** The existing estimator shrinks thin buckets toward the TIGGE prior. That prior was built for the abandoned FT regime, and we proved TIGGE→OpenData transfer *hurts* 7/11 buckets (Jeddah 2.05→9.06). Most OpenData buckets are thin — so "auto-collapse to the prior" would auto-apply a known-harmful correction to exactly the buckets that can't defend themselves = **sd3 under a new name.** Keep the two layers strictly distinct:
- The hierarchical estimator EMITS the TIGGE-shrunk value as **one candidate** among {raw, scale-only, opd-bias, opd-bias+scale, tigge-prior-bias, tigge→opd-transfer}.
- The accept-gate's **default is raw identity**; it adopts the shrunk candidate ONLY on a same-product/same-lead settlement-OOS `LCB>0` win.
- When a bucket is thin and nothing clears the gate → **serve raw**, not the prior.

Adding hierarchy levels does NOT fix this by itself — it inherits the wrong shrinkage target. The estimator is a *candidate generator*; the accept-gate is the *serving authority*. This separation is what stops the redesign quietly re-arming the bug it exists to kill.

### 3b. Name the enforcement chokepoint (else it's doc, not antibody)
- Writer seam: `scripts/ingest_grib_to_snapshots.py` (writes `ensemble_snapshots`).
- Reader seam: `src/data/executable_forecast_reader.py` (serves to the decision path).
- Mechanism: a single `ForecastObject.from_snapshot_row(row)` constructor that **RAISES** if product/cycle/lead_bucket/window/contributes can't be resolved; called at the writer before INSERT and at the reader before serving. `SettlementObject.from_settlement_row(...)`. A residual is constructible **only** via `Residual(forecast, settlement)` which asserts `forecast.target == settlement.target`. That assertion is the antibody — it makes a TIGGE-prior-tagged-OpenData row or a lead-mismatched residual *unconstructable*, not merely discouraged.

### 3c. Pin the two lead definitions to their roles (Draft 2 §4.1 lists both, assigns neither)
- **Evidence + bias/σ keyed by `forecast_lead_bucket`** (issue→target-day): reproducible historically, settlement-paired, backfillable.
- **Serving at decision time τ**: the reader selects the freshest run *within the target forecast_lead_bucket*, and records `decision_lead` + `staleness = τ − issue` as serving metadata feeding the edge/uncertainty layer (not the bias key). This separates "what was the forecast error at this horizon" (evidence) from "how stale is my information now" (serving). Cross-run/cross-cycle spread (Draft 2 §4.2) is recorded here as serving uncertainty, not averaged into the point estimate.

### 3d. Analytic p_raw must run in settlement-unit space with the rounding preimage  [Fitz unit-provenance class]
Members are °C (`members_unit`); settlement is integer °F, `wmo_half_up`. The analytic Gaussian-mixture CDF must: convert each member μ_m and σ to °F, then evaluate Φ at the bin **preimage** half-integer edges `[a−0.5, b+0.5)`. The °C→°F + rounding-preimage chain is exactly the silent-unit-error failure mode — the EQUIVALENCE test (analytic vs 10k MC, §2a) is what proves the chain is exact before MC is retired. Do not retire MC until equivalence passes under every `settlement_rounding_policy`.

### 3e. Migration = dual-run, not big-bang
Shadow-compute the NEW pipeline alongside the OLD (which already runs in shadow). Log both per decision. The before/after harness scores the divergence continuously. Cut a surface over only when its mode-appropriate gate passes (equivalence for refactors, improvement for keying). This applies Zeus's own 14-day-shadow discipline to the refactor itself — the reshape never goes live unproven.

### 3f. Backfill is free (no re-ingest) — descope the Tribunal's "re-extract everything" for the ledger
`data_version` encodes product (mx2t3 live / mx2t6 TIGGE); `source_cycle_time` encodes cycle (existing `_cycle_from_source_cycle_time`, calibration_serving_status.py:76-80); `lead_hours` gives lead_bucket by arithmetic. The re-keyed evidence ledger is **backfillable by SQL alone** on existing `ensemble_snapshots_v2` columns. (Caveat: `source_cycle_time` nullable on pre-PLAN legacy rows → `issue_time` fallback.) Re-ingest is needed ONLY for D1-LOW source-value repair, not for the ledger re-key. This removes the single largest cost the Tribunal assumed.

---

## 4. Revised phasing (harness woven in; each cutover gated)

| Phase | Work | Gate to advance |
|---|---|---|
| 0 | Freeze: sd3 off, raw OpenData shadow default. **Capture the immutable before-fixture (§2c).** | fixture frozen + checksummed |
| 1 | D1-LOW source fix (product-derived STEP_HOURS + re-extract LOW). PARALLEL — pre-LOW-launch, not blocking. | mx2t3/mx2t6 same-issue agree ~1°C; LOW boundary remeasured |
| 2 | ForecastObject/SettlementObject contract @ named chokepoint (§3b). | every served row emits a valid ForecastObject; reader refuses invalid |
| 3 | Evidence ledger v2 — SQL backfill (§3f), full provenance + source_kind{tigge_prior\|opendata_live\|paired_delta} + product/cycle/lead_bucket. | no OpenData row tagged 'prior'; every residual references Forecast+Settlement objects |
| 4 | Extend EB estimator with lead/cycle/product hierarchy (§3a); re-key σ; wire the T4 scorer (currently a stub). | scorer keyed product×cycle×lead; raw always in candidate set |
| 5 | Analytic p_raw (§3d) + **EQUIVALENCE-mode before/after** (analytic vs MC \|Δ\|≈0). | equivalence passes under every rounding policy |
| 6 | **IMPROVEMENT-mode before/after** (new keyed bias vs old, LCB>0 on settlement OOS) per bucket. | adopt non-raw only where LCB>0 + mainstream sanity (§Draft2-10) |
| 7 | Lead-aware raw live candidate behind the contract; non-raw dormant until its Phase-6 win. | shadow→14-day→unshadow (M5 carry-over gates) |

Gate-hash re-bump (retire sd3, mint new gate id) happens deliberately at Phase 4. Build base = `pr3-schema-stable (e0092e89bd)`.

---

## 5. The meta-point (why this reaches "正确获取市场收益")

The before/after harness is the **immune system** for the reshape — but be honest about which half is decisive when.

- **Equivalence mode is decisive now.** It makes "a refactor silently moved the probabilities" impossible to ship. This is the solid half and it carries Phases 2-5.
- **Improvement mode is underpowered on the live product for months.** Scored on OpenData settlement, it inherits the exact n=12-18 / 7,858-LOW-pair / 1-of-11-clears-LCB depth problem already measured. The only statistically powered history is TIGGE — whose signal we proved does not transfer. So improvement mode cannot certify "this makes money live" pre-launch; it can only certify "not worse on available data" and strengthen continuously as live depth accrues.

Therefore "before/after as 最终验证" means precisely: **proven behavior-preserving (equivalence) + proven structurally correct (contract/lineage/keying) + proven not-worse on available settlement OOS** — NOT a profit guarantee. Live-alpha is a continuously-strengthening gate, not a one-shot pre-launch sign-off. That is the honest, executable form of the operator's own "backtest ≠ live alpha": the reshape removes the structural defects that make probabilities *wrong*, and the harness guarantees no step regresses the objective — which is the necessary precondition for capturing returns, not the proof of capture itself.

## 6. Open for operator
- Confirm the before-fixture replay window (suggest 60-90d OpenData HIGH + TIGGE slice) + whether to include the SF-MAM station-gap bucket (the one LCB>0 case) as a known-correction control.
- Confirm `MetricIdentity.data_version` Python attr stays as the lineage value (not renamed) under the contract.
