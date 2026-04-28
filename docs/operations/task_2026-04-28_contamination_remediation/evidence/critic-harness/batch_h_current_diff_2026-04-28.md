# Batch H Current Diff Evidence — 2026-04-28

## Scope

Batch H fixes the exit-lifecycle canonical dual-write backfill bug for legacy positions that have non-entry canonical history (for example `DAY0_WINDOW_ENTERED`) but lack canonical entry events. It edits only `src/execution/exit_lifecycle.py`, `tests/test_runtime_guards.py`, and packet evidence/docs.

Non-goals: no settlement/bin topology changes, no supervisor env grammar changes, no source routing/current-fact rewrites, no TIGGE/data-readiness/history-lore work, no production DB/state artifacts, no live/credentialed side effects, and no Hong Kong WU ICAO/alias assumptions.

## Tests-first failure captured before source fix

Immediately after adding the two Batch H regressions and before editing `src/execution/exit_lifecycle.py`, the targeted run failed as expected: Day0-only canonical history produced only `[1, 2]` instead of `[1, 2, 3, 4, 5]`, and partial-entry history produced only `[1, 2, 3, 4]` instead of `[1, 2, 3, 4, 5]`. This locked the bug before source edits.

## Verification commands and outputs

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py::test_exit_dual_write_backfills_missing_entry_history_after_day0_only_canonical_event tests/test_runtime_guards.py::test_exit_dual_write_backfills_only_missing_entry_events_for_partial_history --no-header
..                                                                       [100%]
2 passed in 1.21s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py::test_monitoring_phase_persists_live_exit_telemetry_chain_with_canonical_entry_baseline tests/test_runtime_guards.py::test_exit_dual_write_backfills_missing_entry_history_after_day0_only_canonical_event tests/test_runtime_guards.py::test_exit_dual_write_backfills_only_missing_entry_events_for_partial_history --no-header
...                                                                      [100%]
3 passed in 1.06s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
........................................................................ [ 59%]
.................................................                        [100%]
121 passed in 3.70s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py --no-header
........................................................................ [ 49%]
........................................................................ [ 98%]
..                                                                       [100%]
146 passed in 3.57s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decision_evidence_entry_emission.py tests/test_exit_evidence_audit.py --no-header
.......................                                                  [100%]
23 passed in 0.07s

# exit=0
```

```bash
$ python3 -m py_compile src/execution/exit_lifecycle.py tests/test_runtime_guards.py

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --navigation --task 'Batch H legacy Day0-only canonical history entry backfill remediation' --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
navigation ok: True
profile: batch h legacy day0 canonical history backfill remediation
repo_health_warnings: 49 (34 error, 15 warning) [unrelated to this task; rerun with --issues-scope all to inspect]
excluded_lanes:
- strict: strict includes transient root/state artifact classification; run explicitly when workspace is quiescent
- scripts: script manifest can be blocked by active package scripts; run explicitly for script work
- planning_lock: requires caller-supplied --changed-files and optional --plan-evidence

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
{
  "ok": true,
  "issues": []
}

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py semantic-bootstrap --task-class day0_monitoring --task 'Batch H legacy Day0-only canonical history entry backfill remediation' --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py --json | python3 - <<'PY'  # summarized
{
  "current_fact_surfaces": [
    {
      "age_days": 7,
      "freshness_status": "fresh",
      "last_audited": "2026-04-21",
      "max_staleness_days": 14,
      "path": "docs/operations/current_source_validity.md",
      "present": true,
      "warnings": []
    },
    {
      "age_days": 5,
      "freshness_status": "fresh",
      "last_audited": "2026-04-23",
      "max_staleness_days": 14,
      "path": "docs/operations/current_data_state.md",
      "present": true,
      "warnings": []
    }
  ],
  "exit": 0,
  "graph_usage_availability": "unavailable_or_stale",
  "issues": [],
  "ok": true,
  "proof_questions": [
    "day0_source_vs_settlement_source",
    "high_low_day0_causality"
  ],
  "task_class": "day0_monitoring"
}

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --tests --json | python3 - <<'PY'  # filtered for tests/test_runtime_guards.py
topology_doctor --tests exit=1
global_issue_count=4
tests/test_runtime_guards.py issue_count=0

# exit=0
```

```bash
$ git diff --check -- src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md

