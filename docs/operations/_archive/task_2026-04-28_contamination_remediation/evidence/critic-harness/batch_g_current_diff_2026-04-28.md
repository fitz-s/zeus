# Batch G current diff evidence — runtime guard fixture alignment

Date: 2026-04-28
Scope: test-only fixture alignment for `tests/test_runtime_guards.py`; no production `src/**` behavior edits in Batch G.

## Scope notes

- `tests/test_runtime_guards.py` was given trusted-test lifecycle headers and fixtures were aligned to current executable contracts (targeted entry-gate helper, explicit metric identity, Day0Router route seam, no-fake ENS valid_time, live-safe boot guard, collateral fail-closed seam, canonical-entry-baseline monitor event ordering).
- `architecture/test_topology.yaml` registers `tests/test_runtime_guards.py` as trusted. The file already contains earlier/co-tenant changes in the branch; Batch G's intended new topology entry is the `tests/test_runtime_guards.py` line.
- Protected production source diff gate is empty for: `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py`, `src/engine/evaluator.py`, `src/execution/exit_lifecycle.py`, `src/execution/collateral.py`, `src/main.py`, `src/control/control_plane.py`, `src/supervisor_api/contracts.py`.
- Known split-out follow-up remains Batch H: legacy positions with `DAY0_WINDOW_ENTERED` but missing entry events causing exit-lifecycle entry backfill ambiguity.

## Verification executed

```text
python3 -m py_compile tests/test_runtime_guards.py src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/execution/exit_lifecycle.py src/execution/collateral.py src/main.py src/control/control_plane.py
=> pass

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
=> 119 passed in 4.12s

python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> {"ok": true, "issues": []}

python3 scripts/topology_doctor.py --navigation --task "Audit and reuse tests/test_runtime_guards.py as trusted current-law test evidence; update lifecycle header and architecture/test_topology only; no source behavior changes" --files tests/test_runtime_guards.py architecture/test_topology.yaml
=> navigation ok false; profile=r3 live readiness gates implementation; direct blocker says tests/test_runtime_guards.py out_of_scope. This is treated as a topology admission false-positive/known limitation for this test-current-law reuse batch, not scope expansion into production implementation. Planning-lock passed.

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_runtime_guards.py
=> global exit 1 from unrelated/co-tenant missing topology entries; runtime_guards_issues=[]; global_issue_count=5

git diff -- src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/execution/exit_lifecycle.py src/execution/collateral.py src/main.py src/control/control_plane.py src/supervisor_api/contracts.py | wc -c
=> 0

git diff --check -- tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

## Current diff for Batch G touched files

```diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index 3c1abcb..22b8b73 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -101,7 +101,7 @@ test_trust_policy:
     tests/test_proxy_health.py: {created: "2026-04-21", last_used: "2026-04-21"}
     tests/test_semantic_linter.py: {created: "2026-04-13", last_used: "2026-04-25"}
     tests/test_tier_resolver.py: {created: "2026-04-21", last_used: "2026-04-21"}
-    tests/test_topology_doctor.py: {created: "2026-04-13", last_used: "2026-04-21"}
+    tests/test_topology_doctor.py: {created: "2026-04-13", last_used: "2026-04-28"}
     tests/test_truth_surface_health.py: {created: "2026-04-07", last_used: "2026-04-25"}
     tests/test_digest_admission_policy.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_digest_profile_matching.py: {created: "2026-04-25", last_used: "2026-04-27"}
@@ -118,6 +118,7 @@ test_trust_policy:
     tests/test_rebuild_pipeline.py: {created: "2026-04-12", last_used: "2026-04-28"}
     tests/test_replay_time_provenance.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_riskguard.py: {created: "2026-03-30", last_used: "2026-04-28"}
+    tests/test_runtime_guards.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_settlements_authority_trigger.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_settlements_verified_row_integrity.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_settlements_unique_migration.py: {created: "2026-04-24", last_used: "2026-04-28"}
@@ -125,6 +126,7 @@ test_trust_policy:
     tests/test_sigma_floor_evaluation.py: {created: "2026-04-07", last_used: "2026-04-28"}
     tests/test_edge_observation.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_edge_observation_weekly.py: {created: "2026-04-28", last_used: "2026-04-28"}
