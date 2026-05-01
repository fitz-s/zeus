# 04 — Final Multi-Phase Step-by-Step Plan

This is the final staged implementation plan. It assumes authority may be modified, but it does not authorize live deployment or production mutation.

## Phase 0 — Authority admission and scope lock

### Goal
Make the repository law match the real three-layer trading architecture.

### Steps
1. Run topology doctor for pricing semantics cutover.
2. Read root `AGENTS.md`, architecture invariants, negative constraints, scoped `AGENTS.md` for strategy/execution/state, math reference, execution lifecycle reference, current_state, known_gaps, current_source_validity.
3. Create a packet scope named `pricing_semantics_authority_cutover`.
4. Modify existing authority surfaces:
   - root `AGENTS.md` money path.
   - `architecture/invariants.yaml`.
   - `architecture/negative_constraints.yaml`.
   - `docs/reference/zeus_math_spec.md`.
   - `src/strategy/AGENTS.md`.
   - `src/execution/AGENTS.md`.
   - `src/state/AGENTS.md`.
5. Add supersession text: old `P_market` edge formula is not live economic authority.

### Gates
- No new highest authority file.
- No source behavior changes yet.
- Authority conflicts named and harmonized.

---

## Phase A — Safety freeze and invariant tests first

### Goal
Make unsafe legacy behavior fail closed by default.

### Steps
1. Add flags:
   - `ALLOW_LEGACY_VWMP_PRIOR_LIVE=false`.
   - `CORRECTED_PRICING_SHADOW_ONLY=true`.
   - `CORRECTED_PRICING_LIVE_ENABLED=false`.
2. Name legacy semantics:
   - `pricing_semantics_version=legacy_price_probability_conflated`.
3. Add failing tests before implementation:
   - Quote/depth changes cost, not posterior.
   - Market prior changes posterior, not token/snapshot.
   - NO quote cannot be full-family prior.
   - Executor rejects missing immutable final limit/cost basis.
   - Executor no recompute from posterior/VWMP.
   - Reports fail/segregate mixed semantics.
   - Monitor held-token quote cannot become `p_market` vector.
4. Add static rules to detect forbidden crossings.

### Gates
- Tests fail against old behavior.
- Live entry default fail-closed.
- No live deploy.

---

## Phase B — Contracts and physical import fences

### Goal
Introduce the minimal contracts that make semantic misuse hard.

### Steps
1. Add `PosteriorBelief` / `EpistemicProbability` types if not already present.
2. Add `MarketPriorDistribution`.
3. Add `ExecutableCostCurve`.
4. Add `ExecutableCostBasis`.
5. Add `ExecutableTradeHypothesis`.
6. Add `FinalExecutionIntent` or strengthen existing `ExecutionIntent` so final fields are immutable.
7. Add import-fence tests:
   - Epistemic cannot import CLOB/venue/orderbook/fees.
   - Microstructure cannot import weather/calibration/posterior/Kelly.
   - Executor cannot accept raw `BinEdge` as corrected live authority.
8. Use Decimal in microstructure and final intent.

### Gates
- Contracts unit tests pass.
- No live source behavior changed yet beyond gates.
- No parallel venue model; reuse `ExecutableMarketSnapshotV2`, `ExecutionPrice`, `ExecutionIntent`, `VenueSubmissionEnvelope`.

---

## Phase C — Microstructure snapshot producer and CLOB sweep

### Goal
Produce real executable cost basis from token-level CLOB facts.

### Steps
1. Build or identify the single canonical runtime owner for `ExecutableMarketSnapshotV2` creation.
2. Snapshot fields must include:
   - condition id, question id, market id.
   - YES token id, NO token id, selected token id.
   - active/trading status.
   - bids/asks depth.
   - tick size.
   - min order size.
   - fee metadata.
   - neg-risk metadata.
   - orderbook hash.
   - source timestamp.
3. Implement `simulate_clob_sweep`.
4. Implement `build_executable_cost_basis`.
5. Reject missing/stale/invalid snapshots before tradeability.
6. Entry and exit both use the snapshot producer.

### Gates
- Fresh snapshot can produce cost basis.
- Stale/missing snapshot produces structured no-trade/no-exit.
- Tick/min-order/fee/neg-risk validations covered.

---

## Phase D — Epistemic posterior fusion split

### Goal
Posterior fusion consumes only belief distributions, not executable quotes.

### Steps
1. Change `compute_posterior` signature to accept `MarketPriorDistribution | None`.
2. Modes:
   - `model_only_v1` — corrected baseline.
   - `legacy_vwmp_prior_v0` — explicit legacy only, not promotion evidence.
   - `yes_family_devig_v1_shadow` — shadow-only until OOS evidence.
3. Delete or quarantine sparse monitor vector fallback as prior.
4. Make `MarketAnalysis` output posterior/payoff candidates, not `entry_price`/`vwmp` authority.

### Gates
- Raw floats cannot be market prior.
- Changing ask/depth does not change posterior.
- `model_only_v1` works without Polymarket import.

---

## Phase E — Executable hypothesis and edge construction

### Goal
Build live economic edge against executable cost, not raw quote probability.

### Steps
1. For each candidate bin/direction map to selected token:
   - `BUY_YES` -> YES token.
   - `BUY_NO` -> NO token.
2. Compute payoff probability:
   - `BUY_YES`: `P_posterior_yes[i]`.
   - `BUY_NO`: `1 - P_posterior_yes[i]`.
3. Compute cost basis from snapshot + order policy.
4. Compute:
   - `live_economic_edge = payoff_probability - fee_adjusted_execution_price`.
5. Reject before Kelly if edge <= 0 at required probe/fixed-point policy.
6. Build `ExecutableTradeHypothesis`.

