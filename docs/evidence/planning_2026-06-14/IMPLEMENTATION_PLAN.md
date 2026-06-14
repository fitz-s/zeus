# IMPLEMENTATION PLAN — Forecast-Precision-Constrained, Settlement-Proven Correct-Bin Alpha

```
Created: 2026-06-14
Last reused or audited: 2026-06-14
Authority basis: edge_existence_decisive.md (THE PIVOT, HIGH confidence), P3_architecture.md
  (per-layer adjudication), P3_redteam_2.md (UP-arm refutation), P3_verification.md (gate ladder),
  P2_W-KEEP-SIMPLIFY.md (KEEP-list), data_source_audit_prompts_2026-06-14.md (the alpha-path audit),
  diagnosis_confirmation.md (binding constraint), operator contract laws 1-8, the no-caps /
  no-shadow / no-gate-accretion operator memories.
Mode: PLAN-ONLY. One document. No code edits, no deploy, no live touch. DBs read-only.
```

**This is the single, final, fully-written implementation plan. It is reframed around a VERIFIED pivot
that overturns the pre-pivot strategy-of-record (P1) and its q_lcb causal-fix track (P2 W-QLCB).**
The pre-pivot plans proposed *releasing suppressed ring alpha* by raising a crushed q_lcb. Fresh,
independent, settlement-graded measurement (`edge_existence_decisive.md`, re-confirmed by
`P3_architecture.md` F1–F5 and `P3_redteam_2.md`) proves there is **no suppressed alpha to release at
the current forecast precision** — every price band loses after the fee, and the model's peak-bin error
equals one bin-width. The causal-alpha fixes (the bidirectional UP arm, the σ promotions) are therefore
**KILLED**. What survives is (a) a small set of honesty/correctness fixes that prevent the system from
trading measured losers, and (b) the real work: **sharpening the forecast** so a band's after-fee edge
goes positive at adequate n. RULE 1 is not violated — "no edge at current precision" is OUR forecast
defect to fix (the data/sources/fusion are not sharp enough to resolve the bin), not a license to stand
down forever.

---

## 1. VERIFIED REALITY — no tradeable correct-bin alpha at the current forecast precision

### 1.1 The decisive measurement (re-measured at source and settlement, HIGH confidence)

`edge_existence_decisive.md` re-measured every number at source this session (not inherited from
P1/P2/P3), on `state/zeus-world.db::no_trade_regret_events` (260,936 rows; 40,009 graded with
`would_have_won`) and `state/zeus-forecasts.db::settlement_outcomes` (7,029 VERIFIED). Event-level dedup
= ONE row per `city|target_date|bin_label|direction` (dedup verified collision-free: **0** bins where
`would_have_won` differs across duplicate rows — the outcome is a settlement property of the bin).

**After-fee realized edge by price band** (edge = `would_have_won − entry_cost − 0.01 fee`; event-level
dedup; Wilson 95% CI on win-rate; normal 95% CI on mean edge):

| dir | band | n | wins | WR | mean cost | **edge after fee** | edge 95% CI |
|---|---|---:|---:|---:|---:|---:|---|
| buy_yes | cheap <0.05 | **314** | 2 | 0.006 | 0.011 | **−0.0145** | **[−0.0233, −0.0058]** |
| buy_yes | **ring 0.05–0.15** | **66** | 2 | 0.030 | 0.093 | **−0.0725** | **[−0.1146, −0.0304]** |
| buy_yes | near-ctr 0.15–0.40 | 70 | 19 | 0.271 | 0.286 | −0.0247 | [−0.1274, +0.0780] |
| buy_yes | mid 0.40–0.60 | 24 | 9 | 0.375 | 0.460 | −0.0954 | [−0.2954, +0.1046] |
| buy_no | deep-fav >0.60 | **957** | 868 | 0.907 | 0.925 | **−0.0285** | **[−0.0449, −0.0120]** |
| buy_no | mid 0.40–0.60 | 15 | 8 | 0.533 | 0.569 | −0.0460 | [−0.305, +0.213] |
| buy_no | ring 0.05–0.15 | 4 | 1 | 0.250 | 0.098 | +0.1423 | [−0.341, +0.626] |

**Every band's after-fee edge is ≤0 at adequate n.** The two best-powered buy_yes bands (cheap n=314;
ring n=66) have 95% CIs **strictly below zero** — significantly negative, not underpowered. The base-rate
deep-favorite buy_no band (n=957) is also negative after the fee (−0.0285, CI below 0): even
favorite-buying loses 2.85¢/share net at these prices. The single positive cell (buy_no ring +0.14) is
**n=4** with a CI of [−0.34, +0.63] — pure noise, not a band.

**Peak-bin selection accuracy** (latest posterior per city|date|metric; argmax bin center vs
`settlement_value`, °C cities, VERIFIED, n=230):

| metric | value | verdict |
|---|---|---|
| MAE \|peak_center − settled\| | **1.30 °C** | one full bin-width |
| in bin-widths (1 °C interior) | **1.30** | ≈ bin-width |
| median error | 1.00 °C | |
| exact-bin hit rate | **24.3% (56/230)** vs 11.1% chance | z = 6.39 (real skill) |
| within-1-bin | 67.0% | |

The model has **real, significant bin skill** (24.3% exact vs 11.1% chance, z=6.39) — but its center MAE
equals one full bin-width, so it routinely lands one bin off, missing the exact bin ~76% of the time.
Per-city variance is large (Manila 0.83 / London 0.70 / Tokyo 0.50 hit vs Seoul / Taipei / Chengdu /
Wuhan 0.00).

### 1.2 What it means (the pivot, stated plainly)

1. **The calibration / q_lcb "unlock suppressed alpha" track is REFUTED.** There is no positive-edge
   band that an over-conservative q_lcb is suppressing. Relaxing q_lcb to admit the cheap/ring bins
   admits a *measured-losing* distribution (the ring loses 7.25¢/share after the 1¢ fee). `P3_redteam_2`
   independently killed the proposed bidirectional UP arm at the mechanism level: its discriminator
   (isotonic by claimed-band) is structurally blind on the crush cohort — it pools far-tail (population
   A) and ring (population B) at q_lcb≈0 and short-circuits to a single pooled win-rate
   (`settlement_backward_coverage.py:115-116`), so it lifts honest-zero tail bins, has no vs-market term
   (a base-rate/law-4 trap), and removes a deliberate antibody without proof.

