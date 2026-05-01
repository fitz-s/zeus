# Two-System Independence Design — Zeus Ingest vs Live Trading (REVISED v2)

**Status:** Strategic design draft v2 (architect, read-only) — revised after critic-opus APPROVE-WITH-CONDITIONS review.
**Drafted:** 2026-04-30 (v1) → revised 2026-04-30 (v2).
**Authority basis:** Operator directive 2026-04-30 — "把两个系统做的更好更独立" (make the two systems BETTER and MORE INDEPENDENT, not simple separation).
**Trigger event:** 2026-04-29 calibration push observed a 12-day TIGGE gap and 7-day observation gap accumulating during the period when `com.zeus.live-trading` was unloaded for the rebuild.
**Companion docs:** [`open_questions.md`](open_questions.md) (Q1/Q2/Q6 RESOLVED in v2), [`critic_review.md`](critic_review.md).

---

## 0. Summary

Zeus today is a single launchd daemon (`com.zeus.live-trading`, `src/main.py`) running an APScheduler that bundles 11 jobs across two unrelated lifecycles. When trading is unloaded for a rebuild, ingest dies with it — the 12-day TIGGE gap during the 2026-04-29 calibration push proves it. The remediation is **not** a pure split; it is the construction of two daemons whose contracts are sharper than the bundled monolith's were, with explicit independence axes and antibody tests that prevent re-coupling.

This v2 specifies seven things:
1. The independence axes and their target contracts (§1)
2. Ingest improvements (§2)
3. Trading improvements, including the cycle_runner.py:42 seam migration (§3)
4. Operational specifics — plists, boot order with sentinel freshness + retry, signals, cross-daemon coordination, log/secrets/heartbeat ops (§4)
5. A 4-phase migration path with explicit Phase 1.5 harvester split (§5)
6. The antibody test surface that locks the split in (§6)
7. Revision log mapping changes to critic finding IDs (§9)

The remaining four operator decisions (Q3, Q4, Q5, Q7) are flagged in `open_questions.md`; Q1/Q2/Q6 were resolved in this revision per critic recommendations.

---

## 1. Independence Axes — Current vs Target

Per Fitz Constraint #1 (structural decisions > patches): not "how do we split jobs across two plists" but "which K structural decisions express the entire split." Five axes.

| Axis | Current state | Target contract |
|---|---|---|
| **Lifecycle** | Ingest jobs run inside `src/main.py:651-770` APScheduler; restarting trading kills ingest. | Ingest is `com.zeus.data-ingest`, trading is `com.zeus.live-trading`. Each has independent KeepAlive + ThrottleInterval. Either can be unloaded without affecting the other. Ingest never imports from `src.engine|execution|strategy|signal|control|main`; trading never imports from `scripts/ingest/*`. |
| **State** | `zeus-world.db` and `zeus_trades.db` are physically separate. Harvester is the only cross-DB writer: it opens TWO independent connections — `trade_conn = get_trade_connection()` at `harvester.py:451` and `shared_conn = get_world_connection()` at `harvester.py:452` — and writes settlements via `_write_settlement_truth(shared_conn, ...)` at `harvester.py:556` (the actual INSERT INTO settlements lives at `harvester.py:1018-1020`), while `store_settlement_records(trade_conn, ...)` at `harvester.py:633` writes `decision_log`. The cycle_runner read path (separate from harvester) does ATTACH at `db.py:66-73` (`get_trade_connection_with_world`) for joins. **No ATTACH exists in harvester.** | Ingest **owns** all writes to `zeus-world.db`. Cross-DB writes coalesce to one audited surface: `src.contracts.world_writer.WorldSettlementWriter`, which wraps the **two-connection mechanism** (NOT ATTACH). The wrapper takes a `world_conn` (must be a `get_world_connection()` instance) and a `trade_conn`, owns the write-order rule (world.settlements first, then trade.decision_log, both committed atomically per record), is feature-flagged, and is contract-tested. Trading reads `zeus-world.db` either via the cycle_runner ATTACH seam (now wrapped by `world_view`, see §3.2) or — in Phase 1.5 onward — via separate `get_world_connection()` opened by `harvester_pnl_resolver`. |
| **Failure** | Ingest exception → `_scheduler_job` decorator (`main.py:33-60`) writes `scheduler_jobs_health.json` and swallows. Trading exception → same. No degradation contract; trading runs whether ingest is fresh or 12 days stale. | Ingest dies → trading reads world DB and refuses entry decisions where `data_coverage` shows source-family freshness violation (`hourly_observations` last row > 6h old → DAY0 disabled; TIGGE > 24h old → forecast-staleness gate). Trading dies → ingest keeps writing. Riskguard is the third independent surface and continues regardless. **`source_health.json`-absent branch is a first-class failure mode (see §3.1).** |
| **Schema** | Trading reads world tables by name (`forecasts`, `observations`, `observation_instants_v2`, `settlements`, `model_bias`, `forecast_skill`). Schema changes flow via `init_schema(conn)` called by both processes (`main.py:619-625`). | Ingest publishes `architecture/world_schema_manifest.yaml` (table → column → semantic). Trading reads only via `src.contracts.world_view.*` typed accessors that validate the manifest at boot. Mismatches → trading boot fails closed (parallel to G6 strategy guard at `main.py:573-599`). |
| **Test** | `tests/test_ingest_isolation.py` enforces `scripts/ingest/* ⇏ src.engine|execution|strategy|signal|...` (one-way). | Add reverse antibody: `tests/test_trading_isolation.py` enforces `src.engine|execution|strategy|signal ⇏ scripts/ingest|src.calibration.refit_*`. Plus `tests/test_world_writer_boundary.py` (only allowlisted writers may write `zeus-world.db`). Plus `tests/test_dual_run_lock_obeyed.py` (Phase 1 dual-write race antibody, see §5). |

