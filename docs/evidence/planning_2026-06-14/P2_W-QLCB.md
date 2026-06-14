# P2 — W-QLCB: File-Level Implementation Plan for the q_lcb Collapse Fix

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (P2 workstream plan within task #79). No production edits, no deploy, no live touch. DBs opened `?mode=ro`.
**Workstream:** W-QLCB — THE HEADLINE constraint: why q_lcb collapses to ≈0 on cheap/near-center bins, and the exact change (or the settlement-graded proof it is honest).
**Authority spine:** `diagnosis_confirmation.md` (authoritative target), `P1_strategy_of_record.md` (Thrust 3 + open decisions D1–D4), `b2_capital_efficiency_audit.md`, operator contract laws 1–8. Every empirical claim is cited to file:line, artifact, or query+counts gathered THIS session.

---

## 0. RE-DERIVATION FROM EVIDENCE (not a restatement of the diagnosis)

The diagnosis says: ~88% of rejections are `capital_efficiency_lcb_ev`, "overwhelmingly because q_lcb has collapsed to ≈0" (`diagnosis_confirmation.md:18,128`). I re-derived this from source and the live DB this session and found the picture is **sharper and more actionable** than "q_lcb≈0 everywhere." There are **three distinct populations** inside the 18,829-count `capital_efficiency_lcb_ev` mass, and only ONE is the fix target. Getting this taxonomy right is the whole plan — a fix aimed at the wrong sub-population either manufactures far-tail alpha (law 4 violation) or moves nothing.

### 0.1 The live q_lcb is the BUNDLE path, and it is LIVE right now

Confirmed live flag state (`config/settings.json`, read this session):
- `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled = True` → `_replacement_authority_enabled()` returns True (`event_reactor_adapter.py:9616-9621`), so the **replacement_0_1 bundle path is the live primary** (`_replacement_authority_probability_and_fdr_proof:9822`). The canonical bootstrap path (`_canonical_probability_and_fdr_proof:10228`) is the fallback when the bundle is absent/ineligible.
- `q_lcb_settlement_coverage_gate_enabled = True` → the settlement-coverage shrink is **active live**, not shadow. (P1 §1.1 assumed the canonical seam was primary; the live flag shows the bundle seam dominates. This does NOT change the fix — it changes WHICH producer the fix must intercept. See D1.)
- `edli_emos_ci_live_enabled = None` (absent → default False): the EMOS override never fires (`diagnosis_confirmation.md:50-57`). Red herring, confirmed.

So the live q_lcb the `capital_efficiency` gate consumes is, for an eligible family, the **bundle `q_lcb` map** read by `_replacement_yes_lcb_for_bin` (`event_reactor_adapter.py:9756`), which prefers the materialized `fused_center_bootstrap_p05` map and falls back to Wilson-over-AIFS-votes (returning **literal 0.0** for any zero-vote bin, `:9788`).

### 0.2 The bundle q_lcb crush mechanism — proven at source + DB

The materialized bundle `q_lcb` map is built by `_build_fused_q_bounds` (`replacement_forecast_materializer.py:1359`). The call site (`:1764-1766`):

```python
_lcb_map, _ucb_map = _build_fused_q_bounds(
    ...
    center_sigma_c=float(bayes_precision_fusion_override.anchor_sigma_c),
    ...
)
```

The bound construction (`:1396-1426`):
```python
mu_draws = rng.normal(loc=mu_star, scale=center_sigma_c, size=200)   # N(μ*, center_sigma_c)
...
probs = ndtr(z_high) - ndtr(z_low)        # per-draw per-bin settlement mass
q_lcb_vec = np.percentile(probs, 5.0, axis=0)   # 5th pct ACROSS the 200 center draws
```

And the DB fact (query this session, `state/zeus-forecasts.db`):
```
SELECT json_extract(provenance_json,'$.anchor_sigma_c'), COUNT(*)
FROM forecast_posteriors GROUP BY 1;   →   3.0 | 3464
```

**Every live posterior carries `anchor_sigma_c = 3.0`.** So the live bundle q_lcb is: *draw 200 centers from N(μ*, 3.0°C), integrate each settlement bin, take the per-bin 5th percentile.* A 3°C center wobble is enormous relative to a ~1–2°C-wide ring bin: across 200 draws the center wanders ±5–6°C, so on roughly 5% of draws the ring bin's mass is near-zero, and the **5th-percentile of that distribution is ≈0** — while the point q (integrated at the fixed μ*, `predictive_sigma_c`) stays at its honest ~0.10. This is the mechanical crush, at source, on the live path. It is NOT a structural-zero (the point mass is real); it is a too-wide center-uncertainty jitter zeroing the lower tail of a bin the model genuinely believes in.

The materializer's own comment already KNOWS this (`:1216-1222`): *"The soft-anchor Gaussian spread is anchor_sigma_c≈3.0C (wide vs ~1-2C settlement bins); a center-uncertainty bootstrap at that sigma collapses the per-bin 5th-percentile to ~0 on every bin (useless)."* It used that knowledge to justify the Wilson FALLBACK on CAPTURE_MISSING rows — but the FUSED rows still go through the 3.0°C bootstrap. The defect was diagnosed in a comment and never fixed on the live fused path.

### 0.3 The THREE populations inside `capital_efficiency_lcb_ev` (the taxonomy that drives the plan)

From `b2_capital_efficiency_audit.md` §2–§3 (real candidates, real q_lcb/price):

| Pop | Example | q_lcb | price | What it is | Fix target? |
|---|---|---|---|---|---|
| **A — structural far-tail zero** | Munich 26C+ | 0.0000 | 0.0010 | 0 ensemble members on bin; point mass genuinely ~0 | **NO** — honest (laws 1/4/8). Keep rejecting. |
| **B — center-crush ring zero/near-zero** | (ring `exact` bins; P1 §1.3) | ≈0 | ~0.09 | point q ~0.10 honest, lower tail zeroed by the 3.0°C jitter | **YES** — the suppressed alpha |
| **C — honest below-market** | Seoul 25C: 0.0247 vs 0.080; Busan 29C: 0.0681 vs 0.16; Istanbul 24C: 0.0884 vs 0.34 | positive | > q_lcb | model conservatively BELOW market; (q_lcb−price)<0 | **NO** — honest no-edge (the model thinks the market over-prices) |

**Population C is the largest single share of the 18,829** (b2 §3: "16-21 out of 22 bins per family") and it is **NOT a q_lcb-collapse-to-zero problem** — these q_lcb values are positive and meaningful; they are simply below the market price because the model honestly disagrees in the market's favor. **No q_lcb fix should touch population C** — raising q_lcb to clear those prices would be manufacturing edge the model does not hold (law 8: the point belief itself is below market). The diagnosis's phrase "q_lcb collapsed to ≈0" over-generalizes; the b2 audit's own numbers show most of the mass is population C, not zero.

**Population B is the ONLY fix target.** It is the cohort where the POINT q is honest and ABOVE or AT the market (model agrees there is mass the market under-prices) but the lower-bound construction zeroes the 5th percentile. P1 §1.3's verdict stands and I confirm it: *q_lcb≈0 is HONEST on A (far/tail) and BROKEN on B (near-center ring); settlement is the instrument that separates them; the point q is never the defect.*

### 0.4 Why "everything can only lower q_lcb" is the architectural disease (P1 §3 / S1, confirmed)

Trace every authority that can move the live q_lcb:
- Canonical: `probability_uncertainty_from_samples` → `raw_lcb = percentile(samples, 5)`, then `q_lcb = clip(raw_lcb − penalties.total(), 0, 1)`, then `min(q_lcb, q_point)` (`probability_uncertainty.py:307,311,315`). Penalties **only subtract**. The N_eff correction **only widens** (lowers). And on the live canonical call there are **no penalties and no n_eff_override** (`event_reactor_adapter.py:10181`) — so it is the raw 5th percentile, floored under the point.
- Bundle: the 3.0°C center bootstrap 5th percentile (§0.2), with an optional `settlement_floor_lcb` that **only ever lowers** (`event_reactor_adapter.py:9771-9774`, currently passed `None` so inert).
- Coverage: `apply_settlement_coverage` returns `min(q_lcb, verdict.q_lcb_out)` on UNLICENSED, else unchanged (`settlement_backward_coverage.py:222-224`) — **shrink-only, one-directional.** `_isotonic_realized_rate` (`:97`) computes the realized rate but the wrapper only ever uses it to LOWER.

**There is no authority anywhere that can RAISE a too-low q_lcb toward its settlement-realized rate.** Yet the settlement record (S1's join, P1 §3) says under-claiming is the dominant alpha-killer on the ring. The fix must add the missing UP arm — using the SAME settlement-realized-rate map the shrink already computes — making the correction bidirectional. This is a SIMPLIFY (it collapses the raw-percentile producer + the shrink-only coverage into one settlement-anchored bidirectional authority), not an addition.

---

## 1. THE FIX (current path → exact change)

### 1.1 Current path to file:line (the live q_lcb pipeline, both producers)

```
                       ┌─ BUNDLE (live primary, flag ON) ──────────────────────────────┐
forecast_posteriors    │ _build_fused_q_bounds(center_sigma_c=anchor_sigma_c=3.0)       │
(anchor_sigma_c=3.0) ──┤   materializer.py:1359  → 5th pct of N(μ*,3.0) draws → q_lcb map│
                       │ read live by _replacement_yes_lcb_for_bin  adapter:9756         │
                       │   (else Wilson-over-votes → 0.0 for zero-vote bins :9788)       │
                       └────────────────────────────────────────────────────────────────┘
                       ┌─ CANONICAL (fallback) ─────────────────────────────────────────┐
analysis.bin_yes_      │ _side_q_lcb_from_yes_samples  adapter:10148                     │
probability_samples ───┤   → probability_uncertainty_from_samples (no penalties/n_eff)   │
                       │   → raw 5th percentile, floored under q_point  prob_unc.py:307   │
                       └────────────────────────────────────────────────────────────────┘
both producers write → lcb_by_direction : QlcbByDirection   (qlcb_provenance.py:120)
        │
        ├─ coverage shrink (flag ON): apply_settlement_coverage  → min(q_lcb, q_lcb_out)   [shrink-only]
        │      settlement_backward_coverage.py:204; verdict from _isotonic_realized_rate:97
        │
        └─ proof.q_lcb_5pct  (adapter:7403 via _qlcb_raw_float)
               │
               └─ live_capital_efficiency_rejection_reason(q_lcb=q_lcb_5pct, price)   live_admission.py:87
                      conservative_ev = (q_lcb − price)/price ; reject iff ≤ 0     ← THE HONEST GATE (KEEP)
```

The gate at `live_admission.py:113` is the honest arbiter and is **out of scope** — we never touch the `(q_lcb − price)/price ≤ 0` comparison. The fix targets the q_lcb that flows IN.

### 1.2 The exact change: a settlement-calibrated BIDIRECTIONAL q_lcb authority

**New module:** `src/calibration/settlement_calibrated_qlcb.py` (one new file; it REPLACES three scattered behaviors, net file-count-neutral after Thrust-2 deletions in the sibling P2 plans).

**Core function (before → after of the coverage seam):**

CURRENT (`settlement_backward_coverage.py:204-225`, shrink-only):
```python
def apply_settlement_coverage(*, q_lcb, verdict, enabled) -> float:
    if not enabled:
        return float(q_lcb)
    if verdict.status == "UNLICENSED":
        return float(min(float(q_lcb), float(verdict.q_lcb_out)))   # only ever LOWERS
    return float(q_lcb)
```

NEW (bidirectional, reusing the SAME isotonic realized-rate map):
```python
def calibrated_qlcb(*, q_lcb_raw, realized_isotonic, n_obs, q_point, enabled) -> CalibratedQlcb:
    """Move q_lcb toward the settlement-realized win-rate in its band — BOTH ways.

    realized_isotonic is _isotonic_realized_rate(obs, q_lcb_raw): the monotone
    claimed-band → realized-win-rate map read at the claimed band.
    """
    if not enabled or n_obs < MIN_N_BIDIRECTIONAL:        # thin → cold-start fallback
        return CalibratedQlcb(q_lcb=q_lcb_raw, source="FORECAST_BOOTSTRAP", arm="none")
    # honesty margin: a lower bound must under-claim the realized point estimate
    target = max(0.0, realized_isotonic - JEFFREYS_MARGIN)
    # clamp so the calibrated LCB never exceeds the honest point belief (law 8 / Hidden #2)
    target = min(target, float(q_point))
    if target < q_lcb_raw - TOL:                          # DOWN arm (preserved: record over-claimed)
        return CalibratedQlcb(q_lcb=target, source="SETTLEMENT_ISOTONIC", arm="down")
    if target > q_lcb_raw + TOL:                          # UP arm (NEW: record proves bound too low)
        return CalibratedQlcb(q_lcb=target, source="SETTLEMENT_ISOTONIC", arm="up")
    return CalibratedQlcb(q_lcb=q_lcb_raw, source="SETTLEMENT_ISOTONIC", arm="hold")
```

The UP arm is the entire fix: where the settled record proves the band's realized win-rate exceeds the crushed raw bound (population B's signature), q_lcb rises from ≈0 toward its settlement-real ~0.03–0.13, clears the ~0.09 price, and `capital_efficiency` ADMITS — honestly, no loosening. Population A (far tail) has realized rate ≈0 in its band → target stays ≈0 → keeps rejecting. Population C (honest below-market) has q_point already below price → the `min(target, q_point)` clamp keeps the calibrated q_lcb under the point under the price → keeps rejecting. **The clamp to q_point is what makes population C untouchable** — the UP arm can never raise q_lcb above the model's own point belief, so a model that is honestly below market is never lifted over the price.

**Cold-start (the thin-cohort fallback).** When `n_obs < MIN_N` the bidirectional map is untrustworthy. P1's "Jeffreys/Wilson analytic lower bound" is the fallback. The honest, simple choice: **keep the raw q_lcb** (do not calibrate) AND tag the band so the harness (Thrust 6) accrues observations. Do NOT invent a tighter analytic floor on thin data — that is exactly the over-confidence the materializer comment warned against (`materializer.py:1219`). `MIN_N` is decided in §4-D5.

### 1.3 The bundle-path root repair (D1 lean: single authority; D3 fallback: σ_center fit)

The bidirectional calibration above intercepts the q_lcb at the `lcb_by_direction` seam, AFTER the producer. That repairs BOTH paths if it becomes the single authority feeding the gate (P1-D1 lean). But it leaves the **3.0°C center jitter generating a garbage raw input** — the UP arm then has to do all the lifting from ≈0, which is fragile on thin cohorts. The cleaner, composable repair attacks the raw producer too:

**The `center_sigma_c=3.0` is itself the bundle-path bug** (`materializer.py:1766`). `anchor_sigma_c` is supposed to be the *posterior center uncertainty* (`fused.sd`), but the DB shows it is pinned at the prior default `3.00` (`materializer.py:125`) universally — meaning `fused.sd` collapsed to the soft-anchor prior τ0 and was never per-cell fitted. **The center-uncertainty jitter should be the SEM of the fused center (~0.3–0.5°C for a multi-member fusion), not the 3.0°C predictive spread of the temperature itself.** Drawing centers at 3.0°C conflates "how uncertain is the center" with "how spread is the weather" — a category error that over-widens the bound by ~6–10×.

This is P1-D3. Two sub-options, weighed in §4.

### 1.4 RESOLVING THE FORK: 2–3 weighed alternatives + opinionated pick

The decisive design choice is **WHERE the fix lands** — the post-producer calibration seam, the raw producer, or both.

**Alternative 1 — Post-producer bidirectional calibration ONLY (intercept at `lcb_by_direction`).**
- *How:* §1.2, applied as the single q_lcb authority for both paths; leave `center_sigma_c=3.0` untouched (the UP arm corrects its garbage output).
- *Pro:* one seam, one authority (大一统), touches no producer, smallest blast radius, self-correcting bidirectionally, settlement-anchored end-to-end.
- *Con:* the raw input is still ≈0 garbage on the ring, so the UP arm must lift from ≈0 every cycle; on a band with `n_obs` just above MIN_N the lift is noisy; far-tail and ring share the same crushed raw value so the calibration carries the entire discriminative load (acceptable — settlement IS the discriminator — but it concentrates risk in the isotonic fit).

**Alternative 2 — Raw producer fix ONLY (fix `center_sigma_c`).**
- *How:* replace `center_sigma_c=anchor_sigma_c` with a settlement-fitted `σ_center` (the SEM of the fused center, or |fused_center − settled| by lead bucket); leave the coverage seam shrink-only.
- *Pro:* fixes the bound at its source so the raw q_lcb is honest before any calibration; the ring bin's 5th percentile stops being ≈0 mechanically; composes with the point-q σ-fit (W's sibling Thrust 4).
- *Con:* does NOT add the missing UP arm — if the model is systematically under-claiming for a *different* reason (not just the center jitter), nothing raises it; leaves the "everything only lowers" architectural disease (§0.4) intact; σ_center must itself be fitted and validated (a second artifact).

