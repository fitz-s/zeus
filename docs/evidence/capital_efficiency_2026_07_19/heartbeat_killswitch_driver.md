# Heartbeat/kill-switch reduce-only driver — cause, verdict, fix status (2026-07-19)

Read-only investigation. Scope: why `kill_switch_armed` with
`kill_switch_reason=heartbeat_lost` accounts for ~58% (1874/3223) of all
`reduce_only=true` `exit_monitor` decision_log rows over the last 30 days
(`state/zeus_trades.db`), per the premise handed to this investigation and
`docs/evidence/capital_efficiency_2026_07_19/m5_latch_persistence.md`.

**Headline: the defect is real, was measured correctly, and has already been
fixed in this repo — twice, in the last ~27 hours, the second time 21 minutes
before this investigation queried the DB.** No code change is proposed here;
this report documents the mechanism, confirms the fix landed and is behaving
as intended, and flags one still-open, separate concern (freshness-window
margin) that the fix did not touch.

---

## 1. Mechanism — every producer/consumer, with the historical bug

**Where the field comes from**: `exit_monitor` decision_log rows carry
`summary.held_monitor_allocator_refresh`, built by
`_refresh_global_allocator_for_held_position_monitor`
(`src/execution/exit_lifecycle.py:6512`), which calls
`refresh_global_allocator` (`src/risk_allocator/governor.py:706`) with
`heartbeat=heartbeat_supervisor.summary()` and `ws_status=ws_gap_guard.summary()`.
That function calls `PortfolioGovernor.update_state`
(`src/risk_allocator/governor.py:498-534`), which builds `GovernorState` and
exposes it via `RiskAllocator.kill_switch_reason` (`governor.py:440-446`) and
`RiskAllocator.reduce_only_mode_active` (`governor.py:403-438`).

**Heartbeat health itself** comes from `ExternalHeartbeatSupervisor.status()`
(`src/control/heartbeat_supervisor.py:957-1090`), a read-only reader of
`state/venue-heartbeat-keeper.json`, written by a separate keeper daemon
(`com.zeus.venue-heartbeat` launchd job, PID confirmed live at investigation
time). Health is `HeartbeatHealth.LOST` when the status file is missing,
unreadable, has no `written_at`, is from the future, or its `age_seconds`
exceeds the freshness threshold (`heartbeat_supervisor.py:1057`,
`FreshnessLevel.STALE` via `_freshness_registry.evaluate("heartbeat_status", ...)`).

**The historical bug** (git-confirmed, `git log -p` on
`src/risk_allocator/governor.py`): before commit `a42510ca0` (authored
2026-07-18T18:37:25+01:00 = 17:37:25 UTC), `_automatic_kill_switch_reason`
contained:
```python
if heartbeat_health is HeartbeatHealth.LOST:
    return "heartbeat_lost"
```
This set `kill_switch_armed=True` with `kill_switch_reason="heartbeat_lost"`
whenever the keeper's status file went stale/missing — for **any** duration,
including single-cycle blips. `kill_switch_armed` is the harshest gate in the
system: `kill_switch_reason(...)` is checked unconditionally in
`can_allocate`/`maker_or_taker`/`select_global_order_type` and blocks **all**
submit paths, exits included — not just new entries. So every heartbeat blip,
however brief, froze the entire money path (entries and exits) until the
keeper's status file refreshed.

**Two commits already fixed this, sequentially**:
- `a42510ca0` "perf(execution): isolate heartbeat to resting orders"
  (2026-07-18 17:37 UTC) removed the `heartbeat_lost` → kill-switch mapping
  entirely, and also removed heartbeat from `reduce_only_mode_active()`.
  Unhealthy heartbeat now only forces `maker_or_taker()` into `TAKER` (FOK)
  mode — resting orders need a live lease, immediate orders don't. This
  over-corrected: for the next ~27 hours, a lost heartbeat placed **zero**
  restriction on new entries.
- `d8802c749` "fix(control): bind submits to current heartbeat truth"
  (2026-07-19 16:25 EDT = 20:25 UTC — 21 minutes before this investigation's
  DB queries) re-added heartbeat unhealthy
  (`UNCONFIGURED`/`STARTING`/`LOST`) as a trigger inside
  `reduce_only_mode_active()` (`governor.py:409-414`), **not** the kill switch.
  This is the correctly-scoped consequence: lost heartbeat blocks new
  resting-risk entries, but exits and existing-position management remain
  fully available.

**What clears it**: `heartbeat_health` returns to `HEALTHY` the next time the
keeper writes a fresh, valid status file (observed live: `state/
venue-heartbeat-keeper.json` currently shows `health=HEALTHY`,
`consecutive_successes=44`, `cadence_seconds=5`, updated every ~5s). No manual
clear path exists or is needed — this is a continuous read of current keeper
state, not a latch.

---

## 2. DB verification that the fix is real and live

```
30d exit_monitor rows with kill_switch_reason='heartbeat_lost' (kill_switch_armed=1):
  1639 rows (of the 1874 credited to "kill_switch_armed/heartbeat_lost" broadly)
  Daily counts 2026-06-19 → 2026-07-18: nonzero on 20 of 21 days, ranging 1–458/day
  Latest occurrence: 2026-07-18T17:37:27.716654+00:00 — 2 seconds after a42510ca0's
    commit timestamp (consistent with the fix deploying essentially at commit time)
  Rows since a42510ca0 (>2026-07-18T17:37:25): 1 (the in-flight cycle above)
  Rows since d8802c749 (>2026-07-19T20:25:19, i.e. in the last 21 min of data): 0

Gap window (a42510ca0 → d8802c749, ~27 hours, 2026-07-18T17:37:27 → 2026-07-19T20:25:19):
  heartbeat_health=LOST, reduce_only=false: 159 exit_monitor cycles
    (confirms the over-correction was real: lost heartbeat blocked nothing)
  heartbeat_health=LOST, reduce_only=true:   51 cycles (reduce_only True for
    other reasons — m5/ws-gap/systemic-unknown — coincident with LOST, not caused by it)

Since d8802c749 (2 exit_monitor cycles observed so far, ~21 min of data):
  heartbeat_health=LOST, kill_switch_armed=0, reduce_only=1: 2/2 — exactly the
  new intended behavior (entries blocked, kill switch not armed).
```

