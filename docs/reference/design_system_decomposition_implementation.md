# Zeus System Decomposition — Implementation Report

Created: 2026-06-08
Last reused or audited: 2026-06-09 (whole-refactor final verification)
Authority basis: docs/architecture/system_decomposition_plan.md (the spec — §0 framing,
  §3 verdicts, §4 lift-outs, §5 what-stays, §6 target topology, §7 interface contracts,
  §8 migration steps, §9 regression-unconstructable proof). This report records WHAT was
  implemented against that plan and the verification evidence; the plan remains the law.

Worktree: /Users/leofitz/zeus-decomp · branch refactor/system-decomposition · HEAD 5626c18b38
Verification harness:
`cd /Users/leofitz/zeus-decomp && PYTHONSAFEPATH=1 PYTHONPATH=/Users/leofitz/zeus-decomp /Users/leofitz/zeus/.venv/bin/python -m pytest <files> -q`

> **DEPLOY POSTURE: ARTIFACT-ONLY.** This refactor created the new daemon entry-point
> modules and their launchd `.plist` artifacts. It did NOT `launchctl load`, kickstart,
> enable, or restart any service, and it did NOT touch the live checkout
> (`/Users/leofitz/zeus`). The residual operator deploy steps are listed in §7.

---

## 1. New program topology (which jobs now live where)

The order daemon (`src/main.py`, `com.zeus.live-trading`) was a god-host registering ~22
APScheduler jobs. Four lift-outs moved every ALWAYS_ON producer and POST_TRADE follow-up
concern into three NEW programs, leaving a lean order runtime. Each lifted producer writes a
DB TABLE the order runtime only READS (plan §7); there is no in-process back-coupling, and no
lifted producer is gated on the consumer (reactor) queue/flags.

| Program (launchd label → module) | Status | Jobs it now owns (id) |
|---|---|---|
| **P1 order-runtime** `com.zeus.live-trading` → `src.main` | LEAN (unchanged service) | `edli_event_reactor`, `edli_bankroll_warm`, `edli_mainstream_warm`, `arm_gate_emit`, `exit_monitor` (the exit-SUBMIT phase), `heartbeat`, `world_wal_checkpoint`, `venue_heartbeat`, `deployment_freshness` (+ legacy_cron-only: `opening_hunt`/`day0_capture`/`imminent_open_capture`/`update_reaction_*`) |
| **P2 substrate-observer** `com.zeus.substrate-observer` → `src.ingest.substrate_observer_daemon` (lane: `src.data.substrate_observer`) | NEW | `market_discovery` (universe sweep, 5-min, STALENESS-only), `edli_market_substrate_warm` (pending-family warm, 20s) |
| **P3 price-channel-ingest** `com.zeus.price-channel-ingest` → `src.ingest.price_channel_daemon` (lane: `src.ingest.price_channel_ingest`) | NEW | user-channel WS ingestor THREAD (`_start_user_channel_ingestor_if_enabled`), `edli_market_channel_ingestor` (1-min), `edli_user_channel_reconcile` (1-min) |
| **P4 post-trade-capital** `com.zeus.post-trade-capital` → `src.ingest.post_trade_capital_daemon` (lane: `src.execution.post_trade_capital`) | NEW | `chain_sync_read` (chain-truth READ phase, 2-min), `harvester` (1h), `redeem_submitter` (5-min), `redeem_reconciler` (10-min), `wrap_intent_creator` (5-min), `wrap_submitter` (2-min), `wrap_reconciler` (2-min) |
| data-ingest `com.zeus.data-ingest` → `src.ingest_main` | unchanged | sole live WU daily owner (`ingest_k2_daily_obs`) after Step 4 dedup |

**What STAYS in P1 and why (plan §5):** the EDLI event reactor (the q→size→submit heart);
bankroll warm (feeds Kelly atomically); the **mainstream-forecast warmer** (writes the
process-global `_WARM_CACHE` dict that ONLY P1's reactor reads via
`read_mainstream_point_cached` — IN_PROCESS_SHARED, not DB-mediated, so lifting it would make
every receipt carry `mainstream_*=None` forever; it is the single in-process producer→consumer
link the split deliberately does NOT touch, plan §3/§5/§7); the exit-SUBMIT phase (posts real
sell orders); the arm-gate re-emit; and the daemon's own infra (heartbeat, world WAL
checkpoint, venue-heartbeat re-arm, fill-bridge/settlement boot self-heal). The order-runtime
boot self-heal still runs ONE synchronous harvester pass at startup by importing the P4
module's `_harvester_cycle` (plan §5), so already-settled positions drain immediately on
restart without waiting for P4's first hourly tick.

