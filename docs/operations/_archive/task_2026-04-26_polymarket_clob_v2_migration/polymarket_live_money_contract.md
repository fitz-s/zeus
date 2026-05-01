# Polymarket Live-Money Contract

Created: 2026-04-27
Last reused/audited: 2026-04-27
Authority basis: R3 Z0 (`docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z0.yaml`) adapted to the registered CLOB V2 migration packet because `docs/architecture/` is not an active docs subroot; R3 `INVARIANTS_LEDGER.md`; corrected `v2_system_impact_report.md`.
Receipt-bound source: this file

This packet-local contract lists the Polymarket live-money invariants Zeus must uphold before any CLOB V2 live-money cutover. It is operational evidence and phase guidance, not a new architecture authority plane.

## Invariants

- V2 SDK (`py-clob-client-v2`) is the only live placement path after cutover.
- Heartbeat is mandatory for GTC/GTD live resting orders; FOK/FAK may run without resting-heartbeat supervision only when the adapter and CutoverGuard explicitly allow it.
- pUSD is BUY collateral; CTF outcome tokens are SELL inventory; never substitute pUSD for outcome-token inventory on normal exit sells.
- No live placement may proceed when `CutoverGuard.current_state()` is not `LIVE_ENABLED`.
- ExecutableMarketSnapshot freshness gates every command that can produce a venue side effect.
- `MATCHED` is not `CONFIRMED`; trade-fact finality must preserve MATCHED/MINED/CONFIRMED distinctions and calibration consumes CONFIRMED only.
- Provenance is preserved at the `VenueSubmissionEnvelope` contract layer, not by pinning to a specific SDK call shape.
- Every cancel has a typed outcome: `CANCELED`, `CANCEL_FAILED`, or `CANCEL_UNKNOWN`.

## Non-goals

- This file does not choose the CLOB V2 cutover date.
- This file does not classify USDC.e ↔ pUSD FX/PnL treatment; Q-FX-1 owns that operator decision.
- This file does not claim Zeus has alpha dominance; F/A/G phases must prove benchmark, allocator, and live-readiness gates.
