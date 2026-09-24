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

## In flight
- Center-bias artifact (`src/calibration/day0_remaining_bias.py`).
- Replay corpus, validation certificate and VALIDATED_IDENTITY policy.
- Resolver terminal residual and composition operator (switch off).
- Source-clock cursor fix (quota burn).

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
Integrate the agents' commits after independent review. Run the release test on
outer folds. Enable the per-scope license once the table passes.
