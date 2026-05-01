# critic-opus review of two-system independence design

HEAD: a3ee87c914d8855be181dbccb31d315ffd5b0e5f
Reviewer: critic-opus
Date: 2026-04-30
Subject: `docs/archives/packets/task_2026-04-30_two_system_independence/{design.md, open_questions.md}` — proposal to split `com.zeus.live-trading` into `com.zeus.data-ingest` + `com.zeus.live-trading`

## Verdict

**APPROVE-WITH-CONDITIONS**. The design is structurally sound and substantively above the rubber-stamp bar — it identifies real K-decisions (5 axes, not 25 patches), it names antibody tests, and it proposes a phased migration that keeps the world DB write-coherent during dual-running. The 12-day TIGGE gap problem IS solved by lifecycle independence + asymmetric KeepAlive. However, there are 3 HARD blockers (HBL-1 premise mismatch on cross-DB pattern, HBL-2 boot-order sentinel underspecified, HBL-3 schema-version race during dual-running) and 6 soft critiques that should be addressed before Phase 1 deliverables land.

---

## ATTACK 1 — Drift detection [VERDICT: PASS]

The design DOES solve the stated problem. The 12-day TIGGE gap occurred because operator unloaded the bundled monolith while rebuilding trading; ingest jobs died with it. The proposed split makes ingest's KeepAlive=true (§4.3) independent of trading's KeepAlive=false. Operator can now `launchctl unload com.zeus.live-trading` for trading rewrites without touching ingest.

The NON-trivial part: §3.1 freshness gate ensures the second-order failure mode (ingest is alive but the data it produces is stale because an upstream is dead) also surfaces — TIGGE > 24h → DAY0 disabled. This is what `scheduler_jobs_health.json` confirmed as currently invisible: ECMWF Open Data has been failing since `2026-04-28T19:30Z` with zero impact on entry decisions. The freshness gate closes that loop.

Not just shuffled code. Real lifecycle independence + a degradation contract.

## ATTACK 2 — Omission hunt [VERDICT: FAIL]

Six material omissions:

**a)** **`control_plane.json` cross-daemon coordination is not specified.** `src/control/control_plane.py:25` reads `state/control_plane.json` each cycle. The design proposes that trading's freshness gate can be operator-overridden via this file (§3.7). But: does the new ingest daemon also read it? Operator may want to suspend a degraded source (e.g., `pause_source: ecmwf_open_data`) without restarting the ingest daemon. The design does not say.

**b)** **Log rotation strategy.** Two daemons → two log streams (`logs/zeus-ingest.{log,err}`, `logs/zeus-live.{log,err}`). Today there is no logrotate. With `KeepAlive=true` for ingest, an unbounded `.log` file is now in the failure surface (disk fill).

**c)** **Secrets duplication.** Current `com.zeus.live-trading.plist` carries `WU_API_KEY` inline (visible at `~/Library/LaunchAgents/com.zeus.live-trading.plist:15-16`). The design splits env: WU_API_KEY moves to ingest, POLYMARKET_API_KEY stays on trading. Good. But the design does not address rotation: when WU_API_KEY rotates, only the ingest plist needs reload — but operator muscle memory is "reload the trading plist." This is a foot-gun and should be a P1 doc deliverable.

**d)** **Heartbeat-sensor coverage.** `com.zeus.heartbeat-sensor.plist` exists. The design doesn't specify whether the heartbeat sensor should monitor BOTH daemons or only trading. Today it watches `daemon-heartbeat.json` written by trading at `src/main.py:338-369`. Ingest needs its own heartbeat file or the watchdog will not see ingest crashes.

**e)** **Cycle-runtime trading reads world via ATTACH-on-trade-conn.** `src/engine/cycle_runner.py:42` aliases `get_connection = get_trade_connection_with_world`. The design proposes a `world_view` accessor layer (§3.2) but does not call out that this default seam — used by 47-call-site tests and monkeypatched in fill_tracker — must also be replaced or wrapped. This is more invasive than the prose suggests.

