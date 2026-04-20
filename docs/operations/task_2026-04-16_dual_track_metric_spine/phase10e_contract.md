# Phase 10E Contract v2 — Final Phase 10 Closeout (post critic-eve cycle-3 precommit)

**Written**: 2026-04-19 post P10D slim (`f55f4e1`).
**Revised**: 2026-04-19 post critic-eve cycle-3 precommit — v1 had 3 CRITICAL + 3 MAJOR + 2 MINOR (eve 5/5 predictions hit). v2 absorbs all.
**Branch**: `data-improve` @ `f55f4e1`.
**Mode**: Gen-Verifier. critic-eve cycle 3 RETIREMENT.
**User ruling 2026-04-19**: R10 strict / 3 sub-commits / write ghost tests (not retire) / eve cycle-3 PASS→retire / team-lead writes handoff + recycles context.

## v1 → v2 delta

| Finding | v1 | v2 |
|---|---|---|
| **C1** replay wrap semantic fraud | `ExecutionPrice(value, "fee_adjusted", True, ...)` on raw probability | **Route replay through `_size_at_execution_price_boundary()` at `evaluator.py:255`** — single seam; OR explicit `ExecutionPrice(value, "implied_probability", fee_deducted=False, ...).with_taker_fee(fee_rate)` if needed. NO type-level lie. |
| **C2** evaluator.py:291 3rd bare caller missed | "2 typed callers" | **Delete shadow-off branch `evaluator.py:288-308`** entirely (strict R10 implies the shadow-off path is dead); bare `raw_entry_price` caller removed |
| **C3** test_k3_slice_q preserve impossible | "preserve 6 Bug#12 regressions" | **Explicit REWRITE** — split into (a) ExecutionPrice construction-validity tests (negative/>1/NaN → ValueError at `__post_init__`) + (b) kelly_size-level tests for remaining (bankroll≤0, p_posterior out-of-range, p_posterior≤entry). Assertion targets migrate where type subsumes |
| **M1** test migration undercount (4 files / 6 sites) | underestimated | **7 files / ~28 sites** enumerated: test_kelly.py (6), test_k3_slice_q.py (9 not 6), test_dual_track_law_stubs.py (3 incl. BC flip at L149), test_kelly_cascade_bounds.py (1), test_kelly_live_safety_cap.py (2), test_no_bare_float_seams.py (1+R-BW flip), test_execution_price.py (6) |
| **M2** harvester.py:868 already migrated | "harvester:868 missed by P10C" | **REMOVED from prod scope** — `harvester.py:862 + 877` both already pass `city_obj=city` (P10C). Only 1 script remaining: `rebuild_calibration_pairs_canonical.py:331`. Plus tests. |
| **M3** NC-08 false-positives on "threshold" | allowlist too loose | **Scope strictly to temperature unit names** `{temp, temperature, kelvin, celsius, fahrenheit}`. **DROP `threshold`** from pattern. Pre-verify false-positive rate = 0 via grep before locking test |
| **m1** INV-06 test hits `_get_stored_p_raw:601` legitimate fallback | all-harvester scope | Scope AST walk to **`harvest_settlement` function body only**; exclude `_get_stored_p_raw` |
| **m2** R-DD.5 redundant with R-DD.2 | separate | **Merged** — R-DD.5 removed; R-DD.2 tests 3 axes (price_type / fee_deducted / currency) |

## Scope — 3 sub-commits

### P10E.1 — R10 Kelly strict ExecutionPrice

**kelly.py signature**: `entry_price: ExecutionPrice` strict (drop `float |`)
**kelly.py body cleanup**: remove the `isinstance(entry_price, ExecutionPrice)` branch at L55-59; `assert_kelly_safe()` now called unconditionally

**evaluator.py**:
- L279: already passes `ep_fee_adjusted` ✓
- **L288-308 DELETE entire shadow-off branch** (strict R10 → shadow-off path impossible; `EXECUTION_PRICE_SHADOW` feature flag becomes moot here)

**replay.py:1361** (route through existing seam):
- Preferred: call `_size_at_execution_price_boundary(...)` if accessible from replay; else
- Fallback wrap: `ExecutionPrice(value=edge.entry_price, price_type="implied_probability", fee_deducted=False, currency="probability_units").with_taker_fee(_REPLAY_TAKER_FEE_RATE)` where `_REPLAY_TAKER_FEE_RATE` is read from replay settings / config
- DO NOT lie (`fee_deducted=True` on raw price)

**Test migration (7 files)**:

| File | Sites | Action |
|---|---|---|
| `tests/test_kelly.py` | 6 (L12/17/18/22/28/32) | wrap in valid ExecutionPrice |
| `tests/test_k3_slice_q.py` | 9 | **REWRITE** — split into ExecutionPrice-construction tests + kelly-level tests |
| `tests/test_dual_track_law_stubs.py` | 3 (L149 BC flip target) | L149 `bc_size` test: **flip to "strict rejects bare → TypeError"** |
| `tests/test_kelly_cascade_bounds.py:173` | 1 | wrap |
| `tests/test_kelly_live_safety_cap.py` | 2 (_LARGE/_SMALL_KWARGS) | wrap |
| `tests/test_no_bare_float_seams.py` | 1 (L227) + R-BW annotation at L273-311 | L227 wrap; R-BW flip in SAME commit to avoid red-window |
| `tests/test_execution_price.py` | 6 (L203/219/220/259/260/523) | audit per-test: some exercise typed path (keep) |

