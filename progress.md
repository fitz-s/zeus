# progress.md

## Mission
Close Zeus runtime spine so lifecycle, attribution, execution, and risk surfaces are fail-closed, durable, and operator-truthful before any signal expansion.

## Non-Negotiables
- runtime spine first
- no signal complexity expansion first
- fail-closed pending/live
- Day0 terminal phase for all positions
- ExitContext hardening
- canonical ledger / durable event spine
- strategy/execution-aware risk

## Current Truth Map
- Runtime spine today: `cycle_runner.run_cycle()` orchestrates pending reconciliation -> chain sync -> quarantine timeout -> monitoring -> discovery -> portfolio/tracker save -> decision artifact -> status summary.
- Authority sources today:
  - `positions-*.json` / `PortfolioState` = practical runtime authority for open positions
  - chain API + `chain_reconciliation.py` = external truth for live holdings, but incomplete around `pending_tracked`
  - `decision_log.artifact_json` = lossy cycle summary, not canonical stage ledger
  - `chronicle` = append-only event log, but not comprehensive for exits/execution
  - `strategy_tracker-*.json` = derived attribution surface, not authority
  - `status_summary-*.json` = operator mirror only
  - Venus / OpenClaw = outer operator surface reading status/control surfaces; not Zeus state authority
- Known lossy points:
  - `cycle_runner` / `cycle_runtime` drops rich evaluator output at trade/no-trade boundary
  - exit decisions are built from partial caller context; many optional protections are dormant
  - harvester learning contexts still lean on open portfolio path instead of full evaluated universe / durable ledger
  - strategy tracker derives semantics from mixed event shapes and stale statuses
- Known fail-open points:
  - `pending_tracked` is skipped by chain reconciliation, so chain truth cannot rescue status API failure
  - quarantine mostly records anomalies; it does not reliably tighten entry behavior or force authoritative resolution
  - Day0 is still partly a discovery mode instead of mandatory terminal phase for all positions
  - RiskGuard is still portfolio-averaged and settlement-row-limited
- Currently preserved runtime teeth:
  - held-side/native-side typed probability contracts
  - mode-isolated process state via `state_path()`
  - live exit lifecycle state machine in `exit_lifecycle.py`
  - monitor-first cycle ordering
- Current blockers:
  - no canonical position event ledger
  - distributed raw-string lifecycle transitions
  - incomplete ExitContext contract
  - dirty attribution inputs for tracker/riskguard

## Locked Decisions
- Position runtime source of truth for open positions remains `PortfolioState` until canonical ledger/event spine is implemented.
- `status_summary` and `strategy_tracker` are derived views, not authority.
- `decision_log` / `chronicle` must evolve toward stage-level durable records rather than remain cycle-summary-only.
- Exit decisions must move toward a required `ExitContext` contract; missing critical fields should fail closed or explicitly abort the exit.
- Day0 is treated as terminal lifecycle phase work, not optional strategy embellishment.
- Venus/OpenClaw remain the outer operator environment; Zeus must expose truthful status/control surfaces, not fake teeth.
- StrategyTracker stays a coarse derived projection until authoritative event readers land; runtime may call only `record_entry`, `record_exit`, and `record_settlement` on it, and must not invent a separate `record_trade_result` contract.

## Active Workstreams
- lifecycle-planner: lifecycle authority, pending/live rescue, quarantine behavior, Day0 terminal phase, ExitContext wiring
- Agent B: durable decision/execution/exit/settlement records, execution telemetry, learning-source migration
- Agent C: invariants, adversarial regression, StrategyTracker/RiskGuard cleanup, fake-teeth deletion

