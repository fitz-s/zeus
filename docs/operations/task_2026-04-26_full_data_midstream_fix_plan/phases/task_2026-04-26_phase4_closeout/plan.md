# Zeus Phase 4 — Closeout (Deferred Items)

Created: 2026-04-26
Authority basis: parent packet plan §11 + phase 1/2/3 plan addenda; PR #19 workbook closeout
Status: closeout phase. No new investigation; tackle 4 items previously deferred from earlier phases.
Branch: `claude/zeus-full-data-midstream-fix-plan-2026-04-26` (continuing from `6e3d9bf`)
Phase context: phases 1+2+3 + their review-fix cycles complete (33 commits, 84 antibody tests). Phase 4 is the closeout that finalizes the parent packet by addressing the explicitly-deferred items.

---

## 0. Scope statement

4 items deferred across phases 1-3 that are tractable without operator input:

1. **A1b** — extend `scripts/semantic_linter.py` with `_has_calibration_pairs_metric_predicate` (parent §11 deferred from Phase 1).
2. **P3.3b** — wrap raw slippage computation at `executor.py:242` in SlippageBps (P3.3 commit promised, partial-deliver). RealizedFill at fill-receipt remains DEFERRED to a separate packet (it's a different code path requiring its own audit).
3. **P3.1a** — delete dead-code `obs_dominates()` + `day0_obs_dominates_threshold()` (Phase 3 §3.A deferred — all callers verified migrated to `observation_weight()` 2026-03-31).
4. **Mesh-maintenance** — register 10 new test files + 4 new public symbols in `architecture/test_topology.yaml` + `architecture/source_rationale.yaml` (deferred across phases 1+2+3 §11).

**Explicitly NOT in scope** (require operator/data work):
- B1 ops-A/B test for smoke-test cap (operator decision)
- Operator-driven dashboards / alerting infrastructure
- C-track operator/data trust gates
- A3 integration test for malformed-metric candidate (heavy fixture)
- P3.2 true integration test (heavy fixture)
- Push to remote (per "先不合并")

---

## 1. Slice ordering

```
P4-1 P3.1a cleanup     — smallest, verified safe
P4-2 A1b linter        — high-value antibody extension
P4-3 P3.3b slippage    — SlippageBps wrap at executor.py:242 only
P4-4 mesh-maintenance  — register files + symbols (largest, mostly mechanical)
```

---

## 2. Per-slice detail

### P4-1 P3.1a — Remove obs_dominates() + day0_obs_dominates_threshold()

Pre-removal verification (already done in Phase 3 audit):
- ZERO callers of `obs_dominates` outside `day0_signal.py` itself.
- ZERO callers of `day0_obs_dominates_threshold` outside the legacy method.
- `observation_weight()` (continuous replacement) is the canonical interface, used by Day0Signal at L166/189/258.

Change:
- `src/signal/day0_signal.py`: remove `obs_dominates()` method + the import of `day0_obs_dominates_threshold` if unused after.
- `src/config.py`: remove `day0_obs_dominates_threshold` function.
- `settings.json`: remove `day0.obs_dominates_threshold` if present.

Risk: external scripts/notebooks could conceivably import these. Mitigation: grep is comprehensive; impact bounded by repo + workspace-* docs.

### P4-2 A1b — semantic_linter calibration_pairs metric predicate

Extend the existing `_has_settlements_metric_predicate` antibody pattern (P3 4.5.A precedent landed pre-PR #19) to `calibration_pairs` table reads. Per Phase 1 §11 + parent post-review addendum, this is the natural extension of slice A1's store-side enforcement.

Change:
- `scripts/semantic_linter.py`: add `_calibration_pairs_table_aliases` + `_has_calibration_pairs_metric_predicate` mirroring the settlements pattern.
- Wire the new check into the same loop that scans SQL literals.
- Tests: extend `tests/test_authority_strict_learning.py` source-scanner OR add a new `tests/test_calibration_pairs_metric_linter.py`.

NOTE: legacy `calibration_pairs` schema lacks `temperature_metric` column (per Phase 1 grep_gate_log). The linter must accept legacy reads (HIGH-only by convention) AND require metric predicate for `calibration_pairs_v2` reads. Two-tier check.

### P4-3 P3.3b — SlippageBps wrap at executor.py:242

Change at `src/execution/executor.py:242-247`:
```python
# Pre-fix:
slippage = current_price - best_bid
if current_price > 0 and slippage / current_price <= 0.03:
    limit_price = best_bid

# Post-fix:
slippage_obj = SlippageBps.from_prices(...)  # or equivalent constructor
if current_price > 0 and slippage_obj.fraction <= 0.03:
    limit_price = best_bid
```

Verify SlippageBps has a from-prices constructor. Behavior preserved; types tightened.

DEFERRED out of P3.3b: RealizedFill construction at fill-receipt (different code path; needs separate audit).

### P4-4 Mesh-maintenance — register new artifacts

10 new test files to register in `architecture/test_topology.yaml`:
1. `tests/test_calibration_store_metric_required.py` (Phase 1)
2. `tests/test_calibration_manager_low_fallback_regression.py` (Phase 1 fix1)
3. `tests/test_lifecycle_terminal_predicate.py` (Phase 1)
4. `tests/test_evaluator_metric_normalizer_failclosed.py` (Phase 1)
5. `tests/test_authority_strict_learning.py` (Phase 1)
6. `tests/test_ensemble_snapshots_bias_corrected_schema.py` (Phase 2)
7. `tests/test_position_metric_resolver.py` (Phase 2)
8. `tests/test_calibration_v2_fallback_alerting.py` (Phase 3)
9. `tests/test_monitor_refresh_ci_fallback.py` (Phase 3)
10. `tests/test_execution_intent_typed_slippage.py` (Phase 3)

All carry the standard `# Created:` + `# Last reused/audited:` + `# Authority basis:` headers per project rule, so should classify as `trusted`.

4 new public symbols to consider for `architecture/source_rationale.yaml`:
- `TERMINAL_STATES` + `is_terminal_state` in `src/state/lifecycle_manager.py`
- `LEARNING_AUTHORITY_REQUIRED` + `resolve_position_metric` in `src/state/chain_reconciliation.py`

Plus 1 schema addition in source_rationale-tracked file:
- `bias_corrected` column in `ensemble_snapshots` (DDL change in `src/state/db.py`)

---

## 3. Test topology

Each slice runs against the existing antibody test set + adds focused regression. Phase 4 is mostly cleanup — no new structural decisions, just executing what's been planned.

---

## 4. Acceptance gates

- All 84 phase 1-3 antibody tests still pass.
- `topology_doctor --map-maintenance` would now see registered files (operator can re-run after merge).
- `obs_dominates` removal doesn't break `test_day0_observation_weight_increases_monotonically` or any signal/test path.
- A1b linter doesn't false-positive on legacy `calibration_pairs` reads (HIGH-only by convention).

End of phase 4 plan.
