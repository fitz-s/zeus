# U2 confusion checkpoint — card DDL was narrower than acceptance semantics

Created: 2026-04-27
Phase: U2
Status: resolved by implementation note

## Confusion

The U2 slice card contains SQL sketches, but several acceptance tests require semantics the sketch does not fully encode:

- `venue_trade_facts` sketch says `trade_id TEXT NOT NULL UNIQUE`, while the acceptance text requires observing a trade across states such as `MATCHED → MINED → CONFIRMED` and `MATCHED → FAILED`. A unique-only `trade_id` would collapse the event history.
- `position_lots` sketch omits provenance columns (`source`, `observed_at`, `local_sequence`, `raw_payload_hash`) even though INV-NEW-F says every fact in the five projections carries those fields.
- `position_lots` state grammar omits the acceptance-test rollback target `QUARANTINED`.
- `venue_submission_envelopes` sketch omits some fields present on the frozen `VenueSubmissionEnvelope` contract (`side`, `price`, `size`, `trade_ids`, `transaction_hashes`). Omitting them would prevent DB-only reconstruction of the envelope.

## Resolution

Treat the card SQL as a minimum sketch and the acceptance semantics/frozen contract as the stricter authority for U2 implementation. The resulting schema:

- makes trade history append-only with uniqueness on `(trade_id, local_sequence)` instead of unique-only `trade_id`;
- adds common provenance fields to `position_lots`;
- includes `QUARANTINED` as the explicit failed optimistic-exposure rollback state;
- persists the complete frozen `VenueSubmissionEnvelope` field set needed for reconstruction.

## Future guard

If a later slice wants a single-row current-state projection for trades or lots, add it as a separate projection/fold. Do not weaken the raw append-only facts table to become both event log and current row.
