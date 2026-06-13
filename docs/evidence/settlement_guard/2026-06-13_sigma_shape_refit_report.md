# GATE-2 σ-shape refit — settlement-forward report

**Task:** fix the GATE-2 defect (filled NO orders LOST because the system sold NO on the bin that settled YES, on every traded ring loser). Produce a CANDIDATE refit artifact + temporal-holdout evidence. No live-artifact overwrite, no flag flip (promotion operator-gated).

**Authority:** workflow A4 calibration diagnosis 2026-06-13 + docs/authority statistical_calibration_addendum. Worktree-isolated, sole editor.

---

## 1. WHY the live shape over-flattens (root cause, settlement-grounded)

Live model form (`state/sigma_scale_fit.json`, family C: k=1.5833, w=0.2811):

    q_adj(bin) = (1 - w) · Normal(μ, σ_impl·k)  +  w · UNIFORM(1/n_bins)

The second term is a **flat pedestal**: it adds `w/n_bins` to EVERY bin, near or far. The MLE chose w≈0.28 to buy far-tail coverage (the favorite-longshot far-NO harvest needs the far bins to carry realized frequency), but a uniform pedestal pays for that tail coverage by diluting the WHOLE vector — including the dist-1/dist-2 ring where the winner lands 0-2 steps from the mode. The fit's own settled table, reproduced on the **current 304-cell** population (live-form refit k=1.295, w=0.291 on the same data):

| dist | realized | LIVE mean_q (ratio realized/exp) |
|---|---|---|
| 1 | 0.2125 | 0.1697 (**1.253** — under-weighted ~25%) |
| 2 | 0.1193 | 0.0877 (**1.361** — under-weighted ~36%) |
| 3 | 0.0611 | 0.0514 (1.190) |
| ≥4 | 0.0167 | 0.0326 (0.512 — OVER-weighted; the pedestal dumps mass here) |

The winner lands on a ring bin → q(ring bin) is too low → q_no = 1−q(ring bin) is too high → the q_lcb>price gate admits a NO → the sharper market priced the winner correctly → we lose. **The market-anchor cap CANNOT stop this** (proven algebraically + by test, §6): `q_anchor_no = a·q_model_no + (1−a)·mkt; out = min(in, anchor)` is one-sided, so for a confident NO whose market also leans NO the blend stays above price → only trims size, never vetoes.

## 2. The DEEPER finding (why a global multiplicative k cannot be the fix)

Probing realized win-frequency and forecast sharpness PER DATE revealed **non-stationarity in the forecast dispersion itself**: mean σ_impl/step rose **0.85 → 0.90 → 1.04 → 1.33 → 1.73** over the 5-day settled window (06-08 → 06-12) — the posteriors are getting dramatically flatter day over day, while the realized ring dispersion is ~**constant in ABSOLUTE (step) units (~1.8 steps)**.

Consequence: a multiplicative k AMPLIFIES the non-stationarity. A k fit on the sharp early days (k≈2.4) over-widens the already-wide late days; a k fit on the late days (k≈1.0) is catastrophically too narrow for the early days. **A single global k that calibrates one half mis-calibrates the other** — this is exactly the prior "71.7% collapsed on holdout" pattern (project memory). Verified: multiplicative-k holdout gave dist-1 ratio **2.21** and dist-3 **5.66** on reverse split (worse than live).

## 3. THE FIX (principled, data-driven, holdout-stationary)

