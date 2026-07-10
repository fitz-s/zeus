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

## Slice B2 -- Released exit projection health

- Canonical sequence for Seoul `656594aa-af2`: `EXIT_ORDER_REJECTED` seq 663 left `pending_exit`; `EXIT_RETRY_RELEASED` seq 666 legally transitioned `pending_exit -> day0_window`; held `MONITOR_REFRESHED` starts at seq 667.
- Defect: the projection-regression query selects the latest event only from `EXIT_INTENT`, `EXIT_ORDER_REJECTED`, and `EXIT_ORDER_POSTED`. It excludes `EXIT_RETRY_RELEASED`, so every legal held monitor after release is compared against the older rejection and mislabeled as a regression.
- Minimal repair: make release part of the latest exit-transition ordering and exclude a position when that latest transition is `EXIT_RETRY_RELEASED`. A new exit intent/rejection after release remains blocking because it becomes latest again.
- Forbidden: change position phase, suppress a genuine held projection after an unreleased exit, weaken any exit submit/runtime gate, or mutate live DB state.
- Acceptance: unreleased rejection + held projection still fails; release + held projection clears; current Seoul false positive disappears after deploy.
- Verification: full `tests/test_run_mode_failure_surfaces.py` passes 86/86. The antibody proves release -> held clears and release -> newer intent/rejection -> held fails again. Independent SQL review found no P0/P1 and judged the slice deploy-safe.

## Slice B3 -- Forecast bridge family identity

- Live proof after B2: the latest FSR names Taipei `2026-07-12/high` identity `0a8e736...`, whose matching and latest same-family posterior both computed at `17:49:44Z`. Health compares it to the global max `18:07:16Z` from unrelated Buenos Aires/Shenzhen families and reports a false `latest_newer_by=1052s`.
- Defect: posterior supersession is meaningful only inside the same probability authority identity family. A posterior for another city/date/metric cannot supersede the FSR's causal posterior.
- Minimal repair: preserve the global posterior-to-event timing bridge, but compute identity supersession against the latest live posterior for the same `product_id × city × target_date × metric`, using the same source-cycle/computed ordering as FSR selection.
- Forbidden: accept a superseded identity inside the same family, remove global bridge timing, weaken posterior/FSR age budgets, or change event production.
- Acceptance: same-family newer posterior remains blocking; different-family newer posterior does not supersede; current live false positive clears after deploy.
- Verification: bridge tests pass 7/7 and full live-health file passes 87/87. A read-only HEAD evaluation on current DB resolves the latest event identity to the exact same-family latest posterior with lag `0.0s` while preserving global posterior-to-FSR timing. Independent review found no P0/P1 and judged the slice deploy-safe.
- Carry-forward P2: the health family query additionally scopes by `source_id`, while producer supersession currently relies on one canonical source per replacement product. This is true in current materialized live rows; if a product later admits multiple live source IDs, producer and health need one shared executable family-key contract.
- Post-deploy review erratum: an adversarial counterexample proved the identity-match success return skipped the pre-existing global producer-stall condition. Current runtime was not stalled (`latest FSR created after global latest posterior`), but the shape could false-green if an unrelated-family posterior became globally newest and no later FSR existed. Corrective slice B3.1 is mandatory immediately.

## Slice B3.1 -- Preserve global producer stall under identity match

- Required conjunction: identity health has two independent axes. Same-family latest ordering proves the FSR's causal identity is not superseded; global posterior/event ordering proves the producer bridge has not stopped altogether.
- Minimal repair: evaluate `posterior newer than latest FSR by > budget AND posterior age > budget` before an identity match can return healthy. Keep same-family supersession and payload-age checks unchanged.
- Acceptance: unrelated-family newer posterior plus a newer FSR remains healthy; unrelated-family posterior with no later FSR becomes `FORECAST_TO_EVENT_BRIDGE_STALLED`; same-family newer posterior remains `IDENTITY_SUPERSEDED`.
- Verification: bridge tests pass 8/8 and full live-health file passes 88/88. Independent re-review confirms the original P1 is closed: identity and non-identity branches now share the global stall predicate, and no healthy return precedes it.

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

## Slice B4 -- Bounded live working-set reads

- Current runtime proof: the reactor run started at `18:46:50Z`, completed `pending_prune` at `18:46:57Z`, and then emitted no `forecast_snapshot_build` completion for more than ten minutes. That stage spans `_edli_pending_entity_keys` plus the forecast builder, so the log anchors alone do not isolate one call. The pending-key query had no SQLite progress deadline and its plan allowed an unbounded status scan, per-row event PK lookup, and temporary DISTINCT tree. `sqlite_stat1` was stale (2,520,044 estimated rows); a later exact read found 10,801,165 processing rows but only 1,018 pending and 12 processing. Hot read-only timing was 179ms for the old query and 94ms for the bounded query; this is a structural I/O amplifier, not proven as the sole ten-minute root. The separately budgeted forecast builder was 52ms hot after recovery.
- Current exit-monitor proof: the run started at `18:48:20Z` and did not reach the cadence-watchdog result until about `18:56:20Z`. The watchdog's `MAX(occurred_at) WHERE event_type='MONITOR_REFRESHED'` has no event-type-leading index and scans the 85.9GB trade DB. It had not entered `PolymarketClient`; this is not venue latency.
- Minimal reactor repair: read only a bounded newest active processing working set per valid active status using the existing `(consumer_name, processing_status, updated_at)` index, then join event provenance and deduplicate entity keys. Apply a SQLite deadline to this pre-build read and restore connection handlers/timeouts on every path. The cap is the existing reactor prune batch limit; no new trading threshold is introduced.
- Minimal monitor repair: enumerate canonical current non-terminal positions, then use the existing `(position_id, event_type, sequence_no DESC)` index to read each position's latest `MONITOR_REFRESHED`. This preserves event-proof semantics and removes the unindexed global event scan; do not replace it with projection `updated_at`.
- Forbidden: add a schema/index migration, delete/archive rows, weaken event freshness or probability gates, bypass global auction revalidation, change order eligibility, or perform venue actions.
- Acceptance: query plans are bounded/indexed; a large irrelevant event history does not change the result or runtime shape; the current 1,030 active processing rows are all below the configured 5,000-per-status bound; active-position latest monitor is detected; closed-only historical monitor rows cannot false-green the watchdog; existing busy timeout/progress handlers are restored; full affected suites pass; independent live-money critic finds no P0/P1.
- Verification: affected suites pass 85/85 twice; target modules compile; direct current-DB read-only timing was 94ms for the bounded pending-key read and 1ms for the indexed watchdog; live `EXPLAIN` proves MATERIALIZED bounded active IDs -> event PK lookups and current-position phase index -> position/event/sequence lookups. Independent review found no P0/P1 and returned `DEPLOY-SAFE WITH P2 FOLLOW-UPS`.
- Carry-forward P2: the per-status cap is applied before event-type filtering, so a future active set above 5,000/status can omit older FSR keys when newer other-type rows dominate and cause bounded queue churn; current active rows are below the cap and submit idempotency/revalidation remain intact. The watchdog still returns no alert for a current position that has never emitted any `MONITOR_REFRESHED`. The helper clears its own progress handler but cannot restore an unknown handler installed before it; the current call site has no nested handler.
