# Zeus End-to-End Money-Path Audit — Full Synthesis (2026-06-14)

Read-only audit of the canonical SIMPLIFIED money path, S1→S9, every stage reviewed once.
Live posture at audit: `real_order_submit_enabled=true`, `reactor_mode=live`,
`edli_live_scope=forecast_plus_day0`, wallet ~$1162. Actual venue reality in the trailing 36h:
**1 distinct order, state=EXPIRED** (zeus_trades.db venue_order_facts, observed_at>2026-06-13T00:00).
The chain is live and armed but essentially silent — which makes belief-corruption defects, not
plumbing, the operative risk: a silent chain that DOES fire must fire on an honest bin.

Law-8 spine: **edge = selecting the CORRECT BIN; q / q_lcb / edge / size are relay+recompute.**
A stage is a DEFECT only if it corrupts the data/metadata that names or weights the correct bin.

---

## (1) PER-STAGE VERDICT TABLE

| Stage | Name | Verdict | One-line reason |
|---|---|---|---|
| **S1** | Data daemon download / ingest (ecmwf_open_data) | **DEFECT (MEDIUM)** | Live mechanics correct; all 5 timestamp columns store cycle_time not dissemination_time → 8h backtest lookahead. No live bin impact. |
| **S2** | Per-city regional temperature (OM9 anchor) | **DEFECT (HIGH)** | Coords/DST/local-day extraction correct; representativeness de-bias artifact ABSENT → ~1°C cold anchor propagates uncorrected into the fusion prior. |
| **S3** | Per-model walk-forward de-bias (edli_per_city_v1) | **DEFECT (HIGH, conditional)** | Rows fitted+stored correctly (Tokyo −4.847°C, SF −4.073°C) but the EMOS sole-calibrator regime hardcodes `_bias_corrected=False` → correction never fires on the primary FORECAST lane. Defect ONLY if EMOS μ does not independently absorb the bias — unverified. |
| **S4** | T2 Bayesian precision fusion | **CORRECT** | T2 algebra, LW shrinkage, date-aligned Σ, provider-dedup all verified at Amsterdam/high/lead1 (μ*=17.154, sd=0.752 vs settle 17.00). Stale module constants are dead, zero runtime effect. |
| **S5** | μ*, σ_pred + settlement σ-shape floor | **DEFECT (HIGH)** | σ pipeline arithmetic correct, but the LIVE σ form is the flat uniform-pedestal (`w=0`, `floor_steps=1.8002`), whose ring-0 calibration ratio is **0.585 (over-confident: q_mode 0.381 vs realized 0.223)**. The center-faithful kernel refit is `candidate:true`, OPERATOR-GATED, NOT live. |
| **S6** | Settlement-preimage bin integration → q | **CORRECT** | wmo_half_up `[bin−0.5, bin+0.5)` preimage, oracle_truncate for HK, per-city preimage threaded (task #24/#41). Integration is honest given μ*,σ. |
| **S7** | q_lcb floor + caps (incl. K3 coverage) | **DEFECT (HIGH)** | K3 settlement-backward-coverage is **shrink-only** (`min(q_lcb, q_lcb_out)`, line 224) and grades against **(city,metric,season) climatology**, not per-day calibration → it can only crush a concentrated forecast toward the seasonal base rate, never license a true sharp edge. |
| **S8** | edge → fractional Kelly → size | **CORRECT (with hardcoded-constant caveat)** | Single-Kelly family-total restructure live (task #18/#63); `kelly_multiplier=0.125` hardcoded (operator-set, documented), bias-decay haircut 0.5 active. Math composes correctly; the constant is a tuning value, not a corruption. |
| **S9** | decision gates → ARM → submit | **CORRECT (by design) / EXPOSED** | Gates (direction_law, capital_efficiency = honest q_lcb>price, ARM coverage-block) are mechanically honest. Mainstream-consistency was DELETED (operator law 2026-06-04: display-only). So the ONLY defense against a phantom order is honest upstream q — which S2/S5/S7 do not currently guarantee. |

Net: **4 stages CORRECT (S4, S6, S8, S9-mechanics), 5 stages DEFECT (S1, S2, S3, S5, S7).**

---

## (2) INTEGRATING TRACE CONCLUSION — did the correct bin get honest mass end-to-end?

Method: for every VERIFIED settled market (148 with a matched winning-bin posterior, all
`openmeteo_ecmwf_ifs9_aifs_samp`), pull the freshest bound posterior and read the realized
winning bin's q_point, q_lcb, q_ucb (`/tmp/trace2.py`, zeus-forecasts.db, read-only).

**Result distribution over the 148 settled winning bins:**

| Category | n | Meaning |
|---|---|---|
| D — q_lcb survives (≥0.03) | **125** | correct bin carried tradeable lower-bound mass — chain worked |
| A — q_point high (≥0.15), q_lcb crushed (<0.03) | 6 | model NAMED the bin, the LCB floor erased it |
| B — q_point mid (0.05–0.15), q_lcb crushed | 15 | model leaned right, LCB erased it |
| C — q_point low (<0.05), model missed the bin | 2 | genuine forecast miss (not a pipeline defect) |

**Conclusion: the chain delivers an honest correct-bin LCB in 125/148 = 84% of settled markets —
the spine is fundamentally working.** Where it BREAKS is the **21/148 (A+B) class where the model
put real point mass on the bin that actually won, but q_lcb was crushed below 0.03 and the bin
became untradeable.** Named A/B casualties: SF 2026-06-11 high 90-91°F (q=0.080, q_lcb=0.004),
Karachi 2026-06-11 40°C (q=0.084, q_lcb=0.009), Shenzhen 2026-06-11 32°C (q=0.094, q_lcb=0.009),
Shanghai 2026-06-13 low 20°C (q=0.074, q_lcb=0.008), Beijing/London/Seattle/Denver/Munich/Helsinki
06-12 highs (q≈0.12–0.16, q_lcb≈0.01–0.03).

**WHERE it broke (two compounding mechanisms):**
1. **S5 over-confident live σ form.** The live flat-pedestal σ produces a center that is too sharp
   on the mode (ring-0 ratio 0.585 = mode q overstated ~1.7×) and correspondingly thin on the
   ±1/±2 ring where most winners actually land. The winning ring bin gets a small q_point and a
   q_lcb that the LCB-width correction then crushes. This is the A/B-class generator.
2. **S7 shrink-only K3 coverage** grading against seasonal climatology: any forecast more
   concentrated than the unconditional base rate is graded UNLICENSED and shrunk toward that base
   rate — exactly the wrong direction for a sharp, correct, concentrated belief. It cannot rescue a
   crushed ring bin; it can only further suppress.

S2's ~1°C cold anchor compounds A/B on the warm side: a cold μ* shifts mass off the true (warmer)
winning bin, which is consistent with the warm-side winners (SF 90-91°F, Karachi 40°C, Beijing 32°C)
appearing in the crushed class.

**Net integrating verdict: the correct bin gets honest mass 84% of the time; the 16% failure is a
single coherent class — concentrated-but-correct ring bins crushed by an over-confident live σ
(S5) and a base-rate-only coverage haircut (S7), aggravated by an uncorrected cold anchor (S2).**
This is the live alpha leak, and it is upstream-of-gates: the gates (S9) correctly reject 0.004<price.

---

## (3) RANKED DEFECT LIST — ordered by money-path impact

### D1 — S5 live σ is the over-confident flat-pedestal, not the center-faithful refit  [HIGH, #1 lever]
- **file:line**: live form `state/sigma_scale_fit.json` `_meta.supersedes_form` (`w=0`, flat
  `1/n_bins` pedestal); fitted-but-gated kernel mixture has `"candidate": true` +
  `"promotion": "candidate"`. Consumer: `src/data/replacement_forecast_materializer.py:1593-1628`.
- **evidence**: live `calibration_at_k1_w0` ring-0 ratio = **0.585** (q_mode 0.381 vs realized 0.223);
  the fitted candidate's `calibration_at_fit` ring-0 ratio = 1.042. 21/148 settled winners crushed.
- **bin consequence**: mode q overstated ~1.7×; ring (±1/±2) q understated; winning ring bin's q_lcb
  crushed <0.03 → untradeable. This is the A/B-class root and the single largest money mover.
- **fix**: promote the center-faithful refit (task #69 A4, `m`/`w=0.6` two-normal) after forward-fill
  validation, and wire `floor_steps`+`m` in the materializer consumer; re-materialize. Operator-gated.

### D2 — S7 K3 coverage is shrink-only + climatology-graded (cannot license a sharp edge)  [HIGH]
- **file:line**: `src/calibration/settlement_backward_coverage.py:224`
  (`return float(min(float(q_lcb), float(verdict.q_lcb_out)))`), cohort key `(city, metric, season)`
  at lines 132/137; applied via `event_reactor_adapter.py:12193-12220` under
  `q_lcb_settlement_coverage_gate_enabled=true` (LIVE).
- **evidence**: `min()` proves it can only lower q_lcb; season-cohort grading means any per-day sharp
  forecast is compared to the unconditional seasonal win-rate and shrunk to base-rate−1pp when
  "over-claimed". KNOWN FINDING (b) confirmed structurally.
- **bin consequence**: a correct concentrated belief is penalized for being concentrated; reinforces
  the D1 crush on exactly the ring bins that win. Compounds, never rescues.
- **fix**: regrade coverage per-day-conditional (against the realized rate of the model's OWN
  concentration tier, not unconditional climatology) and make the verdict two-sided (license up to a
  calibrated ceiling, not shrink-only). Currently being rebuilt — keep flag OFF for the shrink leg
  until per-day grading lands; ARM-block leg may remain.

### D3 — S2 representativeness de-bias artifact ABSENT → ~1°C cold anchor uncorrected  [HIGH]
- **file:line**: `src/data/replacement_forecast_materializer.py:1470-1486` (correction = raw − 0.0
  when artifact missing); `src/calibration/anchor_representativeness_debias.py:89-115`;
  `state/anchor_representativeness_debias.json` (ABSENT).
- **evidence**: single_runs cold bias vs VERIFIED settlement = Tokyo −0.982°C (n=165), Amsterdam
  −1.110°C (n=150). Artifact never generated → bias_shift_c=None → no correction.
- **bin consequence**: μ* sits ~1°C cold; on a 2°C bin, ~½-bin of mass shifts to the cold-adjacent
  bin. Suppresses warm-side winning bins (consistent with SF/Karachi/Beijing warm-side crushes).
- **fix**: generate the artifact — BUT (task #90 BLOCKER) the fitter must first carry Law-8
  settlement-station provenance, AND the known product mismatch must be resolved: the fitter trains
  on `previous_runs` (−0.425°C) but the live anchor is `single_runs` (−0.982°C), a ~0.557°C residual
  gap. Quantify the inter-product gap on full history before activation; do not deploy the
  previous_runs-fit delta blindly onto the single_runs anchor.

### D4 — S3 de-bias dead on the primary FORECAST lane (EMOS regime bypass)  [HIGH, conditional]
- **file:line**: `src/engine/event_reactor_adapter.py:10875-10883` (EMOS branch hardcodes
  `_bias_corrected=False`), gate at 10820-10823, sole call site at 10958-10960.
- **evidence**: `edli_emos_sole_calibrator_enabled=true` (LIVE) → EMOS branch fires → bias correction
  never called for non-DAY0 forecast orders. `_mass_enable_note_2026_06_09` confirms
  `edli_bias_correction_enabled=true` is INERT on this lane. Rows exist (Tokyo −4.847°C) but unused.
- **bin consequence**: IF EMOS μ does not itself absorb the cold bias, Tokyo runs ~4.85°C / SF ~7.3°F
  cold → systematic mis-bin toward cold on the most-liquid daily-max markets. This is an
  **architecture decision, defect only conditional on the unverified EMOS-absorbs-bias claim.**
- **fix**: VERIFY whether EMOS μ independently absorbs the per-city cold bias (compare EMOS-served μ
  to settlement on Tokyo/SF). If yes → S3 is correctly superseded, downgrade to INFO. If no → either
  route the edli_per_city_v1 correction into the EMOS branch or confirm EMOS calibration covers it.
  This verification is the highest-value cheap next step — it disambiguates a HIGH defect to INFO.

### D5 — S1 timestamp conflation (cycle_time in dissemination columns)  [MEDIUM, backtest-only]
- **file:line**: `scripts/ingest_grib_to_snapshots.py:739`, `src/data/ecmwf_open_data.py:1355-1357`.
- **evidence**: all 5 timestamp columns = cycle 00:00Z for both cities; expected
  source_release_time = cycle+485min ≈ 08:05Z.
- **bin consequence**: NONE live (the causal gate is recorded_at freshness, not available_at). In
  backtest, available_at=00:00Z permits an 8h lookahead — compounds with D6.
- **fix**: store `next_safe_fetch_at` (cycle+485min) into source_release_time/source_available_at;
  fix the isinstance/serialization fallback at ecmwf_open_data.py:1356-1357.

### D6 — S1 ecmwf_open_data absent from dissemination_schedules registry  [LOW, backtest-only]
- **file:line**: `src/data/dissemination_schedules.py` (no ecmwf_open_data entry).
- **evidence**: `derive_availability('ecmwf_open_data', …)` → UnknownSourceError. Live path uses
  release_calendar.py and is unaffected.
- **bin consequence**: none live; backtest availability derivation fails closed or (worse) falls back
  to "available immediately", compounding D5's lookahead.
- **fix**: register ecmwf_open_data with the +485min dissemination offset.

### D7 — S8 kelly_multiplier hardcoded 0.125  [LOW, tuning not corruption]
- **file:line**: `config/settings.json:200` (`sizing.kelly_multiplier=0.125`, documented operator history).
- **evidence**: hardcoded, operator-tuned (0.25→0.125→0.0625→0.125), note explicitly says "replace
  after 500+ settlements".
- **bin consequence**: scales size, not the bin belief — no Law-8 corruption. Listed for completeness.
- **fix**: task #64 constant-elimination — fit from empirical edge-estimation error once n≥500.

---

## (4) CLEAN PASS-LIST — stages verified CORRECT (the live fix may trust these)

- **S4 — T2 Bayesian precision fusion**: algebra, Ledoit-Wolf shrinkage, date-aligned Σ intersection,
  provider-family dedup, ifs025→ifs9 anchor bridge all verified numerically at Amsterdam/high/lead1
  (μ*=17.154, final_sd=0.752 vs settle 17.00). The prior "raw vs bias-corrected Σ" finding was
  refuted (covariance is translation-invariant, inflation 0.000%). Stale module constants
  (`bayes_precision_fusion.py:61-63`) are dead code, zero runtime effect. **TRUST.**
- **S6 — settlement-preimage bin integration**: wmo_half_up `[bin−0.5, bin+0.5)` preimage,
  oracle_truncate for HK, per-city preimage contract threaded (tasks #24, #41). Integration is honest
  for any given (μ*, σ). The defect is upstream in σ (S5), not in S6. **TRUST the integrator.**
- **S8 — single-Kelly sizing**: family-total pinned to equity×fractional-Kelly applied once, cash as
  a bound (tasks #18, #63, recent commits 57c441/92a9ef). ΔU concavity is shape-only. Math composes
  correctly; the only caveat is the hardcoded fraction (D7), which is tuning. **TRUST the structure.**
- **S9 — decision gates / ARM / submit mechanics**: direction_law, capital_efficiency (the honest
  q_lcb>price gate — task #66 confirmed it is NOT a removable gate), ARM coverage-block, submit-lane
  stamp/persist invariant (task #54), final-fresh-snapshot authority (task #39). The gates correctly
  reject phantom orders because they are fed an honest comparison. **TRUST the gates** — but note they
  are only as honest as the q they receive, so D1/D2/D3 must be fixed upstream, not at the gate.

---

## BOTTOM LINE

The money path is **structurally sound and 84% honest end-to-end** (125/148 settled winners carried
tradeable correct-bin LCB). The four core stages a live fix needs to stand on — **S4 fusion, S6 bin
integration, S8 Kelly, S9 gates** — are CORRECT and trustworthy. The live alpha leak is one coherent
class: **concentrated-but-correct ring bins crushed before they reach the gate**, generated by an
**over-confident live σ (D1, the #1 lever)**, a **shrink-only/climatology-graded coverage haircut
(D2)**, and an **uncorrected ~1°C cold anchor (D3)** — with **S3's de-bias bypass (D4)** an unverified
HIGH that the cheapest next probe (does EMOS μ absorb the bias?) can resolve to INFO.

**Fix order by money moved: D1 (σ refit) → D4 (verify EMOS absorbs bias) → D2 (per-day coverage) →
D3 (anchor de-bias with Law-8 provenance + product-gap quantified) → D5/D6 (backtest timing) →
D7 (Kelly constant).** None of these are at the gates; all are upstream belief corruptions, exactly
where Law-8 says the damage is irreversible.
