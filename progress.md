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

## Recent consumer/automation deltas
- Added a stable `recommended_commands_from_status()` builder and a manual `scripts/apply_recommended_controls.py` helper so surfaced recommendations can be turned into explicit control-plane commands without silent auto-mutation.
- Healthcheck now mirrors `recommended_commands`, letting the fast path expose concrete suggested actions in the same contract used by the helper.

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

## Control/Operator Slice 4 (gate drift surfaced)
- Landed protections:
  - `status_summary.control` now explicitly shows:
    - `recommended_but_not_gated`
    - `gated_but_not_recommended`
  - this makes the gap between current manual controls and RiskGuard recommendations directly visible without external diffing.
- Validation evidence for this slice:
  - targeted status tests after the slice: `29 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual control/operator backlog after this slice:
  - the remaining question is no longer “can we see the drift?”, but whether any of that drift should auto-resolve or remain an operator decision.

## Control/Operator Slice 5 (control recommendation drift surfaced)
- Landed protections:
  - `status_summary.control` now explicitly shows when recommended controls themselves remain unapplied via `recommended_controls_not_applied`.
- Validation evidence for this slice:
  - targeted status tests after the slice: `29 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual control/operator backlog after this slice:
  - the remaining question is now policy: which recommendations should continue to wait for an operator versus auto-resolve.

## Automation Contract Slice 2 (recommended command builder)
- Landed protections:
  - `recommended_commands_from_status()` now provides a stable, non-mutating contract for converting surfaced recommendation drift into explicit control-plane commands, without silently auto-applying them.
- Validation evidence for this slice:
  - targeted control/status tests after the slice: `30 passed`
  - full suite after landing the slice: `447 passed, 3 skipped`
- Residual automation backlog after this slice:
  - the remaining choice is policy: whether to keep this builder as a manual/outer-automation hook or wire it into stronger automatic behaviors.

## Automation Contract Slice 3 (apply recommended controls helper)
- Landed protections:
  - `scripts/apply_recommended_controls.py` now reads the status surface, uses the stable recommended-command builder, and enqueues explicit control-plane commands without silently mutating runtime state itself.
- Validation evidence for this slice:
  - targeted automation/control tests after the slice: `32 passed`
  - full suite after landing the slice: `449 passed, 3 skipped`
- Residual automation backlog after this slice:
  - the remaining automation decision is whether this helper stays manual/operator-invoked or becomes part of an automatic loop.

## Risk Loop Slice 3 (strategy-specific recommendations from execution truth)
- Landed protections:
  - RiskGuard recommendations now incorporate per-strategy execution decay, so a strategy can be specifically recommended for gating when its own durable fill/reject evidence is poor enough.
- Validation evidence for this slice:
  - targeted RiskGuard tests after the slice: `19 passed`
  - full suite after landing the slice: `446 passed, 3 skipped`
- Residual risk-loop backlog after this slice:
  - the remaining question is whether these strategy-specific recommendations should stay advisory or become direct automated control mutations.

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

## 2026-04-02 — P1-E temporary-control durability slice
- Main review delta: bounded scan/adversarial passes surfaced one real live-ready control seam after the recommendation/control work landed — `pause_entries` and `tighten_risk` were truthfully mirrored into status, but they only lived in `_control_state` memory. A daemon restart silently cleared those temporary global controls, so operator/control truth and runtime truth could drift even though the control-plane file still held executed acknowledgements.
- Main contract decision: executed control acknowledgements, not in-process memory, now own recovery of temporary global controls. `pause_entries` and `tighten_risk` must survive control-state refresh/restart; `resume` is the single command that clears those temporary global controls back to normal operation. Explicit per-strategy gates and quarantine acknowledgements remain independent durable surfaces.
- Implementation delta: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/control/control_plane.py` now replays executed acknowledgements into `entries_paused`, `edge_threshold_multiplier`, `strategy_gates`, and acknowledged quarantine tokens during `refresh_control_state()`. `resume` now clears both entry pause and tightened-risk posture instead of only toggling entry pause, so temporary global controls have one durable normalization path.
- Touched tests: `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks two invariants: paused entries survive `clear_control_state()`/restart, and tightened risk survives refresh until an explicit `resume`, which resets both `entries_paused` and `edge_threshold_multiplier` back to their normal values.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'control_strategy_gate_persists or pause_entries_survives or tighten_risk_survives or apply_recommended_controls or recommended_commands_from_status'` → `5 passed`
  - `./.venv/bin/pytest -q tests/test_healthcheck.py tests/test_riskguard.py tests/test_runtime_guards.py tests/test_live_safety_invariants.py` → `108 passed`
  - `./.venv/bin/pytest -q` → `451 passed, 3 skipped`
- Residual P1-E truth after this slice: control recommendations and temporary global controls now survive restarts, but deeper learning-loop migration plus stronger strategy/current-regime policy automation still remain open. The next valuable slice is no longer “make control surfaces visible”; it is “decide how much of the learned/recommended strategy ladder should become durable executable policy rather than manual recommendation.”

## 2026-04-02 — P1-E automation-policy freeze slice
- Main review delta: after temporary controls became durable, the next unsafe seam was the automation helper contract itself. `scripts/apply_recommended_controls.py` treated review-required strategy-gate flips the same as auto-safe global controls, so one helper invocation could silently enqueue `set_strategy_gate` actions meant for operator judgment rather than low-regret automation.
- Main contract decision: the policy surface is now explicitly split into **auto-safe commands** and **review-required commands**. Auto-safe commands may be enqueued by default automation; review-required commands remain visible and queryable, but default automation must not silently apply them. For now, `tighten_risk` / `pause_entries` are auto-safe; recommended strategy-gate flips remain review-required until a stronger learned policy ladder exists.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/control/control_plane.py` now exposes `recommended_autosafe_commands_from_status()`, `review_required_commands_from_status()`, and a parameterized `recommended_commands_from_status(..., include_review_required=...)`.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/apply_recommended_controls.py` now defaults to auto-safe-only command enqueue and requires explicit `--include-review-required` to enqueue strategy gate changes.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/healthcheck.py` now surfaces `recommended_auto_commands`, `review_required_commands`, and `auto_action_available`, while still preserving the full combined `recommended_commands` surface for operators.
  - Adversarial follow-up hardening: `recommended_commands_from_status()` now defaults to **auto-safe-only** and `healthcheck.py` explicitly opts into the full combined set, so future callers must say when they want review-required commands instead of silently inheriting them by default.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks that auto-safe recommendation builders exclude strategy-gate flips by default and that the helper script only enqueues those review-required commands when explicitly opted in.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_healthcheck.py` now locks the split between `recommended_auto_commands`, `review_required_commands`, and combined `recommended_commands`.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'recommended_commands_from_status or recommended_autosafe_commands or apply_recommended_controls or pause_entries_survives or tighten_risk_survives' tests/test_healthcheck.py` → `6 passed`
  - `./.venv/bin/pytest -q tests/test_riskguard.py tests/test_runtime_guards.py tests/test_live_safety_invariants.py` → `103 passed`
  - `./.venv/bin/pytest -q tests/test_db.py -k 'learning_surface_summary or authoritative_settlement or execution_event_summary'` → `6 passed`
  - `./.venv/bin/pytest -q` → `453 passed, 3 skipped`
