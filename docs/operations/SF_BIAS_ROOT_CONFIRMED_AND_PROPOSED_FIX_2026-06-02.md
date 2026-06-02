# SF Cold-Bias: Confirmed Root + Proposed Fix

Created: 2026-06-02
Last reused/audited: 2026-06-02
Authority basis: GOAL #36 (edge not suppressed); decisive ERA5-anchored probe (agent a4f7e07e12ea10d79); pipeline-calculation trace (wv98m7vtb); Fitz #4 (data provenance).

## 1. What was unresolved, and what the decisive probe settled

Prior traces produced a *map* but deferred the one experiment that breaks the
confound: is SF `effective_bias_c = −4.682°C` a **genuine forecast cold-bias**
or a **station-vs-grid measurement offset**? They imply opposite fixes.

The probe added a third **independent** anchor — ERA5 reanalysis daily-max at the
KSFO settlement cell — for the SF MAM fit dates (n=12 with ERA5):

| comparison | mean (°C) | mean (°F) | SD (°F) |
|---|---|---|---|
| (a) forecast − WU station (KSFO) | **−5.05** | −9.1 | ±2.2 |
| (b) forecast − ERA5 grid | **−7.54** | −13.6 | ±2.8 |
| (c) ERA5 grid − WU station | **+2.49** | +4.5 | ±3.3 |

`(b) + (c) = (a)` closes exactly. Lead-slope r=+0.08 (flat → stable, not artifact).

**VERDICT — GENUINE FORECAST COLD-BIAS.** The ENS model is genuinely cold by
−7.5°C vs ERA5 at the correct cell. KSFO (coastal fog belt) separately reads
+4.5°F *cooler* than the grid — a real station effect, but in the **opposite**
direction, so it **masks** part of the true forecast error. Net forecast−station
= −5.05°C ≈ the stored −4.682. Because markets settle on the WU station, the
**−4.682 is the correct de-bias target for WU-settling SF markets** (if anything
it understates the model error). This is not a phantom; it is real.

## 2. The actual defect: incoherent asymmetry (double alpha loss)

Confirmed live state:
- `edli_bias_correction_enabled = false` → live SF **p_raw runs UNCORRECTED**
  against a genuinely-cold forecast → SF q is systematically cold-biased →
  warm bins under-priced, cold bins over-priced → **mispriced SF candidates**.
- `bias_decay_kelly_haircut_enabled = true` → `_maybe_bias_decay_kelly_haircut`
  (event_reactor_adapter.py:~3483) reads the SAME `effective_bias_c` independent
  of the correction flag: |−4.682×1.8| = 8.4°F > thr 3.0°F → **kelly × 0.5**.
  **SF live sizing is halved.**

So the system is in the **worst-of-both** state: it trades on a **biased q**
AND **halves the size** on account of that bias. It penalizes the position
twice and corrects the probability zero times. Against GOAL #36 this is exactly
edge suppression: a real, correctable signal is left in q while size is cut.

The coherent states are only two:
- (i) **Correct q + full size** (trust the bias → fix the probability, drop the haircut), or
- (ii) **Don't correct q + don't halve** (distrust the bias → act on it nowhere).

The current state is neither.

## 3. Provenance is directionally validated but not yet audit-grade (Fitz #4)

The ERA5 probe validates the *direction and rough magnitude*. The *estimate's
provenance* remains weak:
- **Source** = `/tmp/canonical_bias_rows.json` (37 KB, 2026-05-31). Schema has
  **no `authority` field, no `unit` field**; all SF rows `sst='WU'`.
- **Circular estimator**: train_source = settlement_source = WU_KSFO,
  `weight_live=1.0`, `n_prior=0`. `eff = mean(forecast − observed)` over the same
  authority the market settles on → "corrected ≈ settled" is arithmetic identity,
  not forward validation.
- **May copied to June**: month=5 and month=6 rows identical (−4.682, n_live=15),
  no June refit.
- **Nowcast-heavy**: 12/15 rows `lead_h=0.0` → the "bias" is dominated by
  near-zero-lead error, not the lead at which we actually trade.
- **`settlement_outcomes` table is EMPTY** → no independent settle-graded truth
  exists yet; forward OOS validation is currently impossible to run.

So: the bias is **real** (ERA5-confirmed) but the **−4.682 number** is a weak,
circular, cross-season, nowcast-weighted point estimate. Good enough to prove a
problem exists; not yet audit-grade enough to silently drive live q.

## 4. Proposed fix (layered)

### 4a. ANTIBODY — safe to land now, no live-risk change (Fitz #4 "make the category impossible")
Type the bias by **authority**, and gate **both** consumers (p_raw correction
*and* Kelly haircut) on the **same** authority decision, so the incoherent
asymmetry of §2 becomes unconstructable:
- Compute a real `authority` for each `model_bias_ens` row instead of hard-coding
  `'VERIFIED'`: a row is `VERIFIED` only if settlement_source is independent of
  train_source AND season-matched (not copied) AND unit present AND
  (n_prior>0 OR n_paired ≥ floor at the traded lead, excluding lead_h=0 nowcasts).
  The current SF rows reclassify to **`PROVISIONAL`**.
- `_maybe_apply_edli_bias_correction` may consume **only `VERIFIED`** rows (it is
  already fail-closed/OFF — this makes "correct q on /tmp-circular data" impossible
  even when the flag flips).
- `_maybe_bias_decay_kelly_haircut` on a `PROVISIONAL` row routes to the existing
  **conservative** path (same 0.5 it already applies) → **SF sizing unchanged** →
  zero live-risk delta, but the system stops *labelling* unverified data VERIFIED
  and stops being able to correct q from it.
- **Relationship test (RED now)**: a bias row whose settlement provenance is not
  independent / is cross-season-copied / has no unit MUST report
  `authority != 'VERIFIED'` AND MUST be ineligible to correct p_raw. RED today
  (rows claim VERIFIED + are correction-eligible), GREEN after the gate.

### 4b. ALPHA RECOVERY — operator-gated (changes live q), prerequisite-bound
The genuine fix that recovers SF edge is to **correct q**, not halve size:
- Re-fit the bias **on June/JJA data at the traded lead**, excluding lead_h=0
  nowcasts, sourced from an authority-tagged store (not /tmp). Blocked today:
  `settlement_outcomes` empty + no June SF settlement exists yet
  (wu_icao max 2026-04-19). So a clean refit cannot run this moment.
- Once a `VERIFIED` (season-matched, lead-matched, independent-source) bias
  exists, **enable `edli_bias_correction_enabled` for those cities** (de-bias q,
  train/serve lockstep already wired via `payload['_edli_bias_corrected']` →
  identity Platt) and **retire the redundant haircut** for VERIFIED-bias cities.
  This is the move that recovers the suppressed SF edge. It changes live q →
  **operator gate**.

## 5. Bottom line for the operator
- SF forecast is **genuinely cold** (ERA5-confirmed −7.5°C grid error; −5°C vs
  the settlement station). Not a station artifact. The correction direction is right.
- Live system is in the **worst-of-both** posture: biased q + halved size.
- I can land **4a (antibody, zero live-risk)** now and verify RED→GREEN.
- **4b (enable correction → recover edge)** is the alpha move but needs (1) an
  authority-grade June/lead-matched refit (blocked on settlement data) and (2)
  your gate to change live q.