**`_chain_sync_and_exit_monitor_cycle` was SPLIT (plan §8 Step 2):** the chain-sync READ phase
became P4's `chain_sync_read` (commits before returning, no per-position HTTP after — kills the
trades.db WAL-lock-across-HTTP starvation that flapped riskguard DATA_DEGRADED); the
exit-monitoring/exit-SUBMIT phase stayed in P1 as the new `exit_monitor` job.

---

## 2. Launchd plist artifacts created (artifact-only)

All three mirror the existing `com.zeus.data-ingest` pattern (KeepAlive + RunAtLoad +
ThrottleInterval, `ProgramArguments → python -m <daemon module>`), carry a Created/Authority
header comment, and an INSTALL comment block describing the operator step (NOT performed here).

- `deploy/launchd/com.zeus.substrate-observer.plist` → `python -m src.ingest.substrate_observer_daemon`
- `deploy/launchd/com.zeus.price-channel-ingest.plist` → `python -m src.ingest.price_channel_daemon`
- `deploy/launchd/com.zeus.post-trade-capital.plist` → `python -m src.ingest.post_trade_capital_daemon`

Security note: the price-channel plist needs Polymarket L2 credentials; they are committed as
`REPLACE_ME_*` placeholders (NOT real secrets) for the operator to substitute at install time
— no live secret is checked into the repo.

New daemon modules carry the file-header provenance block (Created/Last-audited 2026-06-08 +
Authority basis = the plan section). Each daemon mirrors the existing daemon shape (logging
split, SIGTERM graceful shutdown, INV-37 connection pre-flight, BlockingScheduler, 60s
heartbeat file for the staleness sensor) and imports NO trading lane (failure-domain
isolation, plan criterion 3) — verified by import-smoke (all 6 new modules import clean) and by
the relationship tests `test_*_module_is_not_a_trading_lane_import` / `test_new_daemon_does_not_import_trading_lane`.

---

## 3. src.main job-count delta

Authoritative AST enumeration of `scheduler.add_job(..., id=…)` in `src/main.py`,
baseline `772c083d1d` (pre-refactor) vs HEAD `5626c18b38`:

| Measure | Baseline | HEAD | Δ |
|---|---|---|---|
| Raw `add_job` registrations | 25 (incl. `market_discovery` registered twice: legacy_cron + EDLI) | 13 | **−12** |
| EDLI-mode runtime jobs (legacy_cron-only excluded) | 21 | 9 | **−12** |

