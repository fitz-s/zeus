# test_no_on_modal_reproof — Evidence Record
**Date:** 2026-06-15
**File:** `tests/decision/test_family_decision_engine.py`
**Test:** `test_no_on_modal_survives_select_via_edge_gated_relaxation`

## What was tested

A NO-on-modal candidate (`direction_law_ok=False`, `route.side="NO"`, `route.bin_id="b25"` = modal bin, `economics.edge_lcb=0.08 > 0`) must be admitted by `FamilyDecisionEngine._select` and returned as the selected candidate.

The fix committed in the deployed `family_decision_engine.py` defines `_direction_admitted(d)` as:

```python
def _direction_admitted(d):
    return d.direction_law_ok or (
        d.route.side == "NO" and d.economics.edge_lcb > 0.0
    )
```

and passes this predicate to **both**:
1. `after_direction = [d for d in after_executable if _direction_admitted(d)]`
2. `live_candidate_passes(..., direction_law_proof_present=_direction_admitted(d), ...)`

## RED-on-revert contract

Reverting the re-proof to `direction_law_proof_present=d.direction_law_ok` (False for NO-on-modal) makes `live_candidate_passes` return `False`, so `_select` returns `(None, NO_TRADE_NO_POSITIVE_EDGE)` — the entire favorite-longshot harvest class is silently re-zeroed. This is the exact bug that existed in commit `3c4aeecc75`.

## Assertions

```python
# Primary: NO-on-modal admitted, selected, direction_law_ok=False
assert reason is None
assert selected is not None
assert selected.route.side == "NO"
assert selected.route.bin_id == "b25"
assert selected.direction_law_ok is False   # admitted DESPITE direction-law illegality

# Side-proof: YES-on-non-modal (direction_law_ok=False, side="YES", edge_lcb>0) stays banned
selected2, reason2 = engine._select([yes_on_non_modal_cand])
assert selected2 is None
assert reason2 == NO_TRADE_NO_DIRECTION_LAW
```

## Candidate construction

| Field | Value | Reason |
|---|---|---|
| `route.side` | `"NO"` | NO-on-modal |
| `route.bin_id` | `"b25"` | modal bin (confirmed argmax-q for members ~25C) |
| `route.route_cost.executable` | `True` | passes executable gate |
| `economics.edge_lcb` | `0.08` | > 0 → relaxation fires; also passes edge gate |
| `economics.delta_u_at_min` | `0.001` | > 0 → passes live_candidate_passes ΔU-at-min check |
| `economics.optimal_delta_u` | `0.05` | > 0 → passes ΔU gate and argmax selection |
| `direction_law_ok` | `False` | NO-on-modal is direction-law-ILLEGAL |
| `coherence_allows` | `True` | coherence does not block |

## Pytest output

```
....... 
=============================== warnings summary ===============================
...
7 passed, 2 warnings in 1.74s
```

All 7 tests pass, including the new `test_no_on_modal_survives_select_via_edge_gated_relaxation`.