- Residual P1-E truth after this slice: the operator/automation contract is now conservative by default, but the system still lacks a stronger, data-backed rule for when a recommended strategy gate should graduate from review-required diagnosis into durable executable policy. That remains the next strategy/current-regime ladder question rather than a control-plane visibility issue.

## 2026-04-02 — P1-E recommendation-rationale slice
- Main review delta: once auto-safe vs review-required commands were separated, the next truth gap was **why** those commands existed. `recommended_strategy_gates` and `recommended_controls` were still naked names; operator surfaces and queued command payloads could say *what* to do, but not the authoritative evidence that produced the recommendation.
- Main contract decision: recommendation surfaces now carry structured rationale. RiskGuard owns the evidence for why a control or strategy gate is recommended; status mirrors that rationale; generated command payloads carry a stable audit note built from that same rationale. This keeps review-required policy work tied to authoritative execution/edge signals instead of naked action names.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py` now persists `recommended_strategy_gate_reasons` and `recommended_control_reasons`, including explicit execution-decay and edge-compression rationales.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now mirrors those reason maps into `status.control` and per-strategy summaries via `recommended_gate_reasons`.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/control/control_plane.py` now injects stable `note` fields into generated commands so default automation and review-required commands carry their `recommended_by=...` provenance into the control queue.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_riskguard.py` now locks both the strategy-gate reason map and control-reason map for execution-decay and edge-compression recommendations.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks status-surface reason mirroring plus note-bearing generated commands in both auto-safe and review-required apply paths.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_riskguard.py tests/test_pnl_flow_and_audit.py -k 'recommended_commands_from_status or recommended_autosafe_commands or apply_recommended_controls or execution_decay or edge_compression or write_status_writes_runtime_truth'` → `6 passed`
  - `./.venv/bin/pytest -q tests/test_healthcheck.py tests/test_runtime_guards.py tests/test_live_safety_invariants.py` → `89 passed`
  - `./.venv/bin/pytest -q tests/test_db.py -k 'learning_surface_summary or authoritative_settlement or execution_event_summary'` → `6 passed`
  - `./.venv/bin/pytest -q` → `453 passed, 3 skipped`
- Residual P1-E truth after this slice: recommendation payloads are now explainable and auditable, but the system still stops at rationale-bearing recommendation rather than a fully learned policy ladder. The next step is not more note fields; it is deciding how the learned current-regime surface should elevate or suppress strategy gates with stronger structural authority.

## 2026-04-02 — P1-E strategy-tagged no-trade learning slice
- Main review delta: after recommendation payloads became explainable, the next missing learning edge was the no-trade side of the current regime. `query_learning_surface_summary()` could count no-trades globally, but it could not attribute them by strategy, because `NoTradeCase` did not carry `strategy` / `edge_source` and the learning surface therefore lost one whole side of current-regime opportunity truth.
- Main contract decision: `NoTradeCase` now carries strategy provenance whenever it is knowable at decision time. Learning/current-regime surfaces should be able to answer not only “what settled?” and “what filled?”, but also “which strategy opportunities are getting rejected or gated right now?” This remains learning truth only; it does not yet auto-gate anything.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/decision_chain.py` now extends `NoTradeCase` with `strategy` and `edge_source`, and `query_learning_surface_summary()` now folds strategy-tagged no-trades into `learning.by_strategy[*].no_trade_count` and `no_trade_stage_counts`.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/engine/cycle_runtime.py` now writes strategy/edge-source provenance into both ordinary no-trades and strategy-gate rejections whenever the strategy is knowable from the evaluated edge.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_db.py` now proves that the learning surface carries per-strategy no-trade counts/stages alongside settlement and execution truth.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_runtime_guards.py` now locks that strategy-gate rejections persist `strategy` + `edge_source` into the `NoTradeCase` artifact.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_db.py -k 'learning_surface_summary'` → `1 passed`
  - `./.venv/bin/pytest -q tests/test_runtime_guards.py -k 'strategy_gate_blocks_trade_execution'` → `1 passed`
- Residual P1-E truth after this slice: the learned current-regime surface now sees strategy-tagged no-trades, but it still uses them as descriptive evidence rather than a closed policy ladder. The next step is to decide how this richer by-strategy rejection truth should influence review-required vs executable gating policy.

## 2026-04-02 — P1-E current-regime strategy summary merge
- Main review delta: after strategy-tagged no-trades landed, the next truth gap moved one layer up: operator strategy surfaces still split open-position PnL/exposure from learning/regime evidence. `status.strategy` knew holdings and recent exits; `status.learning.by_strategy` knew settlements / execution / no-trades. A human or downstream automation still had to manually join those two surfaces to understand a strategy’s current regime.
- Main contract decision: `status.strategy` is now the human-facing current-regime strategy surface. It keeps operator-truth fields (open positions, exposure, realized/unrealized PnL, gate state) and now also absorbs the learning-side counts (settlements, entry execution, no-trades) so one strategy bucket shows both current book truth and current-regime learning truth.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now merges `learning.by_strategy` into `status.strategy`, carrying `settlement_count`, `settlement_pnl`, `settlement_accuracy`, `entry_attempted`, `entry_filled`, `entry_rejected`, `no_trade_count`, and `no_trade_stage_counts` alongside the existing operator/gating fields.
  - Strategies that only exist in the learning surface (for example, recently rejected or inactive strategies with no open positions) now still appear in `status.strategy`, so the operator surface is no longer biased toward currently-held exposure only.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks that `status.strategy` preserves gate truth and also absorbs the learning/regime fields, including learning-only strategies with zero open positions.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'status_strategy_merges_learning_surface'` → `1 passed`
  - `./.venv/bin/pytest -q` → `454 passed, 3 skipped`
