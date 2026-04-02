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

## Program Reset (2026-04-02)
- User explicitly dissolved the previous teammate set. Historical `repair/adversary` baton truth is no longer runtime truth and must not drive new dispatch.
- Main-thread direction is now: rebuild a **new** agent team around bounded lanes, while keeping architecture authority and final integration in the main thread.
- A second external research document (`/Users/leofitz/Downloads/外部调研.md`) was added. It does **not** override runtime-spine work; it expands the roadmap by adding a second foundational track: **clock / target semantics closure before forecast-layer learned upgrades**.
- Reset artifacts written:
  - `progress.md` now records the new integrated direction and team shape.
  - `task.md` now exposes `P0-A` through `P2-I` as the new authoritative live queue.
  - `.claude/baton_state.json` now reflects the real post-dissolution state (`solo`), not the dead historical team.
- Context policy for this round:
  - main model reads core authority docs and core runtime files directly;
  - narrow truth/function-design archaeology should go to bounded `explore` subagents;
  - durable teammates are only for implementation lane, detailed review lane, and adversarial review lane;
  - if a subagent stops returning concrete facts, close it and respawn a narrower one rather than let context sprawl.

## Integrated Direction (runtime + external research)
- Immediate program truth is now two coupled priorities, in this order:
  1. **runtime authority closure** — canonical settlement payload, `ExitContext` completeness, lifecycle transition ownership, pending/live fail-closed, Day0 terminal-phase truth, durable event spine, strategy/execution-aware protective loop.
  2. **clock / target semantics closure** — unify `time_context.py`, `day0_window.py`, and the general `EnsembleSignal` hour-selection path under one local-day / local-hour model before learning richer forecast surfaces.
- External research broadens the roadmap but does **not** justify jumping ahead to learned decision policy. Current valid sequencing is:
  - first: runtime spine + time semantics;
  - next: forecast-distribution layer (`day0` solar backbone + online residual update; day1..day7 lead-continuous mean/sigma; heteroscedastic sigma);
  - later: gate layer (`conflict`, `alpha`, dependence);
  - last: decision layer (`opening-hunt timing`, richer exit policy).
- Main architectural synthesis:
  - `zeus框架优化.md` is right that capital scaling is blocked by runtime truth, lifecycle, attribution, execution, and risk closure;
  - `外部调研.md` is right that learned upgrades are unsafe until local-day / target semantics are unified across modules;
  - therefore the program is **not** “runtime first or semantics first”; it is **runtime authority closure + time semantics closure as the shared foundation**, with learned forecast work explicitly downstream of that foundation.

## Landed This Round (post-reset, accepted)
- **P0-B contract freeze landed**
  - `ExitContext v2` is now the runtime exit-authority surface in `src/state/portfolio.py`, wired from `src/engine/cycle_runtime.py`, fed by `src/engine/monitor_refresh.py`, and consumed by `src/execution/exit_lifecycle.py`.
  - Exit authority now fails closed on stale/missing monitor inputs rather than silently accepting fallback values as “fresh enough”.
  - `buy_yes` edge exits now require a sell-side realizable bid; paper mode explicitly reuses current market price as simulated bid so paper exits are not disabled by construction.
- **Canonical settlement payload / authoritative seam landed**
  - `POSITION_SETTLED` now has an explicit canonical payload contract in `src/state/db.py`.
  - authoritative settlement rows now surface `authority_level`, `is_degraded`, `degraded_reason`, `canonical_payload_complete`, `learning_snapshot_ready`, `metric_ready`, and `required_missing_fields`.
  - `src/execution/harvester.py` now distinguishes durable/legacy/working-state snapshot sources, records dropped context rows, and refuses to feed learning from `working_state_fallback`.
  - `src/riskguard/riskguard.py` now tracks degraded settlement-row counts/readiness and fails closed (`settlement_quality_level=RED`) when only malformed authoritative rows exist.
- **P0-C time-semantics slice landed**
  - `src/signal/day0_window.py` preserves true “no remaining target-day hours” semantics instead of silently falling back to all target-day hours.
  - `src/engine/evaluator.py` now uses local target-day slicing for GFS crosscheck and rejects `CROSSCHECK_UNAVAILABLE` instead of defaulting to `AGREE`.
  - `src/data/ensemble_client.py` + `src/engine/evaluator.py` no longer fake upstream issue/valid timestamps; degraded clock metadata is now explicit.
- **Review / adversarial gate outcome**
  - detailed review initially found one truth-surface blocker (malformed canonical settlement rows being hidden) and adversarial review found four blockers (ExitContext freshness, GFS fail-open, RiskGuard false authority, harvester working-state fallback feeding learning).
  - all identified blockers were fixed before acceptance.
- **Validation evidence**
  - project venv full suite now passes: `./.venv/bin/pytest -q` → **409 passed, 3 skipped**
  - this round also kept targeted runtime/harvester/risk/time-semantics tests green while blocker fixes were applied.
- **VCS trace**
  - committed on branch `pre-live`: `d00a329` — `Close runtime authority and time-semantics blockers`
  - pushed to remote `origin/pre-live`

## P0-D Slice 1 (runtime-spine live-risk closure)
- Landed protections:
  - chain reconciliation no longer phantom-voids exit-lifecycle-owned positions in `exit_intent`, `sell_placed`, `sell_pending`, or `retry_pending` when chain truth temporarily disappears;
  - the new `exit_pending_missing` chain state now separates “missing while exit ownership is still active” from ordinary phantom/local-only semantics;
  - incomplete chain snapshots (`0` chain positions while active locals exist) no longer escalate retrying exits into `exit_pending_missing`;
  - `retry_pending` / `exit_intent` / `backoff_exhausted` positions that are already `exit_pending_missing` now resolve through explicit admin closure (`EXIT_CHAIN_MISSING_REVIEW_REQUIRED`) instead of looping forever or placing duplicate sells;
  - Day0 refresh fallback now preserves stale probability truth via `last_monitor_prob_is_fresh=False` rather than silently marking reused posterior values as fresh.
- Validation evidence for this slice:
  - targeted runtime tests: `76 passed`
  - full suite after landing the slice: `417 passed, 3 skipped`
- Review outcome:
  - detailed verification passed after the final incomplete-chain-response fix
  - adversarial review accepted the slice for merge/push
- Residual P0-D backlog after this slice:
  - Day0 still is not a full terminal-phase exit authority
  - pending/live entry verification still has split-brain ownership (`cycle_runtime` vs `fill_tracker`)
  - `day0_window` lifecycle still is not yet a durable event-owned phase

