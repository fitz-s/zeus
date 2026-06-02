# BEST ORDER SELECTION — ROOT CAUSE + CORRECT DESIGN (2026-06-01)

```
Created: 2026-06-01
Last reused or audited: 2026-06-01
Authority basis: operator PRIMARY concern (best-order-not-selected); reconciles with
  CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md §4.2 (admission-gate rescope).
Scope: selection + ranking of the CLEAN future-date candidate pool. Day0/phantom and
  chain reconcile are OUT OF SCOPE (handled by sibling sessions).
Method: read-only. Live rows pulled from state/zeus-world.db edli_no_submit_receipts
  (decision_time window 2026-06-01T13:13Z..16:13Z, 13,227 NO_SUBMIT proofs).
```

## 12-LINE DECISION SUMMARY

1. There is **NO global "best order across the book" selection anywhere**. Each forecast/redecision
   event is processed **independently, in temporal `fetch_pending` order** (`event_store.py:122`),
   and within an event only that event's single candidate produces a proof.
2. The only cross-candidate `max()` in the system (`event_reactor_adapter.py:2853`) ranks
   **within one family's ≤2 tokens**, by `(trade_score, q_lcb)` — never across families/cities.
3. So the firing rule is **first-qualifying-in-arrival-order**, with quality ignored. An armed
   canary submits on whichever event reaches the reactor first and clears the gates — not the best.
4. The ranking metric `robust_trade_score = p_fill_lcb × min(q_lcb−c95−λ, q_live−c95−λ)`
   (`trade_score.py:48-52`, `:68-71`) systematically **under-weights high-confidence winners**.
5. Live proof: Shanghai-28°C (q=0.995, cost=0.891, EV=+10.4¢, Kelly $44, **expected PnL $4.61**)
   scores **ts=0.053**, ranking **26/56** in the clean pool — below thin speculative bins.
