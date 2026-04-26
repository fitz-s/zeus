# Zeus Phase 2 — Adjacent Fixes Surfaced During PR #19 Implementation

Created: 2026-04-26
Last audited: 2026-04-26
Authority basis: parent packet `docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md`; Zeus AGENTS.md; `current_state.md` (mainline = main; P4 still BLOCKED)
Status: planning evidence; not authority. No production DB or schema mutation in slices below except for one explicit additive ALTER TABLE migration (P2-B1) flagged operator-confirm-before-merge.
Branch: `claude/zeus-full-data-midstream-fix-plan-2026-04-26` (continuing from `cec0185`, parent worktree `/Users/leofitz/.openclaw/workspace-venus/zeus-fix-plan-20260426`)
Phase context: parent plan packet's slices A1+A2+B1+A3+A4 + four post-review fix commits closed. Phase 2 addresses adjacent issues that surfaced during phase 1 implementation but lay outside PR #19's named findings.
Operator note: multiple parallel worktrees are active; this branch is NOT scheduled for merge. Phase 2 lands locally first.

---

## 0. Scope statement

This packet is the **second phase plan** for the same parent task as the workbook in PR #19. It addresses three issue clusters that:

- Were surfaced during phase 1 implementation OR by the dual-reviewer pass.
- Live in the SAME modules / share the SAME structural concerns as phase 1's fixes.
- Were explicitly DEFERRED in the phase 1 plan addendum (§11) or noted only in commit messages.

**Phase 2 does:**
- Implement narrow code fixes for 3 clusters.
- Add antibody tests pinning each cluster's contract.
- Document each cluster's structural decision in this plan.

**Phase 2 does NOT:**
- Re-touch any phase 1 slice.
- Address P3 todos from the workbook (Day0 weighting, exit symmetry, slippage contracts) — those need their own deep plans.
- Address operator/data trust gates (parent §3.C).
- Push to remote — multiple worktrees are active.
- Mutate production DB rows. The ONE schema migration (P2-B1) is additive ALTER TABLE only; existing rows are unaffected; flagged for operator confirmation in §9.

---

## 1. Why phase 2 exists

Phase 1 closed the 10 findings + 16 todos in PR #19's workbook. During grep-gate audits and reviewer feedback, three families of related issues surfaced that:

- Affect the SAME files/modules phase 1 touched (calibration_pairs reads, evaluator snapshot writers, position metric handling).
- Share the SAME structural failure pattern as phase 1's findings (silent default to HIGH; missing metric thread-through; docstring-only contracts).
- Would CONTINUE to bleed phase 1's antibody value if left unaddressed (e.g., monitor_refresh's silent-HIGH override neutralizes Phase 9C L3's metric-aware get_calibrator gate).

Phase 1 was workbook-scoped. Phase 2 widens to the **cone of immediate adjacency** — one structural step out from each phase 1 fix.

---

## 2. Audit method (grep-gate, 2026-04-26)

Three audit queries across `src/calibration/`, `src/engine/`, `src/state/`:

- **Q1**: All callers of `get_pairs_for_bucket` / `get_pairs_count` / `get_decision_group_count` that don't pass `metric`. Found: 2 sites (evaluator.py:1000, monitor_refresh.py:177 + L371).
- **Q2**: `ensemble_snapshots` schema vs writer column expectations. Found: writer at `_store_snapshot_p_raw:1928` writes `bias_corrected`, schema `CREATE TABLE ensemble_snapshots` does not declare it. Real schema gap.
- **Q3**: `getattr(position, "temperature_metric", "high")` defensive defaults outside `chain_reconciliation.resolve_rescue_authority`. Found: 4 sites (lifecycle_events.py:100, monitor_refresh.py:140, 298, 334) that silently default to HIGH WITHOUT the UNVERIFIED authority tag that `resolve_rescue_authority` provides.

Premise rot: 0% — all citations grep-verified within 10-minute window before this plan write.

Detailed evidence: `evidence/phase2_audit_log.md`.

---

## 3. Structural decomposition (Fitz Constraint #1)

3 clusters → 3 decisions → 5 narrow code slices.