## P0-D Slice 2 (entry verification / chain authority reduction)
- Landed protections:
  - normal CLOB-filled pending entries now stamp `entry_order_id`, `entry_fill_verified=True`, and canonicalize `order_status=\"filled\"`;
  - the normal FILLED path now keeps `chain_state=\"local_only\"` instead of overclaiming `synced` before chain truth has actually confirmed the position;
  - chain reconciliation now preserves `entry_fill_verified` local positions that are still waiting for chain appearance instead of immediately voiding them as `PHANTOM_NOT_ON_CHAIN`;
  - incomplete chain snapshots (`0` chain positions with active locals) no longer escalate retrying exits or verified fresh entries into false missing-chain recovery.
- Validation evidence for this slice:
  - targeted runtime tests after the slice: `74 passed`
  - full suite after landing the slice: `419 passed, 3 skipped`
- Residual P0-D backlog after slice 2:
  - pending/live verification still has two code paths (`cycle_runtime.reconcile_pending_positions` vs `fill_tracker.check_pending_entries`) and therefore still lacks a single frozen owner;
  - Day0 still is not a full terminal-phase exit authority;
  - `day0_window` lifecycle is still not durably emitted as an event-owned phase.

## P0-D Slice 3 (pending/live verification owner contraction)
- Landed protections:
  - production `pending_tracked` verification is now explicitly delegated to `fill_tracker.check_pending_entries()` through `cycle_runtime.reconcile_pending_positions()`, removing the second inline production owner;
  - normal CLOB fill path now aligns its verification contract with chain rescue on the key fields that drive authority and lifecycle:
    - `entry_order_id`
    - `entry_fill_verified=True`
    - `order_status="filled"`
    - `entered_at`
    - `chain_state="local_only"` until actual chain appearance;
  - chain-rescue path now also updates the `trade_decisions` lifecycle surface so event spine and table-row truth no longer diverge as sharply;
  - verified fresh entries waiting for chain appearance are no longer same-cycle voided as `PHANTOM_NOT_ON_CHAIN`.
- Validation evidence for this slice:
  - targeted verification after the slice: `99 passed` on `tests/test_runtime_guards.py tests/test_live_safety_invariants.py tests/test_db.py`
  - full suite after landing the slice: `422 passed, 3 skipped`
- Residual P0-D backlog after slice 3:
  - Day0 still is not yet a true terminal-phase exit authority;
  - `day0_window` is still not a durable event-owned lifecycle phase;
  - normal fill accounting still does not fully reconcile authoritative `cost_basis_usd/size_usd` to actual execution telemetry / chain truth.

## P0-D Slice 4 (Day0 exit authority)
- Landed protections:
  - `day0_active` now actually changes exit authority inside `Position.evaluate_exit()` instead of being a passive field on `ExitContext`;
  - both `buy_yes` and `buy_no` now support a single-confirmation `DAY0_OBSERVATION_REVERSAL` path in Day0, matching the trading-rule requirement that Day0 observation overrides ENS;
  - when Day0 observation continues to support the position, the system now holds through `SETTLEMENT_IMMINENT`, divergence-panic, and flash-crash branches rather than letting those generic triggers override Day0 authority.
- Validation evidence for this slice:
  - targeted runtime/day0 tests after the slice: `84 passed`
  - full suite after landing the slice: `425 passed, 3 skipped`
  - adversarial merge gate on the narrow Day0 authority surface: **ACCEPT**
- Residual P0-D backlog after slice 4:
  - `day0_window` still is not durably emitted as an explicit event-owned lifecycle phase;
  - normal fill accounting still does not fully reconcile `cost_basis_usd/size_usd` to executed truth before chain confirmation;
  - entry execution telemetry for normal fill still remains thinner than the exit-side telemetry spine.

## P0-D Slice 5 (normal fill accounting convergence)
- Landed protections:
  - normal fill verification now updates `size_usd` / `cost_basis_usd` from executed fill economics instead of leaving planned notional in place until a later chain correction;
  - `fill_quality` is now set on the normal fill-confirmation path from executed vs submitted price;
  - normal fill confirmation now emits `ORDER_FILLED` entry telemetry through the same execution spine instead of only relying on lifecycle promotion;
  - chain reconciliation now repairs `entry_price` / `cost_basis_usd` / `size_usd` from chain truth even when share count already matches, eliminating the old “synced but still wrong notional” case.
- Validation evidence for this slice:
  - targeted accounting/runtime tests after the slice: `103 passed`
  - full suite after landing the slice: `426 passed, 3 skipped`
- Residual P0-D backlog after slice 5:
  - `day0_window` still is not yet a first-class event-owned lifecycle phase with an explicit Day0-entered timestamp/surface;
  - pending/live verification is structurally reduced, but chain rescue vs normal fill is still a deliberate two-path authority model rather than a single unified owner;
  - broader post-entry execution attribution (`entry_alpha_usd` / slippage / downstream accounting surfaces) can still be tightened later.

## P0-D Slice 6 (durable Day0 phase surface)
- Landed protections:
  - `day0_window` transitions now stamp `day0_entered_at` on the position the first time the runtime promotes a holding into Day0;
  - `update_trade_lifecycle()` now uses `day0_entered_at` as the lifecycle timestamp when persisting a `day0_window` transition, instead of reusing entry/post-order timestamps;
  - the durable lifecycle event surface now carries `day0_entered_at`, so `POSITION_LIFECYCLE_UPDATED` can answer when a position actually entered Day0 rather than only that it was once seen there;
  - chain reconciliation still preserves `day0_window`, so the phase no longer collapses between cycles before monitoring runs.
- Validation evidence for this slice:
  - targeted lifecycle/runtime/db tests after the slice: `104 passed`
  - full suite after landing the slice: `427 passed, 3 skipped`
- Residual P0-D backlog after slice 6:
  - chain rescue vs normal fill is still intentionally a two-path owner model rather than a single frozen owner;
  - broader post-entry execution attribution (`entry_alpha_usd` / slippage / downstream accounting surfaces) can still be tightened;
  - if needed later, Day0 could still be upgraded from “durably emitted phase” to an even richer explicit event taxonomy, but the missing timestamp/truth-surface problem is now closed.

## Consumer Truth Slice 1 (env-filtered authoritative readers)
- Landed protections:
  - authoritative settlement readers now accept and honor `env`, instead of scanning the shared `zeus.db` across paper/live indiscriminately;
  - `query_settlement_events()`, `query_authoritative_settlement_rows()`, `query_settlement_records()`, and `query_legacy_settlement_records()` now filter by environment, defaulting to the current runtime mode when no override is passed;
  - this closes the highest-leverage cross-env truth seam for `RiskGuard` and other settlement consumers built on the shared DB.
