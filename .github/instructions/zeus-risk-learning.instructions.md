---
applyTo: "src/analysis/**/*.py,src/backtest/**/*.py,src/strategy/**/*.py,src/state/*evidence*.py,src/state/*experiment*.py"
---

# Zeus risk, evidence, and learning review

These paths govern promotion authority, cluster risk, and shadow/paper
learning loops. Errors here produce systematic bias in go-live decisions
or mask capital-concentration risk.

## Evidence cohort scope

`build_evidence_report()` queries n_decisions, n_settled, and n_wins
from three different query paths. All three must scope to the same
cohort (strategy_key + experiment_id + cohort_tag + source filter).
A mismatch — e.g. n_decisions scoped to a source but n_wins not —
produces a denominator/numerator pair from different populations.

Cross-strategy contamination: regret_decompositions rows must be joined
through decision_events.decision_event_id to verify strategy_key matches
(Finding 3 / SEV2-1 join). Queries that join only through experiment_id
without the decision_events join will count another strategy's wins.

## Bayesian credible interval

Posterior is Beta(2+k, 2+n-k) for Beta(2,2) prior. Promotion gate:
`ci_lower > breakeven_win_rate + cost_of_capital`. Check:
- Prior is exactly (2,2), not (1,1) or (0,0).
- `breakeven_win_rate` comes from strategy profile metadata, not
  a hardcoded constant.
- n_settled (not n_decisions) is the trial count fed to the CI.

## Strategy authority in routing

`strategy_key` determines which strategy routes a market candidate.
Flag any code that derives routing from condition_id, token_id, or
market type alone — without consulting the strategy registry.

## Cluster / exposure dimensions

Risk cap checks must use all registered exposure dimensions:
weather_family_key (city+date+metric), outcome_label (YES/NO),
and strategy_key. Checks that collapse dimensions (e.g. summing
YES+NO exposures into a single position cap) silently allow
factor-2 over-concentration.

## Shadow replay integrity

Shadow replay harness must never produce decision_events rows that
contaminate live evidence. `source` must be `shadow_decision` for all
replay-generated rows. Replay must not commit to the live world DB —
use an isolated fixture DB or transaction rollback.