| Decision | Cluster | Findings | Type | Code slices |
|---|---|---|---|---|
| **P2-A. Adjacent calibration_pairs read sites must pass metric** | Q1 | 2 belt-and-suspenders K4 gate sites lack metric pass-through; defense-in-depth incomplete | Math + Architecture (low risk) | P2-A1, P2-A2 |
| **P2-B. ensemble_snapshots schema must declare `bias_corrected` column** | Q2 | Writer expects column; schema does not declare it. Cross-environment fragility (works on migrated prod DB; fails on fresh init_schema). | Architecture (additive schema) | P2-B1 |
| **P2-C. Position metric handling has one canonical resolver, not 4 silent defaults** | Q3 | 4 sites silently default missing `position.temperature_metric` to HIGH without UNVERIFIED tag; one of them (monitor_refresh:140) directly undermines Phase 9C L3's metric-aware calibrator gate | Math + Architecture (medium risk — touches monitor hot path) | P2-C1, P2-C2 |

This phase explicitly reuses phase 1's pattern of "encode insight as structure, not docs" (Fitz Constraint #2). Each slice replaces an implicit convention with an executable contract.

---

### 3.A Decision P2-A: Adjacent calibration_pairs reads pass metric

**Failure pattern.** Phase 1 slice A2 threaded `metric="high"` through `manager.py`'s 2 calls. But `get_pairs_for_bucket` / `get_pairs_count` / `get_decision_group_count` are also called from 2 GUARD CODE sites that exist solely to detect UNVERIFIED contamination as a belt-and-suspenders check:

- `src/engine/evaluator.py:1015-1024` (approx — within `_authority_verified` block)
- `src/engine/monitor_refresh.py:177-184, 371-378` (twin pattern at 2 places)

These guards don't pass metric, meaning they detect contamination across BOTH metric tracks but cannot localize the contamination to the active metric. For HIGH callers receiving a contamination alert, the alert may stem from LOW pairs that don't actually affect HIGH refit. False-positive alerts erode operator trust.

**Structural fix.** Pass `metric=temperature_metric` (or `metric="high"`) to these guard reads so contamination detection is metric-scoped.

**Findings resolved**:
- Defense-in-depth holes that defeat slice A1's purpose at the guard layer.
- False-positive contamination alerts on cross-metric noise.

#### Slice P2-A1 — Pass metric to evaluator K4 belt-and-suspenders gate

Scope: `src/engine/evaluator.py` only (single read site at L1015 area inside `evaluate_candidate`).

Change: replace `_get_pairs(conn, city.cluster, _cal_season, authority_filter='UNVERIFIED')` with `_get_pairs(conn, city.cluster, _cal_season, authority_filter='UNVERIFIED', metric=temperature_metric.temperature_metric if temperature_metric.temperature_metric == "high" else None)`. Use `None` for LOW since legacy table is HIGH-only and slice A1 raises NotImplementedError on `metric="low"`. For HIGH callers, pass `metric="high"`.

