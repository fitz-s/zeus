# P1 Strategy Scoring — Independent Adjudication

**Date:** 2026-06-14
**Scorer:** Independent adjudicator
**Lens:** K<<N compliance + evidence-grounding + foundation-first (Law 8)

---

## Scores

| Strategy | Score | Verdict |
|---|---|---|
| S0 Foundation | 62 | Correct mechanism hypothesis, unresolved contradiction with settlement consensus |
| S1 Minimal | 82 | Only strategy with signed, reproducible Brier backtest; root cause is structural |
| S2 Calibration | 74 | Best epistemic discipline; slowest path; build-first mildly violates K<<N collapse default |
| S3 K-Cut | 78 | Cleanest gate enumeration and K=5 design; names hidden 0.01 trade_score gate |
| S4 Edge-Location | 80 | Largest settlement join; bin-kind split surfaces mass redistribution; fix already built |
| S5 Gate-Implication | 76 | Cleanest null result; names condemned-incumbent / dead-replacement pair |

**Overall winner: S1 (82). Best complement: S4 (80).**

---

## S0 — Foundation (62)

Identifies a plausible mechanism (anchor_sigma_c=3.0°C hardcoded in
replacement_forecast_materializer.py:125 expanding bootstrap to crush ring-bin q_lcb) and
traces it to file:line. The simulation showing q_lcb sensitivity to center-sigma is
reproducible. Proposed 3-change set (fit center-sigma, collapse twin q_lcb authority,
live-eligible read semantics) is lean and K<<N.

However S0's headline finding — 83% of 116 matched winning-bin observations carry
q_lcb≥0.03 — is derived from forecast_posteriors, and the near-mode ring population it
claims to identify (q_lcb crushed to 0.004-0.030 on bins that actually won) is not
reproduced by three sibling strategies running the receipt→settlement join. S2/S3/S4/S5
all find cheap-tail q_lcb≈0 is HONEST (0/72, 0/453 settlement); S0 does not address the
cheap-vs-ring population split explicitly. The mechanism is worth testing; the evidence
base has an unresolved tension.

Best single idea: C (live-eligible read semantics) — reactor reads latest live-eligible
fused posterior rather than reverting to soft-anchor on freshest-cycle capture timing
mismatch. Low risk, independent of the center-sigma hypothesis, verifiable.

---

## S1 — Minimal (82)

Only strategy that ran a direct settlement calibration backtest (119 settled families,
1309 graded bins, forecast_posteriors→settlement_outcomes, 100% win-bin match,
reproducible at /tmp/bt_clean/bt3.py). Produces the only signed Brier comparison:
Brier(q_lcb)=0.0802 > Brier(q_point)=0.0712. R/E table (q_lcb∈[0.06,0.10] realises
R/E=3.96) quantifies the under-coverage without ambiguity. Root cause (5th-percentile
nonparametric bootstrap on n=51 multinomial cell is a known severe under-estimator) is
structural: no gate tuning can fix it. K=4 change set is tight.

Main penalty: proposed fix (settlement-calibrated bidirectional bound reusing
_isotonic_realized_rate) requires a new calibration authority and is more complex to
validate than a deletion program. n=119 is thin. S1's "mid bins 3-4× suppressed" finding
addresses a different population from S2/S3/S4's cheap-tail analysis — S1 is correct that
these are not the same population, but it does not reconcile them explicitly. The delete-
coverage_unlicensed_tail-before-K1-proven step is the one ordering risk.

Best single idea: signed Brier R/E harness as continuous settlement-grading and licensing
authority — weekly on new settled families, license a q_lcb band only when R/E lower CI >
1.0, replacing both vocabulary systems with one evidence-based gate.

---

## S2 — Calibration (74)

S2's decisive contribution: event-level de-duplication of no_trade_regret_events
(discovering ~40× snapshot-repeat inflation; NYC 2026-06-04 has 42 rows for one bin).
This is methodologically load-bearing for every calibration number in the project. S2
correctly finds cheap buy_yes settles at ~4.88% (41 event-level obs), honest-no-edge, and
identifies near-center band as the only candidate for honest disagreement. It honestly
admits this rests on 15 event-level observations — "far too thin to license."

The "build a reliability harness first" pick is epistemically correct but the slowest
path. The operator contract specifies DONE = continuous settlement win-rate; S2's proposal
defers that determination indefinitely. K<<N compliance is moderate: proposing a new
harness structure (a BUILD) violates the collapse-default. N_eff and James-Stein correctly
ruled out as shadow-only.

