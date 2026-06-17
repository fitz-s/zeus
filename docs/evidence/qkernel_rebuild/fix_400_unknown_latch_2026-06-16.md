# Fix: venue 400 mis-classified as unknown-side-effect → 8h global submit latch

- Created: 2026-06-16
- Last reused or audited: 2026-06-16
- Authority basis: live observation (edli_live_order_events + venue_commands), governor.py
  kill-switch semantics, executor deterministic-rejection classifier. RULE 1: the zero-fill
  state is OUR defect, root-caused to a misclassification — not absent alpha.

## Observed root cause (by watching the live system run, not theory)
Timeline 2026-06-15 (edli_live_order_events):
- 16:28:10 `SubmitUnknown` reason_code=`submit_unknown_side_effect: PolyApiException[status_code=400,
  error_message={'error': 'invalid post-...}]`.
- 17:39 → 00:39 (~8h): EVERY `VenueSubmitAttempted` → `SubmitRejected` reason_code=
  `risk_allocator_pre_submit_blocked: unknown_side_effect_threshold`, venue_order_id=None.
- 00:39 latch cleared (reconciler crawled the unknown command terminal) → one order reached venue.

Mechanism: a Polymarket `status_code=400` is a request-VALIDATION rejection — the venue rejected
the request BEFORE creating any order (`venue_order_created=False`). But only the specific
`invalid_amount` 400 messages were classified deterministic (executor.py `_is_polymarket_invalid_
amount_400_message`); the `'invalid post-...'` 400 fell through to the generic
`SUBMIT_UNKNOWN_SIDE_EFFECT` command_state. The governor counts venue_commands in
`_UNRESOLVED_SIDE_EFFECT_STATES` (governor.py:583-590); `unknown_side_effect_limit=0` → ONE such
row latches `unknown_side_effect_threshold` and blocks ALL submissions until the recovery loop
resolves it (~8h here). One deterministic 400 poisoned the whole submission lane for 8h.

## Fix (correctness, not a gate change)
`src/execution/executor.py`: generalize `_deterministic_submit_rejection_payload` — after the
existing geoblock-403 and invalid_amount-400 specific checks, add `_is_polymarket_deterministic_400`
(any `status_code=400` PolyApiException) → `_generic_400_rejection_payload`
(reason=`venue_rejected_400`, proof_class=`deterministic_venue_400`, venue_order_created=False).
A 400 now routes to command_state=`REJECTED` (line ~2793/3823 deterministic branch), which sits
OUTSIDE `_UNRESOLVED_SIDE_EFFECT_STATES` (command_recovery.py:5461) so the governor count stays 0
and the kill switch never latches. Specific invalid_amount reason_code preserved (checked first,
for its downstream no-verbatim-retry handling).

## Safety (on-chain boundary)
STRICTLY SAFER on-chain: the change only reclassifies a NON-submitting FAILURE (reject vs unknown);
both outcomes place no order. It CANNOT cause an erroneous trade. A 400 = request rejected at
validation = no venue order created (HTTP/API semantics; the existing invalid_amount_400 path
already assumes venue_order_created=False). 400s are non-retryable verbatim — the family re-decides
next cycle on fresh inputs.

## Test
RED-on-revert: a PolyApiException(status_code=400, 'invalid post-only') → deterministic rejection
payload (not None), command-level REJECTED (not SUBMIT_UNKNOWN_SIDE_EFFECT); reverting the new
branch makes it fall through to None → unknown. Plus money-path green.

## Rollback
`git revert` the commit; `launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`. No data
migration; classification-only.