- Residual P1-E truth after this slice: the operator surface can now see a unified by-strategy regime picture, but policy remains advisory. The next real step is to decide whether any of that richer current-regime evidence should promote or suppress strategy gates automatically, and under what contract.

## 2026-04-02 — P1-E bidirectional gate-drift review commands
- Main review delta: once `status.strategy` unified the current-regime picture, the next ladder gap was asymmetry in review-required gate commands. The control-plane builder could propose “disable this strategy” when evidence recommended a new gate, but it could not propose the symmetric “re-enable this strategy” action when a strategy stayed gated after the recommendation disappeared (`gated_but_not_recommended` drift).
- Main contract decision: review-required gate commands are now **bidirectional**. The operator/automation contract should surface both sides of gate drift: disable strategies whose evidence newly warrants gating, and review re-enable strategies whose manual gate now lacks current supporting evidence. This is still review-required, not auto-applied.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/control/control_plane.py` now turns `control.gated_but_not_recommended` into explicit review-required `set_strategy_gate(..., enabled=True)` commands with `note='recommended_by=gate_drift_resolved'`, alongside the existing review-required disable commands.
  - Existing auto-safe behavior remains unchanged: default automation still enqueues only auto-safe commands unless explicitly opted into review-required actions.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks that the full review-required command builder and `--include-review-required` apply path generate both disable and re-enable gate commands with stable notes.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'recommended_commands_from_status or apply_recommended_controls'` → `3 passed`
  - `./.venv/bin/pytest -q` → `454 passed, 3 skipped`
- Residual P1-E truth after this slice: gate drift is now surfaced symmetrically as explicit review-required commands, but the actual decision logic for when a strategy should graduate from review-required enable/disable recommendations to stronger executable policy still remains open.

## 2026-04-02 — stale-contract fail-closed + real-time filtering slice
- Review-triggered finding: current runtime files on disk can lag the new contract badly enough to create **false authority + fail-open**. A stale `status_summary-{mode}.json` without `control/runtime/execution/learning/truth` and a stale `risk_state-{mode}.db` without new `recommended_*` fields made `healthcheck.py` / `apply_recommended_controls.py` quietly behave as if there were no policy actions to take, when in reality the daemon just had not emitted the new schema yet.
- Main contract decision: consumer tools must now **fail closed on stale authority surfaces**. `healthcheck.py` and `apply_recommended_controls.py` are no longer allowed to silently treat missing contract fields as “no actions needed.” In parallel, `query_no_trade_cases()` must filter by real timestamps, not SQLite string-order accidents, because polluted “recent” windows would corrupt the learning/current-regime ladder.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now emits explicit `control.recommended_auto_commands`, `review_required_commands`, and combined `recommended_commands` so the status truth surface itself carries the command contract instead of forcing downstream tools to reconstruct it.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/healthcheck.py` now validates both the status contract and the RiskGuard details contract, marks them invalid when required fields are missing, and only trusts status-surface command arrays when that contract is present. This turns stale-schema runtime state into an explicit degraded condition instead of a silent no-op.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/apply_recommended_controls.py` now rejects stale status contracts with `reason='stale_status_contract'` instead of silently enqueueing nothing.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/decision_chain.py` now filters recent no-trade cases in Python by parsed UTC timestamps, closing the SQLite string-comparison seam that could include old same-day rows as if they were recent.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_healthcheck.py` now locks healthy status/risk contract validation and explicit stale-contract degradation.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks status-surface command arrays plus stale-contract rejection in `apply_recommended_controls.py`.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_db.py` now locks real timestamp filtering for `query_no_trade_cases()`.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_healthcheck.py` → `6 passed`
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'write_status_writes_runtime_truth or status_strategy_merges_learning_surface or apply_recommended_controls'` → `4 passed`
  - `./.venv/bin/pytest -q tests/test_db.py -k 'query_no_trade_cases_filters_recent_rows_by_real_timestamp or learning_surface_summary'` → `2 passed`
  - `./.venv/bin/pytest -q` → `457 passed, 3 skipped`
- Residual P1-E truth after this slice: consumer tools now detect stale authority surfaces instead of trusting them, but the actual local daemon/RiskGuard processes still need a fresh emission cycle before the on-disk runtime files will reflect the new contract. That is now an operational refresh problem, not a silent control-policy bug.

## 2026-04-02 — operational refresh + launchctl fallback hardening
- Operational action: restarted the paper daemon and RiskGuard via launchd, then forced a one-shot `write_status()` / `tick()` / `write_status()` refresh so the on-disk authority surfaces re-emitted under the new contract instead of waiting for the next normal cycle.
- Main follow-up finding: after the refresh, status/risk contracts were valid, but `healthcheck.py` still reported `daemon_alive=false` / `riskguard_alive=false` because `launchctl list <label>` fails from Python subprocesses in this environment even while the agents are actually running. `launchctl print gui/<uid>/<label>` works and exposes the real PID/state.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/scripts/healthcheck.py` now falls back from `launchctl list <label>` to `launchctl print gui/<uid>/<label>` and correctly parses multiline `pid = ...` output instead of treating that runtime shape as dead.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_healthcheck.py` → `7 passed`
  - live one-shot check after restart/refresh: `./.venv/bin/python scripts/healthcheck.py` → `healthy: true`, `daemon_alive: true`, `riskguard_alive: true`, `status_contract_valid: true`, `riskguard_contract_valid: true`
- Runtime truth after refresh:
  - paper daemon alive: `PID 74917`
  - RiskGuard alive: `PID 74919`
  - current healthcheck is now GREEN on process truth, with `risk_level: YELLOW` and one review-required strategy gate command visible: disable `opening_inertia` because of `edge_compression`

## 2026-04-02 — edge-compression robustness hardening
- Operator decision recorded: `opening_inertia` remains unchanged for now because the current `edge_compression` evidence is too thin to justify a gate. The warning class stays alive, but the present sample/time profile is now treated as insufficient evidence for policy action.
- Main root-cause finding: `StrategyTracker.edge_compression_check()` was too eager. It could emit `EDGE_COMPRESSION` from a short burst of trades because the trend used trade-order indexing and only a count floor, so a few early oversized edges could look like regime decay before enough real elapsed time had passed.
- Main contract decision: edge compression now requires both **enough samples** and **enough real elapsed time**. Trend is computed over actual elapsed days from `entered_at`, not trade index. Alerting now requires:
  - `EDGE_COMPRESSION_MIN_TRADES = 20`
  - `EDGE_COMPRESSION_MIN_SPAN_DAYS = 3.0`
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/strategy_tracker.py` now adds `recent_edge_points()`, computes `edge_trend()` over real days, and gates `edge_compression_check()` on both sample count and time span.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_strategy_tracker_regime.py` now locks:
    - enough trades but insufficient elapsed span → no compression alert
    - enough trades and enough span with shrinking edge → alert still fires
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_strategy_tracker_regime.py tests/test_riskguard.py -k 'edge_compression or strategy_edge_compression_alert'` → `3 passed`
  - `./.venv/bin/pytest -q` → `460 passed, 3 skipped`
