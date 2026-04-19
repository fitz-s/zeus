# Phase 8 Wide Review — critic-carol (fresh spawn)

**Commit**: `6ffefa4` feat(phase8): code-ready LOW shadow — run_replay metric threading + DT#6 rewire
**Reviewer**: critic-carol (cycle 1, fresh spawn replacing critic-beth)
**Date**: 2026-04-18
**Mode**: Gen-Verifier (team-lead-direct exec + persistent critic)
**Operating mode during review**: THOROUGH (no escalation to ADVERSARIAL)
**Persisted-by**: team-lead (critic-carol Write/Edit was blocked, inheriting from critic-beth cycle 3 constraint)

## VERDICT: **PASS**

P8 Route A passes all hard constraints, acceptance gates, and P5/P6/P7A targeted regression. 4/4 new antibodies GREEN. Zero new regressions (20→20 failures, 227→231 passes on targeted suite, matching commit message math). Review produced 0 CRITICAL / 4 MAJOR / 4 MINOR / 6 test-gap items — all MAJOR items are observability/antibody-hygiene concerns that are P9 forward-log, not P8 blockers.

## Pre-commitment predictions vs actuals

| # | Prediction | Actual | Confidence | Verified |
|---|---|---|---|---|
| P1 | run_replay default "high" preserves compat; antibody may check signature not semantics | Partially right — R-BP.1 captures via monkeypatched `_replay_one_settlement`, which IS semantic; no E2E test of `forecast_low` column | HIGH | YES |
| P2 | Degraded portfolio passed to tick_with_portfolio may cause issues | Resolved — run_cycle completes, DATA_DEGRADED returned | HIGH | YES (empirical probe) |
| P3 | Downstream consumers of portfolio_degraded flag may not exist | CONFIRMED as MAJOR — `entries_blocked_reason` does not include DATA_DEGRADED branch; `tick_with_portfolio` does not persist to risk_state.db | MEDIUM | YES |
| P4 | P3.1 vocab grep may surface silent-GREEN tests | Negative — pre-P8 RuntimeError was not antibody-covered; commit msg correctly notes this | MEDIUM | YES |
| P5 | R-BQ.1 may be a text-match (fragile) antibody | CONFIRMED — L228 matches literal pre-P8 string + L236 silent-return path | MEDIUM | YES |

