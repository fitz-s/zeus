# First-four current tracked diff — 2026-04-28

Generated after recovery pass; excludes unrelated untracked co-tenant files.

```diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index e9a6340..84035fe 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -25,7 +25,7 @@ test_trust_policy:
     audit_required: "No lifecycle header or missing required dates"
   enforcement: "topology_doctor test_trust check + digest gate output"
 
-  # Machine-readable registry: 61 tests with valid lifecycle headers.
+  # Machine-readable registry: 112 tests with valid lifecycle headers.
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
@@ -117,6 +118,7 @@ test_trust_policy:
     tests/test_settlements_authority_trigger.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_settlements_verified_row_integrity.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_settlement_semantics.py: {created: "2026-04-27", last_used: "2026-04-27"}
+    tests/test_supervisor_contracts.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_tick_size.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_vig_treatment_provenance.py: {created: "2026-04-24", last_used: "2026-04-24"}
     tests/test_zpkt.py: {created: "2026-04-25", last_used: "2026-04-25"}
diff --git a/docs/operations/AGENTS.md b/docs/operations/AGENTS.md
index 4c791e6..a050202 100644
--- a/docs/operations/AGENTS.md
+++ b/docs/operations/AGENTS.md
@@ -106,6 +106,7 @@ make a surface default-read unless `current_state.md` routes it.
 | `task_2026-04-26_polymarket_clob_v2_migration/` | packet evidence | Polymarket CLOB V1→V2 migration packet; now supporting R3 Z0 source-of-truth correction and later R3 CLOB V2 phases |
 | `task_2026-04-26_ultimate_plan/` | packet evidence | R3 ultimate implementation packet for Zeus CLOB V2 live-money execution and dominance infrastructure; phase cards, boot notes, work records, reviews, and M3 user-channel ingest evidence live under `r3/` |
 | `task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` | packet evidence | R3 Z0 packet-local live-money invariant summary for CLOB V2; not a durable authority doc |
+| `task_2026-04-28_contamination_remediation/` | packet evidence | Codex drift self-audit and contamination remediation packet; temporary evidence only, not live-deploy authority |
 | `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/` | package input | Attached reconstruction package input; not universal authority |
 | `zeus_topology_system_deep_evaluation_package_2026-04-24/` | package input | Topology system deep evaluation and P0–P5 reform roadmap (P0–P5 implementation landed via PR #15 + #13/#14 + commits `c495510`..`0ca6db9`); package preserved as historical evaluation evidence |
 
diff --git a/docs/operations/current_state.md b/docs/operations/current_state.md
index fb815d4..bf48ccf 100644
--- a/docs/operations/current_state.md
+++ b/docs/operations/current_state.md
@@ -17,6 +17,7 @@ Role: single live control pointer for the repo.
 - Current phase: `G1 ENGINEERING HARDENED; EXTERNAL EVIDENCE BLOCKED / LIVE NO-GO` — post-interruption verification confirms the safe no-operator seams keep improving, but this is **not** only waiting for a human "yes". The current local evidence is: targeted residual repair group `15 passed, 15 skipped`; broad R3 aggregate `128 passed, 2 skipped`; topology `--scripts` and `--tests` both `ok true`; R3 drift `GREEN=241 YELLOW=0 RED=0` with `r3/drift_reports/2026-04-28.md`; `scripts/live_readiness_check.py --json` still fails closed with `16/17` gates because Q1 Zeus-egress and staged-live-smoke evidence are absent and `live_deploy_authorized=false`; full-repo pytest sample is still red (`--maxfail=30`: 30 failed, 2566 passed, 91 skipped, 16 deselected, 1 xfailed, 1 xpassed). Additional hardening since the second-round review includes CutoverGuard LIVE_ENABLED evidence binding to a 17/17 readiness report, WU transition scripts requiring operator-provided `WU_API_KEY`, settlement rebuild helper registration, and stale fixture compatibility fixes. Remaining no-go blockers: real Q1/staged evidence, G1 close review, explicit `live-money-deploy-go`, full-suite riskguard/harvester/runtime-guard triage, and current-fact data/training evidence for any TIGGE/calibration/live-alpha claim.
 - Freeze note: A2 pre-close completion does not authorize live venue submission/cancel/redeem, CLOB cutover, automatic cancel-unknown unblock in production, live R1 redeem side effects, calibration retrain go-live, external TIGGE archive HTTP/GRIB fetch, production DB mutation outside explicit test/local schema seams, credentialed WS activation, live strategy promotion, or live deployment. Q1-zeus-egress and CLOB v2 cutover go/no-go remain OPEN.
 - Freeze point: live placement remains blocked by Q1/cutover plus heartbeat/collateral/snapshot gates. G1 may implement/readiness-check gate surfaces only; it cannot authorize live deployment, run live smoke, or execute live venue side effects.
+- Temporary remediation packet: `docs/operations/task_2026-04-28_contamination_remediation/` is active evidence for Codex drift cleanup only. It does not authorize live deployment, production DB mutation, CLOB cutover, TIGGE training/data-readiness work, or history_lore remediation.
 
 - Branch: `main`
 - Mainline task: **Post-audit remediation mainline — operations package cleanup closed; P4 mutation blocked**
diff --git a/tests/test_pnl_flow_and_audit.py b/tests/test_pnl_flow_and_audit.py
index 9128e9c..ecdd2f3 100644
--- a/tests/test_pnl_flow_and_audit.py
+++ b/tests/test_pnl_flow_and_audit.py
@@ -1,3 +1,6 @@
+# Created: 2026-04-28
+# Last reused/audited: 2026-04-28
+# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md first-four gate.
 """Cross-module P&L flow, CI-threshold, and hardcoded-audit tests."""
 
 from __future__ import annotations
@@ -58,6 +61,100 @@ def _ensure_auth_verified(conn) -> None:
     conn.commit()
 
 
+def _allow_entry_gates_for_cycle_test(monkeypatch) -> None:
+    """Open only test-local runtime gates needed to exercise entry materialization."""
+    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
+    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
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
+def _insert_source_correct_harvester_obs(
+    conn,
+    *,
+    city: City | None = None,
+    target_date: str = "2026-04-01",
+    high_temp: float = 40.0,
+) -> None:
+    """Seed a source-family-correct observation row for harvester live tests."""
+    city = city or NYC
+    conn.execute(
+        """
+        INSERT INTO observations (
+            city, target_date, source, high_temp, unit, fetched_at, authority
+        ) VALUES (?, ?, ?, ?, ?, ?, ?)
+        """,
+        (
+            city.name,
+            target_date,
+            "wu_icao_history",
+            high_temp,
+            city.settlement_unit,
+            "2026-04-01T23:00:00Z",
+            "VERIFIED",
+        ),
+    )
+
+
+def _enable_live_harvester_test_path(monkeypatch, *, mock_lookup: bool = False) -> None:
+    """Enable flag-gated harvester tests without depending on Batch-A schema parity."""
+    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "1")
+
+    def _assert_source_family_and_skip_schema_write(
+        conn,
+        city,
+        target_date,
+        pm_bin_lo,
+        pm_bin_hi,
+        *,
+        event_slug="",
+        obs_row=None,
+    ):
+        assert obs_row is not None
+        assert obs_row["source"] == "wu_icao_history"
+        return {
+            "authority": "VERIFIED",
+            "settlement_value": obs_row["high_temp"],
+            "winning_bin": f"{int(pm_bin_lo)}-{int(pm_bin_hi)}°{city.settlement_unit}",
+            "reason": None,
+        }
+
+    monkeypatch.setattr(
+        harvester_module,
+        "_write_settlement_truth",
+        _assert_source_family_and_skip_schema_write,
+    )
+    if mock_lookup:
+        monkeypatch.setattr(
+            harvester_module,
+            "_lookup_settlement_obs",
+            lambda conn, city, target_date: {
+                "id": 1,
+                "source": "wu_icao_history",
+                "high_temp": 40.0,
+                "unit": city.settlement_unit,
+                "fetched_at": "2026-04-01T23:00:00Z",
+            },
+        )
+
+
 NYC = City(
     name="NYC",
     lat=40.7772,
@@ -1203,7 +1300,7 @@ def test_inv_control_pause_stops_entries(monkeypatch, tmp_path):
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
     monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{"city": NYC}])
     monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: True)
@@ -3062,11 +3159,12 @@ def test_inv_strategy_tracker_receives_trades(monkeypatch, tmp_path):
             pass
 
     calls: list[dict] = []
+    _allow_entry_gates_for_cycle_test(monkeypatch)
 
     monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
     monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
     monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
-    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
     _market_list = [{
@@ -3075,6 +3173,7 @@ def test_inv_strategy_tracker_receives_trades(monkeypatch, tmp_path):
         "outcomes": [],
         "hours_since_open": 2.0,
         "hours_to_resolution": 30.0,
+        "temperature_metric": "high",
     }]
     monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: _market_list)
     monkeypatch.setattr("src.data.market_scanner.find_weather_markets", lambda **kwargs: _market_list)
@@ -3121,7 +3220,7 @@ def test_inv_strategy_tracker_receives_trades(monkeypatch, tmp_path):
     monkeypatch.setattr(
         cycle_runner,
         "execute_intent",
-        lambda *args, **kwargs: OrderResult(trade_id="trade-1", status="filled", fill_price=0.35, shares=14.29),
+        lambda *args, **kwargs: OrderResult(trade_id="trade-1", status="filled", fill_price=0.35, shares=14.29, command_state="FILLED"),
     )
     monkeypatch.setattr(
         "src.state.strategy_tracker.StrategyTracker.record_entry",
@@ -3192,6 +3291,7 @@ def test_inv_harvester_triggers_refit(monkeypatch, tmp_path):
         edge_source="center_buy",
         settled_at="2026-04-01T23:00:00Z",
     )])
+    _insert_source_correct_harvester_obs(conn)
     conn.commit()
     _ensure_auth_verified(conn)
     conn.close()
@@ -3204,14 +3304,18 @@ def test_inv_harvester_triggers_refit(monkeypatch, tmp_path):
                 "question": "39-40°F",
                 "winningOutcome": "Yes",
                 "clobTokenIds": json.dumps(["yes1", "no1"]),
-                "outcomePrices": json.dumps([1.0, 0.0]),
+                "outcomePrices": json.dumps(["1", "0"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m1",
             },
             {
                 "question": "41-42°F",
                 "winningOutcome": "No",
                 "clobTokenIds": json.dumps(["yes2", "no2"]),
-                "outcomePrices": json.dumps([0.0, 1.0]),
+                "outcomePrices": json.dumps(["0", "1"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m2",
             },
         ],
@@ -3225,10 +3329,11 @@ def test_inv_harvester_triggers_refit(monkeypatch, tmp_path):
         "load_portfolio",
         lambda: PortfolioState(bankroll=150.0, positions=[]),
     )
-    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
     monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
+    _enable_live_harvester_test_path(monkeypatch)
     refit_calls = []
     monkeypatch.setattr(
         harvester_module,
@@ -3262,7 +3367,9 @@ def test_harvester_stage2_preflight_skips_canonical_bootstrap_shape(
                 "question": "39-40°F",
                 "winningOutcome": "Yes",
                 "clobTokenIds": json.dumps(["yes1", "no1"]),
-                "outcomePrices": json.dumps([1.0, 0.0]),
+                "outcomePrices": json.dumps(["1", "0"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m1",
             }
         ],
@@ -3286,7 +3393,7 @@ def test_harvester_stage2_preflight_skips_canonical_bootstrap_shape(
             ],
         ),
     )
-    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
     monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
@@ -3321,6 +3428,7 @@ def test_harvester_stage2_preflight_skips_canonical_bootstrap_shape(
         "store_settlement_records",
         lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("decision_log writer must be skipped")),
     )
+    _enable_live_harvester_test_path(monkeypatch, mock_lookup=True)
 
     with caplog.at_level(logging.ERROR):
         result = harvester_module.run_harvester()
@@ -3343,6 +3451,7 @@ def test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_set
     init_schema(conn)
 
     snapshot_id = _insert_snapshot(conn, "NYC", "2026-04-01", [0.65, 0.35])
+    _insert_source_correct_harvester_obs(conn)
     conn.commit()
     conn.close()
 
@@ -3354,14 +3463,18 @@ def test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_set
                 "question": "39-40°F",
                 "winningOutcome": "Yes",
                 "clobTokenIds": json.dumps(["yes1", "no1"]),
-                "outcomePrices": json.dumps([1.0, 0.0]),
+                "outcomePrices": json.dumps(["1", "0"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m1",
             },
             {
                 "question": "41-42°F",
                 "winningOutcome": "No",
                 "clobTokenIds": json.dumps(["yes2", "no2"]),
-                "outcomePrices": json.dumps([0.0, 1.0]),
+                "outcomePrices": json.dumps(["0", "1"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m2",
             },
         ],
@@ -3383,10 +3496,11 @@ def test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_set
             )],
         ),
     )
-    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
     monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
+    _enable_live_harvester_test_path(monkeypatch)
 
     result = harvester_module.run_harvester()
 
@@ -3444,6 +3558,7 @@ def test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio(m
             settled_at="2026-04-01T23:00:00Z",
         )
     ])
+    _insert_source_correct_harvester_obs(conn)
     conn.commit()  # Fix B: store_settlement_records no longer commits internally.
     conn.close()
 
@@ -3455,14 +3570,18 @@ def test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio(m
                 "question": "39-40°F",
                 "winningOutcome": "Yes",
                 "clobTokenIds": json.dumps(["yes1", "no1"]),
-                "outcomePrices": json.dumps([1.0, 0.0]),
+                "outcomePrices": json.dumps(["1", "0"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m1",
             },
             {
                 "question": "41-42°F",
                 "winningOutcome": "No",
                 "clobTokenIds": json.dumps(["yes2", "no2"]),
-                "outcomePrices": json.dumps([0.0, 1.0]),
+                "outcomePrices": json.dumps(["0", "1"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m2",
             },
         ],
@@ -3484,10 +3603,11 @@ def test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio(m
             )],
         ),
     )
-    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
     monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
+    _enable_live_harvester_test_path(monkeypatch)
 
     result = harvester_module.run_harvester()
 
@@ -3497,7 +3617,7 @@ def test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio(m
     rows = conn.execute(
         """
         SELECT range_label, p_raw
-        FROM calibration_pairs
+        FROM calibration_pairs_v2
         WHERE city = ? AND target_date = ?
         ORDER BY range_label ASC
         """,
@@ -3576,6 +3696,7 @@ def test_inv_harvester_prefers_durable_snapshot_over_open_portfolio(monkeypatch,
                "pnl": 15.0,
                "exit_reason": "SETTLEMENT",
            })))
+    _insert_source_correct_harvester_obs(conn)
     conn.commit()
     conn.close()
 
@@ -3587,14 +3708,18 @@ def test_inv_harvester_prefers_durable_snapshot_over_open_portfolio(monkeypatch,
                 "question": "39-40°F",
                 "winningOutcome": "Yes",
                 "clobTokenIds": json.dumps(["yes1", "no1"]),
-                "outcomePrices": json.dumps([1.0, 0.0]),
+                "outcomePrices": json.dumps(["1", "0"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m1",
             },
             {
                 "question": "41-42°F",
                 "winningOutcome": "No",
                 "clobTokenIds": json.dumps(["yes2", "no2"]),
-                "outcomePrices": json.dumps([0.0, 1.0]),
+                "outcomePrices": json.dumps(["0", "1"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
                 "conditionId": "m2",
             },
         ],
@@ -3616,10 +3741,11 @@ def test_inv_harvester_prefers_durable_snapshot_over_open_portfolio(monkeypatch,
             )],
         ),
     )
-    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
     monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
+    _enable_live_harvester_test_path(monkeypatch)
 
     result = harvester_module.run_harvester()
 
@@ -3629,7 +3755,7 @@ def test_inv_harvester_prefers_durable_snapshot_over_open_portfolio(monkeypatch,
     rows = conn.execute(
         """
         SELECT range_label, p_raw
-        FROM calibration_pairs
+        FROM calibration_pairs_v2
         WHERE city = ? AND target_date = ?
         ORDER BY range_label ASC
         """,
@@ -3719,6 +3845,7 @@ def test_inv_harvester_marks_partial_context_resolution(monkeypatch, tmp_path):
             settled_at="2026-04-01T23:00:00Z",
         ),
     ])
+    _insert_source_correct_harvester_obs(conn)
     conn.commit()
     conn.close()
 
@@ -3726,8 +3853,24 @@ def test_inv_harvester_marks_partial_context_resolution(monkeypatch, tmp_path):
         "title": "Highest temperature in New York City on April 1 2026",
         "slug": "highest-temperature-in-new-york-city-on-april-1-2026",
         "markets": [
-            {"question": "39-40°F", "winningOutcome": "Yes", "clobTokenIds": json.dumps(["yes1", "no1"]), "outcomePrices": json.dumps([1.0, 0.0]), "conditionId": "m1"},
-            {"question": "41-42°F", "winningOutcome": "No", "clobTokenIds": json.dumps(["yes2", "no2"]), "outcomePrices": json.dumps([0.0, 1.0]), "conditionId": "m2"},
+            {
+                "question": "39-40°F",
+                "winningOutcome": "Yes",
+                "clobTokenIds": json.dumps(["yes1", "no1"]),
+                "outcomePrices": json.dumps(["1", "0"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
+                "conditionId": "m1",
+            },
+            {
+                "question": "41-42°F",
+                "winningOutcome": "No",
+                "clobTokenIds": json.dumps(["yes2", "no2"]),
+                "outcomePrices": json.dumps(["0", "1"]),
+                "outcomes": json.dumps(["Yes", "No"]),
+                "umaResolutionStatus": "resolved",
+                "conditionId": "m2",
+            },
         ],
     }
 
@@ -3735,10 +3878,11 @@ def test_inv_harvester_marks_partial_context_resolution(monkeypatch, tmp_path):
     monkeypatch.setattr(harvester_module, "get_trade_connection", lambda: _hconn)
     monkeypatch.setattr(harvester_module, "get_world_connection", lambda: _hconn)
     monkeypatch.setattr(harvester_module, "load_portfolio", lambda: PortfolioState(bankroll=150.0, positions=[]))
-    monkeypatch.setattr(harvester_module, "save_portfolio", lambda state: None)
+    monkeypatch.setattr(harvester_module, "save_portfolio", lambda *args, **kwargs: None)
     monkeypatch.setattr(harvester_module, "get_tracker", lambda: StrategyTracker())
     monkeypatch.setattr(harvester_module, "save_tracker", lambda tracker: None)
     monkeypatch.setattr(harvester_module, "_fetch_settled_events", lambda: [event])
+    _enable_live_harvester_test_path(monkeypatch)
 
     result = harvester_module.run_harvester()
     assert result["pairs_created"] == 2
diff --git a/tests/test_supervisor_contracts.py b/tests/test_supervisor_contracts.py
index 8908a0d..733b5a5 100644
--- a/tests/test_supervisor_contracts.py
+++ b/tests/test_supervisor_contracts.py
@@ -1,3 +1,6 @@
+# Created: 2026-04-28
+# Last reused/audited: 2026-04-28
+# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md first-four gate.
 """Tests for supervisor_api.contracts env enforcement (K1-A4, Bug #28)."""
 
 import pytest
@@ -20,7 +23,7 @@ from src.supervisor_api.contracts import (
     (BeliefMismatch, dict(category="drift", expected="x", observed="y")),
     (Gap, dict(gap_id="G1", title="t", category="semantic", description="d")),
     (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r")),
-    (SupervisorCommand, dict(command="pause_entries", reason="test")),
+    (SupervisorCommand, dict(command="pause_entries", reason="test", timestamp="2026-01-01T00:00:00Z")),
     (ChangeOutcome, dict(change_id="C1", verdict="PENDING")),
     (Antibody, dict(antibody_id="A1", source_gap_id="G1", antibody_type="test", target_surface="s", recurrence_class="r")),
 ])
@@ -31,10 +34,10 @@ def test_empty_env_raises(cls, kwargs):
 
 @pytest.mark.parametrize("cls,kwargs", [
     (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="2026-01-01T00:00:00Z", env="live")),
-    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="paper")),
+    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="unknown_env")),
     (Gap, dict(gap_id="G1", title="t", category="semantic", description="d", env="test")),
     (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r", env="test")),
-    (SupervisorCommand, dict(command="pause_entries", reason="test", env="paper")),
+    (SupervisorCommand, dict(command="pause_entries", reason="test", env="unknown_env", timestamp="2026-01-01T00:00:00Z")),
     (ChangeOutcome, dict(change_id="C1", verdict="PENDING", env="live")),
     (Antibody, dict(antibody_id="A1", source_gap_id="G1", antibody_type="test", target_surface="s", recurrence_class="r", env="test")),
 ])
@@ -47,9 +50,9 @@ def test_nonempty_env_passes(cls, kwargs):
 # B006 relationship tests: env must be one of the Literal enum values
 # ---------------------------------------------------------------------------
 
-@pytest.mark.parametrize("env_value", ["prod", "PROD", "staging", "Live", "  paper  ", "dev"])
+@pytest.mark.parametrize("env_value", ["prod", "PROD", "staging", "Live", "  unknown_env  ", "dev"])
 def test_b006_env_rejects_value_outside_literal(env_value):
-    """env must be exactly one of ("live","paper","test"); any other
+    """env must be exactly one of ("live","test","unknown_env"); any other
     spelling (case, whitespace, unknown envs) must be rejected."""
     with pytest.raises(SupervisorContractError, match="is not one of"):
         Observation(
@@ -61,7 +64,7 @@ def test_b006_env_rejects_value_outside_literal(env_value):
         )
 
 
-@pytest.mark.parametrize("env_value", ["live", "paper", "test"])
+@pytest.mark.parametrize("env_value", ["live", "test", "unknown_env"])
 def test_b006_env_accepts_all_literal_values(env_value):
     o = Observation(
         kind="heartbeat",
@@ -96,7 +99,7 @@ def test_b006_env_reject_message_names_offending_value():
 
 @pytest.mark.parametrize("cls,base_kwargs", [
     (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="t", env="live")),
-    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="paper")),
+    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="unknown_env")),
     (Gap, dict(gap_id="G1", title="t", category="semantic", description="d", env="test")),
     (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r", env="test")),
     (ChangeOutcome, dict(change_id="C1", verdict="PENDING", env="live")),
@@ -113,7 +116,7 @@ def test_b005_provenance_ref_field_exists_on_all_classes(cls, base_kwargs):
 
 @pytest.mark.parametrize("cls,base_kwargs", [
     (Observation, dict(kind="heartbeat", severity="INFO", payload={}, observed_at="t", env="live")),
-    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="paper")),
+    (BeliefMismatch, dict(category="drift", expected="x", observed="y", env="unknown_env")),
     (Gap, dict(gap_id="G1", title="t", category="semantic", description="d", env="test")),
     (Proposal, dict(proposal_id="P1", kind="test", title="t", rationale="r", env="test")),
     (ChangeOutcome, dict(change_id="C1", verdict="PENDING", env="live")),
@@ -148,7 +151,7 @@ def test_b074_unknown_env_is_accepted_by_contract():
 @pytest.mark.parametrize("env_value,expected", [
     ("unknown_env", True),
     ("live", False),
-    ("paper", False),
+    ("unknown_env", True),
     ("test", False),
 ])
 def test_b074_is_unverified_env(env_value, expected):

```

## Untracked packet files included by scope

- docs/operations/task_2026-04-28_contamination_remediation/plan.md
- docs/operations/task_2026-04-28_contamination_remediation/work_log.md
