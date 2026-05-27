# Copilot review — Zeus money-path

Zeus: live Polymarket weather derivatives. Real capital. Review by
changed economic object, then **localize the root runtime path**. Do not
stop at the nearest helper, test, or manifest if the failure occurs in a
reader, daemon, executor, scheduler, or DB owner boundary.

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

## Step 2 — Root runtime path

For every Critical/Important finding, name the runtime boundary that
can fail: reader, daemon, executor, scheduler, DB writer/reader, or
manifest consumer. Helper-only comments are insufficient if the caller
boundary is the bug.

## Step 3 — Finding quality contract

`Severity | Path:function/table | Object | Invariant | Runtime failure | Existing guard miss | Required fix/test`

Invalid: "add tests" without naming invariant + runtime path; style
comments while Critical/Important risk exists; helper-only comments when
caller boundary is the bug; docs/topology warning treated as runtime
failure; citing docs without current code evidence.

## Historical miss patterns

- FC-01 forecast: latest snapshot ≠ production bundle; check coverage/readiness/source_run/snapshot.
- FC-02 discovery: helper test ≠ daemon path; per-city breadth, not slug-only or global cap.
- FC-03 execution: selection-time snapshot ≠ submit-time; stale must recapture or fail closed.
- FC-04/05 scheduler: guard must parse every wiring shape, not only literal `add_job`.
- FC-07 source planes: settlement/Day0/historical-hourly/forecast-skill are NOT interchangeable.
- FC-08 schema: new table needs db_table_ownership, writer, reader, ghost-exclusion.

## Severity

**Critical** (block): live-money loss; identity error (condition_id,
token_id, YES/NO); SettlementSemantics bypass; market order;
place_limit_order outside gateway (INV-24); side effect without
venue_commands (INV-28, 30); void on CHAIN_UNKNOWN (INV-18);
schema data loss; secret exposure.

**Important**: probability crossing without provenance (INV-21, 33–35);
held-token quote into posterior (INV-36); strategy_key drift (INV-04, 22);
DB-before-JSON inversion (INV-17); evidence cohort scope gap;
ci_lower vs hardcoded threshold; missing relationship test for changed
Tier 0/1 invariant; manifest points at wrong test/owner/source.

**Nit**: style / naming. Suppress when Critical/Important exist.

## Report format

Header: `Reviewed: <objects>; Coverage: full|partial; Findings: N C, N I, N N`.
Per finding: contract above. Mark **Uncertain** if unresolvable from diff.

Tier scope: `.github/instructions/tier-scope.instructions.md`.
Invariants: `architecture/invariants.yaml`.
</content>
