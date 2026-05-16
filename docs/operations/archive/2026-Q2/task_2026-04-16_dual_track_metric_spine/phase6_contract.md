# Phase 6 Contract — Day0 Runtime Split

**Issued**: 2026-04-18 post-compact, sniper-mode.
**Basis**: `team_lead_operating_contract.md` P1-P7 (binding). This is the single contract doc per P2 (contract-based scope).
**Target commit**: ONE. Scope locked at issue. Mid-phase drift → defer to Phase 7.

## Why Phase 6 is dangerous (silent corruption)

Today `src/signal/day0_signal.py:85-91` raises `NotImplementedError` for LOW. That guard masks a deeper structural bug at TWO callsites:
- `src/engine/evaluator.py:819/825` passes `remaining_member_extrema` (ONE metric-dispatched array) to BOTH `member_maxes_remaining` AND `member_mins_remaining`
- `src/monitor_refresh.py:306/311/312` does the same with variable `remaining_member_maxes` (misnamed)

Upstream: `remaining_member_maxes_for_day0(temperature_metric=LOW)` already dispatches to MIN internally. So post-guard-removal both slots receive the same metric-conditional array, impossible to route maxes+mins correctly to Day0Router.

**Phase 6 does not "patch 2 lines". Phase 6 fixes the API contract so the category is unwritable (Fitz P4: make wrong code impossible).**

## Deliverables (single commit)

### NEW files
1. `src/signal/day0_high_signal.py` — `Day0HighSignal` (hard-floor: `max(obs_high_so_far, remaining_high)`)
2. `src/signal/day0_low_nowcast_signal.py` — `Day0LowNowcastSignal` (ceiling: `min(obs_low_so_far, blended_remaining)`; nowcast weight)
3. `src/signal/day0_router.py` — `Day0Router.route(inputs) -> Day0HighSignal | Day0LowNowcastSignal`; rejects low + `causality_status ∉ {OK, N/A_CAUSAL_DAY_ALREADY_STARTED}`

### MODIFIED files
4. `src/signal/day0_window.py` (or wherever `remaining_member_maxes_for_day0` lives) — rename to `remaining_member_extrema_for_day0`; return type `RemainingMemberExtrema(maxes: np.ndarray | None, mins: np.ndarray | None)` (frozen dataclass); HIGH sets maxes, LOW sets mins, the other is `None`
5. `src/engine/evaluator.py:~806-833` — `Day0Router.route(Day0SignalInputs(...))` replaces `Day0Signal(...)` direct construction; pass extrema dataclass
6. `src/monitor_refresh.py:~286-319` — same Router replacement
7. `src/signal/day0_signal.py:85-91` — remove NotImplementedError guard. Preserve class body only if still imported elsewhere; prefer deletion if Router covers all callers
8. `src/state/portfolio.py` — DT#6 `load_portfolio()` authority-loss graceful-degradation: disable new-entry paths, keep monitor/exit/reconciliation read-only, surface degraded state to operator. Integrates with `PortfolioState.authority` field (5A)
9. `src/riskguard/riskguard.py` — B055 trailing-loss 2h staleness absorption into DT#6 degraded path

### NEW tests
10. `tests/test_phase6_day0_split.py` — R-BA..R-BG (≤7 R-letters, ~15-20 tests)
    - R-BA: HIGH path uses MAX array; produces settlement samples ≥ `obs_high_so_far`
    - R-BB: LOW path uses MIN array; produces settlement samples ≤ `obs_low_so_far`
    - R-BC: LOW missing `low_so_far` → raises (clean reject, no silent degrade)
    - R-BD: LOW + `N/A_CAUSAL_DAY_ALREADY_STARTED` → routed through nowcast, NOT historical Platt
    - R-BE: `day0_low_nowcast_signal` does NOT import `day0_high_signal` (AST walk)
    - R-BF: `RemainingMemberExtrema` with both maxes+mins None → raises at construction; metric-mismatch raises
    - R-BG: DT#6 graceful-degradation — authority-loss does not kill cycle; monitor lane runs read-only

## Acceptance gates

1. `pytest tests/test_phase6_day0_split.py` → all GREEN
2. Full regression ≤ 137 failed / ≥ 1783 passed baseline (from `59e271c` post-unblock baseline). No new failures introduced.
3. `grep -n "NotImplementedError" src/signal/day0_signal.py` → zero LOW-guard hits (or file deleted)
4. critic-beth wide-review PASS with L0.0 discipline

## Hard constraints — DO NOT

- Decouple the evaluator.py:825 + monitor_refresh.py:306 fixes from guard removal (both must land same commit)
- Bundle Phase 7 scope (rebuild_v2 METRIC_SPECS iteration, `_tigge_common.py` extraction, replay migration to `historical_forecasts_v2`)
- Re-add obsolete non-live runtime
- Modify `validate_snapshot_contract`, `PortfolioState.authority`, `ModeMismatchError`, `CalibrationMetricSpec` semantics (all locked 5A/5B)
- Add `--track` CLI flags; use spec dataclass pattern (precedent: METRIC_SPECS)
- Mock-heavy tests (use synthetic inputs against real public entry points)
- Write tests by reading implementation first — spec → tests → code, order inviolable (contract P4)

## Pointers (3, per operating contract P6)

1. **This contract** — scope truth
2. **`zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/day0_signal_router.py`** — reference skeleton, adapt to zeus naming/typing conventions
3. **`team_lead_handoff.md`** §"Phase 6 scope" — DT#6 + B055 integration detail, co-landing imperative

## Executable bootstrap

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
git status && git log --oneline -3
pytest tests/test_phase5a_truth_authority.py tests/test_phase5b_low_historical_lane.py tests/test_phase5c_replay_metric_identity.py tests/test_phase5_gate_d_low_purity.py tests/test_phase5_fixpack.py 2>&1 | tail -5
# Expected: all GREEN (P5 regression baseline)
```

## Team protocol during Phase 6

- **Team-lead**: silent. Monitor `git log` passively. Respond only to scope-ruling question or CRITICAL escalation.
- **Exec** (Sonnet, fresh spawn): implementation + R-letter drafting (spec-first, NO impl grep) + test execution. Use subagents aggressively (Explore for codebase scans, debugger for test failures, scientist for invariant verification) to preserve own context.
- **Critic-beth** (retained, Opus): silent standby until commit candidate. Then ONE wide review with L0.0 posture. If ITERATE, one-cycle fix dispatch.
- **Subagents** (ephemeral, team-lead + exec both spawn): scouting, testing, specific file reads. Use the minimal Agent payload; one-shot, no memory carryover.

Phase opens with single dispatch SendMessage + this contract path. Phase closes with commit candidate + critic verdict. No mid-phase check-ins.
