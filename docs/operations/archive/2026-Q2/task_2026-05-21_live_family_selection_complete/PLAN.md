# Live Family Selection Complete Repair Plan

Created: 2026-05-21

## Scope

Complete the remaining live-family economic-object repair after PR #246.

This PR is stacked on PR #246 and covers:

- first-class `WeatherFamilyDecision` / `ExclusiveOutcomePortfolio` data object for single-leg Stage-B family intent;
- family selection score based on expected net profit / utility proxy, not post-Kelly `size_usd`;
- passive-maker execution context that requires explicit fill probability for live passive entry sizing / authorization;
- canonical `NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP` persistence for family-dropped decisions, with legacy CHECK fallback to `UNCATEGORIZED` detail rather than losing the row;
- family exposure read model that can include trade DB command/order/trade fact evidence, not only portfolio positions;
- live runtime wiring that prefers trade DB family exposure authority when available and keeps portfolio projection as fallback.

## Non-Scope

- No live venue submit, cancel, or chain mutation.
- No operator execution of DB migration scripts in this PR.
- No multi-leg payoff-vector Kelly optimizer beyond the single-leg Stage-B intent object; this PR makes the live scalar path consume one explicit family action and records enough structure for the later vector optimizer.

## Verification

- Relationship tests for pre-Kelly family selection, dedup persistence, trade DB exposure authority, passive-maker fill-probability guard, and no `size_usd` survivor objective.
- Focused runtime repricing tests.
- `py_compile` for modified Python modules.
- `git diff --check`.

