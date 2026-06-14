# K-cut: collapsing the buy_no / buy_yes decision path — design for operator review

```
Created: 2026-06-13
Last reused or audited: 2026-06-13
Authority basis: operator directive 2026-06-13 ("有这么多乱七八糟规则本身就是问题");
  docs/authority/exit_portfolio_execution_authority_2026-06-13.md (single log-growth objective, P1/P5/K2);
  docs/authority/statistical_calibration_addendum_2026-06-13.md (A2 multiplicity condemned, A8 maker, A10 JS);
  CLAUDE.md §1 "N surface problems = K structural decisions, K≪N".
Status: DESIGN ONLY. Nothing is deleted. Every DELETE/MERGE is operator-gated.
```

## 0. The disease (operator's framing, restated as the structural claim)

The buy_no decision path in `src/engine/event_reactor_adapter.py` (14,849 lines) is a
**gauntlet**: ~174 forced-zero / fail-closed / gate-reject seams, 40+ rejection codes
(`src/contracts/rejection_reasons.py`), 14 gating flags. Tonight three individual
gate-fixes (q_lcb_no=0, maker p_fill=0) were applied and **live orders stayed at ZERO** —
because unblocking one of N gates only exposes the next gate down.

This is the textbook CLAUDE.md pathology: **N surface symptoms are K structural decisions
incompletely executed, K≪N.** Patching symptoms one-by-one creates new bugs at the patch
boundaries and never terminates. The cure is not a 175th patch. It is to name the K honest
decisions a trade actually requires, route every candidate through exactly those K, and
classify every other seam as KEEP (it *is* a K), MERGE (a twin/duplicate of a K), or
DELETE (scar tissue a K subsumes).

**Counting the seams down to K.** The 174 seams are NOT 174 distinct checks. Measured in
the file: `return None`×141, `reason=`×66, `score = 0.0`×4, `_enabled`×79 (only 14 unique
flags), `q_lcb`×405 refs. The 66 `reason=` emit sites collapse to **~22 distinct decision
points** (the rest are the same receipt rebuilt with different field values on parallel
branches). Those 22 decision points collapse to **K=5 honest decisions**. Everything else
is duplication, shadow scaffolding, or a partial heuristic that one of the 5 already owns.

```
174 seams  →  66 reason emit-sites  →  22 distinct decision points  →  K = 5 honest decisions
```

---

## 1. The complete buy_no / buy_yes gauntlet, end to end

Two layers run in sequence. **Layer A — candidate generation** (`_generate_candidate_proofs`,
~line 7426): prices every (bin, direction) and stamps a `trade_score`; four `score = 0.0`
zeroing gates live here. **Layer B — family decision** (the `_event_bound_decision` /
no-submit-receipt core, ~lines 1540–3300): picks the family winner and runs it through the
sequential reject chain. A candidate must survive BOTH layers and the pre-decision phase.

### 1A. Pre-decision phase gates (before any belief/price is even read)

| # | file:line | check | can reject with | category |
|---|---|---|---|---|
| P1 | era.py boundary 1556 | day0 scope: forecast_only/day0_shadow/forecast_plus_day0 lane admission | `DAY0_OUT_OF_SCOPE_AT_BOUNDARY`, `DAY0_SCOPE_SHADOW_ONLY` | DESIGNED_GATE |
| P2 | 1580/1676 | day0_shadow force-shadow: full pipeline runs, NEVER submits | `DAY0_SCOPE_SHADOW_ONLY` | DESIGNED_GATE (shadow) |
| P3 | 1652 | day0 input-ordering: quote must be newer than the observation it prices | `DAY0_QUOTE_PRECEDES_OBSERVATION` | DESIGNED_GATE (honest) |
| P4 | 1692 | durable submit outbox present | `EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED` | DESIGNED_GATE |
| P5 | 1700 | executor boundary wired | `EXECUTOR_BOUNDARY_MISSING` | ARTIFICIAL_SUSPECT |
| P6 | 1716 | operator arm token present | `OPERATOR_ARM_REQUIRED` | DESIGNED_GATE (honest) |
| P7 | 3250 | `real_order_submit_enabled` / `live_submit_enabled` | `SUBMIT_DISABLED` | DESIGNED_GATE |

