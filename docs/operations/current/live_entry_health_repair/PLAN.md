# live_entry_health_repair -- Live repair packet

Date: 2026-07-10
Branch: `agent/runtime-throughput-first-principles`
Status: active

## Objective

Restore truthful live entry admission after the global auction reached a real winner. Repair only current, independently proven defects; never weaken live-health, forecast freshness, probability identity, exit lifecycle, or venue gates.

## Current truth

- Live PID 98439 is loaded at `16cec04f6`; entries are not paused.
- Chain-mirror repair succeeded: `6be10bfa-f2f` is canonical `voided/closed_exited`.
- Reactor reached winner claim, then selected winner identity `e664824…`.
- Entry authority currently fails on `entry_q_version`, `forecast_event_bridge`, and `pending_exit_release_loop`.
- A reactor cycle started at 2026-07-10T17:32:26Z and did not return through three following scheduled intervals, which were skipped by `max_instances_reached`.
- No new venue command, submit, ACK, or fill exists.

## Authority and boundaries

- Truth order: Chain/CLOB > canonical DB/events > current health receipt > logs/projections.
- Zone: K0/K2 live state repair plus K1 protective-health diagnosis.
- Invariants: INV-03 append-first authority, INV-06 point-in-time truth, INV-08 single transaction boundary, INV-29 command identity grammar.
- Required reads: root/scoped AGENTS, current architecture/delivery law, execution lifecycle reference, script manifest, `repair_active_entry_q_versions.py`, `src.control.live_health`, and exact live DB/health rows.
- Forbidden: DB backup/copy, venue submit/cancel from a repair, manual SQL, q recomputation from hindsight, health-gate bypass, stale posterior acceptance, lifecycle phase rewrite, schema change.
- Code files are not yet authorized to change. If diagnosis proves a code defect, amend scope and planning evidence before editing.

## Slice A -- Active ENTRY q_version

- Dry-run found exactly one active missing row: Seoul position `656594aa-af2`, command `d671dcc6142a4152`.
- Existing FinalIntentCertificate reconstructs exactly one posterior identity: `5aa5d7d02c36827aa1f6d11fb5da81ad79d02f855f49aab4156e871a3cea5597`.
- Apply may update only that command's empty `venue_commands.q_version` through the registered repair script.
- Acceptance: applied_count=1, subsequent dry-run candidate_count=0, no share/phase/state change, no venue action, no backup.
- Executed evidence: apply reported `active_missing_count=1`, `candidate_count=1`, `applied_count=1`, `blocked=0`, `db_backup_created=false`, `venue_action=false`; the post-apply dry-run reported `active_missing_count=0`, `candidate_count=0`.
- Canonical re-read: command remains `FILLED`, position remains `day0_window/synced` with `shares=chain_shares=18.44`; only its empty `q_version` became the uniquely reconstructed certificate hash. No new `venue_commands` row was created.

## Slice B -- Remaining blockers

1. Determine whether `forecast_event_bridge` is producer lag, wrong identity routing, or health-probe mismatch. No stale posterior may become executable.
2. Determine whether Seoul's `pending_exit_projection_regression` is a real illegal phase regression or a health query that failed to recognize canonical `EXIT_RETRY_RELEASED` evidence.
3. Locate the stuck reactor call from current process/log/DB evidence; distinguish bounded slow solve from deadlock or unbounded queue work.
4. Any repair must be one minimal independently reviewed slice; otherwise remain fail-closed with an exact blocker.

## Slice B1 -- Post-trade leaked-thread recovery

- Current evidence: the collateral heartbeat succeeded through 18:33:17 local; from 18:34:35 onward every 30-second attempt exceeded its 25-second deadline and logged `thread leaked, daemon should restart`.
- The daemon wrapper currently records FAILED and returns, so the next cadence leaks another thread. Its concurrent chain-sync job has also remained in-flight since 18:33:40, holding downstream DB users behind it.
- Rejected design: terminating the whole sidecar on collateral timeout could interrupt a concurrent Safe WRAP/APPROVE after the chain transaction but before its local receipt commit. It also could not distinguish a truly leaked deadline thread from an inner `TimeoutError`. This design was not deployed.
- Change boundary: `src/ingest/post_trade_capital_daemon.py` only. Run the existing pUSD-only refresh in a one-shot child interpreter with an outer deadline. A hang is killed and reaped at the child boundary; wrap/redeem/harvester jobs remain alive in the parent.
- Safety basis: collateral refresh performs venue balance/allowance reads plus a short SQLite snapshot write; it submits no external transaction. Killing its child can roll back an in-flight DB write but cannot create an external-action/local-receipt tear.
- Forbidden: change collateral truth, extend stale acceptance, terminate the parent sidecar, restart the order daemon from inside the sidecar, or alter chain-sync/venue logic.
- Test: child command/deadline is exact, timeout becomes FAILED scheduler health without parent exit, nonzero child outcome is not false-green, and existing success behavior remains.
- Verification: P4/collateral suite passes 24/24; a real sleeping child is killed and reaped by the outer deadline. Independent adversarial re-review found no P0/P1 and ruled the slice `DEPLOY-SAFE`.
- Carry-forward P2: a custom collateral deadline can make `deadline+2s` exceed the 30-second cadence. The default is 27 seconds and `max_instances=1` prevents overlap; deploy observation must verify no skip/restart loop, and a later bounded-config contract may make the relationship unconstructable.

## Verification

- Re-run current health composite after every mutation.
- Re-sample loaded SHA/PID, open positions, q identity, posterior/FSR identity, reactor completion cadence, and venue command/event counts.
- Actual order proof requires separate `venue_commands`, submit event, venue ACK/order ID, fill/trade fact, and capital change lines. A candidate or health-clear signal is not an order.
