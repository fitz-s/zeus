# P1 / S0 — FOUNDATION & BIN-SELECTION STRATEGY (law-8 root lens)

**Date:** 2026-06-14
**Lens:** FOUNDATION / METADATA & BIN-SELECTION (operator law 8 — the ROOT lens).
**Mode:** read-only adjudication + design. No production code edited. DBs opened `?mode=ro`.
**Owns:** the #1 headline constraint — "q_lcb collapses to ≈0 on cheap bins → ~88% capital_efficiency rejection."
**Authority inputs read in full:** `docs/evidence/investigation_2026-06-13/diagnosis_confirmation.md` (authoritative), `b2_capital_efficiency_audit.md`, `synthesis.md` (mined for keep-invariants only), `docs/authority/replacement_final_form_2026_06_09.md` §1d–§1e, and source: `src/data/replacement_forecast_materializer.py`, `src/engine/event_reactor_adapter.py`, `src/strategy/live_inference/live_admission.py`, `src/strategy/probability_uncertainty.py`, `src/strategy/selection_shrinkage.py`, `src/strategy/market_fusion.py`.

---

## 0. ONE-PARAGRAPH VERDICT (the headline, re-derived from settlement)

The diagnosis is correct that **q_lcb≈0 driving `capital_efficiency_lcb_ev` is the binding admission constraint**, but its framing ("cheap bins") conflates two physically different populations that settlement separates cleanly. I traced **270 VERIFIED settled markets end-to-end** (winning bin → live posterior q and q_lcb). The result: **q_lcb on the winning bin is NOT generically crushed** — 96/116 matched winners (83 %) carry a q_lcb that survives (≥0.03). The crush is concentrated in two classes: **(1) far-OTM bins with zero ensemble support — q_lcb=0 is HONEST (B2 is right; do not lift),** and **(2) NEAR-MODE RING BINS that actually won (SF 90-91°F q=0.08, Seattle 74-75°F q=0.14, Beijing 32°C q=0.12, Denver 92-93°F q=0.15) whose q_lcb was crushed to 0.004–0.030.** Class 2 is the suppressed alpha. Its cause is **mechanical, not epistemic**: the fused-center q_lcb bootstrap jitters the center by `anchor_sigma_c = 3.0 °C` — a hardcoded soft-anchor prior, identical on all 1791 fused posteriors — whereas the settlement-measured fused-center error is ~0.85–1.31 °C. A 3 °C center wobble slides the whole predictive Normal off any ring bin, so the bin's 5th-percentile mass collapses to ≈0 **while its point mass barely moves**. This is a calibration defect that crushes real, settlement-backed ring-bin probability. **Fix the center-uncertainty input to its fitted/settlement-measured value; keep the honest zero on genuinely unsupported far bins.**

---

## 1. THE MECHANISM, DECOMPOSED TO file:line

### 1.1 The admission gate is honest and must be KEPT

`src/strategy/live_inference/live_admission.py:87-119` `live_capital_efficiency_rejection_reason`:
```
conservative_ev_per_dollar = (q_lcb - price) / price
reject iff conservative_ev_per_dollar <= 0.0
```
This is a single honest inequality: *does the conservative win-probability beat the all-in price?* No cap, no throttle, no fill-prob term. **KEEP it verbatim** (operator law: the gate is the honest q_lcb>price test; the defect is the INPUT q_lcb, never the comparison). Every fix below targets the q_lcb that flows INTO this line, never the line.

### 1.2 Where the live q_lcb comes from — the twin authority

There are two q_lcb producers, and the live reactor reads them in priority order at `event_reactor_adapter.py:9756-9788` `_replacement_yes_lcb_for_bin`:

1. **Bundle map first** (`:9776-9778`): the materialized per-bin `q_lcb_json`, clamped to `[0, q_yes]`. Its basis is one of:
   - `fused_center_bootstrap_p05` — the certified, **live-eligible** bound (200-draw center-uncertainty bootstrap, built at `replacement_forecast_materializer.py:1359-1441` `_build_fused_q_bounds`).
   - `wilson_aifs_member_votes` — the soft-anchor fallback (`replacement_forecast_materializer.py:1236-1299`), structurally **NOT live-eligible** (q_mode `CAPTURE_MISSING`).
