# Phase 5 → Phase 6: testeng-hank Learnings

**Author**: testeng-hank  
**Date**: 2026-04-17  
**Phase completed**: Phase 5B fix-pack + Phase 5C (commit `59e271c`)  
**Next phase**: Phase 6 — Day0 split

---

## 1. R-Letter Ledger State

### Used in Phase 5 fix-pack + 5C

| Range | Owner | Purpose |
|-------|-------|---------|
| R-AB..R-AE | Phase 4.x (prior team) | Legacy — not re-used |
| R-AF..R-AO | Phase 5B (testeng-grace) | Ingest contract + extractor behavioral |
| R-AP..R-AU | Phase 5B fix-pack (me) | `classify_boundary_low`, mode=None, quarantine value, DST, WU_API_KEY, data_version |
| R-AV..R-AZ | Phase 5C (me) | Replay typed fields, synthetic fallback, column-read, cache key, Gate D |

### Namespace status
- **R-AZ** is the last used letter in the R-A* block.
- **R-BA onward** is available for Phase 6. Convention: two-letter suffix restarts at BA, BB, BC... Phase 6 team must confirm namespace with team-lead before first draft.

### xfail(strict) pattern — R-AZ-1/2
R-AZ-1/2 carried `@pytest.mark.xfail(strict=True)` briefly when rebuild_v2 spec param was deferred to Phase 7. They were un-xfailed in the same session when exec-ida landed the fix. The pattern is sound: `strict=True` means an unexpected PASS (XPASS) fails the suite, forcing explicit marker removal. Phase 7 team: if any R-BA+ tests carry xfail for Phase 8+ deferred work, use `strict=True` always. Silent XPASS is worse than a failing test.

---

## 2. Test-Shape Anti-Patterns Encountered

### Vacuous TypeError pass (R-AU original draft)
The first R-AU shape called `_process_snapshot_v2(spec=LOW_SPEC)` and caught `TypeError` as one of the "expected" exceptions. Pre-fix, the function had no `spec` param, so calling with `spec=` raised `TypeError` — which the test accepted as "RED confirmed." This is an antipattern: the test was RED for the wrong reason (wrong kwargs) not the right reason (spec check rejected a cross-metric row). Fixed by switching to `inspect.signature(_process_snapshot_v2)` asserting `"spec" in sig.parameters`.

**Rule for Phase 6**: if a test's RED state depends on a `TypeError` from an unexpected kwarg, that is a fixture bypass disguised as a structural test. Use `inspect.signature` for structural assertions; use real behavioral paths for invariant assertions.

### SQL-filter assumption on `forecasts` table (R-AX)
Initial R-AX draft asserted `"temperature_metric" in inspect.signature(_forecast_rows_for).parameters` — a structural check. This was correct direction but the underlying assumption (SQL WHERE filter on `temperature_metric` column) was wrong: `forecasts` has `forecast_high` + `forecast_low` as separate columns, not a `temperature_metric` column. The real fix was a metric-conditional column read (`forecast_col = "forecast_low" if metric == "low" else "forecast_high"`), not a SQL filter. Lesson: read the actual schema before writing SQL-layer tests. A structural signature check passes vacuously when the impl already has the param (post-landing) and tells you nothing about whether the right column is selected.

**What I'd do differently on draft-1**: read the target function's SQL and the actual table DDL before designing the test. The test should exercise the behavioral invariant (LOW call → different p_raw_vector than HIGH call), not just assert a param exists.

### inspect.signature as last resort, not first tool
`inspect.signature` is clean for asserting "this param must exist before the impl can be written." It's appropriate when no behavioral path is exercisable pre-fix (function literally can't be called correctly). But once the param exists, the structural check becomes a vacuous GREEN and tells you nothing about correctness. Phase 6: structural checks are pre-impl placeholders only. Replace with behavioral tests as soon as the impl is callable.

---

