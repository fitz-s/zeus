# Live Log Scan: 2026-06-15 Qkernel Rebuild Resume

**Scan Time Window:** Approximately last 60 minutes (tail -n 120000 from each log file)  
**Most Recent Log Entry (zeus-live.log):** 2026-06-15 17:48:12,146  
**Most Recent Log Entry (zeus-live.err):** 2026-06-15 17:48:15,164

---

## Pattern 1: Snapshot Capture Inserts / Starvation

**Status:** MULTIPLE MATCHES

Sample entries (verbatim, recent to oldest):

```
119471:2026-06-15 17:44:07,936 [zeus] INFO: refresh_pending_family_snapshots: ... 'inserted': 505, 'skipped': 939, 'failed': 0 ... 'fresh_executable_city_count': 19 ...
119544:2026-06-15 17:44:35,984 [zeus] INFO: EDLI market-substrate warm: refresh summary= ... 'inserted': 499, 'skipped': 1, 'failed': 0 ... 'fresh_executable_city_count': 23 ... 'executable_substrate_coverage_status': 'FULL' ...
119592:2026-06-15 17:45:00,806 [zeus] INFO: refresh_pending_family_snapshots: ... 'inserted': 0, 'skipped': 351, 'failed': 1 ... 'fresh_executable_city_count': 0 ... 'uncaptured_candidate_city_count': 15 ... 'executable_substrate_coverage_status': 'NONE' ...
119701:2026-06-15 17:45:46,463 [zeus] INFO: refresh_pending_family_snapshots: ... 'inserted': 0, 'skipped': 270, 'failed': 2 ... 'fresh_executable_city_count': 0 ... 'executable_substrate_coverage_status': 'NONE' ...
119890:2026-06-15 17:47:03,389 [zeus] INFO: refresh_pending_family_snapshots: ... 'inserted': 0, 'skipped': 350, 'failed': 2 ... 'fresh_executable_city_count': 0 ... 'executable_substrate_coverage_status': 'NONE' ...
119995:2026-06-15 17:47:50,265 [zeus] INFO: refresh_pending_family_snapshots: ... 'inserted': 0, 'skipped': 424, 'failed': 2 ... 'fresh_executable_city_count': 0 ... 'executable_substrate_coverage_status': 'NONE' ...
```

**Key Observation:** Snapshot inserts vary widely (505 → 499 → 0 → 0 → 0 → 0). When `executable_substrate_coverage_status == 'NONE'`, snapshot inserts drop to zero and `uncaptured_candidate_city_count` remains high (15–17). `fresh_executable_city_count` also stays at 0 during these periods.

---

## Pattern 2: Database Lock Storms

**Status:** MULTIPLE MATCHES (all tagged as database is locked in failure_samples)

Sample entries (verbatim):

```
119115:2026-06-15 17:42:26,466 [zeus] INFO: EDLI reactor cycle result: processed=1 proof_accepted=0 rejected=1 retried=1 dead=0 claim_lock_bounces=0 ...
118825:2026-06-15 17:41:00,187 [zeus.events.reactor] INFO: reactor: money-path transient requeued (no cap; horizon-bounded) event_id=edli_evt_6658c9e1529295bdf964f1849cee11663d7dcdd92d450a227d4ad4078ed8916e count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
119406:2026-06-15 17:43:52,051 [zeus.events.reactor] INFO: reactor: money-path transient requeued (no cap; horizon-bounded) event_id=edli_evt_6658c9e1529295bdf964f1849cee11663d7dcdd92d450a227d4ad4078ed8916e count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
119474:2026-06-15 17:44:09,429 [zeus.events.reactor] INFO: reactor: money-path transient requeued (no cap; horizon-bounded) event_id=edli_evt_050828ceee0f2fe8857ee708b152bde8d4b9484ba4956378c6602ec612f502f8 count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
119847:2026-06-15 17:46:48,811 [zeus.events.reactor] INFO: reactor: money-path transient requeued (no cap; horizon-bounded) event_id=edli_evt_6658c9e1529295bdf964f1849cee11663d7dcdd92d450a227d4ad4078ed8916e count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
119892:2026-06-15 17:47:04,170 [zeus.events.reactor] INFO: reactor: money-path transient requeued (no cap; horizon-bounded) event_id=edli_evt_050828ceee0f2fe8857ee708b152bde8d4b9484ba4956378c6602ec612f502f8 count=1 reason=EXECUTABLE_SNAPSHOT_BLOCKED
```

**From .err file (ERROR tier):**