- Runtime truth after one-shot refresh under the new rule:
  - `./.venv/bin/python - <<'PY' ... tick(); write_status() ... PY` updated paper state
  - `./.venv/bin/python scripts/healthcheck.py` then returned:
    - `healthy: true`
    - `risk_level: GREEN`
    - `recommended_strategy_gates: []`
    - `recommended_commands: []`
  - `opening_inertia` is no longer recommended for gating, matching the recorded operator decision.

## 2026-04-02 — operational DB recovery after restart
- Restarting the launchd services exposed a real runtime issue unrelated to the policy work: the paper daemon began crash-looping with `sqlite3.DatabaseError: malformed database schema (solar_daily) - invalid rootpage`, and RiskGuard also hit disk/database errors. Root cause was not logical schema drift in code; it was an operational storage inconsistency after restart.
- Main recovery finding:
  - `state/zeus.db` main database was intact when opened without journals
  - the corruption came from mismatched `state/zeus.db-wal` / `state/zeus.db-shm`
  - `state/risk_state-paper.db` itself was corrupted and had to be treated as disposable derived state
- Recovery action:
  - stopped paper daemon + RiskGuard
  - archived the broken `zeus.db` WAL/SHM files and the broken `risk_state-paper.db` family into `state/legacy_state_archive/`
  - restarted services
  - forced a one-shot `write_status()` refresh so status truth immediately reflected the recovered runtime
- Runtime truth after recovery:
  - paper daemon alive: `PID 99017`
  - RiskGuard alive: `PID 99018`
  - `./.venv/bin/python scripts/healthcheck.py` now returns:
    - `healthy: true`
    - `risk_level: GREEN`
    - `status_contract_valid: true`
    - `riskguard_contract_valid: true`
    - `recommended_commands: []`
- Follow-up note: this was an operational state-recovery slice, not a semantic code contract slice. The mainline P0/P1 code remains valid; the recovery was necessary to restore runtime truth after restart.

## 2026-04-02 — tracker current-regime start backfill
- Main review delta: after runtime recovered, one remaining semantic weakness in the current-regime surfaces was that `strategy_tracker.accounting.current_regime_started_at` could stay empty unless the rebuild script had been run explicitly. That made the current-regime label partially true in narrative but not fully encoded in the live tracker artifact.
- Main contract decision: the tracker must backfill `current_regime_started_at` from its own trade set whenever it is operating as the live current-regime attribution surface. This is still attribution-only; it does not turn the tracker into runtime authority, but it makes the current-regime metadata truthful and durable.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/strategy_tracker.py` now recomputes `current_regime_started_at` from tracked `entered_at` timestamps:
    - when loading a tracker file with an empty regime start
    - when new trades are recorded into the tracker
  - The backfill is skipped for explicit history archives (`includes_legacy_history=true`).
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_strategy_tracker_regime.py` now locks:
    - load-time backfill from existing trades
    - incremental update when earlier trades are recorded later
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_strategy_tracker_regime.py` → `5 passed`
  - `./.venv/bin/pytest -q` → `462 passed, 3 skipped`
- Runtime truth after one-shot refresh:
  - `strategy_tracker-paper.json.accounting.current_regime_started_at` now reads `2026-03-30T09:53:02.731857+00:00`
  - a follow-up `tick(); write_status()` kept runtime health at `GREEN`

## 2026-04-02 — RiskGuard bootstrap fail-closed hardening
- Adversarial review found one remaining P0 blocker in the runtime guard boundary: `get_current_level()` returned `GREEN` when `risk_state` had no rows. That meant a fresh/cleared/missing RiskGuard state could silently permit new entries before the independent guard had produced even one valid row.
- Main contract decision: **no RiskGuard row is not safe**. It is absence of independent risk authority, so runtime must fail closed. The bootstrap/no-row path now returns `RED` instead of `GREEN`.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py` now logs and returns `RiskLevel.RED` whenever `risk_state` exists but has no rows, closing the runtime/health authority split that previously allowed discovery to proceed while health surfaces would still be degraded.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_riskguard.py` now locks `get_current_level()` → `RED` for the empty-row bootstrap case.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_riskguard.py -k 'get_current_level_fails_closed_when_risk_state_has_no_rows or edge_compression or strategy_edge_compression_alert'` → `2 passed`
  - `./.venv/bin/pytest -q` → `463 passed, 3 skipped`
- Residual note: this closes the specific “empty risk DB rowset = false GREEN” fail-open seam. Remaining P1 work is now primarily about stronger current-regime policy semantics and formal acceptance/review closure, not missing risk authority on bootstrap.