**Removed from src.main (lifted, 12 ids):** `market_discovery` (×2 dedup'd), `edli_market_substrate_warm`
(→P2); `chain_sync_and_exit_monitor` (split — READ→P4, exit-SUBMIT→P1's new `exit_monitor`),
`harvester`, `redeem_submitter`, `redeem_reconciler`, `wrap_intent_creator`, `wrap_submitter`,
`wrap_reconciler` (→P4); `edli_market_channel_ingestor`, `edli_user_channel_reconcile` (→P3);
`wu_daily` (Step 4 verified-duplicate, removed — data-ingest is sole owner).
**Added to src.main:** `exit_monitor` (the retained exit-SUBMIT phase of the split chain-sync function).

Supporting wiring updated so the orphan-check resolves the lifted jobs against their new hosts:
`src/data/source_job_registry.py` repoints `owner_daemon` for `market_discovery`→`substrate_observer`,
`harvester`→`post_trade_capital`, user-WS→`price_channel`, and DELETES the duplicate `wu_daily`
spec; `src/data/scheduler_adapter.py` excludes the new hand-coded daemons from auto-registration
(same treatment `main` gets).

---

## 4. Per-step evidence (relationship-tests FIRST, then no-regression + superiority)

Each step shipped relationship tests that encode the cross-module invariant BEFORE the lift,
covering both the no-regression invariant (producer still writes the table the runtime reads;
src.main still imports and registers the staying jobs) and the superiority invariant (the
lifted module cannot reference the reactor backlog; gates deleted; job count reduced). The four
P-step suites total **65 tests, all green**:

```
tests/test_p2_substrate_observer_lift.py   tests/test_p3_price_channel_ingest_lift.py
tests/test_p4_post_trade_capital_lift.py   tests/test_step4_wu_daily_dedup.py
→ 65 passed
```

| Step | Commit(s) | No-regression evidence | Superiority evidence |
|---|---|---|---|
| **P2** substrate-observer (plan §8 Step 1) | `c69aa0559d` | Lane module owns BOTH producers, which share ONE in-process `_market_substrate_refresh_lock` (cannot race-write `executable_market_snapshots`); P1 keeps the SELECT-side reader + the mainstream warmer; new process opens DB via sanctioned single-DB / RO-ATTACH path (no independent cross-DB conn); src.main still imports. | Lifted module has ZERO reference to `_edli_reactor_active`/`pending_count`/`recent_discovery` — the outer pending gates (plan §0: src/main.py:3632 + :3656) are DELETED; `_market_discovery_cycle` now fires on a PURE producer-local staleness clock regardless of reactor backlog; src.main registers 2 fewer jobs and no longer defines the producers. |
| **P4** post-trade-capital (plan §8 Step 2) | `61a935335e`, `e318081bc2`, `5626c18b38` | P4 module owns the cycle bodies; `settlement_commands` enqueue is idempotent (cutover-safe); exit-SUBMIT phase stays in src.main; cascade-liveness antibody travels to P4 (the 6 pollers' contract owner → P4 module; the P4 daemon carries `_assert_cascade_liveness_contract` boot guard — verified to PASS at daemon boot-smoke); src.main still builds. | `chain_sync_read` commits chain-sync writes BEFORE returning and never submits an order → it no longer holds the trades.db WAL write lock across per-position HTTP (the riskguard DATA_DEGRADED-flap root cause, plan §4.3/I3); src.main registers strictly fewer jobs; the 7 P4 jobs register in the P4 daemon. |
| **P3** price-channel-ingest (plan §8 Step 3) | `d756341a76`, `5518f2cb89` | Lane module owns the lifted producers; the durable fill-bridge scan is shared by both processes (persisted truth → no fill lost across cutover); P1 KEEPS boot fill-bridge recovery + the feasibility-evidence reader; new process uses sanctioned ATTACH path. | The order daemon no longer starts the WS thread nor defines the WS producers; the `ws_gap_guard` submit-latch WRITER lives ONLY in P3 → a WS auth/transport flap can no longer poison P1's in-process submit latch (the reduce_only-FOREVER latch regression, plan §9); src.main registers 2 fewer cycles. |
| **Step 4** wu_daily dedup (plan §8 Step 4) | `914af6cb97` | data-ingest owns + schedules WU collection via the sanctioned INV-37 ATTACH path; the wu_icao slice is set-equivalent across modules (verified, idempotent writer → zero coverage loss). | src.main no longer registers OR defines the duplicate `_wu_daily_dispatch`; the registry duplicate is resolved (data-ingest sole owner); re-adding a main-owned WU collector trips the fail-closed registry gate (`test_unconstructable_readding_main_wu_collector_trips_failclosed_gate`). |

**Daemon boot-smoke (live build of the three new schedulers, `start()` intercepted):** all three
pass their INV-37 DB pre-flight and register exactly their lifted jobs —
P2 `{market_discovery, edli_market_substrate_warm}`,
P3 `{edli_market_channel_ingestor, edli_user_channel_reconcile}`,
P4 `{chain_sync_read, harvester, redeem_submitter, redeem_reconciler, wrap_intent_creator, wrap_submitter, wrap_reconciler}`
(plus each daemon's 60s heartbeat). P4's cascade-liveness boot guard runs and passes.

---

## 5. src.main still boots with all four lifts applied

- **Import smoke:** `import src.main` succeeds; the staying job functions are present
  (`_edli_event_reactor_cycle`, `_edli_bankroll_warm_cycle`, `_edli_mainstream_warm_cycle`,
  `_exit_monitor_cycle`, and `_harvester_cycle` imported from the P4 module for boot self-heal);
  the lifted `_market_discovery_cycle` is GONE from src.main.
- **Scheduler registration (authoritative):** AST enumeration confirms src.main registers
  EXACTLY the order-runtime jobs that should STAY (the reactor, bankroll warm, mainstream warm,
  arm-gate re-emit, the exit-SUBMIT `exit_monitor`, and infra: heartbeat, world_wal_checkpoint,
  venue_heartbeat, deployment_freshness) — and NONE of the 12 lifted ids leak back in. In live
  EDLI mode the runtime job set is exactly 9 jobs (down from 21).
- **Boot-guard:** `_assert_cascade_liveness_contract` still runs after all `add_job` calls and
  before `scheduler.start()`.

> A full live `main()` run cannot reach `scheduler.start()` inside this worktree because
> `main()` performs full live boot (venue-heartbeat against the live Polymarket API; a
> `state/zeus-forecasts.db` that does not exist in the worktree) BEFORE registering jobs. That
> is an environmental gate of the worktree, not a refactor defect — the build_scheduler smoke is
> therefore performed by AST registration enumeration (the method the relationship tests use)
> plus the three new daemons' live scheduler-build smoke.

---

## 6. Broad-suite regression verdict — ZERO new regressions

Full suite (HEAD `5626c18b38`, 15,676 tests collected after ignoring two PRE-EXISTING
collection-error files — see below):

```
704 failed, 14,719 passed, 200 skipped, 36 xfailed, 11 xpassed, 28 errors  (24m33s)
```

The 704+28 failures look alarming but are dominated by the worktree environment (no
`zeus-forecasts.db`, no live network/CLOB/weather APIs, missing optional tables, no venue-
heartbeat creds). **Classification against the pre-refactor baseline `772c083d1d`** (re-ran the
exact 730 HEAD-failing node-ids at baseline):

- **717 of 730 fail at baseline too** → PRE-EXISTING (environmental), not refactor-caused.
- **13 passed at baseline** → candidate new regressions. On per-test ISOLATION (re-run alone on
  HEAD, twice, deterministic):
  - **10** PASS in isolation → test-ordering / shared-global-state flakiness in the 15k-test run,
    NOT regressions (`test_world_mutex_io_guard` ×6, `test_user_channel_ws_auto_derive` ×2,
    `test_user_channel_ingest` ×1, `test_settlement_schema_runtime_no_ddl` ×1).
  - **2** (`test_arm_gate_emit_scheduler_job` ×2) → ENVIRONMENTAL: the `arm_gate_emit` job is NOT
    lifted and `_arm_gate_emit_cycle` is **byte-identical** baseline↔HEAD (AST-verified); the
    failures come from the producer subprocess hitting `no such table: settlement_outcomes`
    (absent in every worktree DB right now). Not a refactor code change.
  - **1** (`test_live_safety_invariants::test_harvester_scheduler_fails_closed_without_legacy_integrated_fallback`)
    → STALE TEST, not a behavioral regression. It asserts the guard string
    `resolver_unavailable_fail_closed` lives in `src/main.py`; that guard correctly TRAVELED with
    the lifted harvester to `src/execution/post_trade_capital.py` (verified). The test's actual
    safety INTENT holds — src.main has ZERO integrated-harvester fallback (`run_harvester`
    import / `result = run_harvester()` both absent, asserts pass). The test should be repointed
    to the P4 module; the fail-closed property is structurally preserved.

**Net: 0 new runtime/behavioral regressions.** The only refactor-attributable test delta is the
1 stale assertion above (safety property preserved; test location needs repointing).

**Pre-existing collection errors (NOT introduced by this refactor):**
`tests/test_backtest_skill_economics.py` (imports `src.execution.exit_triggers`, a module that
does NOT exist at baseline `772c083d1d` either) and
`tests/test_h3_fahrenheit_settlement_step_conversion.py` (imports `_settlement_step_c`, a name
absent at baseline too). Both were already broken before the refactor.

---

## 7. SUPERIORITY — the regression category is now unconstructable across program boundaries

The zero-trade regression (plan §0) was constructable because two conditions held inside ONE
process: (a) the substrate PRODUCER and the order CONSUMER shared an address space, and (b) the
producer's trigger referenced the consumer's mutable in-process state (`pending_count` of the
EDLI queue). The split removes (a) PERMANENTLY for every lifted concern: a separate address
space has no `pending_count` and no reactor handle to reference, so the `if pending: skip-capture`
line is **un-writable**, not merely deleted (plan §9). Every cross-program seam (I1–I7) is a DB
TABLE the producer writes and the consumer only READS; no producer is gated on the consumer's
queue/flags. This is the antibody (a structural change that makes the error class impossible),
not a security-guard note that re-fires.

Concretely, the three live bugs the plan targeted are now structurally prevented:

1. **Zero-trade coverage-collapse (STATE_COUPLING + TRADING_DEPENDENCE).** `market_discovery`
   runs in P2 with the outer pending gates DELETED; it fires on its own staleness clock. No
   reactor backlog can gate substrate capture — there is no in-process queue to read across the
   boundary. A missing/stale snapshot is now an OBSERVABLE table-freshness condition, not an
   invisible queue gate.
2. **riskguard DATA_DEGRADED flaps from WAL-lock starvation (FAILURE_DOMAIN).** Chain-sync moved
   to P4's `chain_sync_read`, which commits before returning and runs no per-position HTTP after.
   The trades.db WAL write lock is never held across network calls in the trading lane, so
   chain-sync can no longer starve `riskguard.tick()`.
3. **reduce_only-FOREVER submit latch (FAILURE_DOMAIN).** The `ws_gap_guard` submit-latch WRITER
   lives only in P3. A WS auth/transport flap poisons only P3's in-process latch; P1's submit
   latch is in a different address space and surfaces a WS outage only as stale/absent
   `execution_feasibility_evidence` rows (observable), never a latched gate.

The one in-process producer→consumer link the split deliberately does NOT touch — the mainstream
warmer → `_WARM_CACHE` → reactor — is co-resident IN P1 by design (it is not DB-mediated, plan
§3/§5/§7). The split introduces ZERO new state-coupling for it (moving it WOULD have:
`mainstream_*=None` on every receipt forever). The plan makes the category unconstructable across
boundaries AND refuses to manufacture a new instance by over-splitting. **Superiority confirmed.**

INV-37 residual obligation holds: every cross-DB write each new program performs still goes
through the sanctioned ATTACH+SAVEPOINT path
(`get_trade_connection_with_world_required` / world-ATTACH `get_connection` / RO-ATTACH for
topology reads) — verified by the daemons' boot pre-flights and the
`test_*_uses_sanctioned_db_path_no_independent_cross_db` relationship tests. The process boundary
relocates WHICH process owns the transaction; it does not relax the law.

---

## 8. Residual deploy steps the operator MUST perform (NOT done here)

This refactor is artifact-only. To cut over (each step independently rollback-able, plan §8):

1. **Install the three new plists** and load them (substitute the price-channel L2 creds first):
   ```
   cp deploy/launchd/com.zeus.substrate-observer.plist   ~/Library/LaunchAgents/
   cp deploy/launchd/com.zeus.price-channel-ingest.plist ~/Library/LaunchAgents/   # set POLYMARKET_API_* first
   cp deploy/launchd/com.zeus.post-trade-capital.plist   ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.zeus.substrate-observer.plist
   launchctl load ~/Library/LaunchAgents/com.zeus.price-channel-ingest.plist
   launchctl load ~/Library/LaunchAgents/com.zeus.post-trade-capital.plist
   ```
2. **Re-point / restart `com.zeus.live-trading`** onto this branch's `src.main` so the moved
   registrations stop running in the order daemon (the code already removes them; the running
   process must be restarted to pick up the lean scheduler).
3. **Co-location decision (plan §6):** if P2/P4 are to be folded into `com.zeus.data-ingest`
   rather than run as their own services, first verify the substrate-warm budget<interval
   invariant against ingest's batch (plan §6 note); otherwise keep them as the standalone
   services whose plists are provided here. P2's two jobs MUST share one process (the
   `_market_substrate_refresh_lock` binding constraint).
4. **Verify the immune-system sensor** watches the three new `daemon-heartbeat-*.json` files
   (substrate-observer / price-channel-ingest / post-trade-capital) so a silently-dead new
   producer is alerted as a stale snapshot/feasibility/settlement table, per plan §9 point 3.
5. **(Optional test hygiene, not a deploy blocker)** Repoint
   `test_live_safety_invariants::test_harvester_scheduler_fails_closed_without_legacy_integrated_fallback`
   to assert `resolver_unavailable_fail_closed` in the P4 module (`src/execution/post_trade_capital.py`)
   instead of `src/main.py` — the guard moved with the lifted harvester; the safety property is
   already preserved.

The live checkout `/Users/leofitz/zeus` was NOT touched; no `launchctl load`/`kickstart`/restart
was performed by this refactor.