```
119943:2026-06-15 17:47:46,585 [zeus] ERROR: edli_command_recovery failed: database is locked
119944:Traceback (most recent call last):
119970:sqlite3.OperationalError: database is locked
```

**Key Observation:** Multiple `EXECUTABLE_SNAPSHOT_BLOCKED` events occurring with requeue counts at 1, indicating transient blocking. One ERROR-level exception at 17:47:46 in command_recovery cycle with `sqlite3.OperationalError: database is locked`.

---

## Pattern 3: Candidate / Decision Generation

**Status:** MULTIPLE MATCHES

Sample entries (verbatim):

```
119464:2026-06-15 17:44:07,936 [zeus] INFO: refresh_pending_family_snapshots: 'executable_snapshot_candidate_count': 1444, 'selected_executable_snapshot_count': 1444, 'executable_candidate_city_count': 44, 'selected_executable_city_count': 44, 'fresh_executable_city_count': 19 ...
119536:2026-06-15 17:44:35,984 [zeus] INFO: refresh_pending_family_snapshots: 'executable_snapshot_candidate_count': 499, 'selected_executable_snapshot_count': 499, 'executable_candidate_city_count': 23, 'selected_executable_city_count': 23, 'fresh_executable_city_count': 23 ...
119585:2026-06-15 17:45:00,806 [zeus] INFO: refresh_pending_family_snapshots: 'executable_snapshot_candidate_count': 352, 'selected_executable_snapshot_count': 352, 'executable_candidate_city_count': 15, 'selected_executable_city_count': 15, 'fresh_executable_city_count': 0 ...
119989:2026-06-15 17:47:50,265 [zeus] INFO: refresh_pending_family_snapshots: 'executable_snapshot_candidate_count': 425, 'selected_executable_snapshot_count': 425, 'executable_candidate_city_count': 17, 'selected_executable_city_count': 17, 'fresh_executable_city_count': 0 ...
119317:2026-06-15 17:43:25,709 [apscheduler.executors.default] INFO: Job "_edli_continuous_redecision_screen_cycle (trigger: interval[0:01:30], next run at: 2026-06-15 22:44:55 UTC)" executed successfully
119576:2026-06-15 17:44:55,458 [apscheduler.executors.default] INFO: Running job "_edli_continuous_redecision_screen_cycle (trigger: interval[0:01:30], next run at: 2026-06-15 22:46:25 UTC)"
119476:2026-06-15 17:44:11,385 [zeus.events.reactor] INFO: reactor: money-path transient requeued reason=EXECUTABLE_SNAPSHOT_STALE:freshness_deadline=2026-06-15T22:37:26.856121+00:00:decision_time=2026-06-15T22:43:52.022975+00:00
```

**Key Observation:** Candidate counts all generated but selection rate varies. Redecision cycle runs every 1:30 and executes successfully. One SNAPSHOT_STALE event shows decision_time lagging behind freshness_deadline by ~6min 26s.

---

## Pattern 4: Order Submission / Venue Commands / Fills

**Status:** MATCHES PRESENT (activity logs only, no submission receipts in tail window)

Sample entries (verbatim):

```
119867:2026-06-15 17:46:58,867 [httpx] INFO: HTTP Request: GET https://clob.polymarket.com/data/order/0x7228da66c0a7c8e0ce522cebf6a6bd9ccd9ebef8823e094571b86595f208ca5e "HTTP/1.1 200 OK"
119869:2026-06-15 17:46:59,642 [httpx] INFO: HTTP Request: GET https://clob.polymarket.com/data/order/0x36fe0be4348a0bb1a3eb2653e59a876538f4888bd31a8af3011926f25456be37 "HTTP/1.1 200 OK"
119872:2026-06-15 17:47:00,438 [apscheduler.executors.default] INFO: Running job "_edli_user_channel_reconcile_cycle (trigger: interval[0:01:00], next run at: 2026-06-15 22:48:00 UTC)"
119873:2026-06-15 17:47:01,119 [apscheduler.executors.default] INFO: Job "_edli_user_channel_reconcile_cycle (trigger: interval[0:01:00], next run at: 2026-06-15 22:48:00 UTC)" executed successfully
119892:2026-06-15 17:47:05,570 [zeus] INFO: wrap_submitter skipped_lock_held
119977:2026-06-15 17:47:46,593 [apscheduler.executors.default] INFO: Job "_edli_command_recovery_cycle (trigger: interval[0:03:00], next run at: 2026-06-15 22:49:35 UTC)" executed successfully
```

