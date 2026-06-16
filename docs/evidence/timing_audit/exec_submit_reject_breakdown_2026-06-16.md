# Exec submit-reject reason breakdown — GROUNDED (2026-06-16)

```
# Created: 2026-06-16
# Authority basis: read-only query of LIVE zeus-world.db edli_live_order_events (operator-authorized),
#   resolving the critical unknown flagged in exec_price_freshness_rootcause_2026-06-16.md.
```

## Funnel (Jun 1–16, live)
`VenueSubmitAttempted=235 → Acked=46 → Rejected=168` (~71% reject). Rejects fire PRE-VENUE
(`raw_response_hash=None`): they are Zeus-side gate rejections, not Polymarket matching-engine rejects.

## SubmitRejected reason_code breakdown (n=168, from payload_json `$.reason_code`)

| reason (prefix) | n | class |
|---|---|---|
| `FinalExecutionIntent event_id does not match executable snapshot` | 42 | **CONSISTENCY/ID BUG** |
| `FinalExecutionIntent BUY notional is below v[min]` | 39 | correct refuse (too small) |
| `FinalExecutionIntent tick_size does not match executable snapshot` | 28 | **CONSISTENCY (snapshot re-elected?)** |
| `risk_allocator_pre_submit_blocked: unknown_side_effect_threshold` | 10 | risk gate |
| `FinalExecutionIntent expected_fill_price_before_fee does not match … sweep` | 9 | **CONSISTENCY (price recompute vs snapshot)** |
| `FinalExecutionIntent executable depth validation failed: DEPTH_INSUFFICIENT` | 8 | correct refuse (liquidity) |
| `FinalExecutionIntent decision_source_context failed integrity` | 8 | **TIMING-PROVENANCE GAP** |
| `invalid_submit_amount_precision` | 5 | format |
| `post_only_passive_limit …` | 5 | order-type |
| `recaptured executable snapshot changed final` | 4 | freshness (correct refuse on recapture) |
| `pre_submit_collateral_refresh_failed: database` | 3 | infra |
| `venue_rejected_invalid_amount_400` | 2 | venue-side format |
| `pre_submit_collateral_reservation_failed: collateral_snapshot_stale` | 2 | freshness (collateral) |
| `executable_snapshot_gate …` | 2 | freshness gate |
| `SlippageBps.direction='adverse'` | 1 | price |

## Interpretation — overturns BOTH freshness hypotheses
The exec-price-freshness tracer hypothesized either (H1) selection-stage starvation or (H2) correct
price-safety refusal. Re-probed reality refutes both as dominant:
- **True freshness/stale rejects ≈ 8 (~5%)** (recapture-changed 4 + collateral-stale 2 + snapshot-gate 2).
  Freshness is NOT the live-fill bottleneck. The 600s selection-window widening already mitigated C5.
- **Dominant blocker ≈ 79 (~47%) = pre-venue intent↔snapshot CONSISTENCY** (event_id 42 + tick_size 28 +
  expected_fill_price 9). The built `FinalExecutionIntent` does not match the executable snapshot it is
  validated against at the pre-venue gate.
- **Correct refuses ≈ 47 (~28%)** (notional-below-min 39 + depth-insufficient 8) — working as intended;
  these markets were genuinely un-tradeable (size too small / no liquidity).

## The smoking gun (event_id, 42 = largest single reason)
Full string: `intent='edli_evt_03f2cd42…' snapshot='highest-temperature-in-warsaw-on-june-7-2026'`.
The gate compares the intent's **edli event-hash id** to the snapshot's **market slug** — two different
identifier namespaces that can never be equal. Either the comparison is wrong, or one side is populated
from the wrong field. This alone accounts for the single largest slice of no-trades.

## Timing-changeset-adjacent slice (decision_source_context, 8)
`missing_model_family, missing_forecast_issue_time, missing_forecast_valid_time, missing_forecast_fetch_time,
missing_forecast_available_at, missing_raw_payload_hash, missing_degradation_level, missing_forecast_source_role,
missing_authority_tier, missing_decision_time, missing_decision_time_status`. The intent's
decision_source_context is built without these provenance fields — incl. `forecast_available_at` and
`decision_time`, squarely the timing domain. Small (8) but must be checked for interaction with the C1 work.

## Next probe (read-only, then fix)
Root-cause the consistency gate: WHERE `FinalExecutionIntent.event_id` is set vs where the executable
snapshot's identity (market slug) is set, and the equality check between them; and whether the executable
snapshot is recaptured/re-elected between intent construction and pre-venue validation (which would explain
the tick_size + expected_fill_price mismatches landing together). Surfaces: src/execution/executor.py,
src/contracts/execution_intent.py, src/contracts/executable_market_snapshot.py, the EXECUTOR_PRE_VENUE_REJECTED gate.
```

---

## RECENCY CORRECTION (same-day, after the aggregate above)

The Jun 1–16 AGGREGATE is dominated by HISTORICAL pre-fix events. A reason×recency query
(MIN/MAX occurred_at, last-3-day count) shows the consistency cluster is ALL fixed:

| reason | total | first→last day | last 3d |
|---|---|---|---|
| event_id mismatch | 42 | 06-06 → **06-12** | 0 (06-12 MAKER fix worked) |
| tick_size mismatch | 28 | **06-01 only** | 0 (BUG #92 fix worked) |
| expected_fill_price | 9 | **06-01 only** | 0 |
| decision_source_context | 8 | 06-01 → 06-06 | 0 |
| executable depth | 8 | 06-01 → 06-07 | 0 |
| BUY notional below min | 39 | 06-07 → 06-12 | 0 |
| **risk_allocator: unknown_side_effect_threshold** | 10 | **06-15 → 06-16** | **10** |
| pre_submit_collateral_refresh_failed (DB) | 3 | 06-10 → 06-16 | 1 |

**The ONLY current (last-3-day) live-fill blocker is `risk_allocator_pre_submit_blocked:
unknown_side_effect_threshold`.** All consistency/freshness/timing rejects are historical and
already fixed. My earlier "intent↔snapshot consistency is the dominant blocker" framing was true
of the aggregate but FALSE of the current state — recency probe corrected it.

### The current blocker, characterized
- `governor.py:242` trips the kill-switch when `unknown_side_effect_count > unknown_side_effect_limit`,
  and `unknown_side_effect_limit = 0` (`governor.py:51`). So ONE unresolved unknown submit halts ALL
  new submits (and forces reduce-only, `governor.py:230`).
- The unknowns DO reconcile: every recent `SubmitUnknown` aggregate reaches `…SubmitUnknown,
  CapTransitioned, Reconciled`. The block is temporary and self-clearing — an HONEST exposure gate
  (you don't know your true position with an unresolved order), not an artificial throttle. Per the
  no-caps law it should STAY.
- Recent funnel (06-14→16): 26 VenueSubmitAttempted → 10 Acked (38%), 5 SubmitUnknown→reconciled
  (~19%), 10 blocked-by-the-gate. Each unknown bounces ~2 following attempts.

### Real lever for more fills (NOT freshness, NOT consistency)
Reduce the **SubmitUnknown rate** (~19% of submits): why does Zeus fail to determine a submit's
outcome (ack/reject/fill)? Likely venue-ACK / response-timeout / WS-result-handling — the
execution/venue layer (src/execution/executor.py venue ack path, src/ingest/polymarket_user_channel.py).
This is the genuine current "no-trade = OUR defect", distinct from the timing changeset.
