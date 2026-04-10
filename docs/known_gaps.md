# Known Gaps — Antibody Register

> Role note: active operational gap register and antibody tracker. Useful for present-tense runtime gaps, but not principal architecture authority.

This file records structural gaps that have been fixed and the tests that make them impossible to regress.
Each entry: what the gap was, what the antibody test is, and when it was closed.

---

## DAY0_EXIT_GATE_STALE_PROBABILITY

**Gap:** `test_day0_exit_gate_uses_fresh_probability`  
**Closed:** 2026-04-07  
**Severity:** P0 — caused 100% stake loss on all extreme-tail buy_yes positions in day0_window  

**What failed:**  
When the day0 observation signal fell back to stale `p_posterior` (observation or ENS data
unavailable), `fresh_prob_is_fresh=False` was set on the ExitContext. `evaluate_exit()` then
returned `INCOMPLETE_EXIT_CONTEXT` before reaching the day0 EV gate — silently holding the
position to settlement.

Even if the code had reached the EV gate, it compared `best_bid <= stale_p_posterior` (= entry
price). On any losing position at an extreme tail (p_entry=0.2–2%), market reprices toward 0
faster than the model updates, so `best_bid < entry_price` always — the gate would have said HOLD.

**Two-layer fix (portfolio.py):**  
1. `evaluate_exit()`: when `day0_active=True` and the only missing authority field is
   `fresh_prob_is_fresh`, waive the INCOMPLETE and allow through (logged as
   `day0_stale_prob_authority_waived`).  
2. `_buy_yes_exit()`: when `fresh_prob_is_fresh=False`, substitute
   `effective_prob = min(stale_prob, best_bid * 1.1)` in the EV gate instead of the stale entry
   probability (logged as `stale_prob_substitution`).

**Antibody tests:** `tests/test_day0_exit_gate.py`  
- `test_stale_prob_does_not_produce_incomplete_exit_context_in_day0`  
- `test_stale_prob_substitution_applied_in_validations`  
- `test_fresh_prob_uses_model_not_market`  
- `test_stale_prob_outside_day0_still_returns_incomplete`  

**Why the 10% buffer (`best_bid * 1.1`):**  
Gives the model a small tolerance over the current bid to prevent whipsawing on noisy microstructure
(bid can transiently understate fair value by a few ticks). Without the buffer, a single-tick bid
drop below stale prob would trigger exit. With it, the market must be clearly below the stale
estimate before exiting.

---

## STALE-PROB FORWARD-EDGE SUBSTITUTION (closed 2026-04-07)

**Gap:** `test_stale_prob_forward_edge_substitution_neutralizes_positive_illusion`  
**Closed:** 2026-04-07  

**What the gap was:**  
When the day0 signal fell back to stale `p_posterior = entry_price` (e.g., 0.02) and the market
had moved against the position (bid = 0.01), the forward edge was POSITIVE (`0.02 - 0.01 = +0.01`).
The DAY0_OBSERVATION_REVERSAL condition requires `evidence_edge < threshold`. With a positive
forward edge, the exit gate was never reached — position held to settlement, full stake lost.

**Fix (`evaluate_exit()`, portfolio.py):**  
When `day0_active=True` and `fresh_prob_is_fresh=False`, substitute `_fresh_prob =
min(stale_prob, current_market_price)` before computing `forward_edge`. This caps the model at
market price, making `forward_edge ≤ 0` and neutralizing the stale illusion. Logged as
`day0_stale_prob_forward_edge_substitution`.
