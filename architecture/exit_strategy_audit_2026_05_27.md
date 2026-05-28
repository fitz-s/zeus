# Exit Strategy Code Audit — 2026-05-27
Base: origin/main b360211d99
Auditor: sonnet (structural verification only, no logic critique)

## Provenance verdicts

| file | last commit date | authority basis (if header present) | verdict |
|------|-----------------|-------------------------------------|---------|
| src/execution/exit_lifecycle.py | 2026-05-27 | no header | CURRENT_REUSABLE |
| src/engine/cycle_runtime.py | 2026-05-27 | no header | CURRENT_REUSABLE |
| src/state/portfolio.py | 2026-05-27 | no header | CURRENT_REUSABLE |
| src/execution/exit_triggers.py | 2026-05-27 | no header | CURRENT_REUSABLE |
| src/contracts/hold_value.py | 2026-05-27 | no header | CURRENT_REUSABLE |
| src/contracts/semantic_types.py | 2026-05-27 | no header | CURRENT_REUSABLE |

## Claim verification (A through H)

### Claim A — exit_lifecycle is state machine, not settlement

- Claim: `exit_lifecycle.py` has explicit state machine `exit_intent → sell_placed → sell_pending → sell_filled` with settlement handled separately by a harvester.
- Evidence: `src/execution/exit_lifecycle.py:1-9` docstring:
  ```
  GOLDEN RULE: confirmed sell fill creates economic close, not settlement.
  State machine: "" → exit_intent → sell_placed → sell_pending → sell_filled (economically_closed)
                 ↘ retry_pending → (back to "" after cooldown for re-evaluation)
  ```
  `src/execution/exit_lifecycle.py:206-207`:
  ```python
  EXIT_LIFECYCLE_OWNED_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})
  EXIT_LIFECYCLE_RECOVERY_STATES = frozenset({"exit_intent", "retry_pending", "backoff_exhausted"})
  ```
  Settlement handled by separate `src/execution/settlement_commands.py` with its own `SettlementState` enum (`REDEEM_INTENT_CREATED … REDEEM_CONFIRMED`).
- Verdict: CONFIRMED
- Notes: State names are `exit_intent`, `sell_placed`, `sell_pending`, `retry_pending`, `backoff_exhausted`. The fill terminal state transitions to lifecycle `economically_closed` not a sell_filled state name. Settlement is fully separate in `settlement_commands.py`.

---

### Claim B — exit is per-position in monitor loop

- Claim: `cycle_runtime.py` monitor loop iterates positions, calls `refresh_position`, builds `ExitContext`, calls `pos.evaluate_exit(exit_context)`, then `build_exit_intent` / `execute_exit`. No family-level grouping today.
- Evidence: `src/engine/cycle_runtime.py:3111`:
  ```python
  for pos in list(portfolio.positions):
  ```
  `src/engine/cycle_runtime.py:3321-3341`:
  ```python
  edge_ctx = refresh_position(conn, clob, pos)
  exit_context = _build_exit_context(pos, edge_ctx, ...)
  exit_decision = pos.evaluate_exit(exit_context)
  ```
  `src/engine/cycle_runtime.py:3407-3411`:
  ```python
  exit_intent = build_exit_intent(pos, replace(exit_context, exit_reason=exit_reason))
  outcome = execute_exit(portfolio=portfolio, position=pos, exit_context=..., ...)
  ```
  No `groupby`, `family`, or `condition_id` grouping in the monitor loop. The `_family_key_for_frontier` function at line 2216 is in the ENTRY candidate evaluation path (math_frontier), not the monitor/exit loop.
- Verdict: CONFIRMED
- Notes: Loop key is per-position `pos.trade_id`. No family aggregation exists in the exit evaluation path.

---

### Claim C — ExitContext is missing observation feasibility fields

- Claim: `ExitContext` does NOT carry `observed_high_so_far/low_so_far`, `observation_constrained_probability`, `feasibility_status`, or `family posterior`.
- Evidence: `src/state/portfolio.py:98-133` — full `ExitContext` dataclass fields:
  ```python
  exit_reason: str = ""
  fresh_prob: Optional[float] = None
  fresh_prob_is_fresh: bool = False
  current_market_price: Optional[float] = None
  current_market_price_is_fresh: bool = False
  best_bid: Optional[float] = None
  best_ask: Optional[float] = None
  market_vig: Optional[float] = None
  hours_to_settlement: Optional[float] = None
  position_state: str = ""
  day0_active: bool = False
  whale_toxicity: Optional[bool] = None
  chain_is_fresh: Optional[bool] = None
  divergence_score: float = 0.0
  market_velocity_1h: float = 0.0
  portfolio_positions: tuple = ()
  bankroll: Optional[float] = None
  ```
  No `observed_high_so_far`, `low_so_far`, `observation_constrained_probability`, `feasibility_status`, or `family_posterior` field exists.
- Verdict: CONFIRMED
- Notes: `_build_exit_context` in `cycle_runtime.py:2880-2897` populates ExitContext from `edge_ctx` and `pos` attributes. No observation feasibility bridging occurs.

---

