# Batch C current diff and verification evidence — 2026-04-28

Scope: `tests/test_sigma_floor_evaluation.py`, `architecture/test_topology.yaml`, packet work_log.

Goal: test-only Day0Signal constructor alignment. No production signal/type behavior edits.

## Verification summary

- Red before edit: `2 failed, 5 passed` because Day0Signal requires explicit MetricIdentity.
- After edit: `7 passed` for `tests/test_sigma_floor_evaluation.py`.
- planning-lock: ok true.
- `py_compile`: pass.
- tests topology: global ok false from unrelated existing issues; no `tests/test_sigma_floor_evaluation.py` issue.
- `git diff --check`: pass.
- protected diff check: `src/signal/day0_signal.py` and `src/types/metric_identity.py` empty.

## Current scoped diff

```diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index e9a6340..3b11e14 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -25,7 +25,7 @@ test_trust_policy:
     audit_required: "No lifecycle header or missing required dates"
   enforcement: "topology_doctor test_trust check + digest gate output"
 
-  # Machine-readable registry: 61 tests with valid lifecycle headers.
+  # Machine-readable registry: 117 tests with valid lifecycle headers.
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
@@ -109,14 +110,20 @@ test_trust_policy:
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
diff --git a/tests/test_sigma_floor_evaluation.py b/tests/test_sigma_floor_evaluation.py
index 08675f2..c7648b0 100644
--- a/tests/test_sigma_floor_evaluation.py
+++ b/tests/test_sigma_floor_evaluation.py
@@ -1,3 +1,6 @@
+# Created: 2026-04-07
+# Last reused/audited: 2026-04-28
+# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md Batch C Day0Signal fixture alignment.
 """MATH-004: Sigma Floor Evaluation Tests.
 
 Evaluate whether the quantization noise floor in day0_post_peak_sigma is appropriate.
@@ -18,6 +21,7 @@ from src.signal.forecast_uncertainty import (
     QUANTIZATION_NOISE_FLOOR_C,
 )
 from src.signal.ensemble_signal import sigma_instrument
+from src.types.metric_identity import HIGH_LOCALDAY_MAX
 
 
 class TestSigmaFloorBehavior:
@@ -125,6 +129,7 @@ class TestSigmaFloorCalibration:
                 observation_time=now.isoformat(),
                 current_utc_timestamp=now.isoformat(),
                 daylight_progress=0.5,  # Mid-day
+                temperature_metric=HIGH_LOCALDAY_MAX,
             )
 
             p_vec = sig.p_vector(bins, n_mc=10000)
@@ -182,6 +187,7 @@ class TestSigmaFloorCalibration:
                 observation_source="wu",
                 observation_time=now.isoformat(),
                 current_utc_timestamp=now.isoformat(),
+                temperature_metric=HIGH_LOCALDAY_MAX,
             )
 
             p_vec = sig.p_vector(bins, n_mc=10000)
```
