# Alpha First-Principles Adjudication — Forecast Accuracy vs Engine Design (2026-07-02, CORRECTED)

**Mode:** Read-only investigation. Local 7-agent empirical workflow over live DBs (run
`wf_0e1d5c56-1fc`; decisive claims re-measured by 2 independent adversarial verifiers each) + a
two-round external frontier consult (theory; live branch `@5230f651` + working-tree gist). Raw
answers: `/tmp/cgc_answer_REQ-20260702-105230-86d0af.txt`, `/tmp/cgc_answer_followup_branch.txt`.

**CORRECTION (operator-caught, verified):** the first version of this report led with a
"market price decisively beats q" log-loss comparison built on `no_trade_regret_events` deduped to
the freshest row per event. That substrate is outcome-leaked: **97% of the post-06-15 graded events
were created ON or AFTER the target date** — the price leg had already watched the day's observed
temperatures — and freshest-row dedup systematically samples the most outcome-informed price snapshot
(W1: 15% on/after target date, 67% near-converged cost). This is the same marshalled-hindsight error
`settlement_station_edge.md` documented on 06-14. **Every market-relative claim (either direction:
"market beats us" AND "we beat market", including the June −1.9¢ adverse-selection figure on the same
substrate) is hereby retracted as UNMEASURABLE on current data.** A fair test requires q and price
sampled at the same fixed pre-observation decision time; that co-temporal capture does not exist
(gap open since 06-14). Nothing in this correction rescues the P&L — it relocates the diagnosis.

---

## VERDICT

**The only meaningful accuracy measurement is forecast vs actual settled temperature, and on that
measurement the forecast is improving and has just touched the usable band for the first time.
The money loss is engine design failure, not proof of forecast inferiority.** The engine converted a
~1-bin-error forecast into deep-favorite positions needing a 65.5% dollar-weighted win rate while
delivering 51.4%; it never exited a losing position; its resting quotes were picked off and its
crossing orders paid +15.6¢ over contemporaneous mid; and its measurement organs (counterfactual
grading, fee provenance, PnL completion, price tape) are dead, so it cannot currently grade its own
fixes. Market-relative alpha is an open empirical question that CANNOT be answered until decision-time
co-temporal capture exists — stop asserting it in either direction.

---

## 1. The only valid metric: forecast vs settled temperature

| era | n | center MAE (bin-widths) | exact-bin hit | source |
|---|---|---|---|---|
| pre σ-floor (≤06-09) | 124 | 1.32 | — (z-std 1.93) | overconfidence_root.md |
| baseline 06-08..14 | 230 | **1.30** (median 1.00) | **24.3%** (chance 11.1%) | edge_existence_decisive.md |
| 07-01 settled day (post sigma/station fixes, PRE EMOS-affine) | 28 | **0.857** | **53.6%** [35.8, 70.5] | fresh, this run |

- The 07-01 point is n=28, one day, hot-city mix, argmax method (not strictly comparable) — but it is
  the first measurement inside the band first-principles requires for exact-bin markets
  (≈0.6–0.8 bin-widths; below ~0.7 an exact-bin hit near ~50% becomes mechanically available).
- **EMOS center-affine has ZERO settlement-graded evidence**: applied to 0/697 lead-1 posteriors of
  the only settled date (artifact populated intraday 07-01); first gradeable settles are 07-02+.
  All headline gains in the emos_upgrade docs are walk-forward replays, methodologically clean but
  not yet live-graded.
- σ honesty trajectory (z-std 1.93 → 1.35 post-floor) plus the center trajectory above is the
  entire real product of the last three weeks of work. Keep grading exactly this, daily, per city.

## 2. Engine design failures (all measured against settlement reality, no price leakage)

### 2a. Position economics: bought favorites its own error bars cannot support  [VERIFIED ×2, to the cent]
88 reconstructable settled fills 06-06→07-02: after-cost **−$132.28**, WR 50.0%.
- 89% of loss dollars = deep-favorite buy_no (entry ≥0.50) carrying mean decision q=0.778 that
  realized **54.1%** — a +23.7 pp claim-vs-reality gap ON FILLS, while the population q is roughly
  calibrated. That is winner's curse measured against reality (the fills are the subset where the
  claim was most wrong), the same pathology `selection_curse_bound`'s own basis doc measured
  (admitted buy_no claims 0.83 → realizes 0.69).
- Dollar-weighted breakeven 65.5% vs realized 51.4% = the engine's admission math licensed prices the
  forecast's own settled error distribution could never clear. First principles: with center error
  ~1 bin, a claim of q≈0.78 against a specific adjacent bin is not licensable; the fitted error
  distribution itself bounds the maximum honest claim.
- buy_yes 1/13 wins under an identity exemption in selection_curse_bound — "benign" is
  sample-starved, not proven.

### 2b. Exits never fire
`exit_reason='SETTLEMENT'` on **all 114** settled outcome rows; EXIT_LOSS = 0/44 losses. Every
losing high-confidence buy_no rode through observation day to settlement even as station observations
(the day0 feed the system ingests) progressively falsified the belief. The held-position
re-decision lane is effectively inert as a loss-cutter — engine defect, independent of forecast skill.

### 2c. Execution donates more than any plausible edge
- Maker resting fills settle to **29.4%** WR (5/17) vs taker crossing fills **58.1%** (25/43):
  resting quotes are not repriced on new information and get hit adversely (pickoff direction
  confirmed pre-fill, −6.2¢ n.s.). Maker orders rest a median 10.6 min in books where day-of
  information arrives continuously.
