# EMOS μ* Airport-Settlement-Honest Correction — Implementation (D4 follow-through)

**Date:** 2026-06-14
**Authority basis:** `emos_mu_bias_probe.md` (D4) + law 8 (the live EMOS center must be
airport-settlement-honest). HELD in worktree for review — no daemon touch, no restart, no main commit.

---

## 1. Discriminating probe — which mechanism won

The D4 critical unknown: is the per-city EMOS μ* cold residual driven by (a) the absent S2
representativeness de-bias on x̄ BEFORE the EMOS formula, or (b) the EMOS intercept `a` already fit cold?

**Probe** (`scripts/probe_emos_mu_correction_D4.py`, run 2026-06-14): reconstruct the LIVE
μ* = a + b·x̄_ensemble from `ensemble_snapshots` (contributes_to_target_extrema=1, shortest genuine
forecast lead 24–144h, per-city members_unit → °C) + live `emos_calibration.json` params; compare three
centers OOS (walk-forward, embargoed) against VERIFIED settlement: **B0** no correction, **A** S2
grid-representativeness de-bias applied to x̄, **C** residual-grounded μ-offset on (μ*−settlement).

Reconstruction validated EXACTLY against the D4 doc: Tokyo −1.146 mean / −1.890 MAM median,
SF +1.055, Beijing +0.405, Karachi +1.145 — byte-for-byte the probe doc's numbers (the SF unit
wrinkle: SF members are `degF`, converted to °C before x̄, matching `build_emos_q`).

| cell | B0 res | **A (x̄-debias)** res | **A** CRPS vs B0 | C (μ-offset) res | C CRPS vs B0 |
|------|--------|----------------------|------------------|------------------|--------------|
| Tokyo\|MAM | −1.368 | **+1.641** (over-warm) | 1.448 vs 1.415 (worse) | **−0.196** | 1.390 vs 1.415 (better) |
| Beijing\|MAM | +0.117 | +2.698 | 2.352 vs 1.811 (worse) | — | — |
| Karachi\|MAM | +1.145 | +2.470 | 1.805 vs 0.800 (worse) | — | — |
| SF\|MAM | −0.089 | +4.577 | 3.868 vs 0.937 (worse) | — | — |

**VERDICT: mechanism (b) / intercept-recalibration wins. (a) is REFUTED.** The S2 grid offset is
large and same-signed for ALL four cities (Tokyo −2.59, SF −3.19, Beijing −2.77, Karachi −1.68 °C),
yet EMOS μ* is unbiased/absorbed for SF-MAM/Beijing/Karachi and cold only for Tokyo. Applying the S2
de-bias on x̄ warms every city by ~b·offset ≈ +3 °C and worsens CRPS everywhere — proof the EMOS
intercept `a` ALREADY absorbed the grid-cold offset at fit time; an x̄-side de-bias DOUBLE-counts. The
correction that earns it OOS is a **residual-grounded per-cell μ-OFFSET measured directly on
(μ*−settlement)** — equivalent to recalibrating the per-cell EMOS intercept on clean VERIFIED settlement.

This is NOT the broken previous_runs-vs-single_runs anchor-offset fitter (that measures the IFS
single-run ANCHOR residual feeding the SHADOW fusion lane, which never reaches the live EMOS
calibrator). This measures the LIVE EMOS center against VERIFIED settlement and corrects only it.

---

## 2. All-city cold scan + the corrected vs left-alone list

`scripts/scan_emos_mu_residual_all_cities.py` (all 54 cities with ensemble snapshots): 22 cells are
materially cold (mean μ*−settlement < −0.5 °C, n ≥ 8). The walk-forward do-no-harm OOS gate
(`scripts/fit_emos_mu_offset.py`: residual toward 0 by ≥0.15 °C AND CRPS improved by ≥0.01 °C over
≥5 held-out days, anti-overcorrection guard) activates **10** of them.

**CORRECTED (cold + earns the OOS gate):** Tokyo\|MAM, Dallas\|MAM, Los Angeles\|MAM, Mexico City\|MAM,
Miami\|MAM, Munich\|MAM, Munich\|JJA, NYC\|MAM, Kuala Lumpur\|JJA, Wellington\|JJA.

**LEFT ALONE — EMOS-absorbed (residual ≈ 0, per D4):** San Francisco\|MAM (−0.089), Beijing\|MAM
(+0.117), Beijing\|JJA (+0.825), Karachi\|MAM (+1.145, warm).
**LEFT ALONE — warm overshoot (separate anomaly, explicitly out of scope):** San Francisco\|JJA
(+2.868). The fitter is one-signed-honest: it never activates a warm cell, so a warm center is never
cooled by this correction.
**LEFT ALONE — cold but gate-fail (offset overcorrects OOS):** Tokyo\|JJA (−0.839 → corrected −1.61
OOS, worse), Paris\|JJA, Tel Aviv\|JJA, plus thin cells dropped by MIN_OOS (Ankara\|JJA, Milan\|JJA,
Seattle\|JJA, Tel Aviv\|MAM) — all fail-closed to today's uncorrected behavior.