- Validation evidence for this slice:
  - targeted DB/RiskGuard tests after the slice: `39 passed`
  - full suite after landing the slice: `430 passed, 3 skipped`
- Residual high-value backlog after this slice:
  - broader strategy-aware / execution-aware RiskGuard behavior is still not closed;
  - some remaining operator surfaces still compress too much runtime truth into status summaries;
  - chain rescue vs normal fill remains a deliberate two-path runtime authority model even though the sharpest seams are now reduced.

## Operator Truth Slice 1 (status/failure surface)
- Landed protections:
  - `status_summary.write_status()` now exposes the runtime states operators actually need during recovery and diagnosis:
    - `chain_state`
    - `exit_state`
    - `entry_fill_verified`
    - `admin_exit_reason`
    - `day0_entered_at`
  - `_run_mode()` now writes an explicit failure status snapshot when a cycle throws instead of only logging and leaving the last success snapshot stale.
- Validation evidence for this slice:
  - targeted status/failure tests after the slice: `25 passed`
  - full suite after landing the slice: `431 passed, 3 skipped`
- Residual operator/risk backlog after this slice:
  - strategy-aware / execution-aware RiskGuard behavior is still not yet a full protective loop;
  - some operator surfaces still summarize rather than diagnose (for example strategy/execution breakdowns are still thinner than runtime internals).

## Execution Attribution Slice 1 (rejected-entry durability)
- Landed protections:
  - live rejected/cancelled entries are no longer visible only inside `decision_log`/cycle artifact; they now emit durable execution events through the same event spine as accepted entry attempts;
  - `log_execution_report()` now distinguishes `ORDER_REJECTED` from generic `ORDER_ATTEMPTED`, so entry-side execution failure is queryable as first-class telemetry rather than a missing-position side effect;
  - `execute_discovery_phase()` now emits durable execution telemetry even when an intended trade never materializes into a `Position`.
- Validation evidence for this slice:
  - targeted DB/runtime tests after the slice: `86 passed`
  - full suite after landing the slice: `433 passed, 3 skipped`
- Residual execution/consumer backlog after this slice:
  - strategy-aware / execution-aware RiskGuard behavior is still not yet a full protective loop;
  - broader post-entry execution attribution (`entry_alpha_usd`, slippage decomposition, downstream strategy/risk consumers) can still be tightened further.

## Consumer Truth Slice 2 (execution-aware RiskGuard details)
- Landed protections:
  - RiskGuard now records an `entry_execution_summary` from durable entry-side execution events (`ORDER_ATTEMPTED`, `ORDER_FILLED`, `ORDER_REJECTED`) scoped to the current env;
  - the summary includes overall attempted/filled/rejected counts plus fill-rate and the same breakdown by strategy, giving the operator and future consumers a first authoritative execution-quality surface that is not inferred from portfolio snapshots;
  - the execution summary path is schema-safe even when `position_events` is absent in isolated tests or partial DB fixtures.
- Validation evidence for this slice:
  - targeted DB/RiskGuard/runtime tests after the slice: `77 passed`
  - full suite after landing the slice: `434 passed, 3 skipped`
- Residual consumer/risk backlog after this slice:
  - RiskGuard still does not yet gate on strategy/execution deterioration; it now surfaces the evidence but remains mostly portfolio/settlement reactive;
  - broader post-entry execution attribution (`entry_alpha_usd`, slippage decomposition, downstream strategy/risk consumers) can still be tightened further.

## Execution Consumer Slice 2 (canonical execution read model)
- Landed protections:
  - `query_execution_event_summary()` now provides a canonical aggregated read model over `position_events` for entry- and exit-side execution surfaces, instead of leaving `position_events` readable only per-trade;
  - `status_summary.write_status()` now exposes an `execution` section backed by that canonical read model, so operator/diagnosis surfaces no longer have to infer execution backlog from scattered flags;
  - this makes the execution spine usable as a real consumer surface, not just a write-only audit log.
- Validation evidence for this slice:
  - targeted DB/status tests after the slice: `51 passed`
  - full suite after landing the slice: `435 passed, 3 skipped`
- Residual consumer/risk backlog after this slice:
  - strategy-aware / execution-aware RiskGuard still surfaces evidence more than it gates on it;
  - higher-order strategy/current-regime truth (edge compression, current heat by strategy, consumer-facing strategy state) still remains to be built on top of these cleaner execution and settlement readers.

## Strategy Consumer Slice 1 (current-regime strategy truth surface)
- Landed protections:
  - `status_summary.write_status()` now emits a `strategy` section derived from the actual current book plus non-admin recent exits, rather than forcing operators to reconstruct strategy state from raw positions and exit history;
  - each strategy bucket now carries at least:
    - `open_positions`
    - `open_exposure_usd`
    - `realized_pnl`
    - `unrealized_pnl`
    - `total_pnl`
  - this gives operators a first current-regime strategy surface without pretending the legacy tracker itself is authority.
- Validation evidence for this slice:
  - targeted status tests after the slice: `25 passed`
  - full suite after landing the slice: `435 passed, 3 skipped`
- Residual strategy/risk backlog after this slice:
  - strategy-aware / execution-aware RiskGuard still does not gate on this truth yet;
  - edge-compression and current-regime strategy health still need a stronger consumer than the derived tracker summary alone.

## Strategy Consumer Slice 2 (RiskGuard strategy diagnostics)
- Landed protections:
  - RiskGuard now records `strategy_tracker_summary`, `strategy_edge_compression_alerts`, and `strategy_tracker_accounting` in its details surface;
  - this gives the protective loop a direct current-regime strategy diagnostic surface alongside settlement- and execution-based truth, without falsely upgrading the tracker itself into authority.
- Validation evidence for this slice:
  - targeted RiskGuard tests after the slice: `17 passed`
  - full suite after landing the slice: `436 passed, 3 skipped`
- Residual strategy/risk backlog after this slice:
  - RiskGuard still surfaces strategy/execution evidence more than it gates on it;
  - the next higher-order step would be to decide which strategy/execution degradations should actually alter protective behavior instead of only appearing in details.

## Operator Diagnosis Slice 2 (healthcheck + non-destructive refresh truth)
- Landed protections:
  - `scripts/healthcheck.py` now surfaces operator-relevant stop/diagnosis truth directly from `status_summary`, including:
    - `entries_blocked_reason`
    - current execution summary
    - current strategy summary
    - cycle failure marker/reason when present
  - `write_status()` no longer wipes the previous `cycle` diagnosis when called without a fresh cycle summary, so operator-triggered `request_status` refreshes preserve the most recent cycle explanation instead of replacing it with `{}`.