6. The collapse has three multiplicative causes: (a) `q_lcb` not EV; (b) flat λ; (c) ×`p_fill_lcb`
   (Shanghai's 0.765 fill alone cuts the score 24%).
7. Ranking by the **correct** metric (expected PnL = Kelly × EV) reorders the book entirely:
   Spearman(trade_score, expected_PnL) = **0.663** — they materially disagree.
8. **857** candidates (whole pool) outrank Shanghai-28 on trade_score while having **lower**
   expected PnL — i.e. the system prefers worse trades to better ones.
9. The §4.2 ruling fixes the **admission gate** (admit on point-EV>0 + FDR, Kelly carries
   variance) but says nothing about **selection/ordering** — it is necessary but not sufficient.
10. **Unified fix = §4.2 gate-rescope (admit) + a NEW expected-PnL-ranked global selector (order).**
    Admit on FDR ∧ point-EV>0 ∧ confidence-floor; then RANK the admitted book by
    `expected_PnL = kelly_size × (q_posterior − cost)` and fire top-down under the live cap.
11. Without the selector, even after §4.2 the canary still fires first-qualifying — a mediocre
    bin can still beat Shanghai to the single tiny-live slot.
12. RED relationship test required: Shanghai near-sure-win must RANK ABOVE a thin speculative bin
    and BE the selected order; an honestly-uncertain bin must NOT outrank it.

---

## 1. ROOT CAUSE

The operator's symptom ("the near-sure-win won't get ordered, even though it's a top opportunity")
has **two independent structural roots**. Both must be fixed; neither alone solves it.

### ROOT A — There is no global "best order" selection. Firing is first-qualifying-in-arrival-order.

Trace the actual decision path:

- **`src/events/event_store.py:107-122` — `fetch_pending`** orders the queue by
  `priority DESC, available_at ASC, received_at ASC, event_id ASC`. **No quality term.** Order is
  purely *when the event was inserted*, unrelated to edge, EV, confidence, or size.

- **`src/events/reactor.py:165-172` — `process_pending`** iterates that list and calls
  `_process_event_unit` **per event, in that arrival order**. Each event is a self-contained write
  unit (claim → proof → mark → commit). There is no "collect all candidates, then choose the best"
  step — the loop commits each event before looking at the next.

- **`src/engine/event_reactor_adapter.py:582`** — each event resolves to **one** proof via
  `_selected_candidate_proof(payload, proofs)`:

  ```python
  # event_reactor_adapter.py:2839-2853
  def _selected_candidate_proof(payload, proofs):
      requested_token = _nonnull(payload.get("token_id"))
      if requested_token:                       # FSR/redecision event names a token →
          for proof in proofs:                  # return THAT proof only. No ranking.
              if proof.token_id != requested_token: continue
              ...
              return proof
      executable = [p for p in proofs if p.execution_price is not None]
      if not executable:
          return max(proofs, key=lambda p: p.q_lcb_5pct, default=None)
      return max(executable, key=lambda p: (p.trade_score, p.q_lcb_5pct))   # within ONE family
  ```

  The `max(...)` only ever ranks the **≤2 tokens of a single family** (`for candidate in
  family.candidates` at `:2777`, each contributing buy_yes/buy_no). It is a per-family tie-break,
  **not** a book-wide selector. And when the event carries a `token_id` (the redecision /
  FSR-emission path bakes a specific (family,bin,direction) into each event — see
  `continuous_redecision.py:enqueue_live_redecisions:289`, which emits one `EnqueuedRedecision` per
  qualifying pair), even that per-family tie-break is bypassed: the event's pre-named token is used.

- **`src/engine/event_reactor_adapter.py:295-410`** — the live submit path takes the
  `no_submit_receipt` *for this event* and, when `real_order_submit_enabled ∧ live_canary_enabled`,
  builds and submits it directly (`executor_submit(final_intent, command)` at `:357`). **No
  cross-event comparison precedes submission.** The first event whose proof clears every gate, with
  the canary armed and below its fill cap, **is the order that fires** — regardless of whether a
  better opportunity sits later in the queue.

**Verdict A:** selection is *first-qualifying*, not *global-best*. This is a defect. The operator's
mental model ("the best order in the book is the one that fires") is not implemented anywhere.

### ROOT B — `robust_trade_score` is the wrong "best order" ranking; it systematically buries high-confidence winners.

Even setting aside ROOT A (suppose we DID rank the book), the score we'd rank by is wrong.

**Formula** (`src/strategy/live_inference/trade_score.py:48-52`, receipt variant `:68-71`,
λ=stress=0.01 injected at `event_reactor_adapter.py` `_robust_trade_score_from_generated_inputs`):

```
edge_bound = min( q_lcb_5pct − c_95pct − 0.01 ,  q_live − c_stress − 0.01 )
score      = p_fill_lcb × edge_bound
```

For the Shanghai-28°C anchor (live row, decision_time 2026-06-01):
```
q_live=0.99495  q_lcb=0.97941  c_fee=0.89056  c_95=0.90056  p_fill_lcb=0.76482
edge_bound = min(0.97941−0.90056−0.01, 0.99495−0.90056−0.01) = min(0.06885, 0.08439) = 0.06885
score      = 0.76482 × 0.06885 = 0.0527        ← matches live trade_score=0.0534 (rounding)
```

Four compounding defects, each pushing a near-sure-win DOWN relative to a thin speculative edge:

| # | Defect | Effect on Shanghai-28 |
|---|--------|----------------------|
| B1 | Uses **`q_lcb` (5th-pct), not EV** | Discards the +10.4¢ point margin; uses the stress floor. |
| B2 | Subtracts **`c_95pct` (95th-pct stress cost)**, +1¢ over fee cost | At an 89¢ price the worst-case-cost subtraction is large relative to the margin. |
| B3 | **Flat λ=0.01** double-penalizes (see §4.2 ruling) | Eats another 1¢ of a thin margin. |
| B4 | **× `p_fill_lcb`** | 0.765 fill alone cuts the score ~24%. A high-fill thin edge can beat a lower-fill fat edge. |

The metric is built to answer *"is this edge robust enough to admit?"* — a **binary gate** question.
It is being mis-used as a **continuous ranking key**. As a ranking key it has the wrong shape: a
99.5%-confidence, +10.4¢-EV, $44-Kelly trade collapses to 0.053 because the formula throws away
exactly the things that make it a great order (high point-EV, large size).

---

## 2. LIVE RANKING-DISAGREEMENT TABLE (clean high-confidence pool)

Pool = unique (city,bin,direction) NO_SUBMIT proofs, last 3h, filtered to **q_live ≥ 0.85 ∧
EV > 0** (the genuine future-date winners; this strips day0-phantom wrong-direction noise).
N = 56 clean candidates. `EV = q_live − c_fee_adjusted`; `expected_PnL = kelly_size_usd × EV`.

**TOP 12 by `trade_score` (what the system ranks/fires by today):**

| ts | exp.PnL | q | EV | p_fill | Kelly | city |
|----|---------|---|----|--------|-------|------|
| 0.8630 | $22.77 | 0.997 | 0.987 | 0.899 | $23 | Paris |
| 0.3957 | $14.36 | 0.913 | 0.697 | 0.584 | $21 | Tokyo |
| 0.3288 | $18.31 | 1.000 | 0.396 | 0.875 | $46 | Wuhan |
| 0.2667 | $7.13  | 1.000 | 0.308 | 0.926 | $23 | Tel Aviv |
| 0.2509 | $22.51 | 0.996 | 0.976 | 0.270 | $23 | Seoul |
| 0.2480 | $6.41  | 0.984 | 0.292 | 0.912 | $22 | Tel Aviv |
| 0.2077 | $13.22 | 0.999 | 0.287 | 0.834 | $46 | Wuhan |
| 0.1804 | $10.70 | 1.000 | 0.231 | 0.941 | $46 | Taipei |
| 0.1710 | $4.86  | 0.917 | 0.274 | 0.674 | $18 | Tel Aviv |
| 0.1508 | $12.65 | 0.916 | 0.341 | 0.470 | $37 | Sao Paulo |
| 0.1360 | $5.59  | 0.924 | 0.174 | 0.884 | $32 | Seoul |
| 0.1206 | $8.80  | 0.984 | 0.205 | 0.844 | $43 | Toronto |

**TOP 12 by `expected_PnL = Kelly × EV` (the CORRECT ranking):**

| exp.PnL | ts | q | EV | p_fill | Kelly | city |
|---------|----|----|----|--------|-------|------|
| $22.77 | 0.8630 | 0.997 | 0.987 | 0.899 | $23 | Paris |
| $22.51 | 0.2509 | 0.996 | 0.976 | 0.270 | $23 | Seoul |
| $18.31 | 0.3288 | 1.000 | 0.396 | 0.875 | $46 | Wuhan |
| $14.36 | 0.3957 | 0.913 | 0.697 | 0.584 | $21 | Tokyo |
| $13.22 | 0.2077 | 0.999 | 0.287 | 0.834 | $46 | Wuhan |
| $12.65 | 0.1508 | 0.916 | 0.341 | 0.470 | $37 | Sao Paulo |
| **$11.06** | **0.0837** | 0.963 | 0.271 | 0.399 | $41 | **Wellington** |
| $10.70 | 0.1804 | 1.000 | 0.231 | 0.941 | $46 | Taipei |
| $9.82  | 0.1042 | 1.000 | 0.212 | 0.542 | $46 | Warsaw |
| $8.80  | 0.1206 | 0.984 | 0.205 | 0.844 | $43 | Toronto |
| $8.76  | 0.0648 | 0.978 | 0.209 | 0.425 | $42 | Toronto |
| $8.52  | 0.0614 | 1.000 | 0.184 | 0.425 | $46 | Taipei |

**Shanghai-28°C anchor**: `q=0.995, c_fee=0.891, EV=0.104, Kelly=$44.2, expected_PnL=$4.61,
trade_score=0.053, p_fill=0.765`.
- **trade_score rank = 26 / 56**
- **expected_PnL rank = 21 / 56**

(Shanghai is mid-pack on EV because at an 89¢ entry its per-share EV is structurally bounded ≤11¢;
its *value* is high *win-probability × size*, which neither metric captures as "top". The point the
operator is making — "almost a sure win, all forecasts agree" — argues for a **confidence-weighted**
selection, see §4.)

**Quantified disagreement across the FULL pool (13,227 proofs):**
- Spearman rank-corr(trade_score, expected_PnL) = **0.663** (materially divergent orderings).
- **857** candidates outrank Shanghai-28 on `trade_score` while having **lower** `expected_PnL`.
- Example inversion: a London bin with **q=0.217** (wrong-side / thin) scores **ts=0.136 > 0.053**
  yet has expected_PnL **$1.04 < $4.61** — the score prefers a near-coinflip thin bin to a
  near-certain one. (This is the speculative-thin-edge over-weighting the operator named.)

---

## 3. IS THERE A SHANGHAI-SPECIFIC GATE BLOCKING IT (beyond shadow)?

No terminal gate is rejecting Shanghai-28. Its live receipt is a **fully-accepted NO_SUBMIT proof**
(`side_effect_status=NO_SUBMIT`, `trade_score_positive=true`, `fdr_pass=true`,
`fdr_hypothesis_count=22`, `kelly_pass=true`, `kelly_size_usd≈44`, `final_intent_id` present). It
clears `_receipt_money_path_blocker` (`reactor.py:618-633`): trade_score>0, FDR pass, Kelly pass.

So Shanghai is **admission-eligible**. The reason it is not *ordered* is purely:
- the system is in NO_SUBMIT/shadow (no real submit), AND
- even when the canary arms, there is **no selector that would pick Shanghai over the other
  admission-eligible events** — the canary fires first-qualifying (ROOT A).

Two secondary observations relevant to live arming:
- **Canary force-taker @ 5¢ floor** (`event_reactor_adapter.py:325-346`, `_select_edli_order_mode`
  / `_build_live_execution_command_certificates`): the canary forces a taker FOK. For an 89¢-cost
  NO this is fine (it crosses the spread to buy at ask); the 5¢ figure is the *min-order-notional*
  floor (`:1524 min_order_notional = min(max_notional, max(price,0.01))`), not a price cap — it
  does not mis-handle an 89¢ order. **Not the blocker.** (Flag: worth a dedicated test that an
  89¢ taker-FOK sizes correctly under the $5 tiny-live notional cap — `:1524-1525` clamps notional
  to `tiny_live_max_notional_usd`, so a $44 Kelly is capped to $5 in canary; that is intended for
  canary but means Shanghai would submit at $5, not $44 — acceptable for canary, must lift post-canary.)
- `edli_live_min_realized_edge_bps = 0` → not gating.

---

## 4. THE CORRECT "BEST ORDER" DEFINITION + UNIFIED FIX

### 4.1 What should rank the order book for a Kelly-sizing, bankroll-constrained trader

A bankroll-constrained trader who can only place K orders this cycle should fill the bankroll with
the orders that maximize **total expected PnL per dollar deployed, subject to a confidence floor
that keeps false-confidence out**. The ranking key is NOT a robust-edge×p_fill gate score. It is:

```
expected_PnL_per_order = kelly_size_usd × (q_posterior − cost_fee_adjusted)      [$ expected]
```

with admission gated SEPARATELY (FDR-proven ∧ point-EV>0 ∧ confidence-floor), and Kelly already
carrying the variance penalty into `kelly_size`. Optionally rank by **EV-per-dollar** with a
size-tiebreak when bankroll is the binding constraint:

```
rank_key = (q_posterior − cost) / cost            # EV per dollar at risk (return on capital)
tiebreak = kelly_size_usd                          # deploy more capital when ROC ties
```

The operator's "almost a sure win" intuition is the **confidence floor** (`q_posterior` high ∧ CI
tight) acting as a *gate*, then **expected PnL** doing the *ordering*. A near-sure-win with a real
$44 Kelly and +10¢ EV must land at/near the top of the *admitted* book; a thin speculative edge
(low q, wide CI) is either rejected by the confidence floor or sized tiny by Kelly and so ranks low.

