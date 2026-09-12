# Zeus Events

The `src/events` package owns EDLI opportunity events and their processing
state. Event rows are world-DB append-only facts; mutable consumer state belongs
in separate processing tables.

`public_trade_observations.py` owns the separate TRADE-class public market
delivery FIFO and append path. It records delivery identities and local gaps,
never opportunity events, own fills, source completeness or entry authority.

Rules:
- Do not import or call venue adapters from this package.
- Do not treat market-channel data as fill truth.
- Keep event timestamps conceptually separate: `observed_at`, `available_at`,
  and `received_at`.
- Use deterministic canonical JSON for payload hashes and idempotency keys.
- Route execution side effects through existing engine/executor contracts only.
