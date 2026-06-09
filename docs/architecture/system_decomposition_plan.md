# Zeus System Decomposition Plan — First-Principles Program Boundaries

Created: 2026-06-08
Last reused or audited: 2026-06-08 (R2 revision: state-coupling corrections after adversarial review)
Authority basis: AGENTS.md money/truth path; architecture/db_table_ownership.yaml; live launchd topology (~/Library/LaunchAgents/com.zeus.*.plist)

READ-ONLY analysis. This doc proposes program boundaries; it changes no running system.

## 0. Framing

`src/main.py` is 7921 lines and registers ~22 APScheduler jobs (src/main.py:7594-7901).
The god-file is a SYMPTOM. The disease is that the live order daemon has become the
default host for any concern that needed "a loop that runs" — including ALWAYS_ON
producers of the substrate the order runtime merely reads. The canonical failure
(zero-trade, 2026-06-08): executable-substrate capture (`_market_discovery_cycle`,
src/main.py:3627) lived in the order daemon and was gated by the EDLI reactor's
pending queue; a backlog made it run topology-only forever, coverage silently
collapsed, trading stopped, and nothing connected cause (a backlog) to effect (no
coverage). That is a STATE_COUPLING + TRADING_DEPENDENCE violation, not a file-size
problem.

**Current-state precision (R2 correction).** A partial antibody landed at
src/main.py:3669-3679, but it only touched the INNER snapshot-capture sub-branch
(it removed a "pending>0 → topology-only / skip-capture" gate inside the function).
The OUTER, function-level pending gates in `_market_discovery_cycle` are STILL LIVE
today: `if _edli_reactor_active(): return` (src/main.py:3632) and
`if pending_count > 0 and recent_discovery: return` (src/main.py:3656, env-gated by
`ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING` default `'1'`, :3638). So the
in-process pending→skip-discovery coupling is NOT "already removed" — the universe
sweep can still early-return on reactor/pending state. The fairness early-return at
:3650-3664 references `_market_discovery_last_completed_monotonic` (a producer-local
clock) AND `pending_count` (a consumer-derived value) together — it is a hybrid
staleness+backlog gate, not a pure staleness gate. This whole class of gate is
removed BY the process split (§8 Step 1, §9), not before it. Any reader/implementer
must treat the cutover as "the outer pending gate is still live and is deleted as
part of the lift", not "the lift just finishes a job that is already half-done".

## 1. Separation criteria (refined from the 5 axes)

A concern earns its own program ONLY if a principle below forces it. Absent a
forcing principle it STAYS in the order runtime (over-splitting multiplies cross-DB
seams and cron surfaces — itself a failure mode).

1. **TRADING-DEPENDENCE (necessary trigger).** If the concern must keep producing
   when no trading happens (paused, no edge, order daemon dead) → it MUST NOT live in
   the order runtime. Test: "if `src.main` is `launchctl unload`-ed for 6h, does this
   concern's truth go stale and silently break trading on resume?" If yes → separate.
   This is the dominant axis; the zero-trade regression was exactly this.

2. **TRUTH-SOURCE / AUTHORITY (provenance forcing).** A concern that OWNS a distinct
   external authority (chain/CLOB facts, weather models, settlement events,
   calibration fit) is a distinct PRODUCER. The order runtime is a CONSUMER of these.
   A producer and its consumer in one process means the consumer's lifecycle gates the
   producer's authority — forbidden by the operator's data-provenance law.

3. **FAILURE DOMAIN (isolation forcing).** If the concern's failure must NOT stop
   trading on already-captured data (a Gamma fetch error), OR a trading bug must NOT
   stop this concern's observation (settlement must be observed even mid-trading-crash)
   → separate. Bidirectional: producer crash must not kill consumer; consumer crash
   must not blind producer.

4. **CADENCE / LIFECYCLE (clock forcing).** Distinct clocks want distinct schedulers:
   publish-time cron (00Z/12Z forecast release), daily batch (calibration refit),
   sub-second event-react (reactor), continuous poll (heartbeat). A daily batch sharing
   a process with a 1-min reactor means the batch's resource spike (the AIFS ~365MB /
   ~11.5min fetch that was REMOVED from the trading scheduler, src/main.py:7728-7735)
   starves the reactor. Clock collision is a forcing reason only when the slow clock's
   resource envelope can starve the fast clock — cadence alone does not force a split.

5. **STATE-COUPLING (the regression axis).** If the concern shares MUTABLE in-process
   state with the decide→submit path, it is a regression surface. If it can be
   DB/file-mediated (it writes a substrate table, the runtime only reads that table)
   it can be lifted out with ZERO behavior change. DB-mediation is the antibody: it
   converts an in-process coupling (invisible, queue-gated) into an explicit table
   dependency (a missing/stale row is observable). KEEP only what shares state that
   CANNOT be DB-mediated without losing the decision's atomicity.

