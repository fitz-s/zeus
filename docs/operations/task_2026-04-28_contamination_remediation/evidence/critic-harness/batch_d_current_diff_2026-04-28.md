# Batch D current diff and verification evidence — 2026-04-28

Scope: `tests/test_riskguard.py`, `architecture/test_topology.yaml`, packet work_log.

Goal: test-only alignment of stale RiskGuard tests to current canonical portfolio / trailing-loss / contributor-level law. No production RiskGuard edits.

## Verification summary

- Red before edit: `16 failed, 31 passed`.
- Pre-edit critic approved test-only plan.
- After edit: `47 passed` for `tests/test_riskguard.py`.
- planning-lock: ok true.
- `py_compile`: pass.
- tests topology: global ok false from unrelated existing issues; no `tests/test_riskguard.py` issue.
- `git diff --check`: pass.
- protected production diff check: `src/riskguard/riskguard.py`, `src/riskguard/risk_level.py`, and `src/state/portfolio_loader_policy.py` empty.

## Current scoped diff

```diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index e9a6340..8ce69ed 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -25,7 +25,7 @@ test_trust_policy:
     audit_required: "No lifecycle header or missing required dates"
   enforcement: "topology_doctor test_trust check + digest gate output"
 
-  # Machine-readable registry: 61 tests with valid lifecycle headers.
+  # Machine-readable registry: 118 tests with valid lifecycle headers.
   # Tests not in this list require audit before running.
   trusted_tests:
     tests/test_alpha_target_coherence.py: {created: "2026-04-07", last_used: "2026-04-23"}
@@ -96,6 +96,7 @@ test_trust_policy:
     tests/test_phase5c_replay_metric_identity.py: {created: "2026-04-17", last_used: "2026-04-17"}
     tests/test_phase8_shadow_code.py: {created: "2026-04-18", last_used: "2026-04-25"}
     tests/test_platt.py: {created: "2026-03-30", last_used: "2026-04-23"}
+    tests/test_pnl_flow_and_audit.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_provenance_enforcement.py: {created: "2026-04-07", last_used: "2026-04-23"}
     tests/test_proxy_health.py: {created: "2026-04-21", last_used: "2026-04-21"}
     tests/test_semantic_linter.py: {created: "2026-04-13", last_used: "2026-04-25"}
@@ -109,14 +110,21 @@ test_trust_policy:
     tests/test_decision_evidence_runtime_invocation.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_exit_evidence_audit.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_harvester_dr33_live_enablement.py: {created: "2026-04-23", last_used: "2026-04-23"}
+    tests/test_harvester_metric_identity.py: {created: "2026-04-24", last_used: "2026-04-28"}
     tests/test_hold_value_exit_costs.py: {created: "2026-04-24", last_used: "2026-04-24"}
     tests/test_neg_risk_passthrough.py: {created: "2026-04-23", last_used: "2026-04-27"}
     tests/test_parse_canonical_bin_label.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_realized_fill.py: {created: "2026-04-23", last_used: "2026-04-23"}
+    tests/test_rebuild_pipeline.py: {created: "2026-04-12", last_used: "2026-04-28"}
     tests/test_replay_time_provenance.py: {created: "2026-04-25", last_used: "2026-04-25"}
+    tests/test_riskguard.py: {created: "2026-03-30", last_used: "2026-04-28"}
     tests/test_settlements_authority_trigger.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_settlements_verified_row_integrity.py: {created: "2026-04-23", last_used: "2026-04-23"}
+    tests/test_settlements_unique_migration.py: {created: "2026-04-24", last_used: "2026-04-28"}
     tests/test_settlement_semantics.py: {created: "2026-04-27", last_used: "2026-04-27"}
+    tests/test_sigma_floor_evaluation.py: {created: "2026-04-07", last_used: "2026-04-28"}
+    tests/test_edge_observation.py: {created: "2026-04-28", last_used: "2026-04-28"}
+    tests/test_supervisor_contracts.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_tick_size.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_vig_treatment_provenance.py: {created: "2026-04-24", last_used: "2026-04-24"}
     tests/test_zpkt.py: {created: "2026-04-25", last_used: "2026-04-25"}
diff --git a/tests/test_riskguard.py b/tests/test_riskguard.py
index 01a50b3..5a49bcb 100644
--- a/tests/test_riskguard.py
+++ b/tests/test_riskguard.py
@@ -1,3 +1,6 @@
+# Created: 2026-03-30
+# Last reused/audited: 2026-04-28
+# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md Batch D RiskGuard test-law remediation.
 """Tests for RiskGuard metrics, policy resolution, and risk levels."""
 
 import json
@@ -40,6 +43,21 @@ def _bootstrap_policy_tables(conn: sqlite3.Connection) -> None:
     apply_architecture_kernel_schema(conn)
 
 
+def _init_empty_canonical_portfolio_schema(
+    db_path,
+    *,
+    drop_risk_actions: bool = False,
+) -> None:
+    """Create canonical DB tables with an empty, healthy position_current view."""
+
+    conn = get_connection(db_path)
+    init_schema(conn)
+    if drop_risk_actions:
+        conn.execute("DROP TABLE IF EXISTS risk_actions")
+    conn.commit()
+    conn.close()
+
+
 def _insert_risk_action(
     conn: sqlite3.Connection,
     *,
@@ -403,7 +421,7 @@ class TestRiskGuardSettlementSource:
         assert details["portfolio_loader_status"] == "ok"
         assert details["portfolio_fallback_active"] is False
         assert details["portfolio_position_count"] == 1
-        assert details["portfolio_capital_source"] == "working_state_metadata"
+        assert details["portfolio_capital_source"] == "dual_source_blended"
         assert details["initial_bankroll"] == pytest.approx(150.0)
         assert details["daily_baseline_total"] == pytest.approx(151.0)
         assert details["weekly_baseline_total"] == pytest.approx(152.0)
@@ -436,21 +454,8 @@ class TestRiskGuardSettlementSource:
             lambda conn, limit=50, **kwargs: [{"p_posterior": 0.7, "outcome": 1, "source": "position_events", "metric_ready": True}],
         )
 
-        riskguard_module.tick()
-        row = get_connection(risk_db).execute(
-            "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
-        ).fetchone()
-        details = json.loads(row["details_json"])
-
-        assert details["portfolio_truth_source"] == "working_state_fallback"
-        assert details["portfolio_loader_status"] == "missing_table"
-        assert details["portfolio_fallback_active"] is True
-        assert details["portfolio_fallback_reason"] == "canonical snapshot unavailable: missing_table"
-        assert details["portfolio_position_count"] == 0
-        assert details["portfolio_capital_source"] == "working_state_metadata"
-        assert details["initial_bankroll"] == pytest.approx(150.0)
-        assert details["daily_baseline_total"] == pytest.approx(149.0)
-        assert details["weekly_baseline_total"] == pytest.approx(148.0)
+        with pytest.raises(RuntimeError, match="riskguard requires canonical truth source.*json_fallback"):
+            riskguard_module.tick()
 
     def test_get_current_level_fails_closed_when_risk_state_has_no_rows(self, monkeypatch, tmp_path):
         risk_db = tmp_path / "risk_state.db"
@@ -473,6 +478,7 @@ class TestRiskGuardSettlementSource:
                 return get_connection(risk_db)
             return get_connection(zeus_db)
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(
@@ -501,6 +507,7 @@ class TestRiskGuardSettlementSource:
                 return get_connection(risk_db)
             return get_connection(zeus_db)
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(
@@ -528,6 +535,7 @@ class TestRiskGuardSettlementSource:
                 return get_connection(risk_db)
             return get_connection(zeus_db)
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(
@@ -632,6 +640,7 @@ class TestRiskGuardSettlementSource:
             "settlement_capture": {"trades": 0, "pnl": 0.0},
         }
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
@@ -781,11 +790,11 @@ class TestRiskGuardTrailingLossSemantics:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert level == RiskLevel.RED
-        assert row["level"] == RiskLevel.RED.value
+        assert level == RiskLevel.DATA_DEGRADED
+        assert row["level"] == RiskLevel.DATA_DEGRADED.value
         assert details["daily_loss"] == pytest.approx(0.0)
         assert details["daily_loss_status"] == "degraded:insufficient_history"
-        assert details["daily_loss_level"] == RiskLevel.RED.value
+        assert details["daily_loss_level"] == RiskLevel.DATA_DEGRADED.value
         assert details["daily_loss_source"] == "no_trustworthy_reference_row"
         assert details["daily_loss_reference"] is None
 
@@ -820,11 +829,11 @@ class TestRiskGuardTrailingLossSemantics:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert level == RiskLevel.RED
-        assert row["level"] == RiskLevel.RED.value
+        assert level == RiskLevel.DATA_DEGRADED
+        assert row["level"] == RiskLevel.DATA_DEGRADED.value
         assert details["daily_loss"] == pytest.approx(0.0)
         assert details["daily_loss_status"] == "degraded:inconsistent_history"
-        assert details["daily_loss_level"] == RiskLevel.RED.value
+        assert details["daily_loss_level"] == RiskLevel.DATA_DEGRADED.value
         assert details["daily_loss_reference"] is None
 
     def test_tick_marks_no_reference_row_when_risk_history_is_empty(self, monkeypatch, tmp_path):
@@ -851,10 +860,11 @@ class TestRiskGuardTrailingLossSemantics:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert level == RiskLevel.RED
-        assert row["level"] == RiskLevel.RED.value
+        assert level == RiskLevel.DATA_DEGRADED
+        assert row["level"] == RiskLevel.DATA_DEGRADED.value
         assert details["daily_loss"] == pytest.approx(0.0)
         assert details["daily_loss_status"] == "degraded:no_reference_row"
+        assert details["daily_loss_level"] == RiskLevel.DATA_DEGRADED.value
         assert details["daily_loss_source"] == "no_trustworthy_reference_row"
         assert details["daily_loss_reference"] is None
 
@@ -877,7 +887,7 @@ class TestRiskGuardTrailingLossSemantics:
             total_pnl=-6.0,
             effective_bankroll=149.0,
         )
-        _insert_risk_state_row(
+        stale_reference_id = _insert_risk_state_row(
             risk_conn,
             checked_at=(datetime.now(timezone.utc) - timedelta(hours=27)).isoformat(),
             total_pnl=-8.0,
@@ -899,9 +909,10 @@ class TestRiskGuardTrailingLossSemantics:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert details["daily_loss"] == pytest.approx(0.0)
-        assert details["daily_loss_status"] == "degraded:inconsistent_history"
-        assert details["daily_loss_reference"] is None
+        assert details["daily_loss"] == pytest.approx(2.0)
+        assert details["daily_loss_status"] == "stale_reference"
+        assert details["daily_loss_source"] == "risk_state_history"
+        assert details["daily_loss_reference"]["row_id"] == stale_reference_id
 
     def test_tick_uses_trustworthy_reference_within_freshness_window(self, monkeypatch, tmp_path):
         zeus_db = tmp_path / "zeus.db"
@@ -1125,8 +1136,8 @@ class TestStrategyPolicyResolver:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert level == RiskLevel.RED
-        assert row["level"] == RiskLevel.RED.value
+        assert level == RiskLevel.YELLOW
+        assert row["level"] == RiskLevel.YELLOW.value
         assert details["execution_quality_level"] == "YELLOW"
         assert details["recommended_strategy_gates"] == ["center_buy"]
         assert "tighten_risk" in details["recommended_controls"]
@@ -1149,6 +1160,7 @@ class TestStrategyPolicyResolver:
         tracker = strategy_tracker_module.StrategyTracker()
         tracker.edge_compression_check = lambda window_days=30: ["EDGE_COMPRESSION: center_buy edge shrinking"]
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
@@ -1164,8 +1176,8 @@ class TestStrategyPolicyResolver:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert level == RiskLevel.RED
-        assert row["level"] == RiskLevel.RED.value
+        assert level == RiskLevel.YELLOW
+        assert row["level"] == RiskLevel.YELLOW.value
         assert details["strategy_signal_level"] == "YELLOW"
         assert details["recommended_strategy_gates"] == ["center_buy"]
         assert "review_strategy_gates" in details["recommended_controls"]
@@ -1344,6 +1356,7 @@ class TestStrategyPolicyResolver:
         tracker = strategy_tracker_module.StrategyTracker()
         tracker.edge_compression_check = lambda window_days=30: ["EDGE_COMPRESSION: center_buy edge shrinking"]
 
+        _init_empty_canonical_portfolio_schema(zeus_db, drop_risk_actions=True)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(riskguard_module, "load_tracker", lambda: tracker)
@@ -1374,6 +1387,7 @@ class TestStrategyPolicyResolver:
                 return get_connection(risk_db)
             return get_connection(zeus_db)
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(riskguard_module, "load_tracker", lambda: (_ for _ in ()).throw(RuntimeError("tracker unavailable")))
@@ -1389,8 +1403,8 @@ class TestStrategyPolicyResolver:
         ).fetchone()
         details = json.loads(row["details_json"])
 
-        assert level == RiskLevel.RED
-        assert row["level"] == RiskLevel.RED.value
+        assert level == RiskLevel.YELLOW
+        assert row["level"] == RiskLevel.YELLOW.value
         assert details["strategy_signal_level"] == "YELLOW"
         assert details["strategy_tracker_error"] == "tracker unavailable"
         assert details["recommended_strategy_gates"] == []
@@ -1404,6 +1418,7 @@ class TestStrategyPolicyResolver:
                 return get_connection(risk_db)
             return get_connection(zeus_db)
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(
@@ -1457,6 +1472,7 @@ class TestStrategyPolicyResolver:
                 return get_connection(risk_db)
             return get_connection(zeus_db)
 
+        _init_empty_canonical_portfolio_schema(zeus_db)
         monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
         monkeypatch.setattr(riskguard_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
         monkeypatch.setattr(
```
