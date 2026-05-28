# PR332 Post-Repair Critic Review

Source: Codex subagent `019e5b60-f4fa-72d2-a9ae-6571ae659980` on 2026-05-24.
No Claude or external paid critic tool was used.

## Verdict

The saved PR332 P0 repair items were mostly addressed structurally, but the
critic still blocked merge on the original EDLI v1 no-submit requirement.

Remaining P0 from the critic:

```text
No-submit final intent proves FDR/Kelly with stubs, not the required money-path
math.
```

Evidence cited by the critic:

- `src/events/money_path_adapters.py` treated FDR as selected id membership in
  a provided id list.
- `src/events/money_path_adapters.py` treated Kelly as typed price plus
  `size_usd > 0`.
- `src/engine/event_reactor_adapter.py` defaulted missing Kelly sizing to
  `1.0`.
- This did not match repo BH/FDR math in `src/strategy/selection_family.py` or
  typed Kelly sizing in `src/strategy/kelly.py`.

Residual P1/P2 from the critic:

- P1: event-bound receipt still trusts q/c/fill inputs from payload rather than
  deriving every field from canonical forecast/inference and executable
  snapshot surfaces.
- P1: market-channel websocket writes/commits in the long-lived reader loop;
  public market channel is still not fill truth, but writer-thread/backpressure
  concerns are only partially reduced.
- P2: Day0 catch-up scanner still reads trade DB
  `settlement_day_observation_authority`, but it is gated off by default and
  rows default to observability-only.
- P2: market-channel refresh uses full discovery with slug fallback, but still
  performs broad discovery then filters by condition.

Closed checks from the critic:

- EDLI runtime no longer calls `run_cycle` or
  `submit_existing_cycle_for_event`.
- No-submit receipts are derived through `EventBoundFinalIntentReceipt`.
- Market discovery uses `find_weather_markets(include_slug_pattern=True)`.
- Executable snapshot fresh-at-submit recapture is restored.
- No-submit does not reserve live cap.
- Public market-channel events are rejected as direct stale-trade/fill
  authority.

## Repair Applied After This Critic Review

- `evaluate_fdr_full_family()` now calls Zeus canonical
  `apply_familywise_fdr()` over the full event-bound sibling family and requires
  p-values for every hypothesis.
- `evaluate_kelly()` now calls Zeus canonical `kelly_size()` with typed
  fee-deducted `ExecutionPrice`, `p_posterior`, `bankroll_usd`, and
  `kelly_multiplier`.
- `build_event_bound_no_submit_receipt()` no longer defaults missing Kelly size
  to `1.0`; missing FDR/Kelly proof inputs fail closed.
- Added tests proving missing sibling p-values and missing Kelly inputs block
  no-submit proof receipts.

## Focused Re-Review After First Repair

Source: Codex subagent `019e5b6b-0286-7f70-89d3-cd726dc1df42`.

The critic confirmed the literal FDR/Kelly stubs were gone, but found two
remaining P0 defects:

1. Kelly proof could still be fabricated from a missing/invalid executable
   native ask because `_execution_price_from_snapshot()` defaulted to `0.50`.
2. FDR called the canonical function but the adapter supplied only one
   executable snapshot row, so the denominator could collapse to one binary
   market's YES/NO tokens instead of the full city/date/metric sibling family.

## Second Repair Applied

- `_execution_price_from_snapshot()` now requires a real native ask from the
  bound executable snapshot and rejects missing, invalid, or out-of-bounds ask
  values.
- `build_event_bound_no_submit_receipt()` now loads all latest fresh executable
  snapshot rows for the event city/date/metric family through canonical
  `market_events_v2` topology and constructs the `EventBoundDecisionEngine`
  topology from all sibling bins.
- The selected row is chosen from that full family by event
  `condition_id`/`token_id`/`executable_snapshot_id`; if it is missing, the
  proof fails closed.
- Removed the unused single-row `_event_snapshot_binding()` fallback so EDLI
  no-submit cannot silently collapse to a one-row denominator.
- Added tests proving the receipt uses a four-hypothesis family in the fixture
  and rejects missing native ask.
