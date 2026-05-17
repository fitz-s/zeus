# Zeus Deep Alignment Audit — Run #6 Findings

**Run date**: 2026-05-17 (Run #6)
**Audit worktree HEAD**: tracks `origin/main` @ `acaae2c242`
**Baseline (Run #5)**: main @ `a924766c8a`
**Delta**: 76 commits since baseline; PR #126 merged at `83e3f0140d` (parents `bf72fc2e54` + `57c9b9df58`)
**Operator pretext**: "Solved and PR merged. Dispatch subagent to verify if completely fixed, then restart loading skills for deep analysis and investigation." (PR not explicitly named — autodetected from git log.)

---

## §1 Phase-1 PR Detection

**PR #126** (merge `83e3f0140d` · 2026-05-16): "PR-I: F14+F16 cascade-liveness fix. SCAFFOLD passed 4 G2 critic rounds + G5c final audit + 2 R1/R2 review-fix rounds. Karachi 5/17 T-0 cascade-plumbing antibody ready."

| Commit | Phase | Subject |
|---|---|---|
| `bdca137631` | C1 | cascade_liveness_contract.yaml + antibody test (8 tests, expected-fail until C2-C4) |
| `24f83c025c` | C2 | `REDEEM_OPERATOR_REQUIRED` state + atomic transition + migration |
| `b180ac7b81` | C3 | scheduler pollers (`_redeem_submitter_cycle`, `_redeem_reconciler_cycle`, `_wrap_unwrap_liveness_guard_cycle`) + boot fail-closed `_assert_cascade_liveness_contract` |
| `174e9bdc53` | C4 | `scripts/operator_record_redeem.py` CLI + 11 tests |
| `7ff5cdf2b0` | C5 | KARACHI runbook §1.5 cascade flow + §3 T+1h CLI step |
| `b8dc2c9890` | R1 | review-fix: 4 BUG (submit_redeem missing commit; RETRYING not polled; world+forecasts user_version bump; 6 freshness headers) |
| `57c9b9df58` | R2 | G5c ship-blocker: drop zeus-forecasts.db from migration default targets (forecasts uses independent `SCHEMA_FORECASTS_VERSION` sentinel) |

**Scope addressed**: F14 (SEV-0 cascade halt at `REDEEM_INTENT_CREATED`) + F16 (SEV-0-latent `wrap_unwrap_commands` no-driver).

---

## §2 Verification Table

| # | Pre-PR claim | Post-PR verdict | Live evidence |
|---|---|---|---|
| **F14** | `submit_redeem` had ZERO production callers; cascade halted at `REDEEM_INTENT_CREATED` | **FIXED-WITH-DEFERRED-LINK** | `_redeem_submitter_cycle` scheduled every 5 min in live `src.main` (PID 44087, 55min uptime; `logs/zeus-live.err` confirms job execution at 04:32, 04:37, 04:42, 04:47, 04:52, 04:57 UTC). Adapter still in `REDEEM_DEFERRED_TO_R1` stub mode → cascade routes `INTENT_CREATED → OPERATOR_REQUIRED` (designed-state per SCAFFOLD §K) requiring operator CLI invocation. Final `OPERATOR_REQUIRED → TX_HASHED → CONFIRMED` link awaits PR-I.5 (web3 wiring); operator CLI bridges manually in interim. |
| **F16** | `wrap_unwrap_commands` state machine had no production driver and no enqueue path | **FIXED-CLEAN (liveness-guarded)** | `_wrap_unwrap_liveness_guard_cycle` runs every 30 min in live daemon; mode=liveness_only (antibody ast-walk enforces no transition calls). Table remains empty (correct until Z5 pUSD migration). |
| **Cascade contract** | (new) | **DEPLOYED** | `architecture/cascade_liveness_contract.yaml` (78 LOC); `tests/test_cascade_liveness_contract.py` 8 antibody tests all pass; `_assert_cascade_liveness_contract` runs at boot fail-closed; no `SystemExit` evidence in last 1h of `logs/zeus-live.err`. |
| **Migration** | (new) | **APPLIED** | `state/zeus_trades.db` `user_version=4` ✓; `state/zeus-world.db` `user_version=4` ✓; `state/zeus-live.db` `user_version=4` ✓; `state/zeus-forecasts.db` `user_version=3` ✓ (R2 fix correctly excluded — independent `SCHEMA_FORECASTS_VERSION` sentinel). CHECK constraint on `settlement_commands.state` includes `REDEEM_OPERATOR_REQUIRED`. |
| **Operator CLI** | (new) | **READY** | `scripts/operator_record_redeem.py` (263 LOC) imports clean. 11 unit tests pass. NORMAL/FORCE modes; idempotency, atomic `_atomic_transition` with `WHERE state=?` race guard; exit codes 0/2/3/4/5/6/7 documented. |
| **Karachi runbook** | (new) | **UPDATED** | KARACHI_2026_05_17_MANUAL_FALLBACK.md §1.5 added 11-step cascade walkthrough naming `condition_id=c30f28a5-d4e` (NB: condition_id token is `0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae` per `position_current`). §3 T+1h step adds operator CLI invocation example. |
| **Test suite** | (new) | **33 passed, 1 skipped** | `python -m pytest tests/test_cascade_liveness_contract.py tests/test_operator_record_redeem.py tests/test_redeem_cascade_liveness.py tests/test_migration_redeem_operator_required.py` in 0.79s. |

**Residue check (other findings — PR-I scope was F14+F16 only, so unfixed expected):**

| # | Status | Delta vs Run #5 | Note |
|---|---|---|---|
| F2 decision_id NULL | **STILL-OPEN, WORSENING** | 693 → **1518** NULL rows (+825 in ~24h burn) | PR-A not yet landed; `src/engine/evaluator.py:1535` `log_selection_hypothesis_fact()` call still omits `decision_id=` kwarg. See F21 below. |
| F4 stranded world.market_events_v2 | UNCHANGED | 2112 rows; no growth | Confirms PR #121 fix held; awaits PR-A purge migration. |
| F5 lock storm | NOT-RE-PROBED | — | Out of Run-6 budget; recommend separate health check. |
| F12 ghost trade tables on world.db | UNCHANGED | rows=0 on all 6 shells | Awaits PR-F migration. |
| F15 settlements vs settlements_v2 | **STILL-OPEN, STATIC-DRIFT** | 5582 → 5599 (+17); 3999 → 4016 (+17); **gap 1583 stable** | Both writers tick in lockstep; the 1583-row gap is historical, not live divergence. PR-J spec stands. |
| F17 calibration trapdoor | UNCHANGED | 0 rows | Flag still off; PR-L spec stands. |
| F18 observation_instants split | **STILL-OPEN, LEGACY-GROWING** | legacy 906081 → **906873** (+792); v2 1835645 (essentially unchanged) | Legacy writer still active. See F21 below. |
| F19 cross-DB market_events_v2 | **STILL-OPEN, TRADES-DB-WRITER-ACTIVE** | forecasts 9914 → 10541 (+627); trades 7326 → **7953** (+627); world 2112 (stable) | Trades-DB writer is active in lockstep with forecasts-DB writer. See F22 below. |
| F20 ensemble_snapshots legacy ghost | UNCHANGED | 116 rows | Awaits PR-F extension. |

---

## §3 New Findings (Run #6)

### F21 · SEV-1 LIVE · `observation_instants` legacy writer is still active (Cat-H)

**Claim**. `state/zeus-world.db.observation_instants` (legacy) grew from 906,081 → 906,873 rows (+792) between Run #5 (2026-05-16) and Run #6 (2026-05-17). Run #5 flagged this table as "drop after migration" (F18) on the assumption it was no-longer written. That assumption is **false**: live writers exist.

**Evidence**.
- Live row count delta (live world.db): `+792` rows since Run #5; `MAX(id)=2108893` vs `COUNT=906873` (autoincrement gap suggests heavy prior deletes; live growth confirmed).
- Writers in src/scripts (grep `INSERT (OR ...)? INTO observation_instants`):
  - `src/data/hourly_instants_append.py:229` — `INSERT OR REPLACE INTO observation_instants` (production writer)
  - `scripts/backfill_hourly_openmeteo.py:241` — `INSERT OR IGNORE INTO observation_instants` (backfill script)
- Parallel v2 writer: `src/data/observation_instants_v2_writer.py:417` (canonical per Run #5 conclusion).

**Why it matters**.
- F18's fix spec was "grep readers and drop the legacy table." This is wrong-order: live **writers** must be cut over first. If the legacy table is dropped while `hourly_instants_append.py` keeps firing, daemon raises `OperationalError: no such table`.
- Future audits will silently mis-classify legacy as "dead orphan" because schema-only checks don't see live writes.

**Fix spec**.
1. Promote `hourly_instants_append.py` write to `observation_instants_v2_writer.py` (canonical-v2 path) or document why dual-write is needed.
2. Add CI antibody `tests/test_no_legacy_observation_instants_writes.py` that greps src/scripts for `INSERT INTO observation_instants(?![_v2])` and fails closed.
3. Only after writer cutover, schedule F18 drop migration.

**Owner-hint**. data team / observation ingest.
**Karachi 5/17 risk**. NONE (Karachi reads forecasts.db, not world.db.observation_instants).
**PR**. PR-N (new).

---

### F22 · SEV-2 SHADOW-WRITE · `market_events_v2` is being written on TWO DBs in lockstep (Cat-F+H)

**Claim**. `market_events_v2` rows grew +627 on BOTH `state/zeus-forecasts.db` (9914 → 10541) AND `state/zeus_trades.db` (7326 → 7953) between Run #5 and Run #6, while the canonical reader (per Run #5 F19) is on forecasts.db. The trades-DB writer is active and unintended.

**Evidence**.
- Writers:
  - `src/state/db.py:3635` — `INSERT INTO market_events_v2 (...)` (likely the production path; needs callee DB-routing audit).
  - `src/data/market_scanner.py:627` — `INSERT OR IGNORE INTO market_events_v2`; the connection at L610 is raw `sqlite3.connect(str(resolved_path), timeout=30)` — bypasses `get_forecasts_connection()` / `get_trade_connection()` helpers. Whatever `resolved_path` is configured to becomes the destination.
- Lockstep growth (+627 on both DBs since Run #5) suggests dual-write or one writer firing the same SQL on both connections.
- F19's recommendation was "drop the trades-DB shadow." Doing so now would crash any live writer pointing there.

**Why it matters**.
- Same wrong-DB pattern as F4 (PR #121 fix): a writer using a raw `sqlite3.connect` bypasses the typed connection helpers and routes silently to the wrong DB.
- Schema is identical on both DBs (per Run #5 F19 enumeration), so writes succeed without error — exactly the silent-corruption profile.

**Fix spec**.
1. Audit `src/data/market_scanner.py:610` — assert `resolved_path` is forecasts.db; if so, replace raw `sqlite3.connect` with `get_forecasts_connection()`.
2. Add registry antibody asserting `market_events_v2` schema present on `db:forecasts` ONLY (extension of PR-F).
3. Once writer is repaired, schedule PR-F drop on `db:trades`.

**Owner-hint**. data team / market_scanner.
**Karachi 5/17 risk**. NONE.
**PR**. PR-F (extend) — same root pattern as F19/F20/F12.

---

### F23 · SEV-1 ARCH · Migration runner is bare — no `_migrations_applied` ledger, no versioned runner (Cat-F new sub-pattern)

**Claim**. `scripts/migrations/` contains exactly `__init__.py` + `202605_add_redeem_operator_required_state.py`. No migration tracker table, no `migrate` CLI, no `make migrate` target. PR #126's R1 review-fix ship-blocker (Codex P1 #2 + G5c) was directly caused by this gap: R1 added forecasts.db to default targets, R2 had to remove it because the operator-driver had no way to express "this DB has a different schema sentinel."

**Evidence**.
- `ls scripts/migrations/` → `__init__.py`, `202605_add_redeem_operator_required_state.py`.
- `grep -rn "_migrations_applied|migration_log|schema_migrations" src/` → 0 hits (no in-DB ledger).
- Migration is a standalone Python script; operator must remember to invoke per-DB before deploying new code; no record of "this migration ran on this DB at this time."
- R2 commit message confirms the pattern: "G5c opus final-audit caught SHIP-BLOCKER on FA3 migration cross-DB user_version mismatch" — caught by human review, not automated CI.

**Why it matters**.
- Next migration repeats the trap. Multi-DB writes need a per-DB ledger to encode "this DB applies this migration, this DB does not."
- Re-running a migration after partial success is undefined behavior (idempotency by accident, not contract).
- Operator-deployed migrations have no audit trail; future incident response cannot answer "when did we bump user_version=4 on world.db?"

**Fix spec**.
1. Add `_migrations_applied` table per DB:
   ```sql
   CREATE TABLE IF NOT EXISTS _migrations_applied (
       migration_id TEXT PRIMARY KEY,
       applied_at TEXT NOT NULL,
       applied_by TEXT NOT NULL,
       schema_version_before INTEGER,
       schema_version_after INTEGER
   );
   ```
2. Add `scripts/migrate.py` CLI:
   - Discover all `scripts/migrations/YYYYMM_*.py` modules.
   - Per-DB: apply only if `migration_id NOT IN (SELECT migration_id FROM _migrations_applied)`.
   - Per-migration module declares `TARGETS: list[Literal["trades","world","live","forecasts"]]` (the missing primitive that bit R1).
3. Add `tests/test_migration_module_targets_declared.py` antibody: every `scripts/migrations/*.py` must export `TARGETS`.

**Owner-hint**. ops / migrations.
**Karachi 5/17 risk**. NONE (immediate); HIGH (next migration cycle).
**PR**. PR-O (new).

---

### F24 · SEV-1 LIVE · F2 `decision_id NULL` regression has accelerated (+825 rows / 24h) (Cat-A worsening)

**Claim**. F2 (Run #1) reported 506/506 NULL `selection_hypothesis_fact.decision_id`; Run #5 reported 693/693 NULL; Run #6 measures **1518/1518** NULL — i.e. +825 NULL rows in ~24 hours (mean burn ≈ 34 NULL rows/hr). PR-A spec is fully written but PR has not landed.

**Evidence**.
- `sqlite3 state/zeus_trades.db "SELECT SUM(decision_id IS NULL), COUNT(*) FROM selection_hypothesis_fact"` → `1518|1518` (100% NULL).
- Live call site: `src/engine/evaluator.py:1535` `result = log_selection_hypothesis_fact(...)` (no `decision_id=` kwarg).
- Default in `src/state/db.py` writer signature: `decision_id=None`.

**Why it matters**.
- Lineage join key 100% broken for the entire history; the Karachi position post-mortem (which the operator will run T+24h) cannot trace from `position_events` to `selection_hypothesis_fact` to `decision_log`. Operator response capability degraded for 5/17 settlement.
- Burn rate is monotonic — every Zeus tick adds NULL rows. Delaying PR-A inflates the historical hole.

**Fix spec**. As per FINDINGS_REFERENCE.md F2 row (no change): make `decision_id` positional-required; raise `ValueError` on falsy; add `tests/state/test_lineage_join_keys.py`. Document the 1518 historical NULL rows in `current_data_state.md` as one-time hole.

**Owner-hint**. data team / lineage.
**Karachi 5/17 risk**. **DIRECT** (post-mortem capability).
**PR**. PR-A (existing — should ship NEXT, before any further trading-week starts).

---

## §4 State-Machine Inventory — Delta vs Run #5

Run #5 reported 11 lifecycles: 8 ALIVE, 2 DEAD, 1 HALF-DEAD.

Post PR #126:

| Machine | Run #5 state | Run #6 state | Reason |
|---|---|---|---|
| `settlement_commands` (REDEEM cascade) | DEAD (F14) | **HALF-ALIVE** | `_redeem_submitter_cycle` exists + ticks; but adapter in `REDEEM_DEFERRED_TO_R1` stub means cascade always routes to `OPERATOR_REQUIRED` (designed-state, needs operator CLI). Auto-cascade to chain DEFERRED to PR-I.5. |
| `wrap_unwrap_commands` | DEAD-LATENT (F16) | **LIVENESS-GUARDED-DEAD** | Poller asserts emptiness; no transitions; correct until Z5 pUSD migration. |
| `selection_hypothesis_fact` (lineage join) | HALF-DEAD (F2) | **HALF-DEAD-WORSENING** | See F24. |
| Others (8) | ALIVE | ALIVE | Re-probed where cheap; no regression. |

**Run #6 total**: 8 ALIVE / 1 GUARDED-DEAD / 2 HALF-ALIVE (one operator-bridged, one lineage-broken).

---

## §5 Updated Cross-Run Executive Ranking (top 5)

1. **F24 (F2 worsening)** — SEV-1, Karachi DIRECT post-mortem risk, burn rate 34/hr. **Ship PR-A next.**
2. **F14-residue (REDEEM_DEFERRED_TO_R1 stub)** — SEV-0-down-to-SEV-1, Karachi DIRECT. Operator CLI is the gap-filler; runbook ready. Karachi will fire as `OPERATOR_REQUIRED` at T-0; operator must invoke `python -m scripts.operator_record_redeem 0xc5faddf... 0x<tx_hash>` at T+1h. PR-I.5 (web3 wiring) closes this for future positions.
3. **F22** — SEV-2 silent dual-write. Same root pattern as F4 PR #121 fix (`sqlite3.connect` bypasses helpers).
4. **F23** — SEV-1 architectural; no immediate risk but materially raised PR #126's R1/R2 ship-blocker probability.
5. **F21** — SEV-1 because dropping the legacy table without writer cutover would crash daemons.

---

## §6 LEARNINGS Update Suggestions

(Recommend `LEARNINGS.md` Cat-F and Cat-K appendices.)

1. **Cat-F** — "Migration target declaration is a missing primitive." PR-I R1 review-fix ship-blocker happened because the migration script couldn't declare per-DB applicability. Adding `TARGETS: list[Literal[...]]` to every migration module is a no-cost antibody for future SCHEMA_VERSION bumps that touch only some DBs.
2. **Cat-K (cascade-liveness)** — "Half-dead is a legitimate stable state if operator-CLI-bridged." F14 was reported as DEAD; the fix landed as HALF-ALIVE with operator-CLI substitute. Future audits should treat `terminal_states_with_operator_action` (per `cascade_liveness_contract.yaml`) as ALIVE — not DEAD — provided the runbook+CLI are reachable.
3. **Cat-H** — "Legacy-shadow audits must distinguish ROW-COUNT-STATIC from WRITER-STATIC." F18/F19 missed live writers because we audited row counts at one moment; live deltas across runs are the real signal.

---

## §7 AUDIT_HISTORY Append (Run #6 retrospective)

(Recommended append; to be written in commit.)

- **Operator pretext**: "Solved and PR merged. Dispatch subagent to verify... fresh Run #6." PR not named — autodetected.
- **Baseline shift**: Run #5 baseline `a924766c8a` → Run #6 effective baseline `acaae2c242` (76 commits, PR #126 the only significant audit-relevant landing).
- **Fix verified**: F14 (HALF-ALIVE), F16 (LIVENESS-GUARDED-DEAD), cascade contract deployed.
- **New findings**: 4 (F21, F22, F23, F24).
- **Test posture**: 33 passed, 1 skipped for PR-I test suites; full suite NOT re-run (out-of-scope for audit).
- **Token economics**: heavy upfront context-loading (SKILL+LEARNINGS+HISTORY+FINDINGS_REFERENCE ≈ 900 lines) before audit work; ~40% of session budget. Recommend SKILL.md Boot-protocol Step 1-3 add a "if FINDINGS_REFERENCE exists, read THIS FIRST and lazy-load others" optimization.

---

## §8 Karachi 5/17 Status (Post-Fix)

- **Position**: `c30f28a5-d4e` Karachi 2026-05-17 ≥37°C, condition_id `0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae`, shares=1.5873, phase=`day0_window`, settle_status=`partial` (partial-settle event at 2026-05-16T19:00 UTC noted).
- **Live daemons**: PIDs 44087 (live-trading, 55min uptime), 10397 (forecast-live, 16h), 14356 (riskguard, 20h), 34316 (data-ingest, 17h) — all running.
- **Cascade pollers**: confirmed active in `logs/zeus-live.err`.
- **T-0 (12:00 UTC) expectation**: Polymarket settles; harvester → enqueues settlement_commands row → `_redeem_submitter_cycle` picks up within 5 min → adapter returns `REDEEM_DEFERRED_TO_R1` stub → row transitions to `REDEEM_OPERATOR_REQUIRED` → `[REDEEM_OPERATOR_REQUIRED]` warning fires in `logs/zeus-live.err`.
- **T+1h operator action**: `python -m scripts.operator_record_redeem 0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae 0x<tx_hash>` (where `<tx_hash>` is whatever the Polymarket UI claim returns).
- **T+24h escalation**: if `REDEEM_OPERATOR_REQUIRED` row aged >24h with no CLI invocation → Path C trigger per SCAFFOLD §I.4.
- **Manual fallback (§3 runbook)**: unchanged; remains the backup if cascade halts at any step.

**Dollar exposure**: $0.59. Runbook value remains procedural, not capital-protective.

---

## §9 Anomalies / Notes

- `state/zeus-live.db` user_version=4 confirms the DB is part of the migration set — undocumented in PR-I commit messages which speak only of zeus_trades + zeus-world. Could be benign (live DB might mirror trades schema for online queries) but worth a 1-line audit-of-audit append.
- `M_BOOT_FRESH` grep returned empty for `Added job "redeem_submitter` etc — that's because the daemon booted ~55 minutes before this audit, and APScheduler's "Added job" log line happens once at boot and rotated out of `tail -100`. Verified via the `Running job "_redeem_submitter_cycle"` lines instead.
- macOS `sqlite3` does not support `-uri` flag; read-only access via `file:?mode=ro` URI must use a different approach in scripts (e.g. open file copy or use Python `sqlite3` module with URI). Updating Run #6 probes to use plain paths is a one-time concession; future audits should standardize.