### 4.2 The unified fix = §4.2 ruling (ADMIT) + a new global selector (ORDER)

These are **orthogonal and both required**:

- **ADMISSION (already ruled in `CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md` §4.2):** replace
  the binary `robust_trade_score > 0` admission gate with **point-EV>0 ∧ p_value<α_FDR**, and let
  fractional Kelly + the dynamic CI/lead multiplier carry variance into SIZE. This stops the gate
  from silently dropping genuine-edge bins. **It does NOT change which admitted order fires.**

- **SELECTION/ORDERING (the gap this doc identifies — NOT covered by §4.2):** introduce a
  **book-wide selector** that, each reactor cycle, collects ALL admission-eligible candidate proofs
  (across families/cities), RANKS them by `expected_PnL = kelly_size × (q_posterior − cost)` (with
  the EV-per-dollar / size tiebreak under a binding bankroll), and fires **top-down under the live
  cap** — instead of first-qualifying-in-arrival-order.

**Reconciliation:** §4.2 makes the Shanghai bin *admissible with honest sizing*; the new selector
makes it *the order that actually fires when it is the best in the book*. Run §4.1 of the ruling
(CI-honesty) FIRST (it un-inflates q_lcb and the Kelly multiplier, so `expected_PnL` is computed on
honest size), then §4.2 (admit), then the selector (order). **§4.2 + PnL-ranked selection IS the
unified fix** — but §4.2 alone leaves ROOT A unaddressed.