**f)** **Backfill orchestration vs Phase 1.** §2.3 promises `python -m scripts.ingest.backfill --table forecasts --since X --until Y` but Phase 1 §5 deliverables do not include it; it lands in Phase 2. Today's 16 ad-hoc `scripts/backfill_*.py` files remain in operator hands during Phase 1's 1-2 weeks. That's the highest-load period (dual-running). Operator may invoke a legacy backfill while the new ingest daemon is also writing — increasing the probability of the data_coverage-row write-write race that Q7 anticipates but defers.

## ATTACK 3 — Rubber-stamp risk [VERDICT: PASS-WITH-CAVEAT]

The design largely cites evidence. Each axis row in the §1 table has a concrete file:line. §2/§3 improvement tables have an "Evidence" column. This is above the rubber-stamp bar.

CAVEAT: §5 Phase 1 exit gate says "7 consecutive days with ingest daemon running standalone (trading daemon stopped) AND world DB row counts +N within ±5% of monolith baseline." The "±5%" is stated without justification. Why 5% and not 1%? What's the baseline measurement window? Is it row counts in `data_coverage`, in `observations`, in `forecasts`? Without specificity, the exit gate is rubber-stamp-shaped.

§3.3 "Strategy-as-process boundary — defer to Phase 4" is also at risk: deferring with no commitment date risks becoming permanent. State an explicit revisit trigger (e.g., "if 2 strategies have divergent restart needs in 8 weeks of operation, escalate to architect").

## ATTACK 4 — Premise mismatch [VERDICT: FAIL]

Spot-checked 8 line refs, 2 mismatches:

**MISMATCH-1 (HARD):** §1 axis 2 cell "harvester cross-writes (reads `world.settlements_v2` via ATTACH at `harvester.py:1120-1124`, writes `world.settlements` at `harvester.py:1020`)."

- Lines 1120-1124 are `_first_snapshot_table()`, a string-prefix helper. It returns `world.{table}` for `ensemble_snapshots` / `ensemble_snapshots_v2`, NOT `settlements_v2`.
- More importantly, `grep -n ATTACH src/execution/harvester.py` returns ZERO matches. The harvester does NOT use ATTACH at all. It opens TWO independent connections at `harvester.py:451-452`: `trade_conn = get_trade_connection()` + `shared_conn = get_world_connection()`. Cross-DB writes happen via the trade_conn; cross-DB reads happen via shared_conn.
- The only ATTACH in `src/` is at `db.py:66-73` (`get_trade_connection_with_world`), used by `cycle_runner` (not harvester).

This misrepresents the cross-DB coupling pattern the design is supposed to fence. The `WorldSettlementWriter` contract surface (§1 axis 2) needs to wrap the actual mechanism — two separate connections — not a non-existent ATTACH.

**MISMATCH-2 (SOFT):** §4.1 plist Env: `ZEUS_MODE=live`. Per `src/config.py:48-57`, `get_mode()` always returns `"live"` and the docstring explicitly says "The environment variable is no longer authority." Setting `ZEUS_MODE=live` in the new plists is decorative — fine to keep for parity with the existing plist, but the design should not present it as load-bearing.

**Other 6 line refs verified clean:**
- `main.py:33-60` `_scheduler_job` decorator — confirmed.
- `main.py:111-233` K2 ingest jobs block — confirmed (extends to 232).
- `main.py:651-770` APScheduler job registration — confirmed.
- `db.py:66-73` `get_trade_connection_with_world` — confirmed.
- `db.py:356` `init_schema` — confirmed.
- `tests/test_ingest_isolation.py:49-63` `FORBIDDEN_IMPORT_PREFIXES` includes `src.calibration` — confirmed.
- `riskguard.py:1102-1114` standalone main loop — confirmed.
- `engine/evaluator.py:25-27`, `signal/diurnal.py:13`, `strategy/market_analysis.py:15` calibration imports — confirmed.

## ATTACK 5 — Pattern-proven self-validation [VERDICT: PASS]

