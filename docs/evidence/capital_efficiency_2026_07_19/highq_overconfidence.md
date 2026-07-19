# High-q (0.80–0.95) Overconfidence — Proof, Localization, and Fix Spec

Investigator: read-only agent, 2026-07-19. All DB access via read-only sqlite3
connections (`file:...?mode=ro`) against `state/zeus-forecasts.db`,
`state/zeus_trades.db`, `state/zeus-world.db`. No writes, no code edits, no
market-price backtests. Scripts and intermediate data at
`/private/tmp/claude-501/-Users-leofitz-zeus/7589dc75-d443-4b7f-8e2b-24945ef3038c/scratchpad/highq_part{1,2,3}_*.py`
and `receipts_graded.json` / `receipts_deduped.json` / `entered_localized.json`
in the same directory.

## Verdict up front

**Confirmed, not refuted.** The 0.80–0.95 served-q band is systematically
overconfident, cross-validated on two independent samples (entered positions,
n=114/264; rejected no-submit candidates, n≈90-140 of 477 deduped markets).
**Localization: PRIMARILY CENTER BIAS** — the fused posterior mean `mu*`
(`anchor_value_c` in `forecast_posteriors.provenance_json`) runs measurably
toward the climatological middle relative to the true daily extremum (cold for
`high`, warm for `low`), not sigma underdispersion (empirical PIT
standard-deviation ≈ 1.0–1.1, close to nominal) and not a full_transport_v1
tail-transform artifact (that path is retired on the live source-clock route
per `docs/authority/regime_unification_2026-06-12.md` U1 — the entered
positions analyzed here are served by `openmeteo_ecmwf_ifs9_bayes_fusion` /
`_aifs_sampled_2t_soft_anchor`, not full_transport_v1). **The fix already
exists in the codebase** (`src/calibration/settlement_coverage_hierarchy.py`,
the F1 walk-forward hierarchical coverage calibrator, wired to the money-path
choke point in commit `56ae8cf16`, 2026-07-04) but is flag-gated OFF
(`feature_flags.settlement_coverage_hierarchy_enabled`, absent from
`config/settings.json` → defaults `False`) — **and even if armed today it
would not fix this band**, because its only data source
(`edli_no_submit_receipts`) has been silently empty since 2026-06-28 and never
carries `strategy_key`, which structurally blocks every pooling level above
the (nearly unreachable) exact-cell shield. See §5 for the precise fix.

---

## 1. Reliability curve, walk-forward, two independent samples

### 1a. Entered positions (n=264 settled/economically_closed, all-time
May–Jul; reused from `pnl_attribution.md` §7, re-verified against
`settlement_outcomes` directly for §2 below)

| q bucket | n | avg q | realized win freq | PnL |
|---|---|---|---|---|
| 0.70–0.80 | 37 | 0.762 | 56.8% | +$77.03 |
| **0.80–0.90** | **99** | **0.846** | **64.6%** | **−$20.69** |
| **0.90–0.95** | **15** | **0.926** | **53.3%** | **−$16.65** |
| 0.95–1.01 | 32 | 0.994 | 75.0% | +$155.00 |

Combined 0.80–0.95 band (114 entered positions with a resolved outcome,
n differs slightly from the 264-row union above because a few rows lack
`resolved_exit_price`): avg claimed q=0.856, **realized win freq = 63.2%**,
avg entry price = 0.617, total realized PnL = **−$37.34**, total capital
staked = **$932.56**.

- **Claimed edge** (q − price) = **+23.9pp**. **Realized edge** (empirical win
  freq − price) = **+1.4pp** — statistically indistinguishable from zero at
  n=114. The Kelly sizing engine deployed $932.56 against an edge that,
  honestly measured, is roughly 1/17th of what was claimed.
- Split by lane: **Day0 n=83, avg_q=0.858, realized=60.2%, PnL=−$37.93** vs
  **non-Day0 n=31, avg_q=0.853, realized=71.0%, PnL=+$0.59**. The entire net
  loss in this band is a Day0-lane phenomenon; non-Day0 in the same q-band is
  roughly breakeven with *better* realized calibration.
