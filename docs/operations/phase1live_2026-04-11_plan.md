# Zeus PLAN — Phase 1: Live Daemon Maturity
> Created: 2026-04-11 | Status: IN PROGRESS | Scope: **Large** (paradigm shift, not refactor)

## Design philosophy (the only axiom of Phase 1)

**Zeus is designed only for live.**

Every line of code, every table name, every connection function, every contract, every test that exists for any reason other than "serving live trading on real money" is either rewritten to serve live or deleted. Paper mode is decommissioned, preserved only as a frozen historical reference, and will be nuked in Phase 2.

This is not a refactor. It is a reorientation. Decisions in this plan follow one question: **does this serve live?** If yes, keep and harden. If no, delete.

## Goal

Bring live daemon to a verified-ready state where:
1. Every line of runtime code has a live-only purpose
2. Every known structural bug has a structural fix (not a defensive workaround)
3. Every live-only code path has been exercised at least once under real market/chain conditions
4. The operator can green-light a $5/5hr controlled canary with zero expected surprises

Phase 1 is **done** when: `zeus-live.db` contains a full trade lifecycle from the canary, the operator signs off that live is as mature as paper was, and we can safely start Phase 2 (delete paper code entirely).

## Context

### 3-Phase strategy (user-directed)
- **Phase 1 (this plan)** — Mature live daemon under the "design only for live" axiom.
- **Phase 2** — Delete paper code: `mode == "paper"` branches, `_paper_fill`, positions-paper.json, zeus-paper.db schema support, ZEUS_MODE env var (becomes redundant), all paper-era dual-vocab compatibility.
- **Phase 3** — Build backtest on top of existing 65%-complete infrastructure (`src/engine/replay.py` 800 LOC, `scripts/run_replay.py` 122 LOC works today). ~2-4 weeks of dedicated work. Backtest audit verdict: feasible, main blocker is market price linkage.

### Current system state (post-full-nuke 2026-04-11 23:20 UTC)
- All 5 DBs zeroed except world data tables in (current name) `zeus-shared.db`
- All paper/live state JSON files deleted
- All 4 daemons stopped; only heartbeat-sensor cron remains
- 11 fix commits on `data-improve` branch: K1/K2/K3-interim/K4 + defensive workarounds for #7/#9 + datetime normalization + ZEUS_MODE value validation
- Snapshots for rollback at `zeus-nuke-snapshots/`

### Bug status after tonight's fixes
| Bug | Status | Handled in Phase 1? |
|---|---|---|
| #1/#2 (K1 tracker projection) | ✅ Structural fix landed | Done |
| #3 (K2 accuracy rename) | ✅ Structural fix landed | Done |
| #4 (ORDER_* vocab mismatch) | ❌ Not fixed | **Tier 2.4 — full legacy vocab elimination** |
| #5 (K3 gate_drift_resolved) | ⚠️ Interim fix only | **Tier 2.5 — full GateDecision provenance** |
| #6 (trailing_loss poisoning) | ✅ Resolves downstream of #1 | Done |
| #7 (JSON fallback leak) | ⚠️ Defensive filter only | **Tier 2.1 — delete fallback entirely** |
| #8 (K4 risk.level overload) | ✅ Structural fix landed | Done |
| #9 (settlement double-emit) | ⚠️ Defensive DB-phase guard only | **Tier 2.2 — iterator from position_current** |
| #10 (position_current non-atomic) | ❌ Not audited | **Tier 2.3 — writer discipline audit** |
| Datetime tz mismatch | ✅ Structural fix landed | Done |

## Approach

Six tiers, executed in order with intra-tier parallelism when safe. **No live trading until Tiers 1-5 are 100% complete.** Tier 6 is the $5/5hr canary — operator will launch when ready, not on a schedule.

Every task must be completable independently and must end with a verifiable pass/fail criterion.

---

## Tasks

### Tier 1 — Hard blockers (must complete before any live daemon boot)