The design references riskguard as precedent (§3.4, §8 line 217 "separate-daemon precedent"). Riskguard IS a real precedent: `com.zeus.riskguard-live.plist` exists, `riskguard.py:1102-1114` confirms standalone main loop. The design does NOT claim "if it worked for riskguard it'll work here" — it explicitly notes riskguard is read-only against `risk_state.db` (§4.2 row 3) and therefore needs no schema coordination. The pattern is cited as a procedural template (independent plist), not as logical proof. Acceptable.

## ATTACK 6 — Narrow scope masking / calibration third-system [VERDICT: PASS-WITH-CAVEAT]

§1.2 ("Non-obvious finding — calibration is currently a third semi-system") correctly identifies the bisection: producers (refit/rebuild scripts) are ingest-side, consumers (`platt`/`store`/`manager`/`metric_specs`) are trading-side. Antibody #10 (`tests/test_calibration_consumer_lane.py`) enforces the boundary. This is real bisection, not a hidden third system.

CAVEAT: `retrain_trigger.py` exists at `src/calibration/retrain_trigger.py` (verified — has `trigger_retrain`, `arm`, `status` functions). The design at §2.2 says "ingest computes daily Brier/log-loss on the last N=200 settlements, fires refit when delta > threshold." Where does this computation LIVE? `retrain_trigger.py` arms based on artifact patterns + corpus filters; it does NOT currently compute drift metrics on the last 200 settlements. The design under-specifies the drift detector. This needs to be a Phase 2 spec line, not a hand-wave.

## ATTACK 7 — Hidden coupling [VERDICT: FAIL]

Coupling that persists despite the split:

**a)** **`zeus-world.db` WAL mode is "proven safe" — but for what cardinality of writers?** `db.py:42` sets `journal_mode=WAL`. WAL allows N readers + 1 writer concurrently. With the old monolith there was 1 writer (the monolith). With Phase 1 dual-running there are 2 writers (monolith + ingest daemon) PLUS any operator-invoked backfill script — potentially 3+ concurrent writer connections. SQLite WAL serializes them via lock contention; the failure mode is `SQLITE_BUSY` after the 120s `timeout` set at `db.py:40`. The design says "WAL across processes is already proven safe" without citing the test that proved it. Citation needed.

**b)** **Schema migration during dual-running.** Phase 1 keeps `init_schema(conn)` called by both monolith AND new ingest daemon. If a schema bump lands in the ingest branch but the monolith's `init_schema` doesn't see it (because monolith branch is older), the second-running process re-applies its own (older) `init_schema` over the newer schema. `init_schema` is `CREATE TABLE IF NOT EXISTS` so it's mostly idempotent, but ALTER TABLE migrations are NOT idempotent in the same way. See `db.py:4871` ("init_schema ALTER must have failed. Re-run init or check DB integrity."). HBL-3 below.

**c)** **`PYTHONPATH` in plists.** Both plists set `PYTHONPATH=/Users/leofitz/.openclaw/workspace-venus/zeus`. If the project root moves (worktree, machine migration), both plists need updating. Design doesn't mention.

**d)** **`HTTPS_PROXY`.** Current plist routes through `localhost:7890`. The design's ingest plist mentions HTTPS_PROXY but doesn't specify VPN-failure failure mode. If ingest daemon's KeepAlive=true and HTTPS_PROXY is dead, ingest will respawn-loop every 30s burning CPU on retry. `bypass_dead_proxy_env_vars()` in `main.py:613` exists for trading; ingest needs the equivalent.

**e)** **Cycle_runner default seam.** `cycle_runner.py:42` aliases `get_connection = get_trade_connection_with_world` AND this is the default for monkeypatched fill_tracker tests. The §3.2 `world_view` refactor must also touch this seam, or every test that patches `deps.get_connection` with the trading-conn-with-world will silently bypass the world_view validation.

## ATTACK 8 — Failure-mode underspecification [VERDICT: FAIL]

§1 axis 3 ("Failure") promises "Ingest dies → trading reads world DB and refuses entry decisions where `data_coverage` shows source-family freshness violation." Walked through 3 scenarios:

**Scenario A: Ingest is alive but writing wrong data.** E.g., a TIGGE source switches its grid resolution silently and ingest continues to write rows tagged with old `data_version`. `data_coverage` shows fresh rows, `source_health.json` shows OK, freshness gate passes. Trading reads stale-shape data and makes garbage decisions. The design's freshness gate does not address semantic correctness. This is the London-DST 2025-03-30 case (Fitz Constraint #4) reincarnated. Antibody #9 (`test_world_writer_provenance_contract.py`) helps at write time, but does not catch upstream-shape changes after a previously-validated source mutates.

**Scenario B: Ingest is alive but slow (3-hour Open-Meteo lag).** Hourly observations rolling end-date is 3 days behind. `data_coverage` MISSING rows accumulate. The design's freshness gate (§3.1) says "hourly_obs > 6h stale → DAY0 disabled." But the trigger is not "Open-Meteo is 3h slow" — it's "no fresh data in 6h." If ingest writes a 4-hour-old row at hour T, then nothing for T+1..T+5, freshness clock starts from the last-written row (4h old, then 5h, then 10h). DAY0 disables at hour 2 of upstream silence, not at hour 6. This may be deliberate, but the design should make the clock-start semantics explicit.

**Scenario C: Trading is up but freshness gate has a bug.** Say `source_health.json` is missing because ingest crashed before first write. The design says trading reads `source_health.json` to decide. What does it do when the file is absent? The design does not say. Antibody #6 only tests the "stale" branch. **Add: when `source_health.json` is absent for > 90s after trading boot → trading exits with FATAL** (parallel to the §4.2 sentinel timeout).

## ATTACK 8b — Migration risk: Phase 1 dual-running [VERDICT: FAIL — HARD blocker HBL-3]

Phase 1 dual-running has both daemons writing world DB. The design says "WAL is proven safe."

Concrete write-write race for a single MISSING row in `data_coverage`:

1. Time T: monolith's `_k2_hourly_instants_tick` finds (city=Austin, target_date=2026-04-29, status=MISSING). Begins fetch.
2. Time T+1s: new ingest daemon's hourly_instants_tick ALSO finds (Austin, 2026-04-29, MISSING) — they share `data_coverage` row state.
3. Time T+30s: both fetches complete. Both call the appender. Both write the same Open-Meteo response to `observation_instants_v2`. INSERT OR REPLACE → second write wins. `data_coverage` row marked PROVIDED twice. Net result: 2x the API quota burned on the same row, and a transient race window where a downstream reader could observe the row in two states.

This is not catastrophic but it is wasteful and clutters quarantine reasoning. Q7 addresses operator-invoked backfill but not the dual-daemon case in Phase 1.

**HBL-3 fix:** Phase 1's "redundant safety net" mode in `src/main.py` (§5 Phase 1: "K2 jobs as a redundant safety net (cron-level @once flag → no-op if ingest sentinel exists in last 90s)") IS the right idea, but the design parenthetical hand-waves the implementation. Make this an explicit deliverable: the @once flag must be a file-lock (advisory), not just a sentinel timestamp, AND every K2 tick in `src/main.py` must consult it. Without that, the sentinel-existence check has a TOCTOU race.

## ATTACK 9 — Antibody adequacy [VERDICT: PASS-WITH-CAVEAT]

The 10 antibodies are well-targeted. Each one gates a real boundary. Spot-checks:

- **Antibody #6 (`test_data_freshness_gate.py`).** Caveat: the design says it tests "DAY0_CAPTURE returns degraded summary; OPENING_HUNT continues with `degraded_data=true`." It does NOT test the "source_health.json absent" branch (Scenario C above). REQUIRED ADDITION: a third test case with the file removed → trading boot exits FATAL after 90s.

- **Antibody #4 (`test_world_writer_boundary.py`).** "grep-based: only `src.data.*_append.py`, ..., `src.execution.harvester` (allowlisted), and `scripts/ingest/calibration/*` may write to zeus-world.db." Question: how does the test detect a "write"? grepping for `INSERT/UPDATE/DELETE` in source is fragile (text in comments, dynamic SQL). The test design needs to either parse SQL strings AST-style, or use `IngestionGuard` as the single chokepoint and assert all writes flow through it. State the detection mechanism explicitly.