- Split by month (entered_at): 2026-05 n=3 (33.3% realized, too thin),
  2026-06 n=61 (67.2% realized, −$35.89), 2026-07 n=47 (59.6% realized,
  +$2.65 — net positive only because two large July winners offset the same
  underlying miscalibration; win *rate* did not improve).
- Split by unit: unit=C n=109 (63.3% realized, −$36.70) carries essentially
  all of it; unit=F n=5 is too thin to read (−$0.64).
- Direction: 107/114 of the band is `buy_no` (only 7 `buy_yes`) — this is
  overwhelmingly a `buy_no` phenomenon by volume.

### 1b. Rejected (no-submit) candidates, `edli_no_submit_receipts` (world.db,
62,944 raw rows, 2026-05-31 through **2026-06-29 only** — see §5 staleness
finding)

The raw per-receipt table is **not** 62,944 independent trials: the reactor
re-emits a no-submit receipt for the same candidate every cycle while a market
stays open (AGENTS.md §0, "re-decision is a first-class lane"), so a single
market can contribute hundreds of near-duplicate rows. Deduping to one
observation per `(condition_id, direction)` (last-decision-time claim, mirroring
production's own `dedupe_observations`) collapses 61,908 graded raw receipts to
**477 distinct market/direction claims** — this is the only honest sample size.
On the raw (undeduped) data the bucket win rates swing wildly (e.g., a single
487-receipt Shenzhen market dominates one bucket) — that artifact is reported
here explicitly so it is not mistaken for a finding.

Deduped reliability curve (won graded via the production D1 keystone
`src.contracts.graded_receipt.grade_receipt` — Direction Law, unit-checked,
BinKind-aware — imported and called directly, not reimplemented):

| q bucket | n | avg q | realized win freq | gap |
|---|---|---|---|---|
| 0.50–0.60 | 14 | 0.552 | 7.1% | +48pp |
| 0.60–0.70 | 16 | 0.658 | 12.5% | +53pp |
| 0.70–0.80 | 17 | 0.75 (blended) | ~29% | +45pp |
| **0.80–0.85** | **33** | **0.835** | **69.7%** | **+13.8pp** |
| **0.85–0.90** | **53** | **0.879** | **71.7%** | **+16.2pp** |
| **0.90–0.95** | **40** | **0.924** | **60.0%** | **+32.4pp** |
| 0.95–1.01 | 246 | 0.994 | 80.1% | +19.3pp |

This is an **independent, adverse-selected sample** (candidates the reactor
specifically chose *not* to trade — different selection mechanism than entered
positions) and it corroborates the same 13–32 percentage-point overconfidence
gap in exactly the 0.80–0.95 range, plus a similar residual ~19pp gap even at
q≈0.99. Two structurally different subsets (entered vs. rejected) show the
same signature — this is not an artifact of Kelly-driven entry selection.

---

## 2. Localization: center bias vs. sigma underdispersion vs. tail-transform

Method: for 163 of the 264 entered positions (settled phase only, real
`p_posterior`>0), joined each to the `forecast_posteriors` row live at entry
(latest `computed_at ≤ entered_at` for that `city, target_date,
temperature_metric`), pulled `mu* = provenance_json.anchor_value_c` and
`sigma_pred = provenance_json.predictive_sigma_c` (confirmed by reading
`src/data/replacement_forecast_materializer.py:2750-2754`: `anchor_value_c` is
the served fused center *after* the identity/EMOS-affine step — on the
source-clock live route this is byte-identical to the diagonal fused `mu_c`),
pulled the VERIFIED `settlement_outcomes` value (unit-converted to °C), and
computed the standard-normal PIT `z = (Y_c − mu*) / sigma_pred`. A
well-calibrated Normal predictive distribution implies `z ~ N(0,1)`.

**Result (n=163):** mean(z) = **+0.273**, stdev(z) = **1.055**, frac|z|<1.0 =
75.5% (nominal 68.3%), frac|z|<1.645 = 90.2% (nominal 90.0%), frac|z|<2.0 =
92.0% (nominal 95.4%).

- **stdev(z) ≈ 1.05 — essentially nominal.** Sigma underdispersion is **ruled
  out** as the primary mechanism; `sigma_pred` is not systematically too
  narrow.
- **mean(z) = +0.27, one-sided and split-consistent — this is center bias.**
  Split by metric: **high: n=142, mean_z=+0.376** (settlement runs *warmer*
  than `mu*`, i.e. `mu*` is biased **cold**); **low: n=21, mean_z=−0.423**
  (settlement runs *colder* than `mu*`, i.e. `mu*` is biased **warm**). Both
  push in the *same physical direction*: the model's daily-extremum center
  estimate is damped toward the climatological middle relative to the true
  observed extreme — a classic ensemble-mean smoothing bias, not a
  side-specific probability-transform defect.
- This directly explains why **`buy_no` (107/114 of the entered band) is the
  hurt direction**: `buy_no` bets against a specific narrow bin sitting away
  from `mu*`; a cold-biased `mu*` (for `high`) or warm-biased `mu*` (for
  `low`) both push the true extremum *toward* the traded-against bin more
  often than the model priced. This matches AGENTS.md's own iron rule #4
  verbatim: *"buy_no derives from forecast YES bin — cold-bias corrupts the
  family."* Confirms losses cluster at larger `|z|` than wins for `buy_no`
  (LOST mean|z|=0.840 vs WON mean|z|=0.626 overall; in the 0.80–0.95 buy_no
  subset specifically: LOST mean_z=+0.400 vs WON mean_z=+0.045 — losses are
  the one-sided-warm-surprise tail, wins cluster near z=0).
- Split by `posterior_method`/`semantics_revision`: rows tagged with a NAMED
  current-evidence semantics revision (`ensemble_center_disagreement_v1`,
  n=19, mean_z=+0.016; `ensemble_anomaly_transport_v1`, n=5, mean_z=+0.564,
  small-n) look closer to unbiased than rows with `semantics_revision=None`
  (n=139, mean_z=+0.298) — i.e. the bias is not evenly spread across every
  materialization path; it concentrates where the current-evidence shape
  provenance is *absent*. This is suggestive, not conclusive at n=19/5, but
  is the right lead for a follow-up narrower than this report's scope.
- **Not a tail-transform (full_transport_v1) artifact**: every matched row's
  `posterior_method` is `openmeteo_ecmwf_ifs9_bayes_fusion` (135/163) or
  `..._aifs_sampled_2t_soft_anchor` (28/163) — the replacement-chain
  fused-Normal-direct route, not the retired legacy shape. The memory-noted
  full_transport_v1 high-tail under-shrink is real history (per
  `docs/evidence/.../forecast_tail_overconfidence...`) but is not what is
  producing *this* band's distortion in the *current* served chain — the
  mechanism here is upstream of the tail-transform step, at the center
  itself.

**Verdict: center bias (mu* damped toward the climatological mean, ~0.3–0.4°C
one-sided, present in the CURRENT live fused-Normal-direct chain), not sigma
underdispersion, not a tail-shape defect.**

---

## 3. Survives the 2026-07-19 fixes?

**Cannot be answered — zero post-fix settled sample.** No entered position in
the dataset has `entered_at ≥ 2026-07-18` (today is 2026-07-19; settlement
takes at least the market's remaining life, so nothing entered post-fix has
settled yet). July entries pre-dating 07-18 (n=47 in the band) show
realized=59.6% vs June's 67.2% — no improvement, but this predates the M-13 /
sub-hourly center-delta fixes and is not evidence either way about them.
Honest answer: **too recent to test; re-run this section in ~1–2 weeks once
positions entered after 2026-07-18 start settling.**

---

## 4. Money quantification, 0.80–0.95 band

- 114 entered positions, $932.56 total capital staked, realized PnL **−$37.34**
  (matches `pnl_attribution.md` §8 #4's −$37.34 for this band, cross-checked
  independently here via direct `settlement_outcomes` join rather than the
  `resolved_exit_price` proxy for the 163-row PIT subset — consistent).
- Claimed edge (q − price) = +23.9pp → realized edge (empirical freq − price)
  = **+1.4pp**, not distinguishable from zero at this n.
- **What "corrected q" changes is not primarily the realized PnL number (that
  already happened) — it is the entry decision itself.** Kelly stake scales
  with edge; an honestly-calibrated q of ~0.63–0.65 (the empirical realized
  frequency) against an average entry price of 0.617 leaves an edge of only
  ~1–3pp, likely below whatever minimum-edge/FDR gate currently licenses
  entry for most of these 114 candidates. The corrected counterfactual is not
  "the same trades at smaller size losing less" so much as **"most of these
  114 positions would plausibly not have cleared the entry gate at all"** —
  the $932.56 of capital and the −$37.34 loss are both largely avoidable
  under honest calibration, not just shrinkable.

---

## 5. Fix specification

**The correct mechanism already exists, is architecturally legal, and is
already wired to the right choke point — but is both disarmed and fed stale,
incomplete data.** Concretely, in order of what changes:

1. **Where it legally sits**: `src/calibration/settlement_coverage_hierarchy.py`
   (created 2026-07-04, authority basis literally cites "q=0.84 bucket
   realizes 0.44, n=36" — the same defect class this report re-proves at
   larger n). It is a walk-forward, strictly-prior-only (`filter_observations_
   prefix`), one-sided-shrink-only (never raises q) Jeffreys beta-binomial
   empirical recalibrator producing an executable `(q_exec, q_lcb_exec)` pair
   distinct from the frozen `(q_raw, q_lcb_raw)` certificate — it does not
   mutate the certificate, matching the "frozen decision probability" law in
   `AGENTS.md §0`. It is consumed at
   `src/engine/event_reactor_adapter.py:_settlement_coverage_hierarchy_
   executable_pair` / `_event_bound_execution_probability_pair`, i.e. the
   Kelly/admission money-path choke point (wired in commit `56ae8cf16`,
   2026-07-04). Under `regime_unification_2026-06-12.md` U4 this is exactly a
   legitimate "fitted artifact" flag (kind 3 of 3), not a resurrected
   shadow/legacy regime — it sits downstream of, and does not touch, `mu*` /
   `sigma_pred` / the Normal q construction; it corrects the OUTPUT q against
   settled history, which is the right layer for a center-bias-driven
   distortion that the fusion math itself is not fully absorbing.
2. **Current state — disarmed**: `feature_flags.settlement_coverage_hierarchy_
   enabled` is absent from the live `config/settings.json` (only present,
   `false`, in `config/settings.example.json`) → the code's own
   `.get(..., False)` default means it is OFF in production today.
3. **Why arming it alone will NOT fix this band (the actual, non-obvious
   blocker)**: its sole data source, `_hierarchy_observations_all`
   (`src/engine/event_reactor_adapter.py:33049`), reads *only*
   `edli_no_submit_receipts` (world.db). Two independent problems there:
   - **Staleness**: the table has been silently empty since 2026-06-28
     (confirmed: `max(decision_time) = 2026-06-29T01:44:25Z`, and independently
     root-caused in commit `342534cbd` — 2026-07-19 06:16 — to commit
     `9e84989525` flipping `proof_accepted` False for mode-flipped/strategy-
     floor aborts and Day0 admission rejections, which both receipt writers
     require True to persist). That fix (`342534cbd`) was itself **reverted 17
     minutes later** the same morning (`12c763ce5`, no reason recorded in the
     commit) — this is a currently open, unexplained regression independent of
     the calibration question, worth a direct operator ping.
   - **No `strategy_key`**: the receipt JSON schema for no-submit candidates
     never includes a `strategy_key` field (confirmed against a live sample —
     the key is simply absent), so every observation the hierarchy would ever
     see from this source canonicalizes to `UNKNOWN`. Levels 1/1b
     (`STRATEGY_BUCKET`/`STRATEGY_SUPERBUCKET`) require a canonical
     `strategy_key` and can **never** fire from this source. Levels 2/3
     (`CROSS_STRATEGY`/`GLOBAL`) require ≥2–3 *canonical* strategies each with
     n≥20 within the pool and also cannot be satisfied by an all-`UNKNOWN`
     pool. That leaves only Level 0 (`LOCAL_SHIELD`: exact `city + metric +
     band_template + direction`, min_n=30) reachable — and `band_template`
     is date-stripped but city-and-specific-degree-value-scoped
     (`_coverage_band_template`, e.g. "Will the highest temperature in
     Singapore be 31°C"), so reaching n≥30 in one exact city/value/direction
     cell is rare. **Simulated directly against the real module** (imported,
     not reimplemented) using the 477 deduped receipt observations: every
     tested q in a representative Shenzhen/high/buy_no cell returned
     `INSUFFICIENT_DATA` — confirms arming the flag today, unchanged, is a
     no-op for this exact band.
4. **What must actually change** (three concrete, minimal-machinery steps,
   no new authority needed):
   - Restore `edli_no_submit_receipts` persistence (re-land `342534cbd` or
     equivalent, after understanding why it was reverted) so the source is
     current again.
   - Extend `_hierarchy_observations_all` to also ingest **entered positions'**
     walk-forward settled outcomes (`outcome_fact` / `position_current`,
     which DO carry `strategy_key`) into the same observation pool, strictly
     prefix-filtered by `settlement_time < decision_time` exactly as the
     no-submit path already is — this is the source that actually has
     canonical strategy identity and is large enough (99+15=114 in-band) to
     let Levels 1–3 engage.
   - Only then arm `settlement_coverage_hierarchy_enabled` (start in a
     shadow/diagnostic run — the module already computes `q_exec` without
     being consumed live if the flag stays off in the sizing path but a
     script calls `hierarchical_coverage_check` directly for monitoring, as
     done in §1b/§5 above — before flipping it into the live Kelly/admission
     path).
5. **What must NOT be done**: do not build a second, parallel recalibration
   layer (regime_unification U1/U3 forbids era-layering); do not touch
   `mu*`/`sigma_pred`/the Normal q construction to "fix" this — the PIT
   evidence (§2) shows the distortion is a real but comparatively small
   (~0.3–0.4°C) center residual that the existing walk-forward de-bias/EMOS-
   affine machinery already targets in principle; layering a second ad hoc
   center correction on top risks double-counting per the EMOS/staleness-
   ladder "no double-counting" precedent already established in
   `replacement_final_form_2026_06_09.md` §4a. The hierarchical q-shrink is
   the licensed, already-built, already-tested, already-wired answer; it
   needs its plumbing finished and its flag armed, not a new artifact.
6. **Invariant tests that must guard this**: the module's own test suite
   (`tests/test_*` for `settlement_coverage_hierarchy` — not enumerated here,
   verify it exists and is green before arming) plus a NEW test asserting
   `_hierarchy_observations_all` includes entered-position observations (not
   only no-submit receipts) and that Levels 1–3 are reachable with realistic
   `strategy_key` diversity, plus a walk-forward backtest-style check (using
   only pre-decision data, per the module's own `filter_observations_prefix`
   contract) proving the 0.80–0.95 band's `q_exec` after arming lands within
   a few points of the ~63% empirical frequency measured here, not the ~85%
   raw claim.

---

## 6. Caveats and honest limitations

- The 163-row PIT-localization sample and the 114-row money-quantification
  sample are not identical (163 required a matched `forecast_posteriors` row;
  114 required a resolved `resolved_exit_price`); both are drawn from the same
  264-row settled/economically_closed universe and agree directionally.
- `band_template` in the §1b/§5 simulation used a coarser `city|metric` proxy
  (bin_label was not carried through the receipts parse) rather than
  production's exact date-stripped label — this is stated as an approximation
  and does not change the Level-1/2/3 unreachability conclusion (that's driven
  by `strategy_key=UNKNOWN`, unaffected by the band-key coarseness).
- The no-submit receipt sample (§1b) is adverse-selected by construction
  (candidates that failed some other gate) — it corroborates the direction and
  rough magnitude of the entered-position finding but should not be read as
  an unbiased estimate of the *same* population.
- §3 (does it survive the 07-19 fixes) is genuinely unanswerable yet — say so
  rather than force a number.