2. **`P3_architecture` F2 sharpens the producer story:** the live ring q_lcb is **0.032** (not crushed),
   while a `center_sigma_c=3.0` bootstrap yields **0.003** — so the live ring bound is *not* drawn at
   σ=3.0; the `anchor_sigma_c=3.0` provenance stamp disagrees with the value the live ring bound was
   actually drawn at. The 3.0 hardcode is therefore **not an alpha lever** — fixing it only affects the
   far tail (where 3.0 does bite and where the bound *should* stay ≈0). It is a **provenance-honesty**
   fix, not an alpha fix.

3. **The binding constraint is FORECAST PRECISION.** Per operator law 8 (edge = selecting the correct
   bin) and RULE 1 (no-edge is OUR defect, never an excuse), a model whose center error equals the bin
   width *cannot* be re-calibrated into exact-bin selection — the residual is center noise at this lead,
   not an over-conservative lower bound. The path to alpha is **sharpening the forecast** (reduce
   peak-bin MAE below bin-width), and/or **selecting market structure** where the current precision
   already suffices (wider bins, shorter lead), and/or **reducing fee drag** where it flips a band
   positive — NOT relaxing calibration.

4. **Capital must NOT trade now.** Every band loses after the fee. Stand down on live admission until a
   correct-bin edge is settlement-proven on a specific structure.

### 1.3 Why RULE 1 points at forecast precision (not at standing down)

RULE 1 says no-edge is OUR defect to fix, never an excuse. The pivot does not violate it: "no edge at
current precision" is a **forecast defect** — our data sources, their time-semantic injection, and the
fusion are not sharp enough to resolve which 1°C bin the day's max lands in. The defect is real and ours
to fix; the lever is upstream (sources/fusion/structure), not the gate. A dated, settlement-proven
"NO_EDGE at adequate n on structure X" is a valid *intermediate* result that **redirects** to another
structure — it is never a terminal "the market is efficient, stop." We stop searching only when every
reachable structure at every reachable precision has been settlement-proven NO_EDGE, which is nowhere
near established (the audit and the structure search below are entirely unrun).

---

## 2. HONEST CORRECTNESS FIXES (truthful + safe; NOT alpha)

These make the system honest and prevent it from trading measured losers. **None manufactures alpha.**
Each is byte-identical-live or shadow/candidate at merge, each carries a RED-on-revert test, each is a
SIMPLIFY (collapses N→K; net gate count strictly decreases — law 3). Every deleted path is re-verified
dead-live in the cited sibling docs. They are the Wave-0/Wave-1 honesty pass; they ship first because
they cost nothing and they clean the signal the alpha path will be measured against.

### 2.1 H1 — Replace the hardcoded `center_sigma_c=3.0` default with a settlement-fitted σ (provenance honesty)

- **What & where:** `_build_fused_q_bounds` draws centers `μ_i ~ N(μ*, center_sigma_c)` and takes the
  per-bin 5th/95th percentile (`src/data/replacement_forecast_materializer.py:1399` the draw,
  `:1425` the q_lcb percentile; the call site passes
  `center_sigma_c=float(bayes_precision_fusion_override.anchor_sigma_c)` with the default
  `anchor_sigma_c: float = 3.00` at `:125`). The DB shows `anchor_sigma_c=3.0` on every posterior
  carrying the field.
- **The fix (two-step, in this order):**
  1. **K-L1-a diagnostic (mandatory prerequisite, never run):** trace WHY the live ring q_lcb is 0.032
     when provenance says σ=3.0 — i.e. whether the live read path
     (`event_reactor_adapter._replacement_yes_lcb_for_bin`) actually uses the 3.0-bootstrap map for
     `FUSED_NORMAL_FULL` ring bins, or reads a different persisted bound (the materializer comment at
     `:1216-1226` states the 3.0 bootstrap was *measured useless and not shipped on the live bound*).
     Write the result to `docs/evidence/planning_2026-06-14/sigma_center_diagnostic.md`. This decides
     whether the 3.0 map is even live for the ring (F2 says the ring bound is inconsistent with 3.0).
  2. **The change:** make provenance name the σ the bound was *actually* drawn at, and replace the 3.0
     **default** with a **settlement-fitted center-uncertainty σ** (`σ_center` = stdev of
     `|fused_center − settled|` by lead bucket, from `settlement_outcomes`), as a `candidate=true`,
     operator-gated artifact (`state/sigma_center_fit.json`, same discipline as `sigma_scale_fit.json`).
     This corrects a number that is provably 6–10× too wide on the far tail against the empirical
     residual.
- **Scope and honesty bound (binding, per `P3_redteam_2` Q1a):** the fitted σ_center carries a one-sided
  floor — **never tighter than the measured residual** — and a settled-tail no-admit assertion. It must
  NOT shrink the *predictive* σ (the weather spread; the 1.0°C floor at `:1119` — KEEP). On the ring this
  fix is a **no-op** (F2: ring bound is not drawn at 3.0); on the far tail it stays ≈0 (the bound
  *should* stay ≈0 there — laws 1/4/8). It re-loosens nothing settlement-dead.
- **Why it is not alpha:** it changes the *width* of a bound on bins the model already believes; it
  admits nothing the point q does not already support, and on the ring it changes nothing. It is
  provenance/honesty, not a lever.
- **RED-on-revert tests:** `test_center_sigma_not_hardcoded_3` (AST/grep antibody: ban
  `center_sigma_c=float(...anchor_sigma_c)` literal at the live call site);
  `test_sigma_center_fit_is_settlement_derived` (`_meta.source == "settlement_residual"`, never
  hand-picked); `test_sigma_center_candidate_true`; `test_shadow_column_does_not_touch_live_table`;
  `test_diagnostic_trace_3_0_origin` (the K-L1-a doc must exist).