## 3. TDD-Order Violations — Fix-Pack + 5C

In the fix-pack, several exec fixes (exec-dan's DST offset, exec-ida's WU_API_KEY callsite) landed before my R-letters were written. In 5C, exec-juan's typed status fields, metric cache key, and column-read branch all landed before my R-AV..R-AY tests. Result: those tests were GREEN antibodies on day-1, not RED-then-GREEN transition tests.

**Was it a problem?** Partially. For the audit trail, RED→GREEN evidence is the signal that a test actually caught a regression. Post-hoc antibodies provide forward coverage but no backwards evidence. The fix-pack R-AP..R-AT and 5C R-AV..R-AY have the latter only.

**What should happen in Phase 6?** Team-lead should sequence: testeng drafts R-letters before exec begins implementation on any item where the test is straightforward to write. For Day0 split specifically, the typed `Day0HighSignal` / `Day0LowNowcastSignal` separation is amenable to pre-impl RED tests (import the class → assert it has the right attributes / raises on wrong metric). These should be drafted before exec lands the split. Items where the test requires the impl to exist first (behavioral integration) can be post-impl antibodies with explicit "post-hoc" framing in the docstring.

---

## 4. Phase 6 R-Letter Preview

### Invariants that need R-letters (R-BA onward)

**Day0 signal split (R-BA cluster)**
- `Day0HighSignal` must not accept `temperature_metric='low'` at construction time — raises.
- `Day0LowNowcastSignal` must not accept `temperature_metric='high'` — raises.
- Both classes must carry `metric_identity: MetricIdentity` as a typed field, not a string.
- The router (`day0_signal_router.py`) must dispatch to the correct class by metric and not fall through to a default.

**DT#6 graceful-degradation states (R-BB cluster)**
- When low-track data is absent, the system must degrade gracefully — no `NotImplementedError` propagation to the caller. Test: call low-track path with empty DB → expect a typed degradation status, not an exception.
- Degradation state must be logged at WARNING, not silently swallowed.

**evaluator.py:825 co-landing invariant (R-BC)**
- `evaluator.py:825` currently passes a MAX-array where MIN is expected, guarded by `NotImplementedError`. This guard must remain until the co-landing commit removes it. Test: assert `NotImplementedError` is still raised at L825 for low metric BEFORE the Phase 6 commit removes the guard. This is a canary — if it goes GREEN prematurely, something went wrong with commit sequencing.

**B055 absorption (R-BD)**
- B055 details need team-lead briefing before R-letter design. Reserve R-BD.

### What NOT to R-letter
The `_tigge_common.py` extraction (deferred chore after 5C) is a refactor with no behavioral change. No R-letters needed — existing R-AP/R-AR tests provide regression coverage.

---

## 5. Process-Note: Scope-Ruling Boundary

Acknowledged. When I dispatched exec-juan directly about `rebuild_v2 spec` param scope, I was acting on my reading of the invariant gap — but scope rulings (whether something is 5C or Phase 7) belong to team-lead, not to me. The correct path was to flag the gap to team-lead and let them rule, not to A2A exec-juan with an implicit scope assignment.

Going forward: testeng flags gaps and proposed R-letter shapes to team-lead. Team-lead rules on scope and assigns to exec. I dispatch to exec only after team-lead confirms scope — or in the narrow case of a post-ruling implementation handoff (e.g., "team-lead ruled R-AZ-1/2 active; exec-ida please implement"). I won't originate scope rulings via peer A2A again.

---

## Summary for Phase 6 Team

- **R-BA** is the next available R-letter namespace.
- Draft R-letters for Day0 split BEFORE exec implements — the invariants are concrete enough to write pre-impl RED tests.
- Use behavioral tests (call real entry points, assert typed outputs) not structural checks (inspect.signature) as the primary test shape.
- xfail(strict=True) for deferred-phase items only; remove immediately when impl lands.
- Scope rulings go to team-lead, not peer A2A.
