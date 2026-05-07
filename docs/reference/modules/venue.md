# Venue Module Authority Book

**Recommended repo path:** `docs/reference/modules/venue.md`
**Current code path:** `src/venue`
**Authority status:** Dense module reference for Polymarket venue adapters. It
explains the adapter boundary; executable source, tests, machine manifests, and
active R3 phase cards outrank it.

## 1. Module purpose

Own the external venue adapter boundary so SDK/API volatility cannot leak into
executor, state, signal, or strategy code. The module turns typed Zeus intent
and market snapshot context into `VenueSubmissionEnvelope` provenance before
any Polymarket side effect.

## 2. What this module is not

- Not canonical state and not a schema owner.
- Not a place to choose live cutover timing.
- Not collateral accounting, heartbeat policy, or exchange reconciliation.

## 3. Domain model

- `PolymarketV2Adapter`: one adapter seam for V2 placement/cancel/query.
- `PolymarketV2AdapterProtocol`: the shared live/fake contract that T1 fake
  venues implement for parity tests without credentials, network I/O, or live
  side effects.
- `VenueSubmissionEnvelope`: immutable submission provenance contract.
- `src.data.polymarket_client.PolymarketClient`: legacy import/call shim that
  delegates live placement/cancel/order queries into the adapter while
  preserving existing executor monkeypatch seams.
- Operator-gated preflight: Q1-zeus-egress evidence controls whether V2 host
  preflight can pass operationally.

## 4. Runtime role

Execution code calls the adapter only after the venue command journal is
persisted. The adapter creates a provenance envelope, signs/posts through the
SDK, and returns typed submit/cancel/query results without mutating canonical
DB tables directly.

## 5. Authority role

The venue adapter is a live-money boundary, but it does not define settlement,
lifecycle, or risk law. It implements R3 Z2 and inherits INV-24, INV-25,
INV-28, INV-30, NC-NEW-G, INV-NEW-B, and T1's INV-NEW-M fake/live adapter parity
contract.

## 6. Public interfaces

- `src/venue/polymarket_v2_adapter.py::PolymarketV2Adapter`
- `src/venue/polymarket_v2_adapter.py::PolymarketV2AdapterProtocol`
- `src/contracts/venue_submission_envelope.py::VenueSubmissionEnvelope`

## 7. Invariants

- Direct `py_clob_client_v2` imports are confined to `src/venue/`.
- Provenance is pinned at the envelope contract, not at one-step or two-step SDK
  method shape.
- Preflight fails closed when operator egress evidence is absent.
- Submit returns typed rejection / missing-order-id results; it must not turn a
  malformed response into a successful ACK.
- Compatibility-submit failures before an SDK order-post side effect
  (Q1/preflight, SDK client construction, market snapshot fetch, local signing)
  return typed rejection. Exceptions during/after a possible venue post remain
  unknown-side-effect inputs for executor recovery rather than safe replay.
- V2 cancel and redeem capabilities are surfaced conservatively: unsupported or
  unverified SDK methods return typed failures instead of falling back to V1.
- Fake-venue safety tests use `tests/fakes/polymarket_v2.py` to implement the
  same protocol and compare envelope / event schemas against a mock live
  adapter. This does not authorize live submit/cancel/redeem or cutover.

## 8. Negative constraints

- Do not write or alter `venue_commands` from this module.
- Do not bypass CutoverGuard or command-journal pre-side-effect ordering.
- Do not store raw secrets or signed payload bytes outside the envelope contract
  and command-journal payloads planned by later phases.

## 9. Verification commands

```bash
pytest -q tests/test_v2_adapter.py
pytest -q tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py
pytest -q tests/test_unknown_side_effect.py
pytest -q tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py
pytest -q tests/test_neg_risk_passthrough.py
```
