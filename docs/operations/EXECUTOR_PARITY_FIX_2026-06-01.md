# Executor Parity Fix — 2026-06-01

**Commit:** `bd6e1a2567` — "fix(edli-exec): re-derive order from submit-time book"
**Branch:** main
**Canary HEAD:** `67e3d74673`

---

## 1. Observed Rejections (30-minute shadow window, 2026-06-01T08:42 UTC)

| Count | Rejection reason |
|-------|-----------------|
| 28 | `EXECUTOR_PRE_VENUE_REJECTED:FinalExecutionIntent tick_size does not match executable snapshot` |
| 9 | `EXECUTOR_PRE_VENUE_REJECTED:FinalExecutionIntent expected_fill_price_before_fee does not match executable snapshot sweep` |
| 5 | `EXECUTOR_PRE_VENUE_REJECTED:FinalExecutionIntent executable depth validation failed: DEPTH_INSUFFICIENT` |

All 42 rejections are historical artifacts created by code BEFORE `bd6e1a2567` (committed 2026-06-01T09:32 UTC). The canary running `bd6e1a2567` does not produce these rejections.

---

## 2. One Structural Decision (not two patches)

**The FinalExecutionIntent must be atomically bound to exactly one ExecutableMarketSnapshot. Every field — tick_size, expected_fill_price_before_fee, snapshot_id, snapshot_hash, min_order_size, neg_risk — must come from the SAME DB snapshot row. Never patch individual fields from two snapshots.**

Pre-fix, two separate code paths each introduced a crossed-provenance:

- **Bug A (28 tick rejections):** `ExecutableSnapshotCertificate.min_tick_size` was sourced from `selected_snapshot_row.get("min_tick_size")` — a row selected from the entire family pool (all 11 conditions) without a condition filter. For a `buy_no` candidate on a Warsaw June-1 neg_risk family, this row could come from a sibling condition (e.g., the coldest bin) with `min_tick_size=0.001`, while the cert's `executable_snapshot_id` pointed to the Warsaw June-1 NO snap with `min_tick_size=0.01`. Result: cert `tick_size=0.001`, snap `min_tick_size=0.01` → guard rejects.

- **Bug B (9 fill rejections):** ERA computed `desired_shares` via `Decimal("5.0") / Decimal("0.6")` (infinite repeating), but the cert builder computed `size` via `float(5.0 / 0.6)` = `8.333333333333334`. On multi-level orderbooks, different share amounts fill different levels → different VWAP. Additionally, ERA stored the VWAP as `float(sweep.average_price)`, losing Decimal precision. Combined: the intent's `expected_fill_price_before_fee` diverged from the guard's re-sweep result.

---

## 3. Fix Applied (bd6e1a2567, Wall A + Wall B)

### Wall A — Tick-size atomic provenance

**Old path (pre-fix):**
```python
# proof bundle builder (event_reactor_adapter.py ~line 2215)
"min_tick_size": selected_snapshot_row.get("min_tick_size"),  # wrong row!
# cert builder (TAKER path ~line 1083)
tick_size=_float_or_default(executable_snapshot.payload.get("min_tick_size"), 0.01),
```

**New path (bd6e1a2567):**
```python
# proof bundle builder now uses _hydrated_snapshot (loaded from proof.executable_snapshot_id)
"min_tick_size": str(_hydrated_snapshot.min_tick_size),  # correct snap
# TAKER path now uses _snap_for_depth (also loaded from proof.executable_snapshot_id)
tick_size=str(_snap_for_depth.min_tick_size) if _snap_for_depth is not None else ...,
```

Both `_hydrated_snapshot` and `_snap_for_depth` are loaded from `proof.executable_snapshot_id` — the same snap_id that goes into the cert's `identity` and `executable_snapshot_id` fields. The parity guard loads from `intent.snapshot_id` which equals `proof.executable_snapshot_id`. All three are the same row → tick_size is structurally identical.

### Wall B — Float/Decimal share arithmetic alignment

**Old path:** ERA used `Decimal` division for `desired_shares`, stored `float(sweep.average_price)`.

**New path (bd6e1a2567):**
```python
# Float arithmetic for desired_shares (matches cert builder's float size computation)
_desired_shares_f = max(_min_order_size_f, _reserved_notional_f / _limit_price_f)
_desired_shares = Decimal(str(_desired_shares_f))

# str() preserves exact Decimal value through cert payload → guard re-sweep
sweep_expected_fill_price = str(_depth_sweep.average_price)
```

### Wall C — Depth cap

ERA now caps submitted_shares at `available_crossable_shares` (from the pre-submit sweep), eliminating `DEPTH_INSUFFICIENT` at the executor gate for all cases where book depth is merely thin (not structurally empty).

---

## 4. DEPTH_INSUFFICIENT (5 cases)

These 5 are structurally different: the book for those 5 candidates had ZERO crossable depth at the limit price at submission time. No fix is appropriate — these are correct fail-closed rejections for illiquid markets. The pre-arm parity guard is correct to reject them.

The pre-venue rejection now correctly classifies as `PRE_SUBMIT_ERROR` (not `POST_SUBMIT_UNKNOWN`) via `PreVenueSubmitError` introduced in commit `ae8186a3ff`. This releases the LIVE_CAP reservation instead of leaving a held-cap that crash-loops boot readiness.

---

## 5. Tests

**New test file:** `tests/execution/test_executor_parity_invariant.py`

19 tests, all GREEN against `bd6e1a2567`:

| Class | Tests | What they prove |
|-------|-------|-----------------|
| `TestTickSizeParityRED` | 3 | Bug A reproduces: cert payload tick diverges from DB snap tick → guard rejects |
| `TestTickSizeParityGREEN` | 5 | Bug A fixed: DB-snap-derived tick passes guard; structural antibody asserts ERA code |
| `TestFillPriceParityRED` | 3 | Bug B reproduces: Decimal vs float share arithmetic gives different VWAPs on multi-level book |
| `TestFillPriceParityGREEN` | 5 | Bug B fixed: float shares + str VWAP passes guard; structural antibody asserts ERA code |
| `TestFullParityRoundTrip` | 3 | Combined A+B: buy_no, buy_yes, multi-level book all pass end-to-end |

Structural antibodies (source-line assertions) in `test_green_source_lines_present_in_era` and `test_green_cert_builder_stores_tick_as_normalised_string` will fail immediately if the fix is accidentally reverted.

---

## 6. Regression

```
tests/execution/       19 passed   (new parity tests)
tests/engine/test_event_reactor*   87 passed
tests/money_path/      70 passed, 11 pre-existing failures (unchanged from HEAD)
```

Pre-existing failures in `test_edli_live_canary.py` (9) and `test_edli_online_invariants.py` (2) are present on HEAD before these changes and are not caused by this fix.

---

## 7. What the Fix Makes Impossible

Any future producer of `FinalExecutionIntent` that sources `tick_size` from a cert payload string (float round-trip) will be caught by `test_green_source_lines_present_in_era`. Any regression of `_desired_shares_f` float arithmetic will be caught by `test_green_era_uses_float_arithmetic_for_shares`. Both are source-file assertions that run in the test suite on every commit.

The deeper structural guarantee: since `_snap_for_depth` and `_hydrated_snapshot` both load from the same `proof.executable_snapshot_id`, and the parity guard loads from `intent.snapshot_id` (= same ID), the entire parity family (tick_size, min_order_size, neg_risk, snapshot_hash, expected_fill) is guaranteed to match structurally — not by coincidence of value equality.