`zeus-live.log` (12-day window, 2026-07-07 onward) independently shows 727
lines containing `kill_switch_reason=heartbeat_lost` — i.e. 727 reactor
cycles during that window where the **entire** money path (not just entries)
was frozen by this bug, consistent with the DB-side daily counts.

---

## 3. Is the underlying LOST condition itself genuine or a sensor artifact?

`logs/zeus-venue-heartbeat.err` (7.2MB, 62,902 lines) shows the keeper's own
errors are real venue-side transport failures against Polymarket's CLOB
heartbeat endpoint: `PoolTimeout`, `The read operation timed out`,
`UNEXPECTED_EOF_WHILE_READING`, occasional `Invalid Heartbeat ID` / `Could not
create api key` (HTTP 400) responses. **This is a genuine, intermittent
external-dependency reliability problem, not a clock-skew or misclassification
bug** — when the CLOB heartbeat call fails or times out, the keeper's status
file legitimately goes stale.

However, the margin between writer cadence and consumer freshness threshold is
tight: the launchd plist for `com.zeus.venue-heartbeat` sets
`ZEUS_HEARTBEAT_CADENCE_SECONDS=5` and `ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS=2`;
no plist sets `ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS`, so the consumer
(`heartbeat_status_max_age_seconds_from_env()`, `heartbeat_supervisor.py:334`)
falls back to the code default `DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS=8`
(`heartbeat_supervisor.py:35`). An 8s freshness window against a 5s cadence
with a 2s HTTP timeout leaves only ~3s of slack — a single retry after one
timed-out call is enough to exceed the budget. Sampled episode timestamps
(e.g. 2026-07-04, 458 rows that day) show sustained multi-cycle runs
(20+ consecutive 2-minute-cadence cycles ≈ 40+ min), i.e. real outages, not
purely single-cycle flapping — but the tight margin means ordinary
network jitter, not just outright outages, likely also contributes to the
count. This margin was **not** touched by either fix commit and remains a
live (now lower-stakes, entries-only) contributor to reduce_only frequency
going forward. Distinguishing "genuine sustained venue outage" from "jitter
inside the 3s margin" is a direct measurement task against the existing
30-day `decision_log` and heartbeat-keeper logs already on disk — build
per-episode duration histograms from that history now, no new data
collection or waiting period required. This investigation did not build
them (time-boxed); flagging as the next direct measurement, not quantifying
further here.

---

## 4. Cost

Historical (pre-fix) cost was worse than "idle capital": 727 reactor cycles
in the 12-day log window had the **entire money path** frozen (entries and
exits), not just new-entry blocking — a bug the money-path reactor's transient
requeue design (`no cap; horizon-bounded`) could not fully absorb for exits,
since exits were also blocked, unlike the ws_gap case in the sibling M5
report. Direct pre-submit-blocked reactor log lines
(`risk_allocator_pre_submit_blocked`) total only 41 in the 12-day window
across all reduce-only causes combined (not heartbeat-attributable
individually from the log text alone), consistent with the sibling M5
report's finding that the reactor requeues transient blocks quickly rather
than dropping candidates outright. Going forward (post d8802c749), the cost
degrades to the same shape as any other reduce_only cause: new entries
requeued for the cycle(s) the keeper's heartbeat is stale, exits unaffected.

---

## 5. Fix spec — status: ALREADY IMPLEMENTED

No code change is being proposed by this investigation; the fix already
exists in the working tree:

1. **Consequence correctly scoped** (`d8802c749`, `governor.py:403-414`):
   heartbeat `UNCONFIGURED`/`STARTING`/`LOST` → `reduce_only_mode_active()=True`
   (blocks new resting-risk entries only). It is **not** routed through
   `kill_switch_reason()` (`governor.py:440-446`, unchanged — only
   `ws_gap_threshold` and manual reasons trip the kill switch now), so exits
   and existing-position management are never blocked by heartbeat loss.
   Verified live in DB: 2/2 exit_monitor cycles since the fix landed show
   `kill_switch_armed=0, reduce_only=1` on LOST heartbeat.
2. **Gate authority untouched**: `reduce_only_mode_active()` still fails
   closed on missing/lost/starting heartbeat — the fix changes *which* gate
   trips (reduce-only vs full kill-switch), not whether protection exists.
3. **Open, unaddressed, separate item**: the 8s freshness window vs 5s
   writer cadence / 2s HTTP timeout margin (`heartbeat_supervisor.py:35`,
   `com.zeus.venue-heartbeat.plist`) is tight and was not touched by either
   commit. Next direct step: build the episode-duration histograms from the
   existing 30-day `decision_log` + heartbeat-keeper logs (already on disk,
   §3 above) to size genuine-outage vs jitter, then decide whether widening
   the window is warranted — a measurement to run now against history, not
   a forward observation window to sit through.

Report: `/Users/leofitz/zeus/docs/evidence/capital_efficiency_2026_07_19/heartbeat_killswitch_driver.md`