- Validation evidence for this slice:
  - targeted health/status tests after the slice: `30 passed`
  - full suite after landing the slice: `437 passed, 3 skipped`
- Residual operator/risk backlog after this slice:
  - the main remaining gap is no longer missing diagnosis surface, but deciding which surfaced strategy/execution degradations should become actual protective gates.

## Risk Loop Slice 1 (light strategy/execution-aware gating)
- Landed protections:
  - RiskGuard now escalates to `YELLOW` when recent durable entry execution evidence shows true execution decay (`fill_rate < 30%` with enough observed outcomes), instead of only surfacing the counts;
  - RiskGuard now also escalates to `YELLOW` when current strategy diagnostics report `EDGE_COMPRESSION`, making strategy deterioration visible in the actual protective level rather than only in details.
- Validation evidence for this slice:
  - targeted RiskGuard tests after the slice: `19 passed`
  - full suite after landing the slice: `439 passed, 3 skipped`
- Residual risk-loop backlog after this slice:
  - the system still does not yet have a richer multi-threshold strategy/execution protective ladder beyond these first YELLOW gates;
  - broader learning-loop migration and stronger strategy/current-regime gating remain open.

## Operator Diagnosis Slice 3 (no-trade drilldown in healthcheck)
- Landed protections:
  - `healthcheck.check()` now includes `recent_no_trade_stage_counts`, so the fast-path operator surface can expose whether the system is currently blocked by `EDGE_INSUFFICIENT`, `RISK_REJECTED`, or other rejection stages without manual `decision_log` archaeology.
- Validation evidence for this slice:
  - targeted healthcheck tests after the slice: `4 passed`
  - full suite after landing the slice: `439 passed, 3 skipped`
- Residual operator/consumer backlog after this slice:
  - the next remaining step is less about missing surfaces and more about deciding which of the now-visible strategy/execution/no-trade degradations should become stronger policy or control-plane actions.

## Control-Plane Slice 1 (real strategy gates)
- Landed protections:
  - `set_strategy_gate` is no longer a fake control-plane surface: the command now validates, persists, and is readable through `is_strategy_enabled()` / `strategy_gates()`;
  - discovery/runtime now honors disabled strategy gates and converts blocked would-be trades into explicit `RISK_REJECTED` no-trade records with `strategy_gate_disabled:<strategy>` rather than silently ignoring the control-plane intent.
- Validation evidence for this slice:
  - targeted control/runtime tests after the slice: `64 passed`
  - full suite after landing the slice: `441 passed, 3 skipped`
- Residual control/risk backlog after this slice:
  - surfaced strategy/execution/no-trade truth is now materially more actionable, but higher-order policy still remains: deciding which degradations should auto-tighten, which should per-strategy gate, and which should stay diagnostic only.

## Operator Diagnosis Slice 4 (risk details mirrored into status)
- Landed protections:
  - `status_summary` now mirrors the latest `risk_state.details_json` into `risk.details`, so operators and outer systems do not need a second DB read just to understand why current risk is YELLOW/ORANGE/RED.
- Validation evidence for this slice:
  - targeted status tests after the slice: `27 passed`
  - full suite after landing the slice: `441 passed, 3 skipped`
- Residual operator/risk backlog after this slice:
  - the next remaining work is no longer “missing data in the status surface”, but deciding which of the now-visible diagnostics should become stronger automated controls.

## Operator Diagnosis Slice 5 (control-plane truth in status)
- Landed protections:
  - `status_summary` now mirrors the effective control-plane state (`entries_paused`, `edge_threshold_multiplier`, `strategy_gates`) so operator surfaces can see what manual/automatic controls are currently active without separately reading control files.
- Validation evidence for this slice:
  - targeted status tests after the slice: `27 passed`
  - full suite after landing the slice: `441 passed, 3 skipped`
- Residual operator/control backlog after this slice:
  - the remaining work is now less about missing visibility and more about deciding which control-plane actions should become automatic responses to the strategy/execution diagnostics already present.

## Operator Diagnosis Slice 6 (runtime backlog counts)
- Landed protections:
  - `status_summary` now exposes a `runtime` section with direct backlog counts for:
    - `chain_state_counts`
    - `exit_state_counts`
    - `unverified_entries`
    - `day0_positions`
  - this turns previously per-position forensic state into an operator-readable aggregate surface.
- Validation evidence for this slice:
  - targeted status tests after the slice: `27 passed`
  - full suite after landing the slice: `441 passed, 3 skipped`
- Residual operator/control backlog after this slice:
  - the remaining work is now primarily policy and automation: which of these surfaced runtime backlogs should become automatic pauses, gates, or escalations.

## Operator Diagnosis Slice 7 (healthcheck mirrors control/runtime state)
- Landed protections:
  - `healthcheck.check()` now mirrors the status-summary `control` and `runtime` sections into its result, so the fast path sees:
    - current control-plane state
    - current runtime backlog counts
    - execution/strategy/no-trade diagnostics already added earlier
  - this removes another multi-file/manual-join step from the operator loop.
- Validation evidence for this slice:
  - targeted healthcheck tests after the slice: `4 passed`
  - full suite after landing the slice: `441 passed, 3 skipped`
- Residual operator/control backlog after this slice:
  - the remaining work is now predominantly automation policy: deciding which visible runtime/control/strategy/execution conditions should drive automatic actions instead of merely richer diagnosis.

## Health Signal Slice 1 (cycle-failure degrades health)
- Landed protections:
  - `healthcheck` now treats an explicitly failed latest cycle as degraded (`healthy=false`) instead of reporting healthy merely because the daemon and RiskGuard are alive.
- Validation evidence for this slice:
  - targeted healthcheck tests after the slice: `5 passed`
  - full suite after landing the slice: `442 passed, 3 skipped`
- Residual health/control backlog after this slice:
  - the next remaining health-policy question is which additional diagnosis surfaces should affect severity versus remain informational only.

## Consumer Truth Slice 3 (env-filtered no-trade diagnostics)
- Landed protections:
  - `query_no_trade_cases()` now filters by `env`, aligning no-trade diagnostics with the same paper/live truth boundary already enforced for settlement readers;
  - this means operator and health surfaces no longer risk mixing paper and live rejection reasons when they summarize recent no-trade behavior.
- Validation evidence for this slice:
  - targeted DB/healthcheck tests after the slice: `32 passed`
  - full suite after landing the slice: `443 passed, 3 skipped`