**Alternative 3 — BOTH: raw producer fix (σ_center) + post-producer bidirectional calibration.** ← **PICK**
- *How:* (a) fix `center_sigma_c` to a settlement-fitted center-uncertainty σ (§1.3 / D3) so the raw bundle q_lcb is honest at birth; (b) layer the bidirectional settlement calibration (§1.2) as the single authority over BOTH paths so the settled record can still correct residual mis-claiming in EITHER direction. The two compose: the producer fix makes the raw input honest (UP arm rarely needs to lift from ≈0), and the bidirectional calibration is the settlement-graded safety net that the producer fix alone cannot provide.
- *Why this pick:* it is the only option that closes BOTH the mechanical crush (§0.2) AND the architectural one-directionality (§0.4). It is still a net SIMPLIFY: the producer fix REPLACES one wrong constant with a fitted one (no new gate); the calibration COLLAPSES the raw-percentile + shrink-only-coverage into one bidirectional authority (gate count down). It is sequenced shadow-first so neither half ships unproven (§3, §4-D2). The marginal cost over Alternative 1 is the σ_center artifact (D3), which is the same artifact-fitting pattern already blessed for the σ-shape fit (`sigma_scale_fit.json`) — a known, low-novelty move.

**Rejected:** Alternative 1 alone (leaves the garbage raw input and concentrates all risk in the isotonic), Alternative 2 alone (leaves the architectural disease, no settlement-graded UP correction). A fourth tempting option — "just lower `center_sigma_c` to a hand-picked 0.5°C" — is **forbidden** (operator law: no operator-picked constants; `sigma_scale_fit.json` precedent is FITTED, task #50). σ_center must be fitted from the settled residual record, never chosen.

---

## 2. MIGRATION / DATA STEPS

1. **Fit the bidirectional isotonic map.** Reuse `_isotonic_realized_rate` (`settlement_backward_coverage.py:97`) — it already builds the monotone claimed-band → realized-win-rate map and is bidirectional-capable (it returns the realized rate; only the WRAPPER was shrink-only). The settled stream is the SAME `_settlement_coverage_observations` join (`event_reactor_adapter.py:9452`) over `settlement_outcomes` (authority=VERIFIED). **Data confirmed sufficient this session:** 7009 VERIFIED rows, span 2024-01-01 → 2026-06-13, 5885 distinct city-dates. Per-(city,metric,season) cohorts will mostly clear `MIN_N=30`; thin cohorts hit the cold-start fallback (§1.2).

2. **Fit `σ_center` (the producer fix, Alternative 3a / D3).** New artifact `state/sigma_center_fit.json`, same shape and `candidate=true / OPERATOR_GATED` discipline as `sigma_scale_fit.json`. Fitted as either (A1) the analytic SEM of the fused center = `fused.sd_raw / sqrt(n_members)` IF `fused.sd_raw` can be recovered per-cell (requires the D3 diagnostic: trace WHY `anchor_sigma_c` is pinned at 3.0 — almost certainly the EQUAL_WEIGHT degrade leaving prior τ0=3.0), or (A2) the settlement-fitted `σ_center` = stdev of (fused_center − settled_value) by lead bucket from `settlement_outcomes`. **Lean: A2** (settlement-grounded, no dependence on recovering an unavailable per-cell posterior sd), with A1's diagnostic as a mandatory prerequisite to confirm the 3.0 is a degrade artifact and not a real wide posterior.

3. **Rematerialize** the replacement bundle q_lcb/q_ucb maps with the fitted `σ_center` (offline, into a shadow column / shadow bundle first — NOT the live bundle table). The materializer already supports a clean rebuild (`_build_fused_q_bounds` is pure given inputs).

4. **No live-table rename until proven.** Per operator law (memory: version-suffix elimination + live-table renames operator-gated), the shadow artifacts (`sigma_center_fit.json` candidate, shadow q_lcb column) promote to live ONLY after §4-D2's backtest + the Thrust-6 shadow cohort clears DONE.

No schema migration is required for the calibration seam itself (it reads existing `settlement_outcomes` and writes into the existing `QlcbByDirection` carrier with `source="SETTLEMENT_ISOTONIC"`, an existing vocabulary member — `qlcb_provenance.py:43`).

---

## 3. DEPENDENCY ON OTHER WORKSTREAMS / THRUSTS

- **Depends on Thrust 1 (observability):** the cycle-summary must stop conflating display-EV with the kill-gate (`event_reactor_adapter.py:7149-7206`) BEFORE this lands, or the UP arm's effect is invisible in the log. Hard prerequisite.
- **Depends on Thrust 6 (settlement harness) as the GRADING instrument:** every promotion of this fix (shadow→live) is gated by the event-level walk-forward monitor. This workstream PRODUCES the shadow q_lcb that the harness grades; it cannot self-promote.
- **Composes with Thrust 4 (point-q σ-shape fit, `sigma_scale_fit.json`):** T4 raises the ring-bin POINT q (moving over-assigned tail mass back onto the ring); a higher point q raises the `min(target, q_point)` ceiling in §1.2, letting the UP arm lift the ring q_lcb further. **Order:** T4 and W-QLCB can be fitted in parallel but W-QLCB's UP arm should be validated AGAINST the T4-corrected point q (run D2's backtest on the T4 point where available), because the clamp ceiling depends on it.
- **Independent of Thrust 5 (submit-path):** the submit re-decision is downstream; W-QLCB only changes admission. No coupling.
- **Sequencing guard with Thrust 2 (D6):** do NOT delete the `coverage_unlicensed_tail` antibody's EFFECT (`live_admission.py:141`) until the calibrated q_lcb provably reproduces it in shadow (population A stays rejected). The Milan-24C regression test (§4) moves to the new path.

