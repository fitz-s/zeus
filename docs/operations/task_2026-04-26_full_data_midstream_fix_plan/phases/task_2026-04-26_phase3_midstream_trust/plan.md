# Zeus Phase 3 — Midstream Trust Improvements (PR #19 P3 Workbook Items)

Created: 2026-04-26
Last audited: 2026-04-26
Authority basis: parent packet `docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md`; PR #19 workbook P3 section; `docs/operations/known_gaps.md`; Zeus AGENTS.md.
Status: planning evidence; not authority. Code-level mid-stream improvements after data trust gates close (per workbook P3 preamble).
Branch: `claude/zeus-full-data-midstream-fix-plan-2026-04-26` (continuing from `5f6e502`, worktree `/Users/leofitz/.openclaw/workspace-venus/zeus-fix-plan-20260426`)
Phase context: parent phase 1 + phase 2 + post-review fixes closed (17 commits, 77 antibody tests). Phase 3 addresses the 4 P3 workbook items in the same parent packet's scope.
Operator note: multiple parallel worktrees active; phase 3 lands locally per "先不合并" directive.

---

## 0. Scope statement

Phase 3 addresses 4 midstream trust improvements from PR #19 workbook P3:
1. Replace Day0 binary observation dominance with continuous weighting design.
2. Close entry/exit epistemic symmetry (production entry and exit consume same evidence burden).
3. Extend typed execution-price/tick-size/slippage contracts through CLOB-send and realized-fill boundary.
4. Add operator-visible alerting when calibration falls back from v2 to legacy models.

**Phase 3 does NOT:**
- Re-touch phase 1 / phase 2 slices.
- Address operator/data trust gates (parent §3.C).
- Push to remote per directive.
- Make schema changes (P3.4 alerting is in-process logging, not DB).

---

## 1. Audit results (2026-04-26)

| Workbook P3 item | Current state | Phase 3 disposition |
|---|---|---|
| P3.1 Day0 binary→continuous | **OBSOLETE — already fixed.** known_gaps.md records "Day0 连续衰减函数 — observation_weight() implemented (2026-03-31, Sonnet)" with `test_day0_observation_weight_increases_monotonically` passing. `obs_dominates()` is marked "Legacy boolean interface. Prefer observation_weight() for continuous blending." Repo-wide grep finds ZERO callers of `obs_dominates()` or `day0_obs_dominates_threshold` outside `day0_signal.py` itself. | **No code change needed.** Optional dead-code removal slice P3.1a (defer). |
| P3.2 Entry/exit epistemic symmetry | Partially fixed: known_gaps records "Exit uses CI-aware conservative edge (2026-03-31)" + "MC count: monitor=1000 → 5000". BUT `monitor_refresh.py:721-722` still has `ci_lower = current_forward_edge; ci_upper = current_forward_edge` as the FALLBACK path when `_bootstrap_context` is absent on the position. Degenerate ci=point estimate breaks `conservative_forward_edge` (returns raw point when ci_width=0). `pos.entry_ci_width` IS used at L759/764 for EdgeContext construction, but the L721-722 internal degeneracy may leak through. | **P3.2 slice**: hoist `pos.entry_ci_width` as the L721-722 fallback (instead of `current_forward_edge` 0-width point) so conservative_forward_edge has real width even on the no-bootstrap-context path. |
| P3.3 Typed execution contracts boundary | Typed atoms already exist: `src/contracts/realized_fill.py` (RealizedFill dataclass with SlippageBps), `src/contracts/slippage_bps.py` (typed bps), `src/contracts/tick_size.py` (TickSize). `src/execution/executor.py:236-237` uses TickSize.for_market correctly. BUT L128 hardcodes `max_slippage=0.02` as raw float, and L242 computes `slippage = current_price - best_bid` as raw subtraction — never wrapped in SlippageBps at the boundary. | **P3.3 slice**: replace `max_slippage=0.02` float with `SlippageBps`-typed constant + thread RealizedFill construction at fill receipt. Antibody test that boundary types persist through executor flow. |
| P3.4 v2→legacy fallback alerting | `src/calibration/manager.py:172` (`get_calibrator` primary fallback to legacy) + `:232` (season-only fallback to legacy) — both load_platt_model calls execute SILENTLY when v2 misses. logger is already used in this file (warnings at L182, L235, L283, L303, L309, L314, L334) but the v2→legacy fallback path is not surfaced. | **P3.4 slice**: add WARNING-level log at L172 + L232 each time v2 misses and legacy is read. Plus a counter / metric in the EdgeContext or summary dict so ops dashboards can track fallback rate over time. |

Premise rot: 0% — all citations grep-verified 2026-04-26 within 10-minute window.