2. **Wilson-over-AIFS-votes recompute** (`:9781-9788`): when the bundle has no map for the bin, recompute a one-sided Wilson lower bound on `aifs_prob(bin) * 51`. **A bin absent from the AIFS vote map returns `0.0` (`:9788`).**

The reactor brands whatever it returns as `source="FORECAST_BOOTSTRAP"` (`:9974-9978`) regardless of true basis — which is why receipts say `FORECAST_BOOTSTRAP` even when the underlying bound is fused or Wilson. This branding is a provenance smell but not the defect.

### 1.3 The fused-center bootstrap — the exact crush site

`_build_fused_q_bounds` (`replacement_forecast_materializer.py:1359-1441`):
```
mu_draws = rng.normal(loc=mu_star, scale=center_sigma_c, size=200)   # :1399
z_low/z_high = (bin_bounds - mu_draws)/predictive_sigma_c            # :1421-1422
probs = ndtr(z_high) - ndtr(z_low)                                   # (200 draws × M bins)
q_lcb[bin] = 5th percentile of probs over draws                      # :1425
```
`center_sigma_c` is passed as `bayes_precision_fusion_override.anchor_sigma_c` (`:1766`), set to `float(fused.sd)` at `:1133`. **Empirically, every one of the 1791 fused posteriors carries `anchor_sigma_c = 3.0` exactly** — i.e. `fused.sd` is collapsing to (or being overwritten by) the soft-anchor prior default `anchor_sigma_c: float = 3.00` (`:125`), not a per-cell fitted posterior sd. The `predictive_sigma_c` is separately floored at 1.0 °C (`:1119`) and is the spread used for the q POINT.

### 1.4 The mathematics of the crush (simulation, reproducible)

For a ring bin centred +2 °C from the mode, `predictive_sigma=1.3 °C`, 200 draws, seeded `_QLCB_SEED`:

| `center_sigma_c` | q_point | q_lcb | q_lcb/q_point |
|---|---|---|---|
| **3.0 (LIVE)** | 0.101 | **0.0000** | 0.00 |
| 2.0 | 0.114 | 0.0001 | 0.00 |
| 1.3 | 0.123 | 0.0025 | 0.02 |
| **1.0 (≈ settled MAE)** | 0.111 | **0.0050** | 0.05 |
| 0.7 | 0.107 | 0.0182 | 0.17 |
| 0.4 | 0.100 | **0.0356** | 0.36 |

The point mass is **flat** across the whole column (0.10–0.12); only the q_lcb moves, and it moves from 0 to 0.036. **The ring-bin LCB is almost entirely a function of `center_sigma_c`, not of the model's belief.** At the live 3.0 °C it is structurally 0 (reject); at the settlement-honest ~1 °C it is 0.005; at a tight 0.4 °C it clears a 0.03 price. *This single parameter is the lever between "no fills" and "ring-bin alpha."*

### 1.5 Settlement evidence that the ring-bin mass is REAL (not honest-zero)

Of 270 settled winners traced (`/tmp/q_winbin.py`, `/tmp/fused_crush.py`):
- **20 % (54/270)** of winning bins had **≈0 AIFS member votes** (`votes=0.05` = the smoothing pseudo-count only) — e.g. SF 90-91°F (q_point 0.08), Miami 88-89°F (q_point 0.32!), Madrid 32°C (q_point 0.23). The 51-member vote histogram **could not see** these winners; the analytic Normal could. Wilson-LCB = 0.0000 on every one.
- **49 % (106/216)** of vote-bearing winners had Wilson-LCB < q_point/2.
- Yet the model's POINT q put 8–32 % mass on these winning bins, and `sigma_scale_fit.json`'s own calibration table shows the ring (dist=1,2) bins are **well-calibrated at the analytic shape** (`ratio_realized_over_expected` 1.0–1.2 for dist 0/1/2 at `calibration_at_fit`). The analytic shape is the honest authority; the 51-member vote-quantized Wilson floor is a lossy discretization of it.

This is the law-8 / law-1 answer: **for near-mode ring bins, q_lcb≈0 is a BROKEN bound (over-wide center jitter + vote quantization), not an honest belief.** For far-OTM zero-support bins it is honest. Settlement tells them apart.

### 1.6 The second, independent lock (must be fixed in the same breath)

