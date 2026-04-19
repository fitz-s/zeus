# Phase 8 Contract — Code-Ready Low Shadow (Route A)

**Written**: 2026-04-18 post P7B-followup (`2adcbc9`).
**Branch**: `data-improve`.
**Mode**: Gen-Verifier (team-lead direct exec + critic-carol fresh spawn).
**Predecessor handoff**: P6/P7A/P7B/P7B-followup CLOSED; handoff at `team_lead_handoff.md` §"Phase 7B-followup".

## Ruling applied

User 2026-04-18: "延续 Gen-Verifier，critic-beth 轮换，采取路线 A，目前还不能导入任何 TIGGE 数据做校准，起草 phase8 并开工".

Route A = code-only P8. No data activation. No Golden Window lift. critic-carol fresh spawn (critic-beth retires after P7B 3-cycle run).

## Scope — ONE commit delivers

### S1 — `run_replay` metric threading
- `src/engine/replay.py:run_replay` (L1932): add `temperature_metric: str = "high"` kwarg to public signature.
- Thread through to `_replay_one_settlement(ctx, city, target_date, settlement, temperature_metric=temperature_metric)` at the L2001 call site (`_replay_one_settlement` ALREADY accepts the kwarg — L1107; was never threaded at public boundary).
- Default `"high"` preserves backward compat for every existing caller.
- No change to `_forecast_rows_for` / `_forecast_reference_for` / `_forecast_snapshot_for` (these already thread `temperature_metric` internally — only the public-entry passthrough was missing).
- `run_wu_settlement_sweep` and `run_trade_history_audit` passthrough lanes: **out of scope** (those are P9+ extensions).

