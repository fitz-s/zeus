# Zeus Session State u2014 2026-04-07

**Replaces:** All previous session notes for this date.  
**Authors:** pnl-tracer, pnl-auditor, isolation-architect, isolation-researcher, market-verifier  
**Branch:** `Architects`  
**Commits:** `cdc473f` (153 files, 33K insertions) + `48ef200` (AGENTS.md commit discipline rule)

## For Zero-Context Readers

Before reading this document, read in order:
1. `docs/zeus_FINAL_spec.md` u2014 the terminal architecture spec (P0-P11 + Endgame)
2. `AGENTS.md` u2014 repo governance and agent rules
3. `docs/isolation_design.md` u2014 the physical DB separation design
4. `docs/ground_truth_pnl.md` u2014 the verified P&L with full evidence chain
5. `docs/archives/traces/pipeline_trace.md` u2014 complete data flow for one trade lifecycle

Key concepts:
- **zeus.db** = LEGACY shared database (being replaced)
- **zeus-paper.db** = paper-mode trade data (physically isolated)
- **zeus-shared.db** = world data shared by both modes (weather, calibration)
- **FINAL spec** = `docs/zeus_FINAL_spec.md` u2014 the terminal architecture document
- **P7 Phase 2** = wiring exit/settlement events to canonical position_events (72 events missing)
- **K=1** = exit/settlement lifecycle was never connected to canonical data path

## Open u7591u70b9 (Uninvestigated, from end of session)

**These were discovered in the last hour and NOT investigated. Next session must check these FIRST.**

1. **`monitor_incomplete_exit_context=11`** u2014 11 positions can't get complete exit context. May be caused by DB isolation breaking cross-table reads (exit context needs both trade and shared data). Check: does the monitoring phase use `get_trade_connection_with_shared()` or just `get_trade_connection()`?

2. **`effective_bankroll=$579.58` vs `wallet=$93.68`** u2014 Paper bankroll includes unrealized gains from Atlanta tail trades. Is this correct (paper has no real wallet) or a calculation error? Check: `src/engine/cycle_runtime.py:entry_bankroll_for_cycle()` u2014 does paper mode use wallet balance or config_cap + portfolio value?

3. **`riskguard.py` reverted to `get_connection()`** u2014 isolation-researcher's subagent reverted it to make tests pass. This re-opens the contamination vector. Need to re-apply `get_trade_connection()` and fix the test mocking to work with the new function name.

4. **38 test failures mix our truth-surface tests with pre-existing failures** u2014 `test_portfolio_truth_source_is_canonical` and `test_settlement_freshness` are OUR tests correctly detecting known issues. But they're counted alongside actual pre-existing failures. Need to separate "correctly failing antibody tests" from "broken tests".

5. **`etl_recalibrate` subprocess chain untested after DB migration** u2014 daemon runs `etl_observation_instants.py`, `etl_diurnal_curves.py`, etc. as subprocesses. These now import `get_shared_connection as get_connection`. But were they tested? Does the subprocess inherit the correct Python path?

6. **Position count: 26 in DB vs 24 expected** u2014 `position_current` has 26 rows but earlier counts showed 24. Where did 2 extra positions come from? Check: are these from the Atlanta exits that got zombie-reactivated?

7. **`at` job for auto_pause still scheduled** u2014 `atq` shows job #2 at 01:49 CDT Apr 7. LIVE_LOCK already exists so it's redundant, but should be cleaned up.

---

## 1. Ground Truth P&L

**Corrected realized P&L: +$109.13** (paper mode, mark-to-market, zero slippage)

| Component | Amount | Trades | Confidence |
|-----------|--------|--------|------------|
| Settled at expiration | -$13.03 | 19 | HIGH u2014 first-principles verified, chronicle confirmed |
| Early exits (deduped) | +$122.16 | 11 | MEDIUM u2014 real Gamma API prices, paper fills |
| **Total realized** | **+$109.13** | **30** | **MEDIUM** |
| Unverifiable exits | unknown | 16 | LOW u2014 no exit_price recorded |
| Active positions (cost) | $82.32 | 24 | N/A u2014 unrealized |

### Critical Caveats

1. **Paper mode = zero slippage.** Exit prices from Gamma API, but no sell orders placed. Real fills would be lower.
2. **Atlanta dominance.** 7 Atlanta Apr 11 tail trades = +$119.66 of the +$122.16. Without Atlanta: early exits = +$2.50, total = -$10.53.
3. **Duplicate exit bug fixed.** 4 trade_ids appeared twice in `recent_exits`, inflating by $2.48. $122.16 is the corrected (deduped) number.
4. **16 exits unverifiable.** No `exit_price` recorded. Estimated -$0.5 to -$2 total impact.

### Strategy breakdown (settled only)