## 2026-04-02 — learning surface aligned to current-regime boundary
- Main review delta: after backfilling `current_regime_started_at`, one more semantic mismatch remained: `query_learning_surface_summary()` still used rolling windows / fixed limits (`hours=24`, `limit=50`, `limit=200`) instead of the actual current-regime boundary. That meant the “current regime” label could still be technically false even though the tracker finally had a real regime-start timestamp.
- Main contract decision: when current-regime metadata exists, learning surfaces must honor it directly. Settlement, no-trade, and execution summaries used for operator/regime diagnosis should be filtered by `current_regime_started_at`, not just by generic recent-window heuristics.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/decision_chain.py` now accepts `not_before` in:
    - `query_legacy_settlement_records()`
    - `query_no_trade_cases()`
    - `query_learning_surface_summary()`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` now threads `not_before` through canonical settlement-event readers and execution-event summaries.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now loads tracker accounting and passes `current_regime_started_at` into `query_learning_surface_summary()`, then surfaces that timestamp in `status.learning.current_regime_started_at`.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_db.py` now proves `query_learning_surface_summary()` excludes pre-regime settlements and no-trades when `not_before` is provided.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now proves `write_status()` passes `current_regime_started_at` into the learning surface and preserves it in status output.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_db.py -k 'learning_surface_summary_respects_current_regime_start or learning_surface_summary' tests/test_strategy_tracker_regime.py -k 'current_regime'` → `4 passed`
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'status_passes_current_regime_start_to_learning_surface or status_strategy_merges_learning_surface'` → `2 passed`
  - `./.venv/bin/pytest -q` → `465 passed, 3 skipped`
- Runtime truth after one-shot refresh:
  - `strategy_tracker-paper.json.accounting.current_regime_started_at` remains `2026-03-30T09:53:02.731857+00:00`
  - `tick(); write_status()` kept runtime health at `GREEN`
  - `status.learning.current_regime_started_at` is now emitted from the live status surface.

## 2026-04-02 — runtime enum/value reconciliation in status
- Detailed review found one concrete P0-D residual mismatch in the runtime truth surface: `status_summary` could emit `portfolio.positions[*].chain_state = "unknown"` while simultaneously emitting `runtime.chain_state_counts = {"ChainState.UNKNOWN": ...}` because the counter path stringified enum objects instead of normalizing their value.
- Main contract decision: runtime/operator truth surfaces must use one normalized textual representation for enum-backed fields. Status payloads should not depend on Python enum `repr` details.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now normalizes enum-backed values through `_enum_text(...)` before writing:
    - `portfolio.positions[*].chain_state`
    - `portfolio.positions[*].exit_state`
    - `runtime.chain_state_counts`
    - `runtime.exit_state_counts`
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks that an enum-backed `ChainState.UNKNOWN` position yields `chain_state == "unknown"` and `chain_state_counts == {"unknown": 1}`, not mixed `ChainState.UNKNOWN` keys.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'enum_backed_runtime_keys or status_passes_current_regime_start_to_learning_surface or status_strategy_merges_learning_surface'` → `3 passed`
  - `./.venv/bin/pytest -q` → `466 passed, 3 skipped`
- Residual note: this closes the specific multi-surface enum/value mismatch that review found in `status_summary`. Remaining work is now less about representational drift and more about final acceptance/review closure plus any deeper current-regime policy semantics we still choose to land.

## 2026-04-02 — status risk consistency fail-closed
- Adversarial review found a concrete operator-truth seam: `status_summary` could still show headline `risk.level = GREEN` while the embedded cycle already declared `failed=true` and key learning/execution surfaces were unavailable. That made the first risk color look calmer than the actual system state.
- Main contract decision: top-level `status.risk` is an operator-facing headline, not a raw RiskGuard mirror. When the cycle has failed, when cycle risk disagrees with RiskGuard risk, or when execution/learning/no-trade summaries are unavailable, `status.risk.level` must fail closed and the inconsistency must be explicit.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now writes:
    - `risk.riskguard_level` = underlying RiskGuard value
    - `risk.consistency_check = { ok, issues, cycle_risk_level }`
    - `risk.level = "RED"` whenever cycle failure or summary-surface inconsistency exists
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks both:
    - normal GREEN path with `riskguard_level` + `consistency_check.ok=true`
    - fail-closed RED escalation when cycle failed / cycle risk mismatched / execution summary unavailable
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'status_escalates_risk_when_cycle_failed_or_query_errors or enum_backed_runtime_keys or status_passes_current_regime_start_to_learning_surface or status_strategy_merges_learning_surface'` → `4 passed`
  - `./.venv/bin/pytest -q tests/test_healthcheck.py tests/test_riskguard.py -k 'risk_state_has_no_rows or healthcheck'` → `8 passed`
  - `./.venv/bin/pytest -q` → `466 passed, 3 skipped`
- Small delegated side update: a subagent aligned `~/workspace-venus/memory/known_gaps.md` so stale settlement/status claims were retired and the DST item was narrowed to its still-open historical rebuild question.

## 2026-04-02 — remove regime-scoped sample caps from learning summaries
- Final detailed review found one remaining P1 inconsistency in the current-regime learning surface: once `not_before=current_regime_started_at` was introduced, `query_learning_surface_summary()` still silently capped regime-scoped truth (`settlement_limit=50`, `execution_limit=200`, and `query_no_trade_cases()` default 200-row scan). That meant the surface was “regime-aligned” but still not full current-regime truth.
- Main contract decision: when a real regime boundary is provided, learning summaries must scan the full regime window rather than truncating at legacy convenience caps. Limits remain for generic recent-window queries, but **not** for regime-scoped operator truth.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/db.py` now allows uncapped reads for:
    - `query_settlement_events(limit=None, not_before=...)`
    - `query_authoritative_settlement_rows(limit=None, not_before=...)`
    - `query_execution_event_summary(limit=None, not_before=...)`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/state/decision_chain.py` now allows:
    - `query_legacy_settlement_records(limit=None, not_before=...)`
    - uncapped `query_no_trade_cases(..., not_before=...)`
    - `query_learning_surface_summary()` to disable the legacy caps whenever `not_before` is present
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_db.py` now proves that a regime window containing:
    - 55 settlements
    - 55 no-trades
    - 205 execution events
    is reported in full rather than truncated to 50/200.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_db.py -k 'learning_surface_summary_does_not_cap_regime_scoped_samples or learning_surface_summary_respects_current_regime_start or learning_surface_summary'` → `3 passed`
  - `./.venv/bin/pytest -q` → `468 passed, 3 skipped`
- Residual note: this closes the remaining “boundary-aware but still truncated” learning-surface blocker from detailed review. Remaining P0/P1 work is now much closer to formal acceptance/closure than to further semantic rewiring.

## 2026-04-02 — top-level execution summary aligned to current regime
- One residual mismatch remained after uncapping the learning surface: `status.learning.execution` had become current-regime scoped, but top-level `status.execution` was still all-history. That meant a single status file could still mix two execution horizons.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/observability/status_summary.py` now passes `current_regime_started_at` into `query_execution_event_summary()` and stamps `status.execution.current_regime_started_at` when that boundary exists.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_pnl_flow_and_audit.py` now locks that both learning and top-level execution receive the same current-regime boundary.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'status_passes_current_regime_start_to_learning_surface or status_escalates_risk_when_cycle_failed_or_query_errors or enum_backed_runtime_keys or status_strategy_merges_learning_surface'` → `4 passed`
  - `./.venv/bin/pytest -q` → `468 passed, 3 skipped`