Detailed evidence: `evidence/phase3_audit_log.md`.

---

## 2. Structural decomposition (Fitz Constraint #1)

The 4 P3 items are NOT a single decision family — each addresses a different mid-stream trust dimension:

| P3 item | Decision | Type | Code slices |
|---|---|---|---|
| P3.1 Day0 weighting | **Already complete** — interface migration done | Code (cleanup only) | P3.1a (optional dead-code removal; **DEFER**) |
| P3.2 Entry/exit symmetry | Use entry's CI width as the monitor-refresh fallback so conservative-edge math has real dispersion | Math (signal completeness) | P3.2 |
| P3.3 Execution typing | Promote raw-float execution-boundary values to existing typed atoms | Architecture (type discipline) | P3.3 |
| P3.4 Calibration alerting | Operator visibility for the v2→legacy degradation event | Observability | P3.4 |

3 active code slices + 1 deferred cleanup. No structural collapse — each addresses an orthogonal concern.

---

### 3.A P3.1a — Day0 obs_dominates legacy cleanup (DEFERRED)

Current state: `obs_dominates()` and `day0_obs_dominates_threshold()` have no callers outside `day0_signal.py`. The "Legacy boolean interface" was correctly migrated to `observation_weight()` in 2026-03-31's Sonnet pass. No bug.

Optional dead-code removal:
- Delete `obs_dominates()` method on Day0Signal.
- Delete `day0_obs_dominates_threshold()` from `src/config.py`.
- Remove `day0.obs_dominates_threshold` from `settings.json` if present.
- Risk: backward-compat for any external script/notebook that imports the function. Without a usage census this is non-zero risk.

**Disposition**: deferred to a future cleanup packet. Phase 3 declares P3.1 complete via the workbook's own intent (continuous weighting EXISTS and is in use).

---

### 3.B P3.2 — Entry-CI fallback in monitor refresh

**Failure pattern.** `src/engine/monitor_refresh.py:719-735`:

```python
current_forward_edge = current_p_posterior - current_p_market
ci_lower = current_forward_edge   # degenerate point fallback
ci_upper = current_forward_edge   # degenerate point fallback
bootstrap_ctx = getattr(pos, "_bootstrap_context", None)
if bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
    # ... compute fresh bootstrap CI ...
```

When a position lacks cached `_bootstrap_context` (e.g., re-loaded from JSON fallback after process restart, or constructed in a test fixture), `ci_lower == ci_upper == point estimate`. Downstream `conservative_forward_edge(forward_edge, ci_width=0)` returns `forward_edge` unchanged — i.e., **exit decisions revert to point-estimate logic** without the CI-aware safety margin. This is the SAME failure mode known_gaps.md says was fixed for the entry path: monitor + exit must consume the same evidence burden.

**Structural fix.** Use `pos.entry_ci_width` as the fallback CI when `_bootstrap_context` is absent, mirroring the L759/764 EdgeContext construction logic that already uses entry_ci_width. The conservative-forward-edge math then has a real dispersion estimate even on the no-bootstrap path.

#### Slice P3.2 — Hoist entry_ci_width as ci_lower/upper fallback

Scope: `src/engine/monitor_refresh.py` only.

Change (single block at L719-735):
```python
current_forward_edge = current_p_posterior - current_p_market

# Slice P3.2 (PR #19 P3.2, 2026-04-26): when fresh bootstrap CI is
# unavailable (no cached _bootstrap_context, e.g. after JSON-fallback
# load), fall back to entry's CI width. Pre-fix used the degenerate
# `ci_lower = ci_upper = current_forward_edge` (zero width) which made
# `conservative_forward_edge(... ci_width=0)` collapse to point-estimate
# logic — symmetric-with-entry contract violated.
_entry_ci_half = max(0.0, getattr(pos, "entry_ci_width", 0.0)) / 2.0
ci_lower = current_forward_edge - _entry_ci_half
ci_upper = current_forward_edge + _entry_ci_half

bootstrap_ctx = getattr(pos, "_bootstrap_context", None)
if bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
    # ... existing fresh-CI computation overrides the fallback ...
```

Relationship test (NEW, before impl):
- "Position with no _bootstrap_context AND non-zero entry_ci_width: refresh_position must produce EdgeContext with ci_width > 0 (not degenerate)." Fixture-driven.
- "Position with no _bootstrap_context AND entry_ci_width=0: degenerate CI is preserved (no spurious widening)." Edge case.
- "When _bootstrap_context present, fresh CI overrides entry fallback (no regression)." Pinned via mock.

Function tests:
- `conservative_forward_edge(forward_edge, ci_width=entry_ci_width)` returns the lower-bound edge when ci_width > 0.
- Existing exit_triggers tests pass.