`event_reactor_adapter.py:9345-9372` `_replacement_q_mode_live_eligibility`: live submission requires `replacement_q_mode ∈ {FUSED_NORMAL_FULL, FUSED_NORMAL_PARTIAL}`. The latest-cycle posteriors frequently fall back to `CAPTURE_MISSING` soft-anchor when the multi-model single_runs for the freshest cycle have not all landed (at cycle `2026-06-14T00:00`, only 3 cities had the decorrelated providers vs ~50 at the full `06-13T00:00`). So even a ring bin whose fused q_lcb WOULD clear price is blocked at the q_mode gate because that cell reverted to soft-anchor. **This is a coverage/timing problem, not a belief problem, but it gates the same trades.** The basis-by-date table proves it is real and intermittent:

| computed_at | fused | wilson | null |
|---|---|---|---|
| 06-14 | 105 | 51 | 0 |
| 06-13 | 552 | 171 | 0 |
| 06-12 | 503 | 0 | 141 |
| 06-09 | 2 | 0 | 555 |

Fused is the majority on recent days but ~25–30 % of cells still revert to soft-anchor (non-live-eligible) on the freshest cycle.

---

## 2. THE OBJECTIVE (what "fixed" means here)

Make the **correct (settlement-winning) ring bin carry an HONEST, non-degenerate q_lcb** so that when the market genuinely under-prices it, `(q_lcb − price)/price > 0` fires and a real correct-bin trade is admitted — **and prove it by settlement** (>51 % after-cost win-rate on the bins this unlocks). Simultaneously, **preserve the honest zero on genuinely unsupported far bins** (do not manufacture far-tail alpha). The deliverable is not "a fill"; it is the calibration foundation under which admitted ring-bin trades settle in our favour.

---

## 3. DESIGN — three decisions, each weighed

### DECISION A — fix the center-uncertainty input to the q_lcb bootstrap (THE primary lever)