`LIVE_CANARY_DISABLED` (Wave-1) and the submit-time **mainstream-agreement enforce** branch
(line 1719) are ALREADY DELETED by the operator — mainstream is display-only, never a gate.
That is the model for this whole exercise: an honest deletion already proven safe.

### 1B. Connection / topology / snapshot binding gates

| # | file:line | check | rejects with | category |
|---|---|---|---|---|
| B1 | 2200/2202/2204 | forecast/topology/calibration DB connection present | `*_AUTHORITY_CONNECTION_MISSING` | ARTIFICIAL_SUSPECT |
| B2 | 2209/2247/2340 | market topology row present + parseable | `EVENT_BOUND_MARKET_TOPOLOGY_MISSING/INVALID` | HONEST_DATA/MARKET |
| B3 | 2219/2257 | executable + selected snapshot row present | `EVENT_BOUND_*_SNAPSHOT_MISSING` | HONEST_DATA |
| B4 | 2353 | selected snapshot not stale | `EXECUTABLE_SNAPSHOT_STALE` | HONEST_DATA |
| B5 | 2373 | candidate binding succeeded | `EVENT_BOUND_CANDIDATE_BINDING_FAILED` | HONEST_DATA |
| B6 | 2427 | live-inference inputs present | `LIVE_INFERENCE_INPUTS_MISSING` | HONEST_DATA |

### 1C. Layer-A per-candidate scoring gauntlet (THE four zeroing gates)

Inside the `for candidate ... for (token,direction)` loop, each priced candidate is scored,
then **four independent gates can set `score = 0.0`**, each masking a positive-edge candidate:

| # | file:line | gate | sets | rejects family with |
|---|---|---|---|---|
| C1 | 7426–7510 | price the (bin,direction); maker-quote surfacing if own ask empty | — | `NATIVE_QUOTE_MISSING`/`NATIVE_TOKEN_MISSING` (6837/6858/6914) |
| C2 | 7514 | **market-anchor cap** (flag ON, LIVE): one-sided LOWER of q_lcb_no | q_lcb↓ | (silently weakens NO edge) |
| C3 | 7570 | mode-consistent EV → `score = chosen_ev` (maker or taker EV) | score | — |
| C4 | **7580** | `capital_efficiency_untradeable_reason` | **score = 0.0** | `EVENT_BOUND_ALL_CANDIDATES_REJECTED` |
| C5 | **7598** | `direction_law_reason` (buy_yes adjacent / buy_no distant) | **score = 0.0** | (same) |
| C6 | **7614** | `coverage_unlicensed_tail_reason` (price<0.05 & q_lcb>2×price needs licensed source) | **score = 0.0** | (same) |
| C7 | **7628** | `buy_no_conservative_evidence_reason` | **score = 0.0** | (same) |

Then `passed_prefilter` is forced False if ANY of C4–C7 fired, so the candidate **cannot be
selected** and **cannot enter the FDR family as a passed hypothesis**.

### 1D. Layer-B family decision sequential reject chain

After Layer A produces priced proofs, the family winner runs this gauntlet in order. Each is
an early-return no-submit receipt:

| # | file:line | gate | rejects with | category |
|---|---|---|---|---|
| D1 | 2257/2353 | selected candidate / snapshot present + fresh | `EVENT_BOUND_SELECTED_*` | HONEST_DATA |
| D2 | 2501 | a positive-ΔU candidate exists (`_selected_candidate_proof`) | `EVENT_BOUND_SELECTED_CANDIDATE_MISSING` / `ALL_CANDIDATES_REJECTED` | HONEST_MARKET |
| D3 | 2535 | selected proof has an executable price | `EXECUTABLE_NATIVE_ASK_MISSING` | HONEST_MARKET |
| D4 | 2562 | limit price tradeable | `EVENT_BOUND_*_UNTRADEABLE` | HONEST_MARKET |
| D5 | 2592 | replacement hook not BLOCKED | `REPLACEMENT_FORECAST_HOOK_BLOCKED` | HONEST_DATA |
| D6 | 2620/2680/2719/2763 | replacement direction/exec proof present, not flipped, supported | `REPLACEMENT_FORECAST_*` (4 codes) | DESIGNED_GATE |
| D7 | **2790** | `trade_score <= 0.0` | `TRADE_SCORE_NON_POSITIVE` | HONEST_MARKET |
| D8 | 2820 | EB selection-shrinkage (flag) OR FDR | `SELECTION_EB_UNLICENSED` / `FDR_REJECTED` / `FDR_FULL_FAMILY_PROOF_MISSING` | DESIGNED_GATE |
| D9 | 2920–3170 | Kelly sizing + submit-recapture (price-moved / edge-reversed / family-reversed / below-min) | `KELLY_PROOF_MISSING`, `SUBMIT_ABORTED_*` (4 codes) | DESIGNED_GATE |
| D10 | 3207 | RiskGuard | `RISK_GUARD_BLOCKED` | DESIGNED_GATE |
| D11 | 3250 | submit enabled + operator arm | `SUBMIT_DISABLED` / `OPERATOR_ARM_REQUIRED` | DESIGNED_GATE |

**The kill stack for a real favorite-longshot buy_no edge:** the edge sits on far-shoulder
bins (q_lcb_no 0.75–0.99). Those bins have **no native NO ask** (illiquid tail) → C1 either
no-trades them (NATIVE_QUOTE_MISSING) or, if a YES bid exists, surfaces a maker quote whose
**fill probability** is the conservative maker prior. Meanwhile the LIQUID near-center bins
carry weaker edge (flat-σ calibration), and there **C2 (market-anchor, LIVE) actively lowers
q_lcb_no** and **C7 (buy_no conservative evidence)** + **C4 (capital efficiency)** zero them.
**Edge where there is no liquidity; liquidity where there is no edge — and a live one-sided
cap that erases what little near-center NO edge survives.** That is the structural failure,
not any single gate.

---

## 2. The K essential decisions (target K=5), each a relationship invariant

Per the consult-3 authority: **a trade decision is `argmax` over (bin, direction, lane) of
expected-log-growth(calibrated posterior, real executable price); feasibility = a real
executable quote exists.** Everything honest in §1 is a facet of that one objective. The K:

### K1 — CALIBRATED BELIEF EXISTS (the posterior is real and admissible)
> A family enters the decision iff a calibrated posterior bundle with bounds exists for it.
> **Invariant (cross-module, pytest):** for the live replacement bundle,
> `0 ≤ q_lcb_no ≤ q_no ≤ 1` AND `q_lcb_no = 1 − q_ucb_yes` AND `q_shape ∈ {fused_normal_direct,…}`.
> RED-on-revert: feed a bundle with `q_lcb_no > q_no` ⇒ K1 raises, no trade.

Subsumes: B1, B2, B3, B6, C-Q_LCB_INVALID, `LIVE_INFERENCE_INPUTS_MISSING`,
`CALIBRATION_AUTHORITY_EVIDENCE_MISSING`. (Offline repro tonight confirmed the belief is
HEALTHY — K1 is NOT the blocker, but it must stay as the honest gate.)

### K2 — REAL EXECUTABLE QUOTE EXISTS (lane-correct feasibility)
> A (bin,direction) is feasible iff there is a real executable price on the lane it will use:
> a **taker ask** to cross, OR a **maker rest** that can sit behind the complementary book
> with the lane-correct fill probability. Feasibility is a fact about the book, never about
> edge.
> **Invariant:** `executable_price_exists(bin,dir,lane) ⟺ (taker_ask present) OR (maker quote
> with comp_bid present)`; and the fill probability stamped MUST match the lane
> (`p_fill_maker` for a `price_type=="bid"` quote, NEVER the taker visible-depth LCB, which
> is 0 on a maker quote).
> RED-on-revert: a maker quote whose `p_fill` is read off the (empty) taker ladder ⇒ K2 fails
> (this is the exact 5th "taker-shaped check strangles makers" bug).

Subsumes: C1, D3, D4, `NATIVE_*`, `EXECUTABLE_NATIVE_ASK_MISSING`, the maker/taker lane choice.