### Claim D — forward_edge uses current_market_price not best_bid

- Claim: `evaluate_exit()` computes `forward_edge = compute_forward_edge(HeldSideProbability(fresh_prob, direction), NativeSidePrice(current_market_price, direction))` and `compute_forward_edge` is probability minus native price. Cash-out gates separately use `best_bid`.
- Evidence: `src/state/portfolio.py:739-742`:
  ```python
  forward_edge = compute_forward_edge(
      HeldSideProbability(float(exit_context.fresh_prob), self.direction),
      NativeSidePrice(float(exit_context.current_market_price), self.direction),
  )
  ```
  `src/contracts/semantic_types.py:217-226`:
  ```python
  def compute_forward_edge(held_prob: Any, native_price: Any) -> float:
      prob_value, direction = _unwrap_native_value(held_prob, "held_prob")
      price_value, _ = _unwrap_native_value(native_price, "native_price", direction)
      return prob_value - price_value
  ```
  Cash-out gate in `_buy_yes_exit` (portfolio.py ~line 122): `if shares * best_bid <= hold_value.net_value`.
- Verdict: CONFIRMED
- Notes: `current_market_price` is from `edge_ctx.p_market[0]` (orderbook mid price), which is distinct from `best_bid` (from `pos.last_monitor_best_bid`). The two are wired separately in `_build_exit_context`.

---

### Claim E — Day0 branch uses evidence_edge < threshold, not zero-out by observation

- Claim: Day0 active path checks `evidence_edge = conservative_forward_edge(forward_edge, entry_ci_width)`, threshold gate, then EV gate using HoldValue — but does NOT first zero impossible bins by WU/HKO `high_so_far`/`low_so_far`.
- Evidence: `src/state/portfolio.py` `_buy_yes_exit` (line ~905+):
  ```python
  evidence_edge = conservative_forward_edge(forward_edge, self.entry_ci_width)
  if day0_active and evidence_edge < edge_threshold:
      applied.append("day0_observation_gate")
      ...
      if shares * best_bid <= hold_value.net_value: [hold]
      return ExitDecision(True, "DAY0_OBSERVATION_REVERSAL ...")
  ```
  No `high_so_far`, `low_so_far`, or observation-impossibility short-circuit exists anywhere in `_buy_yes_exit` or `_buy_no_exit`. ExitContext carries no such fields (Claim C confirmed).
- Verdict: CONFIRMED
- Notes: The Day0 path goes directly to `evidence_edge` threshold + EV gate. Observation impossibility zeroing (e.g., "bin is physically impossible given observed high so far") is not implemented anywhere in the exit path.

---

### Claim F — HoldValue.compute_with_exit_costs is per-leg

- Claim: `HoldValue.compute_with_exit_costs()` does `gross_value=shares×p_posterior` minus fee_cost minus time_cost minus optional crowding. Per-position, not family-joint.
- Evidence: `src/contracts/hold_value.py:122-135`:
  ```python
  def compute_with_exit_costs(
      cls,
      shares: float,
      current_p_posterior: float,
      best_bid: float,
      hours_to_settlement: float | None,
      fee_rate: float,
      daily_hurdle_rate: float,
      correlation_crowding: float = 0.0,
  ) -> "HoldValue":
  ```
  `src/contracts/hold_value.py:182`: `gross_value = float(shares) * float(current_p_posterior)`
  No family aggregation; takes per-position `shares` and `current_p_posterior`.
- Verdict: CONFIRMED
- Notes: Correlation-crowding cost is the only inter-position coupling, and it enters via the scalar `correlation_crowding` float (pre-computed in `_compute_exit_correlation_crowding` in portfolio.py), not a joint optimizer.

---

### Claim G — settlement_day_observation_authority row already exists in cycle_runtime

- Claim: `cycle_runtime` builds a settlement-day observation authority row including source, station, observation_time, high_so_far, low_so_far, coverage, freshness, local-date-match.
- Evidence: `src/engine/cycle_runtime.py:3558-3700`, function `build_settlement_day_observation_authority_row`:
  ```python
  return {
      "source": ..., "station_id": ..., "observation_time_utc": ...,
      "high_so_far": _f(getattr(observation, "high_so_far", None)),
      "low_so_far": _f(getattr(observation, "low_so_far", None)),
      "coverage_status": cov, "freshness_status": freshness,
      "local_date_matches_target": local_match,
      "source_authorized_for_settlement": source_authorized,
  }
  ```
  Called at `cycle_runtime.py:3866` from `_record_settlement_day_observation_authority`.
- Verdict: CONFIRMED
- Notes: Row includes all claimed fields. The function is pure — it assembles the row dict without DB writes; writing is delegated to `log_settlement_day_observation_authority` in `src/state/db.py`.

---

### Claim H — SETTLEMENT_IMMINENT may force sell even when hold dominates