**Irreducibility rule (anti-over-split).** A concern intrinsic to the order DECISION
itself — anything on the q→size→submit→manage-to-fill/exit path that needs the live
portfolio/bankroll/risk snapshot atomically — STAYS. Producing what it reads, or
following up after settlement, is a separation candidate; the decision math is not.

## 2. Current process topology (ground truth)

ALREADY separate (8 launchd services):
- `com.zeus.live-trading` → `src.main` — the order daemon (and god-host).
- `com.zeus.forecast-live` → `src.ingest.forecast_live_daemon` — weather download/materialize at publish time.
- `com.zeus.data-ingest` → `src.ingest_main` — observations, market_events scan, ETL recalibrate, source health, oracle bridge, calib auto-promote.
- `com.zeus.riskguard-live` → `src.riskguard.riskguard` — risk-level authority, DB-mediated.
- `com.zeus.venue-heartbeat` → `src.control.heartbeat_supervisor` — resting-order liveness (external mode).
- `com.zeus.heartbeat-sensor` → external watchdog over heartbeat JSON files (cron 28/58 min).
- `com.zeus.calibration-transfer-eval` → `scripts.evaluate_calibration_transfer_oos` (weekly Sun 04:00).

The problem is NOT the already-separate set — it is the ~22 jobs still inside
`src.main` (src/main.py:7610-7896), several of which are ALWAYS_ON producers or
POST_TRADE follow-up that violate criteria 1/3/5.

## 3. Verdict summary

SEPARATE_PROGRAM (lift out of src.main):
- **Executable-substrate observer** (`_market_discovery_cycle` universe sweep + the
  reactor-scoped substrate warmer `_edli_market_substrate_warm_cycle`) — the zero-trade
  regression site; ALWAYS_ON, fully DB-mediated (writes `executable_market_snapshots`),
  must outlive the reactor. These two move TOGETHER because they share the in-process
  `_market_substrate_refresh_lock` (src/main.py:73) that serializes writes to the
  snapshot table (acquired at :3680 and :5381); splitting them across processes would
  break that mutual exclusion and let two processes race-write the table.
- **User-channel + market-channel price ingest** (WS ingestor thread + channel/reconcile cycles) — ALWAYS_ON CLOB-fact producer, distinct authority, currently an in-process daemon thread.
- **Settlement→redeem→wrap capital follow-up** (harvester PnL resolver + redeem/wrap state machines + chain-sync) — POST_TRADE; must run when no trading happens; chain-sync currently holds WAL locks that starve riskguard (src/main.py:7226-7236).

KEEP_IN_ORDER_RUNTIME: the EDLI event reactor (evaluate→size→submit→bridge), bankroll
warm, boot gates, exit-lifecycle monitoring, world WAL checkpoint, venue-heartbeat
re-arm, **and the mainstream-forecast warmer** (`_edli_mainstream_warm_cycle`,
src/main.py:5421). These are intrinsic to the order decision or are the daemon's own infra.

**Why the mainstream warmer STAYS in P1 (R2 correction — was wrongly bundled into the
substrate observer).** Unlike the substrate warmer, `_edli_mainstream_warm_cycle` is
NOT DB-mediated on its output side. It writes a PROCESS-GLOBAL in-memory dict
`mainstream_forecast_source._WARM_CACHE` (src/data/mainstream_forecast_source.py:64;
writer `warm_mainstream_point` :249/:265 under `_WARM_CACHE_LOCK`). The ONLY reader of
that dict is P1's reactor, via `read_mainstream_point_cached`
(src/engine/event_reactor_adapter.py:6682/:6704, which `.get()`s the same module-global
dict). This is IN_PROCESS_SHARED state, not a DB/file table. Lifting this job to another
process would leave P1's reactor reading its own permanently-empty `_WARM_CACHE` → every
receipt carries `mainstream_*=None` forever — the daemon's own registration comment states
this explicitly: src/main.py:7713-7714 "this job MUST run for the cache to populate, else
every receipt carries mainstream_*=None". The blast radius is bounded (the value is
display-only / annotation, NEVER a q→size→submit decision input — src/main.py:7717-7718),
so it degrades a logged signal, not the order math — but it is still a real NEW
state-coupling regression the split would CREATE, which is exactly the failure class this
plan's criterion 5 exists to prevent. So it stays co-resident with its only reader. (If a
future change DB-backs `_WARM_CACHE` into a table the reactor SELECTs, the warmer becomes
liftable and can join P2 — but not before.)

ALREADY_SEPARATE: forecast production, observation ingest, market_events scan,
riskguard, venue heartbeat, calibration refit/transfer.

See the structured table (the StructuredOutput payload that accompanies this doc) for
the per-concern provenance, cadence, coupling, and principle justification.