Two structural decisions (K << N), both fitted by MLE — **no hardcoded k/w/m/floor** (task #50 honoured):

1. **Drop the uniform pedestal** (w → 0). It demonstrably steals ring mass; nothing about the far-NO harvest requires a *flat* floor (the open-shoulder catch-all bins supply that coverage as a real density).
2. **Replace multiplicative-k with an ABSOLUTE σ-floor in STEP units**: `σ_core = max(σ_impl·k, floor_steps·step)`. The realized dispersion is ~constant in absolute terms, so a floor widens over-sharp forecasts UP TO the realized dispersion and leaves already-wide forecasts alone — regime-aware, hence holdout-stationary.

Model form (candidate; the kernel second-Normal `m` is retained for generality but the fit drives w→0, m→1):

    σ_core = max(σ_impl·k, floor_steps·step)
    q_adj(bin) = (1-w)·Normal(σ_core) + w·Normal(σ_core·m)

**Fitter:** `scripts/fit_sigma_shape_kernel.py` (candidate-artifact's ONLY writer; reuses `scripts/fit_sigma_scale.py` cell-build / σ back-out / integration verbatim). Objective = `LogLoss + λ·ring_calibration_penalty` (λ=10; penalty = Σ n·(log(realized/expected))² over dist 0-3). λ-sweep (§5) shows the floor is robust to λ — even at λ=0 the floor form wins once `floor_steps` is in the model.

**Full-set MLE result (n=304 C cells):** `k=1.0, w=0.0, m=1.0, floor_steps=1.8002`. **F family independently fits floor_steps=1.8037** (n=69) — the same ~1.8-step realized dispersion in BOTH unit families (1.8°C / 3.6°F), strong evidence the floor is a real physical quantity, not a city/unit artifact (HARD CONSTRAINT 2 satisfied).

## 4. Before/after ring ratio (same 304-cell in-sample population)

| dist | realized | LIVE ratio | CANDIDATE ratio | verdict |
|---|---|---|---|---|
| 0 | 0.223 | 0.926 | 1.042 | both ~ok |
| 1 | 0.213 | 1.253 | **1.150** | improved |
| 2 | 0.119 | **1.361** | **0.998** | **fixed** (the worst case → calibrated) |
| 3 | 0.061 | 1.190 | 1.045 | improved |
| ≥4 | 0.017 | 0.512 | 1.423 | mass pulled back to ring (correct direction) |
| tail | 0.016 | 0.259 | 0.296 | **far-NO harvest preserved** (mean_q > realized ⇒ NO keeps edge) |

Ring-calibration penalty: ~46 (live) → **12.6** (candidate). The relationship-test invariant `q(dist ring) ≥ realized_freq` is RED under the live uniform form and GREEN only under the refit floor (§6).

## 5. λ-sensitivity (robustness of the chosen objective)

| λ | k | w | m | floor | dist1 ratio | dist2 ratio |
|---|---|---|---|---|---|---|
| 0 | 1.00 | 0.02 | 2.80 | 1.74 | 1.142 | 1.014 |
| 10 (chosen) | 1.00 | 0.00 | 1.00 | 1.80 | 1.150 | 0.998 |
| 50 | 1.00 | 0.00 | 1.00 | 1.79 | 1.148 | 0.999 |

The floor ≈ 1.74-1.80 and the conclusion (drop pedestal + floor) are **invariant to λ from 0 to 50**. The structural answer does not depend on the objective weighting — the model FORM was the whole problem.

## 6. Relationship tests (RED-on-revert) — `tests/strategy/test_sigma_shape_ring_calibration.py`

All 5 pass (`/Users/leofitz/zeus/.venv/bin/python -m pytest`). Registered in `architecture/test_topology.yaml`.

- **RT-1 ring calibration** (synthetic settled population, known realized dispersion): dist-2 ring ratio = **4.15 (RED)** under the over-peaked/live shape; **1.02 (GREEN)** under floor=1.8. RED-on-revert CONFIRMED: setting floor_steps=0 turns the GREEN assertion (|ratio-1|≤0.05) RED (ratio 4.15).
- **RT-2 market-anchor-cap blind spot**: `test_rt2_market_anchor_cap_cannot_veto_a_confident_no` pins that a confident NO (q_model_no=0.85) whose market also leans NO (price 0.62) is NOT vetoed (blend 0.735 > price) — the cap only trims, the admit survives. Plus one-sided + trim-when-below pins. Nobody can re-claim the cap fixes GATE-2.

## 7. TEMPORAL-HOLDOUT evidence (the LICENSE) — `scripts/sigma_kernel_holdout_replay.py`

Fit on `target_date < split`, evaluate on `≥ split`; live uniform form and candidate floor form scored on the SAME held-out cells (leak-free). NO-admit gate scored against settlement; market proxy = `1 − realized_freq(dist)` (the harshest honest market: the model gets no edge unless its q deviates from the settled base rate); after-cost 2¢.

### Split 2026-06-11 (train 178, test 126)
Candidate train fit: k=1.017, w=0, **floor=2.022**.

| metric | LIVE | CANDIDATE |
|---|---|---|
| held-out dist-1 ratio | 1.87 | **1.51** |
| near-ring NO admits | 316 | 251 |
| near-ring NO win-rate | 0.744 | **0.749** |
| **near-ring NO LOSSES** | **63** | **47** |
| **GATE-2 losses PREVENTED** | — | **16 (25%)** |

The 16 prevented are dominated by dist-0 winners (the mode itself settled) — the σ-floor stops over-peaking the mode, so q_no(mode) drops below price and the losing NO is no longer admitted (Busan, Chongqing, Guangzhou, Istanbul, KL, London, Manila, Milan, Moscow, Munich, Panama City, Shanghai, Singapore, Warsaw, Wellington×2).

### Split 2026-06-10 (train 109, test 195) — robustness
Candidate train fit: k=1.0, w=0, **floor=2.255**.

| metric | LIVE | CANDIDATE |
|---|---|---|
| held-out dist-1 ratio | 1.79 | **1.49** |
| near-ring NO losses | 100 | 101 (neutral) |
| **far NO admits / win-rate** | 0 / — | **21 / 1.00** (harvest improved) |

### Honest verdict on the holdout
- Ring calibration improves **consistently** out-of-sample (dist-1 1.87→1.51 and 1.79→1.49 across both splits).
- The far-NO favorite-longshot harvest is **preserved and even improved** (split-10: 21 winning far-NO admits the live form missed, win-rate 1.0).
- The σ-floor form generalizes **far better** than multiplicative-k (which collapsed to dist-1 2.21 / dist-3 5.66).
- BUT the **near-ring GATE-2 loss prevention is regime-dependent** (−16 at split-11, ~0 at split-10) because the train window's floor magnitude (2.02-2.26) drifts with the moving σ_impl regime, and 5 days is too short to pin it. The named KL/Karachi 06-12 dist-0/1 winners are NOT prevented by either form (those are cases where the realized mode freq was genuinely low — honest no-edge, not a shape bug).

## 8. Ring-loss replay (named losers)
In both held-out windows the named KL/Karachi cells where the winner landed at dist-0/1 are admit-and-lose under BOTH forms (the floor doesn't fabricate an edge the data doesn't support — correct). The candidate's prevention is concentrated where the MODE settled (the over-peaking the floor directly fixes), and on the far-NO harvest. HK 06-10 (winner dist-2) is correctly NOT a NO-loss under either form.

## 9. Readiness verdict
**PROVEN:** (a) the disease is the uniform pedestal + a global multiplicative k that amplifies non-stationary forecast sharpness; (b) the structural fix is drop-pedestal + absolute σ-floor (~1.8 step), fitted by MLE, robust to λ, agreeing across C and F families; (c) in-sample the ring (esp. dist-2) is restored to calibration and the far-NO harvest is preserved; (d) out-of-sample the ring calibration and harvest improve and the form generalizes where multiplicative-k collapsed.

**NEEDS FORWARD FILLS:** the exact floor MAGNITUDE (1.80 full-set; 2.02-2.26 on shorter trains) is not yet stationary on 5 days — the forecast-sharpness regime is still moving. The GATE-2 near-ring loss prevention is real at the recent split (−25%) but regime-dependent. **Do NOT promote on backtest alone** — the license is forward after-cost settlement win-rate, which requires live fills under the candidate.

## 10. Artifacts / blockers
- Candidate artifact: `state/sigma_scale_fit.candidate.json` (local, gitignored) + committed copy `docs/evidence/settlement_guard/sigma_shape_kernel_candidate.json`. **Live `state/sigma_scale_fit.json` UNTOUCHED. No flag flips. No daemon restart.**
- Fitter: `scripts/fit_sigma_shape_kernel.py`. Holdout harness: `scripts/sigma_kernel_holdout_replay.py`. Both allowlisted in `src/state/db_writer_lock.py` (read-only ro-uri).
- Evidence: `docs/evidence/settlement_guard/sigma_shape_holdout_split11_C.md`.
- **Promotion is operator-gated AND requires consumer wiring**: the materializer currently applies `(k, w)` only — it must be wired to read `floor_steps` (apply `σ_core = max(σ_impl·k, floor_steps·step)`) and to drop the uniform-mixture branch when w=0. That wiring is a SEPARATE change, not done here (candidate-only scope).
- No blockers to the candidate deliverable.