- **Antibody #9 (`test_world_writer_provenance_contract.py`).** Verifies write-time provenance fields. Does NOT verify the upstream-source-shape-changed case (Scenario A). Acknowledge as a known gap; either commit to a follow-on antibody for Phase 4 or add a `data_version` epoch check that fires when an upstream begins emitting unexpected shape.

## ATTACK 10 — Operator-decision dependency / interim equilibrium [VERDICT: FAIL]

Q6 (harvester ownership) is the load-bearing decision. The design says "Phase 1: stays in trading; Phase 4: split into harvester_truth_writer (ingest) + harvester_pnl_resolver (trading)."

Question: what if the operator never reaches Phase 4? Several outcomes are possible: budget cut, priority shift, "good enough" satisficing.

In the interim equilibrium (Phase 3 done, Phase 4 deferred indefinitely), the harvester remains in trading. It is the ONLY cross-DB writer. The design wraps it in `WorldSettlementWriter` contract (§1 axis 2) — that's the antibody. **Is that interim equilibrium safe?** Yes, but only if:

a) The `WorldSettlementWriter` is feature-flagged ON (default) and contract-tested. The design says yes.
b) When trading is unloaded for rebuild, the harvester also stops — which means market settlements stop being recorded in `world.settlements` for the duration. **This is the original 12-day-gap problem reincarnated for settlements specifically.**

The design does not flag (b). If trading is down for 7 days for a rebuild, all market resolutions during that window are unresolved-in-our-DB until trading restarts and harvester replays. This may be acceptable if the harvester's catch-up logic is robust — but the design should state it explicitly. Recommend Q6 be answered with "split in Phase 1.5" if the operator cares about settlement continuity during trading restarts.

---

## HARD blockers (must-fix before Phase 1 starts)

**HBL-1 (Premise mismatch on cross-DB pattern, severity: HIGH)** — §1 axis 2 cell incorrectly cites `harvester.py:1120-1124` as the ATTACH site for `world.settlements_v2`. There is NO ATTACH in harvester. Cross-DB happens via two independent connections (`harvester.py:451-452`). The `WorldSettlementWriter` contract design must wrap the actual mechanism (two-connection write coordination) before it can be implemented.
- **Fix:** Rewrite §1 axis 2 cell + §8 line 211 to cite `harvester.py:451-452` (two-connection split) and `db.py:66-73` (the only ATTACH, used by cycle_runner). Update §1.2 calibration paragraph to reflect that trading reads world via `get_trade_connection_with_world` → ATTACH, NOT via direct `get_world_connection()`.

**HBL-2 (Boot-order sentinel race, severity: HIGH)** — §4.2 says "Trading on boot polls for the sentinel (60s timeout). If absent, trading exits with FATAL." Two problems: (a) what writes the sentinel and when? Design says "Ingest is the schema authority. It runs `init_schema(world_conn)` AND emits `state/world_schema_ready` sentinel" — but ingest's first tick may be 60s+ into its own boot. With trading polling at boot only, race: trading boots faster, polls 60s, sees no sentinel, exits, launchd restarts (KeepAlive=false → does NOT restart!), system stuck. (b) sentinel staleness: a 90-day-old sentinel from a previous run still says "schema ready."
- **Fix:** Sentinel must be (i) written synchronously after `init_schema` returns, BEFORE ingest's APScheduler.start(), AND (ii) include a freshness timestamp; trading rejects sentinels older than 24h. Also: with `KeepAlive=false` for trading, "exit FATAL" means the operator must intervene every time ingest is slow to boot. Either make trading retry-with-backoff up to 5 minutes, OR document the operator playbook explicitly.

**HBL-3 (Phase 1 dual-write race on `data_coverage`, severity: MEDIUM-HIGH)** — Both daemons writing `data_coverage` MISSING rows during dual-running can double-fetch and double-write. The §5 parenthetical "no-op if ingest sentinel exists in last 90s" is under-specified.
- **Fix:** Promote the redundancy-suppression mechanism to a first-class deliverable in Phase 1: (i) advisory file-lock at `state/locks/k2_<table>.lock` acquired before each tick, (ii) `src/main.py` K2 jobs check the lock OR the sentinel-age, whichever is more conservative, and skip if either signals "ingest is live." Add antibody: `tests/test_dual_run_lock_obeyed.py` that simulates both daemons concurrent, asserts only one writes per (city, target_date, table) per minute.