- Taker fills execute **+15.6¢ above contemporaneous mid** (n=58, CI [+8.7,+22.6]) — thin-book spread
  crossing. At current forecast precision no signal clears a 15¢ execution toll; this dwarfs the ~0–1¢
  fee question.
- Post-fill mid-to-mid drift ≈ flat: the damage is paid AT the fill, not after.

### 2d. Redundancy census (22 transforms; the operator's collapse-law applies)
- Three independent estimators of the same dispersion stacked: sigma-scale k=0.70 (sharpens) vs
  settlement σ-floor (median 1.21 °C) vs uniform mixture w=0.146 (widens) — partially canceling.
  One fitted dispersion object should exist.
- Kelly `ci_width` haircut double-counts the q_lcb gap the stake already prices; the unifier INV-40
  exists in code, env-gated OFF in the live plist.
- Dead code masquerading as protection: `arm_gate_coverage_blocks` (0 callers), `floor_steps` (absent
  key → 0), concentration ceiling (`max_single_position_pct=0.0`), bias_decay haircut (excluded for
  the live q_source). Live `kelly_multiplier`=0.02.
- 18 of 22 transforms are selection-changing; none of them encodes the one correction that reality
  demands (fill-conditioned claim deflation vs settled outcomes — only selection_curse_bound does,
  buy_no-only, tighten-only, fit on admissions rather than fills).

### 2e. Measurement organs are dead (Priority 0 — nothing can be proven until fixed)
1. `no_trade_regret_events.would_have_won` grading stopped 2026-06-16T11:19Z — sole caller of
   `enrich_after_settlement` (the `day0_shadow_enrichment` cron) deleted in commit `492588f3` and
   never replaced; 72,712 ungraded rows since.
2. Fee provenance absent: `venue_trade_facts.fee_paid_micro` NULL on all 479 fills.
3. `outcome_fact.pnl` silently zeroed on 37/114 rows (32%) regardless of outcome; 26/114 settled
   positions have no `trade_decisions` coverage at all.
4. **No decision-time co-temporal price capture** (multi-bin, bin-labeled, fixed pre-observation
   cadence, retained ≥30d alongside posteriors). This is the single blocker that has kept every
   market-relative question — timing alpha, station-vs-retail gap, adverse selection — unanswerable
   since 06-14. Until it exists, no beat-market claim (positive or negative) is admissible evidence.

### 2f. Day0 lane status
Code-native day0 lane has produced zero filled trades ever (7 candidates, all voided, one day).
The absorbing-boundary mechanism (observed running extreme truncates q) is correctly implemented and
fail-closed; 19 certificates exist (07-01/02 only). In the parseable rejected subset (n=838), zero
positive-LCB candidates were wrongly rejected — no measured missed free money. The lane is unproven,
not refuted; it is also the lane where forecast error mechanically collapses.

## 3. First-principles engine (the K << N target)

The forecast's settled-error distribution is the product; the engine's only job is to never claim
beyond it and never donate execution value. Concretely:

1. **One accuracy authority:** per (city, metric, lead) walk-forward settled-error distribution of
   the served center (the §1 table, maintained daily). Bin claims derive from it mechanically;
   maximum licensable q per bin distance falls out of the same object. This replaces the σ
   triple-stack as ONE fitted dispersion estimator.
2. **One claim-deflation authority (fill-conditioned, vs reality):** extend selection_curse_bound's
   logic to both sides and fit on actual fills (admissions only for censoring) — the measured
   +23.7 pp fill gap is the quantity it must close. Its output is the only q that admission, sizing,
   taker-cross, and exit may consume.
3. **One admission rule:** deflated q_lcb > all-in executable cost (fee-true, depth-walked) + risk
   premium. Delete the redundant taker-quality floor cluster into this single comparison.
4. **Exits that fire:** on observation day the same absorbing-boundary evidence that gates entries
   must re-price held positions; a held claim falsified by the running extreme exits mechanically.
   (Today: 0 early exits ever.)
5. **Execution that stops donating:** resting quotes cancel/reprice on new-cycle or new-observation
   arrival (pickoff defense); crossing pays ≤ a fixed fraction of deflated edge, never 15¢ over mid.
6. **One uncertainty budget:** enable INV-40, delete the ci_width double-count and the dead gates.
7. **Measurement organs** (§2e) restored first; add the co-temporal capture so market-relative
   questions become answerable at all.

## 4. What remains genuinely open (do not assert; measure)

- Whether the improving forecast (if 0.86 bins / 53.6% holds at n≥200) converts to after-cost EV at
  executable prices — decidable only with fee-true PnL + co-temporal capture.
- Whether market-relative alpha exists at any lead — unmeasurable until capture exists; the retracted
  comparisons must not be cited in either direction.
- Day0 nowcast EV — needs ~150 settled events through the now-instrumented lane.
- buy_yes viability — fail-closed until a licensed cell proves it.

## Verification record

Fill autopsy: 2/2 adversarial verifiers reproduced to the cent (one sub-finding corrected:
`settlement_guard_report` grades via the zeus-world/forecasts lineage — 73/75 gradeable — not broken;
73-vs-88 is scope). The retracted q-vs-price section: both verifiers reproduced the *numbers* but the
substrate itself is outcome-leaked (97% of W2 events created on/after target date; operator-caught,
timing re-measured this session) — numbers correct, comparison meaningless. Census/microstructure/
day0/EMOS findings are single-pass code-and-DB facts.