| Strategy | Trades | Wins | P&L |
|----------|--------|------|-----|
| center_buy | 9 | 0 | -$10.14 |
| shoulder_sell | 4 | 0 | -$4.69 |
| opening_inertia | 6 | 5 | +$1.80 |

center_buy and shoulder_sell went 0-for-13. opening_inertia 5-for-6 but one losing position (-$13.07) wiped all gains.

**Source:** `docs/ground_truth_pnl.md` (supersedes chronicle display values)

---

## 2. Architecture Changes

### Physical DB Separation (Phase 1 u2014 COMPLETE)

**Problem:** Shared `zeus.db` with `env` column allowed both paper and live daemons to:
- Write to the same tables with only convention-based filtering
- Independently discover and enter the same markets (phantom parallel positions)
- Contaminate each other via NULL env values (12 NULL rows leaked through filters)

**Solution:** Three-database architecture

| DB | Path | Contents | Mode access |
|----|------|----------|-------------|
| `zeus-paper.db` | `state/zeus-paper.db` | trade_decisions, position_events, position_current, chronicle, decision_log, outcome_fact, opportunity_fact | Paper daemon only |
| `zeus-live.db` | `state/zeus-live.db` | Same tables | Live daemon only |
| `zeus-shared.db` | `state/zeus-shared.db` | settlements, observations, ensemble_snapshots, calibration_pairs, platt_models, forecast_skill, market_events, token_price_log, shadow_signals, diurnal_curves, all ETL/calibration data | Both daemons read/write |
| `zeus.db` | `state/zeus.db` | LEGACY u2014 unchanged, kept for backward compat | LEGACY only |

**Connection factory** (`src/state/db.py`):
- `get_trade_connection(mode=None)` u2192 routes to zeus-paper.db or zeus-live.db
- `get_shared_connection()` u2192 zeus-shared.db
- `get_trade_connection_with_shared(mode=None)` u2192 trade DB with shared attached as `shared.*`
- `get_connection()` u2192 LEGACY, routes to zeus.db, to be removed in Phase 4

**Guarantee level:** UNCONSTRUCTABLE for trade state. Different file paths = OS-level isolation. A paper daemon cannot write to zeus-live.db by construction.

### Discovery Gate (Phase 1 u2014 COMPLETE)

**Problem:** Both paper and live daemons independently discovered the same markets.

**Solution:** Live daemon = monitor + exit only. No discovery. Entries via `promote_to_live(trade_id)` from paper.

### Call site migration (Phase 2 u2014 IN PROGRESS, uncommitted)

All 47 `get_connection()` call sites classified and migrated:
- **TRADE (migrated):** chronicler.py, status_summary.py, portfolio.py, harvester.py, backfill_trade_decision_attribution.py, backfill_recent_exits_attribution.py, audit_replay_completeness.py, capture_replay_artifact.py, force_lifecycle.py (partial)
- **SHARED (migrated):** replay.py, diurnal.py x3, control_plane.py x2, market_fusion.py, ensemble_signal.py, all ETL scripts (20+ files)
- **BOTH (migrated):** harvester.py, replay.py, backfill_semantic_snapshots.py, audit_replay_fidelity.py, force_lifecycle.py
- **SKIP:** db.py init_schema, fill_tracker.py deps.get_connection() (dependency injection)
- **COMMITTED (cdc473f)** u2014 see WARNING at top

---

## 3. Fixes Applied This Session

| Fix | File | Description |
|-----|------|-------------|
| DAY0_EXIT_GATE stale probability | `src/state/portfolio.py` | When day0_active and fresh_prob_is_fresh=False: waive INCOMPLETE, substitute effective_prob = min(stale, bid*1.1) |
| Stale prob forward-edge substitution | `src/state/portfolio.py` | Cap model at market price to neutralize positive illusion when stale |
| Duplicate exit dedup | `src/state/portfolio.py` | Deduplicate recent_exits by trade_id (first occurrence wins) |
| SD-1 harvester fix | `src/execution/harvester.py` | (Details in isolation_design.md) |
| Connection factory | `src/state/db.py` | Added get_trade_connection(), get_shared_connection(), get_trade_connection_with_shared() |
| 47-site call site migration | multiple files | get_connection() u2192 appropriate new function across src/ and scripts/ |

**Antibody tests added:**
- `tests/test_day0_exit_gate.py` (4 tests)
- `tests/test_pnl_flow_and_audit.py` (repaired, was failing)
- `tests/test_runtime_guards.py` (repaired, was failing)

---

## 4. Known Remaining Issues

### CRITICAL (blocking live)

