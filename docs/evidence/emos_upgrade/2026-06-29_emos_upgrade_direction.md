# EMOS module upgrade — best-direction exploration (current-situation grounded)

**Date:** 2026-06-29
**Authority basis:** operator directive — "根据最新结构完整进行最新语义的emos模块update，探索更优越的实现性" /
"根据当前情况拿到最佳升级设计方向" / "我不要之前的过时产物被直接激活". Explore, from the CURRENT
architecture, the best upgrade *design direction* for the EMOS module. Do NOT reactivate the
legacy artifact.
**Money-path stage:** forecast signal → calibration (predictive width / center). UPSTREAM of edge/execution.
**Status:** EXPLORATION / DESIGN DIRECTION. No code changed. Provenance verdict on the legacy
module + a ranked set of candidate directions + a recommended best.

---

## TL;DR — the recommended best direction

Stop thinking of this as "turn EMOS on." The legacy NGR module is **STALE** (old regime, no station
data, Gaussian-on-an-order-statistic, under-dispersed) and must not be reactivated. The current
architecture already points at the superior thing:

> **Repurpose the EMOS module from a parametric NGR post-processor into a settlement-grounded,
> lead- and spread-resolved *realized-error calibration authority*** — σ first (the highest-confidence,
> mission-aligned win), unified so it actually governs the served posterior, with an *optional*
> OOS-gated conditional-bias center nudge that the RAW law permits only where settlement proves it.

Concretely, in priority order:
1. **σ upgrade (lead + spread resolution of the realized floor).** Take EMOS's *insight* (width depends
   on ensemble spread S² and lead) but ground the *level* in realized settlement error, not the stale
   parametric σ. This is the genuine "superior σ" the realized-floor approach already half-built.
2. **Serving convergence.** Make the served posterior and the belief spine read ONE width authority,
   so the better σ reaches the trades that settle.
3. **Optional center: OOS-gated conditional-bias shrink** on the RAW convex center — do-no-harm,
   walk-forward-validated, applied only where the ensemble has a level-correlated bias the precision
   fusion + representativeness can't reach. Honest outcome may be "off for most cells."
4. **Deep follow-on (separate decision): an order-statistic-aware predictive distribution** (daily-MAX
   is not Gaussian). Biggest structural superiority, biggest effort/risk.

This reuses the one GOOD current artifact (realized settlement error), retires the stale parametric
cells + the μ-offset band-aid, respects the RAW no-de-bias law and the data/representativeness lane,
and fits the latest structure natively.

---

## 1. Current situation — what actually serves settlement, and where EMOS is

