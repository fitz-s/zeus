# Root cause: first live fill bought a far-tail YES our own forecast contradicts (Milan 24°C, 2026-06-10T02:58Z)

Created: 2026-06-10. Authority basis: incident command `0b5c305e26524042`, event
`edli_evt_0d2ed6249b5ce28fd48dcc1fcc2592e4485f0b82ba57d092071f538bfb3b42b9`,
certificates in `state/zeus-world.db::decision_certificates`, posterior 929 in
`state/zeus-forecasts.db::forecast_posteriors`.

## 0. The order (facts, from venue_commands + edli_live_order_events)

- BUY 66.25 YES "Will the highest temperature in Milan be 24°C on June 11?" @ 0.016,
  `order_type=FOK_LIMIT`, `post_only=false`, state FILLED (02:58:29Z).
- Book at pre-submit revalidation: bid 0.009 / ask 0.016 → relative spread
  (ask−bid)/mid = 0.007/0.0125 = **56%**. We paid the full ask as an instant taker.
- Our posterior (posterior_id 929, `fused_normal_direct`, FUSED_NORMAL_PARTIAL):
  µ* = anchor_value_c = **26.42°C**, u0r_fusion.predictive_sigma_c = **1.263°C**.
  The traded bin center is 24°C — **2.42°C ≈ 1.9σ below our own center**.

## 1. The decision trace (what q_lcb / c_95 / trade_score actually were)

From `ActionableTradeCertificate:9018e1d6d8c05edb6f7fb51f` (full opportunity book,
22 candidates) and `BeliefCertificate:29f7c839a52c8e928ce9c43d`:

| bin (YES) | q (fused) | q_lcb_5pct | exec price | trade_score | verdict |
|---|---|---|---|---|---|
| 23°C | 0.0706 | **0.0706 (= q, zero discount)** | 0.0042 | 0.0554 | admitted #2 |
| **24°C** | 0.0927 | **0.0927 (= q, zero discount)** | 0.0168 | **0.0649** | **admitted #1 → traded** |
| 25°C | 0.1108 | 0.1108 (= q) | 0.177 | 0 | capital-efficiency reject |
| 26°C (≈µ*) | 0.1207 | **1.71e-05** | 0.442 | 0 | `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV: ev=-0.999961` |
| 27°C | 0.1198 | 1.71e-05 | 0.351 | 0 | same |
| 28..31, ≤21 | 0.05-0.11 | 1.71e-05 | — | 0 | same |
| all buy_no | 0.86-0.95 | 0.0 | — | 0 | same |

Selected: 24°C buy_yes. `c_fee_adjusted=0.0167872`, `c_cost_95pct=0.0177872`,
`p_fill_lcb=0.99972` (visible-depth taker coverage), `kelly_size_usd=1.077`
(bankroll 904 × 1/16 Kelly), `action_score=0.0648658`. FDR family passed with the
24°C hypothesis sole survivor.

**Why 24°C won the family rank**: the only two candidates with positive
`trade_score = p_fill × min(q_lcb − c95 − 0.01, q − c_stress − 0.01)` were the two
far-LEFT-tail YES bins, because they were the only bins whose q_lcb was NOT crushed
(see §2). The objective therefore peaked exactly at max(q_lcb − price) = the maximum
model-vs-market disagreement — which, with an unlicensed q_lcb, is where the model is
most likely wrong. The forecast-adjacent bin (26°C) was structurally UNSELECTABLE:
its q_lcb was 1.71e-05.

## 2. Design failure 1 (root of violation A): q and q_lcb come from two DIFFERENT probability measures

`posterior 929` provenance:

- `q_json` (the point posterior) = the **fused** Normal: IFS9 anchor 26.42 (weight 0.8)
  with settlement sigma floor 3.26°C applied → a deliberately flat, fat distribution
  (every bin 21..31 gets 5-12%).
- `q_lcb_json` = **EMPTY** → `_replacement_yes_lcb_for_bin`
  (event_reactor_adapter.py:7062-7095) falls back to **Wilson over the RAW AIFS
  member votes** (`provenance_json.aifs_probabilities`), clamped to `min(q, wilson)`.
- The raw AIFS members cluster at 23-25°C (23: 31.1%, **24: 42.8%**, 25: 23.4%) and
  put ~0.097% everywhere else — the AIFS ensemble disagrees with the IFS9 anchor by
  ~2.4°C.