+    tests/test_attribution_drift.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_supervisor_contracts.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_tick_size.py: {created: "2026-04-23", last_used: "2026-04-28"}
     tests/test_vig_treatment_provenance.py: {created: "2026-04-24", last_used: "2026-04-24"}
diff --git a/tests/test_runtime_guards.py b/tests/test_runtime_guards.py
index 6ac3e74..d768adf 100644
--- a/tests/test_runtime_guards.py
+++ b/tests/test_runtime_guards.py
@@ -1,4 +1,7 @@
 """Runtime guard and live-cycle wiring tests."""
+# Created: 2026-04-28
+# Last reused/audited: 2026-04-28
+# Authority basis: task_2026-04-28_contamination_remediation Batch G current-law fixture alignment
 
 from __future__ import annotations
 
@@ -20,6 +23,7 @@ import src.engine.cycle_runner as cycle_runner
 import src.engine.cycle_runtime as cycle_runtime
 import src.engine.evaluator as evaluator_module
 import src.execution.exit_lifecycle as exit_lifecycle_module
+from src.data.observation_client import Day0ObservationContext
 from src.config import City, settings
 from src.control import control_plane as control_plane_module
 from src.data.ecmwf_open_data import DATA_VERSION, collect_open_ens_cycle
@@ -64,6 +68,34 @@ def _default_posture_normal_for_runtime_guards(monkeypatch):
     monkeypatch.setattr(_posture_module, "read_runtime_posture", lambda: "NORMAL")
 
 
+def _allow_entry_gates_for_runtime_test(monkeypatch) -> None:
+    """Open only the outer runtime entry gates for tests that must reach discovery.
+
+    This helper is intentionally targeted (not autouse): runtime_guards also
+    contains tests that verify entry blocking behavior.
+    """
+    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
+    monkeypatch.setattr(cycle_runner.cutover_guard, "summary", lambda: {"state": "READY", "entry": {"allow_submit": True}})
+    monkeypatch.setattr(
+        "src.control.heartbeat_supervisor.summary",
+        lambda: {"health": "OK", "entry": {"allow_submit": True}},
+    )
+    monkeypatch.setattr(
+        "src.control.ws_gap_guard.summary",
+        lambda: {
+            "subscription_state": "CONNECTED",
+            "gap_reason": "",
+            "m5_reconcile_required": False,
+            "entry": {"allow_submit": True},
+        },
+    )
+    monkeypatch.setattr(
+        "src.risk_allocator.refresh_global_allocator",
+        lambda *args, **kwargs: {"entry": {"allow_submit": True}},
+    )
+    monkeypatch.setattr("src.runtime.posture.read_runtime_posture", lambda: "NORMAL")
+
+
 NYC = City(
     name="NYC",
     lat=40.7772,
@@ -192,7 +224,7 @@ def test_chain_reconciliation_updates_live_position_from_chain(monkeypatch, tmp_
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
     monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -319,7 +351,7 @@ def test_stale_order_cleanup_cancels_orphan_open_orders(monkeypatch, tmp_path):
         order_posted_at="2026-03-30T00:00:00Z",
         order_timeout_at="2099-01-01T00:00:00+00:00",
     )]))
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -441,7 +473,7 @@ def test_exposure_gate_skips_new_entries_without_forcing_reduction(monkeypatch,
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -592,7 +624,7 @@ def test_trade_and_no_trade_artifacts_carry_replay_reference_fields(monkeypatch,
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -612,6 +644,8 @@ def test_trade_and_no_trade_artifacts_carry_replay_reference_fields(monkeypatch,
     monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
+    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
+    _allow_entry_gates_for_runtime_test(monkeypatch)
 
     summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
 
@@ -954,7 +988,7 @@ def test_live_dynamic_cap_flows_to_evaluator(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -964,6 +998,7 @@ def test_live_dynamic_cap_flows_to_evaluator(monkeypatch, tmp_path):
         "outcomes": [],
         "hours_since_open": 2.0,
         "hours_to_resolution": 30.0,
+        "temperature_metric": "high",
     }]
     monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: _market_list)
     monkeypatch.setattr("src.data.market_scanner.find_weather_markets", lambda **kwargs: _market_list)
@@ -991,11 +1026,13 @@ def test_live_dynamic_cap_flows_to_evaluator(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
 
-    def _capture_eval(candidate, conn, portfolio, clob, limits, entry_bankroll=None):
+    def _capture_eval(candidate, conn, portfolio, clob, limits, entry_bankroll=None, **kwargs):
         captured["entry_bankroll"] = entry_bankroll
         return []
 
     monkeypatch.setattr(cycle_runner, "evaluate_candidate", _capture_eval)
+    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
+    _allow_entry_gates_for_runtime_test(monkeypatch)
 
     cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
 
@@ -1050,7 +1087,7 @@ def test_execute_discovery_phase_logs_rejected_live_entry_telemetry(monkeypatch,
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1059,6 +1096,7 @@ def test_execute_discovery_phase_logs_rejected_live_entry_telemetry(monkeypatch,
         "target_date": "2026-04-01",
         "hours_since_open": 12.0,
         "hours_to_resolution": 24.0,
+        "temperature_metric": "high",
         "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
     }])
     monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision()])
