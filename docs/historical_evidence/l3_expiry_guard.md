# L-3 Expiry Guard Evidence
# Created: 2026-05-06
# Authority basis: evidence/phase4_h_decision.md L-3; IMPLEMENTATION_PLAN §6 Gate 2

## Summary

Phase 4 critic L-3: `@untyped_for_compat` decorator (in `src/execution/live_executor.py`)
carries `_COMPAT_EXPIRES_AT = "2026-06-05"`. No CI check enforced removal before Phase 5.B.

## Resolution at Phase 5.B

`tests/test_untyped_for_compat_expiry.py` authored as a TIME-BOMB CI check:

- **Today (2026-05-06):** tests PASS — deadline 30 days in the future.
- **After 2026-06-05:** tests FAIL — by design, forcing decorator removal.

## What the test checks

1. `_COMPAT_EXPIRES_AT` constant is present and ISO-parseable.
2. `datetime.date.today() < date.fromisoformat(_COMPAT_EXPIRES_AT)` — fails past deadline.
3. Decorated functions carry `_compat_expires_at` attribute matching the module constant.
4. The decorator's embedded expiry is also a future date (belt-and-suspenders).

## Decorator call sites (as of 2026-05-06)

The `@untyped_for_compat` decorator is defined in `src/execution/live_executor.py` and is
exported for callers in `src/execution/venue_adapter.py`. No call sites with `@untyped_for_compat`
were found in the codebase at authoring time — the decorator is available for callers that
cannot immediately add `LiveAuthToken` parameter (Gate 2 R3 mitigation).

## Removal procedure (when tests start failing)

1. Grep: `grep -rn "@untyped_for_compat" src/`
2. For each call site: update the function signature to accept `LiveAuthToken` explicitly.
3. Verify Gate 2 tests pass: `python3 -m pytest tests/test_gate2_live_auth_token.py`
4. Delete `tests/test_untyped_for_compat_expiry.py` (no longer needed).
5. Remove `untyped_for_compat` and `_COMPAT_EXPIRES_AT` from `src/execution/live_executor.py`.

## Decorator call sites found: 0 active `@untyped_for_compat` usages

```
grep -rn "@untyped_for_compat" src/
# (no output — decorator defined but not yet applied to any function)
```

L-3 CLOSED at Phase 5.B by CI time-bomb test.