Consequences, mechanical and exact:

- Bins 23/24/25: Wilson(votes) > fused q → `min` returns **q itself** →
  `q_lcb == q` to the last digit (zero uncertainty discount; the LCB-consistency
  gate `q_lcb ≤ q` is satisfied degenerately).
- Bins 26+ and ≤21: Wilson(0.097% of 51) = 1.71e-05 (the identical value on 7 bins
  in the certificate) → q_lcb ≈ 0 **at and above our own fused center**.

So the "robust lower bound" is anti-correlated with the fused center whenever the
anchor and the members disagree: it is maximal on the side the fusion itself rejected.
`trade_score`'s argmax then MUST buy the member-vote side against our own µ*. The
24°C YES at 1.6¢ — the classic overpriced Polymarket longshot — got a full-confidence
9.3% lower bound, and the market (1.25-1.6¢) was plausibly more right than us.

This is a Fitz #4 provenance hole: the number was *called* q_lcb_5pct downstream, but
its provenance is "vote share of a different distribution", carried as
`q_lcb_calibration_source=FORECAST_BOOTSTRAP` — unlicensed by any settlement record.

### Why the existing defenses did not fire (all four were written; none was in the path)

1. **q_lcb_settlement_coverage_gate** (flag is ON, `settings.json:edli_v1`): the
   Milan/high/JJA band has <30 settled observations →
   `settlement_backward_coverage_check` returns **INSUFFICIENT_DATA → q_lcb
   unchanged** (event_reactor_adapter.py:9540, settlement_backward_coverage min_n=30).
   Fail-OPEN exactly where the model is least proven — the gate can only ever fire
   on bands that already have settlement history, i.e. never on a fresh tail.
