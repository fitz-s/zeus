# Fill-Wall Trace: why 9/10 live BUY-NO harvest orders expired unfilled

- Date: 2026-06-16
- Author: Tracer (read-only causal trace, no edits)
- Question: Of the last ~10 live BUY-NO harvest orders, 9 EXPIRED unfilled and only 1 FILLED. Decide H1 (q_lcb over-conservative -> our bid sits below the NO ask, we never cross, the thin maker rest expires; real edge suppressed) vs H2 (NO ask is genuinely above honest q_no -> resting is the only legal move; thin books just don't get hit).
- Evidence basis: live state DBs read read-only (`?mode=ro`): `zeus_trades.db`, `zeus-world.db`, `zeus-forecasts.db`. Live code on the active branch (`src/strategy/live_inference/mode_consistent_ev.py`, `src/engine/event_reactor_adapter.py`).

---

## Trace Report

### Observation (stated before interpretation)

The recent live BUY-NO order outcomes (zeus_trades.db `venue_commands` joined to `venue_submission_envelopes`, side=BUY, created_at >= 2026-06-11), by venue lifecycle state and submission shape:

| created_at (UTC) | market | posted_bid | order_type | post_only | state |
|---|---|---|---|---|---|
| 2026-06-16T06:13:38 | 2549521 | 0.58 | GTC | 1 | ACKED |
| 2026-06-16T06:03:05 | 2549630 | 0.63 | GTC | 1 | ACKED |
| 2026-06-16T00:41:03 | 2549604 | 0.72 | GTC | 1 | EXPIRED (4 partials, 0 fill) |
| 2026-06-15T16:29:23 | 2549604 | 0.76 | GTC | 1 | SUBMIT_REJECTED |
| 2026-06-15T14:30:16 | 2549499 | 0.74 | GTC | 1 | **FILLED** (rested 8 min, then hit) |
| 2026-06-15T13:49:10 | 2549710 | 0.70 | GTC | 1 | EXPIRED |
| 2026-06-12T13:04:26 | 2514097 | 0.02 | GTC | 1 | EXPIRED |
| 2026-06-12T10:59:08 | 2514086 | 0.65 | GTC | 1 | EXPIRED |
| 2026-06-12T09:16:52 | 2514032 | 0.73 | GTC | 1 | EXPIRED (1 partial, 0 fill) |
| 2026-06-12T03:37:01 | 2512746 | 0.16 | GTC | 1 | EXPIRED |
| 2026-06-12T02:54:23 | 2502701 | 0.64 | GTC | 1 | EXPIRED |
| 2026-06-11T17:56:46 | 2501685 | 0.60 | FOK | 0 | EXPIRED (taker; rested >1 day as PARTIAL) |

All orders are confirmed BUY-NO (`venue_commands.token_id == executable_market_snapshots.no_token_id`, `outcome_label=NO`). The "wall" is real: of the terminal recent orders, the single FILLED one filled **as a resting maker bid that a counterparty happened to take after 8 minutes** (FILL lifecycle: ACKED 14:30 -> PARTIAL 14:38 -> FILLED 14:38), not by crossing.

### Posted bid vs market NO best-ask at submit (executable_market_snapshots bound to each order)

| created_at | state | posted_bid | NO best_ask | NO best_bid | depth@ask | rest-or-take |
|---|---|---|---|---|---|---|
| 2026-06-16T06:13 | ACKED | 0.58 | 0.59 | 0.58 | 178 | REST (joined bid, 1c below ask) |
| 2026-06-16T06:03 | ACKED | 0.63 | 0.65 | 0.62 | 111 | REST (improve) |
| 2026-06-16T00:41 | EXPIRED | 0.72 | 0.73 | 0.72 | 7 | REST (1c below ask, thin) |
| 2026-06-15T14:30 | FILLED | 0.74 | 0.76 | 0.73 | 122 | REST (2c below ask) |
| 2026-06-15T13:49 | EXPIRED | 0.70 | 0.77 | 0.69 | 400 | REST (7c below ask) |
| 2026-06-12T13:04 | EXPIRED | 0.02 | 0.69 | 0.63 | 15 | REST (67c below ask) |
| 2026-06-12T10:59 | EXPIRED | 0.65 | 0.67 | 0.65 | 110 | REST |
| 2026-06-12T09:16 | EXPIRED | 0.73 | 0.74 | 0.73 | 8 | REST |
| 2026-06-12T03:37 | EXPIRED | 0.16 | 0.54 | 0.51 | 1 | REST (38c below ask) |
| 2026-06-12T02:54 | EXPIRED | 0.64 | 0.73 | 0.71 | 44 | REST (9c below ask) |
| 2026-06-11T17:56 | EXPIRED | 0.60 | 0.60 | 0.55 | 17 | (FOK; at-ask) |

**In every single recent order, posted_bid < NO best_ask. We never crossed (took). We always rested.** This is a fact, not inference.

### The decisive comparison: (best_ask + taker_fee) vs q_lcb vs honest q_no

The proof economics for the SUBMITTED orders live on `decision_certificates` (type `ActionableTradeCertificate`, 436 rows) in `zeus-world.db`, carrying `q_live` (honest un-shrunk q_no point), `q_lcb_5pct` (conservative reservation), `c_fee_adjusted` (all-in taker cost = best_ask + fee), `proof_execution_mode_intent`, `rest_then_cross_policy`. Recent buy_no submissions:

| decision_time | city | honest q_no (q_live) | q_lcb | c_fee = ask+fee | mode | policy | edge left (q_live - c_fee) |
|---|---|---|---|---|---|---|---|
| 2026-06-16T06:12 | Singapore | 0.8750 | 0.7822 | **0.5900** | MAKER | REST_DEFAULT | **28.5c** |
| 2026-06-16T06:01 | Moscow | 0.7858 | 0.7824 | **0.6500** | MAKER | REST_DEFAULT | 13.6c |
| 2026-06-16T00:39 | Chengdu | 0.9165 | 0.8624 | **0.7452** | MAKER | REST_DEFAULT | **17.1c** |
| 2026-06-16T00:32 | Busan | 0.8403 | 0.7821 | **0.7300** | MAKER | REST_DEFAULT | 11.0c |
| 2026-06-16T00:27 | Amsterdam | 0.8317 | 0.7831 | **0.6500** | MAKER | REST_DEFAULT | 18.2c |
| 2026-06-15T17:56 | Chongqing | 0.9858 | 0.8421 | **0.7500** | MAKER | REST_DEFAULT | **23.6c** |
| 2026-06-15T14:29 | Hong Kong (FILLED) | 0.8015 | 0.7828 | **0.7600** | MAKER | REST_DEFAULT | 4.2c |

The ordering in EVERY case is: **c_fee (ask+fee) <= q_lcb <= q_live**. The market NO ask sat **below even the conservative q_lcb**, and far below honest q_no. The taker cross was ADMISSIBLE under the live #127 rule (FIX B: a cross is admissible iff `taker_all_in_cost <= q_lcb`, `mode_consistent_ev.py:514-520`). Yet the policy posted a maker rest in all but one of 436 cases.

System-wide confirmation (all 436 ActionableTradeCertificates parsed):
- **436 of 436** had `c_fee_adjusted <= q_lcb_5pct` — the taker lane was admissible in **100%** of submitted orders.
- Policy distribution: **REST_DEFAULT = 69, TAKER_ESCALATED_AFTER_REST = 1, None (legacy pre-#127 path) = 366**.
- Mode distribution: **MAKER = 70, TAKER = 2, None = 364**.
- The cross lane fired exactly **once** in 436 orders.

### Hypothesis Table

| Rank | Hypothesis | Confidence | Evidence Strength | Why it remains plausible / why ranked here |
|---|---|---|---|---|
| 1 | **H3 (the real defect): the REST_DEFAULT doctrine suppresses the cross.** The take-vs-rest policy rests post_only GTC by default even when the taker is fully admissible (ask+fee <= q_lcb). The cross lanes that could override it (event-end-near < 180m, fleeting-edge < 360m, escalated-after-rest) are structurally unreachable for the day-ahead weather harvest. | **High** | **Strong** (tier 2: 436/436 certificates + matched policy field + book snapshots + read of the predicate) | Every expired order rested with a positive, admissible cross available; only lane 6 (REST_DEFAULT) fired; the cross lanes' guards are unsatisfiable for this population. |
| 2 | H1 (q_lcb over-conservative, the task's RULE-1 default) | **Refuted as the binding cause** | Strong contradicting evidence | q_lcb (0.78-0.86) sat ABOVE the ask in 100% of cases; it never pulled the reservation below the ask. q_lcb is NOT what blocks the cross. (q_lcb being below q_live is real over-conservatism but it is not the fill-wall mechanism — even the conservative q_lcb cleared the ask.) |
| 3 | H2 (no takeable edge; ask above honest q_no) | **Refuted** | Strong contradicting evidence | Honest q_no (q_live 0.79-0.99) was 4-28c ABOVE the all-in taker cost in every order. There was genuine, large takeable edge. |

### Evidence For

- **H3 (REST_DEFAULT suppressor):** `select_rest_then_cross_mode` (`mode_consistent_ev.py:427-593`) is a 6-lane policy. Lane 6 (`POLICY_REST_DEFAULT`, line 587-593) is the catch-all: "rest post_only GTC ... a fresh-book EV preference for crossing is NOT a license to cross." The only lanes that cross are: lane 3 escalated-after-rest, lane 4 event-end < 180m (line 567), lane 5 fleeting-edge < 360m (line 581-584). Live `minutes_to_event_end` for the recent harvest (computed from `target_date` local-midnight in city tz, `event_reactor_adapter.py:8127`) is **2028-2971 minutes** (34-50 hours out). So lanes 4 and 5 are structurally unreachable; only the escalation loop remains, and it fired once in 436 orders.
- **H3:** the submitted-order proofs all carry `proof_execution_mode_intent=MAKER`, `rest_then_cross_policy=REST_DEFAULT`, `proof_maker_limit_price` = `tick_down(min(bid+tick, ask-tick, q_lcb))` (`maker_limit_price`, `mode_consistent_ev.py:218-243`) — a structurally non-crossing bid. The FILLED Hong Kong order posted at 0.74 (= ask 0.76 - 2 ticks) and was lucky enough to be hit in 8 minutes.
- **Against H1/H2:** 436/436 had `c_fee <= q_lcb <= q_live`; the cross was always admissible and always positive-edge.

### Evidence Against / Gaps

- **H3 gap:** `minutes_to_event_end` is not persisted on the certificate; I recomputed it from `target_date` + city tz (matches the live formula at `event_reactor_adapter.py:8127-8155`). Confidence high but not a stored artifact.
- **H3 escalation fragility (additional defect):** the escalation cross (`_family_rest_state`, `event_reactor_adapter.py:8159-8248`) requires the prior rest to have expired UNFILLED with `matched_size <= 0`. Two recent expired rests (2026-06-16 00:41 = 4 partials; 2026-06-12 09:16 = 1 partial) had `matched > 0`, which DISQUALIFIES them from licensing the escalation cross. Plus the HOLD antibody (lane 1, `unexpired_family_rest`) blocks any new family order while a rest is open. So even the one escape hatch (rest -> 120m -> cancel -> re-cert -> cross) is itself partially broken for partially-filled rests.
- **q_lcb over-shrink (task #91 suspicion) is real but not the wall:** q_live - q_lcb runs ~2-14c (e.g., Chongqing q_live 0.986 vs q_lcb 0.842 = 14c of shrink). That is genuine conservatism in the reservation, and it does cap the maker limit. But it is NOT the fill-wall cause: even the shrunk q_lcb cleared the ask in 100% of cases. Fixing q_lcb shrink would NOT make us cross; fixing the policy would.
- **Market-anchor cap (task #91 named suspect) is confirmed INERT on the live path:** `market_anchored_no_lcb` exists (`src/strategy/live_inference/market_anchor.py`) but (a) is flag-gated and scoped only to near-center classes C1-C3, leaving the far-NO harvest C4 untouched (`event_reactor_adapter.py:8499-8530`), and (b) the live selection authority is `q_source=qkernel_spine`, on which the legacy market_anchor "stays INERT" (`event_reactor_adapter.py:2485`). This corroborates task #41f907f5cc (cap retired on the live branch) over the task #91 title. Empirically, q_lcb sat above the ask everywhere, so no anchor cap bit.

### Rebuttal Round

- **Best challenge to the leader (H3):** "Maybe REST_DEFAULT is correct and the books are simply so thin that even crossing wouldn't fill — the thin-depth case the policy is protecting against." Rebuttal: the depth-at-best-ask in the book snapshots is 100-400 shares on most orders (Singapore 178, Moscow 111, HK 122, 06-15 13:49 = 400), comfortably above the small harvest sizes ($5-$56). Crossing would have filled immediately against visible depth. The thin ones (depth 1, 7, 8) are the minority. The spread guard (`TAKER_MAX_RELATIVE_SPREAD=0.25`) already forbids crossing genuinely wide/thin books, so admissible crosses are by construction into measurable two-sided books. The leader stands: we declined admissible, deep-enough, positive-edge crosses by policy.
- **Second challenge:** "Is q_live really the honest un-shrunk q_no, or already shrunk?" Rebuttal: the certificate carries BOTH `q_live` (point) and `q_lcb_5pct` (5% lower bound) with `q_source=qkernel_spine`; q_live > q_lcb by 2-14c confirms q_live is the un-shrunk point estimate and q_lcb is the conservative bound. The forecast posterior `q_json` in `zeus-forecasts.db` is the same family of un-shrunk point posteriors. Confidence high.

### Convergence / Separation Notes

- H1 and H3 are NOT the same root cause and must stay separate. H1 says the RESERVATION (q_lcb) is too low to clear the ask. H3 says the reservation clears the ask fine, but the POLICY refuses to act on it. The data separates them cleanly: q_lcb >= ask in 100% of cases, so H1's mechanism never triggers; H3's mechanism (REST_DEFAULT lane) triggers in 69/70 modern orders.
- H2 and H3 are mutually exclusive and H2 is refuted by the same datum that confirms H3 (ask far below honest q_no).

### Current Best Explanation (provisional only on the recomputed minutes-to-event-end)

**Mixed verdict, but decisively NOT H1 and NOT H2.** The fill wall is caused by **H3: the REST_DEFAULT doctrine in `select_rest_then_cross_mode`**. We post structurally non-crossing maker bids by default on a genuinely-cheap, admissible, positive-edge NO ask, into thin weather books that usually don't get hit before the 120-minute escalation deadline cancels the rest. The market ask was NOT above honest q_no (H2 refuted) and q_lcb was NOT below the ask (H1 refuted — q_lcb is over-shrunk relative to q_live, but that shrink is not the fill-wall mechanism). The single FILLED order filled by luck (resting bid hit in 8 min), not by design.

The edge being forfeited is large: **4-28 cents of after-cost EV per order** (q_live - (ask+fee)), left on the table by resting instead of crossing.

### Critical Unknown

The single missing fact: **why was REST_DEFAULT chosen as the doctrine for the day-ahead harvest at all, given the cross is admissible and positive-edge?** Specifically, is the REST-first design an intentional maker-rebate / adverse-selection-avoidance policy (operator K4.0 directive 2026-06-11, "a favorable all-in alone does NOT license an immediate cross — the Karachi antibody"), or an over-correction whose escalation escape-hatch is too slow/fragile (120-min deadline + matched<=0 disqualifier + HOLD antibody) for thin weather books? The answer decides whether the lever is "lower the escalation deadline / fix the partial-fill disqualifier" vs "add a cross lane for admissible deep-edge day-ahead harvests."

### Discriminating Probe (single highest-value next step)

**Settle-grade the rest-vs-cross counterfactual on the expired orders.** For each of the ~9 expired BUY-NO orders, the market settled (or will settle) at the target. Compute realized PnL of (a) what we did = no fill = $0 captured, vs (b) the counterfactual immediate cross at `c_fee_adjusted` held to settlement. If the counterfactual crosses are settlement-POSITIVE after cost (they will be, since ask+fee was 4-28c below honest q_no and these are high-q NO favorites), that is the on-chain proof that REST_DEFAULT forfeited real, bankable, settlement-graded edge — directly binding the operator's standing goal. This is one query against `zeus-world.db` `settlements`/`outcome_fact` joined to the expired commands' `c_fee_adjusted`, no live action required, and it converts the cents-of-edge estimate into realized dollars.

Secondary probe (cheaper, code-only): trace whether lowering `TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END` is even the right lever, or whether the harvest needs a NEW lane "admissible deep-edge day-ahead cross" — because at 34-50h to event end, NO existing cross lane can ever fire for this population.

### Uncertainty Notes

- `minutes_to_event_end` is recomputed, not a stored artifact (formula matches live; high confidence).
- 364/436 certificates predate #127 (policy=None) — they are on the legacy one-shot path and not used for the policy-distribution claim about the modern lane; the 70 modern-path orders (69 REST, 1 escalated cross) carry the load-bearing signal.
- The 2026-06-11 FOK order is anomalous (a FOK that rested as PARTIAL for >1 day before expiring) — likely a separate venue/lifecycle bug, out of scope for this trace; flagged for follow-up.
- The settlement-graded counterfactual (the discriminating probe) has NOT been run here; the edge claim is currently in expected-value cents from honest q_no, not yet realized settled dollars.

---

## Provenance / currency of code read

- `src/strategy/live_inference/mode_consistent_ev.py` — last commit `ac02923` 2026-06-15 23:38 ("#125 + q_lcb-capped rest-then-cross #127"). CURRENT. The #127 FIX B (`mode_consistent_ev.py:493-520`) and the 6-lane policy are the live law.
- `src/engine/event_reactor_adapter.py` — last commit `f237314` 2026-06-16 00:57. CURRENT. Hosts the live caller (`:7833`, `:7968`), `_family_rest_state` (`:8159`), `_minutes_to_family_event_end` (`:8127`), market_anchor inert note (`:2485`).
- `src/execution/maker_rest_escalation.py` — last commit `9f70e9c` 2026-06-11. CURRENT_REUSABLE (own header audit). The 120-min deadline cancel job.
- `src/strategy/live_inference/market_anchor.py` — exists but INERT on the live spine path (corroborated by data + `:2485`); not the suppressor.