- [ ] **1.1 Live wallet as source of truth**
  - Files: `src/main.py`, `src/config.py`, `src/state/portfolio.py`, wherever Polymarket wallet balance is queryable (search `wallet_balance` in src/)
  - What: live daemon queries on-chain wallet at startup; `initial_bankroll` = floor(wallet_balance). `settings.capital_base_usd` demoted to upper-bound safety cap. If chain query fails at boot, **fail closed** — refuse to start rather than fall back to config.
  - Live-only: yes. Paper has no wallet so paper path (being deleted Phase 2) still uses config.
  - Verify: `status_summary-live.json.portfolio.initial_bankroll` = observed wallet balance ± $0.01.

- [ ] **1.2 Position size cap for canary window**
  - Files: `config/settings.json`, `src/strategy/kelly.py`
  - What: introduce `config.live_safety_cap_usd = 5`. Kelly output hard-clipped to this ceiling in live mode. Comment in config explains this is a Phase 1 maturity rail, only lifted after Tier 6 canary passes.
  - Verify: kelly sizing log shows `capped_by_safety_cap` on any proposal > $5.

- [ ] **1.3 "Nothing is shared" — zeus-shared.db purge + rename**
  - This is not just a table drop. It's a **name change** reflecting the architectural reality that there is no longer anything to share between modes because there is no longer anything except live.
  - Step A — drop 16 trade-state tables from shared DB schema entirely: `chronicle`, `decision_log`, `position_current`, `position_events`, `position_events_legacy`, `trade_decisions`, `execution_fact`, `outcome_fact`, `opportunity_fact`, `probability_trace_fact`, `strategy_health`, `replay_results`, `shadow_signals`, `risk_actions`, `selection_family_fact`, `selection_hypothesis_fact`.
  - Step B — **rename file** `state/zeus-shared.db` → `state/zeus-world.db`. This database holds weather observations, ENS forecasts, calibration models, settlement truth. That is world data, not shared data.
  - Step C — rename code symbols:
    - `ZEUS_SHARED_DB_PATH` → `ZEUS_WORLD_DB_PATH` in `src/state/db.py`
    - `get_shared_connection()` → `get_world_connection()` in `src/state/db.py` and every caller
    - `get_trade_connection_with_shared(mode)` → `get_trade_connection_with_world(mode)` and every caller
    - `"shared"` ATTACH alias → `"world"` in every connection setup
  - Step D — update `init_schema` in `src/state/db.py` so shared DB's schema no longer even defines the dropped tables. New fresh installs start clean.
  - Step E — add a CI lint check: any SQL `INSERT INTO <bare_name>` that could resolve to a table present in the world DB must be schema-qualified.
  - Live-only: naturally. Nothing has "paper and live share X" anymore because paper is gone.
  - Verify: `grep -r "shared" src/` returns zero matches except in legitimate English prose; `sqlite3 state/zeus-world.db ".tables"` shows only world-data tables (~20 tables); boot live daemon, cycle runs clean.

- [x] **1.4 Exception → auto-pause entries hook**
  - Files: `src/engine/cycle_runner.py`, `src/control/control_plane.py`
  - What: wrap entry discovery + sizing + execution loop in a try/except. Any unhandled exception in the entry path → `control.entries_paused = True` with `reason_code = "auto_pause:<exception_class>"`, emit Discord alert, continue running (monitoring/exit/settlement paths unaffected). Operator must explicitly `resume` to re-enable entries.
  - Status: implemented on `data-improve`; latest hardening in `f5025bc` makes pause source/reason current-state visible and clears stale cycle pause fields after resume.
  - Verify: `tests/test_auto_pause_entries.py`, `tests/test_runtime_guards.py::test_entries_paused_reports_block_reason`, `tests/test_pnl_flow_and_audit.py::test_inv_pause_entries_survives_control_state_refresh`, and pause-source status/healthcheck regressions.

### Tier 2 — Structural fixes (replace the defensive workarounds)