2. **LIVE_DIRECTION_WIN_RATE_FLOOR = 0.51** (live_admission.py:15, "lottery legs are
   not live-money entries"): computed and recorded on the receipt
   (`live_win_rate_admissible: false` for every candidate in this book!) but it is
   **NOT part of `CandidateEvaluation.admitted`** (candidate_evaluation.py:137-148)
   — observe-only, gates nothing.
3. **replacement_qlcb_settlement_sigma_floor** (QLCB_HONESTY FIX-C): flag OFF; and
   N(26.42, 3.26) mass on the 24°C preimage ≈ 0.09 ≈ q anyway — it would not have
   blocked this shape (it guards overconfident tight clusters, not measure mismatch).
4. **Direction doctrine (buy_yes ⟺ bin≈forecast)**: existed only as operator law.
   Zero code expressed it. `grep -r DIRECTION_LAW src/` → nothing (until this fix).

## 3. Design failure 2 (root of violation B): hybrid evaluation/execution semantics + every "edge" lane routes to taker

- **Evaluation is taker-priced**: c_95 = depth-walked native ask + taker fee
  (`_execution_price_from_snapshot`, S1 cost curve), multiplied by `p_fill_lcb`
  derived from **visible depth coverage** (0.9997 here) — a crossing-cost with a
  resting-fill factor: a maker system being evaluated as a taker.
- **Execution since 355f2f3a73 is nominally maker-first** and the cert layer is
  actually correct for maker: `_branch_limit_price` maker branch =
  `min(best_bid + tick, reservation)` with `post_only=True` GTC
  (decision_kernel/certificates/execution.py:533-603), and the executor pre-venue
  check rejects a passive intent that would cross the snapshot book
  (executor.py:1806-1815). **The maker lane did not malfunction — it was bypassed.**
- **What actually fired**: `live_canary_enabled=true` → `canary_force_taker=true` →
  `_select_edli_order_mode` §7: `post_cross_edge = q_live − ask − fee =
  0.0927 − 0.016 ≈ 0.075 ≥ 0.05` floor → **FORCE TAKER**. The canary's edge floor is
  measured with the SAME unlicensed q from §2 — the more wrong the model, the more
  the canary insists on crossing. Order spec became FOK_LIMIT/post_only=false;
  `_branch_limit_price` TAKER branch lawfully priced the full ask
  (`tick_round_up(0.016) ≤ reservation 0.0168`).
- **The governor would have forced taker anyway**: `maker_or_taker` returns TAKER
  when snapshot depth < `taker_min_depth_micro` ($50) — a thin tail book *forces*
  crossing. Semantic inversion at a module boundary: "book too thin to rest in"
  (inventory-risk reasoning) became "therefore cross it", i.e. pay the full spread
  precisely where the spread is widest.
- **No spread participation guard existed anywhere**: nothing in the mode selector,
  cert builder, or executor measured (ask−bid)/mid. 56% relative spread did not
  appear in any gate.
- Note on `compute_native_limit_price` (semantic_types.py:197): the formula
  `min(held_prob, native_ask) − offset` does collapse to ≈ask when q ≫ ask, but that
  path (executor.py:1561 legacy planner) was NOT the incident's pricing path — the
  incident limit came from `_branch_limit_price` TAKER. The collapse remains real on
  the legacy lane and is subsumed by the same category fix (taker only via explicit,
  spread-guarded lane).

### Anomaly flagged (separate, not fixed here)

`QuoteFeasibilityCertificate:70d98b7a` recorded `best_bid=0.32 / best_ask=0.34` for
the 24°C token while pricing `execution_price=0.0167872` off ask 0.016 and the fresh
witness saw 0.009/0.016. The cert's bid/ask fields appear sourced from a different
row (family favorite?). Mode selection consumed those numbers as `quote_payload`
bid/ask — provenance bug worth its own audit (it did not change this incident's
outcome: the canary lane decided before the EV boundary mattered).

## 4. Structural decisions (K=2, not N=2 bugs)

- **K1 — confidence must carry a license, and direction doctrine must be code.**
  A lower bound born from a different measure than the posterior, unlicensed by any
  settlement record, was allowed to (a) overrule the market 6x in a tail and
  (b) select a bin 1.9σ from our own center. Fix A makes far-tail YES (and
  near-center NO) *unconstructable* at candidate admission; Fix B makes unlicensed
  longshot disagreement *unconstructable* regardless of direction.
- **K2 — one mode semantics for evaluation and execution.** Evaluate maker
  candidates with maker economics (resting limit, fill probability, adverse-selection
  haircut), taker candidates with taker economics (crossing cost), choose by the
  better EV, and make crossing an explicit lane that a wide spread forbids
  unconditionally (canary and governor included).

## 5. Fixes implemented (category-killing, staged commits on fix/opportunity-book-selector)

### FIX A — direction law as code (`DIRECTION_LAW_BIN_FORECAST_MISMATCH`)

`src/strategy/live_inference/direction_law.py` (new, pure) +
wiring in `_generate_candidate_proofs` (the single proof-construction seam all live
entry candidates pass through):

- Forecast center µ*: replacement path → `provenance_json.anchor_value_c`; sigma →
  `provenance_json.u0r_fusion.predictive_sigma_c`. Threaded through the
  `_live_yes_probabilities` evidence dict. Legacy/canonical rows without fusion →
  fall back to the q-distribution mean over the family bins (computed in bin-native
  unit); sigma fallback is None (strictly conservative threshold = 1 settlement step).
- Distance: 0 if µ* lies inside [low, high]; else distance to the NEAREST present
  bound (open-ended bins use their single bound). °F bins convert µ*/σ from °C.
- Threshold `T = max(1 × settlement_step, k × predictive_sigma)` with k=1.0
  (settlement_step: 1°C / 2°F per Bin width law). Incident: |24−26.42| = 2.42 >
  T = max(1, 1.263) → rejected.
- Law: `buy_yes` admissible iff distance ≤ T; `buy_no` admissible iff distance > T
  (the doctrine's other half; live buy_no already trades forecast-distant bins —
  verified the incident book's far buy_no candidates remain admissible under the law).
- Rejection is deterministic: `missing_reason=DIRECTION_LAW_BIN_FORECAST_MISMATCH:...`,
  `trade_score=0`, `passed_prefilter=False` — the candidate can neither rank nor
  enter FDR, same mechanism as the capital-efficiency rejection.

### FIX B — tail calibration fail-closed (`COVERAGE_UNLICENSED_TAIL`)

`live_admission.py::coverage_unlicensed_tail_rejection_reason` + same-seam wiring:

- Scope: candidates whose fee-adjusted price < 0.05 (longshot) AND q_lcb > 2 × price
  (material disagreement with the market) AND `q_lcb_calibration_source` NOT in
  {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC} (the settlement-licensed vocabulary already
  defined by `qlcb_provenance.CALIBRATION_SOURCES`).
- Action: reject (not shrink). Shrinking q_lcb to the market-implied probability
  yields conservative-EV ≤ 0 → the capital-efficiency gate rejects anyway; an
  explicit reason is the same outcome with honest provenance. Near-center trades
  (price ≥ 0.05) are untouched by construction; a licensed tail (settlement-graded
  band) trades again automatically once `SETTLEMENT_ISOTONIC`/`EMOS_ANALYTIC`
  provenance arrives — exactly the INSUFFICIENT_DATA → fail-CLOSED inversion the
  K3 gate lacked. Direction-agnostic (a cheap unlicensed NO is the same category).

### FIX C — mode-consistent EV + spread participation guard + maker placement provenance

(Implemented per the operator's extended directive: mode-consistent evaluation,
not just the spread guard. The system is structurally a maker; evaluation now
prices the mode it executes.)

`src/strategy/live_inference/mode_consistent_ev.py` (new, pure) — the two explicit
EV formulas the operator asked for, in the same per-share probability units as
`robust_trade_score`:

- `EV_taker = q_lcb − c_taker_all_in` (today's crossing formula; only admissible when
  the relative-spread guard passes).
- `EV_maker = p_fill_maker × (q_fill_adj − maker_limit)` where
  `maker_limit = min(bid + tick, ask − tick, reservation)` (bid-improving, and
  structurally non-crossing even when the venue ignores post_only: at a one-tick
  spread it rests AT the bid instead of lifting the ask) and
  `q_fill_adj = q_lcb − λ × (ask − bid)/2` with λ=1.0 (first-order microstructure
  estimate: a resting bid-side fill implies the mid moved toward us by ~half the
  spread of bad news). The LCB alone does NOT suffice for the haircut: it bounds
  *parameter* uncertainty of q, not the *conditioning event* "we got filled" —
  q|fill < q is selection, not estimation, so it must be a separate subtraction.
- `p_fill_maker`: today's `p_fill_lcb` is visible-depth TAKER coverage (≈1.0) and is
  NOT calibrated for bid-improve resting (measured live fill rate 10.8%, zero fills
  p 0.30-0.80). New conservative prior `edli_v1.maker_fill_probability_prior`
  (default 0.10) with full receipt provenance (`maker_fill_probability_source`)
  so the settlement loop can recalibrate from fill_tracker facts (e6e02796f0).
- `relative_spread = (ask − bid)/mid`; taker is FORBIDDEN when it exceeds
  `edli_v1.taker_max_relative_spread` (default 0.25) — enforced INSIDE
  `_select_edli_order_mode` so the canary force-taker, the governor's
  shallow-depth/degraded TAKER, and the EV override are ALL subject to it (the lane,
  not the callers). Maker resting remains allowed.
- Mode selection at evaluation: compute both EVs, choose the max (taker only if the
  guard passes); the chosen mode's EV IS the candidate's `trade_score` (the
  p_fill × edge − penalty hybrid of taker cost with ≈1.0 fill dies). TAKER-chosen
  scores are byte-identical to the legacy kernel (c_stress == c95 made the legacy
  min() always the q_lcb leg — relationship test pins it), so the change is
  surgical: only wide-spread / maker-favored candidates re-price. Receipts carry
  `execution_mode_intent`, `ev_taker`, `ev_maker`, `maker_limit_price`,
  `relative_spread_at_eval`, `taker_forbidden_reason`, `maker_fill_probability(+source)`;
  the final intent carries `placement` (`maker_bid_improve`/`taker_cross`),
  `spread_at_entry`, `relative_spread_at_entry` — both EVs always recorded so the
  settlement loop can recalibrate p_fill_maker and the haircut from fill facts.
- Adverse selection: the q_lcb alone does NOT suffice as the fill haircut — it
  bounds parameter uncertainty of q, while q|fill < q is a conditioning
  (selection) effect. Implemented as a separate λ × half-spread subtraction, λ=1.0
  (first-order microstructure estimate) until measured.
- Submit-seam consistency: the relative-spread guard is enforced INSIDE
  `_select_edli_order_mode` (step 0) so the canary force-taker, the governor's
  shallow-depth TAKER (the depth-inversion of §3), and the §2 EV override are ALL
  subject to it — and on the late maker→taker re-build lane. An unmeasurable
  two-sided book (missing bid or ask) forbids crossing too. The §2 EV boundary
  (e(1−P_fill) ≥ s/2(1+P_fill)+f−A) remains the submit-time maker-vs-taker
  economics within the guard — numbers decide, no dogma.
- Maker-vs-taker stays a numbers decision on healthy books: a tight-spread
  favorite where EV_taker > EV_maker still routes taker (antibody test).
- Venue post_only: the envelope threads `post_only` to the SDK call
  (`polymarket_v2_adapter.submit → create_and_post_order(..., post_only=...)`);
  the in-repo structural guarantee is independent of venue support: executor
  pre-venue check (executor.py:1806) rejects any passive intent whose limit would
  cross the snapshot book, and the new `ask − tick` cap makes a crossing maker limit
  unconstructable at the price level.

### Incident replay under the fixes

24°C buy_yes: distance 2.42 > T 1.263 → `DIRECTION_LAW_BIN_FORECAST_MISMATCH` (A);
price 0.0168 < 0.05 with q_lcb 0.0927 > 2×0.0168, source FORECAST_BOOTSTRAP →
`COVERAGE_UNLICENSED_TAIL` (B); had both somehow passed, rel-spread 56% > 25% forbids
the taker lane (C), and maker EV = 0.10 × (0.0927 − 0.0035 − 0.010) ≈ 0.008 with a
bid-improving 0.010 post-only rest — no spread cross is constructable. Three
independent antibodies; each kills the whole category, not the instance.

### Selection hardening (follow-on commit)

`_selection_scoped_proofs` ranked every PRICED proof regardless of
`missing_reason`: a gate-rejected proof with a corrupt-high q_lcb could win the
ΔU rank and dead-end at TRADE_SCORE_NON_POSITIVE — never a bad order, but it
STARVED the legitimate admitted sibling (family false-no-trade). Gate-rejected
proofs are now unrankable, not merely unsubmittable. End-to-end antibody: the
verbatim incident family replay yields NO priced selection.

### Residuals (documented, not fixed here)

- The ΔU ranker (`utility_ranker`) still prices candidates at taker cost —
  conservative (a maker fill never pays more than the taker walk at the same
  stake); full maker-aware ΔU payoff is follow-up work.
- QuoteFeasibilityCertificate best_bid/best_ask provenance bug (§3 anomaly,
  0.32/0.34 recorded for a 0.009/0.016 book) — needs its own audit; the spread
  guard reads the FRESH pre-submit witness book, not only the quote cert.
- `p_fill_lcb` (visible-depth taker coverage) still feeds the FDR/no-submit
  telemetry surfaces unchanged; only the trade_score and submit mode are
  mode-consistent. The maker fill prior (0.10) recalibrates from fill_tracker
  facts (e6e02796f0) via the recorded provenance.

## 6. Verification

Commits on `fix/opportunity-book-selector` (no push, no restart, daemon
untouched in edli_shadow_no_submit):

- `d3d2dbc5c3` incident: root-cause doc (docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md)
- `47a173bdc7` fix A: direction law as code (+22 antibody tests, incl. verbatim
  incident family replay through `_generate_candidate_proofs`)
- `c7a2252e65` fix B: COVERAGE_UNLICENSED_TAIL (+10 tests, incl. proof-seam
  wiring on a forecast-adjacent cheap bin where only this guard can decide)
- `cc15a18d70` selection hardening: gate-rejected proofs unrankable (+2 tests:
  incident family no-trades end-to-end; no sibling starvation)
- `5f750abce6` fix C: mode-consistent EV + spread guard + non-crossing maker
  placement (+21 tests)

Suites: tests/engine + tests/strategy/live_inference + tests/decision_kernel +
tests/events → **1163 passed**; the 30 failures are byte-identical to the HEAD
baseline (verified via throwaway worktree diff of FAILED sets — pre-broken,
owned by the concurrent test-triage/co-tenant work, zero regressions from these
fixes). s4/s5/s6 + candidate_evaluation + trade_score suites green.

Incident replay under the fixes (all asserted by tests):
DIRECTION_LAW kills 24°C/23°C YES; COVERAGE_UNLICENSED_TAIL kills any unlicensed
longshot disagreement that is direction-consistent; the spread guard makes the
56%-spread FOK cross unconstructable even under canary force; the incident
family as a whole produces NO priced selection.