- Claim: A near-settlement exit path can trigger forced exit under missing-probability / near-settlement conditions.
- Evidence: `src/execution/exit_triggers.py:79-86`:
  ```python
  if hours_to_settlement is not None and hours_to_settlement < 1.0:
      return ExitSignal(trade_id=..., trigger="SETTLEMENT_IMMINENT", urgency="immediate")
  ```
  `src/state/portfolio.py:692-701` (inside `model_probability_missing_only` branch):
  ```python
  if exit_context.hours_to_settlement is not None and exit_context.hours_to_settlement < 1.0:
      return ExitDecision(True, "SETTLEMENT_IMMINENT", "immediate", ...)
  ```
  Also fires at `portfolio.py:791-795` in the main evaluate_exit path after any Day0 decision, regardless of hold/sell EV.
- Verdict: CONFIRMED
- Notes: SETTLEMENT_IMMINENT fires at `hours_to_settlement < 1.0` threshold unconditionally — no EV comparison against hold. It fires both when model probability is missing (special path) and in the standard path post-Day0 evaluation.

---

## K=5 structural decisions framing check

| Decision | Operator description | Current code surface touched |
|----------|---------------------|------------------------------|
| D1 | SettlementProgressConstraint typed object (Phase B observation authority bridge) | `cycle_runtime.build_settlement_day_observation_authority_row` (line 3558) exists but is NOT yet bridged into ExitContext — the row is logged to DB (via `_record_settlement_day_observation_authority`) but `high_so_far`/`low_so_far` are not threaded into `ExitContext` or called within `evaluate_exit`. New wiring required. |
| D2 | Observation-constrained posterior transform (Phase A core math) | No current surface. `ExitContext` has no `observed_high_so_far` field. `_buy_yes_exit`/`_buy_no_exit` have no impossibility zeroing. Entirely new code path needed in `src/state/portfolio.py`. |
| D3 | ExitFamilyDecision optimizer + monitor family grouping | No current surface. Monitor loop at `cycle_runtime.py:3111` is flat per-position. No `groupby` or `condition_id` family grouping exists in exit path. New grouping layer required before the `for pos in` loop. |
| D4 | Executable-bid edge authority | Partial: `best_bid` already flows through ExitContext and is used in EV gates (`_buy_yes_exit` line ~84, `_buy_no_exit` line ~207). However, `forward_edge` uses `current_market_price` not `best_bid` (Claim D). The "executable-bid" change requires rerouting `compute_forward_edge` to use `best_bid` instead, modifying the call at `portfolio.py:739-742`. |
| D5 | Deterministic impossibility short-circuit | No current surface. Neither `_buy_yes_exit` nor `_buy_no_exit` contain any early return based on observation-constrained bin impossibility. Entirely new code path needed before `evidence_edge` computation in both methods. |

**Unlisted files the operator's design will also touch:**
- `src/state/portfolio.py` (`ExitContext` dataclass fields, `_buy_yes_exit`, `_buy_no_exit`, `evaluate_exit`) — central to D2, D4, D5
- `src/engine/cycle_runtime.py` (`_build_exit_context`, monitor loop grouping) — D1 bridge, D3
- `src/contracts/hold_value.py` — D3 family-joint optimizer would require a new factory method
- Tests listed below for migration

---

## Files in scope

| file | current LOC |
|------|------------|
| src/execution/exit_lifecycle.py | 2473 |
| src/engine/cycle_runtime.py | 6107 |
| src/state/portfolio.py | 2717 |
| src/execution/exit_triggers.py | 287 |
| src/contracts/hold_value.py | 234 |
| src/contracts/semantic_types.py | 248 |
| tests/test_exit_safety.py | 2259 |
| tests/test_day0_exit_gate.py | 309 |
| tests/test_exit_lifecycle_chain_truth_void.py | 577 |
| tests/test_hold_value_exit_costs.py | 940 |
| tests/test_live_safety_invariants.py | (large; contains `evaluate_exit` mocks) |
| tests/test_runtime_guards.py | (large; contains `_evaluate_exit` stubs) |
| tests/test_day0_runtime_observation_context.py | (D1 bridge relevance) |

New files the design will create: one or more of `src/strategy/exit_family.py` (D3 optimizer), `src/contracts/settlement_progress.py` (D1 typed object), `tests/test_exit_family.py`.

---

## Existing antibody/test files already covering parts of the design

| file | what it covers | migration note |
|------|---------------|---------------|
| `tests/test_exit_safety.py` | Full exit lifecycle path incl. fill, partial fill, chain truth | Extend: add family-level tests once D3 grouping lands |
| `tests/test_day0_exit_gate.py` | Day0 `evaluate_exit` path; `SETTLEMENT_IMMINENT` without model prob | Extend: add observation-constrained posterior cases (D2/D5) |
| `tests/test_hold_value_exit_costs.py` | `HoldValue.compute_with_exit_costs` per-leg math | Extend: add family-joint optimizer tests once D3 lands |
| `tests/test_exit_lifecycle_chain_truth_void.py` | Chain-truth void path in exit lifecycle | No migration needed; orthogonal to family changes |
| `tests/test_day0_runtime_observation_context.py` | Day0 observation context building in cycle_runtime | Directly relevant to D1 bridge; extend to verify ExitContext gets `high_so_far` |
| `tests/test_live_safety_invariants.py` | `evaluate_exit` mocks + live safety | Must update mocks when ExitContext gains new fields |