### K3 — EXPECTED-LOG-GROWTH > 0 OVER (posterior, quote) — the single admission authority
> Admit (bin,direction,lane) iff expected log growth of the chosen stake on the **calibrated
> posterior** against the **real executable price** is strictly positive; select the family
> winner = `argmax` of that quantity. THIS SUBSUMES edge>threshold, FDR/multiplicity,
> coverage licensing, capital-efficiency, and the buy_no conservative-evidence heuristic —
> they are all partial, mutually-redundant approximations of "is this +EV under the honest
> posterior and the real price". (Authority A2: FDR controls E[V/R], NOT bankroll log growth —
> "the WRONG objective"; the {0,1}-p-value BH gate is "vacuous", a CONDEMNED BLOCKER.)
> **Invariant:** `admit(c) ⟺ E_log_growth(q_posterior(c), price(c), stake*(c)) > 0` where
> `stake*` is the horse-race/Kelly optimum; and for the family, `selected = argmax_c
> E_log_growth`. No candidate with `E_log_growth>0` may be rejected by any *other* scalar gate.
> RED-on-revert: a cheap-NO whose honest q_lcb_no genuinely covers cost (E_log_growth>0) but
> which the legacy 0.95-cutoff buy_no gate blocked ⇒ K3 admits it, the cutoff does not.

Subsumes (MERGE/DELETE targets): C4 capital_efficiency, C6 coverage_unlicensed_tail, C7
buy_no_conservative_evidence, D7 trade_score, D8 FDR/EB, the entire `p_fill·edge` trade_score
formula. **This is the stage that most likely unblocks live orders.**

### K4 — KELLY SIZE (portfolio-correct, the cash threshold is endogenous)
> Size by horse-race Kelly within the family (closed form, §P1): bins COMPETE, cash `s*` is
> the endogenous shadow price, exposure is capped by the MATH not a throttle (NO-caps law).
> Free cash bounds the chosen stake ONCE (a min, never a haircut). Per-candidate
> `kelly_multiplier` + `capital_efficiency_lcb_ev` per-bin sizing is the WRONG structure (each
> bin sized in isolation; authority §P1/§K2).
> **Invariant:** `Σ f_k* + s* = 1`, `f_k* ≥ 0`, AND `E_log_growth(horse-race allocation) ≥
> E_log_growth(any per-candidate sizing)` (growth-dominance, 0 violations / 400k families).
> RED-on-revert: a 2-bin overround family where per-candidate sizing overbets ⇒ horse-race
> commits less and dominates; per-candidate path violates the inequality.

Subsumes: D9 Kelly/recapture (size + price recapture stay; the *sizing structure* changes),
the per-bin `kelly_multiplier` stacking.

### K5 — HONEST OPERATOR / DIRECTION / TIME-ORDERING GUARDS (never weakened)
> The genuinely honest non-EV guards stay, exactly as is:
> direction law (buy_yes adjacent / buy_no distant — unconstructable, not down-weighted),
> operator arm token, real_order_submit flag, durable outbox, RiskGuard, day0 input-ordering
> (quote newer than observation), submit-time recapture (price/edge/family re-rank on the
> fresh book), DST/time-semantics.
> **Invariant:** these fire ONLY on a genuine violation (direction law: candidate on the
> wrong side of μ; arm: token absent; recapture: fresh-book edge actually ≤0). None of them
> may mask "missing input" as "no edge".
> RED-on-revert: a far-tail buy_yes (Milan-24C class) ⇒ direction law refuses (unconstructable).

Keeps: P3, P4, P6, P7, C5 direction_law, D6 replacement direction/proof, D9 recapture, D10
RiskGuard, D11 submit/arm.

**The honest minimal path:** `K1 belief → K2 real quote (lane) → K3 E_log_growth>0 = argmax →
K4 horse-race size → K5 operator/direction/recapture → submit`. Five decisions. One objective
(K3) does the admission work that C4+C6+C7+D7+D8 do today by five overlapping approximations.

---

## 3. KEEP / MERGE / DELETE classification (every enumerated gate)

