# PR332 Real Trigger Hydration Review

Date saved: 2026-05-24T19:47:17Z
Reviewed head reported by reviewer: `a2a03a82390ea2c5a0ed4aec26ffba08d932e02c`
Current branch when saved: `codex/edli-v1-no-submit-complete`
Verdict from reviewer: NO-GO / do not merge / do not daemon reboot

## Executive Summary

The reviewer verified that PR332 removed the dangerous broad `run_cycle()`
wrapper, restored full market discovery, restored executable snapshot
fresh-at-submit recapture, disabled the Day0 observability catch-up scanner by
default, and fixed durable live-cap consumption for no-submit proof receipts.

The remaining NO-GO finding is that PR332 still does not generate the full
event-bound decision proof from repo authorities. Instead, the no-submit adapter
requires the event payload to carry proof inputs such as `q_posterior`,
`q_5pct`, `p_fill_lcb`, `c_95pct`, penalties, bankroll, Kelly multiplier, and
`fdr_hypotheses`. Real `ForecastSnapshotReadyPayload` and
`Day0ExtremeUpdatedPayload` do not contain those fields, so real trigger events
would likely reject as `TRADE_SCORE_INPUTS_MISSING`,
`FDR_FULL_FAMILY_PROOF_MISSING`, or `KELLY_PROOF_MISSING` rather than generate a
typed event-bound no-submit final-intent receipt.

## P0 Blockers

### P0-1 Real Forecast/Day0 trigger events cannot produce no-submit proof

Current no-submit adapter reads proof inputs from event payload:

```text
_robust_trade_score_from_payload(payload)
_fdr_hypothesis_p_values(payload, family)
_required_float(payload, "q_posterior")
_required_float(payload, "bankroll_usd")
_required_float(payload, "kelly_multiplier")
```

Real forecast and Day0 triggers emit event facts, not decision proof fields.
The adapter must hydrate/generate proof inputs from repo authorities instead of
requiring the immutable event to carry them.

Required fix:

```text
event
  -> bind market family
  -> load p_cal / p_live from executable forecast reader + LiveBinInferenceEngine
  -> apply Day0 absorbing boundary if Day0 event
  -> bind executable snapshot for each candidate
  -> compute native executable cost from snapshot/depth
  -> compute RobustTradeScore
  -> construct full sibling hypotheses with p-values
  -> run FDR
  -> run Kelly with typed ExecutionPrice and runtime bankroll/settings
  -> run RiskGuard
  -> build EventBoundFinalIntentReceipt(NO_SUBMIT)
```

### P0-2 Runtime no-submit adapter does not use LiveBinInferenceEngine or native executable-cost kernel

The live inference and executable-cost helpers exist, but the runtime adapter
does not call them. It reads `q_posterior` from payload and uses a snapshot top
ask directly.

Required tests:

```text
test_forecast_trigger_event_without_q_fields_still_builds_no_submit_receipt
test_day0_live_authority_event_without_trade_score_fields_still_builds_no_submit_receipt
test_adapter_calls_live_inference_engine
test_adapter_calls_native_executable_cost_kernel
```

### P0-3 Candidate family construction uses event payload bin for every sibling

The review found the adapter could build every sibling candidate with the same
payload/default bin rather than `market_events_v2.range_label/range_low/range_high`.
The selected row and selected candidate must match by condition/token.

Required tests:

```text
test_family_candidates_use_market_events_range_bounds_not_event_payload_default
test_selected_snapshot_row_not_first_still_binds_to_matching_candidate
test_missing_bin_topology_blocks_no_submit_receipt
```

### P0-4 FDR p-values are verified from payload, not generated from repo authority

The adapter now uses canonical FDR helpers, but test fixtures inject
`fdr_hypotheses` into event payload. Real trigger events do not carry those
hypotheses. The adapter must generate or hydrate sibling p-values from the
event-bound candidate family / existing FDR analysis path.

### P0-5 Kelly proof uses payload bankroll and multiplier

Bankroll and Kelly multiplier are runtime/account/config authorities, not event
facts. The adapter must not read `bankroll_usd` or `kelly_multiplier` from
trigger event payload.

Required tests:

```text
test_event_payload_bankroll_is_ignored
test_kelly_uses_runtime_bankroll_authority
test_kelly_uses_settings_multiplier
```

## P1 Blockers

```text
P1-1 Full pytest sweep skipped.
P1-2 Market-channel online service still needs live smoke before daemon reboot.
P1-3 EventWriter is still a synchronous facade, not a process-wide queue.
```

## Explicit Non-Blocker Fixes Already Verified By Reviewer

```text
- run_cycle wrapper removed from EDLI main path.
- full tag+slug market discovery restored.
- fresh-at-submit executable snapshot recapture restored.
- Day0 observability catch-up scanner default-disabled.
- no-submit does not reserve durable live cap.
```

## Current Implementation Direction

Do not downgrade the PR to scaffold. Implement adapter-side hydration and proof
generation for no-submit receipts while keeping real order submit disabled.
