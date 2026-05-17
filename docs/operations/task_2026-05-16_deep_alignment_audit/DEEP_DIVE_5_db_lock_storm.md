# Deep Dive #5 — DB lock storm (CollateralLedger heartbeat + ingest harvester)

**Audited:** 2026-05-16 (UTC)  
**Repo HEAD:** worktree-zeus-deep-alignment-audit-skill @ f65a6abe96  
**Karachi position:** `c30f28a5-d4e` Karachi 2026-05-17, phase=`active`  
**Mode:** READ-ONLY. No edits, no DB writes.

---

## 1. Executive summary

Two distinct lock storms confounded into one by Run-2:

- **Storm A (live, ~1,800 events, NEW since 2026-05-15 ~16:17 UTC):** `CollateralLedger heartbeat refresh failed closed: database is locked` (`src/main.py:254`). Onset matches commits `8cae5a7f79` + `e218fac02d` (2026-05-15, "keep collateral snapshots fresh via heartbeat" / "commit heartbeat collateral refresh truth"). Root cause: `CollateralLedger.__init__` (`src/state/collateral_ledger.py:164-169`) calls bare `sqlite3.connect(str(db_path), check_same_thread=False)` — **no `timeout=`, no `PRAGMA busy_timeout`, no `PRAGMA journal_mode=WAL`**. Defaults to the SQLite 5-second wait. Other processes (riskguard, ingest user-channel) hold `state/zeus_trades.db` >5 s and the ledger times out. Exception is swallowed at `src/main.py:253-256` (logger.warning, returns False) — operator only sees the noisy WARNING.
- **Storm B (ingest, ~50,730 events, pre-existing):** `harvester_truth_writer` (`src/ingest/harvester_truth_writer.py:766`) — 94 % of all "database is locked" lines in `logs/zeus-ingest.err`. Target DB was `state/zeus-forecasts.db` (51 GB). K1 forecasts-DB split (2026-05-11 / `eba80d2b9d` + `2e00271cee`) sharply reduced this storm: 17,827 events on 2026-05-06 → 398 on 2026-05-14. Exception is logged-and-counted at `src/ingest/harvester_truth_writer.py:764-768` (`errors += 1`) per event.

Net live trading impact today: **LOW**. Snapshots are still landing every ~60 s (`SELECT … FROM collateral_ledger_snapshots ORDER BY id DESC` confirms id 9436-9440 at 60-s intervals up to 2026-05-16 17:29:52 UTC, `authority_tier=CHAIN`). Failure is per-attempt (15 % per hour at 5 s tick × 30 s gate = ~120 attempts/h, ~109 fail), retry on the next 30 s-gated tick succeeds. No `DEGRADED` tier observed.

**Run-2 over-rotated** by tagging this as the single dominant finding; it is two findings, only one of which is hot. The "37 in last 1000 lines of live" was specifically Storm A (which is real and a regression). The 50,730 figure is Storm B (pre-existing, mitigated by K1 split, separate process, separate DB).

---

## 2. Code paths

### Storm A — CollateralLedger heartbeat (live daemon)