**Problem:** `center_sigma_c = anchor_sigma_c = 3.0 °C` (hardcoded soft-anchor prior) is 2–3× the settlement-measured fused-center error (~0.85–1.31 °C, the materializer's own `_sigma_resid` evidence at `:1101`). It crushes ring-bin q_lcb to 0.

**Alternatives weighed:**

- **A1 — Set `center_sigma_c` to the FITTED fused posterior sd `fused.sd`, and make `fuse_bayes_precision_posterior` actually emit a per-cell `sd` instead of collapsing to 3.0.** Root-cause why `fused.sd==3.0` universally (is the fusion degrading to the EQUAL_WEIGHT prior because `n_train < MIN_TRAIN`, leaving the prior τ0 as the posterior sd?). *Pro:* attacks the true seam; the center uncertainty becomes the genuine Bayesian posterior width, per-cell, fitted — fully law-compliant (no hand-set constant). *Con:* requires tracing the fusion's degrade path; if `n_train` is structurally thin the fitted sd may itself be unreliable.
- **A2 — Replace the center-jitter sigma with a settlement-fitted center-error scale `σ_center_fit` (a small MLE/robust fit of |fused_center − settled_value| by lead bucket), analogous to the existing `sigma_scale_fit.json` σ-shape fit.** *Pro:* directly settlement-grounded, the honest measured quantity, fitted not picked (law-compliant); same artifact pattern the team already trusts; decouples the q_lcb center-width from the soft-anchor prior entirely. *Con:* a new fitted artifact + consumer wiring; must be operator-gated like the σ-shape fit; non-stationary on 5 days (same caveat the σ fit carries).
- **A3 — Drop center-jitter entirely; build q_lcb as a one-sided lower bound of the predictive Normal at the q POINT (`predictive_sigma`) using a fixed conservative tail, no center bootstrap.** *Pro:* simplest, removes the fragile bootstrap. *Con:* throws away the genuine center uncertainty (over-confident on bins where the center IS uncertain — the Milan two-measures incident the bootstrap was built to prevent); changes the bound's meaning; likely under-covers far bins.

**PICK: A2 as the primary, with A1 as the diagnostic that must run first.**
Run A1's trace to confirm WHY `fused.sd` is pinned at 3.0 (almost certainly `EQUAL_WEIGHT` degrade leaving prior τ0=3.0 as the posterior sd — `fuse_bayes_precision_posterior` reaching `T2_BAYES` only at `n_train>=MIN_TRAIN`). If the fused posterior sd is genuinely unavailable per-cell (thin history), do NOT keep 3.0 — replace the q_lcb center-width with **A2's settlement-fitted `σ_center_fit`** (the measured fused-center error by lead bucket), exactly the way `sigma_scale_fit.json` fits the σ-shape. This is the law-compliant move: the center-uncertainty becomes a fitted, settlement-measured quantity, never a hand-picked or prior-default constant. Operator-gate the artifact (candidate → promote on forward-fill validation) identically to the σ-shape fit.

**Invariant preserved:** `q_lcb ≤ q_point` per bin (already clamped at `:1437`); the center-width only LOWERS the bound from the point — a tighter (honest) center-width raises q_lcb toward the point but can never exceed it. Far bins still collapse to ~0 because their point mass is ~0 regardless of center-width (the Normal tail is thin) — **honest zero is structurally preserved.**

### DECISION B — the AIFS-vote Wilson floor must never be the LIVE q_lcb authority

**Problem:** the Wilson-over-51-votes path (`_replacement_yes_lcb_for_bin:9781-9788`) returns 0.0 for any bin with zero votes, and a coarse, quantized bound otherwise. A 51-member ensemble cannot resolve per-bin probability below ~1/51 ≈ 0.02, so it structurally zeroes ring bins the analytic Normal supports. It is a *fallback*, not an authority.

**Alternatives weighed:**

- **B1 — Keep Wilson only as the explicit `CAPTURE_MISSING` shadow bound (status quo intent), and guarantee the live path NEVER reads it for a live-eligible decision.** The q_mode gate already enforces this in principle (soft-anchor = non-live-eligible). *Pro:* no new code; matches existing design. *Con:* the bundle-map-first read at `:9776` can still return a Wilson bundle value if a fused posterior is missing for the bin but a Wilson one exists — verify the priority is airtight.
- **B2 — Delete the Wilson member-vote q_lcb path entirely; make the analytic fused-center bootstrap the SOLE q_lcb producer; when fusion is unavailable, the cell is simply not live-eligible (no degraded bound at all).** *Pro:* collapses the twin authority to one (operator's 大一统 law); kills the vote-quantization category permanently. *Con:* removes the shadow coverage measurement the Wilson bound currently provides; must confirm nothing downstream depends on a non-null bound for shadow accrual.

**PICK: B2, gated behind A being done first.** Once A makes the fused bootstrap the honest per-cell authority, the Wilson member-vote bound has no remaining job except shadow coverage — and shadow coverage can read the fused bound too. Collapsing to ONE q_lcb authority (the analytic fused-center bootstrap with the fitted center-width) is the operator's single-authority law and eliminates the vote-quantization crush as a *category*. Keep a NULL-bound = non-live-eligible cell (fail-closed) rather than a degraded vote bound — never size live Kelly on the quantized floor.

### DECISION C — close the freshest-cycle fused-coverage gap (the q_mode lock)

**Problem:** ~25–30 % of freshest-cycle cells revert to `CAPTURE_MISSING` soft-anchor because the decorrelated multi-model single_runs for the latest cycle (`06-14T00:00`) have not all landed when the posterior is built; the live reactor reads the freshest cycle, which is the least-populated. The bin belief is fine — the cell is just non-live-eligible by timing.

**Alternatives weighed:**

- **C1 — Substitute the previous fully-populated cycle's fused posterior when the freshest cycle is multi-model-incomplete (freshest-COMPLETE rather than freshest-cycle for fusion inputs).** *Pro:* the fused belief is barely-staler but FULL; aligns with the existing "previous-run substitution / freshest-row-per-key" pattern (task #40). *Con:* a slightly older center; must fail-closed on no-leak (target_date < decision).
- **C2 — Build the fused posterior only after a completeness check (all decorrelated providers present for the cycle), and let the live reader prefer the latest COMPLETE fused posterior over a fresher soft-anchor one.** *Pro:* the q_mode gate then sees fused on every cell that has any complete cycle; clean. *Con:* adds a read-time "latest complete fused" selection.
- **C3 — Do nothing; accept that only fully-captured cells trade.** *Pro:* zero work, fully honest. *Con:* leaves ~30 % of cells dark for a timing reason, suppressing real ring-bin alpha that A+B would otherwise unlock — violates law-1 (the suppression is OUR defect).

**PICK: C2 (prefer the latest COMPLETE fused posterior at read time), with C1's substitution as the materializer-side dual.** The decision authority should read the **latest fused (live-eligible) posterior**, not the latest posterior-of-any-kind; a fresher soft-anchor row must not shadow a slightly-older fused one for the same cell. This is a read-semantics fix (single-authority "tradeable-latest" already exists for books — extend the same principle to posterior selection). Strictly no-leak: only cycles with `target_date < decision_time`.

---

## 4. THE CAUSAL CHAIN TO A SETTLEMENT-PROVEN FILL

1. **A** makes `center_sigma_c` the fitted/settlement-measured fused-center error (~1 °C) instead of the 3.0 °C prior → ring-bin q_lcb rises from 0.000 to ~0.02–0.04 where the model honestly supports the ring (simulation §1.4).
2. **B** removes the vote-quantized Wilson floor as a live authority → the analytic bound (which "saw" SF/Miami/Beijing winners the vote histogram missed) is the sole q_lcb.
3. **C** ensures the cell is `FUSED_NORMAL_FULL/PARTIAL` (live-eligible) by reading the latest COMPLETE fused posterior → the q_mode lock opens.
4. With an honest ring-bin q_lcb ~0.03 vs a market price ~0.02 on a genuinely under-priced ring bin, `(q_lcb − price)/price > 0` → **`capital_efficiency` ADMITS** → proof_accepted=1.
5. Direction law, the buy_no native-NO authority, and fractional-Kelly sizing are downstream RELAYS (law 8) — they re-compute on the now-honest q_lcb; none manufactures or needs to manufacture edge.
6. The submit lane (B1 latch open per diagnosis §GAP5) executes → fill.
7. **Settlement adjudicates:** the unlocked ring bins must settle >51 % after-cost. The `sigma_scale_fit` calibration table predicts dist-1/2 ring bins are well-calibrated at the analytic shape (ratio≈1.0–1.2), so the prior is that they DO settle in our favour — but **settlement, not this plan, is the only proof** (law 5). If they do not, A's center-width is still too tight and must widen (the loop closes on settlement, not on a fill).

---

## 5. WHAT TO KEEP / WHAT TO DELETE

**KEEP (do not touch):**
- `live_capital_efficiency_rejection_reason` — the honest q_lcb>price gate (operator law).
- The honest **zero q_lcb on far-OTM zero-support bins** (B2 audit is correct; do not manufacture far-tail alpha — law 4).
- `ProbabilityUncertainty` invariants (`q_lcb ≤ q_point`, penalties only lower the bound) — `probability_uncertainty.py:218-238`.
- The catch-all open-ended-bin coherence cap (`replacement_forecast_materializer.py:1608-1751`) — the Paris >=26 inflation antibody.
- The B1 submit latch absorber (self-cleared, self-heals).
- The settlement-backward coverage license vocabulary (`live_admission.py:40`) — it stays the cert authority.

**DELETE / COLLAPSE:**
- The hardcoded `center_sigma_c = 3.0` default as the q_lcb bootstrap input (DECISION A) — replace with fitted/settlement-measured center-width.
- The Wilson-over-AIFS-votes path as a LIVE q_lcb authority (DECISION B2) — collapse the twin authority to the single analytic fused-center bound; keep Wilson only as a non-live shadow if anything still needs it, else delete.
- The freshest-cycle-wins posterior read when it returns a non-live-eligible soft-anchor over an available fused cell (DECISION C) — read latest-COMPLETE-fused.

**Net gate count:** this REMOVES a producer (Wilson live authority) and a constant (3.0), adds ZERO gates/caps/throttles. It is a SIMPLIFY, consistent with the K-cut law.

---

## 6. FAILURE MODES + THE VERIFICATION THAT CATCHES EACH

| # | Failure mode | Catch / verification |
|---|---|---|
| F1 | A's tighter center-width over-confidently admits far-OTM bins (manufactured tail alpha). | **Settlement replay** on the dist=tail bins: tail `ratio_realized_over_expected` is ~0.14–0.30 in `sigma_scale_fit` (model over-assigns tails). Assert post-fix far-bin q_lcb stays ~0 (point mass thin → bound thin regardless of center-width). Add a settled-tail win-rate check; if tail admits rise, center-width is too tight. |
| F2 | `fused.sd` is genuinely ~3.0 (the fusion really is that uncertain) and tightening it is dishonest. | A1 trace MUST confirm the degrade path first. Cross-check against the **measured** \|fused_center − settled\| distribution by lead bucket; only tighten to the EMPIRICAL error, never below it. The settlement-fitted σ_center is the ceiling on how tight we may go. |
| F3 | Collapsing to the single analytic authority (B2) under-covers a cell where fusion legitimately failed → no bound → trade blocked that should trade. | Fail-CLOSED is correct here (no bound = non-live). C2 then supplies a complete-cycle fused posterior so the cell is covered without a degraded bound. Monitor the fused-coverage rate by cycle. |
| F4 | C2's latest-COMPLETE-fused substitution leaks future data. | Hard no-leak assertion: substituted cycle `source_cycle_time` whose target_date < decision_time only; reuse the existing INV-37 / IRON-RULE-#3 no-leak guard. |
| F5 | The unlocked ring-bin trades settle BELOW 51 % (the edge was base-rate / illusory). | The ONLY real test (law 5). Shadow-first: run A+B+C in shadow, accrue the would-be ring-bin admissions, grade against settlement. Promote to live ONLY if the shadow ring-bin cohort clears 51 % after-cost. This is the gate between "more fills" and "proven alpha." |
| F6 | Provenance smell: reactor brands every bound `FORECAST_BOOTSTRAP` (`:9974`) regardless of true basis, hiding which authority fired. | Stamp the TRUE basis (`fused_center_bootstrap_p05` vs the fitted-center variant) onto the receipt so post-hoc settlement grading can attribute wins to the correct authority. Cheap, non-behavioral. |

---

## 7. WHERE THIS LENS HANDS OFF

- **Metadata / bin-identity (the other half of law 8):** I confirmed the q_json↔settlement bin mapping is sound (text-match on the question string resolves the winning bin cleanly in 270/270 attempted; preimage rounding rule is per-city and enforced at `_family_rounding_rule:1341`). Bin IDENTITY is not the defect here; bin-belief WIDTH is. A sibling lens owning city/station/date/boundary preimage should still independently confirm no HK-truncate / boundary-shift mis-mapping on the specific ring bins this unlocks.
- **Selection/sizing lens:** `selection_shrinkage.py` (EB shrink, lfsr, log-utility license) and horse-race Kelly are downstream relays — they re-compute on the corrected q_lcb. No change needed there for this fix; flag only that `select_license`'s `pi_min=0.90` posterior-probability bar interacts with a tighter q_lcb (more candidates clear it) and must be settlement-validated, not loosened.

---

## 8. APPENDIX — evidence index (every claim is reproducible)

- q_lcb basis by date (fused vs wilson): `sqlite3 zeus-forecasts.db` group-by computed_at → §1.6 table.
- `anchor_sigma_c=3.0` on all 1791 fused posteriors: `json_extract(provenance_json,'$.anchor_sigma_c')` group-by → single value 3.0.
- 270-settled winning-bin q/q_lcb trace: `/tmp/q_winbin.py`, `/tmp/votezero.py`, `/tmp/fused_crush.py` (re-runnable read-only).
- center_sigma → q_lcb sensitivity simulation: §1.4 (seeded `_QLCB_SEED`, 200 draws).
- 54/270 winners with ≈0 AIFS votes (vote-quantization crush): `/tmp/votezero.py` STATS.
- B2 audit concordance/divergence: B2 §4 (far-OTM honest-zero CONFIRMED) vs this doc §1.5 (near-mode ring crush — B2's blind spot, the alpha).
- Live admission gate: `live_admission.py:87-119`. q_mode gate: `event_reactor_adapter.py:9345-9372`. Live q_lcb authority: `:9756-9788`. Fused bootstrap: `replacement_forecast_materializer.py:1359-1441`, center input `:1133/:1766`, prior default `:125`.

*End P1/S0 foundation strategy. Read-only; no production code or daemon changed.*