---

## 4. VERIFICATION GATE (settlement-graded preferred) + THE OPEN DECISIONS

### 4.1 The decisive backtest that MUST run first (P1-D2)

Before ANY q_lcb change ships, run S1's settlement backtest **restricted to the fix-target population**:
```sql
-- join forecast_posteriors (q_point, raw q_lcb) to settlement_outcomes (VERIFIED won/lost)
-- RESTRICT to:  replacement_q_mode = FUSED_NORMAL_FULL  AND  q_point > 0.05
-- (excludes population A structural-zeros; isolates populations B + C)
-- compute per claimed-band:  realized_win_rate  vs  claimed q_lcb  (R/E ratio)
```
- If **R/E ≈ 3–4× under-coverage PERSISTS** on this sub-population → population B is real and large → the bidirectional UP arm (Alternative 3) is correct and primary.
- If R/E **collapses to ~1.0** once population A is excluded → there is no broad suppressed ring alpha (the diagnosis's headline was driven by population A + C), and the honest verdict is law-1: the market is efficient on the ring. The fix then reduces to the producer σ_center repair (so the bound is at least mechanically honest) with NO UP arm, and DONE is a dated "no tradeable ring alpha" verdict.

**This single backtest decides whether the UP arm ships at all.** It is the gate before the gate.

### 4.2 RED-on-revert test(s) (the regression antibodies)

These tests must FAIL if the fix is reverted (proving they bind the new behavior), and must encode the population taxonomy so a future change cannot silently re-crush the ring or re-inflate the tail:

1. **`test_qlcb_up_arm_lifts_ring_band`** — synthetic cohort: claimed q_lcb=0.005 (crushed), realized win-rate in band=0.11 over n=40 → assert calibrated q_lcb rises to ≈0.10 (realized − Jeffreys margin), `source="SETTLEMENT_ISOTONIC"`, `arm="up"`. RED on revert (shrink-only wrapper returns 0.005 unchanged).
2. **`test_qlcb_far_tail_stays_zero`** (population A antibody) — claimed q_lcb=0.0, realized=0.0 over n=72 (the 0/72 cohort) → assert calibrated q_lcb stays ≈0, `capital_efficiency` still rejects. This is the Milan-24C antibody re-homed onto the new path (D6). RED if a future change lets the UP arm fabricate far-tail mass.
3. **`test_qlcb_never_exceeds_point`** (population C + Hidden #2 antibody) — q_point=0.03 (model below a 0.08 market), realized in band=0.20 → assert `min(target, q_point)` clamps calibrated q_lcb ≤ 0.03, so `(q_lcb−price)/price < 0` still rejects. RED if the clamp is dropped (which would manufacture edge on a below-market model).
4. **`test_center_sigma_not_hardcoded_3`** (producer antibody) — assert `_build_fused_q_bounds` is called with a fitted `σ_center`, not the literal `anchor_sigma_c=3.0`, on a FUSED_NORMAL_FULL row. RED on revert to `materializer.py:1766` as-is. (AST/grep antibody: ban `center_sigma_c=float(...anchor_sigma_c)` at the live call site.)
5. **`test_qlcb_bidirectional_down_arm_preserved`** — over-claimed band (claimed 0.30, realized 0.10, n=40) → assert shrink to ≈0.09 still fires (`arm="down"`). RED if the refactor accidentally drops the downward honesty haircut (the original coverage purpose).

### 4.3 The settlement-graded promotion gate (the real verification)

The unit tests prove mechanism; **settlement proves correctness.** The promotion gate (via Thrust 6, event-level walk-forward, de-duplicated to one row per (city,target_date,bin), vs-market benchmark mandatory):
- **Shadow band-verdict:** a (city,metric,season) band's UP-arm correction is trusted only after `MIN_N` settled events (D5) AND model-Brier < market-Brier on those events.
- **Live promotion:** the shadow ring cohort, then live ring fills, clear **>51% after-cost (1¢ fee) settlement win-rate at n≥30 forward fills**, model-Brier < market-Brier. If no band clears after the fee → honest law-1 verdict (market efficient on the ring), stand down on the lane (do NOT loosen `capital_efficiency`).

### 4.4 The open decisions this plan hands to P3 (with leans)

- **D1 — single authority vs two producers.** *Lean: single authority* (the §1.2 bidirectional calibration is the sole q_lcb feeding `capital_efficiency` for both paths). Test: confirm `_replacement_yes_lcb_for_bin:9756` can defer to the calibration seam without losing the `q_mode` live-eligibility semantics (the eligibility gate at `:9895` is separate and stays).
- **D2 — the §4.1 backtest.** Run it before committing T3-vs-T4 primacy. *Lean:* run first; it may obviate the UP arm.
- **D3 — σ_center source.** *Lean: A2 (settlement-fitted), with the A1 diagnostic ("why is anchor_sigma_c pinned at 3.0") as a mandatory prerequisite.* Never keep the 3.0 default; never hand-pick a constant.
- **D4 — Wilson-over-votes fallback (`:9788`).** Once the calibration is the authority, the vote-quantized Wilson-zero has no live job. *Lean: delete as a LIVE authority* (fail-closed NULL → non-live-eligible when fusion is absent), but confirm no shadow-coverage consumer depends on a non-null bound first.
- **D5 — MIN_N (cold-start threshold) + DONE cohort size.** *Lean:* MIN_N≈30 governs the shadow band-verdict (matches the existing coverage `min_n=30`, `settlement_backward_coverage.py:136`); n≥30 forward FILLS governs live promotion. Two different units, both explicit.

---

## 5. RISK + ROLLBACK

| Risk | Detection | Rollback |
|---|---|---|
| UP arm fabricates far-tail alpha (re-enables population A) | `test_qlcb_far_tail_stays_zero` RED; harness shows tail-band realized < claimed | revert calibration to shrink-only `apply_settlement_coverage`; the seam is flag-gated, flip OFF |
| UP arm lifts population C (below-market model) over price | `test_qlcb_never_exceeds_point` RED; shadow shows admissions on bins with q_point<price | the `min(target, q_point)` clamp is the structural guard; if it's bypassed, that's the bug to fix, not the fix to ship |
| σ_center fit is non-stationary (sigma_scale_fit.json's own holdout warning, `_meta.promotion`) | forward-fill validation on settlements the fit did not see; mode-bin ratio out of [0.85,1.15] | keep `candidate=true`; do not promote; live keeps the (honest-but-wide) raw bound |
| isotonic over-fits thin cohorts | MIN_N cold-start fallback (§1.2); harness vs-market gate | cold-start returns raw q_lcb (no calibration) — strictly no worse than today |
| flag interaction: coverage gate already ON live | the change must be byte-identical with the calibration DISABLED (shadow flag OFF) before promotion | new behavior behind a NEW shadow flag (time-boxed, not permanent — operator law); OFF == today |

**Rollback is clean at every layer:** the producer fix is a candidate artifact (not promoted = no effect); the calibration is behind a shadow flag (OFF = byte-identical to today's shrink-only behavior); both are settlement-gated. There is no irreversible live-table change until DONE is proven.

---

## 6. SELF-CHECK — systematically correct, or a 1-order hack?

**Systematically correct.** The test of law-2 ("a fix just to fill one order = FAILURE"): this fix does NOT target filling an order — it targets a **calibration class** (the bidirectional q_lcb input for an entire bin population), gated by **settlement across all bands**, and it is **explicitly allowed to admit ZERO new trades** if the §4.1 backtest shows population B is empty (the law-1 "market is efficient" outcome is a first-class result, §4.3). It manufactures no edge: the UP arm can never raise q_lcb above the model's own point belief (the `min(target, q_point)` clamp), so it only ever lets the model's HONEST belief reach the gate — it corrects a lower-bound *construction artifact*, not the belief (law 8: the point q, the bin selection, the metadata are all untouched). It is a net SIMPLIFY (raw-percentile + shrink-only-coverage → one bidirectional settlement-anchored authority; one wrong constant → one fitted artifact; gate count strictly down). It KEEPS the honest `capital_efficiency` gate (`live_admission.py:113`) exactly as-is. And it is the FIRST authority in the entire stack that can correct a too-LOW bound — closing the architectural blind spot (§0.4) that survived 100 patches because every prior fix could only lower.

The one place it could degrade into a hack is the cold-start fallback: if someone "helps" thin cohorts with a hand-tuned analytic floor, that re-introduces the operator-forbidden constant and the over-confidence the materializer comment already warned against. The plan forbids that explicitly (§1.2): cold-start keeps the raw bound and accrues observations — no fabricated tighter floor, ever.

---

## 7. ONE-PARAGRAPH SUMMARY FOR THE IMPLEMENTATION PLANNER

The live q_lcb on the primary (bundle) path is the 5th percentile of 200 center draws from **N(μ*, 3.0°C)** (`materializer.py:1766`, `anchor_sigma_c=3.0` on all 3464 posteriors) — a center jitter ~6–10× too wide that zeroes the near-center ring bin's lower tail while the honest point q stays at ~0.10. The diagnosis's "q_lcb collapsed to ≈0" is true for population B (center-crush ring) but the b2 audit shows most of the 18,829 `capital_efficiency` rejections are population A (structural far-tail zero — honest, keep) and population C (q_lcb positive but honestly below market — keep). The fix is **Alternative 3 (BOTH)**: (a) replace the `center_sigma_c=3.0` hardcode with a settlement-fitted `σ_center` so the raw bundle q_lcb is honest at birth, and (b) layer a **bidirectional** settlement-isotonic calibration (reusing `_isotonic_realized_rate`, `settlement_backward_coverage.py:97`, whose UP arm is the entire novelty — the current `apply_settlement_coverage` wrapper is shrink-only) as the single q_lcb authority over both paths, clamped to `min(realized−margin, q_point)` so it can never lift a below-market model (population C) or fabricate far-tail mass (population A). It is gated shadow-first, settlement-graded, behind a time-boxed flag; the §4.1 sub-population backtest decides whether the UP arm ships at all; DONE is a forward >51% after-cost ring cohort or a dated "market is efficient" law-1 verdict, never a single fill. Data confirmed sufficient (7009 VERIFIED settlements, 2024-01-01→2026-06-13). Every behavior is reversible to byte-identical-with-today until DONE is proven.

*End of P2 W-QLCB. Read-only planning; no production code or daemon changed. Every empirical claim cited to file:line, artifact, or query+counts.*