### Gates
- Native NO cost comes from NO token book.
- `BinEdge.entry_price` not used as live authority.
- Size-dependent cost has fixed-point/reject-shrink policy.

---

## Phase F — Live economic FDR rebuild

### Goal
FDR selects exactly the executable hypothesis that can be submitted.

### Steps
1. Build full executable hypothesis family after fixed snapshot/cost basis.
2. Hypothesis id includes:
   - bin, direction, selected_token_id, snapshot_id/hash, cost_basis_id/hash, order_policy.
3. Run live economic FDR on this family.
4. Treat quote sensitivity as separate robustness test, not model/bootstrap uncertainty.
5. If snapshot/cost changes after FDR, reject or recompute FDR.

### Gates
- Selected hypothesis materializes same token/snapshot/cost.
- Late reprice invalidates selection.
- Research FDR and live economic FDR are separate.

---

## Phase G — Runtime no-late-reprice rewrite

### Goal
Move executable snapshot and cost basis before FDR, not just before executor.

### Old runtime
```text
forecast -> market analysis -> FDR/decision -> late executable reprice -> executor
```

### New runtime
```text
forecast
-> posterior belief
-> executable snapshots for candidate tokens
-> cost basis for executable hypotheses
-> live economic FDR
-> final execution intent
-> executor validation/submission
```

### Steps
1. Move snapshot acquisition before selection.
2. Remove mutable post-selection economic repricing in corrected mode.
3. Convert selected decision to immutable final intent.
4. Add no-trade reasons:
   - missing_snapshot.
   - stale_snapshot.
   - invalid_fee.
   - invalid_tick.
   - insufficient_depth.
   - missing_cost_basis.
   - fdr_materialization_drift.

### Gates
- Corrected path cannot mutate edge/size after FDR.
- No corrected executor call from legacy `BinEdge` alone.

---

## Phase H — Executor hardening

### Goal
Executor submits or rejects immutable intent. It never invents price.

### Steps
1. Implement corrected executor entrypoint:
   - `execute_final_intent(intent: FinalExecutionIntent)`.
2. Validate:
   - token id.
   - snapshot hash.
   - cost basis hash.
   - tick alignment.
   - min order.
   - fee metadata.
   - neg-risk flag.
   - order policy mapping.
   - collateral/allowance.
   - risk/cutover/heartbeat gates.
3. Remove corrected-mode recompute from posterior/VWMP.
4. Persist venue command before side effect.
5. Persist final signed/submitted envelope facts after response.

### Gates
- Submitted limit exactly equals final intent limit.
- No dynamic jump in corrected executor.
- Legacy path remains explicit and non-promotion-grade.

---

## Phase I — Monitor and exit symmetry

### Goal
Corrected entry cannot be paired with legacy exit.

### Steps
1. Positions carry:
   - held token id.
   - pricing semantics version.
   - entry cost basis.
   - fills and remaining shares.
2. Split monitor into:
   - probability refresh.
   - held-token quote refresh.
3. Exit EV:
   - `hold_value = payoff_probability * payout_value`.
   - `sell_value = executable_sell_quote_after_fee`.
   - exit decision compares these under exit policy.
4. Partial fill updates remaining exposure.
5. RED/ORANGE behavior interacts with exit cost basis, not `p_market` vector.

### Gates
- Held-token quote cannot become posterior prior.
- Partial sell fill reduces exposure.
- Corrected live promotion blocked until corrected exit path passes.

---

## Phase J — Persistence, reports, and backtests

### Goal
Stop legacy economics from being laundered into corrected economics.

### Steps
1. Add additive fields:
   - pricing_semantics_version.
   - market_prior_version.
   - entry_cost_source.
   - snapshot_id/hash.
   - cost_basis_id/hash.
   - final_limit_price.
   - fee_adjusted_execution_price.
2. Do not relabel old rows as corrected.
3. Promotion-grade reports hard-fail or segregate mixed cohorts.
4. Backtests without point-in-time executable snapshots/depth/hash are diagnostic only.

### Gates
- Mixed cohort report fails.
- Corrected row traces to snapshot and cost basis.
- Legacy ROI cannot satisfy corrected promotion evidence.

---

## Phase K — Source/calibration/risk blockers remain separate

### Goal
Do not confuse pricing semantics correctness with live alpha readiness.

### Steps
1. Keep Paris/HK/source truth blockers separate.
2. Keep calibration readiness separate.
3. Keep risk/RED/ORANGE/settlement blockers separate.
4. Update current_state only with evidence.

### Gates
- Pricing packet closeout does not claim live ready.
- Strategy promotion still requires source, calibration, execution, risk, settlement, and learning evidence.

---

## Phase L — Shadow, dry-run, canary ladder

### Goal
Prove observable behavior before live money.

### Steps
1. Shadow-only corrected semantics:
   - collect snapshots.
   - build cost basis.
   - run live economic FDR.
   - produce final intents.
   - do not submit.
2. Dry-run venue command facts.
3. Compare legacy vs corrected selections.
4. Only after all gates, canary with tiny caps.
5. Promotion evidence includes fill quality, maker/taker status, partial fills, cancel remainder, realized fees, adverse selection, OOS model and economics.

### Gates
- Canary is not promotion by itself.
- Realized fill/exit evidence required.

---

## Phase M — Deferred quant upgrades

Do not include in first corrected packet:

- live `yes_family_devig_v1` prior.
- negative-risk conversion/arbitrage estimator.
- queue priority model.
- fill probability model.
- adverse-selection model.
- post-only policy.
- marketable depth-bound policy.
- cost-curve Kelly optimizer.

These are future validated upgrades after semantics are correct.