| seam | gate | verdict | survivor / rationale |
|---|---|---|---|
| P1 | day0 scope lane | KEEP | K5 — honest lane routing |
| P2 | day0_shadow force-shadow | **DELETE** | shadow path; "NO MORE SHADOW" operator law. Once the comparator has licensed day0, the shadow lane is scar tissue |
| P3 | day0 input-ordering | KEEP | K5 — honest correctness check (quote newer than obs) |
| P4 | durable outbox | KEEP | K5 |
| P5 | executor boundary missing | KEEP | K1/wiring — ARTIFICIAL_SUSPECT, a wiring fault not a gate |
| P6 | operator arm | KEEP | K5 — honest |
| P7 | submit enabled | KEEP | K5 — honest operator switch |
| B1 | DB connection missing | KEEP | K1 — wiring fault surfaced honestly |
| B2–B6 | topology/snapshot/inputs present | KEEP | K1/K2 — honest "input exists" |
| C1 | price the bin + maker-quote surface | KEEP→**FIX** | K2 — but maker-quote scope (buy_no only, requires comp YES bid) is the structural gap (see §4) |
| **C2** | **market-anchor cap (LIVE)** | **DELETE (operator-gated)** | partial heuristic to patch flat-σ over-confidence. One-sided LOWER of q_lcb_no ⇒ can ONLY kill a NO trade, never make one. It is a calibration band-aid living in the decision path; the real fix is C1/C3 era-EB (§5). Subsumed by K3 once calibration is honest. **Until calibration lands, removing this WITHOUT C1/C3 would re-admit the phantom near-center NO edge** — so DELETE is staged AFTER C3-JS, not before |
| C3 | mode-consistent EV → score | MERGE | becomes K3's E_log_growth on the chosen lane |
| **C4** | **capital_efficiency zeroing** | **DELETE** | partial approximation of K3 (+EV). A per-candidate edge/efficiency cutoff is exactly the per-bin "edge>threshold" the authority §P1 says is the WRONG structure. Subsumed by K3+K4 |
| C5 | direction_law zeroing | KEEP | K5 — honest, unconstructable side |
| **C6** | **coverage_unlicensed_tail zeroing** | **MERGE→K3** | the "licensed source" requirement folds into K1 (belief admissibility) + K3 (+EV). A tail with an unlicensed q_lcb fails K1's bounds/provenance, OR clears it and is judged purely on E_log_growth. Standalone zeroing gate = redundant |
| **C7** | **buy_no_conservative_evidence zeroing** | **DELETE** | the cheap-NO-overconfidence 0.95-cutoff. Authority + the in-code comment at 7630 already say the ΔU/E_log_growth ranker SUBSUMES it ("scattered on/off gates ARE the regression disease"). Pure scar tissue once K3 is the authority |
| D2 | positive-ΔU candidate exists | MERGE→K3 | it IS the argmax of K3 |
| D3/D4 | executable ask / tradeable limit | MERGE→K2 | feasibility = K2 |
| D5/D6 | replacement hook block/direction/proof | KEEP (audit→simplify) | K5 (direction) + K1 (belief). The replacement IS the belief authority now; the 4 proof-missing codes are HONEST_DATA fail-closes — keep but they should be K1 sub-clauses, not a parallel 4-code lane |
| **D7** | trade_score ≤ 0 | **MERGE→K3** | `p_fill·min(edge,...)` is a degenerate one-bin E_log_growth proxy. Replaced by K3 directly |
| **D8** | FDR / EB shrinkage | **DELETE FDR; PROMOTE EB** | FDR on {0,1} p-values is CONDEMNED (A2 BLOCKER, "vacuous"). The EB selection-shrinkage (winner's-curse) is the honest replacement — but it should be the lfsr/posterior-log-utility license INSIDE K3, not a flag-gated shadow twin of FDR. Kill the FDR lane; make EB-log-utility the K3 admission |
| D9 | Kelly + recapture | KEEP (restructure) | size→K4 (horse-race); recapture→K5 (honest fresh-book re-gate) |
| D10 | RiskGuard | KEEP | K5 |
| D11 | submit/arm | KEEP | K5 |
| — | mainstream-agreement enforce | ALREADY DELETED | the precedent (display-only) |
| — | LIVE_CANARY_DISABLED | ALREADY DELETED | the precedent (artificial throttle) |

### Top-3 DELETE candidates (highest unblock-value, operator-gated)
1. **D8 FDR lane** (`evaluate_fdr_full_family` on {0,1} p-values). CONDEMNED by the authority
   itself as a vacuous BLOCKER. Replace with the already-built EB log-utility license (K3).
2. **C4 capital_efficiency zeroing** + **C7 buy_no_conservative_evidence zeroing** (the two
   per-candidate `score=0.0` gates). Both are partial +EV approximations K3 subsumes; the code
   comment already declares the ΔU ranker subsumes C7.
3. **C2 market-anchor cap (LIVE, one-sided q_lcb_no↓)** — but DELETE only AFTER C1/C3
   calibration lands (§5), else the phantom near-center edge returns.

---

## 4. The maker-quote structural gap (K2 fix — where the book actually is)

`_maker_quote_execution_price_from_snapshot` (line 13483) is **buy_no ONLY** and requires a
**complementary YES best bid** to quote behind (`cap = 1 − comp_best_bid − tick`); it
fail-closes to `NATIVE_ASK_MISSING` when no comp bid exists. Consequence: the maker quote
surfaces **only where a YES bid exists** — i.e. it does NOT systematically place resting NO
quotes on the **liquid near-center bins** where the order book actually has depth. The system
"is fundamentally a maker" (its own comment) but the quote-surfacing is bolted onto the
illiquid-far-bin taker-missing path, not driven by where the book is.

**K2 design correction (not in this doc's scope to implement):** maker-quote surfacing should
be a first-class lane for EVERY bin with a complementary book (near-center included), with the
lane-correct `p_fill_maker`, so K3 ranks resting near-center NO quotes against far-bin taker
edge on ONE E_log_growth scale. This is the "surface MAKER quotes where the book is" half of
the unblock. It pairs with the calibration fix: near-center is where the liquidity is, so it is
where a maker NO harvest must live — but only once near-center q is honest (§5).

---

## 5. Staged collapse (lowest blast-radius first, each independently shippable + RED-on-revert)

All stages land flag-gated, default = current behavior, shadow-computed first; flips are
operator-only (standing law). Each stage carries its relationship test (the K invariant) that
goes RED on revert.

**Stage 0 — instrument (no behavior change).** Stamp every receipt with `E_log_growth(q,price,
stake)` shadow next to the live trade_score, for every priced candidate (not just the winner).
This is the comparator that licenses every later flip. RED-on-revert: a candidate the live path
rejected but E_log_growth>0 is now visible — proves the gate-mass is killing +EV trades.

**Stage 1 — K3 as the single admission authority (THE unblock stage).** Promote the EB
log-utility license (`selection_shrinkage.py`, already built) to be THE family admission +
selection authority, replacing the `trade_score = p_fill·edge` gauntlet AND killing the FDR
lane (D8). Concretely: admit `c ⟺ E_log_growth>0`; select `argmax E_log_growth`; C4/C6/C7
zeroing gates become shadow-only diagnostics, not decision inputs. RED-on-revert: the cheap-NO
subsumption test (`test_cheap_no_overconfidence_loser_is_delta_u_no_trade` already exists) plus
a new test that a +EV near-center NO admitted by K3 was rejected by the legacy C7 cutoff.
**This is the stage that most likely turns live orders from 0 → nonzero**, because it removes
the four stacked zeroing gates + the vacuous FDR in one structural move.

**Stage 2 — K2 maker-quote as a first-class lane on liquid bins.** Drive maker-quote surfacing
from "complementary book exists" (near-center included), lane-correct p_fill, so K3 can rank
resting near-center NO quotes. RED-on-revert: a near-center bin with a YES book and +EV resting
NO must produce a maker proof; a taker-shaped p_fill on it must fail the K2 lane test.

**Stage 3 — K4 horse-race Kelly live.** Flip `replacement_horse_race_kelly_enabled` (built,
shadow today) so bins compete and `s*` is endogenous. RED-on-revert: the growth-dominance
inequality (already verified over 400k families) holds live; per-candidate path violates it.

**Stage 4 — calibration honesty (C1 era-EB / C3 JS-toward-market), operator-gated.** ONLY after
this lands can C2 (market-anchor cap) be DELETED — the cap exists solely to patch the flat-σ
near-center over-confidence; honest near-center q removes its reason to exist. RED-on-revert:
the e-process calibration alarm (A3, NO-vs-modal class) stays quiet on honest q and would have
exploded on the 7/7 sell-the-mode sequence.

**Stage 5 — physical deletion of MERGE/DELETE seams** (operator-gated, after Stages 1–4 prove
the K path carries all traffic). Remove C2/C4/C7/D7/D8-FDR/P2-shadow from the file. Blast radius
is bounded because by Stage 5 they are already shadow-only no-ops.

Order rationale: Stage 1 is highest-value + self-contained (the authority is built, the test
exists). Stages 2–4 each widen where the +EV path can find a trade. Stage 5 is pure cleanup,
last, when nothing reads the dead gates.

---

## 6. What the collapse CANNOT fix (the operator must see TWO design failures, not 174 patches)

The K-cut fixes **gate-mass** — the 174 seams collapse to 5 honest decisions and live orders
unblock. It does NOT fix **calibration**. These are independent design failures:

1. **GATE-MASS (this doc).** 174 seams = 5 decisions incompletely executed. Curable by the
   K-cut. Stage 1 alone likely unblocks orders.
2. **FLAT-σ CALIBRATION (NOT curable here).** The fused-N posterior under-weights its own
   near-center bins vs the sharper market, manufacturing phantom NO edge on the adjacent ring
   (C3 −4.8pt mean, 30–54pt tails) AND leaving genuine near-center edge too weak to clear cost.
   **No amount of gate-collapse creates edge where the belief is miscalibrated.** The real fix
   is the calibration authority's C1 (era-EB partial pooling over 6117 settlements) + C3
   (James–Stein toward market, N_eff=3.71) — both BUILT, both operator-gated, both OFF today.

If Stage 1 unblocks orders but they lose, the cause is failure #2, not the gates — and the
honest response is C1/C3, never a 175th gate. **The market-anchor cap (C2, LIVE today) is the
tell:** it is failure #1 (a gate) deployed to paper over failure #2 (calibration). Removing it
without C1/C3 re-admits the phantom edge; that is why Stage 4 precedes the C2 deletion.

---

## 7. Summary for the operator

- **174 seams → 5 honest decisions (K1–K5).** The 66 reason-emit sites are 22 decision points;
  22 collapse to 5. One objective (K3 = expected-log-growth over calibrated posterior × real
  executable price) does the admission work that capital-efficiency + coverage + buy_no-evidence
  + trade_score + FDR do today as five overlapping, mutually-redundant approximations.
- **Top-3 deletions:** (1) the FDR lane (D8, authority-CONDEMNED, vacuous on {0,1} p-values);
  (2) the two per-candidate `score=0.0` zeroing gates C4 capital_efficiency + C7
  buy_no_conservative_evidence (subsumed by K3, the code already says so); (3) the LIVE
  market-anchor cap C2 (one-sided, can only kill NO trades) — staged after calibration.
- **The single stage that most likely unblocks live orders:** Stage 1 — make the built EB
  log-utility (`selection_shrinkage.py`) the single family admission+selection authority,
  replacing the `p_fill·edge` trade_score gauntlet and killing the FDR lane. It removes the
  four stacked zeroing gates + vacuous FDR in one structural move.
- **Two design failures, not 174:** gate-mass (curable here) and flat-σ calibration (curable
  ONLY by the operator-gated C1 era-EB / C3 JS-toward-market). The K-cut unblocks orders; it
  cannot manufacture edge a miscalibrated belief does not have.
- **NO-caps law respected throughout:** every K is honest math (E_log_growth, horse-race s*,
  direction law, real-quote feasibility, operator arm). No notional cap, allowlist, time-ban,
  or q-haircut is introduced. The endogenous cash threshold s* is the only "cap" and it is
  derived, not set.

Nothing in this document is deleted or implemented. Every DELETE/MERGE is a recommendation for
operator review and is gated on the staged RED-on-revert proofs above.