- Residual consumer backlog after this slice:
  - deeper learning migration still remains: evaluated opportunity set, no-trade diagnostics, and strategy/execution truth are now cleaner, but they are not yet unified into a single learned-current-regime consumer model.

## Control-Plane Slice 2 (real tighten-risk effect)
- Landed protections:
  - the control-plane `tighten_risk` surface is no longer inert: `get_edge_threshold_multiplier()` now directly reduces the effective Kelly multiplier inside evaluator sizing, so a tightened-r​isk command actually shrinks new entry risk instead of only appearing in state.
- Validation evidence for this slice:
  - targeted sizing/status tests after the slice: `28 passed`
  - full suite after landing the slice: `444 passed, 3 skipped`
- Residual control/risk backlog after this slice:
  - the remaining higher-order decision is whether future auto-controls should use this same multiplier path, stricter per-strategy gates, or both.

## Risk Loop Slice 2 (actionable recommendations)
- Landed protections:
  - RiskGuard now emits `recommended_controls` and `recommended_strategy_gates`, turning execution decay / edge compression evidence into explicit operator-action recommendations instead of leaving them as raw diagnostics only.
- Validation evidence for this slice:
  - targeted RiskGuard tests after the slice: `19 passed`
  - full suite after landing the slice: `444 passed, 3 skipped`
- Residual risk-loop backlog after this slice:
  - the next remaining step is policy, not plumbing: deciding which recommendations should stay advisory and which should become direct automated control-plane actions.

## Health Signal Slice 2 (risk recommendations visible in fast path)
- Landed protections:
  - `healthcheck` now mirrors `risk.details` directly, so recommended controls / strategy-gate suggestions and other risk diagnostics are visible on the fast path without opening the status file separately.
- Validation evidence for this slice:
  - targeted healthcheck tests after the slice: `5 passed`
  - full suite after landing the slice: `444 passed, 3 skipped`
- Residual health/risk backlog after this slice:
  - the main remaining question is no longer surfacing recommendation data, but whether any of those recommendations should automatically mutate runtime controls.

## Operator Diagnosis Slice 8 (no-trade counts in status)
- Landed protections:
  - `status_summary` now includes recent no-trade stage counts, so the status file itself can answer whether the recent system bottleneck is `EDGE_INSUFFICIENT`, `RISK_REJECTED`, or another rejection family without forcing healthcheck/database-only access.
- Validation evidence for this slice:
  - targeted status tests after the slice: `28 passed`
  - full suite after landing the slice: `444 passed, 3 skipped`
- Residual operator/consumer backlog after this slice:
  - deeper learning migration still remains; the status and health surfaces are increasingly self-contained, but they still sit on top of separate consumer summaries rather than one unified learned-current-regime model.

## Learning Consumer Slice 1 (unified current-regime learning surface)
- Landed protections:
  - `query_learning_surface_summary()` now combines authoritative settlement counts, degraded-settlement counts, recent no-trade stage counts, and execution summary into one consumer helper;
  - `status_summary` now exposes that helper as a `learning` section, so outer consumers can read one current-regime learning surface instead of stitching settlement/execution/no-trade summaries manually.
- Validation evidence for this slice:
  - targeted DB/status tests after the slice: `56 passed`
  - full suite after landing the slice: `445 passed, 3 skipped`
- Residual learning backlog after this slice:
  - the next remaining step is no longer “how do I read these surfaces together?”, but “how do I actually use this unified surface for stronger gating or model evolution?”.

## Automation Contract Slice 1 (supervisor/control contract alignment)
- Landed protections:
  - `SupervisorCommand` now matches the real control-plane command set and carries the load-bearing payload fields the runtime actually understands, reducing drift between external automation and actual runtime behavior.
- Validation evidence for this slice:
  - targeted contract/control tests after the slice: `29 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual automation backlog after this slice:
  - the remaining question is no longer command naming drift, but which control-plane actions should be automatically produced from the richer diagnostics now available.

## Health Signal Slice 3 (healthcheck uses unified learning surface)
- Landed protections:
  - `healthcheck` now consumes the status summary’s `learning` section directly when present, instead of recomputing no-trade diagnosis separately by default.
- Validation evidence for this slice:
  - targeted healthcheck tests after the slice: `5 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual health/learning backlog after this slice:
  - the remaining work is no longer surface duplication, but deciding which parts of the learning surface should drive automated controls or model evolution.

## Learning Consumer Slice 2 (strategy-aware learning summary)
- Landed protections:
  - the unified learning surface now carries a `by_strategy` view that merges authoritative settlement truth with entry-side execution truth, giving outer consumers a first strategy-oriented learned-current-regime surface without scraping multiple sections independently.
- Validation evidence for this slice:
  - targeted DB/status tests after the slice: `57 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual learning backlog after this slice:
  - what remains is less about assembling current-regime learning truth and more about deciding how that truth should alter controls, gating, or model adaptation.

## Control/Operator Slice 3 (recommendations mirrored into control surface)
- Landed protections:
  - `status_summary.control` now includes `recommended_controls` and `recommended_strategy_gates`, so active controls and RiskGuard-recommended controls are visible in one place.
- Validation evidence for this slice:
  - targeted status tests after the slice: `29 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual control/operator backlog after this slice:
  - the remaining question is now squarely automation policy: whether and when those recommendations should be auto-applied instead of merely surfaced.

## Strategy Operator Slice 3 (gate state + recommendations in strategy summary)
- Landed protections:
  - each strategy bucket in `status_summary.strategy` now shows both:
    - whether it is currently gated by control-plane truth
    - whether RiskGuard currently recommends gating it
  - this closes another operator loop: current strategy state, recommended action, and active manual control now live in one surface instead of three.
- Validation evidence for this slice:
  - targeted status tests after the slice: `28 passed`
  - full suite after landing the slice: `444 passed, 3 skipped`
- Residual strategy/operator backlog after this slice:
  - the remaining work is less about seeing strategy state and more about deciding which recommendations should automatically become control mutations or higher-severity gates.

## Planned Team Shape (new round)
- **Main** — architecture authority, contract freeze, integration, final acceptance, queue discipline.
- **runtime lane** — lifecycle authority, pending/live rescue, Day0 terminal-phase behavior, exit/event wiring.
- **time-semantics lane** — audit and unify local-day / local-hour slicing and freshness semantics across evaluator/day0/general ensemble paths.
- **truth/learning lane** — canonical ledger/query contracts, harvester source migration, authoritative consumer migration.
- **detailed-review lane** — file-level review for correctness/contract completeness after each landed slice.
- **adversarial lane** — challenge each landed slice for fail-open edges, false authority claims, and missing invariants.
- **explore subagents** — one-off truth/function-design archaeology only; never promoted to durable teammate lanes by default.
- First dispatch in the new round is now live:
  - `runtime lane` owns the `ExitContext v2` / lifecycle contract-wiring slice.
  - `truth/learning lane` owns the canonical settlement payload / authoritative reader-harvester seam.
  - `time-semantics lane` remains intentionally undispached for one turn while Main personally re-reads the core time-semantics files before freezing that lane's contract.