- Residual note: with this slice, current-regime settlement / no-trade / execution summaries are all aligned to the same boundary. Remaining P1-E work is now more about whether to add stronger policy semantics, not about mismatched scopes across current-regime truth surfaces.

## 2026-04-02 — strategy-tracker failure is no longer silent GREEN
- One remaining P1 policy seam was that `riskguard.tick()` swallowed `load_tracker()` / strategy-diagnostics failures into empty summaries and a synthetic GREEN `strategy_signal_level`. That meant the current-regime strategy signal surface could disappear without any operator-facing degradation.
- Main contract decision: missing strategy-tracker diagnostics is not neutral. It is absence of strategy-signal authority, so `strategy_signal_level` must fail to YELLOW and the error must be surfaced explicitly.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/riskguard/riskguard.py` now records `strategy_tracker_error` when tracker loading fails and treats that as `strategy_signal_level = YELLOW` instead of silently GREEN.
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_riskguard.py` now locks `load_tracker()` failure → `YELLOW` with a surfaced `strategy_tracker_error`.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_riskguard.py -k 'strategy_edge_compression_alert or strategy_tracker_unavailable or risk_state_has_no_rows'` → `3 passed`
  - `./.venv/bin/pytest -q tests/test_healthcheck.py -k 'healthcheck'` → `7 passed`
  - `./.venv/bin/pytest -q` → `469 passed, 3 skipped`
- Residual note: this closes the “tracker missing but still GREEN” seam. The remaining P1-E space is now mostly about whether to introduce stronger policy semantics, not about silent disappearance of current-regime diagnostics.

## 2026-04-02 — P0/P1 acceptance checkpoint
- Detailed review lane is now effectively clear for the current base:
  - concrete review blockers found during this round were:
    - enum/value mismatch in runtime status
    - boundary-aware-but-capped learning summaries
  - both are now landed and covered
- Adversarial blocker slices closed this round:
  - `status.risk` now fails closed on cycle/runtime summary inconsistency
  - RiskGuard bootstrap no-row path now fails closed
  - weak `opening_inertia` edge-compression pressure was downgraded by a stricter sample/span contract
  - restart-time `zeus.db` / `risk_state-paper.db` corruption was operationally recovered and current runtime truth is healthy
- Current acceptance evidence:
  - `./.venv/bin/pytest -q` → `468 passed, 3 skipped`
  - `./.venv/bin/python scripts/healthcheck.py` → `healthy: true`
  - `risk_level: GREEN`
  - `status_contract_valid: true`
  - `riskguard_contract_valid: true`
  - `recommended_commands: []`
- Queue consequence:
  - `P1-F` detailed review lane can close as DONE for the current P0/P1 base.
  - `P1-G` adversarial review lane can close as DONE for the current P0/P1 base.
  - `P0-D` is no longer “active unresolved blocker work”; it moves to REVIEW as a structurally closed runtime base with only optional future tightening left.

## 2026-04-02 — P0-D runtime base accepted
- After the remaining runtime-truth fixes landed (enum normalization, bootstrap risk fail-close, top-level status risk fail-close) and the runtime was recovered to a healthy state, there are no longer concrete unresolved blockers inside the P0-D runtime spine itself.
- Acceptance evidence at this point:
  - daemon/riskguard healthy in launchd
  - `status_contract_valid: true`
  - `riskguard_contract_valid: true`
  - `risk_level: GREEN`
  - no recommended commands outstanding
  - full suite `468 passed, 3 skipped`
- Queue consequence:
  - `P0-D` can now close as DONE for the current base
  - remaining active work is P1-E policy/learning semantics, not runtime-spine correctness

## 2026-04-02 — P1-E current-base closure
- After the remaining P1-E blockers from this round were closed — regime-boundary alignment, uncapped regime summaries, top-level execution/current-regime alignment, and fail-closed behavior when strategy diagnostics disappear — there are no more concrete P1 blockers on the current live-ready base.
- What is now complete for the current base:
  - authoritative settlement payloads are usable and non-degraded in current runtime
  - current-regime tracker metadata is truthful and backfilled
  - settlement / no-trade / execution summaries all honor the same regime boundary
  - strategy/current-regime recommendations are durable, explainable, and split into autosafe vs review-required
  - missing or stale control/risk/status surfaces fail closed instead of silently reading as safe
  - strategy diagnostics no longer disappear into synthetic GREEN when the tracker fails
- What is explicitly **not** being treated as remaining P1 blocker work:
  - stronger policy automation semantics
  - richer learned gating policy
  - forecast-layer de-hardcode
  - learned decision/timing policy
- These remain future work, not blockers for the current P0/P1 base.
- Queue consequence:
  - `P1-E` can close as DONE for the current base
  - the active backlog now shifts entirely to future-phase work rather than unresolved P0/P1 closure

## 2026-04-02 — phase shift to P2-H
- With `P0-D`, `P1-E`, `P1-F`, and `P1-G` closed for the current base, the active program is no longer runtime-spine closure; it is the first forecast-layer de-hardcode lane.
- Queue consequence:
  - `P2-H` is now the next READY lane
  - `P2-I` remains blocked behind richer, cleaner forecast-layer evidence
- First principle for the next lane:
  - do not jump straight to learned decision policy
  - first create a clean forecast-layer seam where `day0` and `dayN` uncertainty policy can evolve without being hardcoded into unrelated modules

## 2026-04-02 — P2-H first seam: forecast uncertainty policy extraction
- The first P2-H slice is intentionally behavior-preserving. Instead of changing forecast math immediately, it extracts the current hardcoded sigma choices behind a dedicated forecast-layer seam so later de-hardcode work can proceed without touching unrelated modules.
- Implementation delta:
  - new module: `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - `analysis_bootstrap_sigma(unit)`
    - `day0_post_peak_sigma(unit, peak_confidence)`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/market_analysis.py` now gets its bootstrap sigma through the new forecast-layer seam instead of calling `sigma_instrument()` directly.
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/day0_signal.py` now gets its post-peak sigma through the new seam instead of embedding the formula inline.
- Why this slice first:
  - it keeps output behavior unchanged
  - it creates one explicit place for future heteroscedastic sigma / lead-continuous sigma upgrades
  - it reduces future blast radius when P2-H stops using today’s hardcoded policy
