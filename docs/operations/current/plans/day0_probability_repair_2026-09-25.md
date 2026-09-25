# Day0 probability repair: plan of record (2026-09-25)

Goal: every city, every lead, a settlement-calibrated probability that updates within
seconds of new evidence and beats the market. Operator directive: fix the probability,
never disable a market.

## Evidence
- `docs/operations/current/source_inventory_and_probability_race_2026-09-24.md` §8.
- Consult answers: `/tmp/cgc/answer_REQ-20260924-154114-47cdbb.txt` (review) and
  `/tmp/cgc/answer_REQ-20260924-181005-501f04.txt` (design).

## Decisions
1. **Calibration corpus survives revisions.** Current-recipe replay of historical
   causal information sets feeds the fitter. Filtering is by evaluation recipe, not by
   the revision that was live when the data was collected. Provenance is preserved;
   nothing is relabelled.
2. **Day0 center bias**, early local hours. Fitted on the terminal-bin likelihood,
   metric × phase pooled with station shrinkage. Cells are selected inside outer folds
   and activated only with ≥0.02 nats improvement and a 95% upper bound < 0.
3. **Resolver-graded terminal operator** replaces the mirror-agreement survival
   mixture: Q = s·Q+ on non-violation, (1−s)·G− on failure. s is estimated
   hierarchically from resolver-settled outcomes. It ships behind a switch until
   validated.
4. **Entry licensing.** VALIDATED_IDENTITY / qualified correction / qualified fallback
   per scope. SourceIdentityBaseline stays in place until the validation table exists.
   Rejections happen per claim, never per market.
5. **Maker entries** require an execution-conditioned bound
   P(W|F) ≥ (qL + fL − 1)/fL, or a qualified fill/payoff model.

## Landed 2026-09-25
- `dbe1b38b3` + `800300db1` Day0 center shift (live, v20).
- `9097550c2` capture `correction_applied` bound to the correction's own `applied` bit. Before this, 5,607 baseline certificates were excluded from the market-anchored fit corpus.
- `23749250b` replay corpus, validation certificate and VALIDATED_IDENTITY policy. Inert: no live consumer. Replay coverage is 3.2% (54/1697). Blockers: `FORECAST_REMATERIALIZATION_NOT_IMPLEMENTED` (forecast lane) and `CARRIER_INPUTS_NOT_ARCHIVED` (Day0; certificates do not carry carrier extremes).
- `14244e742` executable-forecast reader verdict made family-scoped. Previously one dark family, Jinan LOW 09-25, rejected every auction cut from 00:30Z to 06:40Z.

## In flight
- Resolver terminal operator `203912a84` (branch `claude/agent-abb535b220f9757df`), switch OFF. It is in review. The US °F LOW non-violation UB95 of +0.0033 must be decided before ON.
- Source-clock cursor fix (branch `claude/agent-a273d8c92070f3b88`). Review verdict: NO-GO. A structural-gap source still reports `global_models_unavailable`, so it stays retryable. Rework is in progress.
- ENS local-day boundary interval evidence (new agent worktree). 27 venue families for 09-25..27 have no posterior because every ENS cycle is excluded:
  - LOW: `REJECTED_BOUNDARY_AMBIGUOUS`.
  - HIGH: certificate `native_interval_gap` or `boundary_can_exceed_inner`.
  - Seed discovery never admits them, so this is a one-way door.
  - The authority's preferred fix is interval widening (`statistical_calibration_addendum_2026-06-13.md` D2).
- Provisional-revision likelihood unavailable for NOAA-settled Tel Aviv, Istanbul and Moscow Day0. The routing trace is in progress.

## Host fault (operator-owned)
`webfilterproxyd` (the macOS content filter) is at 43 GB compressed. The venue heartbeat goes LOST repeatedly (4 times in 23 min), and Polymarket cancels resting maker orders 3–4 min after posting. Zeus does not tune timeouts around it. The operator must restart or exempt the filter.

## Release test (predeclared; freeze before the next untouched evaluation)
- Chronological outer folds; hourly rows averaged within city-day before pooling.
- At least 100 city-day clusters across ≥14 settlement dates.
- 2,000 resampling replicates, simultaneous one-sided 95% bounds.
- Center-repair cells: ≥0.02 nats improvement, upper bound < 0.
- No collateral degradation: ≤0.01 nats, full-simplex Brier ≤0.002.
- Overstatement E[q − I] ≤0.03, and ≤0.01 in the q ≥ 0.95 band.
- 90%-mass prediction-set miss rate: upper bound ≤13%.

## Rollback
Each artifact ships behind a fail-to-current-recipe or explicit-switch path. Rolling
back selects the previously qualified bundle. It never rewrites attribution on
historical receipts.

## Next action
1. Land the cursor fix after rework and re-review. Then restart, and verify that
   `replacement broad reseed cursor commit` appears and the quota rate drops.
2. Land the resolver operator with the switch OFF once review says GO.
3. Land the boundary interval path after validation.
4. Archive Day0 carrier inputs on certificates so replay coverage can grow. Then run
   the release test on outer folds and enable the per-scope license once the table
   passes.
