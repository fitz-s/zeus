# Exit capability (consult-3 Q1, task #52) — implementation plan & ARCH hooks

Created: 2026-06-13
Authority: docs/authority/exit_portfolio_execution_authority_2026-06-13.md (E1–E6)
+ docs/authority/consult3_exit_portfolio_execution_2026-06-13_raw.txt Q1 (reference impls).
Rollout shape mirrors C2 selection_shrinkage / C3 james_stein_blend
(flag-gated, default = current behavior, shadow-computed when off).

## The condemned current exit path (discovery)

The held-position exit decision is `Position.evaluate_exit(exit_context)` in
`src/state/portfolio.py:842`, invoked live from `src/engine/cycle_runtime.py:3650`
(`_build_exit_context` at 2967). It is a cascade of HAND-SET heuristic gates:

* `divergence_hard_threshold()` / `divergence_soft_threshold()` /
  `divergence_velocity_confirm()` (portfolio.py:1041/1053) — adverse posterior-vs-
  market gap panic (`_compute_divergence_score`, monitor_refresh.py:102).
* `flash_crash_*`, `vig_extreme` (>1.08/<0.92), CI-separation-vs-`entry_posterior`
  (portfolio.py:1133) — a belief-reversal gate keyed on the ENTRY posterior.
* `_buy_no_exit` / `_buy_yes_exit` floors/ceilings (`buy_no_floor=-0.02`,
  `buy_no_ceiling=-0.15`, …) + 2-consecutive `EDGE_REVERSAL` confirmation.
* `effective_cost_basis_usd < 1.0` micro-position hold (portfolio.py:1101).

config/settings.json `exit.*` block: every key carries a `_..._note` saying
"HARDCODED. Replace after N events" (consecutive_confirmations, near_settlement_hours,
buy_no/yes scaling/floor/ceiling, divergence_soft/hard/velocity). These are the
sweep-rank-31 thresholds the authority condemns.

### Condemned vs authority
* `entry_posterior` / `entry_ci` / `effective_cost_basis_usd` re-entering the
  decision violates **E1 (cost basis SUNK)** and **E4 (stop-loss not distinct)**.
* divergence/floor/ceiling are hand-set, not sell-dominance with updated q — the
  Denver class (E5): the posterior says "still winning", market disagrees, no
  principled exit. The CI-separation gate even HOLDS when `forward_edge>0` despite
  market disagreement (portfolio.py:1139) — exactly the refusal pathology.

## What the new policy needs (all available on ExitContext / Position)
* q_t (held-side posterior) = `exit_context.fresh_prob`
* executable bid = `exit_context.best_bid`; depth curve from book snapshot
  (`_top_book_level_decimal`, monitor_refresh.py:1477) — extend to full ladder.
* q_market-implied = `exit_context.current_market_price` (fee/spread adjusted).
* n (position_units) = `Position.effective_shares`; W = `exit_context.bankroll`.
* t_remaining = `exit_context.hours_to_settlement`/24; q_sd ~ `entry_ci_width`.
* NO entry price / cost basis enters.

## Modules built (this slice)
1. `src/strategy/exit_policy.py` — `exit_fraction_binary` (depth-aware integrated
   proceeds, no hidden liquidity → no-fill if depth insufficient), closed-form
   partial-exit `x0` fast path, `sell_all_dominance_gap`, `take_profit_net_bid_threshold`,
   `one_step_information_option_value`. Pure numpy, no DB. (E2/E3/E6)
2. `src/strategy/exit_belief.py` — `fit_blended_exit_belief` (logistic stacking
   logit π = β0+β_a·logit(q_agent)+β_m·logit(q_market) on RESOLVED snapshots),
   writes `state/exit_belief_fit.json`; `predict_q_exit`; OOS-log-score-CI license
   → else shadow-only / degrade to raw agent q with loud source label. (E5a)
3. `src/strategy/exit_calibration_alarm.py` — anytime-valid e-process `E_n`
   (likelihood-ratio agent-q vs market-blend); SUSPEND when `E_n ≥ h*`, h* DERIVED
   from the Q4d false-alarm/missed-miscalibration cost functional (Wald boundary
   `h* = (1-β)/α`-style from c_+/c_-/c_impl), NOT hardcoded 20. (E5b)
4. `scripts/fit_opportunity_growth_rate.py` — read-only replay → `state/opportunity_growth_rate.json`;
   exit defaults g*=0 until the artifact CI licenses the sell/hold sign. (E3)

## Flags (config/settings.json, all default false)
`replacement_exit_policy_enabled`, `replacement_exit_belief_blend_enabled`,
`replacement_exit_calibration_alarm_enabled`. OFF ⇒ compute exit_fraction + q_exit
+ E_n and SHADOW-LOG them on the monitor result; live `should_exit` byte-identical.

## ARCH hooks
* shadow-compute lands at cycle_runtime.py:3650 (right after evaluate_exit), mirroring
  C2's reactor hook (commit a506210dfd). Additive columns excluded from any hash.
* registries: settings consumers, script_manifest, naming_conventions,
  SQLITE_CONNECT_ALLOWLIST (read-only fitter), test_topology, schema fingerprint.

## Test results (relationship-first)
tests/strategy/test_exit_policy.py + test_exit_policy_shadow.py: 23 passed.
Existing exit-strategy relationship suite (test_exit_strategy_relationship +
constrained_posterior + entry_exit_symmetry): 48 passed (flag-off live path
unchanged). ruff clean on all new modules. settings.json valid, all 3 flags
default false. No schema files touched ⇒ schema fingerprint unaffected (shadow
values flow through the logger + summary dict, NOT a decision-artifact column —
this also keeps the canonical MonitorResult decision artifact untouched, which
the edit-time capability gate correctly guards as TRUTH_REWRITE). sklearn-gated
exit tests are blocked only by the missing env dependency (my modules use
numpy/scipy only). The 10 topology_doctor failures pre-exist on the pristine base
(stale manifest ref to a deleted _v2 script) — unrelated to this work.

## How E5 fixes the Denver class
Denver: agent q_no said "winning"; market priced it losing; divergence gate either
missed it or fired on a hand-set threshold. E5a re-fits q_exit toward the market
when resolved snapshots show the market is the better forecaster in that regime;
E5b's e-process accumulates likelihood-ratio evidence against the agent posterior
across the held class and, once `E_n ≥ h*`, SUSPENDS raw-posterior authority so the
exit rule runs on the market-blend q — making the refusal category impossible
rather than patching one threshold.