Acceptance:
- All 3 relationship tests pass.
- Existing monitor + exit triggers regression unchanged.
- No new failures attributable to this slice.

Blast radius: low. Single block edit; behavior change only for the no-bootstrap-context path which previously was always degenerate.

---

### 3.C P3.3 — Execution-boundary typed contracts

**Failure pattern.** Typed atoms exist (`SlippageBps`, `RealizedFill`, `TickSize`) but `src/execution/executor.py:128` declares `max_slippage=0.02` as raw float, and L242 computes `slippage = current_price - best_bid` as raw subtraction. The boundary between "intent" (typed) and "executor send" (untyped) drops type discipline at the most critical seam. Per Fitz Constraint #2, "encode insight as code, not docs": untyped boundary values are silent unit-confusion hazards (bps vs pct, signed vs unsigned).

**Structural fix.** Replace `max_slippage=0.02` (which is bps-or-pct ambiguous — comment doesn't specify) with `SlippageBps.from_pct(0.02)` (explicit bps). At fill receipt, construct `RealizedFill` from CLOB response so downstream Kelly attribution + slippage budgeting receive typed evidence.

#### Slice P3.3 — Replace executor max_slippage float + RealizedFill at receipt

Scope: `src/execution/executor.py` (replace 1 hardcoded constant + thread RealizedFill at fill receipt site).

Change shape:
1. L128: `max_slippage=0.02` → `max_slippage=SlippageBps.from_pct(0.02)` (or whichever constructor SlippageBps exposes — VERIFY via grep).
2. At fill receipt (where CLOB returns fill price + size): construct `RealizedFill.from_intent_vs_actual(intent, actual)` so downstream consumers get typed evidence.
3. Ensure existing call sites accept the typed object (may need conversion at consumer boundary if any consumer still expects raw float).

Relationship test (NEW):
- "max_slippage typed: cannot pass a raw float that would silently be interpreted as either bps or pct" — TypeError test.
- "RealizedFill at receipt carries (intent_price, actual_price, slippage as SlippageBps, side)" — shape pin.

Function tests:
- Existing executor flow tests pass.
- New tests for SlippageBps.from_pct + RealizedFill.from_intent_vs_actual.

Acceptance:
- All call sites updated.
- Repository-wide grep for `max_slippage=0.02` (raw float) returns 0 hits.
- Antibody test: passing raw 0.02 to executor entry should raise (after slice). If too disruptive, defer the type-strictness to a follow-up packet and just thread the typed value at the boundary first.

Blast radius: medium. Touches executor hot path. Run full execution + executor regression.

**Note**: this slice's scope depends heavily on what `SlippageBps` and `RealizedFill` constructors actually accept. Audit-stage 2 will refine the slice plan; if construction is awkward, splits to sub-slices P3.3a (replace constant) + P3.3b (RealizedFill threading).

---

### 3.D P3.4 — v2→legacy fallback alerting

**Failure pattern.** `src/calibration/manager.py:172` and `:232` invoke `load_platt_model(conn, bk)` (legacy) when `load_platt_model_v2` returns None. The fallback executes silently. Operators monitoring calibration health have no signal that v2 coverage is incomplete for some clusters/seasons. Pre-P3.4, the only way to detect v2-miss-then-legacy-hit is to query the DB after the fact, which doesn't surface cumulative fallback rate.

**Structural fix.** Add WARNING-level log at each fallback site naming the (cluster, season, metric) bucket that fell back. Include a `legacy_fallback_count` counter in the calibration return tuple OR via a module-level counter for ops scrape. The simplest first step: WARNING log at fallback time.

#### Slice P3.4 — Operator-visible WARNING on v2→legacy fallback

Scope: `src/calibration/manager.py` only (2 fallback sites).

Change at L172 (primary bucket fallback):
```python
if model_data is None and temperature_metric == "high":
    bk = bucket_key(cluster, season)
    legacy_data = load_platt_model(conn, bk)
    if legacy_data is not None:
        logger.warning(
            "v2_to_legacy_fallback: cluster=%s season=%s metric=high "
            "primary v2 missed; serving legacy platt_models. Operator "
            "review v2 coverage gap.",
            cluster, season,
        )
        model_data = legacy_data
    else:
        model_data = legacy_data  # both v2 + legacy missed; later checks handle
```

Change at L232 (season-only fallback) — twin pattern.

Relationship test (NEW):
- "When load_platt_model_v2 returns None and load_platt_model returns a model, a WARNING log fires identifying cluster/season." caplog-driven test.
- "When v2 returns a model, no fallback warning fires (happy-path silence)."
- "When both v2 + legacy miss, no fallback warning (only the existing 'no calibrator available' path applies)."

Function tests:
- get_calibrator behavior unchanged; only the log surfaces.

Acceptance:
- 3 log-assertion tests pass.
- get_calibrator regression unchanged.
- Operator runbook addendum (DEFERRED — separate operator packet) describing the new WARNING + how to interpret.

Blast radius: low. Pure observability addition; no behavior change.

---

## 4. Slice ordering and dependencies

```
P3.4 (alerting)         — independent, lowest blast, ship first
P3.2 (CI fallback)      — independent, low-medium blast
P3.3 (execution typing) — independent, medium blast (executor hot path)
P3.1a (cleanup)         — DEFERRED (no functional value)
```

Suggested execution sequence:
1. **P3.4** — observability win immediately.
2. **P3.2** — restores entry/exit CI symmetry on the fallback path.
3. **P3.3** — typed boundary extension.
4. P3.1a — deferred.

---

## 5. Test topology (relationship tests first)

| Slice | Relationship test | Where |
|---|---|---|
| P3.2 | refresh_position with no bootstrap_ctx + entry_ci_width > 0 produces non-degenerate EdgeContext | new `tests/test_monitor_refresh_ci_fallback.py` |
| P3.3 | executor SlippageBps boundary; RealizedFill at receipt | new `tests/test_executor_typed_boundary.py` |
| P3.4 | WARNING log fires on v2 miss + legacy hit; silent on v2 hit | new `tests/test_calibration_v2_fallback_alerting.py` |

---

## 6. Acceptance gates per slice

Each slice must:
1. Pass relationship + function tests.
2. Pass focused regression diff = 0 new failures.
3. NOT mutate production DB rows. P3 makes no schema/DB changes.
4. NOT change manifest files (mesh-maintenance separate operator packet).
5. Commit-by-slice; reviewer pass after all 3 land.

---

## 7. Risk + blocker matrix

| Slice | Risk | Mitigation | Blocker |
|---|---|---|---|
| P3.4 | None — additive logger calls | logger already used in file; consistent with L182/235/283/etc | None |
| P3.2 | Behavior change at no-bootstrap-context fallback path. Could surface latent bugs in tests that relied on degenerate CI | Tests that depended on degenerate CI must be updated; expected to be few | None |
| P3.3 | Executor hot path; type-strictness may break callers passing raw floats | Phase 3 starts with boundary threading (constant + receipt); strict type-rejection at API entry deferred to P3.3b sub-slice if surfaced | Audit may reveal more sites than expected |

---

## 8. Out-of-scope (explicitly)

- Operator-driven dashboards / alerting infrastructure — P3.4 surfaces logs; consuming them is ops work.
- A1b semantic_linter extension to calibration_pairs (parent §11 deferred).
- Mesh-maintenance registration packet.
- P3.1a cleanup (deferred).

---

## 9. Open questions for operator

**Q1.** Slice P3.3: should `max_slippage` be expressed as bps or pct? Current value `0.02` is ambiguous — could be 0.02 bps (very tight) or 2% (very loose). Recommendation: confirm intent (likely 2% per typical execution defaults), use `SlippageBps.from_pct(0.02)` to make units explicit.

**Q2.** Slice P3.4: should the legacy-fallback signal also propagate to status_summary / Discord ops dashboard? Recommendation: WARNING log first (this packet); dashboard wiring is a separate ops packet.

**Q3.** P3.1a cleanup: prune `obs_dominates()` + `day0_obs_dominates_threshold()` now that all callers migrated, or keep for backward-compat with hypothetical external scripts? Recommendation: prune in a future cleanup packet after a usage-census sweep across project memory + git log.

---

## 10. Provenance and authority

This plan is operational evidence under the parent packet. Authority basis:
- Parent: `docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md` (phase 1 + phase 2)
- `zeus/AGENTS.md` — money path, INVs, planning-lock
- `docs/operations/known_gaps.md` — already-FIXED claims for Day0 weighting + entry/exit MC count + exit CI-aware
- PR #19 workbook P3 section (vendored at parent `evidence/pr19_workbook_snapshot.md`)
- `src/contracts/realized_fill.py` + `src/contracts/slippage_bps.py` + `src/contracts/tick_size.py` — pre-existing typed atoms

Memory citations applied:
- `feedback_grep_gate_before_contract_lock.md` — citations re-grep'd 2026-04-26.
- `feedback_critic_reproduces_regression_baseline.md` — reviewers must independently re-run.
- `feedback_no_git_add_all_with_cotenant.md` — phase 3 commits stage only files this packet owns.

End of phase 3 plan.