### 1a. The served posterior (what settles) — the materializer path
`forecast_posteriors` is 100% `posterior_method = openmeteo_ecmwf_ifs9_bayes_fusion`, produced by
`replacement_forecast_materializer` (the LIVE producer — yesterday's CWA/HKO live serving was deployed
through it; the integration agent's "offline backtest" label is incorrect):
- **Center:** `_mu_diagonal = Σ w_m·z_m` via `raw_precision_center` — a CONVEX combination of RAW
  member values, weights = inverse RAW second moment (incl. bias²) + Option C grid-representativeness
  + the new station sources (CWA/HKO). **No EMOS.**
- **σ:** `predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²))`, with the realized
  `settlement_sigma_floor` applied (materializer:894).
- **q:** `bin_probability_settlement` over the settlement-preimage Normal.

### 1b. The belief / trading spine (event_reactor decisions) — separate path
The q-kernel rebuild (`src/forecast/`): `build_center` → `build_sigma` → `predictive_distribution_builder`.
- `build_center`: RAW members (NoOp de-bias) → weighted Huber consensus (convex, in-envelope) →
  **dormant** `SHRUNK_EMOS` at strength 0 → envelope-enforced. A fail-closed antibody REJECTS any
  served center with a non-zero de-bias shift (the RAW no-de-bias law).
- `build_sigma`: composes `emos_sigma_model` as a candidate but **serves the realized walk-forward
  settlement-residual floor** when it exists (ARM replay n=693: the parametric RSS over-disperses 1.94×).
- Flag-gated EMOS lanes (`edli_emos_sole_calibrator_enabled`, `edli_emos_ci_live_enabled`) — **OFF.**

### 1c. The legacy EMOS subsystem — PROVENANCE VERDICT: **STALE_REWRITE**
| Artifact | What it is | Why stale |
|---|---|---|
| `emos_calibration.json` (`fit_emos_calibration.py`) | NGR cells μ=a+b·x̄, σ²=exp(c+d·logS²+e·lead); **CRPS-fit** (Nelder-Mead) + EB-shrink + held-out gate | Trained 2024–2025 only (no rolling); **no station data** (calibrates an older member set); per-(city,season) n≥40 (thin); **daily-MAX treated as Gaussian** (no order-stat); its own 2026 regime-gate likely demotes most cells to `served='raw'` |
| `emos_mu_offset.json` (`fit_emos_mu_offset.py`) | one-signed warm median-residual offset | Band-aid over an intercept that absorbed the grid-cold offset — a symptom fix the representativeness/station-data lane addresses at the source |
| strength-0 `SHRUNK_EMOS` | the dormant center shrink; `emos_oos_strength` has **no producer** | wired but inert; the only RAW-law-legal EMOS center seam, but unvalidated |
| `settlement_sigma_floor.json` (`fit_settlement_sigma_floor.py`) | **realized** MAD-σ of posterior center vs VERIFIED settlement; cohort ladder | **This one is CURRENT and good** — it is realized settlement error. But keyed `city\|season\|metric`, **lead-agnostic and spread-agnostic** (a constant per cell) |

The good news: the part worth keeping (realized settlement error) is exactly the part that is NOT
EMOS-parametric. The parametric NGR (μ slope + exp-σ) is the stale part.

---

## 2. Binding constraints (the "latest semantics" the upgrade must respect)

1. **RAW no-de-bias law.** The served center must be RAW (zero forward de-bias). EMOS's a+b·x̄ IS a
   forward de-bias → forbidden as a direct center; legal ONLY as a bounded, OOS-gated shrink toward the
   RAW consensus (whitelisted `SHRUNK_EMOS`), envelope-proofed.
2. **Realized error is the σ authority.** A served σ may never fall below the realized walk-forward
   settlement error. Parametric σ that under-disperses is the disease (iron rule 5).
3. **Data/representativeness lane is the chosen center philosophy.** Center accuracy is pursued by
   adding better-located/station data and precision-weighting it (CWA/HKO, Option C), NOT by regression
   de-bias. An EMOS center upgrade is, philosophically, swimming against this; it earns a place only as
   a residual the data lane provably can't reach.
4. **No hand-set weights / no caps / no shadow-default-OFF accretion** (operator laws). Any weight is
   fit from settled data; do-no-harm gates stay; don't add a flag-gated shadow lane as the "deploy."
5. **Settlement = raw airport METAR daily-MAX**, an order statistic — the honest target the predictive
   distribution should eventually match in shape, not just mean+var.

---

## 3. Candidate directions (evaluated against current situation + mission)

**D1 — Lead+spread-resolved realized-floor σ.** Extend `settlement_sigma_floor` to a lead-bucketed,
spread-modulated realized width: `σ = realized_floor(city,season,metric,lead) · g(S²_today / S²_typical)`,
where the LEVEL is realized settlement error and the SHAPE borrows EMOS's spread/lead dependence. Fixes
the lead-flat / spread-flat defect; settlement-grounded; fits the σ authority natively; reuses nothing
stale. **Confidence HIGH. Effort LOW–MED. Mission: coverage/q_lcb (and indirectly edge).**

**D2 — Serving convergence.** Unify the materializer's served σ and the spine's σ authority so the
best realized width governs the *posterior that settles*, not just belief. Precondition for D1 to reach
trades. Mostly plumbing + a parity check. **Confidence HIGH. Effort MED. Mission: makes any σ upgrade real.**

**D3 — OOS-gated conditional-bias center nudge.** A do-no-harm, walk-forward, per-cell slope/level
correction applied as a bounded shrink on the RAW convex center, fit fresh on CURRENT members (station
data included), CRPS, hierarchically pooled for thin cells, order-stat-aware — activated ONLY where it
beats the RAW+representativeness center on held-out settlement. Respects the RAW law via the existing
`SHRUNK_EMOS` seam + envelope proof. **Confidence MED (gate may turn it off for most cells — the honest
result). Effort MED. Mission: center accuracy, the headline metric, where it survives the gate.**

**D4 — Refit the full parametric NGR on current data.** Re-fit μ=a+b·x̄, σ²=exp(...) on rolling
2026 + station-inclusive members. **Evaluated and NOT recommended as the core:** it re-introduces the
forward de-bias the RAW law rejected, keeps the Gaussian-on-an-order-statistic gap, and its σ is
dominated by the realized floor anyway. Its only defensible residue (spread/lead SHAPE) is already
captured better by D1. Keep its CRPS+EB-shrink machinery as *implementation reference* for D1/D3, not as
the served model.

**D5 — Order-statistic / non-Gaussian predictive distribution.** Replace N(μ,σ) with a daily-MAX-aware
law (skew-normal / GEV upper tail / explicit max-of-hourly preimage). Deepest superiority — the chain
currently assumes Gaussian everywhere. **Confidence MED. Effort HIGH, risk HIGH.** A separate, later
decision; flagged so it isn't lost.

---

## 4. Recommended best direction — the synthesis

**Core: D1 + D2** — make the EMOS module's job the *settlement-grounded, lead- and spread-resolved
realized-width authority*, and converge serving so it governs the posterior. This is the highest-confidence,
most structure-native, mission-aligned upgrade, and it is precisely the superior form of what EMOS was
reaching for (spread-skill σ) without its stale parametric defects.

**Then D3 as an optional, gated center add** — let settled data decide, per cell, whether a bounded
conditional-bias nudge beats the RAW+data center. Build the missing OOS-strength producer; accept "off"
as a valid, honest outcome.

**D5 noted as the deep follow-on** — the order-statistic distribution is the next real frontier once the
width authority is correct, but it is its own project.

This explicitly **retires** the stale parametric cells (`emos_calibration.json`) and the μ-offset
band-aid as *served* artifacts, and reactivates **nothing** at its old weights.

---

## 5. Staged plan (each stage settlement-validated, do-no-harm, no shadow-default deploy)

1. **Confirm the serving truth** (1 read): verify the materializer is the live posterior producer and
   exactly how `predictive_sigma_c` and `settlement_sigma_floor` combine in the served σ today. (Resolves
   the one agent contradiction; pins D2's target.)
2. **D1 fitter**: extend the realized-floor fit to (city,season,metric,**lead-bucket**) + a spread
   modulation `g(S²)`, fit/validated walk-forward on settled residuals (CRPS + coverage/PIT), cohort
   ladder for thin lead-buckets. New artifact; old one left in place until parity proven.
3. **D2 convergence**: route the served posterior σ through the single realized-width authority; parity
   test (served σ == authority σ) before/after; coverage must not regress.
4. **D3 (optional)**: build the walk-forward OOS conditional-bias producer; thread a validated per-cell
   strength into the `SHRUNK_EMOS` seam; activate only where held-out settlement RMSE improves.
5. **D5**: separate design round.

## 6. Explicitly NOT done
- No flip-on of `emos_calibration.json` / `emos_mu_offset.json` at their stale fits.
- No setting `emos_oos_strength` > 0 without a walk-forward producer.
- No enabling `edli_emos_sole_calibrator_enabled` / `edli_emos_ci_live_enabled`.
- No hand-set weights, caps, or shadow-default-OFF "deploys."

## 7. Risks / open questions
- **Does the lead-flat floor actually bite the served posterior?** If `predictive_sigma_c`'s fused.sd
  already carries enough lead-dependence on most live cells, D1's marginal win shrinks — measure first.
- **Does D3 survive the gate anywhere?** If the RAW+representativeness center already captures the
  conditional bias, D3 is correctly a no-op — that is information, not failure.
- **Order-statistic skew (D5)** may be the dominant un-modeled error on hot cities; D1/D3 don't touch it.
- **Two-headed drift (D2)** must be resolved carefully (INV-37 cross-DB, identity hashes, no silent σ change).
