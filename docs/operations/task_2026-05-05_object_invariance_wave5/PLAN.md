# Plan: Object-Meaning Invariance Wave 5 Settlement Authority Cutover

Status: planning-lock evidence only
Created: 2026-05-05

## Goal

Repair the scoped settlement source/result to position settlement boundary so
Zeus cannot treat a venue/economic settlement outcome, a quarantined settlement
source row, a legacy decision-log settlement record, or an ungated replay
settlement row as the same authority class.

## Scope

Allowed repair surface for this wave:

- `src/main.py`
- `src/execution/harvester.py`
- `src/execution/harvester_pnl_resolver.py`
- `src/engine/lifecycle_events.py`
- `src/engine/monitor_refresh.py`
- `src/riskguard/riskguard.py`
- `src/state/db.py`
- `src/state/decision_chain.py`
- `src/state/portfolio.py`
- `src/state/strategy_tracker.py`
- `src/engine/replay.py`
- `src/contracts/world_view/settlements.py`
- `src/calibration/drift_detector.py`
- `scripts/etl_forecast_skill_from_forecasts.py`
- `scripts/etl_historical_forecasts.py`
- focused relationship/static tests proving the boundary
- admitted operator script readers found by the downstream contamination sweep,
  routed separately through `add or change script`, limited to VERIFIED/high
  settlement filters and no script execution

Out of scope:

- production DB mutation, migrations, backfills, rebuilds, harvest runs, or
  redemption execution
- changes to `src/contracts/settlement_semantics.py`
- live lock changes or venue/account mutations
- rewriting legacy settlement rows as corrected truth

## Invariants

1. Position settlement may proceed only from a VERIFIED settlement-truth row on
   the corrected harvester split path.
2. Legacy integrated harvester behavior must fail closed when settlement truth
   is QUARANTINED/UNVERIFIED instead of settling positions or writing learning
   facts from venue-only labels.
3. Canonical SETTLED events must carry settlement-truth authority/provenance so
   downstream risk, learning, reports, portfolio projections, and replay can distinguish verified
   source truth from legacy/diagnostic/economic-only rows.
4. Replay/report reads of `settlements` must filter to VERIFIED authority and
   the explicit temperature metric before using settlement values as outcome
   truth.
5. VERIFIED source truth is not sufficient by itself; position settlement must
   preserve high/low temperature-metric identity before mutating lifecycle,
   P&L, redemption, or canonical SETTLED state.
6. Degraded or metric-unready settlement rows may be counted as degraded
   availability evidence, but must not become realized-exit, risk P&L,
   strategy attribution, learning summary, or ETL training authority.
7. Calibration drift/retrain evidence must consume the same VERIFIED
   settlement authority and explicit temperature-metric identity as live
   settlement readers; calibration_pairs alone are not enough settlement truth.
8. Any unresolved topology mismatch found while routing this wave is evidence
   for topology maintenance, not an excuse to bypass live-money guardrails.

## Verification Plan

- Topology planning-lock with this file as `--plan-evidence`.
- Semantic boot for `settlement_semantics`.
- Unit/relationship tests for harvester split, legacy fallback removal, SETTLED
  event authority payload, authoritative settlement row normalization, and
  replay VERIFIED-only reads.
- Relationship/static tests for high/low settlement-to-position identity,
  metric-ready gating in risk/reporting/strategy projections, and VERIFIED-only
  ETL promotion joins.
- Drift-detector relationship tests proving UNVERIFIED or wrong-metric
  settlements cannot become refit evidence.
- Static sweep for corrected-path bypasses through legacy harvester fallback and
  unfiltered settlement reads.
- Critic pass before advancing to another wave.
