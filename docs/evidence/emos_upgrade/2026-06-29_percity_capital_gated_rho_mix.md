# Per-city calibration — capital-gated ρ-mix: VERIFIED (4 gates green), crosses the thin-data wall

**Date:** 2026-06-29
**Money-path stage:** forecast signal → **calibration** (the served settlement-bin q).
**Authority basis:** operator — "根据最新结构探索最新的emos方向" (explore the latest EMOS direction on the latest structure) + "利用数据" + "是数学和统计决定" (math and statistics decide).
**Supersedes the framing of:** [`2026-06-29_percity_calibration_realized.md`](2026-06-29_percity_calibration_realized.md) — that NO-GO was for **blanket** per-city (hard k/w swap, harms ~40% of cities). This doc is the **safe realization** that ships: the same per-city signal, served only up to each city's *earned* capital.

## FINAL VERDICT — GO (verified, math-licensed; not yet committed/deployed)
All four verification gates are GREEN. The capital-gated per-city EB ρ-mix is statistically licensed. Per [[ship-decision-is-math-stats-not-approval]] the gates are the arbiter; the remaining commit + live-artifact regen + materializer deploy are the live-money *acts* (operator-gated), not the decision.

## How it was derived — two-phase frontier consult (operator-designed) + local code-read
1. **Phase 1 (clean-room, no Zeus content):** an abstract design brief (the regime, the bin-win-log-score money metric, the measured phenomena P1–P6, the binding constraints) → the frontier model challenged the `Normal(σ·k)⊕Uniform(w)` frame and produced the **provable per-location non-inferiority mechanism** the blanket NO-GO lacked: fallback-anchored online stacking under a per-location **score-capital cap**.
2. **Phase 2 (the running repo):** reconcile the ideal vs Zeus's real EMOS → **ship the capital-gate on the EXISTING per-city EB machinery first**, NOT a Student-t rebuild (the live `k` already absorbs the σ-basis; a law swap breaks the calibration identity and has no per-location proof; the capital seam is reusable for Student-t/lead/F later).
3. **Local ground-truth (verification authority):** confirmed the settlement reporting-kernel + bin-edge convention are already correct (`bin_probability_settlement` integrates through `settlement_preimage_offsets`), the fused center μ is clean, and the only real plumbing gap (raw σ-spread decomposition) matters only for the *later* Student-t — so graft-first is right.

## The served object (zero hand-set constants)
`q_serve(bin) = (1−ρ_ℓ)·q_global(bin) + ρ_ℓ·q_cityEB(bin)`,
`ρ_ℓ = 0 if C_ℓ ≤ 0, else 1 − exp(−C_ℓ / W)` — `C_ℓ` = the city's earned out-of-sample Bernoulli-log-score capital, `W` = eligible Bernoulli-bin count.
**Pathwise proof:** every Bernoulli term loses at worst `log(1−ρ)` vs global, so a batch loses at worst `W·log(1−ρ) ≥ −C_ℓ` → a city can never spend more than it earned → per-location non-inferiority by construction. `C_ℓ≤0` ⇒ pure global ⇒ structurally cannot harm. A calibration weight, not a throttle; auto-rises from 0 (no flag).

## Implementation (TDD)
- **`scripts/fit_sigma_scale.py`** (fitter): `_eb_shrink` now shrinks on TRANSFORMED params (log k, logit w — geometric/logit, not raw-linear); `_fit_city_capital` computes leak-free per-city OOS capital `C_ℓ = Σ_splits[NLL_global − NLL_cityEB]`; `_fit_cities_shrunk` writes ONLY positive-capital cities, each with `score_capital`, **no min-n gate** (the capital decides). 13 tests.
- **`src/data/replacement_forecast_materializer.py`** (serving): extracted the pure `_build_scaled_normal_uniform_q` (byte-identical to today's global q — full k→floor→catch-all-cap→uniform ladder); `_replacement_city_candidate_lookup` (replaces the unsafe hard swap); `_city_rho_from_capital` + `_mix_q_by_rho`; the q point AND the q_lcb/q_ucb carriers are mixed by the same ρ and re-clipped; 5 city-provenance fields. 27 tests (incl. 3 goldens).
- **`scripts/percity_after_cost_ev_gate.py`** (gate): the after-cost EV non-inferiority replay.

## The four gates (the arbiter)
| Gate | Result |
|---|---|
| Byte-identical global (ρ=0 ⇒ today's q) | PASS — builder goldens (full ladder); no-cities artifact ⇒ q = q_global |
| Per-city proper-score non-inferiority (`C_ℓ>0`) | PASS by construction — the capital ledger is the consult's `D_ℓ≥0` test |
| ρ-mix serving math (mix / bounds / provenance) | PASS — 27 tests |
| Per-city after-cost EV ≥ 0 | PASS — 27/27 cities, aggregate **+0.0094** |

## Real-data numbers (window 2026-06-10..2026-06-28; temp artifact, live untouched)
- **18 C + 9 F cities** earn positive capital (top: Tel Aviv C=2.93, Chongqing 2.59, Wellington 2.54, Milan 2.33, Tokyo 2.33; Miami 0.86, Houston 0.72). κ_C=200, κ_F=20.
- Served `ρ ≤ 0.23` — a light blend (≥77% proven global even for the strongest city); the hot-city correction (Jeddah `k_eb=0.819` vs raw MLE 2.115, global 0.697) is a *gentle earned* widening.
- The blanket NO-GO's persistent losers (Madrid −7.65, Singapore −6.32) earn `C_ℓ≤0` → omitted → pure global → cannot harm. Chongqing (a −5.90 loser under raw blanket) earns +2.59 under transformed-EB + capital-gating — rehabilitated safely.
- **After-cost EV gate:** 466 cells reconstructed faithfully (sanity: rebuilt q_global matches stored q_json within 1e-6), 334 with decision-time books, 249 graded decision points (buy_yes + buy_no on the live carrier). 26/27 cities flip zero decisions; Shenzhen flips one, EV-positive. The expected non-inferiority signature.

## Reusability
The capital seam is the reusable safety layer. Sequenced behind it (each a candidate behind the same ρ-gate, not a fallback replacement): Student-t/skew-t base family, lead-specific (k,w), the F-family uniform-mixture C-only fix (deliberately NOT bundled here — it changes the fallback and would invalidate capital accounting).

## Activation (live-money acts — pending)
1. Commit the slice: `scripts/fit_sigma_scale.py`, `src/data/replacement_forecast_materializer.py`, `tests/test_fit_sigma_scale.py`, `tests/calibration/test_scaled_normal_uniform_q_builder.py`, `tests/data/test_per_city_sigma_scale.py`, `scripts/percity_after_cost_ev_gate.py` — untangled from unrelated uncommitted work (σ-revert, CWA/HKO, schema change) in the tree.
2. Regenerate `state/sigma_scale_fit.json` with the cities layer (proven content = `/tmp/sigma_fit_test.json`).
3. Deploy the materializer code (zeus-live-main + daemon restart) — until then the cities artifact is inert (old reader).