## 4. The three lift-outs, concretely

### 4.1 Executable-Substrate Observer (NEW program, or fold into data-ingest)
Lift TWO jobs that share the snapshot write lock: the **universe sweep**
`_market_discovery_cycle` (src/main.py:3627) and the **reactor-scoped substrate warmer**
`_edli_market_substrate_warm_cycle` (:5334), plus the snapshot-refresh helper they both
call (`_refresh_pending_family_snapshots` :3020). Truth source: CLOB executable prices →
`executable_market_snapshots` on zeus_trades.db. The order runtime becomes a pure READER
of that table. Principle: 1 (ALWAYS_ON), 5 (the coupling that broke), 3 (a Gamma/CLOB
fetch error must not touch the reactor).

DO NOT lift `_edli_mainstream_warm_cycle` (:5421) here — it is NOT DB-mediated (writes a
process-global in-memory dict only P1's reactor reads; see §3). It stays in P1.

**Two producers, two coupling profiles (R2 correction — these are NOT the same shape):**
- `_market_discovery_cycle` is a REACTOR-INDEPENDENT universe sweep: it should fire on
  substrate STALENESS alone. Today it does NOT — it still carries the outer pending gates
  (`if _edli_reactor_active(): return` :3632; `if pending_count>0 and recent_discovery:
  return` :3656). The lift DELETES those gates (the producer in a separate process has no
  `pending_count` to read). This is the genuine staleness-triggered producer.
- `_edli_market_substrate_warm_cycle` is REACTOR-SCOPED: its WORKLOAD is the families of
  PENDING events. But that scoping is DB-MEDIATED, not in-process — it derives the pending
  set by SELECTing world-DB rows: `_pending_family_rows_for_refresh(world_conn,
  consumer_name='edli_reactor_v1')` reads `opportunity_event_processing` JOIN
  `opportunity_events WHERE processing_status='pending'` (src/main.py:2858-2880). So the
  warmer's SCOPE tracks the reactor's pending working set, but it reads that set from a
  queryable table, not from an in-process queue handle. That is data-coupling via DB rows
  (acceptable, observable) — NOT the in-process-queue gate that caused the zero-trade. It
  is therefore liftable: in P2 it re-derives the same pending set by reading the same world
  DB rows cross-process. (See §7 I1 for why this scoping does NOT violate the
  no-back-coupling guarantee — the warmer is never gated ON reactor `pending_count`; it is
  merely SCOPED to pending families it reads from a table.)

**Why these two move together, not apart.** Both acquire the in-process
`_market_substrate_refresh_lock` (src/main.py:73; :3680 and :5381) to serialize concurrent
writes to `executable_market_snapshots`. If `_market_discovery_cycle` went to P2 and the
warmer stayed in P1 (or vice-versa), that lock would no longer mutually-exclude them and
two processes could race-write the snapshot table. So they are ONE program (P2).

`market_events` scan is already in data-ingest (src/ingest_main.py:1204) — but that writes
a DIFFERENT table on forecasts.db; the EXECUTABLE substrate (trades.db) has no separate
producer. This is the gap P2 fills.

### 4.2 Price-Channel / CLOB-Fact Ingest (NEW program)
Lift `_start_user_channel_ingestor_if_enabled` (src/main.py:2533, a daemon THREAD in
the order process), `_edli_market_channel_ingestor_cycle` (:6954),
`_edli_user_channel_reconcile_cycle` (:6581). Truth source: Polymarket user/market
WebSocket → CLOB fills/book facts → market_events / execution_feasibility_evidence.
Principle: 1 (channel must stay subscribed while paused), 2 (distinct CLOB authority),
3 (a WS auth flap must not crash the reactor; today it is wrapped in the order
process). DB-mediated: the reactor reads the durable fill bridge + feasibility rows.

### 4.3 Post-Trade Capital Lifecycle (NEW program, or fold into data-ingest)
Lift `_harvester_cycle` (:1293), `_redeem_submitter_cycle` (:1375),
`_redeem_reconciler_cycle` (:1519), `_wrap_intent_creator/submitter/reconciler`
(:1579/:1633/:1771), and `_chain_sync_and_exit_monitor_cycle`'s chain-sync phase
(:7162). Truth source: chain settlement events + Gamma settled + on-chain
redeem/wrap receipts → settlement_commands state machine. Principle: 1 (a settled
position must be harvested/redeemed even if trading is paused for weeks), 3
(POST_TRADE follow-up must not share a lane with live decisions), and critically the
WAL-lock starvation: chain-sync holds the zeus_trades.db write lock across per-position
HTTP (src/main.py:7226-7236) and starves riskguard.tick() → DATA_DEGRADED flaps that
block all trades. Moving it to its own process removes that contention from the
trading lane. CAVEAT: the EXIT-monitoring phase of `_chain_sync_and_exit_monitor_cycle`
is order-runtime (it posts sell orders) and STAYS — split the chain-sync read-phase
from the exit-submit phase.

## 5. What explicitly STAYS (anti-over-split)
- EDLI event reactor (`_edli_event_reactor_cycle` :4849): the q→size→submit heart.
- Bankroll warm (`_edli_bankroll_warm_cycle` :5284): feeds Kelly atomically per decision.
- **Mainstream-forecast warmer** (`_edli_mainstream_warm_cycle` :5421): writes the
  process-global `_WARM_CACHE` dict that ONLY P1's reactor reads (`read_mainstream_point_cached`,
  event_reactor_adapter.py:6682/:6704). It is IN_PROCESS_SHARED, not DB-mediated, so it
  CANNOT be lifted without DB-backing the cache first. Display-only (never a decision input,
  src/main.py:7717-7718), but lifting it would silently degrade the logged mainstream signal
  to None forever (src/main.py:7713-7714) — a NEW coupling regression. Stays with its reader.