1. ~~All code changes uncommitted.~~ **COMMITTED** as `cdc473f` (153 files) + `48ef200` (AGENTS.md).
2. **Migration script not run.** `scripts/migrate_to_isolated_dbs.py` exists but the actual data migration (moving rows from zeus.db to zeus-paper.db / zeus-shared.db) has not been executed.
3. **Live daemon still uses zeus.db.** Until migration script runs and daemon restarts with new DB paths, the physical separation exists in code but not in runtime.

### HIGH (affects correctness)

4. **16 exits without exit_price.** Unknown P&L contribution. Estimated -$0.5 to -$2 but unverified.
5. **Atlanta Apr 11 concentration risk.** 7 tail trades = 97% of early exit P&L. If those were paper artifacts (bid/ask manipulation), real P&L = -$10.53.
6. **center_buy 0-for-9.** Strategy is systematically wrong on tail buys in settled markets. Not yet diagnosed.

### MEDIUM (tech debt)

7. **zeus.db legacy.** `get_connection()` still exists. Phase 4 removes it. Currently 3 call sites remain: db.py init_schema (infra), fill_tracker.py x2 (deps injection).
8. **No cross-mode position check.** Risk limits check only local portfolio. Live daemon can still enter a market paper already occupies (until promote_to_live gate is enforced).
9. ~~stash@{0} trap.~~ **RESOLVED** u2014 stash dropped in this session.

---

## 5. Data Integrity

### Clean
- **outcome_fact table:** 19 rows, verified against first-principles calculation (19/19 match)
- **chronicle P&L:** Chronicle `realized_pnl` matches first-principles for all 19 settled trades
- **Calibration data:** error_records and error_stats_agg rebuilt from clean IEM ASOS + OpenMeteo only (is_synthetic=1 flagged)
- **zeus-shared.db:** Created and contains shared world data tables
- **zeus-paper.db:** Created and contains paper trade data

### Dirty / Unverified
- **16 exits without exit_price:** Source unknown. May be valid exits from periods before exit price logging was implemented.
- **NULL env rows:** 12 rows in zeus.db trade tables have NULL env. These predate env-column enforcement. Not migrated to new DBs yet.
- **position_current:** projection table rebuilt from trade data; may lag actual positions until next daemon cycle.

---

## 6. Venus Sensing

### What Venus can see (as of this session)
- `state/venus_sensing_report.json` u2014 structured summary of Zeus state for Venus consumption
- `state/venus_antibody_queue.json` u2014 queue of antibody events Venus can act on
- `docs/venus_sensing_design.md` u2014 protocol for Venus to read Zeus state
- `docs/archives/reports/venus_sensing_audit_report.md` u2014 audit of sensing accuracy

### Relationship checks that exist
- P&L computed from `outcome_fact` (settled) + `recent_exits` (deduped)
- `chronicle.realized_pnl` cross-checked against first-principles
- Position exposure checked against chain positions (on-chain vs local divergence logged)

### What Venus cannot yet see
- Real-time fill confirmation (paper mode has no fills)
- Live daemon state (live not yet active)

---

## 7. Next Session Priorities

1. **COMMIT current changes.** `git add -p && git commit -m "feat: Phase 2 get_connection() migration complete"`
2. ~~Drop the stash.~~ **DONE** u2014 stash dropped in this session.
3. **Run migration script.** `python scripts/migrate_to_isolated_dbs.py` u2014 moves data from zeus.db to zeus-paper.db / zeus-shared.db
4. **Verify daemon uses new DB paths.** Restart paper daemon, confirm it connects to zeus-paper.db, not zeus.db
5. **Run full test suite post-migration.** Expect 3 pre-existing failures; any new failures = regression
6. **Diagnose center_buy 0-for-9.** Why does the strategy fail on settled markets? Is it a signal quality issue or entry threshold issue?
7. **Implement promote_to_live gate.** Prevent live daemon from entering markets paper already holds
8. **Phase 3: Remove get_connection().** After daemon migration verified, delete legacy function and remaining 3 call sites

---

## Appendix: File Change Inventory (Phase 2 migration)

All files modified (unstaged) as of session end:

**src/ files:**
`src/calibration/store.py`, `src/control/control_plane.py`, `src/data/ecmwf_open_data.py`,
`src/data/observation_client.py`, `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py`,
`src/engine/replay.py`, `src/execution/harvester.py`, `src/main.py`,
`src/observability/status_summary.py`, `src/signal/diurnal.py`, `src/signal/ensemble_signal.py`,
`src/state/db.py`, `src/strategy/market_fusion.py`

**tests/ files:**
`tests/test_diurnal.py`, `tests/test_migration.py`, `tests/test_pnl_flow_and_audit.py`,
`tests/test_run_replay_cli.py`, `tests/test_runtime_guards.py`

**scripts/ files (all ETL/calibration):**
33 scripts/ files u2014 all migrated from get_connection() to get_shared_connection() or get_trade_connection()