- Time-semantics audit findings from the bounded explore pass:
  - consistent now: `time_context.py` lead helpers are city-local and no longer anchored to bare `date.today()`; the main `EnsembleSignal` path now slices target-day hours from real forecast timestamps; Day0 observation paths already carry target-date/reference-time semantics into evaluator/monitor refresh.
  - immediate P0 inconsistencies still open:
    1. `src/signal/day0_window.py` falls back from “no remaining target-day hours” to “all target-day hours”, which silently destroys remaining-window semantics;
    2. `src/engine/evaluator.py` still computes GFS crosscheck agreement from the first 24 forecast hours instead of the same local target-day slice used by ECMWF;
    3. ENS snapshot metadata still uses mixed/fake clock fields (`issue_time` from first valid timestamp, `valid_time` hardcoded to noon target date) and therefore does not yet represent the true time semantics the runtime is converging toward.
  - lower-priority follow-up: evaluator / monitor-refresh still do not share one fully explicit cycle clock, so some boundary-time behavior may remain non-replayable even after the three P0 mismatches above are fixed.

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

## Historical Workstreams (prior execution round)
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
- Agent C reader-gap audit delta: the exact RiskGuard consumer gap is now frozen. `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py` reads `query_settlement_records()`; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/decision_chain.py` currently delegates that to `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py:query_authoritative_settlement_rows()`, but that DB helper falls back by calling `decision_chain.query_settlement_records()` again. A safe RiskGuard settlement-reader contract therefore must require: prefer `position_events` rows with `event_type='POSITION_SETTLED'`; normalize from event columns plus `details_json`; if no stage events exist, fall back only to a legacy-only reader (`query_legacy_settlement_records()`), never back through the mixed authoritative entrypoint; and stamp returned rows with explicit `source` so RiskGuard can record whether it evaluated canonical or legacy settlement truth.
- Agent C blocker delta from that audit: until the fallback path is split cleanly, the current helper boundary can recurse on legacy-only databases and does not provide a trustworthy source contract for RiskGuard observability.
- Agent B locked decision delta: RiskGuard settlement consumers should prefer stage-level `position_events` with `event_type='POSITION_SETTLED'` and only fall back to legacy `decision_log` settlement blobs when no canonical stage events exist yet. `decision_log` stays backward-compatible for replay/cycle summaries, but settlement truth for risk should migrate toward stage events first.
- Agent B implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` now splits canonical vs legacy settlement reads cleanly so `query_authoritative_settlement_rows()` falls back only to `query_legacy_settlement_records()` instead of recursing back through `decision_chain.query_settlement_records()`. The same DB module now appends sell-side lifecycle telemetry events — `EXIT_ORDER_ATTEMPTED`, `EXIT_FILL_CHECKED|EXIT_FILL_CONFIRMED`, `EXIT_FILL_CHECK_FAILED`, `EXIT_RETRY_SCHEDULED|EXIT_BACKOFF_EXHAUSTED`, `EXIT_RETRY_RELEASED`, `EXIT_INTENT_RECOVERED`, and `EXIT_ORDER_ID_MISSING` — while keeping `position_events` supplemental rather than authoritative for open positions.
- Agent B runtime wiring delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/execution/exit_lifecycle.py` now accepts the active DB connection and emits durable sell-side telemetry during placement, quick fill checks, retry scheduling, cooldown release, and pending-exit recovery; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` passes the cycle DB connection through both `execute_exit()` and `check_pending_exits()`/`check_pending_retries()` so the exit state machine owns the telemetry without moving position authority out of `PortfolioState`.
- Agent B touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_db.py` now covers canonical-settlement-reader preference/fallback plus sell-side event helper emission and backoff event typing. Tests were updated but not run in this harness.
- Agent B current blocker after this slice: sell-side telemetry is now append-only at the exit_lifecycle seam, but rescued pending fills from chain reconciliation still do not emit corresponding stage events, and no integration/adversarial runtime test yet proves the full placed -> checked -> retried/filled path through `cycle_runtime.py`.
- Agent B cleanup delta: the immediate follow-up is diff narrowing, not feature expansion. `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` picked up duplicated historical helper blocks and large blank/noise regions in commit `e715e7d`; this cleanup pass removes only stale duplicate definitions and formatting debris while preserving the intended settlement-reader split plus sell-side telemetry helpers.
- Agent B cleanup status: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` is back to one live copy each of `log_trade_exit`, `update_trade_lifecycle`, `query_position_events`, `query_authoritative_settlement_rows`, and the sell-side event helpers, with the stray blank/noise tail removed. Current diff against `HEAD` is now only the deletion of that stale duplicate tail plus this doc update. Push-safety is improved to reviewable for this file shape, but tests still have not been run in this harness and no post-cleanup commit has been created yet.
- Agent C implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py` now makes the first real consumer switch to `query_authoritative_settlement_rows()` and persists settlement storage-source provenance into `risk_state.details_json` via `settlement_storage_source` plus `settlement_row_storage_sources`. This is storage-source provenance (`position_events` vs `decision_log`), not original event-writer provenance. This slice remains settlement-truth-only and does not claim broader lifecycle/execution authority migration.
- Agent C touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks canonical `POSITION_SETTLED` preference and clean legacy-only fallback storage-source provenance in RiskGuard state, alongside the existing Brier-flow audit. The stale `risk_state.win_rate` authority expectation was also removed; RiskGuard now leaves the legacy column null and records live directional performance only in `details_json.accuracy`.
- Agent C locked decision delta: for the first RiskGuard consumer migration, provenance surfaced to operators is settlement-source only (`position_events` vs `decision_log`); rescued `pending_tracked -> entered` stage-event coverage remains a later lifecycle/execution authority dependency and does not block settlement-reader adoption.
- Agent C current blocker after this slice: tests were updated but not run in this harness, and commit/push should wait until the working tree can stage this slice without batching unrelated teammate changes.
- Agent B next slice is frozen: add exactly-once durable stage-event coverage for chain-reconciliation rescue of `pending_tracked -> entered` without broadening authority claims. The event must preserve historical entry provenance, mark explicit rescue provenance/source, and avoid duplicate emission on repeated reconciliation of the same already-rescued position.
- Agent B implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/chain_reconciliation.py` now accepts the active DB connection and emits a rescue-specific `POSITION_LIFECYCLE_UPDATED` stage event when reconciliation upgrades `pending_tracked -> entered`; `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` now supports an explicit `position_state` override on `log_position_event()` plus a dedicated `log_reconciled_entry_event()` helper so rescue events preserve historical entry provenance while marking `source='chain_reconciliation'` and `reason='pending_fill_rescued'`.
- Agent B exactly-once delta: repeated reconciliation after the first rescue no longer appends duplicate rescue events because the reconciliation path checks for an existing `chain_reconciliation` rescue lifecycle row for the same runtime trade before emitting again. This slice remains rescue-event coverage only and does not claim broader lifecycle/execution authority migration.
- Agent B touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now locks rescue-stage-event emission, explicit rescue provenance, historical entry provenance preservation, and exactly-once behavior across repeated reconciliation calls. Tests were updated but not run in this harness.
- Agent B next slice is frozen: T11 harvester learning-source migration should move decision-time snapshot sourcing off open-portfolio-only discovery for the smallest safe settlement-learning path. This slice must keep `PortfolioState` as open-position runtime authority, avoid replay migration claims, and document the new durable source path explicitly.
- Agent B T11 contract delta: harvester settlement learning should resolve `decision_snapshot_id` from durable settlement authority first, keyed by `city` + `target_date`, and only fall back to open portfolio discovery when no durable settlement rows exist yet. This slice changes learning input sourcing only; `PortfolioState` remains runtime authority for open positions.
- Agent B implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/execution/harvester.py` now resolves settlement-learning snapshot contexts from durable `POSITION_SETTLED` rows in `position_events` before consulting still-open portfolio positions. The fallback remains narrow and only applies when no durable settlement snapshot IDs exist for the settled market.
- Agent B touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now proves three behaviors — harvester can refit from durable settlement rows with no open position left, still falls back to open portfolio discovery when no durable settlement exists yet, and prefers the durable snapshot over a conflicting still-open portfolio snapshot. Tests were updated but not run in this harness.
- Agent B current blocker after this slice: verification is still pending because no Sonnet-run pytest or file review has been executed in this harness yet.
- Agent A locked decision delta: until full T6 phase authority lands, monitoring must compute real per-position `hours_to_settlement` from target date + city timezone and feed that into exit evaluation; a hardcoded 24h monitor context is fail-open and forbidden.
- Agent A audit/fix delta: the next fail-open gap was a real Day0 bypass — monitoring always passed `hours_to_settlement=24.0`, which silently disabled settlement-imminent and near-settlement logic and prevented automatic `day0_window` transition for positions already near target date. `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` now computes real hours-to-settlement per position and promotes `entered|holding -> day0_window` inside monitoring when the remaining window is <= 6h.
- Agent A touched test: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now locks the Day0 transition invariant and verifies exit evaluation receives the real sub-1h settlement distance.
- Agent A remaining blocker after this slice: `day0_window` now exists as a real lifecycle transition, but refresh still dispatches primarily from `entry_method`; full T6 still requires explicit Day0-phase-specific refresh/exit semantics for all held positions, not just correct timing context.
- Agent A locked decision delta: once a position enters `day0_window`, refresh provenance is phase-owned, not entry-owned. `entry_method` remains immutable historical provenance, but monitor refresh must dispatch through `DAY0_OBSERVATION` semantics for any `day0_window` position.
- Agent A implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/monitor_refresh.py` now treats `day0_window` as load-bearing by refreshing those positions through `DAY0_OBSERVATION` semantics even when the original `entry_method` was ENS-based. The historical `entry_method` is preserved, while phase-time dispatch is reflected in `selected_method` and `applied_validations`.
- Agent A touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now locks both the `holding -> day0_window` runtime transition and the invariant that `day0_window` refresh dispatches through Day0 observation semantics without mutating historical provenance.
- Agent A current blocker after this slice: Day0 refresh dispatch is now phase-aware, but exit behavior is still only indirectly affected through refreshed probabilities and settlement timing; a later T6 slice still needs explicit Day0-phase exit/microstructure semantics if the spec requires stronger terminal overrides.
- Agent A locked decision delta: terminal-phase ownership starts in the same monitoring cycle that first observes `<6h` to settlement. Monitoring must classify/promote `entered|holding -> day0_window` before refresh dispatch so that the crossing cycle itself uses `DAY0_OBSERVATION` semantics. Historical `entry_method` remains immutable provenance.
- Agent A implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` now computes and applies the `<6h` Day0 promotion before calling `refresh_position()`, removing the one-cycle fail-open gap where a newly terminal position still refreshed through its old hold semantics. Existing `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/monitor_refresh.py` phase-owned dispatch then takes effect in that same cycle without mutating historical `entry_method`.
- Agent A touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now asserts both that the crossing cycle enters `day0_window` before refresh and that a same-cycle `<6h` transition refreshes through `DAY0_OBSERVATION` instead of the ENS path.
- Agent A current blocker after this slice: same-cycle Day0 refresh semantics are now closed, but exit behavior is still only indirectly affected through refreshed probabilities and settlement timing; a later T6 slice still needs explicit Day0-phase exit/microstructure semantics if the spec requires stronger terminal overrides.
- Agent A slice hygiene: no subagent used; context kept narrow to `monitor_refresh.py`, `cycle_runtime.py`, and one test file; progress/task docs updated before reporting. Commit status: not created in this harness because the user has not explicitly authorized git commit. Push status: not attempted.
- Agent A locked decision delta: quarantine must own an explicit fail-closed administrative resolution path inside runtime monitoring. Unresolved `quarantined|quarantine_expired` positions must be marked for administrative review exactly once and then stay on a dedicated resolution path that skips ordinary monitoring/entry flow while continuing to block new entries.
- Agent A implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/chain_reconciliation.py` now freezes explicit quarantine resolution reasons (`QUARANTINE_REVIEW_REQUIRED`, `QUARANTINE_EXPIRED_REVIEW_REQUIRED`), and `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` now routes quarantined positions into that administrative-resolution path before normal monitoring. The first cycle marks `admin_exit_reason`/`exit_reason` once and records a monitor artifact; later cycles keep skipping normal monitoring without duplicating the transition.
- Agent A touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now locks exactly-once quarantine resolution marking plus the distinct expired-quarantine reason.
- Agent A current blocker after this slice: quarantine now has a dedicated protective runtime path, but there is still no external operator command/workflow that actually resolves the flagged position; this slice only makes the need explicit and durable inside runtime state.
- R3 boundary decision: runtime owns detection of local↔chain divergence, quarantine entry, persistence of `chain_state`, fail-closed entry blocking, exactly-once administrative marking via `admin_exit_reason`, and continued exclusion of quarantined positions from ordinary monitoring/discovery. Operator-owned exposure resolution begins after that mark: humans must inspect chain reality, decide whether the quarantined holding should be accepted, manually closed/redeemed, or ignored, and then clear the condition through an explicit external workflow. Zeus runtime must not invent an economic exit, auto-void, or pretend quarantine itself neutralizes exposure.
- Smallest landed R3 slice: documentation/truth-surface clarification only. No production code change was required because the current runtime already matches the intended protective boundary; the missing piece was explicit contract truth about where runtime authority stops and operator authority begins.
- Residual R3 gap: no operator command surface or reconciliation-clear workflow exists yet, so quarantine is now truthful and fail-closed but still review-required rather than exposure-resolving.
- Agent A locked decision delta: `day0_window` must carry explicit terminal-phase microstructure semantics, not just refreshed probabilities. During Day0 monitoring, live held-side pricing must use immediately realizable sell-side liquidity (`best_bid` for the held token) rather than ordinary hold-phase VWMP blending. Historical `entry_method` remains immutable provenance.
- Agent A implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/monitor_refresh.py` now gives `day0_window` positions a real terminal-phase microstructure rule in live mode: when refreshing the held-side market price, Day0 uses the immediate sellable `best_bid` instead of VWMP. Non-Day0 positions keep ordinary VWMP behavior, and the existing Day0 observation refresh path continues to own probability recomputation without mutating historical `entry_method`.
- Agent A touched tests for this slice: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` now locks that live `day0_window` refresh uses `best_bid` rather than VWMP while preserving the Day0 observation dispatch invariant.
- Agent A current blocker after this slice: Day0 now has explicit terminal-phase sell-side pricing semantics, but any stronger Day0-specific forced-exit policy remains a separate contract choice and was intentionally not broadened into this slice.
- Agent C validation delta: the newly landed runtime seams now have load-bearing proof in existing tests rather than only helper-level coverage. `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_live_safety_invariants.py` already locks chain-reconciliation rescue event emission with exactly-once behavior for `pending_tracked -> entered`, and `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_riskguard.py` plus `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` lock RiskGuard settlement-source provenance persistence for canonical `position_events` preference and clean legacy-only fallback.
- Agent C validation status: this slice tightened the acceptance story for T14 without broadening scope or changing authority claims. Tests were localized/read but not run in this harness, so commit/push should still wait for explicit execution/verification and for clean staging around shared team docs.

