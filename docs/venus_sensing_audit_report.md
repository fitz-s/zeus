# Venus Sensing Audit Report

Date: 2026-04-06

## Executive summary

The new Venus sensing layer is doing the right kind of work: it can now observe cross-surface truth drift instead of only reading a status summary. The latest report proves the sensing pipeline is live and useful, but it also confirms that Zeus still has several unresolved semantic fractures. Venus is therefore not finished; it has crossed the threshold from "script treadmill" to "usable detector," but it has not yet become a closed antibody loop.

## What the current report detects

### 1) Truth-surface drift remains real
- `trade_decisions.entered = 49`
- `position_current.count = 12`
- `positions_json.active_count = 8`
- `ghost_positions = 33`
- `canonical_path_alive = false`
- `portfolio_truth_source = working_state_fallback`

This is the core finding: the system can still make decisions, but the semantic path from decision to durable position truth is broken or partially degraded.

### 2) Settlement / lifecycle freshness is stale
- `settlement_harvester` fails
- latest settlement target date is `2026-03-30`
- settlement gap is `173.3h`

This indicates that the settlement lifecycle is not keeping pace with the current trading cycle. The report should keep treating this as a high-signal structural issue, not just a stale-data warning.

### 3) The status surface is healthy enough to read, but not healthy enough to trust fully
- `status_summary_completeness = PASS`
- `risk_state.level = YELLOW`
- `reality_contracts.stale_count = 1` (`GAMMA_CLOB_PRICE_CONSISTENCY`)

The sensing layer can now see enough of the system to reason, but it should not confuse “readable” with “correct.”

### 4) The fact tables are still empty
- `outcome_fact = 0`
- `execution_fact = 0`

These are not the most urgent blocker today, but they are part of the long-term truth chain and should remain visible in the report.

## What Venus should propose next

The report should not stop at diagnosis. It should point to the exact places where the next durable fix belongs.

### Proposal targets

1. **Make canonical truth path self-evident**
   - Target: loader / projection path that currently falls back to `working_state_fallback`
   - Goal: make fallback mode impossible to miss and impossible to treat as normal
   - Antibody form: test + runtime assertion + clearer surface naming

2. **Close the decision → position → settlement lifecycle gap**
   - Target: the boundary where `trade_decisions` should materialize into durable position truth
   - Goal: reduce `ghost_positions` and eliminate stale lifecycle drift
   - Antibody form: reconciliation test and lifecycle invariant test

3. **Distinguish hard failures from soft staleness**
   - Target: report classification and healthcheck semantics
   - Goal: avoid treating a readable-but-stale surface as either fully healthy or fully broken
   - Antibody form: severity contract and explicit naming for blocking vs non-blocking staleness

4. **Turn report findings into queued antibodies**
   - Target: `venus_antibody_queue.json`
   - Goal: ensure Venus outputs an implementable next step, not only a diagnosis
   - Antibody form: structured proposal object with file target, invariant, and test expectation

## How to avoid becoming a script treadmill

Venus becomes a script treadmill when it repeats checks that merely rediscover known failures. It becomes an antibody when every finding is forced through this funnel:

1. **Detect a relationship failure**
   - Example: decision state and position state disagree.

2. **Name the category of failure**
   - Example: canonical path fallback masquerading as normal operation.

3. **Choose the durable artifact**
   - Type / schema if the bad state should be unrepresentable.
   - Test if the bad state is a regression.
   - Runtime contract if the bad state can appear transiently.
   - Diagnostic only if the category is already understood and low risk.

4. **Write the antibody proposal in machine-readable form**
   - File target
   - Invariant
   - Expected failure mode
   - Verification step

5. **Do not keep re-reporting the same category**
   - Once a category is converted into a durable artifact, the next report should mention the artifact, not re-explain the symptom.

## Current verdict

- **Sensing architecture:** good enough to use
- **Truth-surface integrity:** still degraded
- **Antibody closure:** not yet complete
- **Next step:** convert the highest-signal mismatch into one concrete antibody, then ensure the report recognizes the category as closed

## Suggested immediate antibody candidates

- Canonical path fallback should become explicit, actionable, and test-covered
- Settlement freshness should be elevated from a passive metric to a lifecycle invariant
- `ghost_positions` should be reduced by a reconciliation invariant between decision and position layers