**Non-obvious finding — calibration is currently a third semi-system bundled into trading's import graph.** `src.calibration` is forbidden for ingest tick scripts (`test_ingest_isolation.py:62`) but heavily imported by trading (`engine/evaluator.py:25-27`, `strategy/market_analysis.py:15`, `signal/diurnal.py:13`). The refit and pair-rebuild scripts that **produce** Platt models are ingest-side artifacts; the runtime that **consumes** them is trading-side:
- **Read API (trading-consumed):** `src.calibration.{store, platt, manager, metric_specs}`
- **Producers (ingest-owned):** `scripts/refit_platt_v2.py`, `rebuild_calibration_pairs_canonical.py`, `etl_tigge_direct_calibration.py` → moved to `scripts/ingest/calibration/` in Phase 3 (Q2 RESOLVED).

---

## 2. Ingest System — How It Becomes BETTER Standalone

| # | Improvement | Priority | Evidence |
|---|---|---|---|
| 1 | **Source-health probe loop (10-min cadence).** Each upstream gets a 1-row-fetch + latency probe. Result lands in `state/source_health.json` with `last_success_at`, `consecutive_failures`, `degraded_since`. Schema MUST include a top-level `written_at` ISO-8601 timestamp so consumers can detect stuck files. Trading reads this to gate decisions. | P0 | `scheduler_jobs_health.json` shows `ecmwf_open_data` failing since `2026-04-28T19:30Z` with no impact on trading. |
| 2 | **Drift-triggered Platt refit, not weekly cron.** Today `_etl_recalibrate` (`main.py:244-301`) runs at UTC 06:00 daily but explicitly skips Platt refit. Replace with a `src/calibration/drift_detector.py` (NEW, Phase 2 deliverable) that computes daily Brier/log-loss on the last N=200 settlements, exposes a public API (`compute_drift(city, season, metric_identity) -> DriftReport`), and feeds `retrain_trigger.arm(...)` (`retrain_trigger.py:193`) when delta > threshold OR new pairs > 50. | P0 | `retrain_trigger.py` exists (`status` line 177, `arm` line 193, `trigger_retrain` line 395) but does NOT compute drift on settlements — needs the new detector. |
| 3 | **Backfill orchestration command.** Promote ad-hoc `scripts/backfill_*.py` to `python -m scripts.ingest.backfill --table forecasts --since X --until Y --dry-run` with idempotent re-runs (uses `data_coverage` ledger). **MOVED to Phase 1 deliverable** (was Phase 2) so the dual-run window does not leave operator with the legacy 16 ad-hoc scripts during the highest-load period. | P0 | `scripts/backfill_*.py` count: ~16 files (per critic ATTACK 2f). |
| 4 | **Pre-write data-quality gates (provenance hash, source-tag, contract).** `src/data/ingestion_guard.py` exists. Move ALL writes to the world DB through it: every insert carries `source` (e.g., `wu_icao`, `ecmwf_open_data`), `authority` (`VERIFIED|UNVERIFIED|QUARANTINED`), `data_version`, `provenance_json`. Backward-compat: legacy rows lack these fields; readers tolerate NULL by treating absent provenance as `data_version='legacy_v0', authority='UNVERIFIED'` (see `test_world_writer_provenance_contract.py` Phase 2 spec). | P1 | Fitz Constraint #4: data provenance > code correctness. |
| 5 | **Ingest observability JSON: `state/ingest_status.json`.** Per-table: rows-last-hour, rows-last-day, holes-by-city-by-day-count, last-quarantine-reason, source-health-rollup. **Writer cadence: every K2 tick completion + every 5 minutes from a dedicated rollup tick (whichever fires first).** Reader contract: poll at most every 30s. | P1 | `data_coverage` is row-level; no rollup view exists. |
| 6 | **Scheduled hole-fill with ratchet.** Today `_k2_hole_scanner_tick` (`main.py:185-205`) runs once daily at UTC 04:00. Tighten: scanner runs every 6h; appenders explicitly `catch_up_missing(conn, days_back=7)` every tick. | P2 | `main.py:207-233` only catches up at boot. |
| 7 | **Schema-upgrade orchestration with atomic migrate-or-fail.** Both daemons call `init_schema(conn)` at boot (`main.py:619-625`). When schemas evolve mid-run, only the next-restarting process picks it up. Solution: `architecture/world_schema_version.yaml` integer; ingest bumps on migration; trading reads at boot AND once per cycle, fail-closed on mismatch. **During Phase 1 dual-run, only ingest may bump the version**; the monolith reads-and-rejects-mismatch instead of bumping. | P2 | `init_schema` at `db.py:356` is idempotent (CREATE IF NOT EXISTS) but ALTER TABLE migrations are NOT (see error string at `db.py:4871`). |
| 8 | **Quarantine routing.** Per-source quarantine file at `state/quarantine/<source>/<date>.jsonl` (Q4 default; revisitable). Drop sources with > N quarantines/hour. | P2 | Current behaviour is silent log. |
| 9 | **Per-source separation of cron lines.** Today `_k2_daily_obs_tick` is one job covering WU + HKO + Ogimet (`main.py:111-129`). One source's failure cascades. Fan out: `wu_daily`, `hko_monthly`, `ogimet_daily` each own their cadence. | P2 | `scheduler_jobs_health.json` shows WU failure cascading to whole `k2_daily_obs` job. |
| 10 | **Calibration produced-by ingest.** Move `scripts/refit_platt_v2.py`, `rebuild_calibration_pairs_canonical.py`, `etl_tigge_direct_calibration.py` into `scripts/ingest/calibration/` in Phase 3 (Q2 RESOLVED). Trading still imports `src.calibration.platt|store|manager` for read-only consumption. | P3 | Today refit lives outside the K2 scaffold but is conceptually ingest. |

---

## 3. Live Trading System — How It Becomes BETTER Standalone

### 3.0 Improvements table