### 4.3 Where to implement the selector (design pointers, read-only)

- The natural seam is **between `process_pending`'s collection of admission-eligible proofs and the
  submit decision**. Today `reactor.py:165-172` commits each event independently; a selector needs a
  *two-phase* cycle: (1) process every pending event to NO_SUBMIT proof (admission), accumulating
  the admitted proofs in-cycle; (2) rank the admitted set by expected_PnL and submit top-K under the
  live cap. The per-event mutex/commit discipline (`_process_event_unit`) stays for the proof
  writes; only the *submit* step moves behind the ranking.
- `_selected_candidate_proof` (`:2839`) stays as the per-family tie-break; the new selector sits
  ABOVE it, across families.
- The live-cap ledger (`reactor.py:163 LiveCapLedger`) already bounds how many orders may fire — it
  becomes the K in "fire top-K", which is exactly the bankroll-constraint the ranking is solving for.

---

## 5. RED RELATIONSHIP TEST SPEC (cross-module: ranking ↔ selection ↔ submission)

New file `tests/engine/test_best_order_selection_ranks_by_expected_pnl.py`.

```
# Created: 2026-06-01
# Authority basis: BEST_ORDER_SELECTION_ROOT_2026-06-01.md §4 — the order that fires must be the
#   highest expected-PnL admitted candidate across the WHOLE book, not first-qualifying-in-arrival.
#   Guards the process_pending -> _selected_candidate_proof -> submit selection boundary.
```

