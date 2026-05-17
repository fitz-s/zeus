# Phase 10D Contract v2 — Phase 10 Structural Closeout (SLIM: R10 + 18-callers split to P10E)

**Written**: 2026-04-19 post P10C push `18b510b`.
**Revised**: 2026-04-19 post critic-eve cycle-2 precommit + scout — **v1 had 3 CRITICAL (`ExecutionPrice.bare` fabricated + S4 LOW-skip destroys persistence + S3 @property breaks assignments) + 3 MAJOR**. User ruling: **Option B — slim P10D; R10 + 18-caller blanket defer to deeply-planned P10E**.
**Branch**: `data-improve` @ `18b510b`.
**Mode**: Gen-Verifier. critic-eve cycle 2 (precommit ITERATE → this v2 → wide-review).

## v1 → v2 delta

| eve cycle-2 finding | v1 | v2 |
|---|---|---|
| **C1** `ExecutionPrice.bare()` fabricated API | "wrap as `ExecutionPrice.bare(float)`" | **DROPPED from P10D.** R10 → P10E with proper 4-field `ExecutionPrice(value, "fee_adjusted", True, "probability_units")` construction. |
| **C2** S4 LOW-skip destroys runtime persistence (`ensemble_snapshots_v2` zero runtime writers in src/) | Skip legacy `_store_ens_snapshot` for LOW | **INVERT**: add `temperature_metric` column to legacy `ensemble_snapshots` via ADD COLUMN migration + stamp at write. Downstream filters on metric. No data loss. |
| **C3** S3 `@property` AttributeError at L279+L305 write sites | ambiguous "use member_extrema everywhere" | **Explicit**: step-1 rename L279 + L305 writes to `self.member_extrema`; step-2 add read-only `@property member_maxes`. Document `self.member_maxes_settled` at L313 as separate (NOT aliased). |
| **M1** `test_k3_slice_q.py` 9 validation regressions | mass convert to TypeError | **DROPPED from P10D.** Moved to P10E R10 scope. Test rewrite (not convert) preserving Bug#12 regressions. |
| **M2** S5 18 → ~50 callers | "18" | **DROPPED from P10D.** P10E covers 18-caller blanket migrate (grep accurate count + `harvester.py:868` second prod site). |
| **M3** causality_status `fetch_time DESC LIMIT 1` reads late-filed correction | 无 | **Fix**: thread `snapshot_id` through `_read_v2_snapshot_metadata`; lookup by snapshot_id if available, else `fetch_time DESC` fallback with doc caveat. |
| S6 `kelly_mult` registered already | "register kelly_mult" | **Simplified**: only need `invariants.yaml` INV-13 entry + `cycle_runner.py:226` escape-hatch removal. |
| L20 v2 schema causality column | 假设 | **grep-verified** `src/state/schema/v2_schema.py:135` has `causality_status TEXT NOT NULL DEFAULT 'OK'` column with CHECK constraint |

## Why this slim phase exists

Close Phase 10 structural forward-log **without** R10 + 18-caller blanket. User routing:

- **P10D (this)**: causality wire + ensemble naming + legacy metric column + INV-13 + ghost tests + workspace. ~300 LOC, single atomic commit. ≤1 day.
- **P10E (next)**: R10 Kelly strict ExecutionPrice + 18-caller `city_obj` blanket. Deeply planned — 50+ sites, breaking signature, test-rewrite discipline required. Separate contract.

## 范围 — 7 items, SINGLE atomic commit

### S1 — causality_status DB → evaluator → Day0Router wire

**Files**: `src/engine/evaluator.py`

**Current state** (grep-verified):
- `ensemble_snapshots_v2:135` has `causality_status TEXT NOT NULL DEFAULT 'OK'` column (6-value CHECK)
- `_read_v2_snapshot_metadata` at `evaluator.py:134-175` ONLY SELECTs `boundary_ambiguous`, not `causality_status`
- `Day0SignalInputs.causality_status: str = "OK"` default at `day0_router.py:38` (router gate works if fed)
- Evaluator at L895-908 builds Day0SignalInputs WITHOUT explicit `causality_status` kwarg → always `"OK"`