## Current Recovery Reality (2026-04-02)
- Team mode is currently `single-worker` in baton truth: both `repair` and `adversary` remain live teammates, but only `adversary` currently owns an active baton (`#20` review of the landed operator-clear slice). `repair` is parked in `waiting` after landing `#19`.
- Current recovery truth is the compact `R1`-`R5` queue plus one explicit follow-up under review: operator-clear acknowledgement is now landed narrowly, while adversarial review of that slice is still active.
- Several slices are **landed in working tree** but are **not fully trusted** yet because remediation review found real concerns and acceptance is still being tightened slice by slice.
- The strongest remediation findings shaping the live recovery queue are:
  - Lifecycle lane: pending rescue and Day0 transition/refresh are real; quarantine boundary is explicit; operator-clear acknowledgement is now landed narrowly, but broader exposure resolution remains manual/undefined.
  - Event-spine lane: settlement reader split and rescued-fill exactly-once events look real; exit telemetry runtime proof is now landed, but broader authority migration remains narrower than broad historical language implied.
  - Risk lane: tracker fake-authority cleanup is real; RiskGuard provenance switch is real but narrow; dead/misleading authority surfaces still exist in schema/tests.
- The current active question is whether the landed operator-clear acknowledgement slice is truthful, minimal, and auditable under adversarial review. Broader exposure resolution still remains out of scope for this slice.
- Historical task/workstream sprawl remains in this file and in `task.md`, but it is archive context only. It is not the active queue.