### S2 — `cycle_runner.py` DT#6 rewire
- `src/engine/cycle_runner.py:180-181`: remove `raise RuntimeError(...)` on `portfolio_loader_degraded=True`.
- Replace with: `riskguard.tick_with_portfolio(portfolio)` call + degraded-mode summary flags + continue cycle.
- Behavior contract (from `zeus_dual_track_architecture.md` §6 DT#6 law): process MUST NOT raise; must disable new-entry paths; must keep monitor / exit / reconciliation running read-only.
- Entry-path suppression mechanism: `tick_with_portfolio` returns `RiskLevel.DATA_DEGRADED` when `portfolio.portfolio_loader_degraded=True` (riskguard.py L1029-1030). Downstream entry gates already honor `risk_level != GREEN`. No gate-side changes required.
- `summary["portfolio_degraded"] = True` emitted for operator visibility.

### S3 — B093 half-2 (replay `forecasts` → `historical_forecasts_v2`)
- **DEFERRED to P9**. Root cause: legacy `forecasts` table has per-row `forecast_high + forecast_low` columns (dual-metric), while `historical_forecasts_v2` keys rows on `temperature_metric`. Migration requires data present in v2 to validate behavior. v2 is zero-row per Golden Window policy (user ruling 2026-04-18).
- Comment at `src/engine/replay.py:245-247` already flags this. No code change in P8.

### S4 — `monitor_refresh.py` LOW wiring
- **DROPPED from P8**. Monitor handles LIVE positions; shadow mode = no LOW positions taken. S4 is Gate F (P9) scope.
- Scout-carl's "zero `temperature_metric` refs" finding catalogued in P9 forward-log.

## Acceptance gates

1. **Full regression ≤ baseline**: pre-S1/S2 baseline is `2adcbc9` post-P7B-followup: 144 failed / 1842 passed / 95 skipped / 7 subtests. Post-P8: ≤144 failed, ≥1842 passed, zero new failures.
2. **P8 new tests**: `tests/test_phase8_shadow_code.py` adds R-BP..R-BQ (at least 4 sub-tests) — all GREEN.
3. **P5/P6/P7A/P7B regression targeted**: tests in `tests/test_phase5*.py`, `tests/test_phase6_day0_split.py`, `tests/test_phase7a_metric_cutover.py` all unchanged-green.
4. **critic-carol wide-review PASS**.
5. **No writes to v2 tables**: Golden Window preserved. `git diff HEAD` includes zero INSERT/UPDATE statements targeting `*_v2` tables.
6. **Backward compat**: `run_replay(start_date, end_date)` without `temperature_metric` kwarg behaves identically to pre-S1 code — verified by R-BP.2 backward-compat test.

## Hard constraints (forbidden moves)

- **No TIGGE data import** — no `python scripts/extract_tigge_*` runs, no `rebuild_calibration_pairs_v2` calls, no `refit_platt_v2` runs.
- **No v2 table population** — all 5 v2 tables stay zero-row.
- **No evaluator changes** — evaluator already metric-aware per scout 2026-04-18.
- **No settlement-writer code** — Python doesn't have one; settlements arrive externally.
- **No monitor_refresh changes** — P9 scope.
- **No `run_replay` signature change other than appending kwarg** — preserve positional arg contract.
- **No `raise RuntimeError` reintroduction** — DT#6 law is a durable antibody.
- **Zero new SQL DDL** — v2 schema unchanged.

## Structural antibodies to install

### R-BP — run_replay metric threading (2 sub-tests)
- **R-BP.1** `test_run_replay_temperature_metric_threads_to_replay_one_settlement`: Call `run_replay(start, end, temperature_metric="low")`; monkeypatch `_replay_one_settlement` to capture the `temperature_metric` kwarg. Assert captured value is `"low"`.
- **R-BP.2** `test_run_replay_default_temperature_metric_is_high_backward_compat`: Call `run_replay(start, end)` with no kwarg. Monkeypatch `_replay_one_settlement` to capture. Assert captured value is `"high"`.

### R-BQ — cycle_runner DT#6 rewire (2 sub-tests)
- **R-BQ.1** `test_run_cycle_degraded_portfolio_does_not_raise`: Monkeypatch `load_portfolio` to return a `PortfolioState` with `portfolio_loader_degraded=True`. Call `run_cycle(OPENING_HUNT)`. Assert no `RuntimeError`. Assert `summary["portfolio_degraded"] is True`. Assert `summary["risk_level"]` reflects degraded state (not `GREEN`).
- **R-BQ.2** `test_run_cycle_degraded_portfolio_calls_tick_with_portfolio`: Monkeypatch `riskguard.tick_with_portfolio` to record calls. Call `run_cycle` with degraded portfolio. Assert `tick_with_portfolio` was called exactly once with the degraded `PortfolioState`.

Optional R-BR (if feasible without data):
- **R-BR** `test_run_replay_low_metric_settlement_query`: Call `run_replay(..., temperature_metric="low")` with a zero-row v2 database. Assert the call completes (no exceptions), returns `ReplaySummary` with `n_settlements` reflecting whatever the legacy settlements query returned. Codifies the "code-ready, data-pending" state.

## Process notes — P2.1 / P1.1 enforced

- Team-lead implements directly (no exec subagent this phase — Gen-Verifier pattern, small surface).
- Pre-`git add`: `git status --short` snapshot; isolate unexpected content.
- Commit boundary owned by team-lead.
- One commit candidate for critic-carol's wide review.

## critic-carol fresh-spawn brief

critic-carol inherits disk-durable learnings from critic-beth's cycles 1-3:
- `phase5_evidence/critic_beth_phase5fix_wide_review.md`
- `phase5_evidence/critic_beth_phase5c_wide_review_final.md`
- `phase7_evidence/critic_beth_phase7a_wide_review.md`
- `phase7_evidence/critic_beth_phase7a_learnings.md`
- `phase7_evidence/critic_beth_phase7b_wide_review.md`
- `phase7_evidence/critic_beth_phase7b_learnings.md`

Plus operating contract P1.1 / P2.1 / P3.1 (`team_lead_operating_contract.md`).

Critic-carol responsibilities:
1. L0.0 peer-not-suspect discipline.
2. P3.1 vocabulary grep on contract-inverting / guard-removing code (DT#6 rewire qualifies — removes a `raise RuntimeError` guard).
3. Two-seam principle: if any write-side changes, audit the symmetric read-side.
4. Pre-commitment predictions before diving into diff.
5. Persist verdict + learnings to `phase8_evidence/critic_carol_phase8_wide_review.md` + `critic_carol_phase8_learnings.md`.
6. If Write/Edit is blocked (as happened to critic-beth in cycle 3), return content in final message for team-lead to persist.

## R-letter range

- P8 opens at **R-BP**.
- Reserved R-BP..R-BQ (and optional R-BR).
- R-BS+ available for P9 packets (monitor_refresh, Gate F risk-critical items, etc.).

## Forward-log (carried to P9)

Beyond the standing P9 agenda:
1. **B093 half-2** — `_forecast_rows_for` migration to `historical_forecasts_v2` (needs v2 data; lands after Golden Window lift).
2. **monitor_refresh LOW wiring** — zero `temperature_metric` refs today (scout-carl 2026-04-18); required pre-Gate F for any LOW position monitoring.
3. **Settlement query metric filter** in `run_replay` L1968 — legacy settlements doesn't have metric column; wait for v2 settlement population.
4. **Shadow report generation** — `capture_shadow_lineage` / `shadow_signals` writer audit; needed to close Gate E properly once data flows.

## Evidence layout

- This contract: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase8_contract.md`
- Evidence dir: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase8_evidence/`
- critic-carol wide review: `phase8_evidence/critic_carol_phase8_wide_review.md`
- critic-carol learnings (if deliverable): `phase8_evidence/critic_carol_phase8_learnings.md`
- Scout report (implicit, not persisted — findings incorporated in this contract §"Scope"/§"Hard constraints"): team-lead scout dispatch 2026-04-18 to explore subagent.

## Gate E position

P8 route A lands **code-ready** Gate E — all pipeline code is metric-aware through the public boundary. Actual Gate E closure (shadow trace evidence) requires data, which blocks on Golden Window lift.

P8 does NOT claim Gate E complete. It claims **"Gate E code prerequisites complete"**.

---

*Authored*: team-lead (Opus, main context), 2026-04-18.
*Authority basis*: plan.md §"Phase 8"; zeus_dual_track_architecture.md §6 (DT#6); scout-carl 2026-04-18 authoritative source digest.
