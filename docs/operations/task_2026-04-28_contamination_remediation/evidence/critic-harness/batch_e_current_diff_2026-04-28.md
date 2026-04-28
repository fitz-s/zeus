# Batch E current diff and verification evidence — 2026-04-28

Scope: `src/execution/executor.py`, `tests/test_tick_size.py`, `architecture/test_topology.yaml`, packet plan/work_log.

Goal: exit finite-price malformed input rejects before cutover, while valid exits still hit CutoverGuard before side effects.

## Verification summary

- Pre-edit safety-half regression: valid exit CutoverGuard test passed; malformed NaN/Inf tests failed with CutoverPending.
- Context-complete pre-edit critic approved after reading AGENTS/scoped/reference/handoff context.
- After edit: `tests/test_tick_size.py` = `24 passed`.
- Batch E suite: `68 passed, 1 skipped, 1 xfailed, 1 warning`.
- planning-lock: ok true.
- `py_compile`: pass.
- tests topology: global ok false from unrelated existing issues; no `tests/test_tick_size.py` issue.
- map-maintenance advisory: ok true with existing packet-file warnings.
- `git diff --check`: pass.

## Current scoped diff

```diff
diff --git a/src/execution/executor.py b/src/execution/executor.py
index dea3362..7681ce9 100644
--- a/src/execution/executor.py
+++ b/src/execution/executor.py
@@ -642,8 +642,6 @@ def execute_exit_order(
     from src.state.venue_command_repo import insert_command, append_event, get_command
     from src.contracts.executable_market_snapshot_v2 import MarketSnapshotError
 
-    _assert_cutover_allows_submit(IntentKind.EXIT)
-
     current_price = intent.current_price
     best_bid = intent.best_bid
     # T5.b 2026-04-23: replace bare 0.01 magic with TickSize typed
@@ -711,6 +709,7 @@ def execute_exit_order(
             idempotency_key=intent.idempotency_key,
         )
 
+    _assert_cutover_allows_submit(IntentKind.EXIT)
     _assert_risk_allocator_allows_exit_submit()
 
     # -----------------------------------------------------------------------
diff --git a/tests/test_tick_size.py b/tests/test_tick_size.py
index da96f6d..6c48a12 100644
--- a/tests/test_tick_size.py
+++ b/tests/test_tick_size.py
@@ -1,5 +1,5 @@
 # Created: 2026-04-23
-# Last reused/audited: 2026-04-23
+# Last reused/audited: 2026-04-28
 # Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.b TickSize typed contract + exit-path NaN closure for T5.a-LOW follow-up)
 
 """T5.b TickSize typed contract antibodies.
@@ -204,3 +204,19 @@ class TestExitPathNaNGuard:
         result = execute_exit_order(intent)
         assert result.status == "rejected"
         assert "malformed_limit_price" in result.reason
+
+    def test_valid_exit_still_hits_cutover_guard_before_side_effects(self):
+        """Valid exit intents must still pass CutoverGuard before live side effects."""
+        from src.control.cutover_guard import CutoverPending
+        from src.execution.executor import ExitOrderIntent, execute_exit_order
+
+        intent = ExitOrderIntent(
+            trade_id="t-valid-cutover",
+            token_id="tok-x",
+            shares=5.0,
+            current_price=0.50,
+            best_bid=None,
+        )
+
+        with pytest.raises(CutoverPending):
+            execute_exit_order(intent)
```