**Antibodies (4 after m2 merge)**:
- R-DD.1 `kelly_size(entry_price=0.42)` raises TypeError
- R-DD.2 `ExecutionPrice` with invalid axis (wrong price_type / fee_deducted / currency) fails `assert_kelly_safe()`; valid 4-field succeeds
- R-DD.3 AST — prod callers in src/ use `kelly_size(...)` with non-literal entry_price (no bare float constant)
- R-DD.4 Bug#12 regressions: 4 kept at kelly-level (bankroll / p_posterior bounds / p ≤ entry) + 4 migrated to ExecutionPrice `__post_init__` tests

### P10E.2 — `city_obj` strict requirement

**Prod scope (eve M2 correction)**:
- `src/calibration/store.py` — remove `city_obj: City | None = None` default; make required positional-or-kwarg
- `scripts/rebuild_calibration_pairs_canonical.py:331` — add `city_obj=cities_by_name[city_name]`
- `harvester.py:862 + 877` — already compliant (no change)
- `scripts/rebuild_calibration_pairs_v2.py:286` — grep-verify status; add if missing

**Test scope** (blanket):
- `test_calibration_manager.py` ~9 sites
- `test_calibration_bins_canonical.py` ~4 sites
- `test_phase4_foundation.py` 2 sites
- `test_phase4_rebuild.py` 2 sites
- `test_pnl_flow_and_audit.py` 1 site
- `test_data_rebuild_relationships.py` 1 site
- `test_phase10c_dt_seam_followup.py` ~4 sites (if applicable)

**Antibodies (3)**:
- R-DE.1 AST — every `add_calibration_pair*(` caller passes `city_obj=`
- R-DE.2 `add_calibration_pair(conn, city=str, ...)` without `city_obj` raises TypeError
- R-DE.3 HKO vs WU dispatch via SettlementSemantics unchanged post-strict

### P10E.3 — Loose ends + antibodies

**S3a — `Day0ObservationContext.causality_status` field**
- Add to dataclass `src/data/observation_client.py`
- Unblocks INV-16 test 3 (xfail → PASS)

**S3b — INV-06 ghost test (scoped per m1)**
- New test in `tests/test_cross_module_invariants.py`
- AST walk **`harvest_settlement` function body only** — assert snapshot lookup uses `decision_snapshot_id` not `MAX(fetch_time)`
- Excludes `_get_stored_p_raw:601` (legitimate fallback)

**S3c — NC-08 ghost test (scoped per M3)**
- New test in same file
- AST walk — flag `ast.Compare` where one side is bare float constant AND other side is `Name` with id matching `^(temp|temperature|kelvin|celsius|fahrenheit)` (strict regex, no "threshold")
- Pre-grep verification required: false-positive rate must be 0

**S3d — `MarketAnalysis` consumer rename**
- `evaluator.py:1219` `member_maxes=ens.member_maxes` → `member_maxes=ens.member_extrema` (kwarg stays; source expression clean)

**S3e — `members_json` LOW doc comment**
- `_store_ens_snapshot` inline comment: LOW rows store mins in `members_json`; downstream filters by new `temperature_metric` column

**S3f — eve MINOR-2 DEBUG→WARNING**
- `cycle_runtime.py:290-292` `_dual_write_canonical_entry_if_available` exception: DEBUG → WARNING with descriptor

**S3g — eve MINOR-1 SAVEPOINT integration**
- New test `tests/test_dt1_savepoint_integration.py` — monkeypatch `log_execution_report` raise; assert rollback

**Antibodies (6)**:
- R-DF.1 `Day0ObservationContext.causality_status` field + `"OK"` default
- R-DF.2 INV-16 test 3 xfail → PASS
- R-DF.3 INV-06 test: `harvest_settlement` uses `decision_snapshot_id`
- R-DF.4 NC-08 test: strict temperature identifier scope; false-positive = 0
- R-DF.5 SAVEPOINT integration via `execute_discovery_phase`
- R-DF.6 `_dual_write_canonical...` logs WARNING (not DEBUG)

## Total antibodies

P10E.1: 4 | P10E.2: 3 | P10E.3: 6 | **Total: 13**

## Hard constraints (unchanged)

- No HKO special branch / no architect packet / Golden Window intact / no CI config

## Acceptance envelope

Baseline post-P10D: 142 failed / 1936 passed / 92 skipped / 1 xfailed

Expected close (per-commit):
- P10E.1: delta failed = 0 (all 28 test sites migrated), passed +4 antibodies + new TypeError test + rewrite net (expect +5)
- P10E.2: delta failed = 0 (tests migrated), passed +3 antibodies
- P10E.3: delta failed = 0, passed +6 antibodies + INV-16 test 3 xfail→PASS (-1 xfailed); possibly +1 if SAVEPOINT integration catches pre-existing issue

**Total expected**: 140-142 failed / ~1950 passed / 92 skipped / 0 xfailed

## R-letter

R-DD (P10E.1 4) + R-DE (P10E.2 3) + R-DF (P10E.3 6) = 13 new

## Sequence

1. ✓ contract v2 ← this file
2. Single executor worker, 3 sub-commits serial:
   - Batch P10E.1 → team-lead disk verify + regression diff → stage
   - Batch P10E.2 → verify → stage
   - Batch P10E.3 → verify → stage
3. critic-eve cycle-3 wide review covering all 3 batches at once
4. PASS → 3 separate commits + push + eve retires | ITERATE → fix + re-verify
5. team-lead writes session handoff + recycles context

## Coordination

- L20: citations grep-gated in v2 per eve + scout findings
- L22: executor DOES NOT autocommit
- L28: team-lead + critic reproduce regression; envelope in commit msg
- L30: no new SAVEPOINT except S3g integration test

## Eve retirement prep

Post-PASS wide review: eve writes retirement verdict at `phase10_evidence/critic_eve_phase10e_wide_review.md` including L31-L40+ learnings for critic-frank successor (next critic opens at P11 or architect packet).