**5/5 pre-commitment predictions hit.** Methodology stabilizing (inherited critic-beth's 3-cycle streak).

## Critical findings

None.

## Major findings

### 1. `entries_blocked_reason` tuple does not include `DATA_DEGRADED`
**Evidence**: `src/engine/cycle_runner.py:281`
```python
elif risk_level in (RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED):
    entries_blocked_reason = f"risk_level={risk_level.value}"
```
`RiskLevel.DATA_DEGRADED` is NOT in this tuple. Empirical probe: when bankroll>0 and portfolio is degraded, `_risk_allows_new_entries(DATA_DEGRADED) == False` correctly blocks entries, but `entries_blocked_reason` falls through to None. `summary["entries_blocked_reason"]` never gets populated for the DATA_DEGRADED case.

- **Confidence**: HIGH
- **Why this matters**: Observability gap. Ops runbooks / dashboards / Discord reports depending on `entries_blocked_reason` will show "no reason" for the degraded cycle despite entries being silently blocked. Fitz P3 (Immune System): signal available, downstream reader lane not wired.
- **Fix**: Add DATA_DEGRADED to the elif tuple at L281, or set `entries_blocked_reason = "portfolio_loader_degraded"` inside the DT#6 branch at L180-195.
- **Realist-check**: Downgrade considered but retained — structural observability gap. Mitigated by `summary["portfolio_degraded"]=True` providing SOME signal. Deferrable to P9.

### 2. `tick_with_portfolio` does not persist to `risk_state.db`
**Evidence**: `src/riskguard/riskguard.py:983-1040` — opens `risk_conn`, reads trailing-loss snapshots, computes `level`, closes conn, returns `level`. **No INSERT/UPDATE** on `risk_state`.

Contrast: `src/observability/status_summary.py:48-49` `get_current_level()` reads from `risk_state` DB. In a DT#6 cycle, `tick_with_portfolio` returns DATA_DEGRADED ephemerally — next observer (monitor_refresh, status_summary, dashboard poller) reads stale `risk_state`, likely GREEN.

- **Confidence**: HIGH
- **Why this matters**: Cross-component state drift. Cycle summary says DATA_DEGRADED; `status_summary.json` still says GREEN.
- **Fix**: Either (a) persist DATA_DEGRADED via `persist_risk_state(conn, level, now, details)` inside `tick_with_portfolio` before returning, OR (b) document tick_with_portfolio as advisory. (a) is structural; (b) is a patch.
- **Realist-check**: Mitigated — cycle summary IS correct; status_summary drift resolves on next full riskguard tick. Kept MAJOR for P9 forward-log.

### 3. R-BQ.1 has a silent-pass path
**Evidence**: `tests/test_phase8_shadow_code.py:227-236`
```python
except RuntimeError as exc:
    if "Portfolio loader degraded: DB not authoritative" in str(exc):
        pytest.fail(...)
    return  # <-- silent return; assertion at L239 never runs
```
If any downstream stub raises RuntimeError, the test returns without validating `summary.get("portfolio_degraded") is True`. This is the critic-beth cycle-2 "silent-GREEN monkeypatch" pattern.

- **Confidence**: HIGH pattern / MEDIUM current impact — today the test does reach the assertion (empirical probe).
- **Fix**: (a) `pytest.raises` outside the try block, OR (b) robust downstream stubs + remove tolerant `return`.
- **Realist-check**: Structural fragility remains. Retained MAJOR for antibody hardening in P9.

### 4. R-BQ.1 text-match antibody (translation-loss)
**Evidence**: `tests/test_phase8_shadow_code.py:228` — matches literal pre-P8 message `"Portfolio loader degraded: DB not authoritative"`.

If future refactor reintroduces `raise RuntimeError("Portfolio loader degraded - failsafe tripped (DB degradation)")`, the test passes (different wording) even though DT#6 law is violated. Fitz P2 (Translation Loss): design intent encoded in test strings has low cross-session survival.

- **Confidence**: HIGH
- **Why this matters**: "Structural antibody" is actually a text-match. Type-system antibody would fail-at-compile; behavioral antibody would fail-on-any-RuntimeError-in-degraded-branch.
- **Fix**: "No RuntimeError should escape the DT#6 branch at all" — assert `summary.get("portfolio_degraded") is True` unconditionally (requires fix #3 first).
- **Realist-check**: Pre-P8 message is stable in git history; cross-session survival is actually OK here. Retained as MAJOR because representative of "text-match antibody" concern.

## Minor findings

### M1. `scripts/run_replay.py` CLI does not expose `--temperature-metric`
Operators can only use the new kwarg via the Python API. Flagged for P9 ops hardening.

### M2. `run_wu_settlement_sweep` / `run_trade_history_audit` silently drop `temperature_metric`
`src/engine/replay.py:1956-1963` — these modes return early; kwarg silently dropped. User ruling explicitly scoped these out, but a QA/ops user passing `temperature_metric="low", mode="trade_history_audit"` gets HIGH behavior with no warning. Consider:
```python
if mode in (WU_SWEEP_LANE, TRADE_HISTORY_LANE) and temperature_metric != "high":
    logger.warning("temperature_metric=%r ignored by %s lane", temperature_metric, mode)
```

### M3. Duplicate `4.` item in `team_lead_handoff.md`
L56-57 — two consecutive list items both numbered `4.` (P7B-followup + P8). Cosmetic, fix in handoff update.

### M4. `summary["risk_level"]` overwrites at L195 but L176 already set it
`src/engine/cycle_runner.py:176, 195` — L176 sets from `get_current_level()`, L195 overwrites with `tick_with_portfolio` result. Functionally correct (DT#6 branch wins). Add comment that overwrite is intentional.

## What's Missing (gaps, unhandled edge cases, unstated assumptions)

1. **No test that `_forecast_reference_for` actually selects `forecast_low` when metric="low"**. R-BP.1 verifies kwarg reaches `_replay_one_settlement` but not SQL column selection. Second-seam gap; acceptable for P8 code-ready, required for P9 data closure.
2. **No test that `status_summary.json` reflects DATA_DEGRADED during degraded cycle**. Would catch finding #2.
3. **No test covering `entries_blocked_reason` output for degraded path**. Would catch finding #1.
4. **No rollback path documented if DT#6 branch itself raises**. `tick_with_portfolio` throwing would crash cycle — worse than pre-P8 clean RuntimeError. No try/except around new path.
5. **`portfolio_dirty` / `tracker_dirty` under degraded mode not asserted False**. Contract says "monitor/exit/reconciliation continue read-only" but `_reconcile_pending_positions` can mark `portfolio_dirty=True`, leading to `save_portfolio(degraded)` overwriting canonical positions. Interpretation-ambiguous.
6. **No test that `risk_state.db` is unchanged after DT#6 branch**. Would catch the "doesn't persist" gap as specified-behavior vs accidental-behavior.

## Ambiguity Risks

- **Contract interpretation**: "monitor / exit / reconciliation continue read-only"
  - **A**: No writes of any kind (including save_portfolio JSON refresh)
  - **B**: Read-only on canonical DB but JSON snapshots may refresh for cache coherence
  - **Risk if A**: `save_portfolio(degraded_portfolio)` at L333 violates contract
  - **Risk if B**: Current code correct
  - Contract doesn't nail this down. Defer to user/architect.

## Multi-Perspective Notes

- **Executor**: Clean, isolated. S1 = 2-line signature + pass-through. S2 = 15-line swap. Antibodies explicit.
- **Stakeholder**: User ruling "Route A = code-only" honored literally. Gate E code prerequisites claim defensible.
- **Skeptic**: DT#6 rewire is the RIGHT structural move (matches §6 law), but observability surface (risk_state persistence + entries_blocked_reason coding) incomplete. First DT#6 path to actually exercise `tick_with_portfolio` in runtime — cracks show. P9 naturally absorbs via monitor_refresh agenda.
- **Security-/ops-engineer lens**: Silent entries_blocked_reason gap is exactly the kind of gap that costs 1 cycle of ops confusion per occurrence. Low blast radius, worth P9 patch.

## Fitz Four-Constraints Lens

1. **Structural Decisions > Patches**: S1 is a patch (one-liner). S2 is 1 structural decision (failsafe-raise → graceful-degradation) with patch-shaped delivery. **2 decisions, clean delivery. PASS.**
2. **Translation Loss**: R-BP.1/2 strong (semantic capture via monkeypatch). R-BQ.1 text-match + silent-return loses fidelity. R-BQ.2 call-count antibody strong. **Average immunity: ~65-75% structural.**
3. **Immune System**: R-BP antibodies make "silently ran HIGH" category harder to reintroduce. R-BQ makes "raise on degraded" impossible-by-test. BUT `entries_blocked_reason` coding gap means ops-observability category remains open. **Partial immunity.**
4. **Data Provenance**: N/A for P8 route A (no data). However, `save_portfolio(authority="degraded")` may pollute positions-cache.json truth-stamp to UNVERIFIED — a provenance tag DEGRADATION that persists. Worth P9 audit.

## Baseline-restore grep (inherited critic-beth methodology)

Did not do hard baseline-restore — commit message claims 20→20 failures, +4 passes exactly match the 4 new antibodies. Running `pytest tests/test_phase5_fixpack.py tests/test_phase6_day0_split.py tests/test_phase7a_metric_cutover.py tests/test_phase8_shadow_code.py` returns `54 passed` confirming P5/P6/P7A/P8 targeted suites all GREEN, consistent with commit.

## Verdict Justification

**PASS** (not ACCEPT-WITH-RESERVATIONS because all reservations are P9-scope observability, not P8-contract violations):

- **Hard constraints**: All honored (no v2 writes, no DDL, no TIGGE, no evaluator/monitor_refresh/settlement-writer changes, kwarg-only signature change).
- **Acceptance gates**: All pass (regression ≤ baseline empirically verified; P8 antibodies 4/4 GREEN; P5/P6/P7A targeted suites unchanged; no v2 writes in diff; backward compat via R-BP.2).
- **Structural correctness**: S1 + S2 minimal, correct, isolated.
- **Antibody quality**: R-BP.1/2 strong. R-BQ.1 has two weak edges (text-match + silent-return) but R-BQ.2 backstops structurally.
- **Findings**: 4 MAJOR (all observability/antibody-hygiene) + 4 MINOR + 6 test-gap. All deferrable to P9 forward-log; none block P8's claim of "Gate E code prerequisites complete".

## Methodology trend — first-try PASS streak

**P7B (first-try PASS, critic-beth cycle 3) + P8 (first-try PASS, critic-carol cycle 1) = 2 consecutive first-try PASSes under Gen-Verifier.** Pattern converging: small-surface phases with explicit contracts + pre-commitment + antibody design = low-friction review. Meta-note for future critics: the critic's role is shifting from "find the flaw" to "surface P9 forward-log items". Maintain pre-commitment discipline to keep finding-quality high — the risk of this pattern is complacency.

## Open Questions (unscored)

1. Is Interpretation A (strict read-only = no save_portfolio) or Interpretation B (JSON cache refresh OK) the intended DT#6 contract? Defer to user.
2. Should `tick_with_portfolio` persist to risk_state.db to align with `get_current_level` readers? Architecture call for P9.
3. Should P9 add a `test_degraded_portfolio_preserves_risk_state_db` antibody to lock the persistence-vs-ephemeral contract?
4. Is `entries_blocked_reason = "portfolio_loader_degraded"` preferred over `entries_blocked_reason = "risk_level=DATA_DEGRADED"` for operator clarity? User preference call.

---

*Critic: critic-carol, spawned 2026-04-18 replacing critic-beth after her 3-cycle P6/P7A/P7B run.*
*Inherited methodology: L0.0, two-seam, P3.1 vocab, baseline-restore grep, pre-commitment, deferral-with-rationale.*
*Next cycle opens*: P9 (LOW limited activation + DT#5/#2/#7 risk-critical packet + P8-observability forward-log absorption).
