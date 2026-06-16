# Escalation Re-decision Lane — Phase 2: FSR Routing Fix

**Date:** 2026-06-16
**Branch:** live/iteration-2026-06-13
**Status:** IMPLEMENTED, NOT COMMITTED

## Problem (Phase 2)

Phase 1 emitted `EDLI_REDECISION_PENDING` with source `escalation_cross-*` and
Tier-0 claim. The lane claimed correctly (attempt_count=4 fast), but then
transient-requeued forever:

```
reason=unsupported live candidate event type: EDLI_REDECISION_PENDING
```

Root cause: `EDLI_REDECISION_PENDING` is DORMANT — hard-blocked at 6+ dispatch sites:
- `candidate_binding.py:97` — raise
- `candidate_binding.py:203` — raise
- `verifier.py:134` — else → raise
- `edli_position_bridge.py:401` — else → raise
- `compiler.py:380` — completeness/snapshot skip gap
- `evaluator.py:2666` — completeness/snapshot skip gap

Patching all 6+ is fragile whack-a-mole. Robust fix: route through
`FORECAST_SNAPSHOT_READY` (fully supported, complete dispatch chain). The
`escalation_cross-` source prefix still uniquely identifies the Tier-0 lane.
`_family_rest_state` detects `escalated_after_rest=True` from venue truth at
decision time (event-type-independent), so FSR-typed escalation events still
cross as `TAKER_ESCALATED_AFTER_REST`.

## Diff

### `src/events/event_priority.py`

```diff
-# The escalation cancel job emits an EDLI_REDECISION_PENDING for a JUST-CANCELLED...
+# The escalation cancel job emits a FORECAST_SNAPSHOT_READY for a JUST-CANCELLED...
+# EDLI_REDECISION_PENDING is DORMANT (hard-blocked at 6+ dispatch sites)...

-      0  ESCALATION-ORIGIN EDLI_REDECISION_PENDING (source starts with
+      0  ESCALATION-ORIGIN FORECAST_SNAPSHOT_READY (source starts with
+         ``escalation_cross-``) — ... Routed through the FSR path (not the dormant
+         EDLI_REDECISION_PENDING type) so the full dispatch chain processes it
+         without hard-block gaps.

-    NON-ESCALATION FAIRNESS IS UNTOUCHED: the escalation clause matches ONLY an
-    EDLI_REDECISION_PENDING whose source begins with ``escalation_cross-``...
+    NON-ESCALATION FAIRNESS IS UNTOUCHED: the escalation clause matches ONLY a
+    FORECAST_SNAPSHOT_READY whose source begins with ``escalation_cross-``. A
+    regular FSR (source ``forecast_snapshot_ready_trigger`` or ``cycle-*``) does
+    NOT match and stays Tier 1.

-    escalation_tier0_clause = (
-        "WHEN e.event_type = 'EDLI_REDECISION_PENDING'\n"
+    escalation_tier0_clause = (
+        "WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'\n"
```

### `src/main.py`

```diff
-            event_type="EDLI_REDECISION_PENDING",
+            event_type="FORECAST_SNAPSHOT_READY",
```

### `tests/events/test_fetch_pending_escalation_cross_lane.py`

```diff
-    return make_opportunity_event(
-        event_type="EDLI_REDECISION_PENDING",
-        ...
+    return make_opportunity_event(
+        event_type="FORECAST_SNAPSHOT_READY",   # Phase 2: FSR path
+        ...
```

`_continuous_redecision` stays `EDLI_REDECISION_PENDING` with `source="cycle-tok-7"`.
Under the new clause (`FORECAST_SNAPSHOT_READY AND source LIKE 'escalation_cross-%'`)
it still falls to Tier 2 (ELSE) — fairness invariant preserved.

### `tests/execution/test_escalation_redecision_emit.py`

```diff
-    assert captured["event_type"] == "EDLI_REDECISION_PENDING"
+    assert captured["event_type"] == "FORECAST_SNAPSHOT_READY"
```

## FSR Completeness Confirmation

`scan_committed_snapshots` builds payloads from committed forecast snapshots. The
`restrict_to_families` argument filters to only the specified (city,target_date,metric)
triples. `compiler.py:380` and `evaluator.py:2666` enforce
`completeness_status=COMPLETE + snapshot_id==causal_snapshot_id`. The FSR machinery
already satisfies these checks for committed snapshots. If a family's snapshot is
incomplete, the escalation FSR is correctly blocked — no completeness check weakened.

## Fairness Guard

The Tier-0 CASE clause is exact:
```sql
WHEN e.event_type = 'FORECAST_SNAPSHOT_READY'
  AND e.source LIKE 'escalation_cross-%'
THEN 0
```

Regular FSR has `source = 'forecast_snapshot_ready_trigger'` or `'cycle-{token}-{N}'` —
neither matches `escalation_cross-%`. Those stay Tier 1. Only an escalation-cancel-emitted
FSR (unique source prefix, no other emitter) gets Tier 0. Per-city fairness for all
non-escalation events is byte-identical to pre-fix.

## Test Results

```
tests/events/test_fetch_pending_escalation_cross_lane.py       2 passed
tests/execution/test_escalation_redecision_emit.py             4 passed  (incl. family recovery)
tests/execution/test_maker_rest_escalation.py                  12 passed
                                                               ----------
                                                               18 passed  1.07s
```

Full suite (excluding above, -x):
```
547 passed, 1 skipped, 19 deselected
1 FAILED: tests/contracts/test_decision_provenance.py::test_real_post_snapshot_rejection...
```

The single failure is **pre-existing** — confirmed by running on the Phase 1 commit
(`git stash` baseline) before our changes: same assertion error on same test.

## Operator Constraints Verified

- NO new cap, throttle, allowlist, or global budget change
- Per-city fairness for NON-escalation events unchanged (clause matches only `escalation_cross-%` FSR)
- Fail-closed: emit wrapped in try/except in `_maker_rest_escalation_cycle`
- Not committed, daemon not restarted