**Fix**:
1. Extend `_read_v2_snapshot_metadata` SELECT to include `causality_status` + `snapshot_id`
2. Thread `causality_status = v2_meta.get("causality_status", "OK")` into `Day0SignalInputs(...)` kwarg at L895-908
3. **M3 ordering fix**: thread `snapshot_id` when available (from candidate's edge origin); fallback to `fetch_time DESC LIMIT 1` with inline doc caveat

**Antibody R-CY.1**: v2 row `causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED"` → `Day0SignalInputs.causality_status` receives that string (not `"OK"` default) — mock conn test
**Antibody R-CY.2**: v2 row missing (Golden Window) → fallback to `"OK"` back-compat

### S2 — ensemble_signal.member_maxes → member_extrema (with ORDERED rename)

**File**: `src/signal/ensemble_signal.py`

**Write sites (MUST rename FIRST before adding property)**:
- L279: `self.member_maxes = member_maxes_for_target_date(...)` → `self.member_extrema = ...`
- L305: `self.member_maxes = corrected` (bias correction) → `self.member_extrema = corrected`

**Read sites** (internal, update):
- L303, L313, L411, L421, L425, L433, L466, L467 — all `self.member_maxes` reads → `self.member_extrema` reads
- **Keep `self.member_maxes_settled` at L313 as-is** — separate attribute (NOT aliased; different semantic — settled values)

**Then** add read-only `@property`:
```python
@property
def member_maxes(self) -> np.ndarray:
    """Deprecated alias — use member_extrema. For LOW this is mins (semantic disambiguation deferred to P10E+)."""
    return self.member_extrema
```

**External readers** (keep unchanged for 1 phase — property alias serves them):
- `monitor_refresh.py:205, 231, 406, 676` — 4 sites
- `evaluator.py:1219, 1751` — 2 sites
- `day0_router.py:69` — 1 site (via `inputs.member_maxes_remaining`)

**Antibody R-CZ.1**: `ens.member_extrema` returns the array; `ens.member_maxes` returns identical value (property alias works)
**Antibody R-CZ.2**: AST probe — `self.member_maxes = ` assignments in `ensemble_signal.py` = 0 (only the property remains); reads/writes inside class use `self.member_extrema`
**Antibody R-CZ.3** (eve M3-B caveat): `self.member_maxes_settled` remains separate attribute; surgical-revert probe verifies L313 `_simulate_settlement(self.member_extrema)` still works

### S3 — Legacy `ensemble_snapshots` metric-aware via additive column (eve C2 INVERSION)

**File**: `src/state/db.py` (schema init) + `src/engine/evaluator.py:1718-1756` (`_store_ens_snapshot`)

**Why inverted**: eve C2 found `ensemble_snapshots_v2` has zero runtime writers in src/ — only ETL scripts populate it. Skipping legacy write for LOW would lose snapshot persistence entirely. Downstream harvester `add_calibration_pair` joins on `snapshot_id` from legacy table.

**Fix**:
1. **Schema**: add `temperature_metric TEXT NOT NULL DEFAULT 'high'` column to legacy `ensemble_snapshots` via `ALTER TABLE ADD COLUMN` migration guard (pattern already used at P10C for `decision_time_status`)
2. **Write site at L1718-1756**: stamp `temperature_metric` in INSERT from candidate's metric (getattr pattern for backward-compat)
3. **Log** once per candidate: `logger.debug("snapshot_metric=%s", metric)` for observability

**Antibody R-DA.1**: new column exists in schema + defaults to `'high'` on existing rows
**Antibody R-DA.2**: LOW candidate → `_store_ens_snapshot` writes row with `temperature_metric='low'`
**Antibody R-DA.3**: HIGH candidate → row with `temperature_metric='high'` (back-compat + default)

### S4 — INV-13 yaml registration + escape hatch removal

**Files**:
- `architecture/invariants.yaml` — add INV-13 entry referencing `tests/test_provenance_enforcement.py`
- `src/engine/cycle_runner.py:226` — remove `requires_provenance=False` flag

**Current state (grep-verified)**:
- `config/provenance_registry.yaml:26-27` HAS `kelly_mult` entry — good, no registry work needed
- `architecture/invariants.yaml` — INV-13 NOT present (only archived spec has it)
- `cycle_runner.py:226`: `require_provenance("kelly_mult", requires_provenance=False)` — escape flag bypasses enforcement

**Fix**:
- Add INV-13 yaml entry:
```yaml
- id: INV-13
  name: provenance_required_for_kelly_cascade_multiplicatives
  statement: >
    Numeric multipliers entering Kelly sizing must be traceable to a
    registered provenance entry (config/provenance_registry.yaml).
  test: tests/test_provenance_enforcement.py
  severity: P1
```
- Remove `requires_provenance=False` flag at `cycle_runner.py:226` → call becomes `require_provenance("kelly_mult")`. If this raises because kelly_mult provenance_registry schema check fails (grep what it requires), add a minimal additional field to the yaml entry to satisfy.

**Antibody R-DB.1**: `require_provenance("kelly_mult")` without escape flag succeeds after fix
**Antibody R-DB.2**: AST grep — `requires_provenance=False` kwarg zero occurrences in `src/engine/`

### S5 — Ghost tests yaml cleanup (eve: safe)

**Files**:
- `architecture/invariants.yaml:56` — references ghost `tests/test_cross_module_invariants.py::test_inv03_harvester_prefers_decision_snapshot_over_latest`
- `architecture/negative_constraints.yaml:37,52` — same ghost references (2 tests)

Per scout: `tests/contracts/spec_validation_manifest.py` is a lookup list only, NOT a test-existence assertion. Safe to edit yaml without breaking other tests.

**Fix**: replace ghost test references with `# TODO: P10E/P11 — test not yet written, tracked by ticket` comments. Keep the INV/NC IDs but annotate absence.

### S6 — Workspace cleanup

Per scout (verified tracking):
1. `.gitignore` — add:
   ```
   raw/oracle_shadow_snapshots/
   docs/**/*.xlsx
   ```
   (`.DS_Store` already at L18)
2. `git rm --cached`:
   - `docs/to-do-list/zeus_data_improve_bug_audit_75.xlsx`
   - `docs/to-do-list/zeus_data_improve_bug_audit_100.xlsx`
   - `docs/artifacts/zeus_data_inventory.xlsx`
   (archive copy at `docs/archives/artifacts/zeus_data_inventory.xlsx` stays tracked — separate scope)
3. **DT packet archive** — DEFERRED (separate chore PR)

### S7 — INV-16 reassess post-S1

**File**: `tests/test_phase6_causality_status.py` — 3 currently-failing tests

Per scout:
- Test 1 (`test_evaluator_has_causality_status_reject_gate_for_low_track`): S1 wires causality into Day0SignalInputs → router already gates via `_LOW_ALLOWED_CAUSALITY` at `day0_router.py:55-57` → **should pass after S1**
- Test 2 (`test_causality_status_reject_is_distinct_from_observation_unavailable`): Same — **should pass after S1**
- Test 3 (`test_day0_observation_context_carries_causality_status`): requires `Day0ObservationContext` (observation_client dataclass, DIFFERENT from Day0SignalInputs) to carry `causality_status`. **NOT covered by S1 alone** — defer with `pytest.xfail("P10E: Day0ObservationContext.causality_status field addition")`

**Fix**: after S1 lands, run these 3 tests. If 1+2 pass as expected, leave them green. If 3 still fails, add `pytest.xfail` with P10E ticket.

**Antibody R-DC.1**: Tests 1+2 transition SKIP/FAIL → PASS post-S1 (regression math captures this)
**Antibody R-DC.2**: Test 3 explicitly xfailed with ticket (no silent skip)

## 硬约束

- No R10 Kelly strict (deferred to P10E)
- No 18-caller `city_obj` blanket migrate (deferred to P10E)
- No HKO special branch
- No architect packet work (B055, B099)
- Golden Window intact
- State files unchanged in commit

## 验收

**Baseline post-P10C**: 144 failed / 1921 passed / 92 skipped (eve measured envelope 144-146; delta direction per L28)
- delta failed ≤ 0 (strict against 146 conservative)
- delta passed ≥ 8 new antibodies + 2 INV-16 unblocks (S7 tests 1+2)
- delta skipped: 0 or +1 (test 3 xfailed if needed)

**R-letter**: R-CY onwards (R-CX.1 last in P10C)

**Antibodies (10 minimum)**:
- R-CY.1/2 causality_status wire
- R-CZ.1/2/3 ensemble_signal property + ordering
- R-DA.1/2/3 legacy ensemble_snapshots metric column
- R-DB.1/2 INV-13 provenance live
- R-DC.1/2 INV-16 tests transition

## Out-of-scope (→ P10E deep plan)

- **R10 Kelly strict ExecutionPrice** — use explicit 4-field `ExecutionPrice(value=X, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")` construction; wrap at 3 prod sites (evaluator 2 already compliant per scout; only replay.py:1363 needs fix); REWRITE `test_k3_slice_q.py` preserving 9 validation regressions (Bug#12); update `_LARGE_KWARGS`/`_SMALL_KWARGS` in `test_kelly_live_safety_cap.py`
- **18-caller `city_obj` blanket migrate** (grep-accurate ~50 sites): 2 scripts + harvester.py:868 + ~7 test files
- P11 ExecutionState Truth (Codex main path)
- Architect packets B055/B099
- `_TRUTH_AUTHORITY_MAP` R13
- semgrep continue-on-error → blocking (CI config)
- NC-12 Platt high/low mix antibody (data-dependent)
- Eve MINOR-1: SAVEPOINT integration test via `execute_discovery_phase`
- Eve MINOR-2: `_dual_write_canonical_entry_if_available` DEBUG→WARNING
- DT#1 atomicity caveat in team_lead_handoff.md
- DT packet archive

## 顺序

1. team-lead 写契约 ← 本文件 (v2)
2. ✓ scout returned
3. ✓ critic-eve cycle-2 precommit returned
4. **Single executor worker serial S1-S7** (all items in same commit)
5. team-lead disk-verify + L22 wide-review-before-push (regression diff vs post-P10C baseline 144/1921/92)
6. critic-eve cycle-2 wide review
7. ITERATE fix or PASS → team-lead commit + push

## 协调

- L20: all citations grep-gated 2026-04-19 v2 (incl. eve-M3 schema verify + edge.entry_price type check — deferred scope confirmed)
- L22: executor does NOT autocommit (L22 discipline)
- L28: team-lead + critic both reproduce regression baseline
- L30: no new SAVEPOINT — any `with conn:` work audited via feedback memory
