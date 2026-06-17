# Timing architecture from first principles — what the requirements actually demand, and the correct complete rebuild (2026-06-15)

```
Created: 2026-06-15
Authority basis: operator request — derive the required timing precision FROM the market requirements,
identify what is most easily missed (not the memorized failure modes), and define the correct complete rebuild.
Grounded in live state: diurnal_peak_prob ALIVE (14,530), settlement_day_observation_authority ALIVE-stale (56),
day0_conditioner.py present, day0_horizon_platt_fits=0 (nowcast lane gated dead), day0_metric_fact/day0_nowcast_runs=0.
```

## 0. The reframe (the thing most easily missed, stated once)

**This market's largest and SAFEST edge is not forecasting — it is near-arbitrage on an outcome that becomes
empirically observable HOURS before settlement.** The daily-high contract settles on `max(temperature)` over the
station-LOCAL civil day. In most climates the high is set in early-to-mid afternoon (solar-driven). After the peak,
`observed-so-far` is a monotonically tightening floor that, with high probability, already equals the settlement
value — while the market may still price residual uncertainty (thin books, slow participants, co-traders). The edge
in that window is **being faster and more correct than the market about a number that is already nearly
determined.** That is a fundamentally different, higher-Sharpe, lower-variance edge than predicting the value days out.

Zeus is architected as a **forecaster** (predict the value, trade on model skill) and the **nowcaster/near-arb lane
is built but dead.** It already has the empirical machinery — `diurnal_peak_prob.p_high_set` (when is the high set),
`settlement_day_observation_authority` (is the settlement source bound), `day0_conditioner` (condition bins on
observed-so-far) — and the decision lane that would exploit them writes nothing (`day0_metric_fact`=0,
`day0_nowcast_runs`=0, gated on an empty `day0_horizon_platt_fits`). **The system reasoned toward the right
architecture and then switched it off — and there is no instrumentation showing the missed near-arb trades, so the
blindness is invisible.** Everything below follows from correcting this inversion.

## 1. Required precision is NON-UNIFORM — allocate it by marginal value of information

The information arrival has a sharp temporal shape, so a uniform cadence (the current ~20–120s warm cycle applied
the same everywhere) is simultaneously over-precise where it does not matter and absent where it does. Two regimes:

- **Regime A — FORECAST (T-7 … morning of D):** edge from model skill; uncertainty wide; information arrives on
  NWP cycle boundaries (00/06/12/18Z, each disseminating hours later). Required precision: **coarse.** Hourly
  polling, issue-time-plane freshness, freshness-weighted multi-provider fusion. Latency budget: minutes are fine.
- **Regime B — NOWCAST / near-arb (day D, from the local-afternoon peak onward):** edge from observed-so-far binding
  the outcome + SPEED. Required precision: **fine and competitive.** Each new settlement-source observation is a
  large discrete information event (it can eliminate bins and collapse uncertainty). Latency budget: must beat the
  market's reaction to the same public observation. This is where minute-scale matters — and ONLY here.

The precision budget must be a function of dq/dt · (edge at risk) — which spikes in the station-local-solar peak
window and is near-zero far out. The current allocation is inverted.

## 2. When each computation must happen (the correct schedule, derived not inherited)

| Computation | Trigger (not a fixed cadence) | Time plane |
|---|---|---|
| Multi-provider de-bias + fusion | each provider's run DISSEMINATES (real availability, not cycle label) | issue/valid time; freshness-weighted |
| Forecast→observation blend weight | continuously through day D; shifts from forecast-dominated (morning) to observation-dominated (post-peak) | station-local-solar |
| Regime A→B switch | when `p_high_set(city,month,local_hour)` crosses a threshold AND settlement source has begun reporting | station-local civil + solar |
| Conditional bin recompute | EVENT-DRIVEN on each new SETTLEMENT-SOURCE observation (eliminate bins below floor; survival-condition the rest) | settlement-source event time |
| "Outcome-known" confidence | continuously post-peak: `P(no higher reading in remaining local hours)` from diurnal climatology + current state | station-local-solar |
| Near-arb order | immediately on a bin-recompute that opens edge vs live price; latency must beat market reaction | monotonic for the race; UTC for record |
| Settlement grade / learning | at settlement-source daily-max finalization (its cutoff), not Zeus wallclock | settlement authority |

The governing rule: **schedule each computation at the moment its inputs become available AND before its output's
value decays** — keyed to the market's information arrival, never to a uniform clock.

## 3. What is most easily missed — the never-thought-of (grounded, not memorized)

1. **Peak-TIMING is itself a forecast.** You must predict WHEN the high occurs (local) to know when observed-so-far
   binds. EXISTS (`diurnal_peak_prob.p_high_set`) but feeds no live decision (lane dead). Without it you cannot
   tell regime A from regime B.
