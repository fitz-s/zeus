# Real Live Hotfix: Allocator and Reconcile Blockers

Status: active
Opened: 2026-06-06

## Goal

Move the current EDLI live runtime from valid shadow receipts to real-live-ready execution without relaxing probability, cost, q_lcb, certificate, or submit gates.

## Current Evidence

- `ws_gap_guard` is currently allowed in `state/status_summary.json`; the earlier `user_ws_status=OK` failures were real but not the current main block.
- `risk_allocator_global` is currently `allocator_not_configured` in derived execution capability.
- `state/risk_state.db` is GREEN, so the allocator block is wiring/configuration, not a risk-math red state.
- `exchange_reconcile_findings` has 15 unresolved `position_drift/ws_gap` rows. With the default `reconcile_finding_limit=0`, a correctly configured allocator must kill-switch on these until they are resolved by canonical truth.
- The substrate warm fix increased live orderbook prefetch from 50 to 500 rows per cycle; coverage is improved but still sometimes budget-truncated.

## Slices

1. Allocator wiring
   - Configure the global allocator in EDLI `live_no_submit` cycles as a read-only/no-submit governance surface.
   - Preserve fail-closed behavior: if bankroll or portfolio truth is unavailable, the allocator remains unconfigured and submit stays blocked.
   - Do not treat this as real-submit authorization.

2. Reconcile truth repair
   - Resolve historical wallet-position drift only through canonical position, settlement-command, or redeem-intent truth.
   - Do not manually mark findings resolved without a matching truth surface.
   - If closed wallet holdings require redeem commands, route them through `enqueue_redeem_command()` only.

3. Runtime verification
   - Restart only after a tested code change.
   - Verify current status, receipts, rejection reasons, candidate flow, and live logs.
   - Real live is eligible only after allocator is configured and reconcile findings no longer force a kill-switch.
