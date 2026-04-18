# critic-beth — Phase 6 Wide Review (extra-strict, post-gate-bypass)

**Date**: 2026-04-18
**Subject**: Phase 6 commit `e3a4700` — Day0 split + DT#6 + B055 (landed via team-lead coordination error, bypassing normal pre-commit gate)
**Pytest**: 19/19 R-BA..R-BG GREEN; full suite 138 failed / 1801 passed (my env) vs team-lead's 115/1768 (env delta noted)
**Posture**: L0.0 peer-not-suspect, extra-strict per team-lead post-bypass protocol, fresh bash greps on every cited claim
**Supersedes**: my prior `phase6_evidence/critic_beth_phase6_wide_review.md` PASS verdict (stale — missed MAJOR-1)

## VERDICT: **ITERATE** — 1 MAJOR + 1 MINOR + 1 forward-log (carryover)

Prior PASS was incorrect. Re-review against the hunt list found one stale antibody that Phase 6's guard-removal invalidated — same class as my fix-pack `test_read_mode_truth_json_none_mode_does_not_raise` miss (flagged in my own P5→P6 learnings doc §5 as "PASS-verdict blind spot; post-commit full-suite diff is non-negotiable"). Structural deliverables otherwise hold.

---

## Hunt-list verdicts

### Hunt #1 — Silent corruption category (MAX→MIN alias): **PASS**

[DISK-VERIFIED: `git show e3a4700 -- src/engine/evaluator.py | grep -A 2 extrema\\.`:
```
-            member_mins_remaining=remaining_member_extrema,
+            member_maxes_remaining=extrema.maxes,
+            member_mins_remaining=extrema.mins,
```
`git show e3a4700 -- src/engine/monitor_refresh.py`:
```
-        member_mins_remaining=remaining_member_maxes,
+        member_maxes_remaining=extrema.maxes,
+        member_mins_remaining=extrema.mins,
```]

Both callsites ATOMICALLY migrated from the one-array-aliased-as-both-slots pattern to typed dataclass field access. Under HIGH dispatch, `RemainingMemberExtrema.for_metric` sets `maxes=arr, mins=None` (extrema.py:32-34). Under LOW dispatch, `maxes=None, mins=arr`. Router at day0_router.py:54 checks `is_low()` and constructs Day0LowNowcastSignal with `member_mins_remaining=inputs.member_mins_remaining`, or Day0HighSignal with `member_maxes_remaining=inputs.member_maxes_remaining`. The None-slot is never read on the matching branch. Silent-corruption category structurally unwritable per Fitz P4. ✓

### Hunt #2 — LOW nowcast mathematical correctness: **MINOR mathematical concern (Phase 9 gate)**

[DISK-VERIFIED: Read `src/signal/day0_low_nowcast_signal.py:41-45`:
```python
def settlement_samples(self) -> np.ndarray:
    anchored = np.minimum(self.ens_remaining, self.current_temp)
    w = self._remaining_weight()
    blended = w * anchored + (1.0 - w) * self.current_temp
    return np.minimum(self.obs_ceiling, blended)
```]

Traced a cold-snap scenario: forecast `ens_remaining=[20, 22, 25]°F` (cold night coming), `current_temp=40°F` (warm now, pre-front), `obs_low_so_far=35°F`, `hours_remaining=8` (w=0.333):

- `anchored = min([20,22,25], 40) = [20, 22, 25]` ✓
- `blended = 0.333*[20,22,25] + 0.667*40 = [33.33, 34.00, 34.67]` — **forecast is BLENDED UP toward current_temp; predicted minimums warmer than actual expected ~20°F**
- `return min(35, [33.33, 34.00, 34.67]) = [33.33, 34.00, 34.67]` — ceiling does not bind

**Result**: systematic warm-bias of ~14°F in cold-snap scenarios. R-BB asserts only the CEILING (`samples <= obs_low_so_far`), not the lower bound — test passes despite the bias.

Interpretation of `_remaining_weight`: `w = hours_remaining/24.0` capped [0.10, 0.95]. "Many hours remaining → trust forecast" is sensible as a nowcast-confidence weighting when `current_temp` genuinely reflects the day's minimum trajectory, but when `current_temp` is BEFORE a forecasted cold front (pre-front warm day), the blend systematically underweights the incoming cold. The anchor step `min(ens_remaining, current_temp)` already clips forecasted cold below current; then blending back toward current raises the prediction again.

**Severity**: MINOR for Phase 6 because (a) R-BB only checks ceiling which is correct, (b) `Day0LowNowcastSignal.p_vector` is NOT IMPLEMENTED on disk (Day0HighSignal has it, Day0LowNowcastSignal does not) — so LOW track cannot produce a probability vector in production today, (c) LOW live trading is Phase 9 Gate F. Blocker for Phase 9, not Phase 6.

**Forward-log for Phase 9**: validate the `_remaining_weight` formula against real cold-snap data. Consider `w = (24 - hours_remaining)/24` inversion (i.e. confidence in current_temp grows as hours_remaining shrinks, inversely) OR a saturation model where `current_temp` only binds as floor when hours_remaining < 2-3. Also implement `p_vector()` before Phase 9 Gate F.