This is a **relationship test** (per Fitz methodology): it asserts a property that holds when
Module A's output (a *set* of admitted proofs) flows into Module B (the selector) — not a
single-function output check.

- **(i) Pre-fix RED — first-qualifying, not best.** Build a reactor cycle with TWO admitted events
  in arrival order: event_A = a thin speculative bin (q=0.55, EV=+2¢, Kelly $5, expected_PnL≈$0.10)
  inserted FIRST (lower `received_at`); event_B = the Shanghai near-sure-win (q=0.995, EV=+10¢,
  Kelly $44, expected_PnL≈$4.4) inserted SECOND. Arm the canary (`real_order_submit_enabled ∧
  live_canary_enabled`, fill-cap=1). Assert: **on current HEAD the submitted order is event_A**
  (first-qualifying) → RED, because the best order (B) did not fire.

- **(ii) Post-fix — best-by-expected-PnL fires.** Same setup; assert the selected/submitted order is
  **event_B (Shanghai)**, and event_A does NOT fire (cap consumed by the better order).

- **(iii) Ranking key correctness — near-sure-win outranks thin speculative.** Without submission,
  assert the selector's ranking places Shanghai (expected_PnL $4.4) **strictly above** the thin bin
  ($0.10). Add a third candidate: a fat-EV-but-tiny-Kelly bin (EV=+30¢, Kelly $2, expected_PnL
  $0.60) — assert Shanghai still outranks it (size matters), encoding "expected PnL, not raw EV".