2. **"I observed X" ≠ "it will settle X".** The near-arb is only valid in the SETTLEMENT source's terms — its
   station, rounding, cutoff time, and revision policy. A faster nowcast source (free METAR/WU) that diverges from
   the settlement source destroys the arbitrage. EXISTS (`settlement_day_observation_authority` with
   `source_authorized_for_settlement`, `local_date_matches_target`) but is stale (last 2026-05-28). The
   correctness condition for EVERY regime-B trade is: floor is expressed in settlement-source terms AND that source
   is authorized AND local_date_matches_target.
3. **Post-peak ≠ certain — the late-spike tail.** The outcome-known confidence is `1 − P(new high in remaining
   hours)`, which is time-of-day-conditional (survival), not a step function at the peak. Must be computed
   continuously through the evening. Easily replaced by a naive "peak passed → done."
4. **In regime B the edge is SPEED on public information, not model skill.** It decays as the market incorporates the
   same observation. So regime B imposes a COMPETITIVE-latency requirement (obs→q→order < market reaction) that does
   not exist in regime A. You can have a better model and still lose the regime-B edge by being slow.
5. **Regime misclassification → systematic, INVISIBLE blindness.** If the system is blind in regime B (it is), it
   only trades regime-A forecast bets, which looks like "honest no-edge" but is "blind in the edge window." The only
   thing that would reveal it — regret on outcome-known opportunities — requires the Day0 metric lane to be alive
   (it is dead). The blindness hides itself.
6. **Bin elimination is a discrete information event.** A new observed floor does not just shift q — it ZEROES all
   bins below it and renormalizes the rest. The bin probabilities must be a live conditional given observed-so-far,
   not a static forecast-derived q. (`day0_conditioner` exists; its output is not persisted/acted in the nowcast lane.)
7. **Solar-time vs civil-time vs DST vs longitude-within-timezone.** The peak is solar-driven; observation and
   settlement are civil-local (with DST); a station at the west edge of a timezone peaks ~1h later in civil time than
   the east edge. `solar_daily` EXISTS (59,235) — is it used to TIME regime B, or only for irradiance? If the peak
   window is assumed at a fixed civil hour, it is wrong by up to ~1h + DST.
8. **The forecaster-vs-nowcaster inversion itself (see §0).** The deepest miss: the architecture is built around the
   wrong primary regime, with the right one dead.

## 4. The correct, complete rebuild (not "fix the 89 items")

Rebuild around the value-of-information time structure; the 89 fix-rows become consequences, not the plan.

1. **Make the A/B regime boundary a first-class, explicit, station-local-solar object** driven by `diurnal_peak_prob`
   + `solar_daily`. Every decision carries its regime. This is the spine.
2. **Resurrect regime B as an EVENT-DRIVEN lane:** ungate the nowcast/metric writer from the empty
   `day0_horizon_platt_fits` (the dead-dependency cascade); trigger on each settlement-source observation; persist
   `day0_metric_fact` honestly (obs event-time, obs_age on the EVENT clock not monotonic cache-age — the WP-18 fix).
3. **Enforce the source-binding correctness condition** on every regime-B trade: only near-arb when
   `settlement_day_observation_authority` says authorized + local_date_matches_target, and express the floor in
   settlement-source terms (station, rounding, cutoff). Refresh that authority live (it is stale).
4. **Survival-condition the remaining bins** for the late-spike tail using `diurnal_peak_prob`; size by
   `edge − P(new-high tail) − source-divergence risk`, not by a peak-passed step.
5. **Instrument regime B specifically and HONESTLY:** the obs→nowcast→order latency (competitive budget) AND the
   regret on outcome-known opportunities missed. This is the single metric that makes the current blindness visible
   and lets the binding constraint be optimized as a number instead of re-tuned by intuition.
6. **Allocate the timing/precision budget by marginal value of information:** coarse, cycle-driven regime A;
   fine, event-driven, competitive regime B. Stop spending precision on far-out fusion cycle-coherence (which does
   not move a trade) and spend it on the peak-window loop.
7. **Fix regime A's one real timing defect in passing:** freshness-weight the multi-provider fusion and record true
   dissemination time (Q1) — but as a secondary, lower-Sharpe feeder to regime B, not the main engine.

**Correctness standard (settlement-graded):** a regime-B design element is correct only if, on settled days, the
observed-so-far floor (in settlement-source terms, at the decision time) actually held at settlement, and the
realized after-cost EV on the trades it enabled is positive. Not "code changed"; not "invariant green" — settled.

## 5. The one-line answer to each of the operator's four sub-questions
- **Precision needed:** non-uniform — coarse in the forecast regime, minute-scale + competitive-latency in the
  station-local-solar peak window; allocate by marginal value of information.
- **When each computation happens:** keyed to information arrival (provider dissemination; settlement-source
  observation events; the solar peak), not a uniform clock — see §2 table.
- **Most easily missed:** that the real edge is near-arbitrage on the near-determined post-peak outcome; that
  "observed ≠ will-settle" (source binding); the late-spike survival tail; the speed-not-skill edge; and that the
  blindness in the edge window is self-concealing. The scaffolding exists and is dead.
- **How to rebuild:** invert the architecture to regime-B-primary, event-driven, source-bound, survival-conditioned,
  honestly instrumented, budget-allocated by value of information; settlement-graded as the only "done."
