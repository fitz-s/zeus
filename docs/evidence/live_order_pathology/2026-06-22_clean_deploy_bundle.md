# Clean Deploy Bundle — q_lcb Far-Tail Honesty + Cold-Start Guard (2026-06-22)

**Date:** 2026-06-22
**Branch:** claude/exit-q-cert (rebased onto deploy/full-lifecycle-fix-20260621)
**Commit:** f699fab6

---

## Base Verification

Branch reset to `deploy/full-lifecycle-fix-20260621` tip (6052c15d).
qkernel contamination check before applying fixes:

```
grep -c "bin_probability_settlement_kernel" src/data/replacement_forecast_materializer.py
# Result: 0  ✓ CLEAN BASE
```

---

## Files Changed (clean diff summary)

### Fix 1 — q_lcb Far-Tail Honesty

**File:** `src/data/replacement_forecast_materializer.py`

1. **Constants added** (after `_QLCB_SEED` at line ~1525):
   - `FAR_TAIL_Q_POINT_THRESH: float = 0.05`
   - `FAR_TAIL_LCB_FLOOR: float = 0.003`

2. **Cap applied** in `_build_fused_q_bounds` per-bin loop, after defensive ordering clips:
   ```python
   if q_pt < FAR_TAIL_Q_POINT_THRESH:
       lcb = min(lcb, FAR_TAIL_LCB_FLOOR)
   ```

3. **Provenance counter** `_far_tail_honesty_count: int = 0` added as local in `_compute_posterior_payload`. Computed from `_lcb_map` after bootstrap success, counting bins with `q_point < THRESH and lcb <= FLOOR + 1e-12`.

4. **Provenance stamps** added to `provenance_payload`:
   - `"q_lcb_far_tail_honesty_applied": _far_tail_honesty_count > 0`
   - `"q_lcb_far_tail_honesty_bin_count": _far_tail_honesty_count`

**Test file:** `tests/test_qlcb_far_tail_honesty.py` (11 tests, ported from c35bd604)

### Fix 2 — Cold-Start MIN_SETTLED_N Guard

**File:** `src/forecast/center.py`

1. **Constant added** (after `EMOS_OOS_STRENGTH_DEFAULT`):
   - `MIN_SETTLED_N: int = 30`  (exported)

2. **Guard in `walk_forward_model_weights()`** — at top of per-member loop, after `n_train` computed:
   ```python
   if n_train < MIN_SETTLED_N:
       precisions[i] = 0.0
       continue
   ```

3. **Guard in `raw_second_moment_weights()`** — at top of per-model loop, after `n_train` unpacked:
   ```python
   if int(n_train or 0) < MIN_SETTLED_N:
       precisions[model] = 0.0
       continue
   ```

**File:** `src/data/replacement_forecast_materializer.py`

4. **Field added** to `_BayesPrecisionFusionFusionOverride`:
   - `cold_start_excluded_models: tuple[str, ...] = ()`

5. **Derivation** in `_replacement_bayes_precision_fusion_override` (before K3 antibody block):
   ```python
   from src.forecast.center import MIN_SETTLED_N as _MIN_SETTLED_N
   _cold_start_excluded = tuple(sorted(
       str(_m) for _m, _v in _precision_center_basis.items()
       if int(_v["n"]) < _MIN_SETTLED_N and _v["weight"] == 0.0
   ))
   ```
   Passed as `cold_start_excluded_models=_cold_start_excluded` to the dataclass.

6. **Provenance stamp** added to the `bayes_precision_fusion_override` branch of `provenance_payload`:
   - `"cold_start_excluded_models": list(bayes_precision_fusion_override.cold_start_excluded_models)`

**Test file:** `tests/test_coldstart_min_n_guard.py` (16 tests, ported from 21230102)

---

## qkernel Contamination Verification

```
grep -c "bin_probability_settlement_kernel\|_qkernel_shape_lookup\|_live_beta\|qkernel_beta" \
  src/data/replacement_forecast_materializer.py
# Result: 0  ✓ NO QKERNEL
```

---

## Full Test PASS Tail

```
ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1 python -m pytest \
  tests/test_qlcb_far_tail_honesty.py \
  tests/test_coldstart_min_n_guard.py \
  tests/test_replacement_forecast_materializer.py \
  -p no:cacheprovider -q

...................................................               [100%]
50 passed, 1 warning in 3.46s
```

Breakdown:
- `test_qlcb_far_tail_honesty.py`: 11 passed
- `test_coldstart_min_n_guard.py`: 16 passed
- `test_replacement_forecast_materializer.py`: 23 passed

---

## Files to Patch into zeus-live-main

Exact 4-file set a deploy must apply:

1. `src/data/replacement_forecast_materializer.py` — Fix 1 constants + cap + provenance; Fix 2 dataclass field + derivation + provenance stamp
2. `src/forecast/center.py` — Fix 2 MIN_SETTLED_N constant + guard in both weight functions
3. `tests/test_qlcb_far_tail_honesty.py` — new file (11 tests)
4. `tests/test_coldstart_min_n_guard.py` — new file (16 tests)

No other files touched. Docs (impl reports) are on the contaminated branch and not required for the live patch.

---

## Constraints Compliance

- LIVE-DIRECT: no shadow, no flag-default-OFF
- Gaussian path only: `bin_probability_settlement` is the integrator (no kernel)
- q_point NEVER modified by Fix 1
- q_ucb / buy_no NEVER modified by Fix 1
- Fix 1 is monotone: can only decrease far-tail q_lcb
- Fix 2 mature-model path is byte-identical (guard branch never fires when n>=30)
- Fix 2 all-immature fallback = equal 1/n (no center refused)