- Touched tests:
  - new `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` locks current behavior at the seam boundary
  - existing day0/instrument tests still pass unchanged
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or Day0Signal or observation_weight'` → `17 passed`
  - `./.venv/bin/pytest -q` → `472 passed, 3 skipped`

## 2026-04-02 — P2-H second seam: day0 observation-weight policy extraction
- The next P2-H slice continues the same low-blast-radius approach: extract the hardcoded day0 observation-dominance constants and temporal-closure formula behind the forecast-layer seam, without changing behavior yet.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py` now also owns:
    - `day0_temporal_closure_weight(...)`
    - `day0_observation_weight(...)`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/day0_signal.py` now delegates both `_temporal_closure_weight()` and `observation_weight()` to the centralized seam instead of carrying the policy inline.
- Why this slice matters:
  - it pulls more of the day0 constant policy out of the signal class
  - it creates one explicit surface for later `solar backbone + online residual update` work
  - it keeps the behavior stable while shrinking the future refactor blast radius
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks closure-weight endpoints and pre-sunrise/post-sunset observation-weight behavior at the seam level.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure'` → `8 passed`
  - `./.venv/bin/pytest -q` → `474 passed, 3 skipped`

## 2026-04-02 — P2-H third seam: lead/spread-aware analysis sigma interface
- A local research pass over `/Users/leofitz/Downloads/外部调研.md` pointed to the next smallest correct forecast-layer move: stop dropping `lead_days` and `ensemble_spread` before the analysis sigma seam. The research recommendation is explicit that day1..day7 sigma should become lead-continuous / heteroscedastic later; the right next step is to carry those covariates into the seam now, without inventing a new heuristic yet.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - `analysis_bootstrap_sigma(...)` now accepts `lead_days` and `ensemble_spread`
    - current numeric behavior is unchanged
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/market_analysis.py`
    - now passes both `lead_days` and the current ensemble spread into that seam
- Why this matters:
  - it freezes the correct interface boundary for the future lead-continuous / heteroscedastic sigma policy
  - it avoids spreading another temporary heuristic into `MarketAnalysis`
  - it keeps this slice behavior-preserving and reviewable
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks that passing `lead_days` / `ensemble_spread` preserves today’s baseline behavior.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure'` → `8 passed`
  - `./.venv/bin/pytest -q` → `474 passed, 3 skipped`

## 2026-04-02 — P2-H fourth seam: day0 residual-blend extraction
- This slice continues the day0-side seam cleanup for forecast-layer work. The existing “observed high as hard floor + residual upside scaled by observation weight” rule is now centralized behind the forecast uncertainty layer instead of being embedded inline in `Day0Signal`.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `day0_blended_highs(...)`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/day0_signal.py`
    - now delegates final-high residual fusion to the seam helper instead of carrying the vector formula inline
- Why this slice matters:
  - it completes a second piece of the day0 forecast policy extraction
  - it makes future `solar backbone + online residual update` work easier to land without mixing policy changes into probability plumbing
  - it remains behavior-preserving and low blast radius
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks the hard-floor and observation-weight endpoint behavior of `day0_blended_highs(...)`.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs'` → `9 passed`
  - `./.venv/bin/pytest -q` → `475 passed, 3 skipped`

## 2026-04-02 — P2-H first behavior-changing step: lead-continuous sigma inflation
- This is the first deliberate behavior change in the forecast-layer lane. Research guidance emphasized that day1..day7 uncertainty should not stay flat across all leads, and that a modest underdispersion correction is warranted. The seam now starts to express that continuously instead of treating all non-day0 leads as identical.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `analysis_lead_sigma_multiplier(lead_days)`
    - `analysis_bootstrap_sigma(...)` now applies a conservative lead-continuous inflation:
      - day0: `1.0x`
      - day3: `1.1x`
      - day6+: `1.2x`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/market_analysis.py`
    - inherits that lead-continuous sigma behavior automatically through the seam
- Why this is the right first behavior change:
  - small blast radius
  - continuous, not per-lead stepwise
  - grounded in the existing research note that raw ensemble uncertainty is underdispersive by roughly 15–20%
  - moves the system off the fully hardcoded “same sigma for every non-day0 lead” regime without introducing station-specific or model-specific complexity yet
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks the multiplier endpoints and continuity
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma'` → `10 passed`
  - `./.venv/bin/pytest -q` → `476 passed, 3 skipped`

## 2026-04-02 — P2-H spread-aware sigma seam (behavior-preserving)
- After the first lead-continuous behavior change, the next bounded step from the research roadmap was to stop dropping ensemble spread at the forecast sigma seam. This slice adds the spread multiplier boundary without changing outputs yet.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `analysis_spread_sigma_multiplier(ensemble_spread, unit=...)`
    - `analysis_bootstrap_sigma(...)` now composes:
      - base instrument sigma
      - lead multiplier
      - spread multiplier
  - current spread multiplier is intentionally neutral (`1.0`) so behavior is preserved until the next explicit P2-H behavior change chooses a real heteroscedastic policy.