- Exit lifecycle / force-exit sweep (cycle_runner monitoring phase): posts real sell orders; intrinsic to managing a live position to exit.
- Boot gates, world WAL checkpoint (:5583), venue-heartbeat re-arm: the daemon's own infra.
- Durable fill bridge boot recovery (:6794) and settlement-redeem boot recovery (:6847): order-runtime self-heal at boot.

## 6. Target program topology

The ~22 src.main jobs do NOT become 22 daemons. They collapse to THREE new program
boundaries (each owning one truth-source × cadence × failure-domain) plus the lean
order runtime. With the 7 already-separate services that is a 10-program topology.
Concerns are merged into a program ONLY when they share truth-source AND cadence AND
failure-domain; the three POST_TRADE state-machines (harvester resolver, redeem, wrap,
chain-sync read) share all three (on-chain capital follow-up, poll cadence, "must run
while paused") so they become ONE program, not four.

Verdict-to-program assignment:

| Program | Owns (single responsibility) | Truth source | Cadence | Runs when not trading | Absorbs (concerns) | Reads | Writes |
|---|---|---|---|---|---|---|---|
| **P1 order-runtime** (`com.zeus.live-trading` → src.main, LEAN) | The order DECISION: evaluate→edge/q→Kelly size→submit→manage-to-fill/exit | consumes substrate+forecast+risk; PRODUCES venue orders | event-react ~1-min + 60s bankroll warm + 90s mainstream warm | NO (only when trading) | EDLI reactor (:4849), bankroll warm (:5284), **mainstream warm (:5421 — in-process `_WARM_CACHE`, display-only, see §5)**, exit-lifecycle/RED sweep + exit-SUBMIT phase of :7162, boot gates, world WAL checkpoint (:5583), venue-heartbeat re-arm, ARM-gate re-emit (:5493), fill-bridge/settlement boot self-heal (:6794,:6847) | `executable_market_snapshots`, durable fill bridge, `execution_feasibility_evidence`, risk_state row, calibration pin, bankroll cache, `settlements`, Open-Meteo mainstream point (warmed in-proc) | venue orders, `position_current`, trade records, `world` WAL, derived snapshot events, `_WARM_CACHE` (process-local) |
| **P2 substrate-observer** (NEW, or fold into data-ingest) | Executable-substrate truth: capture the active CLOB book into snapshots the runtime reads | Polymarket CLOB executable book | continuous warm + 5-min discovery | YES (ALWAYS_ON) | `_market_discovery_cycle` universe sweep (:3627) **with outer pending gates :3632/:3656 DELETED by the lift**, `_edli_market_substrate_warm_cycle` (:5334, pending-family scope re-derived from world DB rows), `_refresh_pending_family_snapshots` (:3020). NOT `_edli_mainstream_warm_cycle` (stays in P1). | `market_events` (forecasts.db, RO topology); `opportunity_event_processing`/`opportunity_events` pending rows (world DB, RO — for warmer scope) | `executable_market_snapshots`, `book_hash_transitions` (trades.db) |
| **P3 price-channel-ingest** (NEW) | CLOB-fact stream truth: keep the user/market WS subscribed, durably bridge fills + book facts | Polymarket user/market WebSocket | continuous WS stream + 1-min reconcile | YES (ALWAYS_ON) | `_start_user_channel_ingestor_if_enabled` thread (:2533), `_edli_market_channel_ingestor_cycle` (:6954), `_edli_user_channel_reconcile_cycle` (:6581) | WS stream, `market_events` | durable fill bridge, `execution_feasibility_evidence`, `market_events` |
| **P4 post-trade-capital** (NEW, or fold into data-ingest) | Capital follow-up after settlement: resolve P&L → redeem → wrap → reconcile chain truth | chain settlement events + on-chain redeem/wrap receipts + REST positions | 1h resolver / 2–10-min state-machine pollers / 2-min chain-sync | YES (POST_TRADE) | `_harvester_cycle` resolver (:1293), `_redeem_submitter_cycle` (:1375), `_redeem_reconciler_cycle` (:1519), `_wrap_*_cycle` (:1579/:1633/:1771), chain-sync READ phase of `_chain_sync_and_exit_monitor_cycle` (:7162) | `settlements`, `settlement_commands`, `position_current`, REST chain positions | `settlement_commands` state, `chain_state`/`chain_shares`, position settlement, REDEEM/WRAP txs |
| forecast-live (already separate) | Weather-model production | ECMWF OpenData / AIFS | publish-time cron + 5-min safe-fetch | YES | — | model APIs | `raw_model_forecast`, readiness (forecasts.db) |
| data-ingest (already separate) | Observation + market_events scan + calibration refit | WU/NOAA/HKO/Ogimet, Gamma metadata, settlement-grounded calibration pairs | hourly/daily cron + daily batch | YES | `_k2_*` ticks (incl. `_k2_daily_obs_tick` :290 → `daily_obs_append.daily_tick`), `_market_scan_tick` (:1204), `_etl_recalibrate` (:741), `_harvester_truth_writer_tick` (:796), `_calibration_auto_promote_tick` (:1382), `_ingest_status_rollup_tick` (:1240). REMOVE residual `_wu_daily_dispatch` duplicate from src.main (:1333/:7894) — VERIFIED set-equivalent to `daily_tick`'s wu_icao slice (§8 Step 4). | observation APIs, Gamma, `calibration_pairs_v2` | `observations`, `observation_instants`, `market_events`, `settlements` (writer side), calibration artifacts |
| riskguard-live (already separate) | The single risk-level authority (GREEN…RED/DATA_DEGRADED) | trade+world DB positions/equity | continuous tick | YES | whole `src.riskguard.riskguard` | trade/world position + equity rows | `risk_state` authority row (risk DB) |
| venue-heartbeat (already separate) | Resting-order liveness attestation | venue connectivity | 5s | NO (only when resting orders exist) | `src.control.heartbeat_supervisor` | venue connectivity | heartbeat attestation |
| heartbeat-sensor (already separate) | External staleness watchdog (immune-system role) | daemon-heartbeat*.json mtimes | twice-hourly cron | YES | external `heartbeat_sensor.py` | heartbeat JSON mtimes | stale-daemon alert |
| calibration-transfer-eval (already separate) | Cross-city/source transfer-skill OOS evaluation | historical calibration pairs | weekly Sun 04:00 | YES (POST_TRADE) | `scripts.evaluate_calibration_transfer_oos` | historical pairs | transfer OOS metrics |

**Co-location decision (P2/P4 into data-ingest vs own service).** P2's continuous
sub-minute substrate warm has a tighter clock than data-ingest's hourly/daily ETL
batch; criterion 4 (clock collision can starve) says co-locate ONLY if the warm's
sub-second budget cannot starve the ETL batch. Verify the substrate-warm budget
invariant (budget < interval, enforced near src/main.py:7691) holds against ingest's
batch before folding; otherwise P2 gets its own launchd service. NOTE P2 carries the
in-process `_market_substrate_refresh_lock` between its two jobs — whichever host it
lands in, BOTH `_market_discovery_cycle` and `_edli_market_substrate_warm_cycle` must
share that one process (the lock is the binding constraint, §4.1). The mainstream
warmer is NOT in this decision — it stays in P1 regardless (§3, §5). P4's pollers are
minute-scale and read-mostly — safe to fold into data-ingest. P3 stays its own
service (a persistent WS thread is a distinct lifecycle from cron ticks).

## 7. Interface contracts (producer → consumer, DB/file-mediated, NO in-process back-coupling)

Every seam below is a TABLE or FILE the producer writes and the consumer only READS.
The invariant that makes the regression class impossible: **a producer is NEVER gated
on a consumer's in-process queue, flag, or lock.** A producer's only trigger is its own
truth's staleness/cadence — never "is the consumer's backlog drained?". That exact
back-edge (substrate capture gated on the reactor's pending queue) was the zero-trade
regression.

| # | Producer | Consumer | Contract (table/file) | No-back-coupling guarantee |
|---|---|---|---|---|
| I1 | P2 substrate-observer | P1 order-runtime | `executable_market_snapshots`, `book_hash_transitions` (trades.db) — fresh book rows keyed by event family | **The guarantee differs per P2 job, and is precise about which (R2 correction).** (a) `_market_discovery_cycle` (universe sweep): AFTER the lift, its trigger is substrate STALENESS alone — the outer pending gates (:3632 `_edli_reactor_active`, :3656 `pending_count>0 and recent_discovery`) are DELETED by the split (a separate process has no `pending_count` to read), and the fairness clock `_market_discovery_last_completed_monotonic` (:3676) is producer-local. NOTE: pre-lift these gates are STILL LIVE (§0, §9) — the guarantee holds only POST-lift. (b) `_edli_market_substrate_warm_cycle`: its CADENCE is its own fixed interval (`_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS`, :7702), never the reactor's pending_count. Its WORKLOAD is scoped to pending families, but it derives that scope by SELECTing world-DB rows (`opportunity_event_processing WHERE processing_status='pending'`, :2858) — a queryable table, NOT an in-process queue handle. So it is data-coupled to reactor STATE via DB rows (observable, acceptable) but never GATED on reactor in-process state. In both cases the reactor only SELECTs snapshot rows (`_latest_snapshot_rows_for_event_family`); it cannot pause or back-pressure P2. A reactor backlog produces ZERO effect on either P2 job's firing — at most it changes WHICH families the warmer prioritizes (read from a table), never WHETHER P2 runs. |
| I2 | P3 price-channel-ingest | P1 order-runtime | durable fill bridge + `execution_feasibility_evidence` (trades.db); `market_events` (forecasts.db) | P3's trigger is the WS stream + its own 1-min reconcile clock. The reactor reads the durable fill bridge; it never signals P3. A WS auth/transport flap is contained in P3 and surfaces to P1 only as stale/absent feasibility rows (observable), not as a shared-process exception. |
| I3 | P4 post-trade-capital | riskguard / P1 | `settlement_commands` state machine, `chain_state`/`chain_shares`, `position_current` settlement (trades.db) | P4 commits chain-sync writes BEFORE any per-position HTTP (src/main.py:7233) so it never holds the trades.db WAL write lock across network calls — riskguard.tick() and P1 reads are never starved. P4's pollers are triggered by `settlement_commands` row states, never by P1's trading activity. |
| I4 | data-ingest | P4 / P1 | `settlements` (forecasts.db) — VERIFIED settlement truth (writer side) | The harvester P&L RESOLVER (P4) reads `settlements`; the WRITER is `_harvester_truth_writer_tick` (ingest, :796). Writer↔resolver split across processes is already the pattern; neither gates the other — resolver polls for new settled rows. |
| I5 | data-ingest | P1 order-runtime | calibration pin artifacts + `market_events` (forecasts.db) | P1 reads pinned calibration at boot (pin shape+staleness assert, src/main.py:249-322). data-ingest refits on its own daily clock; P1's trading state never gates a refit. |
| I6 | forecast-live | P1 / data-ingest | `raw_model_forecast` + readiness (forecasts.db) | Publish-time producer; consumers read readiness rows. The AIFS heavy fetch was explicitly removed from the trading scheduler (src/main.py:7728-7735) — the canonical precedent for this whole decomposition. |
| I7 | P2 substrate-observer | data-ingest market_events scan | `market_events` (forecasts.db) is READ by P2 for topology; WRITTEN by data-ingest (:1204) | Distinct tables on distinct DBs: P2 writes the EXECUTABLE substrate (trades.db); data-ingest writes `market_events` (forecasts.db). No write contention; P2 reads market_events read-only (ATTACH RO, src/main.py:5369). |

**Retained INTRA-process coupling (NOT a cross-program seam) — disclosed for honesty.**
The mainstream warmer → reactor link (`_edli_mainstream_warm_cycle` writes `_WARM_CACHE`;
`read_mainstream_point_cached` reads it) is IN-PROCESS-SHARED memory, not a table. It is
deliberately KEPT inside P1 (§3, §5) precisely BECAUSE it is not DB-mediated: it cannot
become a clean producer→consumer seam without first DB-backing the cache. Listing it here
keeps the §7 "every seam is a table" claim true — it is true of every CROSS-program seam
(I1–I7); the one in-process producer→consumer link that remains is co-resident by design,
so it never crosses a program boundary. This is the single concern that fails criterion 5's
DB-mediation test, and the plan's response is to NOT split it, not to pretend it is
DB-mediated.

All cross-DB writes that any program performs must still obey INV-37 (ATTACH+SAVEPOINT
via `get_forecasts_connection_with_world()` / `trade_connection_with_world_flocked()`,
never independent connections) — the program split does not relax that law; it only
moves WHICH process opens the sanctioned cross-DB transaction.

## 8. Migration order (lowest-risk-first, each step rollback-able)

Step 1 decouples the most-recently-regressing concern (substrate capture). Each step
states the regression class it makes impossible and is independently rollback-able by
reverting the one launchd/registration change — no step depends on a later step.

| Step | What | Risk | Rollback | Prevents regression class |
|---|---|---|---|---|
| 1 | **Lift P2 substrate-observer.** Move BOTH `_market_discovery_cycle` (:3627) AND `_edli_market_substrate_warm_cycle` (:5334) — and only those two (they share `_market_substrate_refresh_lock`, §4.1) — into their own scheduler (new service, or data-ingest after the budget check). **As part of this move, DELETE the outer pending gates that are STILL LIVE in `_market_discovery_cycle`: `if _edli_reactor_active(): return` (:3632) and `if pending_count>0 and recent_discovery: return` (:3656).** These are NOT already removed (only the inner capture sub-branch was, :3669); the lift is what removes them, because a separate process has no `pending_count` to read. **Do NOT move `_edli_mainstream_warm_cycle` (:5421)** — it writes the process-global `_WARM_CACHE` only P1's reactor reads (§3/§5); moving it makes every receipt carry `mainstream_*=None` forever. P1 keeps the SELECT-side snapshot reader AND the mainstream warmer. Remove the in-src.main registrations for the two moved jobs only (:7780, :7700) — leave the mainstream warm registration (:7719) in P1. | MEDIUM — the substrate is P1's hard dependency; a misconfigured new producer = no coverage. Mitigated: the writer table + writer code are unchanged, only the host process moves; pre-flight by asserting the new process can read the world-DB pending rows (warmer scope) and the trades-DB snapshot table (writer) under the INV-37 ATTACH path. | Re-enable the src.main registrations of `_market_discovery_cycle` + substrate warmer (`market_substrate_refresh_enabled`, :7779) and unload the new service. Zero schema change to revert. | STATE_COUPLING + TRADING_DEPENDENCE: the zero-trade coverage-collapse. Once the universe sweep is a separate process, NO shared in-process queue exists for any future code to gate capture on — the back-edge is unconstructable. The outer pending gate (which TODAY can still early-return the sweep on reactor state) is structurally deleted by the move. |
| 2 | **Lift P4 post-trade-capital.** Split `_chain_sync_and_exit_monitor_cycle` (:7162): chain-sync READ phase + harvester resolver + redeem + wrap move to P4; the exit-SUBMIT phase stays in P1. | MEDIUM — splitting a bundled function; must preserve the per-phase commit ordering (chain-sync committed before monitoring, :7237-7241). | Revert the function split (restore the single :7162 cycle in src.main) and unload P4. `settlement_commands` state machine is idempotent — partial progress is safe. | FAILURE_DOMAIN + WAL-lock starvation: P4's chain-sync no longer holds the trades.db write lock in P1's process, so riskguard DATA_DEGRADED flaps (which block ALL trades, INV-05) cannot originate from chain-sync HTTP. |
| 3 | **Lift P3 price-channel-ingest.** Move the user-channel WS thread (:2533) + market-channel + reconcile cycles to P3 (own service — persistent WS lifecycle). P1 reads the durable fill bridge. | MEDIUM-HIGH — the WS thread shares process auth/transport state today (latch bug history, src/main.py:2610-2622); careful handoff of the WS session. | Re-enable `_start_user_channel_ingestor_if_enabled` in src.main (:7555) and unload P3. Durable fill bridge is the persisted truth, so no fills are lost across the cutover. | FAILURE_DOMAIN: a WS auth/transport flap can no longer leave the order daemon stuck (the reduce_only-forever latch). The flap is contained in P3 and observable as stale feasibility rows. |
| 4 | **Remove residual duplicate.** Delete `_wu_daily_dispatch` registration from src.main (:7894). The two code paths DIFFER (`src.main:_wu_daily_dispatch` :1333 → `wu_scheduler.run_wu_daily_dispatch` :121 → `append_wu_city`; data-ingest `_k2_daily_obs_tick` :290 → `daily_obs_append.daily_tick` :1286 → `append_wu_city`), so "duplicate" needed proof, not assertion. **CONTAINMENT VERIFIED (R2):** for the wu_icao slice the two paths are SET-EQUIVALENT — `daily_tick` iterates `cities_by_name.values()`, filters `settlement_source_type != "wu_icao": continue` (:1316-1318), gates on `WuDailyScheduler().should_collect_now` (:1320), fetches `local_yesterday` (:1330-1331), calls `append_wu_city` (:1332); `run_wu_daily_dispatch` does the IDENTICAL iteration/filter/gate/target-date/writer (wu_scheduler.py:155-166). `daily_tick`'s wu_icao set ⊇ `run_wu_daily_dispatch`'s (in fact ==), and `daily_tick` additionally covers HKO — a strict superset of the daily-obs concern. Both writes are INSERT-OR-IGNORE-idempotent, so even the transition window is harmless. | LOW — verified set-equivalence (above), not assumed. Coverage already provided by data-ingest. | Re-add the src.main `add_job` for `_wu_daily_dispatch`. | DUPLICATE-WRITE / surface bloat: shrinks P1's job surface with VERIFIED zero coverage loss for every wu_icao city. |

## 9. Proof: the in-process-coupling regression class is now unconstructable

The zero-trade regression was constructable because TWO conditions held simultaneously
inside one process: (a) the substrate PRODUCER and the order CONSUMER shared an address
space, and (b) the producer's trigger could reference the consumer's mutable in-process
state (`pending_count` of the EDLI queue).

**Current state (R2 correction — the proof must not overstate what already landed).** The
partial antibody at src/main.py:3669-3679 removed ONE (b)-class gate: the INNER
"pending>0 → topology-only / skip snapshot-capture" sub-branch. It did NOT remove the
OUTER, function-level (b)-class gates, which are STILL LIVE in `_market_discovery_cycle`
TODAY: `if _edli_reactor_active(): return` (src/main.py:3632) and
`if pending_count>0 and recent_discovery: return` (src/main.py:3656, env-gated by
`ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING` default `'1'`). So condition (b) is NOT
yet eliminated — the universe sweep can still early-return on reactor/pending state right
now. And as long as condition (a) holds, every removed gate can be re-introduced (any new
`if reactor.something: return` line), because the consumer's state is reachable by
reference. In-process gate removal is fixing the instance, not the category — and here it
is not even a complete instance fix yet.

**The split is what completes it.** Lifting the universe sweep to its own process
(§8 Step 1) DELETES the still-live outer pending gates (:3632, :3656) as a NECESSARY
consequence — a separate address space has no `pending_count` and no reactor handle to
reference, so those lines become un-writable, not merely deleted. The implementer must
therefore treat Step 1 as "remove the outer pending gate AS PART OF the lift" and TEST the
cutover under a non-empty reactor backlog (the exact condition the outer gate reacts to),
not assume the gate is "already gone".

Separating P2/P3/P4 into their own processes removes condition (a) permanently:

1. **No shared reference exists.** P2's process cannot name P1's reactor queue — it is
   in a different address space. The only thing P2 can observe about P1 is what P1
   writes to a DB table. There is no `pending_count` to read, so `if pending: skip` is
   not expressible. The harmful line cannot be written.

2. **The producer's trigger is structurally local.** P2's universe sweep fires on
   substrate staleness (`_market_discovery_last_completed_monotonic`, a P2-local clock,
   once the outer pending gates are deleted by the lift). P2's substrate warmer fires on
   its OWN fixed interval (`_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS`); its workload is scoped
   to pending families it reads from world-DB rows (a TABLE, not the reactor's in-process
   queue), so a reactor backlog changes WHICH families it prioritizes but never WHETHER it
   fires. P3 fires on the WS stream + its own reconcile clock. P4 fires on
   `settlement_commands` row states. None of these TRIGGERS can be a function of consumer
   in-process backlog, because the consumer's in-process state is not in scope across the
   process boundary.

3. **Coupling is now an OBSERVABLE table dependency, not an invisible queue gate.** If
   P2 stops writing `executable_market_snapshots`, P1's reader sees missing/stale rows —
   a queryable, alertable condition (a row's `captured_at` ages). The old failure was
   invisible because "the reactor backlog gated capture" left no artifact; the new
   failure surface is a stale row with a timestamp. The immune-system sensor
   (heartbeat-sensor / freshness check on the snapshot table) can detect it.

4. **Failure domains are isolated bidirectionally.** A P2/P3 fetch error cannot raise
   into P1's reactor (different process); a P1 trading bug cannot blind P2's observation
   or P4's settlement follow-up. Criterion 3 holds in both directions by construction.

The category is therefore not "patched" but made **unconstructable** for every concern
that IS lifted: there is no in-process handle for a lifted producer to be gated on a
consumer's queue, because producer and consumer no longer share a process. This is the
antibody (a structural change that makes the error class impossible) rather than a
security-guard note (an alert that re-fires).

**The one in-process link the split deliberately does NOT touch — and does NOT make
worse.** `_edli_mainstream_warm_cycle` → `_WARM_CACHE` → reactor is in-process-shared
memory. The split does NOT move this job (§3/§5): it stays co-resident with its only
reader in P1. So the split introduces ZERO new state-coupling regression for it — moving
it WOULD have (every receipt `mainstream_*=None` forever), which is exactly why criterion 5
forbids the move until the cache is DB-backed. The honest statement of the post-split
topology is: every CROSS-program seam is a DB table the producer writes and the consumer
reads (I1–I7, no in-process back-edge); the single remaining in-process producer→consumer
link is INTRA-program by design, not a seam, and is the explicit DB-backing TODO before it
could ever join P2. The plan makes the regression CATEGORY unconstructable across program
boundaries AND refuses to manufacture a new instance of it by over-splitting.

The residual obligation is the one law that survives the split: every cross-DB write
still goes through the INV-37 sanctioned ATTACH path — the process boundary does not
weaken transactional integrity, it only relocates which process owns the transaction.