### Hunt #3 — AST static check (R-BE): **PASS**

[DISK-VERIFIED: Read `tests/test_phase6_day0_split.py:249-265`:
```python
tree = ast.parse(src_file.read_text())
forbidden = "day0_high_signal"
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            assert forbidden not in alias.name, ...
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        assert forbidden not in module, ...
```
`python -c "import ast; tree = ast.parse(open('src/signal/day0_low_nowcast_signal.py').read()); ..."` → only `__future__.annotations` + `numpy` imports.]

R-BE is STATIC AST walk via `ast.parse` + `ast.walk`, not runtime trace. Catches both `from ... import ...` (ImportFrom.module check) and `import ... as ...` (Import.names[].name check) with substring match on "day0_high_signal". Survives aliased imports and transitive-import refactors at the first-level direct-import boundary.

**Limitation**: does not catch dynamic imports via `importlib.import_module("src.signal.day0_high_signal")` or string-based attribute access. That's a separate forbidden-move class and low probability in this codebase. Acceptable coverage for Phase 6.

### Hunt #4 — DT#6 graceful-degradation read-only + B055 merged: **PASS**

[DISK-VERIFIED: `git show e3a4700 -- src/riskguard/riskguard.py` — `tick_with_portfolio` body (L983-1040).]

- **No data writes**: function calls `init_risk_db` (idempotent CREATE TABLE IF NOT EXISTS + ALTER with exception-swallow — schema-only, no data writes), `query_authoritative_settlement_rows` (SELECT), `_trailing_loss_snapshot` ×2 (SELECT from risk_state rows), `overall_level` (pure compute). Both connections closed at end. No INSERT/UPDATE/DELETE on data rows.
- **B055 + DT#6 merged into ONE degraded transition**: `overall_level(RiskLevel.DATA_DEGRADED if portfolio.portfolio_loader_degraded else RiskLevel.GREEN, ..., daily_loss_level, weekly_loss_level)` at L1030-1036. portfolio-authority degradation AND trailing-loss staleness (via `daily_loss_level` which comes from `_trailing_loss_snapshot` returning DATA_DEGRADED on stale ref) feed a SINGLE `overall_level()` call. Single degraded-state classification, not two competing states. ✓

### Hunt #5 — Regression delta sanity: **MAJOR-1 FOUND**

[DISK-VERIFIED: `pytest tests/test_metric_identity_spine.py --tb=no -q` → `1 failed, 11 passed`; specifically `TestDay0SignalLowMetricRefuses::test_day0signal_low_metric_refuses_until_phase6` FAILED.]

Test at `tests/test_metric_identity_spine.py:154-174`:
```python
def test_day0signal_low_metric_refuses_until_phase6(self):
    ...
    with pytest.raises(NotImplementedError):
        Day0Signal(
            ...,
            temperature_metric=LOW_LOCALDAY_MIN,
        )
```

This test asserts the R2 Phase 1 antibody: `Day0Signal.__init__` MUST raise NotImplementedError when constructed with LOW. Phase 6 removed the guard at `day0_signal.py:85-92` without updating/replacing/deleting this test. Post-Phase 6, `Day0Signal(..., LOW_LOCALDAY_MIN)` constructs normally → test fails.

**Structural implication beyond the test**: the class itself is now willing to accept LOW and would produce HIGH-semantics output. Production code doesn't exercise this (only `Day0HighSignal._day0_signal()` at day0_high_signal.py:68 constructs `Day0Signal` directly, with `temperature_metric=HIGH_LOCALDAY_MAX` hardcoded), but the CATEGORY of "wrong code constructable" — Fitz P4 violation — is now open. Any future caller that instantiates `Day0Signal(LOW, ...)` directly bypasses the Router and gets HIGH-math labeled LOW.

This is the EXACT class of finding I flagged in my own P5→P6 learnings doc §5: "Fix-pack PASS-verdict-blind-spot: cross-check obsolete tests in OTHER files when a fix-pack commits a contract inversion. Post-commit full-suite diff is non-negotiable, not 'regressions handwave.'" My prior P6 PASS verdict missed this the same way I missed `test_read_mode_truth_json_none_mode_does_not_raise` in fix-pack. Self-correction confirmed — L0.0 peer-not-suspect applies to self too.

**Severity: MAJOR**. The test name literally says "refuses_until_phase6" — Phase 6 shipped; the test is now obsolete contract language. Two fixes are needed: test-level (the test must be replaced) AND code-level (day0_signal.py should re-assert the type-check at a fresh boundary to keep Fitz P4 category-impossibility for direct constructions).

---

## L0-L5 (condensed; hunt list above covers detail)

