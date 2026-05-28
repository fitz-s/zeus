# PR332 Trigger Repair Critic Review

Date saved: 2026-05-24T20:10:00Z
Reviewed head: `385d5c4af940ae82b52bd39197df13d463242bfb`
Reviewer: Codex subagent `Darwin`, non-Claude, read-only
Verdict: NO-GO for reviewed scope due P1; no P0 live-submit/venue side-effect path found.

## Findings

### P1 Canonical probability/FDR authority bypass

The critic found that `src/engine/event_reactor_adapter.py` rebuilt `q_live`
from raw ensemble bin-hit frequencies and generated FDR p-values with a normal
approximation. That bypassed Zeus strategy law:

- calibrated probability path is `P_raw -> P_cal -> market fusion -> posterior`
- FDR p-values must come from bootstrap evidence, never approximation formulas

Required repair:

- hydrate posterior/CI/p-value proof from existing Zeus authority facts
- or fail closed instead of declaring FDR/Kelly pass

### P2 Reactor rejection stage misclassification

The critic found that no-submit receipts with `submitted=False` were rejected as
`EXECUTOR_EXPRESSIBILITY` before `_receipt_money_path_blocker()` could classify
`FDR_REJECTED`, `KELLY_REJECTED`, or `TRADE_SCORE_NON_POSITIVE`.

Required repair:

- split event-bound receipt identity validation from proof acceptance
- route no-submit proof-stage blockers to `FDR`, `KELLY`, `TRADE_SCORE`, etc.

## Repair Applied After Review

- `src/engine/event_reactor_adapter.py` now reads canonical `p_posterior` from
  `probability_trace_fact` and canonical `p_value` / `ci_lower` /
  `passed_prefilter` from `selection_hypothesis_fact`, optionally joined to
  `selection_family_fact.decision_snapshot_id`.
- Approximation-based p-values and ensemble-hit-rate posterior generation were
  removed from the no-submit authority path.
- Missing canonical probability/FDR facts now fail closed with
  `LIVE_INFERENCE_INPUTS_MISSING`.
- `src/events/reactor.py::_receipt_matches_event()` no longer treats
  `submitted=False` as an identity mismatch. Non-submitted but event-bound
  receipts now flow through `_receipt_money_path_blocker()`.
- Added regression tests for `FDR_REJECTED` and `KELLY_REJECTED` no-submit
  stage classification.

## Verification

- `python -m py_compile src/engine/event_reactor_adapter.py src/events/reactor.py tests/engine/test_event_reactor_no_bypass.py tests/events/test_redemption_reactor_no_submit.py`
- `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py tests/events/test_redemption_reactor_no_submit.py --maxfail=5` -> 19 passed
- `python -m pytest -q tests/engine/test_event_reactor_no_bypass.py tests/events/test_redemption_reactor_no_submit.py tests/events/test_redemption_fdr_kelly_risk_adapters.py tests/events/test_forecast_snapshot_ready.py tests/events/test_day0_extreme_updated_trigger.py tests/events/test_market_channel_ingestor.py --maxfail=3` -> 68 passed
- `python -m pytest -q tests/state/test_edli_table_ownership.py tests/events tests/strategy/live_inference tests/engine/test_event_reactor_no_bypass.py tests/analysis/test_event_opportunity_report.py --maxfail=8` -> 183 passed
- `python -m pytest -q tests/money_path --maxfail=5` -> 15 passed
- `python scripts/check_schema_version.py && python scripts/check_table_registry_coherence.py && python scripts/ci/assert_test_quality.py` -> PASS
