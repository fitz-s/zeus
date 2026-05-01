# Batch B current diff and verification evidence — 2026-04-28

Scope: `scripts/rebuild_settlements.py`, `tests/test_rebuild_pipeline.py`, `architecture/test_topology.yaml`, packet plan/work_log.

Hard correction encoded: Hong Kong/HKO has no WU ICAO path. HKO accepts only `hko_daily_api`; both `wu_icao_history` and legacy `wu_icao` must skip as `source_family_mismatch`.

## Verification summary

- New source-family tests: `2 passed`.
- Batch B targeted suite: `51 passed, 1 warning`.
- `py_compile`: pass.
- semantic-bootstrap settlement_semantics: ok true.
- planning-lock: ok true.
- scripts topology: global ok false only from unrelated unregistered scripts; no `scripts/rebuild_settlements.py` issue.
- tests topology: global ok false only from unrelated existing missing topology entries; no `tests/test_rebuild_pipeline.py` issue.
- fatal-misreads: ok true.
- core-claims: global ok false from existing locator issues; no Batch B code/schema edit made there.
- `git diff --check`: pass.
- protected diff check: `src/contracts/settlement_semantics.py` and `src/data/rebuild_validators.py` empty.

## Current scoped diff

```diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index e9a6340..a697e0c 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -25,7 +25,7 @@ test_trust_policy:
     audit_required: "No lifecycle header or missing required dates"
   enforcement: "topology_doctor test_trust check + digest gate output"
 
-  # Machine-readable registry: 61 tests with valid lifecycle headers.
+  # Machine-readable registry: 116 tests with valid lifecycle headers.
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
@@ -109,14 +110,19 @@ test_trust_policy:
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
+    tests/test_edge_observation.py: {created: "2026-04-28", last_used: "2026-04-28"}
+    tests/test_supervisor_contracts.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_tick_size.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_vig_treatment_provenance.py: {created: "2026-04-24", last_used: "2026-04-24"}
     tests/test_zpkt.py: {created: "2026-04-25", last_used: "2026-04-25"}
diff --git a/scripts/rebuild_settlements.py b/scripts/rebuild_settlements.py
index 0a34421..c6faa2a 100644
--- a/scripts/rebuild_settlements.py
+++ b/scripts/rebuild_settlements.py
@@ -1,5 +1,5 @@
 # Created: 2026-04-27
-# Last reused/audited: 2026-04-27
+# Last reused/audited: 2026-04-28
 # Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/full_suite_blocker_plan_2026-04-27.md
 """Rebuild high-temperature settlement rows from VERIFIED daily observations.
 
@@ -12,26 +12,86 @@ Callers own transaction boundaries; dry-run is the default for CLI use.
 from __future__ import annotations
 
 import argparse
+import json
 import sqlite3
+from collections import Counter
 from datetime import datetime, timezone
 from pathlib import Path
 from typing import Any
 
+from src.config import cities_by_name
+from src.contracts.exceptions import SettlementPrecisionError
 from src.contracts.settlement_semantics import SettlementSemantics
+from src.data.rebuild_validators import (
+    ImpossibleTemperatureError,
+    UnknownUnitError,
+    validate_observation_for_settlement,
+)
 from src.state.db import get_world_connection
 
 HIGH_PHYSICAL_QUANTITY = "mx2t6_local_calendar_day_max"
 HIGH_OBSERVATION_FIELD = "high_temp"
-HIGH_DATA_VERSION = "wu_icao_history_v1"
+SETTLEMENT_DATA_VERSION_BY_SOURCE_TYPE = {
+    "wu_icao": "wu_icao_history_v1",
+    "hko": "hko_daily_api_v1",
+    "noaa": "ogimet_metar_v1",
+    "cwa_station": "cwa_no_collector_v0",
+}
 
 
-def _round_high_value(raw_value: float, unit: str, city: str) -> float:
-    sem = (
-        SettlementSemantics.default_wu_celsius(city)
-        if str(unit).upper() == "C"
-        else SettlementSemantics.default_wu_fahrenheit(city)
+class SettlementRebuildSkip(ValueError):
+    """Expected row-level skip for rebuild_settlements."""
+
+    def __init__(self, reason: str):
+        self.reason = reason
+        super().__init__(reason)
+
+
+def _city_for_observation(row: sqlite3.Row):
+    city_name = str(row["city"])
+    city = cities_by_name.get(city_name)
+    if city is None:
+        raise SettlementRebuildSkip("unknown_city")
+    return city
+
+
+def _validate_source_family(row: sqlite3.Row, city) -> None:
+    source = str(row["source"] or "")
+    source_type = city.settlement_source_type
+
+    if source_type == "wu_icao":
+        # Legacy fixture alias `wu_icao` is WU-family only. It must never
+        # leak into HKO/Hong Kong or other source families.
+        if source in {"wu_icao_history", "wu_icao"}:
+            return
+        raise SettlementRebuildSkip("source_family_mismatch")
+
+    if source_type == "hko":
+        if source == "hko_daily_api":
+            return
+        raise SettlementRebuildSkip("source_family_mismatch")
+
+    if source_type == "noaa":
+        if source.startswith("ogimet_metar_"):
+            return
+        raise SettlementRebuildSkip("source_family_mismatch")
+
+    if source_type == "cwa_station":
+        raise SettlementRebuildSkip("unsupported_source_family")
+
+    raise SettlementRebuildSkip("unsupported_source_family")
+
+
+def _round_high_value(row: sqlite3.Row, conn: sqlite3.Connection) -> tuple[float, Any]:
+    city = _city_for_observation(row)
+    _validate_source_family(row, city)
+    converted_value = validate_observation_for_settlement(dict(row), city, conn)
+    sem = SettlementSemantics.for_city(city)
+    settlement_value = sem.assert_settlement_value(
+        converted_value,
+        context=f"rebuild_settlements/{city.name}/{row['target_date']}",
     )
-    return sem.assert_settlement_value(raw_value, context="rebuild_settlements")
+    return settlement_value, city
 
 
 def rebuild_settlements(
@@ -83,27 +143,46 @@ def rebuild_settlements(
 
     rows_written = 0
     rows_skipped = 0
+    rows_skipped_by_reason: Counter[str] = Counter()
     now = datetime.now(timezone.utc).isoformat()
     for row in rows:
         try:
-            settlement_value = _round_high_value(
-                float(row["high_temp"]), str(row["unit"]), str(row["city"])
-            )
-        except Exception:
+            settlement_value, city = _round_high_value(row, conn)
+        except SettlementRebuildSkip as exc:
+            rows_skipped += 1
+            rows_skipped_by_reason[exc.reason] += 1
+            continue
+        except (ImpossibleTemperatureError, UnknownUnitError, SettlementPrecisionError):
             rows_skipped += 1
+            rows_skipped_by_reason["invalid_observation"] += 1
             continue
 
         if dry_run:
             rows_written += 1
             continue
 
+        data_version = SETTLEMENT_DATA_VERSION_BY_SOURCE_TYPE.get(
+            city.settlement_source_type,
+            "unknown_v0",
+        )
+        provenance_json = json.dumps(
+            {
+                "source": "scripts/rebuild_settlements.py",
+                "authority": "VERIFIED",
+                "obs_source": row["source"],
+                "settlement_source_type": city.settlement_source_type,
+                "data_version": data_version,
+            },
+            sort_keys=True,
+        )
+
         conn.execute(
             """
             INSERT INTO settlements
             (city, target_date, winning_bin, settlement_value, settlement_source, settled_at,
              authority, temperature_metric, physical_quantity, observation_field,
-             data_version, provenance_json)
-            VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', 'high', ?, ?, ?, ?)
+             data_version, provenance_json, unit, settlement_source_type)
+            VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', 'high', ?, ?, ?, ?, ?, ?)
             ON CONFLICT(city, target_date, temperature_metric) DO UPDATE SET
                 winning_bin = excluded.winning_bin,
                 settlement_value = excluded.settlement_value,
@@ -113,19 +192,23 @@ def rebuild_settlements(
                 physical_quantity = excluded.physical_quantity,
                 observation_field = excluded.observation_field,
                 data_version = excluded.data_version,
-                provenance_json = excluded.provenance_json
+                provenance_json = excluded.provenance_json,
+                unit = excluded.unit,
+                settlement_source_type = excluded.settlement_source_type
             """,
             (
                 row["city"],
                 row["target_date"],
-                f"{int(settlement_value)}°{str(row['unit']).upper()}",
+                f"{int(settlement_value)}°{city.settlement_unit}",
                 settlement_value,
                 row["source"] or "verified_observation_rebuild",
                 now,
                 HIGH_PHYSICAL_QUANTITY,
                 HIGH_OBSERVATION_FIELD,
-                HIGH_DATA_VERSION,
-                '{"source":"scripts/rebuild_settlements.py","authority":"VERIFIED"}',
+                data_version,
+                provenance_json,
+                city.settlement_unit,
+                city.settlement_source_type,
             ),
         )
         rows_written += 1
@@ -136,6 +219,7 @@ def rebuild_settlements(
         "rows_seen": len(rows),
         "rows_written": rows_written,
         "rows_skipped": rows_skipped,
+        "rows_skipped_by_reason": dict(rows_skipped_by_reason),
         "unverified_ignored": unverified_ignored,
     }
 
diff --git a/tests/test_rebuild_pipeline.py b/tests/test_rebuild_pipeline.py
index fdda2f0..43a4271 100644
--- a/tests/test_rebuild_pipeline.py
+++ b/tests/test_rebuild_pipeline.py
@@ -1,3 +1,6 @@
+# Created: 2026-04-12
+# Last reused/audited: 2026-04-28
+# Authority basis: docs/operations/task_2026-04-28_contamination_remediation/plan.md Batch B source-family rebuild gate.
 """K4 rebuild pipeline end-to-end test.
 
 Exercises the full rebuild pipeline on a synthetic fixture:
@@ -600,3 +603,85 @@ def test_rebuild_multi_city_one_unknown_unit_one_valid(tmp_path):
     assert summary["rows_skipped"] == 1, (
         f"Expected 1 skipped (unknown unit), got {summary['rows_skipped']}"
     )
+
+
+def test_rebuild_settlements_writes_source_family_data_versions(tmp_path):
+    """HKO and NOAA-proxy rows keep their city source-family provenance."""
+    conn, db_path = _make_tmp_db(tmp_path)
+
+    conn.execute(
+        "INSERT INTO observations "
+        "(city, target_date, source, high_temp, low_temp, unit, authority) "
+        "VALUES ('Hong Kong', '2025-07-01', 'hko_daily_api', 28.8, 24.0, 'C', 'VERIFIED')"
+    )
+    conn.execute(
+        "INSERT INTO observations "
+        "(city, target_date, source, high_temp, low_temp, unit, authority) "
+        "VALUES ('Istanbul', '2025-07-01', 'ogimet_metar_v1', 29.4, 20.0, 'C', 'VERIFIED')"
+    )
+    conn.commit()
+    conn.close()
+
+    from scripts.rebuild_settlements import rebuild_settlements
+
+    conn2 = sqlite3.connect(str(db_path))
+    conn2.row_factory = sqlite3.Row
+    summary = rebuild_settlements(conn2, dry_run=False)
+    conn2.commit()
+
+    rows = conn2.execute(
+        """
+        SELECT city, settlement_value, winning_bin, data_version,
+               settlement_source_type, unit
+        FROM settlements
+        WHERE city IN ('Hong Kong', 'Istanbul')
+        ORDER BY city
+        """
+    ).fetchall()
+    conn2.close()
+
+    assert summary["rows_written"] == 2
+    assert summary["rows_skipped"] == 0
+    by_city = {row["city"]: dict(row) for row in rows}
+    assert by_city["Hong Kong"]["settlement_value"] == 28
+    assert by_city["Hong Kong"]["winning_bin"] == "28°C"
+    assert by_city["Hong Kong"]["data_version"] == "hko_daily_api_v1"
+    assert by_city["Hong Kong"]["settlement_source_type"] == "hko"
+    assert by_city["Hong Kong"]["unit"] == "C"
+    assert by_city["Istanbul"]["settlement_value"] == 29
+    assert by_city["Istanbul"]["winning_bin"] == "29°C"
+    assert by_city["Istanbul"]["data_version"] == "ogimet_metar_v1"
+    assert by_city["Istanbul"]["settlement_source_type"] == "noaa"
+    assert by_city["Istanbul"]["unit"] == "C"
+
+
+def test_rebuild_settlements_skips_hong_kong_wu_source_aliases(tmp_path):
+    """Hong Kong/HKO has no WU ICAO path; both WU aliases must fail closed."""
+    conn, db_path = _make_tmp_db(tmp_path)
+    for source in ("wu_icao_history", "wu_icao"):
+        conn.execute(
+            "INSERT INTO observations "
+            "(city, target_date, source, high_temp, low_temp, unit, authority) "
+            "VALUES ('Hong Kong', '2025-07-01', ?, 28.8, 24.0, 'C', 'VERIFIED')",
+            (source,),
+        )
+    conn.commit()
+    conn.close()
+
+    from scripts.rebuild_settlements import rebuild_settlements
+
+    conn2 = sqlite3.connect(str(db_path))
+    conn2.row_factory = sqlite3.Row
+    summary = rebuild_settlements(conn2, dry_run=False, city_filter="Hong Kong")
+    conn2.commit()
+
+    settlements = conn2.execute(
+        "SELECT COUNT(*) FROM settlements WHERE city='Hong Kong'"
+    ).fetchone()[0]
+    conn2.close()
+
+    assert settlements == 0
+    assert summary["rows_seen"] == 2
+    assert summary["rows_written"] == 0
+    assert summary["rows_skipped"] == 2
+    assert summary["rows_skipped_by_reason"] == {"source_family_mismatch": 2}
```
