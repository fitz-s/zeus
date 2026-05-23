# Live Reduce-Only Reconcile Loop Plan

Created: 2026-05-18
Last reused/audited: 2026-05-18
Authority basis: AGENTS.md money path and Chain > Chronicler > Portfolio law; live evidence after PR #174 (`07fc6191f2`).

## Objective

Remove the live `risk_allocator_global=reconcile_finding_threshold` blocker only by reconciling canonical truth, not by clearing gates manually.

Current live state after PR #174 deploy:

- Loaded code is current: `commit=expected=07fc6191f2`, `dirty=False`.
- M3 WS is repaired: `subscription_state=SUBSCRIBED`, `m5_reconcile_required=False`.
- Entry remains blocked by `risk_allocator_global` because `exchange_reconcile_findings` has 5 unresolved `position_drift` rows.
- Manual entries pause and Karachi Q-FX are independent operator gates.

## Evidence

Latest `collateral_ledger_snapshots` are `CHAIN` authority and show only the Karachi token balance; none of the 5 unresolved finding tokens are present.

`chain_reconciliation` repeatedly logs `AGGREGATE_PHANTOM ... voided by chain aggregate reconciliation`, but at least one canonical row (`bf0a16f5-f95`) remains `position_current.phase='active'`, `chain_state='synced'`. That indicates the runtime portfolio was voided while canonical DB truth was not updated.

The 5 findings split into:

- `104906...` / Manila `bf0a16f5-f95`: active local row, chain balance 0, no exit path. Candidate canonical phantom-void persistence bug.
- `113959...` / London: one economically closed row plus one pending-exit duplicate; chain balance 0; local confirmed journal still nets +6. Candidate duplicate/lot resolution bug.
- `103133...`, `266212...`, `756036...`: pending_exit + `exit_pending_missing` + repeated `BACKOFF_EXHAUSTED`; chain balance 0. Candidate exit-missing terminalization policy gap.

## Structural Decisions

1. Canonical phantom persistence:
   When chain reconciliation voids a runtime portfolio position because chain authority proves no backing token, the same transition must be durably reflected in canonical `position_current` and `position_events`. Runtime-only voids recreate the same ghost next cycle.

2. Pending-exit chain-missing terminalization:
   `backoff_exhausted` means hold to settlement for non-executable exits when tokens still exist or when settlement is the remaining economic path. It should not indefinitely preserve a local position when fresh chain authority says the wallet has zero token balance and the exit reason is `EXIT_CHAIN_MISSING`.

3. Finding resolution follows truth mutation:
   `exchange_reconcile_findings` should resolve only after the canonical row is terminalized or after fresh venue/chain truth proves the journal matches. Do not resolve findings as an independent manual cleanup.

## Implementation Slices

Slice A: canonical phantom void persistence.

- Add a relationship test for aggregate phantom: `reconcile(..., conn=...)` with chain balance 0 must update `position_current.phase='voided'` and append an `ADMIN_VOIDED` event for the affected position.
- Implement by syncing the `void_position(...)` result through the existing canonical lifecycle sync helper.
- Verify existing pending-exit deferral tests still pass.

Slice B: pending-exit chain-missing terminalization.

- Add a relationship test for `phase='pending_exit'`, `chain_state='exit_pending_missing'`, `exit_state='backoff_exhausted'`, fresh chain absence: canonical row must move to terminal review/void state, not remain an open risk finding forever.
- Do not broaden canonical admin-close persistence for recoverable `exit_intent` or `retry_pending` paths; the new durable terminalization target is `backoff_exhausted` plus chain-known absence.
- Implementation target is likely `exit_lifecycle.handle_exit_pending_missing` or a chain-reconciliation branch that only fires after exit lifecycle has exhausted recovery.

Slice C: reconcile finding closure.

- Add a focused `exchange_reconcile` test proving findings are resolved only after canonical terminalization or current truth match.
- Do not hand-edit `exchange_reconcile_findings` in production; run the existing refresh/sweep path after code deployment.

## Verification

Focused local gates:

- `python3 -m pytest -q tests/test_live_safety_invariants.py::<new/changed chain reconciliation tests>`
- `python3 -m pytest -q tests/test_allocate_chain_truth.py`
- `python3 -m pytest -q tests/test_exchange_reconcile.py::<finding resolution tests>`
- `python3 -m py_compile src/state/chain_reconciliation.py src/execution/exit_lifecycle.py src/execution/exchange_reconcile.py`
- Topology planning-lock and map-maintenance for the final changed files.

Live acceptance after merge/restart:

- `live_health_probe` remains current/clean and WS stays `SUBSCRIBED`.
- `exchange_reconcile_findings` unresolved count drops for terminalized ghosts.
- `risk_allocator_global` no longer reports `reconcile_finding_threshold`.
- If manual entries pause remains, entries stay blocked for `manual_command`; do not remove it without operator authorization.
- Karachi redeem remains blocked until operator sets `ZEUS_PUSD_FX_CLASSIFIED` to one of `TRADING_PNL_INFLOW`, `FX_LINE_ITEM`, `CARRY_COST`.

## Non-Goals

- Do not clear `control_overrides` manually.
- Do not set Q-FX classification in code.
- Do not enable autonomous redeem; current production path broadcasts when enabled and has no sign-only dry-run gate.
- Do not resolve findings by direct SQL unless an existing recovery function has first made canonical truth consistent.