- [ ] **2.1 Bug #7 structural — delete JSON fallback in load_portfolio**
  - Files: `src/state/portfolio.py` (`load_portfolio` ~L794-862, `_load_portfolio_from_json_data` ~L670), `src/riskguard/riskguard.py` (`_load_riskguard_portfolio_truth` ~L100)
  - What: remove `_load_portfolio_from_json_data` from the load_portfolio exception path entirely. On DB projection failure: log ERROR, return empty `PortfolioState(positions=[], bankroll=<wallet>)`, set a daemon-level "portfolio loader failed this cycle" flag that suppresses new entries (monitoring still runs). positions-live.json becomes a write-only cache of the DB projection, never an authority source.
  - `_load_portfolio_json_payload` may still exist as a serialization helper but must not be used as a data authority.
  - Live-only: paper's `_load_portfolio_from_json_data` path is deleted, not refactored.
  - Verify: delete `state/positions-live.json`; boot; confirm empty portfolio loaded cleanly; grep for `_load_portfolio_from_json_data` returns zero runtime callers (test-only callers allowed).

- [ ] **2.2 Bug #9 structural — settlement iterator from position_current**
  - Files: `src/execution/harvester.py` (`_settle_positions` ~L540-750)
  - What: replace `for pos in portfolio.positions` with `SELECT trade_id, ... FROM position_current WHERE phase IN ('active', 'economically_closed', 'day0_window', 'pending_exit')`. The in-memory `portfolio.positions` object becomes a snapshot cache, NOT the source for iteration decisions. The defensive DB-phase dedup guard in `_dual_write_canonical_settlement_if_available` (commit d7858ee) stays as belt-and-suspenders but should never fire after this change.
  - Verify: unit test with a stale in-memory pos object (phase=economically_closed) while position_current says phase=settled; assert no new SETTLED event is written.

- [ ] **2.3 Bug #10 — position_current writer audit**
  - Deliverable: `docs/reference/position_current_writers.md` — exhaustive inventory of every code path that writes to `position_current`.
  - What: grep every `UPDATE position_current`, `INSERT INTO position_current`, `REPLACE INTO position_current`, and `upsert_position_current` caller. For each writer: which transaction it's in, whether it also writes a paired `position_events` row, the atomicity guarantee. Anything that writes `position_current` without a paired event in the same transaction is a bug. Fix by routing through `append_event_and_project` or `append_many_and_project` (already atomic). Any writer that genuinely cannot be atomic (e.g., startup migration) must be documented and gated behind `_canonical_bootstrap` flag.
  - Expected outcome: at most 1 non-atomic writer (the bootstrap migration); all runtime writers atomic.
  - Verify: doc exists, every writer in the doc is either atomic or explicitly bootstrap-gated, regression test confirms no drift.

