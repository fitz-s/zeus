# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: AGENTS.md topology planning-lock route for live open-order disappearance; Polymarket CLOB heartbeat documentation; live DB/log evidence 2026-05-17T07:45Z-08:35Z

# Live Order Survival Plan

## Objective

Make live orders survive the full lifecycle boundary:

`SUBMIT_REQUESTED -> venue ACK -> local ownership -> heartbeat lease maintained -> fill or venue-terminal reconciliation -> risk gate released`

This packet exists because topology classified the combined fix as governed:
heartbeat lease ownership, state truth/audit writes, and orphan cleanup touch
different authority surfaces and must be split into relationship-tested slices.

## Current Evidence

1. Venue accepted live GTC orders and then canceled them.
   - `venue_commands` shows ACKED commands later `EXPIRED`.
   - `venue_order_facts` shows `LIVE` followed by `CANCEL_CONFIRMED` with
     `matched_size=0`.
   - Three orders were canceled by WS user-channel at exactly
     `2026-05-17T07:46:52.552Z`; two later orders were point-read as
     `CANCELED` after restart.

2. The heartbeat lease was not continuously protected.
   - Polymarket docs state resting orders are canceled if no valid heartbeat is
     received within 10 seconds, with up to a 5 second buffer.
   - Live logs show the pre-fix APScheduler heartbeat missed the 07:46:46 tick
     and posted the next heartbeat at 07:46:53.808, after the 07:46:52.552
     cancellations.
   - Restart later created a multi-minute heartbeat gap before the new process
     reached scheduler-ready.

3. ACK-to-local ownership is torn across legacy and canonical state.
   - Every successful ACK in the affected burst logged
     `Failed to log trade entry: FOREIGN KEY constraint failed`.
   - `trade_decisions` is empty in `state/zeus_trades.db`.
   - `position_current` does contain the affected order IDs, but
     `cleanup_orphan_open_orders` still uses `trade_decisions` as a recent-order
     guard even though live K1 runtime now relies on `venue_commands` and
     `position_current`.

4. Live is currently blocked by one unresolved unknown side-effect.
   - `venue_commands` has command `c4707abb7e65464c` in `REVIEW_REQUIRED` with
     reason `recovery_no_venue_order_id`.
   - Risk allocator blocks new entries with `unknown_side_effect_threshold`.

## Structural Decisions

### S1. Heartbeat Lease Owner

Heartbeat is a lease for existing venue orders, not a scheduler maintenance job.
It must not share a critical path with market scanning, collateral refresh, M5
reconcile, user-channel bootstrap, wallet checks, or Python cold start.

Implementation direction:
- Keep the in-daemon dedicated loop as a short-term improvement.
- Add a minimal external `zeus-venue-heartbeat` owner as the durable fix.
- The keeper may only post the CLOB heartbeat and write a local status surface.
- The trading daemon must consume keeper health before submitting GTC/GTD
  orders and must not create a competing heartbeat chain once the keeper is
  active.

### S2. Submit Ownership Boundary

A venue ACK must become locally owned by canonical command/position truth before
any later process can classify it as orphan or unknown.

Implementation direction:
- `venue_commands` and `position_current` are the live ownership surfaces.
- Legacy `trade_decisions` must not be required for order ownership or orphan
  protection.
- `log_trade_entry` may remain a legacy replay/audit projection, but it must
  degrade without FK failure when the decision snapshot lives in the K1/V2
  forecast surface rather than legacy `ensemble_snapshots`.

### S3. Review-Required Clearance

`recovery_no_venue_order_id` is not the same as an unknown venue side effect
when durable evidence proves the process never crossed SDK submit or never
received a venue order id. The clearance path needs typed proof, not manual
ad hoc DB edits.

Implementation direction:
- Add a relationship test for `SUBMIT_REQUESTED` without `SUBMIT_ACKED`, without
  venue order id, without order/trade facts.
- Permit clearance only when proof establishes no venue-side order identity and
  no matching venue facts.
- Leave actual live DB mutation behind a backup/dry-run operator step unless the
  code can safely self-clear from DB evidence alone.

## Slice Routes

1. Heartbeat keeper slice:
   - likely files: `src/control/heartbeat_supervisor.py`, `src/main.py`,
     new script under `scripts/` or a narrow entrypoint, LaunchAgent template or
     runbook, heartbeat tests.
   - required proof: unit loop survival, no slow maintenance in critical path,
     local status freshness, restart soak.

2. Submit ownership / legacy projection slice:
   - likely files: `src/state/db.py`, `src/engine/cycle_runtime.py`,
     `tests/test_db.py`, `tests/test_runtime_guards.py`.
   - required proof: V2/K1 decision snapshot does not FK-fail legacy
     `trade_decisions`; orphan cleanup never uses empty legacy
     `trade_decisions` as ownership truth when `venue_commands` or
     `position_current` proves ownership.

3. Review-required clearance slice:
   - likely files: `src/execution/command_recovery.py`,
     `src/state/venue_command_repo.py`, `tests/test_unknown_side_effect.py`,
     `tests/test_venue_command_repo.py`.
   - required proof: current `c4707abb7e65464c` class is either safely cleared
     by typed DB proof or remains fail-closed with explicit operator action.

## End-to-End Verification

Completion is not a smoke test. The final claim requires:

1. Focused unit and relationship tests for each slice.
2. Healthcheck clean except expected branch/PR code-plane state while running
   from the live hotfix branch.
3. Live restart proof that heartbeat status stays fresh during trading-daemon
   restart.
4. Per-order `get_order` proof that at least one resting GTC order remains
   `LIVE` across multiple heartbeat intervals.
5. Command journal proof that new submits cannot remain stuck in
   `SUBMITTING` without ACK/reject/unknown evidence.
6. If orders do not fill, separate pricing/fill analysis against best ask and
   expected edge. Non-fill is not success, but it is a different failure class
   from heartbeat cancellation.

## Non-Goals

- No market orders.
- No broad DB schema rewrite.
- No live DB mutation without backup/dry-run evidence.
- No claiming live stability from process liveness or one order-placement smoke.