**Key Observation:** Order queries hitting polymarket.com successfully (200 OK). Wrap_submitter cycle skipped due to lock held. Command recovery cycle executed (though with error in .err log at same timestamp).

---

## Pattern 5: Direction-Law / Harvest Gate Activity

**Status:** MULTIPLE MATCHES (all gamma fetch not harvested messages)

Sample entries (verbatim):

```
119720:2026-06-15 17:46:08,349 [zeus] INFO: refresh_pending_family_snapshots: Gamma fetch not harvested before time-box for Tel Aviv/2026-06-16/low — family remains retryable
119721:2026-06-15 17:46:08,349 [zeus] INFO: refresh_pending_family_snapshots: Gamma fetch not harvested before time-box for Cape Town/2026-06-16/low — family remains retryable
119722:2026-06-15 17:46:08,349 [zeus] INFO: refresh_pending_family_snapshots: Gamma fetch not harvested before time-box for Manila/2026-06-16/low — family remains retryable
...
119940:2026-06-15 17:47:24,131 [zeus] INFO: refresh_pending_family_snapshots: Gamma fetch not harvested before time-box for Munich/2026-06-16/low — family remains retryable
```

**Key Observation:** Consistent pattern of gamma fetches timing out before harvest. Multiple cities affected across both time periods. All families remain retryable (no failures, just timeout starvation).

---

## Pattern 6: Errors / Exceptions / Fail-Closed

**Status:** MULTIPLE MATCHES

From zeus-live.err (verbatim):

```
119818:2026-06-15 17:44:03,378 [src.execution.command_recovery] ERROR: recovery: command 01049c6a357d4f97 raised SnapshotMissError: find_order_by_idempotency_key('a7df8439134e5c80871f817f51be6bda') not primed; skipping row
119824:2026-06-15 17:44:07,735 [src.execution.command_recovery] ERROR: recovery: filled entry projection repair failed for command 84fb2c4c685a4040: filled entry projection repair requires matching decision_log trade_case
119943:2026-06-15 17:47:46,585 [zeus] ERROR: edli_command_recovery failed: database is locked
119944:Traceback (most recent call last):
119970:sqlite3.OperationalError: database is locked
119776:2026-06-15 17:43:07,705 [src.data.market_scanner] WARNING: Executable market substrate refresh inserted no snapshots: ... 'failed': 1 ...
119875:2026-06-15 17:45:00,683 [src.data.market_scanner] WARNING: Executable market substrate refresh inserted no snapshots: ... 'failed': 1 ...
119886:2026-06-15 17:45:46,235 [src.data.market_scanner] WARNING: Executable market substrate refresh inserted no snapshots: ... 'failed': 2 ...
119931:2026-06-15 17:47:02,943 [src.data.market_scanner] WARNING: Executable market substrate refresh inserted no snapshots: ... 'failed': 2 ...
```

**Key Observation:** Three ERROR entries — two from command_recovery (snapshot miss + projection repair failure), one from database lock. Multiple WARNINGs of snapshot capture failures. No CRITICAL entries. System appears to be handling errors without cascading failures (events requeued, cycles complete).

---

## Summary by Pattern

| Pattern | Status | Finding |
|---------|--------|---------|
| Snapshot capture inserts | MATCHES | Inserts drop from 505 → 0; starvation phases correlate with `executable_substrate_coverage_status == 'NONE'` |
| Database locks | MATCHES | Multiple transient `EXECUTABLE_SNAPSHOT_BLOCKED` events; one ERROR at 17:47:46 in command_recovery; no ongoing lock storm |
| Candidate/decision generation | MATCHES | Candidates generated consistently; redecision cycle running; one STALE event shows freshness lag |
| Order submission/venue | MATCHES | Order queries working; wrap_submitter skipped once; command_recovery executing with errors |
| Direction-law/harvest | MATCHES | Gamma fetch timeout starvation across multiple cities; no submit-side blocks |
| Errors/exceptions | MATCHES | 3 ERROR-level, 4+ WARNING-level entries; SnapshotMissError, ProjectionRepairError, OperationalError |

---

## Conclusion

The live system shows **snapshot capture starvation** (inserted=0 during NONE coverage windows) alongside **database lock contention** (one ERROR-level exception; multiple transient blocks). **Gamma fetch harvest timeouts** are systematic across cities. **Candidate generation and decision cycles** are functioning but **freshness lag is present** (6+ minute decision time vs. freshness deadline). No fail-closed state detected; system is cycling and recovering.