- [ ] **2.4 Bug #4 "design only for live" — legacy vocabulary path deletion**
  - Under the "design only for live" axiom, the bug #4 fix is NOT "patch the reader to count differently". It is **delete the entire legacy vocabulary path** (ORDER_ATTEMPTED / ORDER_FILLED / ORDER_REJECTED / POSITION_SETTLED / POSITION_ENTRY_RECORDED / POSITION_EXIT_RECORDED / POSITION_LIFECYCLE_UPDATED). These exist solely for paper-era dual-vocab compatibility.
  - Files:
    - Writer: `src/state/db.py` `log_position_event`, `log_execution_report`, `log_settlement_event` — delete or restrict to canonical-only
    - Reader: `src/state/db.py` `_legacy_position_events_table`, `_legacy_runtime_position_event_schema_available`, `_assert_legacy_runtime_position_event_schema`, `query_position_events`, `query_settlement_events`, `query_execution_event_summary`
    - Reader: `src/riskguard/riskguard.py` `_entry_execution_summary` (~L402) — rewrite to query canonical `position_events` table with canonical event types (`POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, `ENTRY_ORDER_FILLED`, `ENTRY_ORDER_REJECTED`, `SETTLED`)
    - Reader: `src/state/decision_chain.py` `query_legacy_settlement_records` — delete or canonical-only
    - Schema: drop the `position_events_legacy` empty shell table from zeus-live.db's schema after confirming no live reader depends on it
  - Canonical vocabulary semantics:
    - `attempted` = `COUNT(DISTINCT position_id) WHERE event_type='POSITION_OPEN_INTENT'` OR equivalent intent-count metric
    - `filled` = `COUNT(DISTINCT position_id) WHERE event_type='ENTRY_ORDER_FILLED'`
    - `rejected` = `COUNT(DISTINCT position_id) WHERE event_type='ENTRY_ORDER_REJECTED'`
  - Live-only: legacy vocabulary only exists because paper used it. Deleting it matches the axiom exactly.
  - Verify: grep `ORDER_FILLED` (without `ENTRY_` prefix) returns zero matches in src/; grep `POSITION_SETTLED` returns zero matches; live daemon writes only canonical events; readers return correct counts.

- [ ] **2.5 Full K3 — GateDecision provenance**
  - Files: `src/control/control_plane.py`, new `src/control/gate_decision.py`, `src/observability/status_summary.py` (`gated_but_not_recommended` computation)
  - What: replace `strategy_gates: dict[str, bool]` with `dict[str, GateDecision]` where:
    ```python
    @dataclass(frozen=True)
    class GateDecision:
        enabled: bool
        reason_code: str  # from enum
        reason_snapshot: dict  # data that justifies the gate
        gated_at: str  # ISO timestamp
        gated_by: str  # "operator" | "auto:<rule_name>"
    ```
  - Reason codes enum: `manual_kill_for_losses`, `edge_compression`, `execution_decay`, `operator_override`, `phase_1_canary_restriction`, `unspecified`.
  - Function `reason_refuted(decision, current_data) -> bool` — per-reason-code refutation rules. Default: return False. Any automated un-gate recommendation must call `reason_refuted` and get True before emitting. Manual un-gate via explicit operator command bypasses this.
  - Verify: set a gate with `reason_code=manual_kill_for_losses, snapshot={count:0, accuracy:None}`; simulate cycle; confirm `status_summary.control.recommended_commands` is empty (not suggesting un-gate).

### Tier 3 — Live code path validation (real-world exercise)

- [ ] **3.1 First-boot dry-run runbook**
  - File: `docs/runbooks/live-phase-1-first-boot.md` (new)
  - What: operator runbook for the very first live boot. Manual steps: wallet balance verify, initial_bankroll sanity check, manual single-cycle trigger via `python -m src.main --once`, verify each subsystem reports `ok`, no orders placed. Repeat until operator is comfortable with the healthy signature.
  - Verify: operator approves runbook.

- [ ] **3.2 `_live_order` CLOB submission validated**
  - Files: `src/execution/executor.py` (`_live_order` ~L370-440)
  - What: with $5 cap active, trigger the first live entry for the first profitable edge the cycle finds. Observe: order posted, tx hash returned, Polymarket UI confirms, `position_events` records `POSITION_OPEN_INTENT` → `ENTRY_ORDER_POSTED` → `ENTRY_ORDER_FILLED`, `position_current.phase = active`.
  - Verify: manual diff of status_summary before/after confirms all expected field changes.

- [ ] **3.3 Live exit path validated**
  - Files: `src/execution/executor.py` (`execute_exit_order` ~L290-360), `src/execution/exit_lifecycle.py`
  - What: observe the first exit of a live position through the full chain: `EXIT_INTENT` → `EXIT_ORDER_POSTED` → `EXIT_ORDER_FILLED` → `position_current.phase = economically_closed`. If no natural exit triggers in the canary window, manually admin_close one small position to exercise the path.
  - Verify: tx hash recorded in `execution_fact`.

- [ ] **3.4 `clob.redeem` + `alert_redeem` validated**
  - Files: `src/execution/harvester.py` (redeem call ~L690, `alert_redeem` Discord alert from WIP)
  - What: wait for a real settlement on a live winning position. Verify: redeem tx_hash returned, USDC balance reflects claim, Discord alert fires, `settlements` row in world DB, `position_current.phase = settled`.
  - Verify: chain-side check that condition_id shows redeemed.

- [ ] **3.5 Chain reconciliation validated**
  - Files: `src/state/chain_reconciliation.py`
  - What: run chain_reconciliation at least once against a real active position. Observe `chain_state` transition `unknown` → `verified` (or `quarantine` if divergent, which would itself be a learning).
  - Verify: status_summary shows a position with `chain_state=verified`.

- [ ] **3.6 Polymarket API error matrix**
  - Files: `src/data/polymarket_client.py` or equivalent
  - What: integration test that mocks CLOB HTTP client to return 429/500/503/timeout in sequence. Assert each case handled correctly: 429→backoff+retry, 5xx→log+skip cycle, timeout→mark pending_verify + re-check next cycle.
  - Verify: test passes; no unhandled exception reaches cycle_runner.

### Tier 4 — Observability and safety net

- [ ] **4.1 Live-specific Discord alert types**
  - Files: `src/riskguard/discord_alerts.py`
  - What: add `alert_first_live_fill`, `alert_first_live_settlement`, `alert_wallet_drop_over_pct`, `alert_chain_sync_failure`, `alert_daemon_heartbeat_missed`. Distinct channels/formats so operator can filter Phase 1 signal from background noise.
  - Verify: each alert type fires at least once during Tier 6 canary.

- [ ] **4.2 Daemon heartbeat beacon**
  - Files: `src/main.py`, new `scripts/check_daemon_heartbeat.py`
  - What: daemon writes `state/daemon-heartbeat-live.json` every 60s. Cron (or separate process) checks staleness; alerts if > 5 min stale.
  - Verify: kill daemon, wait 5 min, alert fires.

- [ ] **4.3 Live operation runbook**
  - File: `docs/runbooks/live-operation.md` (new)
  - What: healthy signature reference, kill-switch procedure (one command), resume procedure, expected Phase 1 warnings (insufficient_history, etc.), alert-triggered playbooks.
  - Verify: operator can walk through a mock incident using only the runbook.

### Tier 5 — Test coverage

- [ ] **5.1 Live execution mock test**
  - File: `tests/test_live_execution.py` (new)
  - What: mock Polymarket CLOB, run `_live_order` through happy path + error modes. Assert OrderResult and position_events side effects.
  - Verify: CI pass; covers lines 370-440 of executor.py.

- [ ] **5.2 Mode isolation regression test**
  - File: `tests/test_mode_isolation.py` (new)
  - What: even though paper is decommissioned, this test protects Phase 2 refactor from accidentally re-introducing cross-mode leakage. Spawn two in-memory DBs (paper_stub, live), run a simulated trade pipeline against each, assert: zero cross-contamination, zero world DB pollution.
  - Verify: CI pass.

- [ ] **5.3 Wallet-as-source-of-truth test**
  - File: `tests/test_wallet_source.py` (new)
  - What: mock wallet query; confirm `load_portfolio` in live uses wallet as `initial_bankroll`; test success and failure paths.
  - Verify: CI pass.

### Tier 6 — The canary ($5 / 5 hour, operator-launched)

- [ ] **6.1 Pre-canary checklist**
  - All Tier 1-5 boxes checked
  - Operator has read both runbooks (3.1, 4.3)
  - Operator has kill-switch command at hand
  - Wallet balance verified
  - `config.live_safety_cap_usd = 5`
  - Strategy gates: only `opening_inertia` enabled, others gated with `reason_code=phase_1_canary_restriction`
  - Discord alert channels confirmed receiving test messages

- [ ] **6.2 Boot and first cycle**
  - `launchctl load com.zeus.live-trading.plist` + `com.zeus.riskguard-live.plist`
  - Observe first cycle status_summary write
  - Verify healthy signature per runbook

- [ ] **6.3 5-hour observation window**
  - Continuous observation: cycle completions, entries (all clipped to $5), exits, settlements, Discord alerts
  - Pass conditions: no unhandled exception, no phase mismatch, no duplicate event, no false RED, no wallet drift beyond trading PnL, all expected events recorded

- [ ] **6.4 Post-canary analysis**
  - Dump zeus-live.db + risk_state-live.db row counts and distributions
  - Run the zero-state reader smoke test pattern on post-canary data (tracker == settlement, no dups, etc.)
  - Write post-mortem: `.omx/context/phase-1-canary-postmortem-YYYYMMDDTHHMMSSZ.md`

## Exit criteria (Phase 1 → Phase 2 gate)

ALL must be true:

- [ ] Tiers 1-5 fully complete
- [ ] Tier 6 canary ran to completion with no unhandled exception and no structural regression
- [ ] At least 1 live fill, 1 live exit, 1 live settlement observed during the canary (or extended observation if market didn't provide)
- [ ] `position_events` in zeus-live.db shows complete lifecycle for at least 1 trade
- [ ] Chain reconciliation ran at least once against a real position
- [ ] `grep "shared" src/` returns zero matches in code (only English prose)
- [ ] `grep "ORDER_FILLED" src/` and `grep "POSITION_SETTLED" src/` return zero matches (legacy vocab gone)
- [ ] `grep "_load_portfolio_from_json_data" src/` returns test-only matches
- [ ] Operator signs off: "live is as mature as paper was"
- [ ] Post-mortem doc exists

## Risks / Open questions

1. **Market may not provide a full lifecycle in 5 hours.** Opening_hunt signals mature over days. If no edge materializes in canary, Tiers 3.2-3.4 can't be validated. Mitigation: extend canary to 24-48h with operator monitoring, still at $5 cap.

2. **Polymarket may rate-limit $5 orders as spam.** Mitigation: test first order manually before enabling automated cycle; if rate-limited, raise cap to $10-$20.

3. **Wallet query may fail at boot.** Per "design only for live" axiom, fail closed — do not fall back to `config.capital_base_usd`. Refusing to start is the correct behavior.

4. **Tier 2.4 (legacy vocab elimination) may surface hidden readers.** Budget: 3 days. If grep finds more than 10 reader sites or a critical path depends on legacy vocabulary, may need to split into 2.4a (writer stop) and 2.4b (reader migration).

5. **Tier 2.3 (position_current writer audit) may uncover non-atomic writers requiring rearchitecture.** Budget: 2 days. If an important writer truly cannot be made atomic without a larger refactor, document + quarantine behind a flag + defer to Phase 2.

6. **Tier 1.3 rename (shared → world) may break imports or tests.** Budget: 1 day. Straight find-and-replace, but test suite and any hardcoded path must be swept. CI run reveals all callers.

7. **Phase 1 has no hard deadline.** Operator's explicit stance: perfection before launch. Quality criteria must not be overridden by time pressure.

8. **5-hour canary is not a stress test.** Canary proves first-lifecycle correctness, not long-tail bugs. Phase 2 refactor will be done against mature-but-young live state. Some bugs will only surface after live has been running for 30+ days. Accepted risk per operator direction.

## Out of scope (for Phase 1)

- Phase 2 refactor itself (deleting paper code)
- Phase 3 backtest MVP
- Full K2 metric key registry beyond the 2 renames already done
- Performance optimization
- New strategies
- Monitor loop enhancements
- Calibration model improvements

These remain valuable but must not bleed into Phase 1.

## References

- Audit: `.omx/context/zeus-truth-contamination-audit-20260411T203446Z.md`
- Nuke runbook: `.omx/context/zeus-post-nuke-runbook-20260411T212548Z.md`
- Backtest feasibility audit: captured in session history, summary: 65% complete, 2-4 weeks to MVP, main blocker is market price linkage (Phase 3 concern)
- 11 fix commits on `data-improve`: c16ce9f (RC2 baseline) → 419b6fd → d7858ee → b3fe239 → ece2a8d → 5753f20 → d16ce74 → f04210f → 5f1a1ee → 649d1ab → ded2c92
- Snapshot: `/Users/leofitz/.openclaw/workspace-venus/zeus-nuke-snapshots/zeus-state-20260411T215102Z.tar.gz`
- Pre-full-nuke paper snapshot: `zeus-nuke-snapshots/full-nuke-20260411T215933Z/`
- Pre-full-nuke live snapshot: `zeus-nuke-snapshots/full-nuke-live-20260411T230131Z/`
- AGENTS.md — Zeus domain model (signal chain, invariants, zones)
- architecture/self_check/authority_index.md — authority hierarchy