- **L0/L0.0**: Authority re-loaded post-subagent-start. Peer-not-suspect applied to self — prior PASS wrong, re-verified. Zero discipline findings on teammates. Team-lead "115 failed" vs my "138 failed" = tier 2 env delta (same category as test_automation_analysis.py WU_API_KEY gating etc.). Not escalating.
- **L1** INV/FM: RemainingMemberExtrema dataclass invariants sharp. Triad preserved through Day0SignalInputs typed object.
- **L2** Forbidden Moves: paper-mode not reintroduced. No `--track` flags. No Phase 7/8 scope bundled.
- **L3** Silent fallbacks: `remaining_member_maxes_for_day0` backward-compat alias at day0_window.py:76-90 — vestigial-defense pattern, comment says "Remove after all callsites confirmed updated" (4 test callers still reference it). Not a runtime hazard; forward-log.
- **L4** Source authority: Router-at-construction enforces HIGH/LOW dispatch. Day0LowNowcastSignal AST-clean.
- **L5** Phase boundary: no 5A/5B/5C contracts regressed except the Phase 1 R2 antibody (MAJOR-1 above).

## WIDE — off-checklist findings

### MAJOR-1 (hunt #5): obsolete R2 antibody test FAILING — already flagged above.

### MINOR (carryover from my prior review): `cycle_runner.py:181` still raises RuntimeError on degraded portfolio. DT#6 law violation in letter, but contract scope didn't list cycle_runner rewiring. tick_with_portfolio mechanism exists but unwired. Forward-log for Phase 8. (Unchanged from prior review.)

### MINOR: `Day0LowNowcastSignal.p_vector` not implemented; `monitor_refresh.py:316-318` calls `day0.p_vector(bins)` — would AttributeError if LOW position ever flowed through. Safe today (no LOW positions exist; LOW live activation is Phase 9), but a tripwire. Forward-log for Phase 9.

### MINOR: commit message `e3a4700 docs(phase6): microplan` misrepresents 844 LOC of implementation. Team-lead dispatch acknowledges this was a coordination-error commit; no amend expected per team-lead direction.

### MINOR: `Day0HighSignal.__day0_signal` name-mangled attribute readability nit. Optional Phase 7 style pass.

## Recommendation

**ITERATE — one surgical cycle to exec-kai**:

1. **Fix MAJOR-1 (both test + code)**:
   - **Test**: rewrite `tests/test_metric_identity_spine.py::TestDay0SignalLowMetricRefuses::test_day0signal_low_metric_refuses_until_phase6`. Either delete (if Day0Signal is now metric-agnostic by design) or rename + repurpose to assert the NEW Fitz P4 guard — e.g. "Day0Router.route with LOW returns Day0LowNowcastSignal, never Day0Signal".
   - **Code** (strongly recommended): keep the Day0Signal LOW type-check but re-scope. Add at day0_signal.py around L80-L92: `if temperature_metric.is_low(): raise TypeError("Day0Signal is HIGH-only; use Day0Router.route() or Day0LowNowcastSignal for LOW")`. Changes the error from NotImplementedError (Phase 1 semantic: "not yet built") to TypeError (Phase 6+ semantic: "wrong type; LOW has its own class"). Preserves category-impossibility for future direct-construction attempts. ~3 LOC addition.

Expected post-fix: `pytest tests/test_metric_identity_spine.py` → 12/12 GREEN. Fix-pack + 5A + 5B + 5C + Phase 6 cumulative full-suite should drop 1 failure (~137 failed / 1802 passed in my env).

2. **No other ITERATE items needed**. Hunt #1-4 PASS. Hunt #2 is MINOR forward-log for Phase 9 gate (not Phase 6 blocker).

3. **Forward-log (unchanged from prior review)**:
   - Phase 8: wire `cycle_runner:180-181` RuntimeError → `tick_with_portfolio` for full DT#6 runtime compliance.
   - Phase 9: implement `Day0LowNowcastSignal.p_vector` + validate `_remaining_weight` blend formula against cold-snap data before Gate F.
   - Phase 7 naming-pass: remove `remaining_member_maxes_for_day0` backward-compat alias after test callers migrate.

## Dispatch plan

A2A to exec-kai with the MAJOR-1 fix shape. One cycle, ~5 LOC delta (test rewrite + optional code re-guard). Re-verify at 14+12+19=45 tests GREEN across metric_identity_spine + phase6_day0_split + delta files. Then PASS.

**Note on my prior PASS miss**: team-lead's dispatch was right to request extra-strict posture. The gate-bypass commit path skipped the natural catch point where an intermediate RED state would have surfaced the obsolete R2 test. My fix-pack miss is now a two-incident pattern — actionable update to methodology: "critic MUST run `pytest tests/ --tb=no -q` filtered by diff-impact zone AND cross-check contract-inverting changes against test-naming vocabulary (`_refuses_`, `_does_not_`, `_until_`) before PASS."

---

*Authored*: critic-beth (opus, persistent, sniper-mode, extra-strict)
*Disk-verified*: 2026-04-18, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, fresh `git show e3a4700`, fresh pytest 19/19 on phase6 file, fresh pytest MAJOR-1 reproduction (`test_day0signal_low_metric_refuses_until_phase6` FAILED in test_metric_identity_spine.py), fresh AST walk on R-BE, fresh `tick_with_portfolio` trace for read-only verification.
*Supersedes*: prior PASS verdict at `phase6_evidence/critic_beth_phase6_wide_review.md`.