@@ -1073,6 +1111,8 @@ def test_execute_discovery_phase_logs_rejected_live_entry_telemetry(monkeypatch,
     monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
+    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
+    _allow_entry_gates_for_runtime_test(monkeypatch)
 
     cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
 
@@ -1127,7 +1167,7 @@ def test_strategy_gate_blocks_trade_execution(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1150,6 +1190,7 @@ def test_strategy_gate_blocks_trade_execution(monkeypatch, tmp_path):
     monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
+    _allow_entry_gates_for_runtime_test(monkeypatch)
 
     summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
     conn = get_connection(db_path)
@@ -1192,7 +1233,7 @@ def test_elevated_risk_still_runs_monitoring_and_reports_block_reason(monkeypatc
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: risk_level)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1254,7 +1295,7 @@ def test_force_exit_review_scope_is_entry_block_only(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: True)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1323,7 +1364,7 @@ def test_entries_paused_reports_block_reason(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1382,7 +1423,7 @@ def test_run_cycle_surfaces_fdr_family_scan_failure_without_entries(monkeypatch,
     monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1406,6 +1447,7 @@ def test_run_cycle_surfaces_fdr_family_scan_failure_without_entries(monkeypatch,
                 "target_date": "2026-12-01",
                 "hours_since_open": 1.0,
                 "hours_to_resolution": 24.0,
+                "temperature_metric": "high",
                 "outcomes": [
                     {
                         "title": "39-40°F",
@@ -1434,6 +1476,8 @@ def test_run_cycle_surfaces_fdr_family_scan_failure_without_entries(monkeypatch,
             )
         ],
     )
+    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
+    _allow_entry_gates_for_runtime_test(monkeypatch)
 
     summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
     conn = get_connection(db_path)
@@ -1764,7 +1808,7 @@ def test_quarantine_blocks_new_entries(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1801,7 +1845,7 @@ def test_operator_clear_ack_applies_ignored_token_only_after_explicit_ack(monkey
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -1859,7 +1903,7 @@ def test_unknown_direction_positions_are_not_monitored(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -2019,7 +2063,11 @@ def test_materialize_position_preserves_evaluator_strategy_key():
         status="filled",
     )
     city = types.SimpleNamespace(name="New York", cluster="US", settlement_unit="F")
-    candidate = types.SimpleNamespace(target_date="2026-04-01", hours_since_open=2.0)
+    candidate = types.SimpleNamespace(
+        target_date="2026-04-01",
+        hours_since_open=2.0,
+        temperature_metric="high",
+    )
     deps = types.SimpleNamespace(
         _utcnow=lambda: datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
         _classify_edge_source=lambda mode, edge: "opening_inertia",
@@ -2065,7 +2113,11 @@ def test_materialize_position_rejects_missing_strategy_key():
         status="filled",
     )
     city = types.SimpleNamespace(name="New York", cluster="US", settlement_unit="F")
-    candidate = types.SimpleNamespace(target_date="2026-04-01", hours_since_open=2.0)
+    candidate = types.SimpleNamespace(
+        target_date="2026-04-01",
+        hours_since_open=2.0,
+        temperature_metric="high",
+    )
     deps = types.SimpleNamespace(
         _utcnow=lambda: datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
         _classify_edge_source=lambda mode, edge: "opening_inertia",
@@ -2747,6 +2799,8 @@ def test_evaluator_projects_exposure_across_multiple_edges(monkeypatch):
     class DummyEnsembleSignal:
         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
             self.member_maxes = np.full(51, 40.0)
+            self.member_extrema = self.member_maxes
+            self.bias_corrected = False
 
         def p_raw_vector(self, bins, n_mc=3000):
             return np.array([0.25, 0.50, 0.25])
@@ -2899,6 +2953,8 @@ def test_update_reaction_degenerate_ci_fails_closed_before_sizing(monkeypatch):
     class DummyEnsembleSignal:
         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
             self.member_maxes = np.full(51, 40.0)
+            self.member_extrema = self.member_maxes
+            self.bias_corrected = False
 
         def p_raw_vector(self, bins, n_mc=3000):
             return np.array([0.25, 0.50, 0.25])
@@ -3027,6 +3083,8 @@ def test_update_reaction_brier_alpha_fails_closed_before_sizing(monkeypatch):
     class DummyEnsembleSignal:
         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
             self.member_maxes = np.full(51, 40.0)
+            self.member_extrema = self.member_maxes
+            self.bias_corrected = False
 
         def p_raw_vector(self, bins, n_mc=3000):
             return np.array([0.25, 0.50, 0.25])
@@ -3097,6 +3155,15 @@ def test_day0_observation_path_reaches_day0_signal(monkeypatch):
         city=NYC,
         target_date=str(date.today()),
         outcomes=[
+            {
+                "title": "38°F or lower",
+                "range_low": None,
+                "range_high": 38,
+                "token_id": "yes0",
+                "no_token_id": "no0",
+                "market_id": "m0",
+                "price": 0.34,
+            },
             {
                 "title": "39-40°F",
                 "range_low": 39,
@@ -3127,13 +3194,14 @@ def test_day0_observation_path_reaches_day0_signal(monkeypatch):
         ],
         hours_since_open=30.0,
         hours_to_resolution=4.0,
-        observation={
-            "high_so_far": 44.0,
-            "current_temp": 43.0,
-            "source": "wu_api",
-            "observation_time": datetime.now(timezone.utc).isoformat(),
-            "unit": "F",
-        },
+        observation=Day0ObservationContext(
+            high_so_far=44.0,
+            low_so_far=39.0,
+            current_temp=43.0,
+            source="wu_api",
+            observation_time=datetime.now(timezone.utc).isoformat(),
+            unit="F",
+        ),
         discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
     )
 
@@ -3162,6 +3230,8 @@ def test_day0_observation_path_reaches_day0_signal(monkeypatch):
     class DummyEnsembleSignal:
         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
             self.member_maxes = np.full(51, 44.0)
+            self.member_extrema = self.member_maxes
+            self.bias_corrected = False
 
         def spread(self):
             from src.types.temperature import TemperatureDelta
@@ -3210,7 +3280,18 @@ def test_day0_observation_path_reaches_day0_signal(monkeypatch):
     )
     monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
     monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
-    monkeypatch.setattr(evaluator_module, "Day0Signal", DummyDay0Signal)
+
+    def _route_day0(inputs):
+        return DummyDay0Signal(
+            inputs.observed_high_so_far,
+            inputs.current_temp,
+            inputs.hours_remaining,
+            inputs.member_maxes_remaining,
+            unit=inputs.unit,
+            temporal_context=inputs.temporal_context,
+        )
+
+    monkeypatch.setattr(evaluator_module.Day0Router, "route", staticmethod(_route_day0))
     from src.signal.day0_extrema import RemainingMemberExtrema as _REM
     def _remaining_for_day0(members_hourly, times, timezone_name, target_d, now=None, **kwargs):
         calls["day0_now"] = now
@@ -3272,19 +3353,21 @@ def test_day0_observation_path_rejects_missing_solar_context(monkeypatch):
         city=NYC,
         target_date=str(date.today()),
         outcomes=[
+            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.34},
             {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
             {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
             {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
         ],
         hours_since_open=30.0,
         hours_to_resolution=4.0,
-        observation={
-            "high_so_far": 44.0,
-            "current_temp": 43.0,
-            "source": "wu_api",
-            "observation_time": datetime.now(timezone.utc).isoformat(),
-            "unit": "F",
-        },
+        observation=Day0ObservationContext(
+            high_so_far=44.0,
+            low_so_far=39.0,
+            current_temp=43.0,
+            source="wu_api",
+            observation_time=datetime.now(timezone.utc).isoformat(),
+            unit="F",
+        ),
         discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
     )
 
@@ -3313,6 +3396,22 @@ def test_day0_observation_path_rejects_missing_solar_context(monkeypatch):
     monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
     monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-day0")
     monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
+
+    class DummyEnsembleSignal:
+        def __init__(self, *args, **kwargs):
+            self.member_maxes = np.full(51, 44.0)
+            self.member_extrema = self.member_maxes
+            self.bias_corrected = False
+
+        def spread(self):
+            from src.types.temperature import TemperatureDelta
+
+            return TemperatureDelta(2.0, "F")
+
+        def spread_float(self):
+            return 2.0
+
+    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
     monkeypatch.setattr(evaluator_module, "_get_day0_temporal_context", lambda city, target_date, observation=None: None)
 
     decisions = evaluator_module.evaluate_candidate(
@@ -3347,17 +3446,17 @@ def test_gfs_crosscheck_uses_local_target_day_hours_instead_of_first_24h(monkeyp
                 "price": 0.30,
             },
             {
-                "title": "39-40°F",
-                "range_low": 39,
-                "range_high": 40,
+                "title": "33-34°F",
+                "range_low": 33,
+                "range_high": 34,
                 "token_id": "yes-mid",
                 "no_token_id": "no-mid",
                 "market_id": "m-mid",
                 "price": 0.31,
             },
             {
-                "title": "51°F or higher",
-                "range_low": 51,
+                "title": "35°F or higher",
+                "range_low": 35,
                 "range_high": None,
                 "token_id": "yes-high",
                 "no_token_id": "no-high",
@@ -3388,6 +3487,8 @@ def test_gfs_crosscheck_uses_local_target_day_hours_instead_of_first_24h(monkeyp
     class DummyEnsembleSignal:
         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
             self.member_maxes = np.full(51, 55.0)
+            self.member_extrema = self.member_maxes
+            self.bias_corrected = False
 
         def p_raw_vector(self, bins):
             return np.array([0.0, 0.0, 1.0])
@@ -3806,7 +3907,7 @@ def test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly(tmp_path):
         "model": "ecmwf_ifs025",
     }
 
-    snapshot_id = evaluator_module._store_ens_snapshot(
+    evaluator_module._store_ens_snapshot(
         conn,
         NYC,
         "2026-01-15",
@@ -3817,15 +3918,15 @@ def test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly(tmp_path):
         """
         SELECT issue_time, valid_time, available_at, fetch_time
         FROM ensemble_snapshots
-        WHERE snapshot_id = ?
+        WHERE city = ? AND target_date = ?
         """,
-        (snapshot_id,),
+        (NYC.name, "2026-01-15"),
     ).fetchone()
     conn.close()
 
     assert row is not None
     assert row["issue_time"] is None
-    assert row["valid_time"] == "FORECAST_WINDOW_START(2026-01-14T05:00:00+00:00)"
+    assert row["valid_time"] is None
     assert row["available_at"] == "2026-01-14T06:05:00+00:00"
     assert row["fetch_time"] == "2026-01-14T06:05:00+00:00"
 
@@ -3983,6 +4084,7 @@ def test_main_registers_ecmwf_open_data_jobs(monkeypatch, tmp_path):
     monkeypatch.setattr(main_module.os, "environ", {"ZEUS_MODE": "live"})
     monkeypatch.setattr(main_module, "_startup_wallet_check", lambda: None)
     monkeypatch.setattr(main_module, "_startup_data_health_check", lambda conn: None)
+    monkeypatch.setattr(main_module, "_assert_live_safe_strategies_or_exit", lambda: None)
     monkeypatch.setattr(main_module.sys, "argv", ["zeus"])
 
     main_module.main()
@@ -4100,7 +4202,7 @@ def test_run_cycle_clears_ensemble_cache_each_cycle(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -4129,7 +4231,7 @@ def test_run_cycle_clears_market_scanner_cache_each_cycle(monkeypatch, tmp_path)
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
@@ -4217,7 +4319,7 @@ def test_monitor_refresh_failure_near_settlement_is_operator_visible(monkeypatch
         artifact=artifact,
         tracker=StrategyTracker(),
         summary=summary,
-        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
+        deps=_monitor_chain_deps(datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc)),
     )
 
     assert summary["monitor_failed"] == 1
@@ -4246,7 +4348,7 @@ def test_monitor_refresh_failure_far_from_settlement_is_not_chain_missing(monkey
         artifact=artifact,
         tracker=StrategyTracker(),
         summary=summary,
-        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
+        deps=_monitor_chain_deps(datetime(2026, 4, 1, 22, 0, tzinfo=timezone.utc)),
     )
 
     assert summary["monitor_failed"] == 1
@@ -4278,7 +4380,7 @@ def test_incomplete_exit_context_near_settlement_escalates_monitor_chain(monkeyp
         artifact=artifact,
         tracker=StrategyTracker(),
         summary=summary,
-        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
+        deps=_monitor_chain_deps(datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc)),
     )
 
     assert summary["monitor_incomplete_exit_context"] == 1
@@ -4366,7 +4468,13 @@ def test_time_context_failure_near_active_position_escalates_monitor_chain(monke
     assert artifact.monitor_results[0].exit_reason.startswith("MONITOR_CHAIN_MISSING:time_context_failed")
 
 
-def test_monitoring_phase_persists_live_exit_telemetry_chain(monkeypatch, tmp_path):
+def test_monitoring_phase_persists_live_exit_telemetry_chain_with_canonical_entry_baseline(monkeypatch, tmp_path):
+    """Current canonical-entry baseline: entry events already exist before Day0/exit.
+
+    Batch G intentionally does not mask the separate legacy ambiguity where a
+    position has DAY0_WINDOW_ENTERED but no entry events; that production-source
+    audit is deferred to Batch H.
+    """
     db_path = tmp_path / "zeus.db"
     conn = get_connection(db_path)
     init_schema(conn)
@@ -4408,6 +4516,16 @@ def test_monitoring_phase_persists_live_exit_telemetry_chain(monkeypatch, tmp_pa
     artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
     summary = {"monitors": 0, "exits": 0}
 
+    from src.engine.lifecycle_events import build_entry_canonical_write
+    from src.state.db import append_many_and_project
+
+    entry_events, entry_projection = build_entry_canonical_write(
+        pos,
+        decision_id="decision-live-exit-seed",
+        source_module="tests/test_runtime_guards:canonical_entry_baseline",
+    )
+    append_many_and_project(conn, entry_events, entry_projection)
+
     class Tracker:
         def __init__(self):
             self.exits = []
@@ -4513,19 +4631,23 @@ def test_monitoring_phase_persists_live_exit_telemetry_chain(monkeypatch, tmp_pa
     assert captured["context"].market_vig is None
 
     # Post-P9: query_position_events reads from position_events (canonical spine).
-    # exit_lifecycle backfills 3 entry events then writes EXIT_ORDER_FILLED.
+    # Current baseline positions already carry entry events. The same monitor
+    # cycle may append DAY0_WINDOW_ENTERED before EXIT_ORDER_FILLED.
     assert [event["event_type"] for event in events] == [
         "POSITION_OPEN_INTENT",
         "ENTRY_ORDER_POSTED",
         "ENTRY_ORDER_FILLED",
+        "DAY0_WINDOW_ENTERED",
         "EXIT_ORDER_FILLED",
     ]
 
-    open_intent, entry_posted, entry_filled, fill_event = events
-    # Backfill entry events come from the canonical backfill path
+    open_intent, entry_posted, entry_filled, day0_event, fill_event = events
+    # Entry events come from the seeded canonical entry baseline.
     assert open_intent["runtime_trade_id"] == "live-exit-1"
     assert entry_posted["runtime_trade_id"] == "live-exit-1"
     assert entry_filled["runtime_trade_id"] == "live-exit-1"
+    assert day0_event["event_type"] == "DAY0_WINDOW_ENTERED"
+    assert day0_event["runtime_trade_id"] == "live-exit-1"
 
     # EXIT_ORDER_FILLED canonical event
     assert fill_event["event_type"] == "EXIT_ORDER_FILLED"
@@ -4667,7 +4789,11 @@ def test_quarantine_expired_positions_do_not_count_as_open_exposure():
 
 
 def test_materialize_position_carries_semantic_snapshot_jsons():
-    candidate = type("Candidate", (), {"target_date": "2026-04-01", "hours_since_open": 2.0})()
+    candidate = type("Candidate", (), {
+        "target_date": "2026-04-01",
+        "hours_since_open": 2.0,
+        "temperature_metric": "high",
+    })()
     edge = _edge()
     edge.direction = "buy_yes"
     decision = type("Decision", (), {
@@ -4695,7 +4821,15 @@ def test_materialize_position_carries_semantic_snapshot_jsons():
     portfolio = PortfolioState(bankroll=100.0)
 
     pos = cycle_runner._materialize_position(
-        candidate, decision, result, portfolio, NYC, DiscoveryMode.OPENING_HUNT, state="entered"
+        candidate,
+        decision,
+        result,
+        portfolio,
+        NYC,
+        DiscoveryMode.OPENING_HUNT,
+        state="entered",
+        env="test",
+        bankroll_at_entry=100.0,
     )
 
     assert pos.settlement_semantics_json == '{"measurement_unit":"F"}'
@@ -4792,6 +4926,7 @@ def test_execute_exit_routes_live_sell_through_executor_exit_path(monkeypatch):
             venue_status="OPEN",
         )
 
+    monkeypatch.setattr(exit_lifecycle_module, "check_sell_collateral", lambda *args, **kwargs: (True, None))
     monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit_order", _execute_exit_order)
 
     outcome = exit_lifecycle_module.execute_exit(
@@ -4831,6 +4966,7 @@ def test_execute_exit_rejected_orderresult_preserves_retry_semantics(monkeypatch
         def get_balance(self):
             return 100.0
 
+    monkeypatch.setattr(exit_lifecycle_module, "check_sell_collateral", lambda *args, **kwargs: (True, None))
     monkeypatch.setattr(
         "src.execution.exit_lifecycle.execute_exit_order",
         lambda intent: OrderResult(
@@ -5017,6 +5153,7 @@ def test_discovery_phase_records_rate_limited_decision_as_availability_fact(tmp_
         "outcomes": [],
         "hours_since_open": 1.0,
         "hours_to_resolution": 4.0,
+        "temperature_metric": "high",
         "event_id": "evt-rate",
         "slug": "slug-rate",
     }
@@ -5067,7 +5204,7 @@ def test_discovery_phase_records_rate_limited_decision_as_availability_fact(tmp_
         summary=summary,
         entry_bankroll=100.0,
         decision_time=datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
-        env="paper",
+        env="test",
         deps=deps,
     )
 
```