- **(iv) Confidence floor — honestly-uncertain bin does NOT outrank Shanghai.** Construct a bin with
  HIGH point-EV but WIDE CI (q_posterior=0.99 but q_lcb=0.60, i.e. unverified/wide-spread) and tiny
  Kelly (the dynamic CI multiplier shrinks size): assert it ranks **below** Shanghai AND, under the
  §4.2-rescoped gate + Kelly variance carry, sizes ε-small. The fix must not let false confidence
  outrank earned confidence.

- **(v) INVARIANT property (antibody).** Over a grid of `(q ∈ {0.55..0.999}, cost ∈ {0.05..0.95},
  kelly ∈ {$2..$46})`: assert the selected order is `argmax(kelly × max(q−cost, 0))` over the
  admitted set, for ALL cells — making "a lower-expected-PnL admitted order fires while a higher one
  is available" **structurally unconstructable**. Fails RED on current HEAD wherever arrival-order
  disagrees with expected-PnL order (the 857-inversion case).

RED proof on HEAD: (i) and (v) fail today (selection is `fetch_pending` arrival order, not
expected-PnL argmax). (iii)/(iv) fail because no book-wide ranking exists to assert against. The
order is **relationship test → selector implementation → per-function tests** (not reversible).

---

## 6. REFERENCES

- `src/events/event_store.py:107-122` — `fetch_pending` ORDER BY `priority, available_at,
  received_at, event_id`; **no quality/edge term** → arrival-order processing.
- `src/events/reactor.py:165-172` — `process_pending` iterates events independently, per-event
  commit; **no collect-then-rank step**.
- `src/events/reactor.py:618-633` — `_receipt_money_path_blocker`: trade_score/FDR/Kelly are
  binary GATES, not rankers.
- `src/engine/event_reactor_adapter.py:2839-2853` — `_selected_candidate_proof`: `max(trade_score,
  q_lcb)` **within one family only**; returns the event's pre-named token when present.
- `src/engine/event_reactor_adapter.py:582,625-626` — per-event proof selection + `trade_score<=0`
  gate.
- `src/engine/event_reactor_adapter.py:295-357` — live submit path: builds+submits THIS event's
  receipt; **no cross-event comparison before `executor_submit`**.
- `src/engine/event_reactor_adapter.py:1524-1525` — tiny-live notional clamp ($5 canary cap;
  must lift post-canary or Shanghai submits at $5 not $44).
- `src/engine/event_reactor_adapter.py:4182-4205` — `_robust_trade_score_from_generated_inputs`
  (λ=stress=0.01).
- `src/strategy/live_inference/trade_score.py:48-52,68-71` — `robust_trade_score = p_fill ×
  min(q_lcb−c95−λ, q_live−c_stress−λ)` — the ranking-metric defect.
- `src/events/continuous_redecision.py:253-290` — `enqueue_live_redecisions`: emits one event per
  qualifying (family,bin,direction) on a flat `min_edge` floor; **no ranking at emission**.
- `docs/operations/CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01.md` §4.2 — admission-gate rescope
  (admit on point-EV+FDR, Kelly carries variance). Complementary; does NOT address selection/order.
- LIVE evidence: `state/zeus-world.db` `edli_no_submit_receipts`, decision_time
  2026-06-01T13:13Z–16:13Z, 13,227 NO_SUBMIT proofs; Shanghai-28 anchor row q=0.99495,
  c_fee=0.89056, ev=0.1044, kelly=$44.15, expected_PnL=$4.61, trade_score=0.0534, p_fill=0.7648.
```
```