Relationship test: contamination alert for a HIGH evaluation must NOT include LOW UNVERIFIED rows (fixture DB seeded with mixed-metric UNVERIFIED rows; assert HIGH path's alert count excludes LOW).

Acceptance:
- HIGH callers see metric-scoped contamination signal.
- LOW callers don't trigger NotImplementedError (passing None preserves backward-compat).
- All existing tests pass.

Blast radius: low.

#### Slice P2-A2 — Same fix at monitor_refresh K4 gate (2 sites)

Scope: `src/engine/monitor_refresh.py` only (2 read sites at L177 + L371).

Change: same pattern as P2-A1. Both sites read `position.temperature_metric` (which P2-C1 will normalize through the new resolver — see ordering below).

Relationship test: same shape as P2-A1 but at monitor cycle.

Acceptance: identical to P2-A1.

Blast radius: low.

---

### 3.B Decision P2-B: ensemble_snapshots schema declares bias_corrected

**Failure pattern.** `_store_snapshot_p_raw:1928` writes `UPDATE ensemble_snapshots SET p_raw_json = ?, bias_corrected = ? WHERE snapshot_id = ?`. The `CREATE TABLE ensemble_snapshots` in `src/state/db.py:309` declares NO `bias_corrected` column. Production DB rows likely have the column due to past ad-hoc ALTER TABLE migrations OR the writer's UPDATE silently no-ops when the column is missing (depending on SQLite mode).

The phase 1 reviewer pass confirmed the test failure mode: fresh in-memory DB built via `init_schema(conn)` lacks the column → `_store_snapshot_p_raw` raises `OperationalError: no such column: bias_corrected` → wrapped except logs warning → caller receives empty / no-op result. Tests like `test_store_ens_snapshot_routes_to_attached_world_db` fail because `world_row["p_raw_json"]` is None.

**Structural fix.** Add `bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1))` to the `ensemble_snapshots` `CREATE TABLE`. Add an idempotent ALTER TABLE migration block (matching the pattern at db.py:880 for calibration_pairs) so existing prod DBs without the column gain it on next process startup.

**Findings resolved**:
- 2 phase 1 test_runtime_guards tests now actually pass (rather than failing at deeper rot).
- Production cross-environment fragility: fresh DBs (CI, dev environments) match prod DB's column set.

#### Slice P2-B1 — Add bias_corrected column + migration

Scope: `src/state/db.py` only.

Change:
1. In `CREATE TABLE ensemble_snapshots` (L309 area), add `bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1))`.
2. In the migration block (L880 area), add `"ALTER TABLE ensemble_snapshots ADD COLUMN bias_corrected INTEGER NOT NULL DEFAULT 0;"` to the idempotent ALTER list.

Relationship test:
- "After `init_schema`, `PRAGMA table_info(ensemble_snapshots)` returns a row for `bias_corrected` with type INTEGER NOT NULL DEFAULT 0."
- "Running init_schema twice on a DB that already has the column does not error (idempotency)."

Function tests:
- `_store_snapshot_p_raw` succeeds on a fresh-init DB without raising OperationalError.

Acceptance:
- Both `test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly` and `test_store_ens_snapshot_routes_to_attached_world_db` now pass.
- No production DB row mutation; only DDL.
- Operator-confirm-before-merge (§9 Q1).

Blast radius: low for fresh DBs (creates column; no-op for prod that already has it). MEDIUM for prod if column already exists with different default — the IF NOT EXISTS guard on ALTER TABLE prevents re-add.

---

### 3.C Decision P2-C: Single canonical position-metric resolver

**Failure pattern.** Phase 1 slice A4 made `chain_reconciliation.resolve_rescue_authority` the canonical source for "given a position, what's its (metric, authority, source)" — preserving UNVERIFIED tags so downstream consumers can filter strictly. But 4 OTHER sites do their own silent default:

| Site | Use of `position.temperature_metric` |
|---|---|
| `src/engine/lifecycle_events.py:100` | Writes to lifecycle event row's `temperature_metric` JSON field — silent HIGH default discards provenance. |
| `src/engine/monitor_refresh.py:140` | **CRITICAL**: passes silent HIGH to `get_calibrator(temperature_metric=...)`. A LOW position with missing metric receives the HIGH Platt model. **This directly undermines Phase 9C L3's metric-aware calibrator gate.** |
| `src/engine/monitor_refresh.py:298` | Reads metric for downstream signal computation. |
| `src/engine/monitor_refresh.py:334` | Same pattern, separate code path. |

The 4 sites pre-date phase 1's slice A4 anchor; nobody has wired them through it. Each silently HIGHs without tagging UNVERIFIED.

**Structural fix.** Add a peer to `resolve_rescue_authority` called `resolve_position_metric` returning `(metric: str, authority: str, source: str)` in `src/state/chain_reconciliation.py`. The metric value mirrors `resolve_rescue_authority`'s default-to-HIGH (preserves backward compat), but the authority tag surfaces UNVERIFIED for consumers that care. Then route the 4 sites through it.

For monitor_refresh.py:140 specifically, the calibrator call signature requires a string ("high"/"low"), so the resolver's metric value is what flows. The CRITICAL improvement is auditability: a structured-log line from the resolver records each "missing metric" event, so operators can trace LOW positions accidentally getting HIGH calibration.

**Findings resolved**:
- 4 silent-HIGH defaults consolidated into 1 honest helper.
- Phase 9C L3's metric gate is no longer undermined by upstream silent defaults.
- Operator visibility into "this position got the default metric" via structured log.

#### Slice P2-C1 — Add `resolve_position_metric` helper

Scope: `src/state/chain_reconciliation.py` only + new test file.

Change:
- Add `resolve_position_metric(position) -> tuple[str, str, str]` at the top of `chain_reconciliation.py` near `resolve_rescue_authority`. Return shape: `(metric, authority, source)`. Default-to-HIGH on missing/invalid (matches existing rescue_authority semantics) but tag UNVERIFIED with provenance source.
- Emit a structured DEBUG log when default fires (operator-traceable).

Relationship test:
- Position with `temperature_metric="high"` → `("high", "VERIFIED", "position_materialized")`.
- Position with `temperature_metric="low"` → `("low", "VERIFIED", "position_materialized")`.
- Position with missing/None/empty/garbage metric → `("high", "UNVERIFIED", "position_missing_metric:...")` (peer of resolve_rescue_authority's contract).
- Resolver logs each UNVERIFIED resolution at DEBUG level for operator audit.

Acceptance:
- New helper lands with passing tests.
- Helper is purely additive; no existing code routed through it yet (P2-C2 does that).
- New helper imported in tests/test_authority_strict_learning.py source scanner OR a new `test_position_metric_resolver.py` file.

Blast radius: low (additive only).

#### Slice P2-C2 — Route 4 sites through `resolve_position_metric`

Scope: `src/engine/lifecycle_events.py` (1 site), `src/engine/monitor_refresh.py` (3 sites).

Change at each site:
- Replace `getattr(position, "temperature_metric", "high")` with `resolve_position_metric(position)[0]` (or unpack with the authority tag if downstream consumer can use it).
- For lifecycle_events.py:100, also write the authority + source into the event JSON so downstream analytics can filter on it (mirrors the rescue_events_v2 pattern).

Relationship test:
- "A position with NO `temperature_metric` flowing through `monitor_refresh` does NOT silently call `get_calibrator(temperature_metric='high')` — instead, the path either (a) records a structured warning identifying the position OR (b) refuses to compute calibration." (Pick option (a) for backward compat; option (b) is too disruptive.)
- "Lifecycle event row written for a position with missing metric carries authority='UNVERIFIED' in the event JSON." Pinned via fixture-driven test.

Acceptance:
- All 4 sites import + use `resolve_position_metric`.
- Existing monitor + lifecycle tests pass (silent-HIGH continues but is now logged).
- Repository-wide grep for `getattr(position, "temperature_metric", "high")` returns 0 hits (only `resolve_rescue_authority` and `resolve_position_metric` may default in this module).

Blast radius: medium (touches monitor hot path). Run full monitor + lifecycle regression. Behavior change: WARNING-level logs may surface for legacy positions; this is OPERATOR-VISIBLE OUTPUT. Acceptable per phase 2's intent (audit visibility upgrade).

---

## 4. Slice ordering and dependencies

```
P2-B1 (schema migration) — independent, runs first to unblock test infrastructure
                          ↓
P2-A1 (evaluator gate)    — independent, can land any time after phase 1
                          ↓
P2-C1 (resolver helper)   — independent, additive
                          ↓
P2-C2 (route 4 sites)     — depends on P2-C1
                          ↓
P2-A2 (monitor gate)      — depends on P2-C2 (the position metric is now resolved consistently)
```

Suggested execution sequence:
1. **P2-B1** (schema fix, unblocks 2 runtime_guards tests immediately).
2. **P2-C1** (resolver helper, additive).
3. **P2-A1** (evaluator gate metric pass-through).
4. **P2-C2** (route 4 sites; surface OPERATOR-VISIBLE warnings for legacy positions).
5. **P2-A2** (monitor gate metric pass-through, using resolved position metric from P2-C2).

---

## 5. Test topology (relationship tests first)

| Slice | Relationship test | Where |
|---|---|---|
| P2-A1 | metric-scoped contamination alert in evaluator | extend `tests/test_calibration_store_metric_required.py` OR new `tests/test_evaluator_k4_gate_metric_scoped.py` |
| P2-A2 | metric-scoped contamination alert in monitor | new `tests/test_monitor_refresh_k4_gate_metric_scoped.py` |
| P2-B1 | bias_corrected column exists post-init_schema; idempotent | new `tests/test_ensemble_snapshots_bias_corrected_schema.py` |
| P2-C1 | resolve_position_metric returns (m, auth, src) for {high, low, missing, invalid} | new `tests/test_position_metric_resolver.py` |
| P2-C2 | 4 sites use resolver; lifecycle event JSON carries authority; missing-metric position logs warning | extend test_position_metric_resolver.py + add monitor/lifecycle integration test |

---

## 6. Acceptance gates per slice

Each slice must:
1. Pass relationship + function tests.
2. Pass focused regression diff = 0 new failures.
3. NOT mutate production DB rows. P2-B1 adds DDL only (column declaration + migration), idempotent.
4. Update no manifest files (mesh-maintenance is a separate operator-driven follow-on per parent §11).
5. Commit-by-slice (no bundled commits).

---

## 7. Risk + blocker matrix

| Slice | Risk | Mitigation | Blocker |
|---|---|---|---|
| P2-A1 | None (additive metric kwarg pass) | Existing K4 gate already catches the wider set; narrowing is strictly safer | None |
| P2-A2 | Same as P2-A1 | Same | None |
| P2-B1 | Schema migration on prod DB. ALTER TABLE on a populated table is fast SQLite operation but not zero-cost | Idempotent ADD COLUMN with DEFAULT 0; guard via `IF NOT EXISTS` pattern; operator-confirm before merge | Operator approval (§9 Q1) |
| P2-C1 | None (additive helper) | Pure-add | None |
| P2-C2 | OPERATOR-VISIBLE warnings for legacy positions with missing metric. Could be noisy at deployment | DEBUG-level log not WARNING (downgrade if too loud); operator should audit log volume on first deploy | None |

---

## 8. Out-of-scope (explicitly)

- P3 todos from workbook (Day0 weighting, exit symmetry, slippage typed contracts) — separate deep plans needed.
- C-track operator/data trust gates (parent §3.C).
- Mesh-maintenance registry updates for phase 1 + phase 2 new files (separate planning-lock-aware packet).
- A3 integration test gap (deferred per parent §11).
- A4 antibody regex tightening for CTE coverage (deferred per parent §11; documented as "intentionally loose").

---

## 9. Open questions for operator

**Q1.** Slice P2-B1 adds an ALTER TABLE migration. Confirm: (a) production `ensemble_snapshots` table is fine receiving `ADD COLUMN bias_corrected INTEGER NOT NULL DEFAULT 0` if column doesn't already exist, (b) if column DOES exist with different schema, the IF NOT EXISTS pattern silently skips. Recommendation: PROCEED — additive, idempotent, default value matches the writer's `int(False) = 0`.

**Q2.** Slice P2-C2 surfaces operator-visible WARNING/DEBUG logs for any position with missing `temperature_metric`. Recommendation: use DEBUG level (not WARNING) so first-week deploy doesn't spam logs; promote to WARNING after legacy positions are audited and updated.

**Q3.** PR #19 itself remains OPEN. Phase 2 commits will land on the same branch. If the user merges PR #19 first, this branch may need rebasing. Recommendation: continue local-only per user's explicit "先不合并" directive.

---

## 10. Provenance and authority

This plan is operational evidence under the parent packet. Authority basis:
- Parent: `docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md` (slices A1+A2+B1+A3+A4 + post-review fixes 1-4)
- `zeus/AGENTS.md` — money path, INVs, planning-lock
- `LEGAL_LIFECYCLE_FOLDS` (slice B1 reference) — terminal canonical set
- Phase 1 reviewer agents: critic-opus (`ad475fbba79c2b04c`) + code-reviewer-opus (`a10124e75fab05ec6`)

Memory citations applied:
- `feedback_grep_gate_before_contract_lock.md` — citations re-grep'd 2026-04-26 within 10-min window.
- `feedback_critic_reproduces_regression_baseline.md` — reviewers must independently re-run.
- `feedback_no_git_add_all_with_cotenant.md` — multiple worktrees active; `git add` only files this packet owns.

End of phase 2 plan.
