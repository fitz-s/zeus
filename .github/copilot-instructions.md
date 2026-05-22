# Copilot review — Zeus money-path

Zeus: live Polymarket weather derivatives. Real capital. Review from the
money-path inward — find the economic object the diff touches, then ask
the domain-specific question for that object.

## Step 1 — Identify the economic object

| Object | Canonical fields | Owner |
|--------|-----------------|-------|
| Probability | p_raw, p_cal, p_market, p_posterior, edge | src/calibration/**, src/engine/evaluator.py |
| Executable price | final_limit_price, orderbook_top_bid/ask, fill_price | src/contracts/execution_price.py |
| Identity | condition_id, token_id, strategy_key, outcome_label | src/contracts/**, src/state/** |
| Side effect | place_limit_order, redeem, cancel | src/venue/**, src/execution/** |
| Settlement | settlement_value, physical_quantity | src/contracts/settlement_semantics.py |
| Evidence | n_wins, n_settled, ci_lower, breakeven_win_rate | src/analysis/evidence_report.py |
| State machine | lifecycle_phase, venue_command, settlement_outcome | src/state/**, src/contracts/** |

## Step 2 — Domain question per object

**Probability.** Is p_raw used where p_cal is required? Does edge include
fees + spread + depth? Is display_price or market_price flowing into a
limit price? (Forbidden.) Is orderbook_top_ask the BUY cost, top_bid
the SELL cost?

**Executable price.** Does final_limit_price round BUY up, SELL down?
Is limit_price promoted to final_limit_price before submission?
Is market_order used? (Forbidden in execution.)

**Identity.** Does strategy_key flow unmodified from decision_event to
regret_decompositions? Are condition_id and token_id distinct? Is
outcome_label always YES or NO, never a raw string?

**Side effects.** place_limit_order outside gateway? (INV-24.) Every
venue side effect needs a venue_commands row first (INV-28, INV-30).
Redeem gated on CHAIN_EMPTY not CHAIN_UNKNOWN (INV-18). RED cancels
pending and sweeps active (INV-19).

**Settlement.** Every DB settlement write passes through
SettlementSemantics.assert_settlement_value()? wmo_half_up for
WU/NOAA/CWA; oracle_truncate only for HKO. Swapping silently
mismatches the oracle.

**Evidence / promotion.** Cohort scope consistent across n_decisions,
n_settled, n_wins queries? Cross-strategy contamination enters through
shared experiment_id unless joined through decision_events.
ci_lower compared against breakeven_win_rate — never hardcoded 0.5?
Bayesian prior stays at Beta(2,2)?

**State machines.** Lifecycle phases from LifecyclePhase enum only
(INV-07). venue_command transitions journaled before side effects.
settlement_outcome monotonic-forward.

## Severity

**Critical** (block): live-money loss; identity error (condition_id,
token_id, YES/NO); SettlementSemantics bypass; market order;
place_limit_order outside gateway (INV-24); side effect without
venue_commands (INV-28, 30); void on CHAIN_UNKNOWN (INV-18);
schema data loss; secret exposure.

**Important**: probability crossing without provenance (INV-21, 33–35);
held-token quote into posterior (INV-36); strategy_key drift (INV-04, 22);
DB-before-JSON inversion (INV-17); evidence cohort scope gap;
ci_lower vs hardcoded threshold; missing relationship test.

**Nit**: style / naming. Suppress when Critical/Important exist.

## Report format

Header: `Reviewed: <objects>; Coverage: full|partial; Findings: N C, N I, N N`.
Per finding: `Severity | Path:line | What | Why | Fix`.
No speculation. Mark **Uncertain** if unresolvable from diff.

Full tier-scope: `.github/instructions/tier-scope.instructions.md`.
Invariants: `architecture/invariants.yaml`.