- Why this slice matters:
  - the correct covariate boundary is now fully present for day1..day7 sigma policy
  - the next heteroscedastic step can happen inside one seam instead of another consumer rewrite
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks that the spread multiplier is neutral for now.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma'` → `11 passed`
  - `./.venv/bin/pytest -q` → `477 passed, 3 skipped`

## 2026-04-02 — P2-H second behavior-changing step: mild spread-aware sigma inflation
- With the spread seam in place, the next small forecast-layer change makes it active in a tightly bounded way. This is the first heteroscedastic move, but deliberately conservative.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - `analysis_spread_sigma_multiplier(...)` now ramps from `1.0x` to `1.1x`
    - baseline spread is defined as `4 * sigma_instrument(unit)`
    - any spread above that baseline saturates at the 10% uplift
  - composed with the earlier lead-continuous multiplier, analysis sigma is now both:
    - lead-aware
    - mildly spread-aware
- Why this is a reasonable first heteroscedastic step:
  - bounded to +10% from spread alone
  - preserves the existing instrument-noise anchor
  - uses the already-extracted seam rather than adding a new hardcoded branch elsewhere
  - still small enough to be reviewable before a richer learned sigma policy lands
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks the spread-multiplier endpoints and the composed sigma behavior.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma'` → `11 passed`
  - `./.venv/bin/pytest -q` → `477 passed, 3 skipped`

## 2026-04-02 — P2-H mean/location seam extraction
- After the first sigma-side behavior changes, the next clean forecast-layer move was to stop passing raw `member_maxes` straight into `MarketAnalysis` without any named boundary. The system now has an explicit seam for future lead-continuous mean/location correction, while keeping current behavior unchanged.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `analysis_member_maxes(member_maxes, unit=..., lead_days=...)`
    - current behavior is identity / no-op
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/market_analysis.py`
    - now routes member maxima through that seam before bootstrap analysis
- Why this slice matters:
  - it opens the correct boundary for future lead-continuous mean/location correction
  - it prevents future de-hardcode work from being forced directly into `MarketAnalysis`
  - it keeps this slice behavior-preserving while moving more of forecast policy into one place
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks that `analysis_member_maxes(...)` is identity for now.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma or member_maxes'` → `12 passed`
  - `./.venv/bin/pytest -q` → `478 passed, 3 skipped`

## 2026-04-02 — P2-H day0 backbone anchor seam
- The next P2-H seam opens the exact boundary where a future solar-backbone / online residual model will plug into day0 logic, without changing current behavior yet.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `day0_backbone_high(observed_high, current_temp, daylight_progress)`
    - current behavior remains: backbone anchor = observed high
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/day0_signal.py`
    - now asks the seam for `backbone_high` before residual blending instead of hardwiring the anchor inline
- Why this matters:
  - it freezes the exact insertion point for the future `solar backbone + online residual update` model
  - it keeps the existing day0 trading behavior unchanged while making the next P2-H day0 upgrade much safer to land
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks that `day0_backbone_high(...)` is identity-to-observed-high for now
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma or member_maxes or backbone_high'` → `13 passed`
  - `./.venv/bin/pytest -q` → `479 passed, 3 skipped`

## 2026-04-02 — P2-H mean-offset seam
- The next forecast-layer seam mirrors what has already been done for sigma: create an explicit place where future lead-continuous mean/location correction can land, without changing today’s forecasts yet.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `analysis_mean_offset(unit, lead_days, ensemble_mean)`
    - current behavior is neutral (`0.0`)
    - `analysis_member_maxes(...)` now routes member maxima through that offset seam
- Why this matters:
  - future mean/location correction now has a single forecast-layer insertion point
  - it keeps `MarketAnalysis` out of the business of inventing forecast-mean policy inline
  - it preserves current outputs while improving the architecture for later P2-H work
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks the mean-offset seam as identity/no-op for now.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma or member_maxes or backbone_high or mean_offset'` → `14 passed`
  - `./.venv/bin/pytest -q` → `480 passed, 3 skipped`

## 2026-04-02 — P2-H day0 residual-update seam
- This slice finishes the other half of the day0 backbone architecture: not just where the backbone anchor comes from, but where a future online residual correction would enter that backbone.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `day0_backbone_residual_adjustment(...)`
    - `day0_backbone_high(...)` now composes:
      - observed-high anchor
      - residual adjustment seam
    - current residual adjustment is neutral (`0.0`)
- Why this matters:
  - it opens the exact insertion point for a future online residual / Kalman-style update
  - it keeps `Day0Signal` free of yet another future policy embed
  - current behavior remains unchanged
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks that the new residual adjustment seam is neutral for now.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma or member_maxes or backbone_high or mean_offset or residual_adjustment'` → `15 passed`
  - `./.venv/bin/pytest -q` → `481 passed, 3 skipped`

## 2026-04-02 — P2-H forecast sigma context surface
- The latest P2-H slice makes the forecast uncertainty seam more inspectable. Instead of only returning a scalar sigma, the seam can now explain how that sigma was built.
- Implementation delta:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/signal/forecast_uncertainty.py`
    - new `analysis_sigma_context(...)`
    - includes: `base_sigma`, `lead_multiplier`, `spread_multiplier`, `final_sigma`
    - `analysis_bootstrap_sigma(...)` now reuses that context instead of recomputing the pieces ad hoc
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/src/strategy/market_analysis.py`
    - now stores `_sigma_context` and exposes `sigma_context()`
- Why this matters:
  - future P2-H changes can be evaluated against an explicit sigma decomposition instead of a hidden scalar
  - it makes later audit / artifact surfacing much easier if we decide to expose forecast uncertainty internals
  - current runtime behavior is unchanged by this slice
- Touched tests:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/tests/test_forecast_uncertainty.py` now locks the sigma-context decomposition values.
- Verification evidence:
  - `./.venv/bin/pytest -q tests/test_forecast_uncertainty.py tests/test_day0_signal.py tests/test_instrument_invariants.py -k 'sigma or observation_weight or temporal_closure or blended_highs or lead_sigma or spread_sigma or member_maxes or backbone_high or mean_offset or sigma_context'` → `15 passed`
  - `./.venv/bin/pytest -q` → `482 passed, 3 skipped`
