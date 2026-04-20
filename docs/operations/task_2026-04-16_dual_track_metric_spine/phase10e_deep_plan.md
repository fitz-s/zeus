# Phase 10E Deep Plan — Final Closeout (R10 Kelly Strict + 18-Caller Blanket + Loose Ends)

**Written**: 2026-04-19 post P10D slim (`f55f4e1`).
**Branch target**: `data-improve` @ `f55f4e1`.
**Status**: PLAN (not yet a locked contract — user reviews then open).
**Mode**: Gen-Verifier. critic-eve cycle 3 (retirement cycle after this — 3-cycle pattern per dave rotation precedent).

## Why this phase

P10A+B+C+D closed DT main-line + LOW-lane tail + HKO + structural seams + causality wire + legacy metric column + housekeeping. **P10E = final Phase 10 closure**. After P10E, Phase 10 Dual-Track Metric Spine Refactor is complete; next phase is either P11 Execution-State Truth (Codex main path) or architect packets (B055 DT#6 / B099 DT#1).

Items consolidated from every prior phase's forward-log + eve cycle-1/2 findings + Angle 1-5 external review residue.

## Scope — 3 sub-commits, sequenced

**P10E.1 — R10 Kelly strict ExecutionPrice** (breaking; high test surface)
**P10E.2 — 18-caller `city_obj` blanket migrate** (mechanical; ~50 sites)
**P10E.3 — Loose ends & hardening** (small structural + doc)

Sequence matters: 10E.1 first (isolates breaking change); 10E.2 second (mechanical sweep, no semantic surprise); 10E.3 third (polish + unblock INV-16 test 3).

---

## P10E.1 — R10 Kelly strict ExecutionPrice (DT#5 LIVE)

**Goal**: remove bare-float polymorphism from `kelly_size`; elevate DT#5 from PARTIAL → LIVE (INV-21 fully enforced at runtime boundary, not just type-annotation).

### Signature change

**File**: `src/strategy/kelly.py:33-35`

Current:
```python
def kelly_size(
    p_posterior: float,
    entry_price: float | ExecutionPrice,
    bankroll_usd: float,
    edge: float = 0.0,
    ...
)
```

Target:
```python
def kelly_size(
    p_posterior: float,
    entry_price: ExecutionPrice,   # strict — no bare float
    bankroll_usd: float,
    edge: float = 0.0,
    ...
)
```

### Prod callers (3 sites — grep-verified 2026-04-19)

| File:line | Current state | Action |
|---|---|---|
| `src/engine/evaluator.py:235` | Already passes `ep_fee_adjusted` (typed ExecutionPrice) | Verify — no change expected |
| `src/engine/evaluator.py:247` | Already passes `raw_entry_price` (typed ExecutionPrice) | Verify — no change expected |
| `src/engine/replay.py:1363` | Passes `edge.entry_price` (bare float per `market_analysis_family_scan.py:26`) | **WRAP** in explicit 4-field `ExecutionPrice(value=edge.entry_price, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")` |

### Test rewrite (NOT convert — preserve regressions per eve M1)

**`tests/test_k3_slice_q.py`** — 6 bare-float callsites testing **Bug #12 validation regressions**:
- `kelly_size(0.60, 0.0, 100.0) == 0.0` — entry_price=0 edge case
- `kelly_size(0.60, -0.10, 100.0) == 0.0` — negative entry_price
- `kelly_size(0.60, 0.40, 0.0) == 0.0` — zero bankroll
- `kelly_size(0.60, 0.40, -50.0) == 0.0` — negative bankroll
- `kelly_size(1.01, 0.40, 100.0) == 0.0` — p > 1
- `kelly_size(-0.01, 0.40, 100.0) == 0.0` — p < 0

**Strategy** (eve M1):
1. Rewrite each bare-float site to wrap value in `ExecutionPrice(value=X, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")`
2. PRESERVE all 6 validation regression assertions (they test kelly.py internal validation logic, not bare-float polymorphism)
3. ADD 1 NEW test: `test_kelly_requires_execution_price_raises` — asserts `kelly_size(0.6, 0.4, 100.0)` (bare float) raises TypeError

**`tests/test_kelly_live_safety_cap.py`** — `_LARGE_KWARGS` + `_SMALL_KWARGS` dicts at top of file (2 sites):
- Migrate `entry_price=0.40` (float) → `entry_price=ExecutionPrice(value=0.40, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")`

**`tests/test_execution_price.py`** — 8+ sites audit:
- Tests that compare bare-float vs typed ExecutionPrice behavior (e.g., L203-220 `test_fee_reduces_kelly_size`): re-read each. Those that verify "bare float still works" must be reframed as "bare float raises TypeError" OR kept as "ExecutionPrice-only path correctness"
- `ExecutionPrice.bare(float)` (fictional in eve C1) does NOT exist; use explicit 4-field construction

**`tests/test_no_bare_float_seams.py:273-309`** — already R-BW migration tests; update per new strict behavior (was "annotation accepts ExecutionPrice" → now "strict rejects bare float").

### Antibodies

| ID | Target |
|---|---|
| R-DD.1 | `kelly_size(entry_price=0.42)` raises `TypeError` (bare float strict) |
| R-DD.2 | `kelly_size(entry_price=ExecutionPrice(...))` with valid fields succeeds |
| R-DD.3 | Prod callers AST — all 3 `kelly_size(` callsites in src/engine/ pass `ExecutionPrice` (not bare literal) |
| R-DD.4 | `test_k3_slice_q` 6 validation regressions remain (pre-existing Bug#12) |
| R-DD.5 | `ExecutionPrice.assert_kelly_safe()` raises on non-fee-adjusted at `kelly_size` entry |

### Acceptance

- delta failed ≤ 0 (strict)
- delta passed = +5 antibodies (+any transition from test_k3_slice_q 6 regressions + 1 new TypeError test)
- INV-21 moves from PARTIAL → LIVE

### Risk

**HIGH** — breaking signature change. Any caller I miss crashes at runtime. Mitigation:
- Scout must exhaustively enumerate callers (grep `kelly_size(` across src/ + scripts/ + tests/)
- AST antibody R-DD.3 fails build if new bare-float caller appears post-fix
- Optional: add `mypy` check for the signature if project uses mypy

---

## P10E.2 — 18-caller `city_obj` blanket migrate (eve real count ~50)

**Goal**: Every `add_calibration_pair` / `add_calibration_pair_v2` caller passes `city_obj=<City>` kwarg. Removes the "backward-compat bare-WMO fallback" path at `src/calibration/store.py` (originally added as escape hatch in P10C). Simplifies HKO dispatch — every caller goes through `SettlementSemantics.for_city(city_obj).round_values`.

### Callers (grep-verified; eve real count ~50, contract v1's 18 was undercounted)

**Prod**:
- `src/execution/harvester.py:854` ✓ already migrated (P10C)
- `src/execution/harvester.py:868` — 2nd prod site not in P10C scope (eve M2 finding)
- `scripts/rebuild_calibration_pairs_canonical.py:331` — needs `city_obj=cities_by_name[city_name]`
- `scripts/rebuild_calibration_pairs_v2.py:286` — same

**Test**:
| File | Sites |
|---|---|
| `tests/test_calibration_manager.py` | ~9 (L161, 282, 341, 384, 427, 485, 534, 559, 754) |
| `tests/test_calibration_bins_canonical.py` | ~4 (L327, 341, 429, 448) |
| `tests/test_phase4_foundation.py` | 2 (L32, 67) |
| `tests/test_phase4_rebuild.py` | 2 (L42, 195) |
| `tests/test_pnl_flow_and_audit.py` | 1 (L3106) |
| `tests/test_data_rebuild_relationships.py` | 1 (L453) |
| `tests/test_phase10c_dt_seam_followup.py` | ~4 (if uses `add_calibration_pair*`) |

**Total**: ~25-30 unique callsites across ~10 files.

### Approach

Each caller needs `city_obj = cities_by_name[city_name]` resolution from `src.config`. In tests, this may require a minimal `cities.json`-loading fixture — check existing test setup for pattern.

### Step 2 — remove back-compat fallback in store.py

Once all callers pass `city_obj`, remove the `if city_obj is not None: ... else: bare_WMO` branch in `add_calibration_pair` + `add_calibration_pair_v2`. Function becomes non-polymorphic: `city_obj` required.

### Antibodies

| ID | Target |
|---|---|
| R-DE.1 | AST grep — every `add_calibration_pair*(` caller in src/ + scripts/ + tests/ passes `city_obj=` kwarg |
| R-DE.2 | `add_calibration_pair(conn, ...)` without `city_obj` raises `TypeError` (strict) |
| R-DE.3 | `city_obj` unwrap path: HKO gets oracle_truncate; WU gets WMO half-up (same as P10C but now universal) |

### Risk

**LOW** — mechanical sweep. Tests assert by calling with string `city="NYC"`; once `city_obj=cities_by_name["NYC"]` is added, test semantics unchanged.

---

## P10E.3 — Loose ends & hardening (structural + doc)

### S1 — `Day0ObservationContext.causality_status` field

**File**: `src/data/observation_client.py` — `Day0ObservationContext` dataclass

Add `causality_status: str = "OK"` field per INV-16 test 3 (currently xfailed with P10E ticket).

Tests 1+2 already pass post-P10D S1 (causality wire). Test 3 requires this field addition.

### S2 — `MarketAnalysis.member_maxes` consumer rename (eve out-of-scope observation)

**File**: `src/engine/evaluator.py:1219`

```python
MarketAnalysis(..., member_maxes=ens.member_maxes, ...)
```

Currently served by `@property member_maxes` alias added in P10D. Consumer can migrate to `ens.member_extrema` for clarity. Non-breaking (property stays).

### S3 — `members_json` for LOW semantic documentation

**File**: `src/engine/evaluator.py:1718-1756` (`_store_ens_snapshot`)

Add inline comment: for LOW rows, `members_json` stores daily **mins** (via `ens.member_extrema` → `.tolist()`). Legacy column name is maxes-oriented; downstream consumers must filter by `temperature_metric` column (added in P10D S3).

Alternative: rename column via ALTER TABLE (schema change; P11 candidate).

### S4 — eve MINOR-2: `_dual_write_canonical_entry_if_available` DEBUG → WARNING

**File**: `src/engine/cycle_runtime.py:290-292`

Current: swallows `RuntimeError` at `logger.debug`. Eve flagged as silent failure path.

Fix: upgrade log level to `WARNING`; include the exception message in the log; add observability counter `canonical_dual_write_skipped` (if status_summary supports metric counters).

### S5 — eve MINOR-1: SAVEPOINT integration test

**File**: new `tests/test_dt1_savepoint_integration.py` OR add to existing

Actual integration test that invokes `execute_discovery_phase` with a mid-transaction `log_execution_report` raise; assert rollback semantics. Currently R-CV.2 simulates SAVEPOINT in-test with mocks.

### S6 — `test_cross_module_invariants.py` ghost test resolution

**File**: `tests/test_cross_module_invariants.py` — scout says file exists but contains 4 functions, none matching ghost refs (INV-03, INV-04 references in yaml commented as TODO in P10D)

Decision:
- **(a)** Write INV-03 + INV-04 tests (substantive work — requires understanding what each enforces)
- **(b)** Formally retire them (remove from `invariants.yaml` + `negative_constraints.yaml` entirely, not just comment)
- **(c)** Leave TODO as-is (P10D already did this minimal action)

Recommend (b) — formal retirement. Less yaml clutter.

### Antibodies

| ID | Target |
|---|---|
| R-DF.1 | `Day0ObservationContext.causality_status` field exists + default `"OK"` |
| R-DF.2 | INV-16 test 3 transitions xfailed → PASS |
| R-DF.3 | (if S5) integration test exercises SAVEPOINT rollback end-to-end |
| R-DF.4 | eve MINOR-2 — `_dual_write_canonical...` logs WARNING on RuntimeError path |

---

## Hard constraints (all sub-commits)

- No HKO special branch
- No architect packet work (B055, B099)
- No Golden Window lift
- No `_TRUTH_AUTHORITY_MAP` changes (R13 pending architect)
- No semgrep CI config change (separate PR)
- No NC-12 enforcement (data-dependent)

## Regression baseline

Post-P10D: 142 failed / 1936 passed / 92 skipped / 1 xfailed.

Expected P10E close:
- P10E.1 delta: ~+5 passed (new antibodies) / 0 failed
- P10E.2 delta: +3 passed (antibodies) + possibly unblock tests that were xfailed for calibration shape
- P10E.3 delta: +4 passed (antibodies) + 1 xfailed → PASS (INV-16 test 3) + 1 skipped→passed (SAVEPOINT integration if written)

Total expected envelope: 140-142 failed / 1950-1955 passed / 91-92 skipped / 0 xfailed.

## R-letter

R-DD onwards (R-DC.2 last in P10D)

## Sequence

1. team-lead writes P10E contract v1 (converts this plan into locked contract)
2. scout landing-zone for all 3 sub-commits (esp. R-DD.3 AST + R-DE.1 grep exhaustive)
3. critic-eve cycle-3 precommit (her LAST cycle per rotation convention)
4. Executor batch 10E.1 → team-lead disk verify + regression → stage for commit-block
5. Executor batch 10E.2 → verify → stage
6. Executor batch 10E.3 → verify → stage
7. critic-eve cycle-3 wide review (covering all 3 sub-commits)
8. Commit each sub-commit separately (3 commits) → push

## P10E completion criteria

- R10 Kelly strict LIVE (DT#5 complete)
- All `add_calibration_pair*` callers use `city_obj` (HKO path universal)
- `Day0ObservationContext` carries `causality_status` (INV-16 fully enforced)
- eve MINOR-1/2 closed (integration test + WARNING)
- No R-letter namespace fragmentation

## What comes AFTER P10E

Phase 10 Dual-Track Metric Spine Refactor → **CLOSED**. Next-phase candidates (user ruling):
- **P11 ExecutionState Truth** — Codex main path (command journal + UNKNOWN first-class + RED de-risk + V2 preflight). Primary live-readiness.
- **B055 DT#6 architect packet** — graceful degradation law formal spec
- **B099 DT#1 architect packet** — atomic `_execute_candidate` full transaction
- **Workspace housekeeping chore** — DT packet archive + archived evidence dirs

## Open questions for user (before locking contract)

1. **P10E.1 bare-float tolerance**: Is there ANY bare-float caller we should keep (backdoor for testing?) or is strict truly strict? Recommend strict.
2. **P10E.3 S6 ghost tests**: retire formally (b) or write them (a)? Recommend (b) — the two tests are not load-bearing without substantial design work.
3. **Sub-commit batching**: 3 separate commits OR 1 big commit? Recommend 3 (cleaner revert surface; smaller diff per critic review).
4. **Eve rotation**: she retires after P10E cycle 3 per dave precedent. Successor critic-frank/etc opens on P11 or architect packet. OK to plan?

Awaiting user's ruling on these four before opening P10E contract.
