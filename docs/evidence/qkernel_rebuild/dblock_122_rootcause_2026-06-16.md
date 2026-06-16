# #122 `database is locked` on state/zeus_trades.db — Root Cause

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: live zeus-live.log (UTC-5) + src.state.db / src.data.market_scanner /
#                  src.execution.executor / src.main / src.events.reactor read-through
# Mode: DIAGNOSE-ONLY (no live-code edits)
```

## 6-line verdict

1. zeus_trades.db IS WAL and the MAIN connection helpers (`_connect`, `get_connection`) DO set a 30 s `busy_timeout` — that hypothesis is REFUTED for the order/submit path.
2. ROOT CAUSE: zeus_trades.db has **NO in-process write serialization** (unlike zeus-world.db's `_GuardedWorldMutex`). N independent live connections (20 s warm cycle + reactor decision-time refresher + reactor end-of-cycle `_drain_substrate_refreshes` + submit collateral refresh + exit lifecycle + collateral heartbeat) all race the single WAL write lock.
3. The dominant failing writer is the snapshot-capture loop, which DELIBERATELY runs a **short** per-row busy_timeout — `ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS` default **2000 ms**, floor **1000 ms**, further clamped to the shrinking per-cycle budget, + only **2** sleep-retries (`market_scanner.py:108-120, 4145-4212`) — NOT the 30 s default. Under concurrent writers it loses the lock and logs `database is locked` (6750 of the classifiable hits).
4. No writer holds a write txn ACROSS network I/O on zeus_trades.db: the inner capture is commit-per-item (`market_scanner.py:4169-4187`), the outer warm cycle is read-only until the bounded write stage, and the submit collateral refresh does its HTTP BEFORE the (uncommitted) write. So this is concurrent-writer contention, NOT a held-lock-across-HTTP bug.
5. The 9 `pre_submit_collateral_refresh_failed: collateral_refresh_failed: database is locked` hits REJECT a decided order (`executor.py:3458-3469`) — the money-path damage; the 6750 warm-cycle hits mostly self-heal via the 2-retry loop but burn snapshot budget (coverage oscillates FULL↔PARTIAL, `fresh_executable_city_count` 2↔24).
6. MINIMAL FIX (rank 1, lowest risk): raise the capture busy_timeout floor so the warm/refresher writer waits like every other writer — set env `ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS=30000` and `ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS` ≥ 5000 (no code change), OR raise the code default at `market_scanner.py:108-109`. Rank 2 (structural, the real cure): a process-global trade-write mutex mirroring the world mutex.

---

## Evidence

### E1. zeus_trades.db state (verified live, 2026-06-16 04:2x UTC-5)
- `state/zeus_trades.db` = **26.0 GB** (`zeus_trades.db` 26,043,097,088 bytes).
- `state/zeus_trades.db-wal` observed **4.1 MB → 93 MB** within minutes (no dedicated trade checkpoint job; see E6).
- `PRAGMA journal_mode` = `wal` (confirmed). `PRAGMA busy_timeout` on a fresh `?mode=ro` handle = `0` — expected, because `?mode=ro` does NOT route through `_connect`/`get_connection`, so it does not carry the daemon's pragmas. This is the read-only-probe artifact the prompt observed; it is NOT the daemon's WRITE-connection state.
- `database is locked` count in `logs/zeus-live.log`: **13,658**. Of the classifiable lines: `refresh_pending_family` (warm/capture path) = **6750**, `pre_submit_collateral` = **9**, `collateral_refresh` = **1**. Steady **40–100 lock-logs/hour** across 2026-06-15→16.

### E2. busy_timeout IS set on the main trade connections (hypothesis partially REFUTED)
`src/state/db.py`:
- `_connect()` (`db.py:186-233`) — used by `get_trade_connection(write_class="live")` — applies `PRAGMA journal_mode=WAL` (205) and `_apply_busy_timeout(conn)` (229).
- `get_connection()` (`db.py:1204-1249`) — the legacy/riskguard helper — applies the same `_apply_busy_timeout(conn)` (1245).
- `_apply_busy_timeout` (`db.py:130-148`) reads `ZEUS_DB_BUSY_TIMEOUT_MS` (default **30000**) and runs `PRAGMA busy_timeout = <ms>` at the SQL level (durable across `executescript`).
- No `.env` / service file overrides `ZEUS_DB_BUSY_TIMEOUT_MS` (grep empty) → default **30 s** in effect on the order/submit/exit connections.

So the order-path and submit-path connections wait 30 s for the WAL write lock. That is why submit-path lock failures are RARE (9 total) — but when they hit, they reject a decided order.

### E3. The decisive divergence — the capture path uses a SHORT, shrinking busy_timeout (ROOT of the 6750)
`src/data/market_scanner.py`:
- `_snapshot_capture_busy_timeout_ms(remaining_seconds)` (`market_scanner.py:80-116`): `configured = ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS` default **2000**; `floor_ms = ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS` default **1000**; result is bounded BY the per-cycle remaining-seconds budget (so it shrinks toward the floor late in a cycle).
- `_snapshot_capture_sqlite_lock_retries()` (`market_scanner.py:118-122`): default **2** retries, each `time.sleep(min(0.05*attempt, ...))` (≈50 ms, 100 ms) — `market_scanner.py:4199-4206`.
- The capture loop applies this REDUCED budget per row: `_set_busy_timeout_ms(conn, _snapshot_capture_busy_timeout_ms(remaining_seconds))` (`market_scanner.py:4151`), restoring the prior value in `finally` (4212).
- The function's OWN docstring (`market_scanner.py:83-96`) names the disease: *"The trade DB … is written concurrently IN-PROCESS by the executor submit path, the exit lifecycle, and the CollateralLedger heartbeat — each on an independent connection, so the in-process write mutex does not serialize them. A WAL write lock therefore changes hands constantly."*

Net: every other trade writer waits 30 s; the capture writer waits ~1–2 s. When the lock is held by any peer for >~1–2 s, the capture insert fails `database is locked`. That is the 6750.

### E4. NO writer holds a write txn across network I/O on zeus_trades.db (held-lock-across-HTTP hypothesis REFUTED for trades)
- Inner capture is **commit-per-item** (`market_scanner.py:4169-4190`, fix 2026-05-31): `capture_executable_market_snapshot` does its per-outcome venue HTTP (`_fetch_clob_market_info` / GET /book / `_fetch_fee_details`) then ONE `insert_snapshot` then `conn.commit()`; on failure `conn.rollback()` (4196). So no write txn spans an HTTP call inside the loop.
- The OUTER warm cycle `_refresh_pending_family_snapshots` (`main.py:3334-4160`) opens `write_conn = get_trade_connection(write_class="live")` (3557) and holds it across the Gamma fetch (3791) and CLOB capture (4113) — BUT everything it does on `write_conn` before the bounded write stage is **read-only**: `_condition_buy_sides_fresh` (SELECT, `main.py:3170-3206`), `reconstruct_weather_market_from_static_topology` (SELECT-only, `market_scanner.py:3427-3486+`), `_prune_fresh_market_outcomes_for_snapshot_refresh` (SELECT, `main.py:3209-3239`). WAL permits concurrent readers, so the open handle does not hold the WRITE lock during Gamma/CLOB HTTP. The write lock is only taken inside the commit-per-item stage.
- Submit collateral refresh `_refresh_entry_collateral_snapshot_for_submit` (`executor.py:518-537`) → `CollateralLedger(conn).refresh(adapter)` (`collateral_ledger.py:246-282`): the venue/chain balance+allowance+positions HTTP happens in `_read_adapter_payload` (`collateral_ledger.py:659-708`) BEFORE `_persist_snapshot` (281), and because the ledger is built with an external `conn` (`_owns_conn=False`) it does NOT commit inside refresh (`collateral_ledger.py:572-573`) — the caller's txn owns the commit. So no HTTP inside an open trade write txn here either.

Conclusion: this is uncoordinated concurrent writers losing a short-budget race, not a single writer wedging the lock across I/O.

### E5. The concurrent zeus_trades.db writers (who races whom)
All target `executable_market_snapshots` (and peers target other trade tables), each on its OWN connection, with NO shared in-process mutex:
- **Warm cycle** `_edli_market_substrate_warm_cycle` → `_refresh_pending_family_snapshots`: `write_conn` at `main.py:3557`. Interval **20 s** (`_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0`, `main.py:85`). Capture budget ~12 s reserve.
- **Reactor decision-time refresher** `_edli_decision_family_snapshot_refresher._refresh`: its OWN `write_conn = get_trade_connection(write_class="live")` at `main.py:7568`, same `refresh_executable_market_substrate_snapshots` path (7581). Fires AT decision time and via the reactor's end-of-cycle drain.
- **Reactor `_drain_substrate_refreshes`** (`reactor.py:1119-1158`) → `_drain_one_bucket` → `self._family_snapshot_refresher` (the callable above), once per blocked family per cycle.
- **Submit path** `_refresh_entry_collateral_snapshot_for_submit` writes the collateral snapshot on the submit `conn` (`executor.py:518`, persisted in the order txn).
- Plus exit lifecycle, command_recovery, exchange_reconcile fill-bridge (`exchange_reconcile.py:335` `conn.commit()`), and the CollateralLedger heartbeat — all independent trade-DB writers.

Because the warm cycle (20 s) and the reactor cycle overlap in wall-clock, ≥2 capture writers routinely insert into the same append-only table on separate connections at the same instant. With WAL's single-writer rule, one waits; the capture writer's wait budget is the ~1–2 s of E3, so it is the one that loses and logs.

### E6. Secondary: trade WAL has no dedicated checkpoint job (NOT the primary cause)
- Only zeus-world.db has `_world_wal_checkpoint_cycle` (90 s interval, completes <2 s — log 04:25–04:31). There is no `_trade_wal_checkpoint` job (grep empty).
- zeus_trades.db relies on SQLite auto-checkpoint (1000 pages ≈ 4 MB) on each writer commit. WAL grew 4 MB→93 MB in minutes, so auto-checkpoint is lagging under the write rate but not blocking (no checkpoint-busy logging). On a 26 GB DB a blocking TRUNCATE checkpoint could itself become a multi-second lock holder, so a future checkpoint job must be NON-blocking (PASSIVE) — do NOT add a TRUNCATE checkpoint that would make contention worse.

### E7. Throughput link (why this is the limiter, per the goal)
- `fresh_executable_city_count` oscillates 2↔24, coverage FULL↔PARTIAL (last 14 summaries) — driven by per-cycle budget + fair-rotation slicing; lock failures burn capture budget and amplify the swing.
- Reactor cycle wall-time ≈ the warm budget; warm runs are ~20–22 s apart and queue back-to-back (log 04:32:39 done → 04:32:41 next). End-of-cycle `_drain_substrate_refreshes` (network I/O + a third trade-writer) is part of the cycle tail the prior redecide report flagged.

---

## Provenance audit (files read)
- `src/state/db.py` — last touched 2026-06-12 (`cfd6ba8294`). `_apply_busy_timeout` + WAL setup CURRENT_REUSABLE; busy_timeout correctly applied on both connection factories.
- `src/state/collateral_ledger.py` — 2026-05-17 audit header; `refresh`/`_persist_snapshot` CURRENT_REUSABLE; HTTP-before-write, external-conn no-commit semantics intact.
- `src/data/market_scanner.py` — last touched 2026-06-14 (`af90efa93b`). Commit-per-item + short capture busy_timeout CURRENT (these are the load-bearing lines).
- `src/execution/executor.py` — 2026-06-15 (`1d286c5e56`). Submit collateral-refresh reject path CURRENT.
- `src/main.py` — 2026-06-16 (`c5162a34b9`). Warm cycle + decision refresher CURRENT.
- `src/events/reactor.py` — 2026-06-16 (`bef3671835`). `_drain_substrate_refreshes` CURRENT.
- No in-flight #122 commit touches the lock budget (recent commits are fill-wall/sizing/escalation), so this fix does not collide with pending work.

---

## Recommended fixes (ranked by impact / risk)

### Rank 1 — RAISE the capture writer's wait budget to match peers (lowest risk, no code change)
The capture path is the ONLY trade writer that waits ~1–2 s instead of 30 s. Make it wait like everyone else.
- Set env on the live daemon: `ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS=30000` and `ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS=5000`.
- Caveat: the per-row budget is also clamped by `remaining_seconds` of the snapshot reserve (`market_scanner.py:4150-4151`), so the effective wait is `min(configured, remaining)`. To let a row actually wait several seconds, the snapshot reserve (`ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS`, default 12) and/or refresh budget must leave that headroom. This is purely WIDENING a wait budget — no txn-semantics, ordering, or INV-37 change. NOT a cap/throttle (operator law respected: it removes failures, adds none).
- Expected effect: the 6750 warm-cycle `database is locked` logs collapse to near-zero (transient peer holds are sub-second); `failed` stays 0; coverage stabilizes. Does not by itself fix the 9 submit rejects (those already wait 30 s — see Rank 2).

If a code-level default is preferred over env: bump `market_scanner.py:108` default `"2000"`→`"30000"` and `:109` floor `"1000"`→`"5000"`. One/two-line change, behavior-preserving (only widens the wait).

### Rank 2 — STRUCTURAL: a process-global trade-write mutex (the real cure, mirrors the world fix)
The disease named in `db.py:289-334` for zeus-world.db (in-process unserialized writers → WAL-lock starvation) applies verbatim to zeus_trades.db, which has NO such mutex. Introduce a `_GuardedTradeMutex` analogous to `_GuardedWorldMutex` and wrap each trade-DB WRITE transaction (capture commit-per-item, submit collateral persist, exit lifecycle, exchange_reconcile) so SQLite never sees two concurrent trade writers. This eliminates the race at the source (both the 6750 warm hits AND the 9 submit-reject hits), and — like the world mutex — must be held ONLY around the BEGIN→COMMIT and NEVER across HTTP (the capture path is already commit-per-item with HTTP outside, so it composes cleanly). Higher risk (touches every trade writer; must preserve the "never across I/O" contract and INV-37 ATTACH+SAVEPOINT cross-DB path), so stage behind Rank 1.

### Rank 3 — Serialize the two snapshot-capture writers (narrow structural)
If a full trade mutex is too broad for now: gate the warm cycle and the reactor decision/drain refresher behind ONE shared `threading.Lock` (they run the identical `refresh_executable_market_substrate_snapshots` path on separate connections and are the two highest-frequency contenders). Smaller blast radius than Rank 2; removes the dominant warm↔reactor collision specifically. There is already a `_market_substrate_refresh_lock` (`main.py:4254`) guarding `market_discovery` vs warm — extend the same pattern to the decision-time refresher.

### NOT recommended
- A zeus_trades.db TRUNCATE checkpoint job: on a 26 GB DB this becomes a multi-second blocking lock holder and would WORSEN contention. If WAL bloat (E6) needs bounding, use a PASSIVE checkpoint only.

## Uncertainty stated explicitly
- Exact lock-holder at each of the 6750 failures is not individually logged; the attribution to "concurrent capture writers" is inferred from (a) the short-budget math (E3), (b) the two-plus concurrent capture connections (E5), and (c) the docstring's own enumeration of peers (E3). Confidence HIGH that the SHORT capture budget is why the warm path (and only the warm path) logs at this rate, given the 30 s peers log only 9 times.
- I did not instrument the live daemon (diagnose-only). A definitive confirmation would log the holder PID/connection + hold-duration at each lock failure, or add a counter on which peer held the WAL write lock when the capture insert raised.
- The submit-path 9 rejects could also arise from a single peer holding >30 s (e.g. a stalled venue HTTP inside a commit-bearing txn elsewhere); I found no such site on the trades path, but did not exhaustively audit every trade writer's txn/IO ordering (exit lifecycle, command_recovery, exchange_reconcile were spot-checked, not line-audited).