| Step | File:Line | Code |
|------|-----------|------|
| Scheduler register | [src/main.py:932](src/main.py#L932) | `scheduler.add_job(_write_venue_heartbeat, "interval", seconds=heartbeat_cadence_seconds_from_env(), id="venue_heartbeat", max_instances=1, coalesce=True)` |
| Default cadence | [src/control/heartbeat_supervisor.py:87](src/control/heartbeat_supervisor.py#L87) | `heartbeat_cadence_seconds_from_env()` — observed in logs: 5 s |
| Heartbeat fn body | [src/main.py:450-495](src/main.py#L450-L495) | `_write_venue_heartbeat()` → `asyncio.run(...run_once())` → `_run_ws_gap_reconcile_if_required(...)` → `_refresh_global_collateral_snapshot_if_due(...)` |
| Refresh gate (30 s) | [src/main.py:210](src/main.py#L210) | `COLLATERAL_HEARTBEAT_REFRESH_SECONDS = 30.0` |
| Refresh fn | [src/main.py:213-256](src/main.py#L213-L256) | `_refresh_global_collateral_snapshot_if_due` |
| Swallowed exception | [src/main.py:252-256](src/main.py#L252-L256) | `except Exception as exc: logger.warning("CollateralLedger heartbeat refresh failed closed: %s", exc); return False` |
| Singleton install | [src/main.py:683-688](src/main.py#L683-L688) | `ledger = CollateralLedger(db_path=_zeus_trade_db_path()); configure_global_ledger(ledger)` |
| **Bare connect (no PRAGMA, no timeout)** | [src/state/collateral_ledger.py:164-169](src/state/collateral_ledger.py#L164-L169) | `self._conn = sqlite3.connect(str(db_path), check_same_thread=False); self._conn.row_factory = sqlite3.Row; self._owns_conn = True; init_collateral_schema(self._conn)` |
| Persist + commit | [src/state/collateral_ledger.py:482-515](src/state/collateral_ledger.py#L482-L515) | `INSERT INTO collateral_ledger_snapshots ... self._conn.commit()` |

**Comparison — the antibody that should have applied:** `src/state/db.py:_connect()` ([db.py:144-160](src/state/db.py#L144-L160)) does it right — `timeout=_db_busy_timeout_s()` (30 s default), `PRAGMA journal_mode=WAL`, `PRAGMA cache_size`, `PRAGMA mmap_size`. The CollateralLedger singleton bypasses this helper.

### Storm B — harvester_truth_writer (ingest daemon)

| Step | File:Line | Code |
|------|-----------|------|
| Outer try/except | [src/ingest/harvester_truth_writer.py:756-768](src/ingest/harvester_truth_writer.py#L756-L768) | `except Exception as exc: logger.error("harvester_truth_writer error for event %s: %s", ...); errors += 1` (per market, not fatal) |
| Inner write (raised) | [src/ingest/harvester_truth_writer.py:617-623](src/ingest/harvester_truth_writer.py#L617-L623) | `except Exception as exc: logger.warning("harvester_truth_writer write failed for %s %s: %s", ...); raise` |
| Final commit | [src/ingest/harvester_truth_writer.py:770-775](src/ingest/harvester_truth_writer.py#L770-L775) | `forecasts_conn.commit()` |
| Forecasts connection (correct PRAGMA) | [src/state/db.py:187-201](src/state/db.py#L187-L201) | `def get_forecasts_connection(...)` → `_connect(ZEUS_FORECASTS_DB_PATH, ...)` (30 s timeout, WAL on) |

---

## 3. Lock pattern analysis

### Storm A — live daemon (`logs/zeus-live.err`)

- **Total:** 1,922 "database is locked" entries; 1,793 from logger `zeus` (this exact warning), 127 from `src.data.market_scanner`.
- **Onset:** 5 entries on 2026-05-14; sudden jump 2026-05-15 13:00 (CDT) ≈ 18:00 UTC, the first full hour after the Polymarket sleep + commits at 2026-05-15 09:17–09:26 PDT.
- **Cadence:** Steady. After onset, ~109 errors/hour, every hour. With ledger gate at 30 s and heartbeat at 5 s, ~120 refresh attempts/h → **~90 % per-attempt failure rate** (≈ 11 successes/h). Successful inserts observed at ~60 s intervals (every other gated attempt succeeds) — DB IDs 9436-9440 at 17:25-17:29 UTC, all `CHAIN`. Net authority freshness: ≤120 s in steady state.
- **APScheduler skipped warnings:** "Execution of job `_write_venue_heartbeat` … skipped: maximum number of running instances reached (1)" appear before the lock burst at each hour (e.g. 11:00:08, 11:00:18). The heartbeat sometimes hangs past 5 s under contention.

### Storm B — ingest daemon (`logs/zeus-ingest.err`)

- **Total:** 50,730 "database is locked". 47,645 from `src.ingest.harvester_truth_writer`, 2,868 from `src.data.hourly_instants_append`, 49 from `src.data.market_scanner`, 53 from `zeus.ingest` (TIGGE startup catch-up), 26 from `src.data.daily_obs_append`.
- **Time distribution:**
  - 2026-05-03: 8,724
  - 2026-05-04: 9,323
  - 2026-05-05: 6,234
  - 2026-05-06: 17,827 (peak)
  - 2026-05-07: 7
  - 2026-05-09: 2,081
  - 2026-05-10: 665
  - 2026-05-11: 2,662 (K1 split lands)
  - 2026-05-12: 1,200
  - 2026-05-13: 1,520
  - 2026-05-14: 398
- **Contention surface:** writes to `state/zeus-forecasts.db` (51 GB). Stack: `harvester_truth_writer._write_settlement_truth → forecasts_conn.execute(INSERT …)`. The harvester swallows per-event, accumulates `errors` counter, never raises — cycle keeps walking the remaining markets.

### Contention source on `state/zeus_trades.db` (Storm A)

`get_trade_connection` is referenced from **22 distinct modules** including:
- `src/riskguard/riskguard.py` — separate launchd service (`com.zeus.riskguard-live`, pid 14356), 60 s tick.
- `src/ingest/polymarket_user_channel.py` — websocket, **8 distinct `conn.commit()` sites** (lines 522, 551, 574, 602, 635, 648, 675, 702, 743), each on event ingest. This is the most likely high-frequency competitor.
- `src/execution/{executor,harvester,harvester_pnl_resolver,settlement_commands,wrap_unwrap_commands,command_recovery}.py`.
- `src/main.py` itself for the cycle runner.

Multiple processes → SQLite WAL helps READERS but writers are still serialized at the DB level. The ledger singleton's missing `busy_timeout` is the proximate cause — it would not lose to other writers if it waited the configured 30 s.

---

## 4. Impact assessment

### What CollateralLedger guards

`docs` and code path summary (`src/state/collateral_ledger.py:1-10, 74-128`): tracks pUSD buy-collateral and CTF sell-inventory + reservations. Used for:

- **Pre-submit gating** ([src/state/collateral_ledger.py:256-265](src/state/collateral_ledger.py#L256-L265) — `_assert_snapshot_fresh`, `CollateralInsufficient` raise). If snapshot age > `COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS = 60 s` (line 41), preflight will **fail closed** with `collateral_snapshot_stale` / `collateral_snapshot_degraded`. This is wired into venue_command_repo blocking semantics ([src/state/venue_command_repo.py:146-157](src/state/venue_command_repo.py#L146-L157)).
- **Observability** ([src/observability/status_summary.py:369-382](src/observability/status_summary.py#L369-L382) — `_collateral_summary`). Drives the dashboard "collateral" component and authority tier.
- **Reservation lifecycle** ([src/state/collateral_ledger.py:530-580](src/state/collateral_ledger.py#L530-L580) — `release_reservation_for_command_state`). Releases at terminal venue command state inside the venue_command_repo savepoint. Currently 1 open reservation in DB.

### Worst-case if heartbeat silently fails 100 %

1. Snapshot ages past 60 s → next preflight raises `CollateralInsufficient("collateral_snapshot_stale")` → venue command path takes pre-SDK collateral-fail branch ([src/state/venue_command_repo.py:146-157](src/state/venue_command_repo.py#L146-L157)). **System fails closed; no over-leverage, no phantom inventory.** Operator sees `pre_submit_collateral_reservation_failed`.
2. If contention persists past entry-window for Karachi 2026-05-17, the new-entry path is blocked but the existing `active` position is unaffected (no refresh needed for hold logic; refresh is required for **new** intents). Exit/settlement is driven by harvester + settlement_commands, not by ledger snapshot freshness.
3. Risk caps are NOT unbounded: riskguard ticks independently every 60 s ([src/riskguard/riskguard.py:1327, 1338](src/riskguard/riskguard.py#L1327)) and consumes bankroll_provider, not ledger snapshot.
4. No silent state drift: each `_persist_snapshot` is one INSERT (append-only history), so a missed refresh just means the next pre-submit reads an older snapshot. **No corruption channel.**

### Today's actual state (verified via read-only SQL on `state/zeus_trades.db`)

- Latest snapshot id 9440 at `2026-05-16T17:29:52.405584+00:00` UTC, tier `CHAIN`.
- Spacing of last 5 IDs is ~60 s — refresh **is** happening, but at half the gated cadence. ≤2 min stale at all times under current load.
- 1 open reservation in `collateral_reservations` (released_at IS NULL). Reservation system functional.
- 2 Karachi 2026-05-17 positions: `7211b1c5-d3b` (voided), `c30f28a5-d4e` (active).

---

## 5. Fix recommendation (ranked by impact ÷ effort)

| Rank | Fix | Effort | Impact | Notes |
|------|-----|--------|--------|-------|
| 1 | **Route CollateralLedger through `src.state.db._connect`** instead of bare `sqlite3.connect`. Adds 30 s busy_timeout + WAL + cache pragmas in one line. | XS (4 lines) | Eliminates Storm A. | Edit `src/state/collateral_ledger.py:164-169`. Either call `_connect(Path(db_path), write_class="live")` or replicate `timeout=` + the two PRAGMA calls verbatim. **Make the category impossible** = make the ledger constructor delegate to the canonical connect helper; future ledger reuse cannot bypass pragmas. |
| 2 | **Promote the swallowed exception to a structured counter + alert when failure-rate > N % over rolling window.** | S | Operator visibility on otherwise-silent Storm A regressions; closes the "fix #1 made a category go silent" gap from Fitz Code Provenance §antibody. | Add `_cnt_inc("collateral_refresh_lock_failed_total")` (mirrors the metrics pattern in `db.py`) and a derived rate in `status_summary`. |
| 3 | **Add a regression test that constructs `CollateralLedger(db_path=…)`, then opens a second sqlite3 connection that BEGIN IMMEDIATE-holds the DB for 10 s, then exercises `ledger.refresh(fake_adapter)`** — must succeed (because timeout > 10 s). | S | Prevents drift — if anyone re-bypasses `_connect` in the future, the test fails. | Place under `tests/state/test_collateral_ledger_lock_resilience.py`. |
| 4 | **Storm B residual:** convert `harvester_truth_writer` per-event WRITE → per-batch SAVEPOINT, so contention window is bounded. Combine with shorter scheduler stagger between harvester and any TIGGE backfill. | M | Drops Storm B closer to zero (already down to ~400/day from 17 k/day). | Lower priority — already mitigated by K1 split. |
| 5 | **Document the singleton lifecycle contract in `T0_SQLITE_POLICY.md`:** all process-wide DB-owning singletons must construct connections via `src.state.db._connect`. | XS | Doctrine / future-proofing. | Aligns with the existing 2026-05-13 "collateral_ledger singleton conn lifecycle remediation" doc trail. |

Rank #1 alone is the structural decision; ranks #2-#5 are antibodies that prevent recurrence. Per Fitz §1 ("structural decisions > patches"), one fix eliminates the symptom class.

---

## 6. Live risk to Karachi 2026-05-17 position `c30f28a5-d4e`

**Verdict: LOW.**

Reasoning:
- Position is already `phase=active` (entered) — new-entry preflight already cleared. Lock storm only affects **future new intents**, not the hold/exit of an active position.
- Exit path uses harvester / settlement_commands, not ledger snapshot freshness.
- Latest collateral snapshot is 1-2 min stale (well under the 60 s freshness cap *per attempt*; in practice each user-facing read sees a snapshot ≤120 s old).
- `authority_tier=CHAIN`, no `DEGRADED` observation in the most recent 5 snapshots.
- 1 open reservation in `collateral_reservations` — release path uses caller-owned conn (`release_reservation_for_command_state(conn, ...)` at [src/state/collateral_ledger.py:530+](src/state/collateral_ledger.py#L530)), wrapped in venue_command_repo savepoint — independent of the singleton's broken pragmas.

Elevation to MED would require: (a) snapshot age > 60 s at the moment a NEW entry intent is dispatched for any market AND (b) the resulting "fail closed" is hit during a window where Karachi-relevant rebalancing is queued. Neither is in flight for `c30f28a5-d4e`.

Elevation to HIGH would require active corruption channel (e.g. partial INSERT). None observed — `_persist_snapshot` is single-statement, auto-commits via `self._conn.commit()` only on `_owns_conn`, and INSERT is append-only.

---

## 7. Open questions / INVESTIGATE-FURTHER

- **Q1.** Who is the actual lock-holder on `state/zeus_trades.db` during the bursts? Need `lsof state/zeus_trades.db-wal` snapshot during a lock event + process map. Suspect `polymarket_user_channel` (8 commit sites, websocket-driven cadence). Could also be the in-process executor under cycle runner.
- **Q2.** Why does the heartbeat sometimes need 5+ seconds (triggering APScheduler "skipped: max_instances reached")? Is it the HTTP POST to `/v1/heartbeats` or a downstream DB op? Add per-stage timing logs.
- **Q3.** Were the 8 user_channel `conn.commit()` sites added/changed recently? Compare commit counts vs. lock-burst onset (2026-05-15 ≈ 18:00 UTC).
- **Q4.** Does `_run_ws_gap_reconcile_if_required` (also called from the heartbeat) acquire a trade-DB write lock? It's a "fresh read-only venue reconciliation sweep" per docstring, but the gap-guard may write a latch update. If it does and runs inside the singleton's connection, that compounds Storm A.
- **Q5.** What is the riskguard live-trading interaction surface? Riskguard runs every 60 s in a separate process (`com.zeus.riskguard-live`, pid 14356) but writes (which tables?) to `zeus_trades.db`. Tabulate riskguard write sites and overlap with collateral_ledger_snapshots write timings.
- **Q6.** The 2026-05-11 K1 split halved Storm B but did not eliminate it. The residual 1,200-2,662/day post-split implies remaining cross-writer contention on `zeus-forecasts.db`. Identify the residual writers (likely TIGGE pipeline + open-meteo archive backfill running concurrently with harvester).
- **Q7.** `_run_ws_gap_reconcile_if_required` returns early if `_cycle_lock.locked()` ([src/main.py:482](src/main.py#L482)). When cycle runner is hot, heartbeat skips reconcile but still attempts refresh — is the cycle runner the lock-holder on the singleton connection? Cycle runner uses its own `get_trade_connection_with_world`, not the ledger singleton, so they should not collide intra-process. **But** they collide at the OS-level WAL writer slot.

---

**End of deep dive.** No edits performed. Authority for all claims above is grep / sed / sqlite3 -readonly run from worktree `zeus-deep-alignment-audit-skill` against working copy at `/Users/leofitz/.openclaw/workspace-venus/zeus`.