| # | Improvement | Priority | Evidence |
|---|---|---|---|
| 1 | **World-data freshness gate (with absent-file branch).** See §3.1 for the three-branch decision tree. | P0 | Operator directive cites the 12-day gap explicitly. |
| 2 | **Read-only world-data accessor layer + cycle_runner seam migration.** See §3.2. | P0 | `cycle_runner.py:42` is the deepest coupling. |
| 3 | **Strategy-as-process boundary — DEFERRED to Phase 4 with explicit revisit trigger:** "If, within 8 weeks of Phase 3 completion, two enabled strategies in `KNOWN_STRATEGIES` (`main.py:594-599`) have divergent restart-policy needs (one safe to auto-restart, one not), escalate to architect for process split." Without this trigger SC-2 deferral risks becoming permanent. | P3 | Avoiding scope creep. |
| 4 | **Riskguard remains the third independent system.** Already runs as `com.zeus.riskguard-live`. Harden read path to use `world_view` accessor too. | P1 | `riskguard.plist` confirmed. |
| 5 | **Replay separation: replay reads, never writes live tables.** Move `src/engine/replay.py` invocation out of `_etl_recalibrate` (`main.py:291-299`) into `scripts/audit/replay.py` with `?mode=ro` against `zeus-world.db`. | P1 | `db.py:57-63` already has `get_backtest_connection`. |
| 6 | **Trade audit trail consolidation.** Materialized view `v_pnl_attribution` in `zeus_trades.db`. | P2 | `db.py:66-73` supports `get_trade_connection_with_world()`. |
| 7 | **Wallet/venue gates separated from data gates.** Wallet failure → fail-closed (no override). Data freshness failure → degrade (operator override via `state/control_plane.json`). | P1 | `main.py:486-488` exits hard on wallet failure. |
| 8 | **Position-lifecycle invariants on trading-side only.** `state/lifecycle_manager.py` trading-owned; antibody #4 enforces. | P2 | Ingest's K2 ticks only touch `observations`, `observation_instants_v2`, `solar_daily`, `forecasts`, `data_coverage`. |

### 3.1 Freshness gate — three-branch decision tree (§3.1 expansion per critic Scenario C)

Trading boot and per-cycle reads `state/source_health.json`. Three branches, all explicit:

| Branch | Trigger | Trading behavior |
|---|---|---|
| **FRESH** | File present, all critical sources `last_success_at` within freshness budget (TIGGE: 24h, hourly_obs: 6h, daily_obs: 36h, ecmwf_open_data: 24h). | Normal operation. |
| **STALE** | File present, ≥1 critical source breaches its budget. | Per-source degradation: TIGGE stale → forecast-staleness gate fires; hourly_obs stale → DAY0_CAPTURE disabled, OPENING_HUNT continues with `degraded_data=true` in decision_log; ecmwf_open_data stale → ensemble-only nowcasts disabled. Operator may override via `state/control_plane.json` (`force_ignore_freshness: ["ecmwf_open_data"]`). |
| **ABSENT** | File missing OR `written_at` older than 90s after trading boot. | **At boot**: trading retries-with-backoff for up to 5 minutes (poll every 10s; same retry loop as the §4.2 sentinel) then exits FATAL with operator-actionable message: `"source_health.json absent — is data-ingest daemon running? Check launchctl list com.zeus.data-ingest"`. **Mid-run**: if file disappears or `written_at` ages past 5 minutes, trading enters degraded mode (treat as STALE for all sources) and emits a `source_health_absent` alert; does NOT exit. |

Antibody #6 (`tests/test_data_freshness_gate.py`) tests all three branches.

### 3.2 World-view accessor and the cycle_runner.py:42 seam migration (§3.2 — addresses HBL-1, ATTACK 7e)

The naive design "trading uses `world_view` instead of raw SQL" understates the deepest coupling: `src/engine/cycle_runner.py:42` defines `get_connection = get_trade_connection_with_world` (alias). This module-level alias is:

1. The default seam for `src/execution/fill_tracker.py:142, 171, 211, 267` (`deps.get_connection()` called 4× on the trade-with-world ATTACH conn).
2. The monkeypatched seam for fill_tracker tests AND for ~10+ riskguard tests at `tests/test_riskguard.py:272, 378, 441, 466, 482, 511, 539, 571, 644, 1108, ...` — they patch `riskguard_module.get_connection` with a fake connection.
3. Used transitively wherever cycle_runner-injected `deps` flows.

The `world_view` migration must replace this seam itself, not just the raw SQL sites. Concrete plan:

- **Phase 2 deliverable A**: introduce `src.contracts.world_view` module exporting typed read functions: `get_latest_observation(world_conn, city, target_date)`, `get_settlement_truth(world_conn, city, target_date)`, `get_active_platt_model(world_conn, city, season, metric_identity)`. Functions take an explicit `world_conn` argument (no module-level singletons).
- **Phase 2 deliverable B**: `cycle_runner.py:42` becomes a *factory* (not an alias): `def get_connection() -> ConnectionPair: return ConnectionPair(trade=get_trade_connection(), world=get_world_connection())`. The two-connection pair object exposes `.trade` and `.world` attributes (NO ATTACH; matches the harvester two-connection pattern).
- **Phase 2 deliverable C**: migrate `fill_tracker.py` callsites from `deps.get_connection()` (single conn assumed to be trade+world ATTACH) to `deps.get_connection().trade` + explicit `world_view` calls for world reads.
- **Phase 2 deliverable D**: update riskguard test monkeypatches to return a `ConnectionPair` (or a fake with `.trade` + `.world`). Provide a `tests/conftest.py` helper (`fake_connection_pair()`) so the diff per test is a single-line constructor change.
- **Antibody (Phase 2)**: `tests/test_no_raw_world_attach.py` — grep-based assertion that `src.engine|src.strategy|src.signal` does not contain `get_trade_connection_with_world` or `ATTACH DATABASE` outside the `world_view` module and the legacy harvester (allowlisted until Phase 1.5 split).
- **Migration order**: run the seam migration BEFORE removing `get_trade_connection_with_world` from `db.py:66-73`. The legacy ATTACH path remains available for one cycle of overlap so the riskguard test suite can be migrated incrementally, then `get_trade_connection_with_world` is deleted in Phase 3 (antibody #8 enforces).

This is intentionally a four-step migration because the seam touches 4+ source files and ~10+ test files. Trying to flip it in one PR will produce a regression cascade.

---

## 4. Operational Design

### 4.1 Two new plists (sketch)

`com.zeus.data-ingest.plist`:
- ProgramArguments: `[.venv/bin/python, -m, src.ingest_main]`
- KeepAlive: **true** (data continuity priority)
- RunAtLoad: true
- ThrottleInterval: 30
- StandardOutPath: `logs/zeus-ingest.log`, StandardErrorPath: `logs/zeus-ingest.err`
- Env: `WU_API_KEY`, `ECMWF_OPEN_DATA_BASE`, `PYTHONPATH`, `HTTPS_PROXY`. (`ZEUS_MODE` omitted — `src/config.py:48-57` confirms it is decorative; per critic MISMATCH-2.)

`com.zeus.live-trading.plist` (revised, smaller):
- ProgramArguments: `[.venv/bin/python, -m, src.main]`
- KeepAlive: **false** (asymmetric — see §4.3)
- RunAtLoad: true
- ThrottleInterval: 60
- StandardOutPath: `logs/zeus-live.log`, StandardErrorPath: `logs/zeus-live.err`
- Env: `POLYMARKET_API_KEY`, `ZEUS_USER_CHANNEL_WS_ENABLED`, `PYTHONPATH`, `HTTPS_PROXY`.

`com.zeus.riskguard-live.plist`: unchanged.

`com.zeus.heartbeat-sensor.plist`: extended to monitor BOTH heartbeat files (see §4.5).

### 4.2 Boot order with sentinel freshness + retry-with-backoff (§4.2 rewrite per HBL-2)

The sentinel handshake replaces "naive 60s poll → FATAL exit," which collides with `KeepAlive=false` (no auto-restart, system stuck).

**Sentinel file shape:** `state/world_schema_ready.json`
```json
{
  "schema_version": 27,
  "written_at": "2026-04-30T18:42:11Z",
  "ingest_pid": 4471,
  "init_schema_returned_ok": true
}
```

**Ingest write contract:** sentinel is written **synchronously** by `src/ingest_main.py` AFTER `init_schema(world_conn)` returns and BEFORE `scheduler.start()`. Use the atomic write pattern (`tmp` + `os.replace`) — see existing convention in `src/main.py:_write_heartbeat` at line 338.

**Trading read contract (boot):**

1. Read `state/world_schema_ready.json`. If absent → enter retry loop.
2. If present: parse `written_at`. If older than 24h → reject (stale sentinel from a previous run); enter retry loop.
3. If present and fresh: parse `schema_version`. If does not match `architecture/world_schema_manifest.yaml` expected version → FATAL exit with explicit version mismatch message.
4. **Retry loop**: poll every 10s for up to 5 minutes (30 attempts). Log each retry at INFO. On loop exhaustion → FATAL exit with operator-actionable message: `"world_schema_ready.json absent or stale after 5 min — is com.zeus.data-ingest running? Run: launchctl list com.zeus.data-ingest"`.

**Trading mid-run**: sentinel is read once at boot only (it certifies init_schema succeeded; per-cycle correctness is enforced by §3.1 freshness gate + §1 row 4 schema-version compatibility check on each cycle).

**Operator playbook for the FATAL case (`docs/runbooks/freshness_gate_fatal.md` — Phase 2 doc deliverable):**
1. `launchctl list com.zeus.data-ingest` — is it running?
2. If not: `launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist`. Wait 60s.
3. Check `tail logs/zeus-ingest.err`. If `init_schema` exception → resolve manually; the ingest plist `KeepAlive=true` will respawn.
4. Once ingest is healthy, trading: `launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist`.

LaunchAgent ordering on macOS is best-effort — there is no native `Requires:`. The sentinel + 5-minute retry is the explicit handshake.

### 4.3 Signal handling — asymmetric restart policy (Q1 RESOLVED — manual recovery for trading)

| Daemon | KeepAlive | Rationale |
|---|---|---|
| **data-ingest** | `true` | Restart on crash within 30s. Acceptable because rerunning a tick is idempotent. |
| **live-trading** | **`false`** | Crashes → operator inspection required. Auto-restart with open Kelly positions risks phantom orders or skipped exits. External watchdog reads `state/auto_restart_allowed.flag` (operator manually flips after positions close). Q1 RESOLVED. |
| **riskguard** | `true` | Monitor-only; safe to flap-restart. |

### 4.4 SIGTERM handling

Add `signal.signal(signal.SIGTERM, _graceful_shutdown)` handlers in both `src/ingest_main.py` (new) and `src/main.py` (revised). APScheduler's `BlockingScheduler.shutdown()` waits for in-flight jobs by default.

### 4.5 Cross-daemon coordination, log/secrets/heartbeat ops (NEW per critic ATTACK 2 a/b/c/d)

**(a) `state/control_plane.json` cross-daemon coordination.** Today only trading reads it (`src/control/control_plane.py:25`). The new ingest daemon ALSO reads it on each tick to honor `pause_source: <source_name>` directives — operator can suspend a degraded source without restarting either daemon. Reader contract: same JSON path, same `_apply_command` semantics, but ingest only acts on `pause_source` / `resume_source` / `pause_ingest` keys; trading's existing keys remain trading-only. Antibody (Phase 2): `tests/test_control_plane_dual_consumer.py` — both daemons honor `pause_source: ecmwf_open_data` within one tick.

**(b) Log rotation strategy.** macOS `newsyslog` is system-default; we add a project file at `/etc/newsyslog.d/zeus.conf` (or operator-specific equivalent in `~/Library/LaunchAgents/com.zeus.log-rotate.plist` if root install is impractical). Rotate 4 logs daily, keep 14 days, gzip after rotate: `logs/zeus-ingest.log`, `logs/zeus-ingest.err`, `logs/zeus-live.log`, `logs/zeus-live.err`. Phase 2 ops deliverable. Without rotation, `KeepAlive=true` ingest fills disk eventually.

**(c) Secrets-rotation playbook (`docs/runbooks/secrets_rotation.md` — Phase 2 doc deliverable).** When `WU_API_KEY` rotates: the key now lives in `com.zeus.data-ingest.plist` ONLY (NOT in trading). Operator action: `launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist; vim ~/Library/LaunchAgents/com.zeus.data-ingest.plist; launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist`. Trading is NOT touched. The runbook explicitly warns against muscle-memory "reload the trading plist" (which would no-op for WU rotation). Same pattern for `ECMWF_OPEN_DATA_BASE`. `POLYMARKET_API_KEY` rotation touches trading only.

**(d) Heartbeat-sensor coverage of new ingest daemon.** Today `bin/heartbeat_sensor.py` (run by `com.zeus.heartbeat-sensor.plist`) monitors `state/daemon-heartbeat.json` written by trading at `main.py:338-369`. Extension: ingest daemon writes its own `state/daemon-heartbeat-ingest.json` on a 60s scheduler tick (mirror of `main.py:_write_heartbeat`). Heartbeat sensor is extended to monitor BOTH files, alerting on either silence > 5 minutes. Without this, the 12-day-gap problem recurs in a different shape: ingest could silently die and the watchdog would never alert. Phase 2 deliverable. Antibody: `tests/test_heartbeat_sensor_dual_coverage.py` — sensor alerts when EITHER heartbeat is stale.

**(e) `PYTHONPATH` and `HTTPS_PROXY` parity.** Both plists set `PYTHONPATH=/Users/leofitz/.openclaw/workspace-venus/zeus`. Worktree/machine migrations must update both — documented in §4.5 ops note. `HTTPS_PROXY=http://localhost:7890` ingest plist must call `bypass_dead_proxy_env_vars()` (extracted from `main.py:613` into `src.data.proxy_health`) at boot, otherwise dead-proxy + `KeepAlive=true` = respawn-loop burning CPU.

---

## 5. Migration Phases (NEW Phase 1.5 per Q6 RESOLVED + HBL-3)

### Phase 1: Dual-running (additive, weeks 1-2)

**Goal:** ingest daemon runs in parallel with current monolith; both write the same world DB without conflict.

**Deliverables:**
- `src/ingest_main.py` — new entry point. Lifts the K2 jobs (`_k2_*` in `main.py:111-233`), `_etl_recalibrate`, `_ecmwf_open_data_cycle`, `_automation_analysis_cycle` from `src/main.py` into a new APScheduler. Re-uses `scripts/ingest/*_tick.py` bodies and `_scheduler_job` decorator.
- `com.zeus.data-ingest.plist` installed and loaded.
- `src/main.py` keeps the same K2 jobs as a redundant safety net **gated by an advisory file lock**, NOT just a sentinel timestamp (HBL-3 fix). Mechanism:
  - Before each `_k2_*_tick` in `src/main.py`, attempt to acquire `state/locks/k2_<table>.lock` with `fcntl.flock(LOCK_EX | LOCK_NB)`. If lock is held by ingest daemon → tick no-ops. If acquired → tick runs and releases on completion.
  - New `src/ingest_main.py` acquires the same lock at the start of every tick and holds for tick duration. On crash, OS releases the lock.
  - Path convention: `state/locks/k2_<table>.lock` per table — `daily_obs`, `hourly_instants`, `solar_daily`, `forecasts_daily`, `hole_scanner`, `etl_recalibrate`. Six locks total.
- Antibody #1: `tests/test_trading_isolation.py` (reverse direction).
- Antibody #2: `tests/test_world_writer_boundary.py`.
- **Antibody #11 (NEW for HBL-3): `tests/test_dual_run_lock_obeyed.py`** — simulates two processes contending on `state/locks/k2_hourly_instants.lock`; asserts only one writes per (city, target_date) per minute and the other returns a `skipped_lock_held` status. Uses `multiprocessing` + a fake appender.
- **§2.3 backfill orchestration moved to Phase 1** so dual-run period does not strand operator with 16 ad-hoc scripts.
- Slice receipt under `phase1/`.

**Exit gate:** 7 consecutive days with ingest daemon running standalone (trading daemon stopped) AND world DB row counts match monolith baseline within ±1% (tightened from ±5% per critic SC-1) on a rolling 24h window for `observation_instants_v2`, `forecasts`, `solar_daily`, `data_coverage`. The baseline measurement window is "last 7 days of monolith-only operation immediately preceding Phase 1 cutover."

### Phase 1.5: Harvester split (weeks 3-4) — Q6 RESOLVED, NEW

**Goal:** prevent the original 12-day-gap shape from reincarnating for `world.settlements` whenever trading is unloaded for rebuilds. Critic ATTACK 10 made the case: if Phase 4 is deferred indefinitely, the harvester remains on trading's lifecycle and settlements stop being recorded during trading downtime.

**Deliverables:**
- New module `src/ingest/harvester_truth_writer.py`:
  - Owns world.settlements writes. Wraps the existing `_write_settlement_truth` body (`harvester.py:911-1090`). Takes a single `world_conn = get_world_connection()`. Feature-flag preserved: `ZEUS_HARVESTER_LIVE_ENABLED`.
  - New scheduler entry in `src/ingest_main.py`: `harvester_truth_writer_tick` runs every hour.
- `src/execution/harvester_pnl_resolver.py` — what remains of the legacy harvester:
  - Reads `world.settlements` via `get_world_connection()` (read-only) AND via `world_view.get_settlement_truth(world_conn, city, target_date)`.
  - Writes `trade.decision_log` via `store_settlement_records(trade_conn, ...)` and settles positions via `_settle_positions(trade_conn, ...)`.
  - Stays on trading's `src/main.py` scheduler at hourly cadence (preserves `harvester.py:104-108` invocation point).
- `src.contracts.world_writer.WorldSettlementWriter` — the audited wrapper class. Accepts a single `world_conn`. Internally delegates to `_write_settlement_truth`. Phase-1.5 only the `harvester_truth_writer_tick` instantiates it; Phase-3 antibody #4 enforces "only allowlisted writers."
- Antibody #4 updated: `harvester` allowlist replaced by `src.ingest.harvester_truth_writer`.
- Antibody #12 (NEW): `tests/test_harvester_split_independence.py` — when trading is stopped, `harvester_truth_writer_tick` continues to write `world.settlements`; when ingest is stopped, `harvester_pnl_resolver` returns `awaiting_truth_writer` status without writing trade.decision_log.

**Exit gate:** 7 days with trading unloaded (simulated rebuild) and `world.settlements` continuing to receive new rows from ingest-side harvester.

### Phase 2: Sharpen each (weeks 5-8)

**Goal:** implement P0+P1 items from §2 and §3.

**Deliverables:**
- §2.1 source health probe loop + `state/source_health.json` (with `written_at` field)
- §2.2 drift-triggered Platt refit wired via NEW `src/calibration/drift_detector.py`
- §2.4 pre-write contract enforcement via `IngestionGuard`
- §2.5 `state/ingest_status.json` rollup (cadence specified)
- §3.1 trading freshness gate (three-branch decision tree — FRESH / STALE / ABSENT)
- §3.2 `src/contracts/world_view/` accessor layer + cycle_runner.py:42 seam migration in 4 deliverables (A-D)
- §3.5 replay moved to `scripts/audit/`
- §3.7 wallet vs data-gate split
- §4.5 cross-daemon control_plane, log rotation conf, secrets rotation runbook, heartbeat-sensor extension
- Schema manifest: `architecture/world_schema_manifest.yaml` + boot validator

**Exit gate:** trading boots in degraded mode when ingest is unavailable + posts `degraded_data=true` to decision_log; refit fires automatically within 24h of new pair threshold being crossed; replay no longer touches live trade DB; sensor alerts on either heartbeat silence.

### Phase 3: Deprecate old paths (subtractive, weeks 9-10)

**Goal:** remove redundant K2 jobs from `src/main.py`; trading's main loop becomes single-purpose.

**Deliverables:**
- `src/main.py` reduced to: `_run_mode(*)`, `_harvester_pnl_resolver_cycle`, `_write_heartbeat`, `_write_venue_heartbeat`, `_start_user_channel_ingestor_if_enabled`, plus startup gates. ~250 lines (from ~780).
- All `_k2_*` and `_etl_recalibrate` jobs removed; advisory locks in `state/locks/k2_*.lock` deleted.
- Calibration scripts moved to `scripts/ingest/calibration/`.
- `db.py:66-73` `get_trade_connection_with_world` deleted (after seam migration completes).
- Antibody #8 (`tests/test_main_module_scope.py`) enforces.

**Exit gate:** monolith is gone; both systems pass their independence antibody suites; one full week of clean operation under the asymmetric restart policy.

### Phase 4 (deferred): Strategy-as-process boundary

Deferred with explicit revisit trigger (see §3.0 row 3). Not a Phase 1 blocker.

---

## 6. Antibody Contract List

| # | Test | Today | Phase | Purpose |
|---|---|---|---|---|
| 1 | `tests/test_ingest_isolation.py::test_no_forbidden_imports_in_ingest` | EXISTS | — | scripts/ingest forbids src.engine|execution|strategy|signal|control|observability|main|calibration |
| 2 | `tests/test_ingest_isolation.py::test_no_forbidden_transitive_imports_in_ingest` | EXISTS | — | subprocess-isolated transitive closure check |
| 3 | `tests/test_trading_isolation.py` | NEW | Phase 1 | src.engine|strategy|signal|execution forbids importing scripts.ingest.* |
| 4 | `tests/test_world_writer_boundary.py` | NEW | Phase 1 | **Detection mechanism: AST-based scan of every module under `src/` and `scripts/`. Walks `ast.Module`, finds every `ast.Call` whose callable resolves (heuristically by name match) to `cursor.execute|conn.execute` or `cursor.executemany|conn.executemany`, and inspects the SQL string's first verb. Allowlist: only `src.data.*_append.py`, `src.data.daily_observation_writer`, `src.data.observation_instants_v2_writer`, `src.ingest.harvester_truth_writer` (replaces legacy harvester allowlist post-Phase-1.5), and `scripts/ingest/calibration/*` may emit INSERT/UPDATE/DELETE against world.* tables. Dynamic SQL strings → flagged for manual review (test fails with explicit list).** |
| 5 | `tests/test_world_schema_version_compatibility.py` | NEW | Phase 2 | trading boot validator must read `architecture/world_schema_manifest.yaml` and confirm DB matches; mismatch → FATAL exit |
| 6 | `tests/test_data_freshness_gate.py` | NEW | Phase 2 | Tests all three branches: FRESH (passes), STALE (per-source degradation matrix), **ABSENT (boot retry-with-backoff for 5 min then FATAL; mid-run degraded mode without exit)** |
| 7 | `tests/test_replay_readonly.py` | NEW | Phase 2 | Replay run with `?mode=ro` raises if any code path attempts INSERT/UPDATE |
| 8 | `tests/test_main_module_scope.py` | NEW | Phase 3 | `src.main` import set is bounded — must not include any K2 ingest module or `get_trade_connection_with_world` |
| 9 | `tests/test_world_writer_provenance_contract.py` | NEW | Phase 2 | Every NEW write to observations/forecasts/observation_instants_v2 carries source, authority, data_version. Backward-compat: rows pre-2026-04-30 with NULL provenance are tagged `data_version='legacy_v0', authority='UNVERIFIED'` at read time. |
| 10 | `tests/test_calibration_consumer_lane.py` | NEW | Phase 2 | trading code (engine/strategy/signal) imports only `src.calibration.{platt,store,manager,metric_specs}` |
| 11 | `tests/test_dual_run_lock_obeyed.py` (HBL-3) | NEW | Phase 1 | Two processes contend on `state/locks/k2_hourly_instants.lock`; only one writes per (city, target_date) per minute; the other returns `skipped_lock_held` |
| 12 | `tests/test_harvester_split_independence.py` (Q6) | NEW | Phase 1.5 | trading down → ingest harvester writes `world.settlements`; ingest down → trading harvester returns `awaiting_truth_writer` without writing trade.decision_log |
| 13 | `tests/test_no_raw_world_attach.py` (HBL-1, ATTACK 7e) | NEW | Phase 2 | grep + AST: src.engine|src.strategy|src.signal does not contain `get_trade_connection_with_world` or `ATTACH DATABASE` outside `world_view` and (legacy) harvester allowlist |
| 14 | `tests/test_control_plane_dual_consumer.py` (§4.5a) | NEW | Phase 2 | Both daemons honor `pause_source: ecmwf_open_data` within one tick |
| 15 | `tests/test_heartbeat_sensor_dual_coverage.py` (§4.5d) | NEW | Phase 2 | sensor alerts when EITHER `daemon-heartbeat.json` or `daemon-heartbeat-ingest.json` is stale > 5 min |

---

## 7. Open Questions for Operator

See [`open_questions.md`](open_questions.md). After v2: Q1, Q2, Q6 RESOLVED per critic recommendations; Q3, Q4, Q5, Q7 remain as operator-decidable, with architect's defaults captured.

---

## 8. References (verified 2026-04-30 against HEAD)

- `src/main.py:33-60` — `_scheduler_job` decorator (uniform observability) [VERIFIED]
- `src/main.py:104-108` — harvester invocation from trading lane [VERIFIED]
- `src/main.py:111-233` — K2 ingest jobs (the target of the split) [VERIFIED]
- `src/main.py:244-301` — `_etl_recalibrate` block (replay invocation at 291-299) [VERIFIED]
- `src/main.py:336-369` — `_write_heartbeat` (`daemon-heartbeat.json` writer) [VERIFIED]
- `src/main.py:474-488` — `_startup_wallet_check` (hard exit on failure) [VERIFIED]
- `src/main.py:573-599` — G6 strategy guard (KNOWN_STRATEGIES check) [VERIFIED]
- `src/main.py:613-614` — `bypass_dead_proxy_env_vars` [VERIFIED]
- `src/main.py:619-625` — `init_schema(world_conn)` + trade conn parity [VERIFIED]
- `src/main.py:651-770` — APScheduler job registration block [VERIFIED]
- `src/state/db.py:37-44` — `_connect()` sets `journal_mode=WAL`, timeout=120 [VERIFIED]
- `src/state/db.py:47-54` — `get_trade_connection`, `get_world_connection` (separate paths) [VERIFIED]
- `src/state/db.py:66-73` — `get_trade_connection_with_world` (THE ONLY ATTACH SITE in src/) [VERIFIED]
- `src/state/db.py:356` — `init_schema` (idempotent CREATE; ALTER not idempotent) [VERIFIED]
- `src/state/db.py:4871` — error string proving ALTER is failure-prone [VERIFIED]
- `src/execution/harvester.py:427-448` — `run_harvester` entry + ZEUS_HARVESTER_LIVE_ENABLED gate [VERIFIED]
- `src/execution/harvester.py:451-452` — TWO-CONNECTION pattern: `trade_conn = get_trade_connection()`, `shared_conn = get_world_connection()` [VERIFIED — replaces v1's incorrect ATTACH cite]
- `src/execution/harvester.py:556-562` — `_write_settlement_truth(shared_conn, ...)` call site [VERIFIED]
- `src/execution/harvester.py:614-622` — `_settle_positions(trade_conn, ...)` call site [VERIFIED]
- `src/execution/harvester.py:633` — `store_settlement_records(trade_conn, ...)` for decision_log [VERIFIED]
- `src/execution/harvester.py:911-1090` — `_write_settlement_truth` body (INSERT INTO settlements at lines 1018-1027) [VERIFIED]
- `src/execution/harvester.py:1120` — `_first_snapshot_table` (string helper, NOT settlements_v2) [VERIFIED]
- `src/state/decision_chain.py:217-256` — `store_settlement_records` writes trade.decision_log [VERIFIED]
- `src/engine/cycle_runner.py:40-42` — `get_connection = get_trade_connection_with_world` (THE SEAM) [VERIFIED]
- `src/execution/fill_tracker.py:142, 171, 211, 267` — 4 callsites of `deps.get_connection()` [VERIFIED]
- `src/control/control_plane.py:25` — `CONTROL_PATH = state_path("control_plane.json")` [VERIFIED]
- `src/config.py:48-57` — `get_mode()` always returns "live"; ZEUS_MODE is decorative [VERIFIED]
- `src/calibration/retrain_trigger.py:177, 193, 395` — `status`, `arm`, `trigger_retrain` (does NOT compute drift on settlements) [VERIFIED]
- `src/data/ingestion_guard.py` (file exists; pre-write contract gate) [VERIFIED]
- `src/riskguard/riskguard.py:1102-1114` — riskguard standalone main loop (precedent) [VERIFIED]
- `tests/test_ingest_isolation.py:49-63` — `FORBIDDEN_IMPORT_PREFIXES` includes `src.calibration` [VERIFIED]
- `tests/test_riskguard.py:272, 378, 441, 466, 482, 511, 539, 571, 644, 1108, ...` — 10+ monkeypatches of `riskguard_module.get_connection` [VERIFIED]
- `scripts/ingest/_shared.py` — `run_tick` wrapper used by all 5 K2 ticks [VERIFIED]
- `scripts/ingest/{daily_obs_tick,hourly_instants_tick,solar_daily_tick,forecasts_daily_tick,hole_scanner_tick}.py` — 5 K2 tick scripts [VERIFIED]
- `~/Library/LaunchAgents/com.zeus.live-trading.plist` — current monolithic plist (HTTPS_PROXY=localhost:7890; WU_API_KEY inline; KeepAlive=true) [VERIFIED]
- `~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist` — sensor plist (uses `bin/heartbeat_sensor.py`) [VERIFIED]
- `~/Library/LaunchAgents/com.zeus.riskguard-live.plist` — separate-daemon precedent [VERIFIED]
- `state/scheduler_jobs_health.json` — evidence of `ecmwf_open_data` failing silently and `k2_daily_obs` cascading WU failure [VERIFIED]

(Note on WAL safety claim: the v1 design said "WAL across processes is already proven safe" without citation. v2 narrows the claim: SQLite WAL allows N readers + 1 writer with `SQLITE_BUSY` retry on contention; `db.py:40` `timeout=120` provides 120s of retry. During Phase 1 dual-running we add advisory file locks at `state/locks/k2_*.lock` (HBL-3) so we never test WAL beyond 1-writer-per-table-per-tick.)

---

## 9. Revision Log (v1 → v2)

| Critic finding | Section changed | What changed |
|---|---|---|
| **HBL-1** (premise mismatch on harvester ATTACH) | §1 axis 2; §8 references | Removed the false ATTACH-at-`harvester.py:1120-1124` claim. Replaced with verified two-connection mechanism: `harvester.py:451` (trade_conn) + `harvester.py:452` (shared_conn = world). Settlements write happens at `harvester.py:556` via `_write_settlement_truth(shared_conn, ...)`. Added explicit note that the only ATTACH in `src/` is at `db.py:66-73`. `WorldSettlementWriter` contract in §1 axis 2 now wraps the two-connection pattern, not ATTACH. |
| **HBL-2** (boot-order sentinel TOCTOU + KeepAlive=false stuck system) | §4.2 (rewritten) | Specified sentinel JSON shape with `written_at` freshness timestamp; trading rejects sentinels > 24h old; trading retry-with-backoff for 5 min before FATAL exit; explicit operator playbook in `docs/runbooks/freshness_gate_fatal.md` (Phase 2 doc deliverable). |
| **HBL-3** (Phase 1 dual-write race on data_coverage) | §5 Phase 1 deliverables; antibody #11 | Promoted "redundancy suppression" from parenthetical to a first-class Phase 1 deliverable: advisory file lock per table at `state/locks/k2_<table>.lock` using `fcntl.flock(LOCK_EX | LOCK_NB)`. Added antibody #11 `tests/test_dual_run_lock_obeyed.py`. Six locks specified (one per K2 table). |
| **ATTACK 8 Scenario C** (`source_health.json` absent branch unspecified) | §3.1 (rewritten as three-branch decision tree) | FRESH / STALE / ABSENT branches all explicit. ABSENT at boot → 5-min retry-with-backoff then FATAL. ABSENT mid-run → degraded mode (treat all sources as STALE), no exit. Antibody #6 tests all three branches. |
| **ATTACK 2a** (`control_plane.json` cross-daemon coordination) | §4.5(a); antibody #14 | Ingest daemon now reads control_plane.json on each tick; honors `pause_source` / `resume_source` / `pause_ingest` keys. Trading retains its existing keys. Antibody #14 enforces dual-consumer behavior. |
| **ATTACK 2b** (log rotation strategy) | §4.5(b) | macOS `newsyslog`-driven rotation; daily rotate, 14-day retention, gzip; configured via `/etc/newsyslog.d/zeus.conf` (or LaunchAgent fallback). Phase 2 ops deliverable. |
| **ATTACK 2c** (secrets-rotation playbook) | §4.5(c) | `docs/runbooks/secrets_rotation.md` Phase 2 doc deliverable; documents WU/ECMWF key rotation touches ingest plist only; warns against muscle-memory "reload trading plist." |
| **ATTACK 2d** (heartbeat-sensor coverage of new ingest daemon) | §4.5(d); antibody #15 | Ingest writes `state/daemon-heartbeat-ingest.json` on a 60s tick; heartbeat sensor extended to monitor BOTH files; antibody #15 enforces. |
| **ATTACK 7e** (cycle_runner.py:42 seam migration) | §3.2 (rewritten) | The `world_view` migration is now a 4-deliverable plan (A-D): introduce `world_view` module; replace cycle_runner alias with a `ConnectionPair` factory; migrate fill_tracker callsites; migrate riskguard test monkeypatches with a `fake_connection_pair()` helper. Antibody #13 enforces no raw ATTACH outside allowlist. Migration runs over Phase 2 with overlap window before Phase 3 deletion of `get_trade_connection_with_world`. |
| **ATTACK 9 antibody adequacy** (#4 detection mechanism) | §6 row 4 | Made detection mechanism explicit: AST-based scan of every module; walks `ast.Call` for `*.execute|executemany`, inspects SQL verb, dynamic SQL flagged for manual review. |
| **ATTACK 10 / Q6** (harvester downtime during trading rebuilds) | §5 (NEW Phase 1.5); open_questions Q6 | Q6 RESOLVED with critic-recommended Phase-1.5 split: `src/ingest/harvester_truth_writer.py` (writes world.settlements) on ingest scheduler; `src/execution/harvester_pnl_resolver.py` (writes trade.decision_log) on trading scheduler. Antibody #12 enforces independence. Settlement continuity preserved during trading rebuilds. |
| **SC-1** (Phase 1 exit gate ±5% ungrounded) | §5 Phase 1 exit gate | Tightened to ±1% on rolling 24h window for `observation_instants_v2`, `forecasts`, `solar_daily`, `data_coverage`. Baseline window: 7 days of monolith-only operation pre-cutover. |
| **SC-2** (Phase 4 strategy split risks permanent deferral) | §3.0 row 3 | Explicit revisit trigger: "If, within 8 weeks of Phase 3 completion, two enabled strategies have divergent restart-policy needs, escalate to architect." |
| **SC-3** (IngestionGuard backward compatibility) | §2 row 4 | Legacy rows tolerated by treating absent provenance as `data_version='legacy_v0', authority='UNVERIFIED'` at read time. Antibody #9 spec updated. |
| **SC-4** (`ingest_status.json` writer cadence) | §2 row 5 | Specified: every K2 tick completion + every 5 minutes from a dedicated rollup tick. Reader contract: poll at most every 30s. |
| **SC-6** (drift detector hand-waved) | §2 row 2; Phase 2 deliverables | Phase 2 deliverable: NEW `src/calibration/drift_detector.py` with `compute_drift(city, season, metric_identity) -> DriftReport` API; feeds `retrain_trigger.arm(...)` (`retrain_trigger.py:193`). |
| **MISMATCH-2** (`ZEUS_MODE` decorative) | §4.1 | Removed `ZEUS_MODE=live` from new plists' Env section; noted parity-only. |
| **Q1** | §4.3; open_questions Q1 | RESOLVED: manual recovery for trading; KeepAlive=false; external watchdog reads `state/auto_restart_allowed.flag`. |
| **Q2** | §1.2; §2 row 10; open_questions Q2 | RESOLVED: ingest-owned calibration. Move scripts to `scripts/ingest/calibration/` in Phase 3. Third-lane deferred to Phase 4 if cadence diverges. |
| **Q6** | §5 (NEW Phase 1.5); open_questions Q6 | RESOLVED: Phase-1.5 split (NOT Phase 4). |

