# Zeus Events

The `src/events` package owns the R1 EDLI redemption proof kernel. Event facts
and candidate binding here are pure source objects; this package must not own
live side effects.

Rules:
- Do not import or call venue adapters from this package.
- Do not treat public market data as fill truth.
- Keep event timestamps conceptually separate: `observed_at`, `available_at`,
  and `received_at`.
- Use deterministic canonical JSON for payload hashes and idempotency keys.
- Do not call scheduler, `run_cycle`, executor, websocket, or submit paths from
  R1 proof-kernel code.