## Active Recovery Queue
- R1 — Re-verify canonical settlement payload contract (`src/state/db.py` + RiskGuard consumer expectations)
- R2 — Runtime exit-path verification is landed and should be treated as review-complete evidence for the event-spine lane, not as an open proof gap
- R3 — Quarantine runtime/operator boundary is explicit; residual gap is operator-clear workflow
- R4 — Re-validate harvester durable sourcing scope and proof level
- R5 — Clean remaining risk-side authority illusions (`risk_state.win_rate`, tracker fallback semantics, stale tests)

## Current Stop Point
- Work got as far as landing multiple runtime/event/risk slices plus a remediation review pass.
- The current body of work should still be treated as: **partially landed, partially reviewed, not yet structurally accepted**.
- Current active queue movement is narrow recovery truth alignment and compact re-verification, not new feature expansion.

## Open Risks
- lifecycle contract mismatches across `portfolio.py`, `cycle_runtime.py`, `chain_reconciliation.py`, `exit_lifecycle.py`
- migration risk if ledger/event schema lands without owner clarity
- tests currently emphasize some cross-module semantics but not the new pending/day0/exit-context closure yet
- operator mirrors may still overstate system completeness

## Current Baton State
- Active owner: `adversary` on `#20`, reviewing the landed operator-clear acknowledgement slice.
- Waiting owner: `repair`, to be reactivated after the adversarial verdict or for the next implementation slice.
- Baton truth, backlog truth, and narrative truth must stay separated: `.claude/baton_state.json` for live ownership, `task.md` for queue truth, `progress.md` / `next_round_handoff.md` for narrative truth.
- The next live queue question is whether `#20` accepts the narrow operator-clear slice, blocks it, or sharpens one more smallest follow-up.
- Baton semantics are coherent: two live teammates remain, but only one currently owns an active baton, so team mode is `single-worker`.

## Historical Baton Context
- Agent A owned truth-map compression for lifecycle authority and proposed locked contracts for PositionState, pending rescue, quarantine, Day0, ExitContext.
- Agent B aligned event names, identity fields, and decision/execution ledger schema with lifecycle work.
- Agent C converted A/B contracts into adversarial invariants and identified fake teeth / bad metrics to delete.

## Do-Not-Do
- do not expand signal/model complexity
- do not treat paper fills as execution truth
- do not use strategy tracker as capital-allocation authority yet
- do not ship UI/control-plane cosmetics as substitutes for runtime closure
- do not preserve dirty semantics for compatibility if they mislead operator or learning loops
