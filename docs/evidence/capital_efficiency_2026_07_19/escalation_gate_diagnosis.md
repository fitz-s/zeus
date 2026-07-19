# Diagnosis — why `TAKER_ESCALATED_AFTER_REST` fires on 18/914 entries, not the 573 arm-eligible

Question from `fill_funnel.md` §6/§7: 573 unfilled GTC rests aged past the 5-minute escalation arm
floor (`escalation_arm_floor_seconds = min(20min, REST_VALUE_REFRESH_MIN_AGE_SECONDS=300s)`), but the
escalation lane (88.9% fill rate, 2.8x plain maker) only fired on 18/914 entries. Read-only,
`sqlite3 -readonly` / `?mode=ro` throughout (`state/zeus_trades.db` for `venue_commands` /
`venue_order_facts`, ATTACH `state/zeus-world.db` for `decision_certificates`), window
2026-06-19..2026-07-19.

## Method

For every ENTRY `venue_command` whose latest `venue_order_facts` state is `CANCEL_CONFIRMED`/`EXPIRED`
with `observed_at - created_at >= 300s` (arm-eligible per `_family_rest_state`,
`src/engine/event_reactor_adapter.py:24343-24346`), found the next ENTRY command on the same
`token_id` and matched its `ActionableTradeCertificate` (`final_intent_id` substring match against
`decision_id`, same technique as `fill_funnel.md`).

- **599** arm-eligible unfilled rests in the window.
- **342** got a subsequent decision with a matched cert; **257** never traded again on that token in
  the window (a separate question — see Open Question below, not diagnosed here).
- Of the 342: **265 (77%) landed on `MAKER_TAKER_FORBIDDEN`**, only **10 landed on
  `TAKER_ESCALATED_AFTER_REST`**, 40 on `REST_DEFAULT` (escalation flag not armed at that specific
  decision — window/ordering edge cases, not investigated further), 16 on `HOLD_REST_IN_PROGRESS`.

## The arm/trigger mechanism itself works

`_family_rest_state` (PASS 1 arming, PASS 2 single-flight antibody with the GAP-4 serial-re-rest
exemption) correctly identifies the 599 arm-eligible cases and correctly re-decides them on the next
cycle — via the in-process `rest_pull_families` fold-in for screen-driven pulls
(`src/events/reactor.py:10037-10065`, same tick) and via `_emit_live_redecision_events_for_families`
for TTL-driven pulls (`src/main.py:5201-5218`, `origin="c3_staleness_cancel"`). Both paths were traced
and both fire. (Aside, non-causal: `src/main.py:5009` `_emit_rest_pull_redecisions` — a third,
`origin="rest_pull"` wrapper — is defined and tested by
`tests/execution/test_rest_pull_redecision_emit.py` but has zero production callers; it is dead,
superseded by the reactor.py in-process path, and harmless. Not touched — unrelated to the escalation
rate and removing it doesn't change any of the numbers above.)

## The actual reason: two pre-existing, evidenced, tested safety gates fire on the re-decision

`select_rest_then_cross_mode` (`src/strategy/live_inference/mode_consistent_ev.py:460-657`) evaluates
`escalated_after_rest=True` correctly, but still requires `taker_admissible` (branch 6a: escalated +
taker inadmissible -> `MAKER_TAKER_FORBIDDEN`, a deliberate no-trade, not a re-rest — see the
2026-06-20 "no-identical-re-rest" comment at that line). Two independent, already-live gates decide
`taker_admissible` here:

**1. buy_no (214/265 = 81% of the `MAKER_TAKER_FORBIDDEN` cases): the selection-curse bound.**
`_event_bound_q_exec_lcb` (`event_reactor_adapter.py:15303`) calls
`corrected_side_q_lcb` (`src/decision/selection_curse_bound.py`, artifact
`state/selection_curse_bound.json`, `armed_sides=["buy_no"]`, built 2026-06-24) — the
settlement-evidenced realized-NO-rate lower bound as a function of price, documented in
`docs/evidence/live_order_pathology/2026-06-23_selection_curse_design_and_impl.md`. Verified directly
against the live artifact for real certs pulled from the 265: e.g. `q_lcb_5pct=0.841`,
`c_cost_95pct=0.67` (raw bound clears: 0.67 <= 0.84) — but `corrected_side_q_lcb(price=0.67) = 0.630`,
which is *below* the 0.67 cost, so the cross correctly self-rejects. Recomputing this for the sampled
`MAKER_TAKER_FORBIDDEN`-after-arm population: **248/265 (94%) clear the raw `q_lcb_5pct` bound but
would be refused once the curse correction is applied at the crossing price** — i.e. this bound, not
a defect in the arm/trigger loop, is what turns most of the arm-eligible population into no-trades.
This is exactly the behavior
`tests/strategy/live_inference/test_q_exec_lcb_taker_gate.py::test_lower_q_exec_lcb_blocks_a_taker_that_clears_q_lcb`
pins as an antibody ("reverting the q_exec_lcb bound ... re-admits the blocked-taker case"), and the
design doc's own OOS validation shows the raw-admitted buy_no population has aggregate after-cost EV
**-45.74** vs the bound-admitted subset's **-3.87** (+41.87 EV recovered by refusing exactly these
crosses). Since buy_no is 80% of all order volume (`fill_funnel.md` §1), this bound alone accounts for
the large majority of the escalation-lane's apparent under-use.

**2. buy_yes (51/265): the taker spread guard.** The `buy_yes` `MAKER_TAKER_FORBIDDEN` sample is
concentrated at deep-longshot prices (`c_cost_95pct` ~0.008-0.14, `q_lcb_5pct` ~0.02-0.08) where the
book is thin enough that `taker_spread_guard_reason` (relative spread > `TAKER_MAX_RELATIVE_SPREAD`
= 0.25, and absolute spread over the one-tick-cross allowance) blocks the cross — pre-existing,
general-purpose illiquidity protection, not escalation-specific and not curse-bound related (buy_yes
is `BUY_YES_IDENTITY` in the curse bound, unaffected by gate 1).

## Verdict: DESIGN INTENT, not a bug — no code changed

Both gates are deliberate, evidenced (settlement-graded, walk-forward validated), reviewed (PR #419),
tested (`tests/decision/test_selection_curse_bound*.py`,
`tests/strategy/live_inference/test_q_exec_lcb_taker_gate.py`,
`tests/engine/test_q_exec_lcb_event_seam.py` — 67 tests across the affected suites, all green,
re-run as part of this diagnosis), and explicitly designed to also gate the escalation-deadline cross
(the design doc lists `_mode_consistent_ev_for_proof` and `_fresh_rest_then_cross_mode` — the
escalation seams — as two of the three TAKER seams the fix targets). Per the task's own instruction:
when escalation is blocked by recorded design intent, report instead of override. Forcing the
escalation lane to ignore `q_exec_lcb` would very likely reproduce the exact toxic buy_no losses
(-45.74 aggregate after-cost EV, OOS) the 2026-06-23 fix was built to remove — the fill-funnel's
88.9%/2.8x escalation-fill-rate statistic measures *fill rate*, not EV, and the curse bound is
precisely the correction that decouples the two for mid-price buy_no.

## Open question (not diagnosed here, out of scope for this task)

257/599 (43%) arm-eligible unfilled rests never got any subsequent decision in the window at all, on
families whose `target_date` was typically 1-3 days out (i.e. not simply "the event already
happened"). Whether this is normal edge decay (the model's view moved on) or a gap in how cancelled
families re-enter the fair-batch redecision cursor is a separate question about the general admission
pipeline, not the escalation gate specifically, and was not investigated further here.
