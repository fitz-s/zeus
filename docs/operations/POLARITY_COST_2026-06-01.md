# POLARITY AUDIT — COST / EDGE / DIRECTION-SELECTION
# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: Operator read-only polarity audit (YES≠1−NO). HEAD 6fcd05a69f.
#   Angle: q_NO paired with cost_YES; NO cost derived as 1−cost_YES (FORBIDDEN —
#   YES and NO are SEPARATE order books with independent asks).

## VERDICT: NO POLARITY BUG. Cost/edge/direction are per-side independent. (CONFIRM correct)

The OPERATOR LAW ("NO must be independently grounded, never 1−YES") is **upheld in
code**. Both the q (win-probability) side and the cost side use direction-matched,
independently-sourced quantities. No cross-wire, no naive-complement cost found.

---

## 1. COST SIDE — NO ask read from the independent NO order book (CONFIRM)

`executable_cost(book, direction, shares)` → `_levels_for_direction`:
- `src/strategy/live_inference/executable_cost.py:158-167` — buy_yes→`book.yes_asks`,
  buy_no→`book.no_asks`. Separate level lists; no derivation of one from the other.
- `executable_cost.py:70-85` — `price_type="ask"` for both buy sides; taker fee applied
  to the side's OWN walked average. NO cost is NOT `1 − yes_cost`.

Book construction keeps YES and NO depth physically separate:
- `event_reactor_adapter.py:4119-4128` (`_native_quote_book_from_snapshot_row`) —
  `yes_asks=_parse_quote_levels(yes_depth["asks"])`, `no_asks=…(no_depth["asks"])`;
  yes_depth/no_depth resolved per-token via `_depth_for_token_or_label` (4111-4116).
- `executable_cost.py:136-155` (`quote_book_from_executable_snapshot`) — same: requires
  native YES **and** NO depth present (fail-closed at 142-145 if either missing).

Explicit anti-complement guards exist and are the documented law:
- `executable_cost.py:98-105` — `assert_not_no_complement_cost` raises on
  `c_no = 1 - yes_price`; `reject_forbidden_cost_source("no_complement")`.

Direction→side map is correct (no YES/NO swap):
- `event_reactor_adapter.py:4481-4490` (`_native_side_for_direction`): buy_no→`NO_ASK`,
  sell_no→`NO_BID`. buy_yes→`YES_ASK`. Correct.
- `event_reactor_adapter.py:4248-4253` (`_p_fill_lcb_for_direction` levels map):
  buy_no→`book.no_asks`. Fill-LCB uses the SAME NO book as the cost. No cross.

## 2. p_market_no — from the NO book, not 1−p_market_yes (CONFIRM)

`_market_analysis_from_event_snapshot`, `event_reactor_adapter.py:3334-3347`:
- `yes_cost = native_costs[(cond,"buy_yes")]`; `no_cost = native_costs[(cond,"buy_no")]`
  — two independent dict lookups (3340-3341).
- `yes_price = yes_cost[1].value`; `no_price = no_cost[1].value` (3342-3343) — each is the
  ExecutionPrice from that side's own `executable_cost` walk.
- `p_market_no.append(no_price)` (3345) — NO market price is the NO-book ask, NOT
  `1.0 - yes_price`. CONFIRM independent.
- `native_costs` built per (cond, direction) in `_native_costs_by_candidate_direction`
  (4444-4478) — each direction calls `_execution_price_from_snapshot(..., direction=…)`
  → `executable_cost` on the matching side. No crossing.

## 3. DIRECTION SELECTION — q and cost never cross (CONFIRM)

`_generate_candidate_proofs`, `event_reactor_adapter.py:2868-2926`:
- Per candidate, two rows are constructed (2876-2879):
    `(yes_token, "buy_yes", yes_q,        yes_lcb)`
    `(no_token,  "buy_no",  1.0 - yes_q,  no_lcb)`
- For EACH row, cost is fetched with the SAME `direction` (2890-2894):
    `_execution_price_from_snapshot(row, selected_token_id=token_id, direction=direction)`
  → buy_no pulls the NO-book ask. So the buy_no proof pairs **q_NO (=1−yes_q) with
  cost_NO**; the buy_yes proof pairs **q_YES with cost_YES**. No q_YES↔cost_NO swap.
- Score (2897-2903) `_robust_trade_score_from_generated_inputs(q_posterior=q_value,
  q_lcb_5pct=q_lcb, execution_price=<this side>, …)` — q and cost are the same side's.

NOTE on `1.0 - yes_q` for q_NO (2878): this is the WIN-PROBABILITY (a forecast posterior
on a MECE 2-way bin), where 1−P(YES wins)=P(NO wins) is a mathematical identity, NOT a
price. The OPERATOR LAW forbids deriving NO **cost/price** as 1−YES (separate books);
it does NOT forbid the win-probability identity. q_NO_LCB is independently grounded:
`no_lcb = q_lcb_by_direction[(cond,"buy_no")]` (2872), produced by the NO-direction
hypothesis bootstrap (see §4), not as `1 − yes_lcb`. CONFIRM correct.

## 4. q_NO LOWER BOUND — from the NO-direction hypothesis scan (CONFIRM)

`_canonical_probability_and_fdr_proof`, `event_reactor_adapter.py:3104-3135`:
- `hypothesis_by_label_direction[(range_label, "buy_no")]` (3121) — the family scan
  produces a SEPARATE hypothesis per direction; the NO ci_lower is its own bootstrap
  percentile, not a complement of the YES bound.