## Recent Deltas
- Main model read core spine files and `zeus框架优化.md` to establish initial truth map before delegation.
- Main model verified the real runtime write paths: `cycle_runtime.py` is the orchestration loss point, `PortfolioState` is still open-position authority, `decision_log` is cycle-summary oriented, `chronicle` is append-only but sparse, `strategy_tracker` is derived and semantically dirty, `RiskGuard` is still settlement-row/portfolio averaged.
- Shared team files initialized and confirmed as collaboration authority: `progress.md`, `task.md`.
- Existing `PLAN.md` now explicitly tracks the runtime-spine hardening lane.
- lifecycle-planner froze a planning contract for T3: `Position.state` owns entry/hold/terminal lifecycle only, `exit_state` owns live sell-order lifecycle only, `chain_state` owns reconciliation confidence only. Proposed transition owners: entry fill reconciliation (`reconcile_pending_positions` / `fill_tracker`) for `pending_tracked -> entered|voided`; chain reconciliation for confidence updates plus `holding` normalization only; exit_lifecycle for all `exit_state` changes; close/void helpers for terminal `settled|voided`; future T6 owns `holding <-> day0_window` and T7 owns required `ExitContext` fail-closed gate.
- lifecycle-planner flagged current leaks: raw string transitions spread across `portfolio.py`, `cycle_runtime.py`, `chain_reconciliation.py`, `fill_tracker.py`, and `exit_lifecycle.py`; monitoring still iterates by permissive skips instead of explicit lifecycle gating; quarantine expiry changes only `chain_state`, so protective runtime behavior is not yet encoded.
- Agent A contract delta (frozen before implementation): chain reconciliation may rescue `pending_tracked` into `entered` when the held token is visible on chain; any `quarantined` or `quarantine_expired` position blocks new entries for the cycle; live exits must be invoked with a complete `ExitContext` object rather than partial optional args.
- Agent B shipped the first durable event-spine slice without replacing replay/open-position authority: added `position_events` in `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` and wired stage-level emission from existing writers. Current event contract uses `runtime_trade_id` as identity and records `POSITION_ENTRY_RECORDED`, `ORDER_ATTEMPTED|ORDER_FILLED`, `POSITION_LIFECYCLE_UPDATED`, `POSITION_EXIT_RECORDED`, and `POSITION_SETTLED`.
- Runtime wiring delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` now emits execution telemetry immediately after `log_trade_entry`; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/execution/harvester.py` now appends a durable settlement event alongside chronicle + decision_log settlement writes. Open-position authority remains `PortfolioState`; `decision_log`/`chronicle` remain intact and are now supplemented rather than replaced.
- Touched files for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/src/execution/harvester.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_db.py`.
- Blockers / follow-up for next slice: exit_lifecycle still does not emit sell-order placement / retry / fill-check events, so T10 is only partially covered; harvester learning source still reads open portfolio snapshot contexts, so T11 is not started; `decision_log` settlement artifacts and `position_events` are parallel sources and need a reader/owner contract before replay or tracker migration consumes them.
- Agent A implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/chain_reconciliation.py` now rescues `pending_tracked` into `entered` when the held token is present on chain, populating shares/cost/condition/entered_at and marking `entry_fill_verified`; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runner.py` now blocks discovery when any position is `quarantined` or `quarantine_expired`; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/execution/exit_lifecycle.py` now requires an `ExitContext` dataclass and `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` is wired to pass it explicitly.
- Agent A touched tests: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` covers chain rescue + `ExitContext` callsites; `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_runtime_guards.py` covers quarantine blocking new entries.
- Agent A unresolved edges: chain rescue currently upgrades local state but does not yet emit DB lifecycle/tracker events for rescued fills; quarantine protection currently blocks new entries but does not force a dedicated administrative exit path beyond existing monitoring/timeout behavior; `ExitContext` currently carries sell-side essentials only and may need explicit trigger/divergence fields if exit-lifecycle becomes the durability owner.
- Agent A status: T4/T5/T7 moved from TODO to IN_PROGRESS with a first real closure slice landed; T6 untouched.
- Agent C implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/strategy_tracker.py` now declares itself attribution-only and no longer publishes derived `fill_rate`; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/metrics.py` and `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py` now remove settlement-row win-rate scoring and persist only authoritative settlement calibration plus drawdown-derived guardrails.
- Agent C touched tests: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_riskguard.py` drops win-rate expectations; `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now asserts pending rescue, quarantine-expired blocking semantics, and required `ExitContext.best_bid` presence.
- Agent C blockers: T13 still lacks canonical strategy/execution-aware authority because `query_settlement_records()` reads cycle-summary `decision_log` artifacts instead of `position_events`.
- Agent C follow-up delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` no longer invents a fake `record_trade_result()` tracker contract for deferred sell fills; it now reuses `record_exit()` and `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_runtime_guards.py` locks that invariant in place.
- Agent C current blocker after that simplification: T13 remains blocked on authoritative event readers, and T14 still lacks Day0 terminal-phase adversarial coverage because T6 is not landed yet.
- Agent C touched files for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/strategy_tracker.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/metrics.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/chain_reconciliation.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_riskguard.py`, `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py`.
- Agent C status: T15 has a landed deletion/shrink slice; T16 has a first semantics cleanup slice; T13 remains blocked on authoritative event readers rather than cycle-summary artifacts.
- Agent C follow-up delta: the next highest-risk remaining fake authority surface was still `StrategyTracker.summary()` publishing per-strategy `win_rate` even after tracker authority was downgraded. `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/strategy_tracker.py` now removes the derived `win_rate()` helper and no longer emits `win_rate` in summaries; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/metrics.py` also drops the dead `evaluate_win_rate()` helper so no dormant win-rate gate remains to be mistaken for live protection.
- Agent C touched test for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_truth_layer.py` now locks that tracker summaries expose only trade count and PnL, not win-rate authority semantics.
- Agent C locked decision delta: until authoritative event readers land, `StrategyTracker` may expose coarse attribution counts/PnL only; per-strategy win-rate is misleading and must not appear on runtime/operator surfaces.
- Agent C blocker after this slice: RiskGuard still consumes settlement rows via `query_settlement_records()` from lossy `decision_log` artifacts, so the next risk-gate illusion requires the `position_events` reader/owner contract before the consumer can switch safely.
- Agent B locked decision delta: RiskGuard settlement consumers should prefer stage-level `position_events` with `event_type='POSITION_SETTLED'` and only fall back to legacy `decision_log` settlement blobs when no canonical stage events exist yet. `decision_log` stays backward-compatible for replay/cycle summaries, but settlement truth for risk should migrate toward stage events first.
- Agent A locked decision delta: until full T6 phase authority lands, monitoring must compute real per-position `hours_to_settlement` from target date + city timezone and feed that into exit evaluation; a hardcoded 24h monitor context is fail-open and forbidden.
- Agent A audit/fix delta: the next fail-open gap was a real Day0 bypass — monitoring always passed `hours_to_settlement=24.0`, which silently disabled settlement-imminent and near-settlement logic and prevented automatic `day0_window` transition for positions already near target date. `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` now computes real hours-to-settlement per position and promotes `entered|holding -> day0_window` inside monitoring when the remaining window is <= 6h.
- Agent A touched test: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now locks the Day0 transition invariant and verifies exit evaluation receives the real sub-1h settlement distance.
- Agent A remaining blocker after this slice: `day0_window` now exists as a real lifecycle transition, but refresh still dispatches primarily from `entry_method`; full T6 still requires explicit Day0-phase-specific refresh/exit semantics for all held positions, not just correct timing context.
- Agent A locked decision delta: once a position enters `day0_window`, refresh provenance is phase-owned, not entry-owned. `entry_method` remains immutable historical provenance, but monitor refresh must dispatch through `DAY0_OBSERVATION` semantics for any `day0_window` position.

## Open Risks
- lifecycle contract mismatches across `portfolio.py`, `cycle_runtime.py`, `chain_reconciliation.py`, `exit_lifecycle.py`
- migration risk if ledger/event schema lands without owner clarity
- tests currently emphasize some cross-module semantics but not the new pending/day0/exit-context closure yet
- operator mirrors may still overstate system completeness

## Next Baton Pass
- Agent A owns truth-map compression for lifecycle authority and proposes locked contracts for PositionState, pending rescue, quarantine, Day0, ExitContext.
- Agent B must align event names, identity fields, and decision/execution ledger schema with Agent A before broad implementation.
- Agent C must convert A/B contracts into adversarial invariants and identify fake teeth / bad metrics to delete.

## Do-Not-Do
- do not expand signal/model complexity
- do not treat paper fills as execution truth
- do not use strategy tracker as capital-allocation authority yet
- do not ship UI/control-plane cosmetics as substitutes for runtime closure
- do not preserve dirty semantics for compatibility if they mislead operator or learning loops