---

## 3. OOS validation (the GATE) — per corrected city, before → after

Residual = mean(μ*−settlement) on the held-out (walk-forward, embargoed) days; CRPS = mean Gaussian
CRPS on the same held-out days. residual_pairs_total = 1363 VERIFIED no-leak (city,date) pairs.

| cell | offset °C | n | OOS n | res before → after | CRPS before → after |
|------|-----------|---|-------|--------------------|---------------------|
| **Tokyo\|MAM** | **−1.890** | 18 | 10 | **−1.502 → −0.196** | **1.599 → 1.390** |
| Dallas\|MAM | −1.417 | 19 | 11 | −1.248 → −0.471 | 1.074 → 0.904 |
| Los Angeles\|MAM | −1.610 | 19 | 11 | −1.720 → −0.161 | 1.254 → 0.520 |
| Mexico City\|MAM | −1.752 | 19 | 11 | −1.891 → +0.106 | 1.450 → 0.536 |
| Miami\|MAM | −2.473 | 15 | 7 | −2.487 → +0.034 | 2.029 → 0.615 |
| Munich\|MAM | −0.610 | 21 | 13 | −0.805 → −0.364 | 0.614 → 0.486 |
| Munich\|JJA | −1.112 | 13 | 5 | −0.731 → +0.230 | 0.952 → 0.839 |
| NYC\|MAM | −0.554 | 22 | 14 | −0.551 → +0.138 | 0.872 → 0.857 |
| Kuala Lumpur\|JJA | −0.618 | 13 | 5 | −0.401 → +0.233 | 0.490 → 0.474 |
| Wellington\|JJA | −0.405 | 13 | 5 | −0.413 → −0.013 | 0.315 → 0.263 |

Every activated cell drives its OOS residual toward 0 AND improves OOS CRPS, without over-correcting
into a larger-magnitude warm bias. No cell ships a correction that does not earn it.

---

## 4. RED-on-revert evidence

`tests/calibration/test_emos_mu_offset.py::test_red_on_revert_warm_bin_gets_honest_q_then_reverts_cold`
(live `build_emos_q` seam, Tokyo\|MAM): with the cell ACTIVATED the °C center warms by exactly
−offset_c = +1.890 °C; the 21–24 °C warm bin and the ≥24 °C warm shoulder both GAIN q (the warm-side
winner under-priced by the cold center), the cold bins SHED mass, σ is unchanged. Deactivate the cell
(revert) → the center is cold again and the warm winner is under-priced again. PASSES.

End-to-end live spot check (Tokyo\|MAM, real params): μ 20.60 → 22.49 °C; ≥24 °C warm shoulder q
0.018 → 0.184 (≈10× more q_lcb mass on the warm winner); reverts exactly on deactivation.

---

## 5. Airport-settlement-honest verdict + live-promotion judgment

The corrected μ* is **airport-settlement-honest** for the 10 activated cold cells: each lands within
±0.5 °C of VERIFIED settlement OOS (Tokyo\|MAM −1.50 → −0.20), grounded in measured `μ*−settlement`
residuals against VERIFIED `settlement_outcomes`, walk-forward and embargoed (no leakage), fail-closed
on thin/absent data (no correction → today's behavior, never crashes a live size), and one-signed (it
only warms a measured-cold center, never cools the SF\|JJA warm overshoot).

It **earns live promotion on settlement-graded OOS evidence**: every shipped correction improves both
the per-cell residual and OOS CRPS; the EMOS-absorbed and warm cells are provably untouched. HELD in
worktree — it goes live only when the orchestrator lands it and restarts the daemon, at which point
the operator must run `scripts/fit_emos_mu_offset.py` (after the σ-floor fit) to materialize
`state/emos_mu_offset.json` in the live state dir.

---

## Provenance

- DB / tables (read-only, `?mode=ro`): `state/zeus-forecasts.db` (ensemble_snapshots +
  settlement_outcomes authority=VERIFIED), `state/emos_calibration.json`,
  `state/grid_representativeness_offset.json`.
- Producer: `scripts/fit_emos_mu_offset.py` → `state/emos_mu_offset.json`
  (authority `emos_mu_offset_v1_residual`), allowlisted read-only in `src/state/db_writer_lock.py`.
- Consumer: `src/calibration/emos.py::emos_mu_offset` (fail-closed loader) →
  `src/calibration/emos_q_builder.py::build_emos_q` (μ_corr = μ* − offset_c, °C, before σ-floor/unit).
- Tests: `tests/calibration/test_emos_mu_offset.py` (14, incl. RED-on-revert) — PASS.