- **Provenance verdict on the 3.0-bootstrap map for FUSED rows:** QUARANTINED (known-useless per the
  materializer's own comment) → confirm via K-L1-a it is genuinely not the live ring read, then it is
  dead vocabulary to remove.

### 2.2 H2 — KILL the bidirectional UP-arm / isotonic (the dual-refuted base-rate trap)

- **What dies:** the proposed `src/calibration/settlement_calibrated_qlcb.py` bidirectional UP arm
  (P2 W-QLCB §1.2 / node N7). It is **never created.** The existing `apply_settlement_coverage`
  (`src/calibration/settlement_backward_coverage.py:204-225`) stays **shrink-only** ("Never widen") —
  its one-sidedness is the antibody, not the disease.
- **Why (dual refutation):** (i) `P3_architecture` F3 — the band it would admit *loses* 7.25¢/share
  after the fee; (ii) `P3_redteam_2` — `_isotonic_realized_rate` short-circuits to the pooled bin base
  rate on the live single-band stream (`settlement_backward_coverage.py:115-116`), it has no vs-market
  Brier term, and it mutates `arm_gate_coverage_blocks` (the operator's verified-correct-before-live ARM
  interlock, `:228-259`). It is a base-rate admitter under a calibration costume.
- **The KEEP corollary:** `arm_gate_coverage_blocks`'s over-claim-block semantics stay **frozen**; the
  `q_lcb ≤ q_point` clamp (`probability_uncertainty.py:307,311,315`) stays absolute; `_isotonic_realized_rate`
  is CURRENT_REUSABLE for the shrink direction only, QUARANTINED for any UP direction.
- **Also KILLED: the σ-shape point-q promotion (node N6, `sigma_scale_fit.json` → live primary).** F3
  removes its rationale (it was to "raise the ring point so the ring clears" — but the ring is
  over-priced, so a higher ring point admits a *loser*). It stays `candidate=true`, OFF the live path.
  It may only ever be promoted later as an **independent, forward-fill-validated point-q precision
  improvement** (an alpha-path lever, §3b), never as an UP-arm ceiling-raiser.
- **RED-on-revert tests:** `test_qlcb_far_tail_stays_zero` (Milan-24C antibody re-homed onto the
  surviving path: far-tail q_lcb≈0 → `capital_efficiency` rejects); `test_qlcb_never_exceeds_point`
  (the clamp holds; a below-market model is never lifted over price);
  `test_no_bidirectional_up_arm_module` (grep/AST antibody: `settlement_calibrated_qlcb.py` does not
  exist as a live authority); `test_arm_gate_coverage_blocks_semantics_frozen`.

### 2.3 H3 — The W-KEEP deletions (dead EMOS lane, dead C2/C3 import, dead δ-penalty shadow)

Pure subtraction; every path re-verified dead-live in `P2_W-KEEP-SIMPLIFY.md §2` and
`diagnosis_confirmation.md`. Net licensing vocabularies 2→1; dead imports −1; shadow fields −3; **zero
live-behavior change** (every deleted path is already a live no-op).

- **H3a — collapse the two licensing vocabularies to one.** Delete (a) the EMOS live-licensing override
  that stamps `q_lcb_calibration_source=EMOS_ANALYTIC` and (b) the static source allow-list
  `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` consumed by `live_admission.py` G2/G4 (`:141,183`). Proof dead:
  `edli_emos_ci_live_enabled` absent → default False (`src/main.py:1016`,
  `event_reactor_adapter.py:11995`); `state/emos_ci_license.json` never built (never committed;
  `main.py:1001` "Operator must populate"); `EMOS_ANALYTIC` stamped 0× live. Make the live
  `settlement_backward_coverage` VERDICT the **sole** license authority; `q_lcb_calibration_source`
  becomes pure telemetry. **DO NOT** build `emos_ci_license.json` or flip the flag (the refuted
  synthesis; gate-accretion the operator forbids).
- **H3b — delete the dead C2/C3 selection-shrinkage live-path import.**
  `_compute_selection_shrinkage(..., authority_on=False)` (`event_reactor_adapter.py:2811`, pinned
  2026-06-13) writes only NULL stamps — neither deciding nor telemetry. Remove the import; leave BH/FDR
  as the condemned-interim gate with a provenance note; defer the BH→C2/C3 promotion until the
  foundation is settlement-proven.
- **H3c — delete the dead δ-penalty / shadow N_eff·JS fields on the live q_lcb seam.**
  `UncertaintyPenalties` is never populated on `_side_q_lcb_from_yes_samples` (the live canonical call
  passes no penalties / no `n_eff_override`, `event_reactor_adapter.py:10181`); `q_lcb_neff_corrected`
  / James-Stein write only `compare=False` shadow fields (`probability_uncertainty.py:215,283`). Remove
  from the live seam (or keep strictly as a grading diagnostic, never a live input).
- **D6 guard (the one place subtraction must wait):** do NOT delete the `coverage_unlicensed_tail`
  antibody's *effect* (`live_admission.py:141`) — only its dead source-allow-list *vocabulary*. The
  Milan-24C regression test moves onto the new path (far-tail q_lcb≈0 → `capital_efficiency` rejects).
  Keep the effect until H1's honest bound provably reproduces the far-tail rejection.
- **Deferred (not immediate):** the Wilson-over-AIFS-votes fallback (`event_reactor_adapter.py:9788`,
  returns 0.0 for zero-vote bins) — park its deletion until confirmed no shadow-coverage consumer needs
  a non-null bound.
- **RED-on-revert tests:** `test_source_allow_list_not_a_gate`;
  `test_c2_c3_selection_shrinkage_dead_on_live_path`; `test_delta_penalty_never_reaches_live_q_lcb`;
  `test_coverage_unlicensed_tail_still_rejects_far_tail` (D6 guard — the intent survives the vocabulary
  collapse).

### 2.4 H4 — The submit-path abort→re-decision (ARM-gated OFF)

- **What & where:** `SUBMIT_ABORTED_MODE_FLIPPED` terminally discards an admitted candidate when the
  fresh book flips maker↔taker (`event_reactor_adapter.py:4015-4020`,
  `_validate_final_order_mode_or_abort:3825-3861`; legacy tripwire `:4320-4342`). The master submit arm
  is already ON and correct (`real_order_submit_enabled=true`, `reactor_mode="live"`,
  `edli_live_operator_authorized=true`) — there is **no flag to flip to "unblock submission"** (that
  would itself be a FAILURE terminus under the operator contract).
- **The fix:** replace `_validate_…_or_abort` with `_resolve_final_order_mode_or_abort` that, on a flip,
  **re-evaluates the fresh mode against the SAME `capital_efficiency` inequality** on the fresh book
  (mirroring the K=1 freshness law already applied to price); submit in the fresh mode iff it re-clears
  `q_lcb − cost_in_that_mode > 0` AND passes the preserved downstream maker-book-agreement /
  taker-spread walls; abort only if neither mode clears. Fold the legacy tripwire into the resolver.
  This collapses the mode-equality vocabulary into the one admission gate (gate count −1) — a SIMPLIFY,
  not a loosening (the fresh mode must clear the identical inequality).
- **Why ARM-gated OFF (the critical sequencing law):** per F3, *no band is profitable now*, so shipping
  this live would only speed a losing distribution to the venue (laws 2/4 FAILURE). It is **built and
  tested but NOT armed** until the alpha path produces a settlement-proven profitable admission. Its
  value is purely contingent and downstream.
- **Also fold in (LOW, defer to task #64):** the covert `improve_delta=0.02` locked-opportunity de-dup
  constant (`:4733`) → market tick size; and the flat `0.01` adverse-selection penalty in
  `_robust_trade_score_from_generated_inputs` (`:13737`) → cost-proportional term, so the honest gate,
  not a constant, rejects cheap bins. Negligible admission effect (5 receipts all-time; cheap bins are
  settlement-dead); they remove lies, not add edge.
- **RED-on-revert tests:** `test_mode_flip_readmits_when_fresh_mode_clears_capital_efficiency`;
  `test_mode_flip_aborts_when_neither_mode_clears`; `test_missing_proof_mode_still_fails_closed`
  (never default a taker submit); `test_readmitted_maker_still_requires_book_agreement`;
  `test_readmitted_taker_still_obeys_spread_guard`; plus the cheap-tail settlement-deadness RED test for
  the looser TAKER→MAKER direction (`P3_redteam_1` KILL-4). And H4-submit observability
  (`test_every_persisted_receipt_names_its_lane`) ships free as Wave-0 telemetry so the abort/re-decide/
  honest-no-edge cases are distinguishable on the receipt alone.

**Net of §2:** the system stops being able to manufacture base-rate alpha, stops chasing a phantom 3.0
crush, stops trading measured losers, and stops dying to transient ticks once a real candidate exists —
all with zero live-behavior change at merge and a strictly smaller gate count. None of it produces alpha.

---

## 3. THE ALPHA PATH (the real work — forecast precision)

This is the only track that can produce a tradeable correct-bin edge. **The driving metric for every
sub-track: a band's after-fee edge goes positive at adequate `n_eff`** (the W-EDGEPROOF instrument, §4,
is how "positive at adequate n" is measured). The mechanism connecting all four sub-tracks: peak-bin MAE
(1.30°C ≈ bin-width) must drop below the bin-width on *some* (city × distance × lead × structure) cell,
OR the structure must be chosen so the existing 1.30°C already resolves it, OR the fee must be reduced
where it flips a measured-near-zero band positive.

### 3a — The full data-source audit (per-source time-semantic injection, per-city math, fusion)

**Why this is alpha-path, not housekeeping (law 8 at the source):** a wrong source run, off-by-one
cycle, lead mis-map, DST error, local-day boundary leak, wrong station, or mis-converted local day
makes the day's-max the model believes *wrong*, which mis-identifies the winning bin and confidently
mis-places q. **A correct calibration on corrupt source-time-semantics still loses.** A single
mis-injected source inflates peak-bin MAE; finding and fixing it is a direct MAE reduction — the most
likely single source of the 1.30°C error after the calibration layer is exonerated.

Run the audit per `data_source_audit_prompts_2026-06-14.md` (read-only; reports under
`docs/evidence/data_source_audit_2026-06-14/`; launched in parallel the moment §2 ships):

- **A. Per-source time-semantic injection** — one deep lens per source-family
  (S-ECMWF-OPENDATA, S-ECMWF-AIFS-ENS, S-OPENMETEO, S-OBS-WU-METAR, S-OBS-METEOSTAT-OGIMET, S-TIGGE,
  S-DAY0-OBS-LANE, S-OTHER-NWP), each establishing with file:line + a real recent example: how
  `run_time`/cycle is determined (actual published run, never a now−lag/previous-run guess — the task
  #30 failure class), how `valid_time`/lead maps to the real instant, ingest/served/watermark, the
  dissemination schedule, the UTC→per-city-local conversion + local-day max window, DST handling — then
  TRACE one real recent run for one real city end-to-end. Verdict per source:
  **time-semantics CORRECT | CORRUPT(<how>)**. Plus the **S-CROSS-CONSISTENCY** lens: does every source
  mean the same thing by "the day's max for city C", or do they disagree (the cross-source contradiction
  that corrupts fusion)? Read the time-semantics contract first
  (`src/contracts/time_semantics.py`, `dst_semantics.py`, `src/engine/time_context.py`,
  `src/data/source_time.py`, `dissemination_schedules.py`, `source_watermarks.py`,
  `temporal_provenance.py`).
- **B. Per-city ("math cities") math** — C-STATION-IDENTITY (settlement station/WMO id, elevation,
  migrations), C-SETTLEMENT-PREIMAGE-BINS (bin boundaries + the venue's actual per-city settlement
  preimage + boundary rounding, task #41), C-LOCAL-DAY-DST (local-day max window, DST, hemisphere),
  C-PER-CITY-CALIBRATION (σ-scale, debias, EMOS HIGH params, era-EB pooling — fitted from sufficient,
  correctly-attributed, leakage-free settled data). Verdict per city.
- **C. Cross-cutting** — X-COVERAGE-FRESHNESS-HEALTH (silent coverage holes corrupting fusion),
  X-FUSION-WEIGHTS-DEDUP (inverse-variance weights, Ledoit-Wolf Σ, source-family single-rep dedup,
  correlation), X-MISSING-MEMBERS (partial-ensemble bias on the max/tail), and the integrating
  **X-METADATA-TO-BELIEF END-TO-END** lens: trace ONE settled city-date from every source's raw
  injection → fusion → μ*,σ → settlement-preimage bin integration → q on the winning bin; did the
  correct bin get honest mass, and if not, which upstream source/time/city defect caused it?
- **D. Synthesis** → `docs/evidence/data_source_audit_2026-06-14/DATA_SOURCE_AUDIT.md` (full,
  untrimmed): per-source + per-city verdicts; the **ranked list of data-foundation defects that corrupt
  the correct-bin belief**, each with the fix required and its expected MAE consequence; plus a clean
  PASS-list so the alpha path knows what it can trust. **Operator-gated:** any source/city fix that
  changes live belief is ARM-gated, validated by re-measuring peak-bin MAE on held-out settlements.

### 3b — Fusion quality (reduce peak-bin MAE below bin-width)

The audit's X-FUSION lens identifies *defects*; this sub-track *improves* fusion to shrink residual
center variance:

- **Weighting:** verify and, where the audit licenses it, refit the inverse-variance / precision-fusion
  weights from walk-forward residual variance; ensure source-family single-rep dedup
  (ICON/NCEP/UKMO most-specific-first) and the Ledoit-Wolf / correlation-shrinkage Σ are correct so a
  mis-weighted or duplicated source does not distort the fused center.
- **More/better sources:** the audit's S-* PASS-list and the cross-consistency lens indicate where
  adding or re-weighting a source family reduces the residual; a multi-member fusion's center SEM should
  be ~0.3–0.5°C, far below the 1.30°C peak error — so a large fraction of the 1.30°C is *recoverable*
  variance, not irreducible NWP spread, IF the sources are correctly injected and weighted.
- **The σ-shape point-q precision lever (formerly N6), repurposed honestly:** `sigma_scale_fit.json` may
  be promoted later **only** as an independent point-q precision improvement (sharper bin mass), gated
  by its own `_meta.promotion` forward-fill validation (mode-bin ratio ∈[0.85,1.15]; tail ratio
  improving on unseen settlements) AND a measured peak-bin-MAE reduction on held-out settlements — NOT
  as an UP-arm ceiling-raiser. It is on the alpha path iff it sharpens the forecast, off it otherwise.
- **The metric:** peak-bin MAE recomputed on held-out settlements (the §1.1 audit, re-run) drops below
  the bin-width on some cell — equivalently, exact-bin hit rate rises materially above 24.3%.

### 3c — Market-structure selection (operationalize W-EDGE-LOCATE)

Trade bins/horizons where the **current** MAE already suffices, instead of waiting for a global MAE
reduction. The model's MAE varies by city (Manila 0.83 hit, Tokyo 0.50 vs Seoul/Taipei/Chengdu/Wuhan
0.00) and a forecast that lands within one bin 67% of the time *does* resolve a **wider** bin or a
**shorter** lead. Operationalize the read-only edge-location instrument:

- **E1 — the edge-location query** (`src/analysis/edge_location.py` + `scripts/edge_location_report.py`,
  read-only): one row per `(city, metric, ring_distance_bucket, lead_bucket, direction)` cell, event-level
  de-duplicated, walk-forward, vs-market, ranking cells by settlement-backed after-fee edge. Geometry via
  the LIVE `bin_forecast_distance` primitive (`direction_law.py`) — one distance authority. Grade through
  `grade_receipt`'s preimage spine (Alt B), with a one-time `would_have_won`-vs-spine cross-check
  (law 8). INV-37 ATTACH+SAVEPOINT cross-DB reads.
- **What it finds, and the search it drives:** today every ring cell is INSUFFICIENT_DATA (the distinct
  ring cohort is n≈5; the 66-event ring band of §1.1 is the *wider* aggregate, still negative). E1's job
  is to **search the structure space** — wider settlement bins (2°C/3°C bins where MAE 1.30°C lands
  inside), shorter lead buckets (Day0/nowcast where the obs lane sharpens the center), and the
  highest-skill cities — for any `(structure)` cell whose after-fee edge is positive at adequate
  `n_eff`. This is the operator's "restore mid-band if real / select where precision suffices",
  settlement-gated.
- **E3 — candidate-focus (DEMOTED to "do not ship" until licensed).** The `direction_law` `k=1.0` ring
  threshold may only be widened per-cell on a W-EDGEPROOF-LICENSED cell; `k_eff` is built (if at all) as
  the task #64 fitted-constant replacement, **default byte-identical to 1.0**, never a verdict-gated
  widen toward a band F3 shows is a loss. It ships LAST, after a structure is proven.

### 3d — Fee-drag reduction (maker-rest) where it flips a band positive

The 1¢ fee is the difference between several near-zero bands and a positive one (e.g. the near-center
0.15–0.40 band is −0.0247 after fee — within the fee of break-even). Where E1 finds a structure whose
*gross* edge (before fee) is positive but the *taker* fee drags it negative, **maker-rest** (zero taker
fee) can flip it positive without any precision gain:

- Reuse the existing K=1 fresh-book maker/taker authority and the H4 re-decision resolver: prefer the
  maker lane on a cell where E1 shows `realized − price > 0 > realized − price − fee`.
- **Hard guard (no-caps law):** this is NOT a maker-only mandate, a throttle, or an artificial bias — it
  is the honest selection of the lower-cost executable lane on a cell that *already* clears gross edge at
  adequate n. It does not manufacture edge; it removes a friction on a structure E1 has surfaced. It is
  gated by the same W-EDGEPROOF LICENSE as everything else (a maker-rested band must still clear >51%
  after *its* (zero) fee at n≥30, event-level, model-Brier < market-Brier).

**The composition:** 3a removes the source-level corruption inflating MAE → 3b shrinks the recoverable
center variance → 3c finds the structure where the resulting MAE resolves the bin → 3d removes the fee
drag where it is the last barrier. Each is settlement-measured; a band goes positive at adequate `n_eff`
or it does not, and the instrument (§4) says which.

---

## 4. THE GATE — W-EDGEPROOF (EP-1..EP-5) + the verification ladder

**No capital trades live until a settlement-graded correct-bin edge is proven.** The gate is the
W-EDGEPROOF instrument operationalizing `P2_W-EDGEPROOF.md` and the `P3_verification.md` ladder. It is a
read-only grading instrument emitting dated JSON evidence (no new DB authority, no new live gate — law 3),
graded solely from `settlement_outcomes(VERIFIED)`.

### 4.1 The unit and the contracts

- **Unit:** `(city, target_date, metric, bin_label)` — one SETTLEMENT EVENT, never a contract, cycle
  snapshot, or receipt row. INV-CAL-1 event-level dedup (`GROUP BY city,target_date,metric,bin,direction`;
  a test asserts no triple contributes >1 unit — the 40× Taipei row-inflation antibody).
- **Settlement-only grade** (INV-CAL-2): `won` from `settlement_outcomes(VERIFIED)` via `grade_receipt`'s
  preimage spine; `zeus-world.db.settlements` is EMPTY — never read it.
- **Walk-forward only** (INV-CAL-3): a band's verdict uses only events with `target_date < decision_date`;
  90-day embargo (fit window ends `2025-10-01`, evaluation `≥2025-10-01`); the isotonic/any calibration
  fit is NOT re-fit inside the evaluation window.
- **vs-market mandatory** (INV-CAL-4): a band is "edge" only if model-q beats market-q (lower Brier AND
  better log-score/RPS) on the SAME events AND realized−price lower-CI > 0. A tie with the market is
  NO_EDGE regardless of win-rate (the law-4 antibody against re-branding base-rate buy_no as alpha; buy_no
  is excluded from the ring gate scope by construction).
- **Proper scores:** multiclass log-score (strictly proper, tail-sensitive) AND RPS (ordinal
  cross-check) — both must beat the market benchmark; win-rate alone is insufficient.

### 4.2 The five artifacts (EP-1..EP-5)

| Artifact | Purpose | Pass criterion | Evidence file |
|---|---|---|---|
| **EP-1 structure/ring backtest** | does any structure carry positive after-fee edge at adequate n? (the G1 gate-before-the-gate) | per (structure) band: `edge_after_fee` lower-CI > 0 at `n ≥ 200`; or a clear dated NO_EDGE | `docs/evidence/edgeproof/EP1_*.json` |
| **EP-2 lift/precision taxonomy** | a precision improvement (3a/3b) lifts the RIGHT cells (ring/licensed structure), not far-tail (pop A) or below-market (pop C) | precision gain on licensed cells > 0; far-tail/pop-C unchanged; bound ≤ q_point always | `docs/evidence/edgeproof/EP2_*.json` |
| **EP-3 proper-score gate (LICENSE)** | model beats market on log-score AND Brier at power | `n_events ≥ 200` ∧ `model_log_score > market_log_score` ∧ `model_brier < market_brier` ∧ `edge_lo95 > 0` → LICENSED | `docs/evidence/edgeproof/EP3_*.json` |
| **EP-4 RPS distribution** | the precision gain did not worsen adjacent-bin calibration | `mean_model_rps < mean_market_rps` | `docs/evidence/edgeproof/EP4_*.json` |
| **EP-5 traded settlement (DONE)** | the licensed structure's LIVE fills clear after-cost at power | `n ≥ 30` event-level forward fills ∧ `lo90 win-rate after fee > 0.51` ∧ model-Brier < market-Brier ∧ continuous (rolling-30 stable) | `docs/evidence/edgeproof/EP5_*.json` |

**EP-1..EP-4 are the PRE-LIVE gate** (no live promotion crosses without all four passing on a structure).
**EP-5 is the POST-LIVE gate and the only DONE** — repeating, never a single fill.

### 4.3 The N_eff power floor

`N_eff ≥ 200` distinct events per band for a ~5¢/share after-fee edge at 80% power, one-sided
(`n ≈ (z_α+z_β)² p(1−p)/δ²`, δ=0.05, p=0.10 → ~222). For a 3¢ bar, ~618. Below 200 →
INSUFFICIENT_DATA regardless of realized rate. This is a HARDER floor than the W-EDGE-LOCATE calibration
`N_MIN=30` (which only says the calibration map is reliable, not that the edge is powered). Both must
pass for a LICENSE.

### 4.4 The verification ladder (gates G0–G5)

- **G0 — unit/RED-on-revert:** every §2 node's tests pass; full submit/admission suite green. Wave-0/1
  honesty nodes (H1 diagnostic+shadow, H2 kills, H3 deletions, H4 telemetry) are **self-arming** (byte-
  identical-live or read-only).
- **G1 — structure backtest (EP-1):** decides whether ANY structure is worth pursuing live. If every
  reachable structure collapses to NO_EDGE at adequate n → dated, settlement-proven "NO_EDGE on structures
  X,Y,Z" → redirect the alpha path (3a/3b sharpen, 3c widens the search) — **never** loosen
  `capital_efficiency`.
- **G2 — forward-fill validation:** any precision artifact (σ_center, σ-shape, refit fusion weights)
  promotes only after holdout validation + a measured MAE reduction on unseen settlements. Operator-ARM.
- **G3 — shadow byte-identity:** every shadow/candidate flag OFF == today (proves the OFF path is a no-op).
- **G4 — E2/W-EDGEPROOF LICENSE (EP-3+EP-4):** a structure band reaches `n≥200` ∧ proper-scores beat
  market ∧ `edge_lo95>0`. Operator-ARM. INSUFFICIENT/NO_EDGE → keep accruing / redirect.
- **G5 — canary live → DONE (EP-5):** the LICENSED structure's first live fills clear >51% after-cost at
  `n≥30` event-level forward fills, continuous, model-Brier < market-Brier. Operator-ARM. Fail → revert to
  shadow, dated NO_EDGE, redirect — do not loosen the gate.

**"Honest NO_EDGE at adequate n on a given structure" is a valid result that redirects to another
structure — never an excuse to stop the alpha path.** The search terminates only when the structure space
and the reachable precision are exhausted, which the audit and structure search have not begun to do.

---

## 5. ARCHITECTURE VERDICT PER LAYER + THE KEEP-LIST

### 5.1 Per-layer rebuild-vs-repair verdict (from `P3_architecture.md`)

| Layer | Verdict | K≪N kernel it collapses to | The plan node(s) it overturns |
|---|---|---|---|
| **L1 bin-belief / calibration** | **TARGETED_FIX** (provenance honesty only) | one bound producer whose provenance names its *actual* draw-σ (H1); σ-shape stays candidate | N5 DEAD as alpha lever (attacks non-live σ; tail-loosener); N6 NOT SHIPPED as UP-arm ceiling-raiser |
| **L2 candidate-gen** | **TARGETED_FIX** (1 constant → fitted boundary) | `T = max(1 step, k_fitted·σ)`, `k_fitted` = coverage radius ≈ today's 1.0 | N11/E3 widen NOT SHIPPED (widens toward a loss band); `k_eff` built only as #64 fitted-constant, default-identical |
| **L3 gates (admission)** | **PARTIAL_REBUILD** (collapse vocabulary; pure subtraction) | `capital_efficiency` byte-identical + shrink-only coverage; N licensing dialects → 1 verdict | **N7 UP arm KILLED**; bidirectional rewrite KILLED; H3 deletions KEEP |
| **L4 submit** | **TARGETED_FIX** (1 abort → re-decision) | one submit-mode authority under the K-spine; lane-stamp assert; tick de-dup | N9 (H4) **built but ARM-gated OFF** until a profitable admission exists |
| **L5 reconcile / settlement** | **TARGETED_FIX** (telemetry only) | existing mechanics + E1/E2/W-EDGEPROOF read-only grading harness (JSON, no DB authority) | E1/E2 KEEP as the settlement-honesty instrument; `edge_observation.py` QUARANTINED for this purpose |

**Net:** NO layer needs a GROUND_UP_REBUILD. One layer (L3) needs a PARTIAL_REBUILD that is entirely
*subtraction*. Four layers are TARGETED_FIX, three of those observability/provenance-honesty with zero
behavior change. This is a **calibration-honesty + vocabulary-collapse problem, plus a forecast-precision
alpha problem** — NOT a rebuild problem.

### 5.2 The KEEP-list (DO-NOT-TOUCH; from `P2_W-KEEP-SIMPLIFY.md`)

Touching any of these is out of scope; a change that needs to is a re-plan, not an edit.

- **K-SPINE — `capital_efficiency`** (`live_admission.py:87-126`): `(q_lcb − price)/price ≤ 0 → reject`.
  The honest arbiter. Never loosen; every fix targets the q_lcb that flows IN. **Capital_efficiency stays
  byte-identical.**
- **K-ARM — `arm_gate_coverage_blocks`** (`settlement_backward_coverage.py:228-259`): the
  verified-correct-before-live interlock; read unconditionally; over-claim-block semantics frozen.
- **K-COVERAGE-DOWN — `apply_settlement_coverage`** shrink-only (`:204-225`): one-sidedness IS the
  antibody; never make it bidirectional.
- **K-POINT — the point posterior `q` chain** (member resample → MAP-Platt #129 → posterior MODEL_ONLY →
  `bin_probability_settlement`): Σq=winners; the crown jewel. **The σ-shape fit may NOT ship as the UP
  arm's ceiling-raiser.**
- **K-TAIL-ZERO — the honest q_lcb≈0 on far-OTM / open-tail zero-support bins** (0/72 settled): law 4;
  keep the *effect* through the H3 vocabulary deletion (D6 guard).
- **K-DIRECTION — `direction_law` geometry** (`direction_law.py`): encodes WHERE settlement-backed edge
  lives (law 6); only the constant `k=1.0` is a deferred, gated, last-in-line fitted-constant exception.
- **K-LCB-CEILING — `q_lcb ≤ q_point`** + native-NO `1 − q_ucb_yes` complement: corrections may only
  LOWER; the structural guard against a UP arm admitting a below-market model.
- **K-ABSORBER — the B1/M5 submit-latch absorber + external-close reconcile** (`exchange_reconcile.py`):
  self-heals (cleared 06-14T01:06); the shared-wallet operator co-trading makes external closes EXPECTED.
- **K-NO-ELIGIBILITY — the profitable-era NO eligibility gate (#74) + market-anchor cap**
  (`event_reactor_adapter.py:7472`): close two settlement-proven loss classes.
- **K-INV37 — cross-DB discipline (ATTACH+SAVEPOINT) + the K1 DB split** (zeus-world / zeus-forecasts /
  zeus_trades).
- **K-SEMANTICS — time-semantics (#16) + per-city settlement-rounding preimage (#24):** the law-8
  metadata that makes any correct-bin claim meaningful; the precondition for the §3a audit and the §4
  grader.
- **K-SETTLEMENT-TRUTH — settlement is the only truth** (law 5): no in-sample promotion; walk-forward
  discipline; a dated settlement-proven verdict is the only DONE.
- **K-CI-HONESTY — σ never tightened below MC; `k_cov` never shrinks σ:** the guard that keeps H1's
  σ_center fix from over-tightening the *predictive* σ (the weather spread) while it corrects the *center*
  jitter.

---

## 6. CRITICAL PATH + ARM GATES — the ordered sequence to the first settlement-proven correct-bin alpha fill

Items in the same wave are parallelizable. Self-arming = byte-identical-live or read-only; ARM = operator
authorizes the live flip on the dated evidence artifact.

### Wave 0 — Honesty pass + instrument (parallel; all self-arming, zero live-behavior risk)
1. **H3** dead-gate deletions (H3a EMOS/allow-list, H3b C2/C3 import, H3c δ-penalty shadow) — G0; D6
   guard keeps the `coverage_unlicensed_tail` effect. *Self-arming.*
2. **H2** kill the UP-arm/isotonic + freeze K-ARM + keep σ-shape candidate — G0. *Self-arming* (a
   deletion of a never-built module + a promotion plan).
3. **H4-telemetry** submit-lane stamp (every receipt names its lane) — G0. *Self-arming.*
4. **W-EDGEPROOF / E1** the read-only edge-location query + grading harness (EP-1..EP-4 scaffolding) — G0.
   First run output: every ring cell INSUFFICIENT_DATA (n≈5). *Self-arming.* **Highest-leverage single
   node** — it is the gate every later promotion passes and the instrument that drives the §3c structure
   search.

### Wave 1 — Provenance honesty (shadow/candidate)
5. **H1-diagnostic** (K-L1-a): write `sigma_center_diagnostic.md` (why is the ring bound 0.032 when
   provenance says 3.0; is the 3.0 map even live for the ring). G0. *Self-arming.* **Mandatory prerequisite
   to H1-fix.**
6. **H1-fix** replace the `center_sigma_c=3.0` default with the settlement-fitted `σ_center`
   (`sigma_center_fit.json`, candidate), shadow column — G0, G3. *Self-arming in shadow.* Live promotion
   is **ARM** (G2 forward-fill + measured tail-bound sanity + operator sign-off) — and is a robustness
   improvement, NOT on the critical path to the first fill (F2: ring is unaffected).

### Wave 2 — The alpha path (the real work; parallel sub-tracks)
7. **3a data-source audit** (parallel, read-only, the moment Wave 0 ships) → `DATA_SOURCE_AUDIT.md` with
   the ranked correct-bin-corruption defects. Each source/city fix that changes belief is **ARM-gated**,
   validated by re-measuring peak-bin MAE on held-out settlements.
8. **3b fusion quality** (after/with 3a): refit weights where licensed, repurpose the σ-shape fit as an
   *independent point-q precision* lever (G2 forward-fill + MAE-reduction gate, **ARM**). Metric:
   peak-bin MAE below bin-width on some cell.
9. **3c structure search** via E1: search wider bins / shorter leads / high-skill cities for a cell with
   positive after-fee edge at adequate `n_eff`. Read-only; *self-arming*.
10. **3d fee-drag**: identify cells where gross edge > 0 > after-taker-fee edge; prepare the maker-rest
    lane (reuses H4 resolver). No live effect until a cell is LICENSED.

### Wave 3 — Gate (settlement accrual; the rate-limiter is settlement cadence, not code)
11. **G1 / EP-1** structure backtest: does any structure carry positive after-fee edge at `n≥200`?
    - Collapses to NO_EDGE on the tried structures → dated, settlement-proven verdict → **redirect** to
      another structure (back to Wave 2; sharpen further / widen the search). Not a terminus.
    - Persists positive on a structure → proceed.
12. **G4 / EP-3+EP-4 LICENSE**: a structure band reaches `n≥200` ∧ model beats market on log-score+Brier
    ∧ `edge_lo95>0`. **ARM.**

### Wave 4 — Live promotion (only after a structure is LICENSED)
13. **H4-submit ARM ON** (the re-decision resolver) — now safe, because a real profitable candidate exists
    on the licensed structure. **ARM, sequencing-gated** (never before a licensed structure).
14. **3d maker-rest** / **E3 `k_eff` widen** — ship LAST, on the LICENSED cell only, default
    byte-identical. **ARM.**
15. live candidate on the LICENSED structure ADMITS (`capital_efficiency`, honest) → SUBMITS (H4 survives
    the tick) → FILLS → SETTLES.

### Wave 5 — DONE
16. **G5 / EP-5**: the LICENSED structure's live fills clear >51% after-cost at `n≥30` event-level forward
    fills, continuous, model-Brier < market-Brier. **This repeating is DONE. A single fill is not.**
    **ARM.** Fail → revert to shadow, dated NO_EDGE on that structure, redirect.

**Operator-gated steps (ARM):** H1-fix→live, every 3a source/city belief change, 3b fusion-weight /
σ-shape promotion, G4 LICENSE, H4-submit ARM ON, 3d/E3 widen, G5 DONE. **Self-arming (no operator word):**
all of Wave 0, H1-diagnostic + H1-shadow, E1/EP scaffolding, the 3c read-only structure search.

**The rate-limiter is settlement cadence (law 5, unfabricatable), not code.** Wave 0/1 land in days; the
alpha path (Wave 2) is real research; the LICENSE (G4) cannot fire until a structure accrues `n≥200`
distinct settled events that beat the market — compressible only by fitting/grading on the 7,029 existing
VERIFIED settlements, never by manufacturing settlement.

---

## 7. OPEN DECISIONS FOR THE OPERATOR

1. **Reach of the alpha path before re-evaluation.** The §3 work is open-ended research (audit + fusion +
   structure search). Set the checkpoint: after the data-source audit (3a) lands and EP-1 runs on the
   widest reachable structure set, do we (a) commit to 3b fusion refits, (b) keep searching structure
   (3c), or (c) accept a dated, settlement-proven NO_EDGE-across-tried-structures and pause the live
   ambition while continuing the audit? RULE 1 says keep fixing the forecast; the operator sets the
   horizon and budget.

2. **σ-shape (`sigma_scale_fit.json`) status.** Confirm it stays `candidate=true`, OFF the live path, and
   may be promoted later ONLY as an independent point-q precision improvement gated on a measured peak-bin
   MAE reduction — never as the (killed) UP-arm ceiling-raiser. Authorize this framing.

3. **H1-fix live promotion (provenance honesty).** It is a robustness improvement, not on the critical
   path (F2: ring unaffected). Promote `sigma_center_fit.json` to live after G2 + the tail-bound sanity,
   or leave it shadow-only as a diagnostic? (Lean: shadow until the K-L1-a diagnostic confirms whether the
   3.0 map is even live for any cell.)

4. **N_eff floor for LICENSE.** §4.3 sets `N_eff ≥ 200` (a ~5¢ after-fee edge bar). If the operator's
   minimum-worth-trading edge is 3¢, the floor is ~618 and most structures stay INSUFFICIENT for many
   months. Confirm the edge bar (5¢ vs 3¢) so the gate's wait is the intended one.

5. **Maker-rest fee-drag (3d).** Confirm this is the honest selection of the lower-cost executable lane on
   a gross-positive licensed cell — NOT a maker-only mandate or throttle (no-caps law). Authorize it as
   gated by the same W-EDGEPROOF LICENSE as everything else.

6. **The honest expected outcome, stated up front.** Per `edge_existence_decisive.md` and every settlement
   join: there is no large suppressed alpha pool; the most probable near-term terminus of the alpha path
   is a dated, settlement-proven "NO_EDGE on the tried structures at current precision" that **redirects**
   the audit/fusion/structure work — not a triumphant fill. The plan is engineered so that verdict is a
   first-class, dated, numeric output that points at the next lever (sharper sources/fusion, a different
   structure), never a stand-down. Confirm this is the accepted definition of progress between now and the
   first settlement-proven fill.

*End of implementation plan. Plan-only; no production code or daemon changed. Every empirical claim cited
to file:line, artifact, or query+counts established in the cited evidence docs.*