- `lcb_by_direction[(cond,"buy_no")] = hyp.ci_lower + cost_by_direction["buy_no"]` (3124),
  with `cost_by_direction["buy_no"] = p_market_no_vec[index]` (3118) — i.e. the NO LCB is
  rebuilt against the NO-book cost. CONFIRM no YES contamination.
- Fallback (3132): when the NO side is non-executable the neutral q_point is
  `1.0 - yes_posterior` (the win-prob identity, non-actionable: p_value=1.0,
  prefilter=False) — never enters a live trade. Acceptable.
- Masked NO LCB `event_reactor_adapter.py:3864`:
  `min(no_lcb, 1.0 - q_value)` — caps NO LCB at the NO win-probability ceiling
  (1−q_YES). This is a conservative CAP on a probability, not a cost complement. Correct.

## 5. trade_score kernel — per-direction, no side mixing (CONFIRM)

`src/strategy/live_inference/trade_score.py:42-80`:
- `robust_edge = min(q_5pct − c_95pct − λ, q_posterior − c_stress − λ)` (48-51 / 68-71).
  Both q_5pct/q_posterior and c_95pct/c_stress are passed in for ONE direction by the
  caller (`_robust_trade_score_from_generated_inputs`, adapter:4313-4322, uses the SAME
  `q_posterior`, `q_lcb_5pct`, and the side's `c_cost_95pct`). The kernel has no concept
  of YES/NO and cannot cross sides itself. CONFIRM.
- Contract test `tests/engine/test_trade_score_direction_semantics.py` — 4/4 PASS
  (re-run 2026-06-01). Includes an inversion-detector control (L108-123) proving that
  feeding the YES posterior into a buy_no score WOULD flip the verdict positive — the
  forbidden wiring the correct path never constructs.

## 6. KELLY inputs — selected direction's (q, price) only (CONFIRM)

`src/strategy/kelly.py:31-63` (`kelly_size`): `f* = (p_posterior − price)/(1 − price)`.
- `price = entry_price.value` (52) — the typed ExecutionPrice for the SELECTED direction
  (assert_kelly_safe at 50). For a buy_no selection the caller supplies the NO-book ask
  ExecutionPrice and q_NO as p_posterior. No YES/NO mismatch in the formula itself.
- `(1 − price)` here is the Kelly payoff denominator (win pays $1 per share at `price`),
  a STANDARD Kelly identity on the selected contract's own price — NOT a NO=1−YES cost
  derivation. Correct.

---

## DECISIVE LIVE EVIDENCE — Singapore 2026-06-01 (zeus_trades.db snapshots)

`state/zeus-world.db.executable_market_snapshots` is empty in this checkout (0 rows);
the populated live capture is `state/zeus_trades.db` (132,082 rows; 1,998 Singapore).
Stored `orderbook_depth_json` is a SINGLE-token book per row (top-level `asks`/`bids` +
one `asset_id`); YES and NO are separate rows keyed by their own token.

Reconstructing 20 Singapore conditions with both a YES-token snapshot and a NO-token
snapshot, top-of-book asks:

| condition_id (trunc) | YES_ask | NO_ask | YES+NO | 1−YES | NO − (1−YES) |
|---|---|---|---|---|---|
| 0x061ee0377fc3ff | 0.999 | 0.999 | 1.998 | 0.001 | **+0.998** |
| 0x8ce2759ca0a2fb | 0.999 | 0.999 | 1.998 | 0.001 | **+0.998** |
| 0x871abef6b55d5a | 0.999 | 0.999 | 1.998 | 0.001 | **+0.998** |
| 0x46005ea125a049 | 0.999 | 0.999 | 1.998 | 0.001 | **+0.998** |
| 0x18ac77bb99cdcb | 0.999 | 0.999 | 1.998 | 0.001 | **+0.998** |

If NO cost were derived as `1 − YES_ask`, NO_ask would be **0.001**. It is **0.999**
— a 0.998 divergence. YES_ask + NO_ask = 1.998 ≠ 1.0. This is the unmistakable
signature of two INDEPENDENT, wide-spread order books (both sides' best asks sitting near
the cap on an illiquid market), exactly what the code reads. **The NO cost is the NO-book
ask, independently grounded. REFUTE the naive-complement hypothesis.**

## CROSS-WIRE / NAIVE-COMPLEMENT SITE INVENTORY

| Site (file:line) | Quantity | Independent? | Verdict |
|---|---|---|---|
| executable_cost.py:158-167 | cost levels per direction | YES (separate lists) | CONFIRM correct |
| executable_cost.py:98-105 | anti-complement guards | n/a (guard) | CONFIRM guard present |
| event_reactor_adapter.py:3340-3345 | p_market_no from no_cost | YES (own lookup) | CONFIRM correct |
| event_reactor_adapter.py:2876-2903 | proof q↔cost pairing | YES (same direction) | CONFIRM no cross |
| event_reactor_adapter.py:3124 | NO LCB + no-book cost | YES (NO hypothesis) | CONFIRM correct |
| event_reactor_adapter.py:2878,3132 | q_NO = 1−yes_q | win-prob identity, not price | CONFIRM allowed |
| event_reactor_adapter.py:3864 | masked NO LCB cap | prob cap, not cost | CONFIRM correct |
| trade_score.py:48-51,68-71 | edge = q−cost per side | caller-paired | CONFIRM correct |
| kelly.py:62 | f*=(q−price)/(1−price) | selected dir price | CONFIRM correct |

NO FORBIDDEN naive-complement COST site found anywhere in the cost/edge/direction chain.
