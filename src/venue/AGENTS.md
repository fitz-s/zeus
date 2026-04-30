# src/venue AGENTS — Zone K2 (Venue Boundary)

Module book: `docs/reference/modules/venue.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Venue code is the only place Zeus may adapt to Polymarket SDK/API volatility.
It must preserve the money-path ordering: executor intent → venue command
journal → `VenueSubmissionEnvelope` provenance → SDK/API side effect.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `polymarket_v2_adapter.py` | Polymarket CLOB V2 adapter, shared adapter protocol, and SDK boundary | CRITICAL — live-money external side effects |
| `__init__.py` | Package marker | LOW |

## Domain rules

- Adapter code may import `py_clob_client_v2`; other live source modules must
  not.
- Fake/live adapter parity fakes must implement `PolymarketV2AdapterProtocol`;
  fake behavior belongs in test-only fakes, not production fake/live split paths.
- Pin provenance at `VenueSubmissionEnvelope`, not a specific SDK call shape.
- Preserve command-journal pre-side-effect discipline. Do not insert or mutate
  `venue_commands` directly from this package.
- Q1-zeus-egress remains operator-gated; preflight must fail closed when
  daemon-machine evidence is absent.
- Do not implement pUSD collateral accounting, heartbeat policy, or cutover-wipe
  reconciliation here; Z4, Z3, and M5 own those surfaces.

## Common mistakes

- Treating a green SDK mock as proof of production V2 readiness.
- Calling SDK `post_order` without a complete provenance envelope.
- Smuggling schema changes into the adapter to make tests pass.