# exit=0
```

```bash
$ git diff -- src/engine/lifecycle_events.py src/state/ledger.py src/engine/cycle_runtime.py src/supervisor_api/contracts.py src/contracts/settlement_semantics.py | wc -c
       0

# exit=0
```

## Current diff — Batch H source/test surfaces

```diff
diff --git a/src/execution/exit_lifecycle.py b/src/execution/exit_lifecycle.py
index b37dfd5..9456fbf 100644
--- a/src/execution/exit_lifecycle.py
+++ b/src/execution/exit_lifecycle.py
@@ -207,15 +207,36 @@ def _next_canonical_sequence_no(conn: sqlite3.Connection, position_id: str) -> i
     return int(row[0] or 0) + 1


-def _has_canonical_position_history(conn: sqlite3.Connection, position_id: str) -> bool:
+_CANONICAL_ENTRY_EVENT_TYPES = (
+    "POSITION_OPEN_INTENT",
+    "ENTRY_ORDER_POSTED",
+    "ENTRY_ORDER_FILLED",
+)
+
+
+def _existing_canonical_entry_event_types(conn: sqlite3.Connection, position_id: str) -> set[str]:
     try:
-        row = conn.execute(
-            "SELECT 1 FROM position_events WHERE position_id = ? LIMIT 1",
+        rows = conn.execute(
+            """
+            SELECT event_type
+            FROM position_events
+            WHERE position_id = ?
+              AND event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED', 'ENTRY_ORDER_FILLED')
+            """,
             (position_id,),
-        ).fetchone()
+        ).fetchall()
     except sqlite3.OperationalError:
-        return False
-    return row is not None
+        return set()
+    return {str(row[0]) for row in rows}
+
+
+def _append_sequence_numbers(events: list[dict], *, start_sequence_no: int) -> list[dict]:
+    resequenced: list[dict] = []
+    for offset, event in enumerate(events):
+        updated = dict(event)
+        updated["sequence_no"] = start_sequence_no + offset
+        resequenced.append(updated)
+    return resequenced


 def _canonical_phase_before_for_economic_close(position: Position) -> str:
@@ -237,13 +258,24 @@ def _dual_write_canonical_economic_close_if_available(
     from src.state.db import append_many_and_project

     trade_id = getattr(position, "trade_id", "")
-    has_history = _has_canonical_position_history(conn, trade_id)
-
-    if not has_history:
-        # Backfill canonical entry events for positions that only exist in
-        # the legacy table.  Create an entry-phase snapshot so
-        # build_entry_canonical_write produces the standard three-event
-        # sequence (OPEN_INTENT / ORDER_POSTED / ORDER_FILLED → active).
+    existing_entry_types = _existing_canonical_entry_event_types(conn, trade_id)
+    missing_entry_types = [
+        event_type
+        for event_type in _CANONICAL_ENTRY_EVENT_TYPES
+        if event_type not in existing_entry_types
+    ]
+
+    next_sequence_no = _next_canonical_sequence_no(conn, trade_id)
+
+    if missing_entry_types:
+        # Backfill missing canonical entry events for positions that predate
+        # full canonical entry history. Existing canonical events are
+        # append-only history: even a DAY0_WINDOW_ENTERED row must not suppress
+        # entry backfill, and no existing row may be renumbered or mutated.
+        # Create an entry-phase snapshot so build_entry_canonical_write
+        # produces the standard sequence (OPEN_INTENT / ORDER_POSTED /
+        # ORDER_FILLED → active), filter to only missing event types, then
+        # resequence the filtered events after the current max sequence.
         #
         # T4.1b 2026-04-23 (D4 Option E): these legacy positions have no
         # captured `DecisionEvidence` (the decision frame predates the
@@ -257,7 +289,7 @@ def _dual_write_canonical_economic_close_if_available(
         entry_snapshot.state = "entered"
         entry_snapshot.exit_state = ""
         try:
-            entry_events, _ = build_entry_canonical_write(
+            generated_entry_events, _ = build_entry_canonical_write(
                 entry_snapshot,
                 source_module="src.execution.exit_lifecycle:backfill",
                 decision_evidence_reason="backfill_legacy_position",
@@ -267,10 +299,19 @@ def _dual_write_canonical_economic_close_if_available(
                 "Canonical entry backfill failed for %s: %s", trade_id, exc,
             )
             return False
-        exit_seq = len(entry_events) + 1
+        entry_events = [
+            event
+            for event in generated_entry_events
+            if event.get("event_type") in missing_entry_types
+        ]
+        entry_events = _append_sequence_numbers(
+            entry_events,
+            start_sequence_no=next_sequence_no,
+        )
+        exit_seq = next_sequence_no + len(entry_events)
     else:
         entry_events = []
-        exit_seq = _next_canonical_sequence_no(conn, trade_id)
+        exit_seq = next_sequence_no

     try:
         exit_events, projection = build_economic_close_canonical_write(
diff --git a/tests/test_runtime_guards.py b/tests/test_runtime_guards.py
index 6ac3e74..cbf52a8 100644
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
@@ -4563,6 +4685,184 @@ def test_monitoring_phase_persists_live_exit_telemetry_chain(monkeypatch, tmp_pa
     conn.close()


+def _raw_position_event_rows(conn, position_id):
+    cursor = conn.execute(
+        """
+        SELECT event_id, sequence_no, event_type, source_module, idempotency_key, payload_json
+        FROM position_events
+        WHERE position_id = ?
+        ORDER BY sequence_no ASC
+        """,
+        (position_id,),
+    )
+    columns = [column[0] for column in cursor.description]
+    return [dict(zip(columns, row)) for row in cursor.fetchall()]
+
+
+def test_exit_dual_write_backfills_missing_entry_history_after_day0_only_canonical_event(tmp_path):
+    """Legacy Day0-only canonical history must receive append-only entry backfill.
+
+    Batch H regression: the existing DAY0_WINDOW_ENTERED row is history and must
+    not be mutated or renumbered, but it also must not suppress missing legacy
+    entry events before EXIT_ORDER_FILLED is appended.
+    """
+    conn = get_connection(tmp_path / "zeus.db")
+    init_schema(conn)
+
+    from src.engine.lifecycle_events import build_day0_window_entered_canonical_write
+    from src.state.db import append_many_and_project
+
+    position_id = "legacy-day0-only"
+    day0_position = _position(
+        trade_id=position_id,
+        state="day0_window",
+        order_id="entry-order-1",
+        entered_at="2026-03-30T00:00:00Z",
+        order_posted_at="2026-03-29T23:59:00Z",
+        day0_entered_at="2026-04-01T00:00:00Z",
+        decision_snapshot_id="snap-legacy-day0",
+    )
+    day0_events, day0_projection = build_day0_window_entered_canonical_write(
+        day0_position,
+        day0_entered_at=day0_position.day0_entered_at,
+        sequence_no=1,
+        previous_phase="active",
+        source_module="tests/test_runtime_guards:seed_day0_only",
+    )
+    append_many_and_project(conn, day0_events, day0_projection)
+    before_day0 = _raw_position_event_rows(conn, position_id)[0]
+
+    closed = _position(
+        trade_id=position_id,
+        state="economically_closed",
+        exit_state="sell_filled",
+        pre_exit_state="day0_window",
+        order_id="entry-order-1",
+        last_exit_order_id="sell-order-1",
+        entered_at="2026-03-30T00:00:00Z",
+        order_posted_at="2026-03-29T23:59:00Z",
+        day0_entered_at="2026-04-01T00:00:00Z",
+        last_exit_at="2026-04-01T01:00:00Z",
+        exit_price=0.46,
+        exit_reason="forward edge failed",
+        decision_snapshot_id="snap-legacy-day0",
+    )
+
+    assert exit_lifecycle_module._dual_write_canonical_economic_close_if_available(
+        conn,
+        closed,
+        phase_before="pending_exit",
+    ) is True
+
+    events = _raw_position_event_rows(conn, position_id)
+    assert events[0] == before_day0
+    assert [event["sequence_no"] for event in events] == [1, 2, 3, 4, 5]
+    assert [event["event_type"] for event in events] == [
+        "DAY0_WINDOW_ENTERED",
+        "POSITION_OPEN_INTENT",
+        "ENTRY_ORDER_POSTED",
+        "ENTRY_ORDER_FILLED",
+        "EXIT_ORDER_FILLED",
+    ]
+    assert len({event["event_id"] for event in events}) == len(events)
+    assert len({event["idempotency_key"] for event in events}) == len(events)
+
+    posted_payload = json.loads(events[2]["payload_json"])
+    assert posted_payload["decision_evidence_reason"] == "backfill_legacy_position"
+    assert events[1]["source_module"] == "src.execution.exit_lifecycle:backfill"
+    assert events[2]["source_module"] == "src.execution.exit_lifecycle:backfill"
+    assert events[3]["source_module"] == "src.execution.exit_lifecycle:backfill"
+    assert events[4]["source_module"] == "src.execution.exit_lifecycle"
+
+
+def test_exit_dual_write_backfills_only_missing_entry_events_for_partial_history(tmp_path):
+    """Partial canonical entry history must not be duplicated during backfill."""
+    conn = get_connection(tmp_path / "zeus.db")
+    init_schema(conn)
+
+    from src.engine.lifecycle_events import (
+        build_day0_window_entered_canonical_write,
+        build_entry_canonical_write,
+    )
+    from src.state.db import append_many_and_project
+
+    position_id = "legacy-partial-entry"
+    pending_entry = _position(
+        trade_id=position_id,
+        state="pending_tracked",
+        order_id="entry-order-1",
+        order_posted_at="2026-03-29T23:59:00Z",
+        entered_at="",
+        day0_entered_at="",
+        decision_snapshot_id="snap-partial-entry",
+    )
+    entry_events, entry_projection = build_entry_canonical_write(
+        pending_entry,
+        source_module="tests/test_runtime_guards:partial_entry_seed",
+        decision_evidence_reason="already_seeded_partial",
+    )
+    append_many_and_project(conn, entry_events, entry_projection)
+
+    day0_position = _position(
+        trade_id=position_id,
+        state="day0_window",
+        order_id="entry-order-1",
+        entered_at="2026-03-30T00:00:00Z",
+        order_posted_at="2026-03-29T23:59:00Z",
+        day0_entered_at="2026-04-01T00:00:00Z",
+        decision_snapshot_id="snap-partial-entry",
+    )
+    day0_events, day0_projection = build_day0_window_entered_canonical_write(
+        day0_position,
+        day0_entered_at=day0_position.day0_entered_at,
+        sequence_no=3,
+        previous_phase="active",
+        source_module="tests/test_runtime_guards:partial_entry_day0",
+    )
+    append_many_and_project(conn, day0_events, day0_projection)
+    before_events = _raw_position_event_rows(conn, position_id)
+
+    closed = _position(
+        trade_id=position_id,
+        state="economically_closed",
+        exit_state="sell_filled",
+        pre_exit_state="day0_window",
+        order_id="entry-order-1",
+        last_exit_order_id="sell-order-1",
+        entered_at="2026-03-30T00:00:00Z",
+        order_posted_at="2026-03-29T23:59:00Z",
+        day0_entered_at="2026-04-01T00:00:00Z",
+        last_exit_at="2026-04-01T01:00:00Z",
+        exit_price=0.46,
+        exit_reason="forward edge failed",
+        decision_snapshot_id="snap-partial-entry",
+    )
+
+    assert exit_lifecycle_module._dual_write_canonical_economic_close_if_available(
+        conn,
+        closed,
+        phase_before="pending_exit",
+    ) is True
+
+    events = _raw_position_event_rows(conn, position_id)
+    assert events[:3] == before_events
+    assert [event["sequence_no"] for event in events] == [1, 2, 3, 4, 5]
+    assert [event["event_type"] for event in events] == [
+        "POSITION_OPEN_INTENT",
+        "ENTRY_ORDER_POSTED",
+        "DAY0_WINDOW_ENTERED",
+        "ENTRY_ORDER_FILLED",
+        "EXIT_ORDER_FILLED",
+    ]
+    assert [event["event_type"] for event in events].count("POSITION_OPEN_INTENT") == 1
+    assert [event["event_type"] for event in events].count("ENTRY_ORDER_POSTED") == 1
+    assert [event["event_type"] for event in events].count("ENTRY_ORDER_FILLED") == 1
+    assert len({event["event_id"] for event in events}) == len(events)
+    assert len({event["idempotency_key"] for event in events}) == len(events)
+    assert events[3]["source_module"] == "src.execution.exit_lifecycle:backfill"
+    assert events[4]["source_module"] == "src.execution.exit_lifecycle"
+
+
 def test_monitoring_skips_economically_closed_positions(monkeypatch):
     pos = _position(
         trade_id="econ-close-1",
@@ -4667,7 +4967,11 @@ def test_quarantine_expired_positions_do_not_count_as_open_exposure():


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
@@ -4695,7 +4999,15 @@ def test_materialize_position_carries_semantic_snapshot_jsons():
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
@@ -4792,6 +5104,7 @@ def test_execute_exit_routes_live_sell_through_executor_exit_path(monkeypatch):
             venue_status="OPEN",
         )

+    monkeypatch.setattr(exit_lifecycle_module, "check_sell_collateral", lambda *args, **kwargs: (True, None))
     monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit_order", _execute_exit_order)

     outcome = exit_lifecycle_module.execute_exit(
@@ -4831,6 +5144,7 @@ def test_execute_exit_rejected_orderresult_preserves_retry_semantics(monkeypatch
         def get_balance(self):
             return 100.0

+    monkeypatch.setattr(exit_lifecycle_module, "check_sell_collateral", lambda *args, **kwargs: (True, None))
     monkeypatch.setattr(
         "src.execution.exit_lifecycle.execute_exit_order",
         lambda intent: OrderResult(
@@ -5017,6 +5331,7 @@ def test_discovery_phase_records_rate_limited_decision_as_availability_fact(tmp_
         "outcomes": [],
         "hours_since_open": 1.0,
         "hours_to_resolution": 4.0,
+        "temperature_metric": "high",
         "event_id": "evt-rate",
         "slug": "slug-rate",
     }
@@ -5067,7 +5382,7 @@ def test_discovery_phase_records_rate_limited_decision_as_availability_fact(tmp_
         summary=summary,
         entry_bankroll=100.0,
         decision_time=datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
-        env="paper",
+        env="test",
         deps=deps,
     )

```

## Packet docs/evidence status note

```bash
$ git status --short -- <Batch H packet docs/evidence>
?? docs/operations/task_2026-04-28_contamination_remediation/plan.md
?? docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

## Notes

- Batch H intentionally appends missing entry events after existing canonical history to preserve append-only law; `occurred_at`, source_module, and the legacy reason sentinel make the historical backfill explicit.
- `ENTRY_ORDER_PLACED` was not introduced; the implementation uses the real canonical event names `POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, and `ENTRY_ORDER_FILLED`.
- Broader worktree still contains unrelated dirty files from previous batches/co-tenants; this evidence covers Batch H touched surfaces only.

## 2026-04-28 Batch H post-edit critic erratum and re-verification

Post-edit verifier passed the Batch H implementation. Post-edit critic requested changes because the H0b topology profile law (not runtime source) still named invented `ENTRY_ORDER_PLACED`. This evidence now records the correction and Batch H re-verification.

Erratum applied:

- `architecture/topology.yaml` Batch H profile law now names real entry events only: `POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, `ENTRY_ORDER_FILLED`.
- `architecture/digest_profiles.py` regenerated from YAML.
- `tests/test_digest_profile_matching.py` now prevents reintroducing `ENTRY_ORDER_PLACED` in the Batch H profile law.

Batch H implementation re-verification after erratum:

```text
Batch H regressions
=> 2 passed

canonical-entry baseline + Batch H regressions
=> 3 passed

pytest tests/test_runtime_guards.py
=> 121 passed

pytest tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py
=> 146 passed

pytest tests/test_decision_evidence_entry_emission.py tests/test_exit_evidence_audit.py
=> 23 passed

python3 -m py_compile src/execution/exit_lifecycle.py tests/test_runtime_guards.py
=> passed

semantic-bootstrap day0_monitoring
=> ok true; current source/data fact surfaces fresh; Code Review Graph stale/derived-only

filtered topology_doctor --tests --json
=> command exit status 1 from unrelated global issues; global_issue_count 5; runtime_guard_issues []

git diff --check over Batch H/H0b changed surfaces
=> passed

protected downstream/source diff byte count for src/engine/lifecycle_events.py src/state/ledger.py src/engine/cycle_runtime.py src/supervisor_api/contracts.py src/contracts/settlement_semantics.py src/state/projection.py
=> 0

HK/WU grep over touched code/tests/topology
=> only standing stop-condition/source-semantics wording in topology/mirror; no WU alias/source assertion. Hong Kong still has no WU ICAO.
```

Batch H remains gated pending context-complete critic/verifier re-review after this erratum.
