# Fill-suppression chain — why few families clear (RULE 1: suppression, not market-efficiency)

- Created: 2026-06-15
- Authority basis: live log + code (live_admission.py, governor.py, edli_live_cap_usage),
  post-#123-un-latch. RULE 1: a candidate failing the gate is PRESUMED our suppression until
  settlement proves otherwise — never concluded as absent alpha.

## State after #122 + #123 fixes
Governor un-latched (count 0), one real venue-acknowledged buy_no fired (Chengdu 06-17 high
@0.72, RESTING 1¢ under the 0.73 ask, unfilled), then continuity stalled. 0 fills.

## Continuity: NOT cap-held (hypothesis falsified)
`edli_live_cap_usage`: 0 RESERVED (39 CONSUMED, 416 RELEASED). One resting order does not hold
the cap. So "one order then silence" is NOT live-cap exhaustion.

## The proximate suppressor on the rejected families
Recent reactor cycles (19:02/19:38/19:55): `EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22
capital_efficiency_lcb_ev=19 other=3; best=Guangzhou 30°C 06-16 buy_yes q_lcb=0.1961 price=0.2700
ev_per_dollar=-0.2738` (and Taipei 30°C: q_lcb=0.2157 price=0.2600).
- Gate: `live_admission.live_capital_efficiency_rejection_reason` (line 130) rejects when
  `(q_lcb − price)/price ≤ 0` — the CONSERVATIVE q_lcb must beat the price.
- For these families the conservative q_lcb sits under the price (e.g. 0.196 < 0.27).
- The Chengdu 06-17 family DID clear (q_no_lcb_modal > NO_cost) and fired an order — so the gate
  is not globally broken; it selectively passes over-priced-favorite families.

## RULE-1 driving directions (do NOT conclude the market is right)
1. **q_lcb conservatism / band width**: is q_lcb pushed below the price by an over-wide band or a
   one-sided cap (#91), so candidates with genuine POINT edge are rejected at the conservative
   bound? (k=1.30 calibrated rings 0–3 to 0.95–1.12, but ring≥4 tail still 2.7× under-dispersed and
   lead-dependent — q_lcb on longer leads may be over-shrunk.) Lever: per-lead floor / band-width
   refit, settlement-validated.
2. **Forecast metadata (law 8)**: is OUR q for specific city/bins mis-calibrated by a station
   representativeness / bin-edge / rounding offset, hiding real over-pricing? Re-examine per-city
   bin-station provenance.
3. **Different horizon/mechanism**: day0 nowcast lane (prior consult: materially higher Kelly edge
   at day0) — a different alpha source than the 24h+ forecast harvest.
4. **Maker→taker fill** (separate, the @0.72-rests-under-0.73 issue): convert a q_lcb-justified
   resting bid into a settlement-honest taker fill (only when the conservative bound still covers
   the ask) so justified intent becomes a fill, not a 900s-timeout cancel.

## In flight
ChatGPT Pro Extended consult (conversation 6a30a014…) is analyzing 1–4 against the live code
(family_decision_engine, executor, event_reactor_adapter, governor, payoff_vector) + the live
evidence bundle. On return: verify its diagnosis against live data, then implement the
settlement-honest fix (no caps, no gate-loosening, no one-order hack).