Best single idea: event-level de-duplication discipline — one row per (city, target_date,
bin) in every calibration and regret surface, enforced by construction.

---

## S3 — K-Cut (78)

S3 enumerates the full gate-gauntlet from live source (N≈8 admission + 4 submit gates,
each with file:line) and draws the K=5 collapse explicitly. It names the covert flat 0.01
trade_score penalty (event_reactor_adapter.py:13737) as a hidden Kth gate — at price
0.001-0.01 the flat deduction consumes all edge; no other strategy surfaces this. The
settlement evidence uses sigma_scale_fit.json's own calibration ratios (verified artifact,
not ad-hoc join): near-center 1.00-1.15, tail 0.296/0.138. The BH/FDR-condemned-while-
live / C2/C3-replacement-dead finding is clearly stated. K<<N compliance is highest in
the set: S3 proposes only deletions and collapses, no new builds.

Main limitation: S3 defers the q_lcb repair question entirely. The K=5 gauntlet
redesign is necessary but not sufficient — if q_lcb on ring bins stays below price after
the K=5 simplification, the system still produces no fills. S3's causal chain ends at
"metadata correctness + direction-law + continuous executability" without proving the
near-center ring edge survives the 1¢ fee at Zeus's throughput.

Best single idea: full gate-gauntlet K=5 design, specifically naming the covert 0.01
trade_score penalty as a hidden gate requiring cost-proportional replacement.

---

## S4 — Edge-Location (80)

Runs the largest settlement join (62,874 receipts, 427 distinct settleable instruments).
0/72 cheap buy_yes finding (including 34 that passed q_lcb>price) is the most concrete
single datum. The bin-kind breakdown (exact / or_higher / or_below) is new and structural:
model under-confident on exact ring bins (realized 0.108 vs q 0.093) and 5× over-confident
on open-tail kinds. This identifies the mass redistribution problem more precisely than any
other strategy — the predictive Normal bleeds mass into open-tail bins and under-fills the
ring, explaining why q_lcb on the ring fails capital_efficiency.

Proposed fix (promote sigma_scale_fit.json σ-fit + uniform-mixture to live primary q path,
wired into replacement_forecast_materializer.py:1040-1062) is the only fix that is already
partially built (the fit exists, is operator-gated, has its own _meta.promotion clause) and
involves no new gates. K<<N: high — tail self-eliminates rather than requiring a new gate.
Kill criterion (fee kills the ring edge → Alternative C) named honestly but the fallback
(trade the +0.067 NO_price-0.8-0.9 pocket) is prohibited by operator law #4; the correct
fallback is accumulate more settled ring evidence.

Best single idea: bin-kind (exact vs or_higher vs or_below) reliability split as a
mandatory calibration dimension, revealing structural mass redistribution and pointing at
the sigma_scale_fit promotion as the concrete fix.

---

## S5 — Gate-Implication (76)

Answers its mandate precisely: 0/1619 proof_accepted buy_yes settlement loss proves the
gate layer has never blocked a winner; every admitted buy_yes candidate was a loser. The
gate layer is the only correct actor. Most actionable finding: the three-authority problem
(condemned BH/FDR live, C2/C3 replacement dead producing NULL telemetry because
FDR_REJECTED early-return skips the shadow-stamp call, honest admission gates beside both)
is the clearest K<<N violation in the codebase. The vocabulary-A-vs-vocabulary-B
unification (delete source-string allow-list, keep settlement-verdict authority) is the
cleanest single simplification across all strategies.

Penalised: S5 defers the positive question (where does alpha come from?) and recommends
keeping BH/FDR as condemned-interim indefinitely with no promotion timeline. If the
foundation fix fails to move q_lcb above price on ring bins, the gate changes in S5
produce no additional trades.

Best single idea: delete the dead C2/C3 live-cycle call (module stays, live-path import
goes) and add explicit # CONDEMNED-INTERIM provenance note on BH/FDR naming the
foundation-fix as the unblock condition for the C2/C3 promotion.

---

## Synthesised minimum-viable plan

1. S1/K1 (calibrate q_lcb bidirectionally, shadow-first) — foundation fix
2. S3/K5 + S5/B3 in parallel (submit re-admit on mode-flip + gate-mass collapse)
3. S4's sigma_scale_fit promotion — once K1 shadow evidence is in
4. S2's event-level de-duplication discipline — cross-cutting hygiene for all evidence
5. S5/A3 (licensing-vocabulary collapse) — cleanup, deferred to post-foundation

Honest negative: no strategy proves the ring-bin edge survives the 1¢ fee at Zeus's
throughput. That verification must happen in shadow before DONE by the operator contract.