---

## Soft critique (nice-to-have, not blocking)

**SC-1** §5 Phase 1 exit gate "±5%" is ungrounded. State the measurement window (e.g., "rolling 24h count of new rows in `observation_instants_v2`") and justify the tolerance.

**SC-2** §3.3 "Defer strategy-as-process to Phase 4" needs an explicit revisit trigger to avoid permanent deferral.

**SC-3** §2.4 IngestionGuard pre-write contract should call out backward compatibility: existing rows in `observations`, `forecasts`, etc. lack `provenance_json`. Migration plan needed for legacy rows or a per-row `data_version` tag that allows mixed-vintage reads.

**SC-4** §2.5 `state/ingest_status.json` rollup: specify the writer cadence (every tick? every 5 min? every change?). Without spec the consumers may poll-busy-loop.

**SC-5** Log rotation, secrets-rotation playbook, heartbeat-sensor coverage of new ingest daemon — three operational omissions called out in ATTACK 2.

**SC-6** §2.2 drift detector for Platt refit is hand-waved. `retrain_trigger.py` exists but does not currently compute drift on last-200 settlements. Phase 2 needs an explicit deliverable: `src/calibration/drift_detector.py` with API + tests.

---

## Strongest defenses (things this design does well)

1. **Five-axis structural framing.** §1 actually identifies the K-decisions (lifecycle, state, failure, schema, test) instead of listing N-job-moves. This is the Fitz Constraint #1 application done correctly.

2. **Asymmetric KeepAlive policy with stated rationale (§4.3).** Most designs would default both daemons to KeepAlive=true. The design correctly identifies that auto-restarting trading with open Kelly positions is dangerous and proposes manual recovery. Q1 surfaces this trade-off honestly to the operator.

3. **Calibration bisection (§1.2).** The producer/consumer split is the kind of non-obvious finding that comes from actually reading the imports. `test_ingest_isolation.py:62` already forbids `src.calibration` from ingest scripts; the design extends this to the symmetric case (trading must not import `refit_*`/`rebuild_*`/`retrain_trigger.fire_refit`). Antibody #10 codifies this as a permanent constraint.

4. **Antibody-list explicit table (§6).** 10 named tests with phase + purpose. This is what "antibody" means in Fitz methodology — not alerts, not docs, but failing-tests that make a category of regression unconstructable.

5. **Honesty about open questions.** 7 questions surfaced as operator decisions, not buried. Q6 (harvester) is correctly identified as the load-bearing one. Decision-block-map at the bottom of `open_questions.md` makes phase blocking explicit.

---

## Final recommendation to operator

**APPROVE-WITH-CONDITIONS.** Direct the architect to address the 3 HARD blockers (HBL-1 premise mismatch, HBL-2 boot sentinel race, HBL-3 Phase 1 dual-write coordination) and answer Scenario C (`source_health.json` missing → trading behavior) before kicking off Phase 1 deliverables. The 6 soft critiques can be folded into Phase 1/2 as the work progresses — they do not block.

Approve operator decisions Q1, Q2, Q6 in parallel with HBL fixes. Recommend:
- **Q1:** Manual recovery (matches design recommendation; revisit at Phase 3).
- **Q2:** Ingest-owned calibration (matches; defer third-lane to Phase 4).
- **Q6:** Reconsider Phase 1.5 split if harvester downtime during trading rebuilds is unacceptable. The current design (harvester stays in trading) means market settlements stop being recorded whenever trading is unloaded — same shape as the original 12-day TIGGE gap.

This design is well above the rubber-stamp bar. The K-decisions are real, the antibodies are concrete, the migration is staged. Three bugs in the citations + one race-condition under-specification + six operational omissions are the cost of admission to making this safe to implement. None are reasons to reject the direction.
