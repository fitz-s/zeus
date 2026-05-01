# Batch A current tracked diff — 2026-04-28

Generated after Batch A test-first schema parity implementation; excludes unrelated untracked co-tenant files.

```diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index e9a6340..16f2350 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -25,7 +25,7 @@ test_trust_policy:
     audit_required: "No lifecycle header or missing required dates"
   enforcement: "topology_doctor test_trust check + digest gate output"
 
-  # Machine-readable registry: 61 tests with valid lifecycle headers.
+  # Machine-readable registry: 114 tests with valid lifecycle headers.
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
@@ -109,6 +110,7 @@ test_trust_policy:
     tests/test_decision_evidence_runtime_invocation.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_exit_evidence_audit.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_harvester_dr33_live_enablement.py: {created: "2026-04-23", last_used: "2026-04-23"}
+    tests/test_harvester_metric_identity.py: {created: "2026-04-24", last_used: "2026-04-28"}
     tests/test_hold_value_exit_costs.py: {created: "2026-04-24", last_used: "2026-04-24"}
     tests/test_neg_risk_passthrough.py: {created: "2026-04-23", last_used: "2026-04-27"}
     tests/test_parse_canonical_bin_label.py: {created: "2026-04-23", last_used: "2026-04-23"}
@@ -116,7 +118,9 @@ test_trust_policy:
     tests/test_replay_time_provenance.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_settlements_authority_trigger.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_settlements_verified_row_integrity.py: {created: "2026-04-23", last_used: "2026-04-23"}
+    tests/test_settlements_unique_migration.py: {created: "2026-04-24", last_used: "2026-04-28"}
     tests/test_settlement_semantics.py: {created: "2026-04-27", last_used: "2026-04-27"}
+    tests/test_supervisor_contracts.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_tick_size.py: {created: "2026-04-23", last_used: "2026-04-23"}
     tests/test_vig_treatment_provenance.py: {created: "2026-04-24", last_used: "2026-04-24"}
     tests/test_zpkt.py: {created: "2026-04-25", last_used: "2026-04-25"}
diff --git a/src/state/db.py b/src/state/db.py
index d0af698..12fbb7f 100644
--- a/src/state/db.py
+++ b/src/state/db.py
@@ -371,6 +371,10 @@ def init_schema(conn: Optional[sqlite3.Connection] = None) -> None:
             settlement_source TEXT,
             settled_at TEXT,
             authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
+            pm_bin_lo REAL,
+            pm_bin_hi REAL,
+            unit TEXT,
+            settlement_source_type TEXT,
             -- REOPEN-2 inline: INV-14 identity spine is part of the fresh-DB
             -- schema so UNIQUE(city, target_date, temperature_metric) can
             -- reference temperature_metric without a second migration pass.
@@ -1301,6 +1305,10 @@ def init_schema(conn: Optional[sqlite3.Connection] = None) -> None:
     # All columns are nullable (pre-P-E rows may carry NULL); NOT-NULL enforcement is
     # deferred to P-E DELETE+INSERT reconstruction writers.
     for ddl in [
+        "ALTER TABLE settlements ADD COLUMN pm_bin_lo REAL;",
+        "ALTER TABLE settlements ADD COLUMN pm_bin_hi REAL;",
+        "ALTER TABLE settlements ADD COLUMN unit TEXT;",
+        "ALTER TABLE settlements ADD COLUMN settlement_source_type TEXT;",
         "ALTER TABLE settlements ADD COLUMN temperature_metric TEXT "
         "CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low'));",
         "ALTER TABLE settlements ADD COLUMN physical_quantity TEXT;",
diff --git a/tests/test_harvester_metric_identity.py b/tests/test_harvester_metric_identity.py
index 4c6168e..69e59e8 100644
--- a/tests/test_harvester_metric_identity.py
+++ b/tests/test_harvester_metric_identity.py
@@ -1,4 +1,4 @@
-# Lifecycle: created=2026-04-24; last_reviewed=2026-04-24; last_reused=never
+# Lifecycle: created=2026-04-24; last_reviewed=2026-04-28; last_reused=2026-04-28
 # Purpose: INV-14 identity spine antibody for harvester settlement writes —
 #          pins temperature_metric / physical_quantity / observation_field to
 #          canonical HIGH_LOCALDAY_MAX.* so regression to the legacy literal
@@ -66,31 +66,20 @@ def _make_city(name: str = "testville") -> City:
 
 @pytest.fixture()
 def harvester_conn():
-    """In-memory settlements schema parity with live DB.
-
-    init_schema creates the modern INV-14 columns but does not add the
-    pre-INV-14 bin-evidence columns (pm_bin_lo/pm_bin_hi/unit/
-    settlement_source_type) via ALTER — those were created by an older
-    schema version. Live DBs already carry them; fresh test DBs don't.
-    Extend fresh schema here so the harvester INSERT path can bind all
-    columns.
-    """
+    """In-memory settlements schema parity with the harvester live write path."""
     conn = sqlite3.connect(":memory:")
     conn.row_factory = sqlite3.Row
     init_schema(conn)
-    for ddl in [
-        "ALTER TABLE settlements ADD COLUMN pm_bin_lo REAL;",
-        "ALTER TABLE settlements ADD COLUMN pm_bin_hi REAL;",
-        "ALTER TABLE settlements ADD COLUMN unit TEXT;",
-        "ALTER TABLE settlements ADD COLUMN settlement_source_type TEXT;",
-    ]:
-        try:
-            conn.execute(ddl)
-        except sqlite3.OperationalError:
-            pass
     return conn
 
 
+def test_fresh_schema_supplies_harvester_live_columns(harvester_conn):
+    columns = {
+        row["name"] for row in harvester_conn.execute("PRAGMA table_info(settlements)")
+    }
+    assert {"pm_bin_lo", "pm_bin_hi", "unit", "settlement_source_type"} <= columns
+
+
 def test_harvester_settlement_uses_canonical_high_identity(harvester_conn):
     """C6: the VERIFIED settlement row carries HIGH_LOCALDAY_MAX identity."""
     city = _make_city()
diff --git a/tests/test_settlements_unique_migration.py b/tests/test_settlements_unique_migration.py
index 54a9456..a6a225a 100644
--- a/tests/test_settlements_unique_migration.py
+++ b/tests/test_settlements_unique_migration.py
@@ -1,5 +1,5 @@
 # Created: 2026-04-24
-# Last reused/audited: 2026-04-24
+# Last reused/audited: 2026-04-28
 # Authority basis: REOPEN-2 data-readiness-tail UNIQUE migration
 # (docs/operations/task_2026-04-23_midstream_remediation/); closure of
 # forensic-audit C3+C4 — settlements UNIQUE(city, target_date) blocks
@@ -41,6 +41,13 @@ import pytest
 
 from src.state.db import init_schema
 
+_HARVESTER_LIVE_SETTLEMENT_COLUMNS = {
+    "pm_bin_lo",
+    "pm_bin_hi",
+    "unit",
+    "settlement_source_type",
+}
+
 
 def _fresh() -> sqlite3.Connection:
     conn = sqlite3.connect(":memory:")
@@ -48,6 +55,10 @@ def _fresh() -> sqlite3.Connection:
     return conn
 
 
+def _settlement_columns(conn: sqlite3.Connection) -> set[str]:
+    return {row[1] for row in conn.execute("PRAGMA table_info(settlements)")}
+
+
 def _seed_legacy_pre_reopen2_schema(conn: sqlite3.Connection) -> None:
     """Build a settlements table as it existed pre-REOPEN-2 (with old
     UNIQUE(city, target_date) constraint + no INV-14 columns yet)."""
@@ -69,6 +80,42 @@ def _seed_legacy_pre_reopen2_schema(conn: sqlite3.Connection) -> None:
     )
 
 
+def _seed_legacy_post_reopen2_without_harvester_live_columns(
+    conn: sqlite3.Connection,
+) -> None:
+    """Build a post-REOPEN-2 settlements table that predates DR-33 harvester
+    bin/source-family columns.
+
+    This shape already has UNIQUE(city, target_date, temperature_metric), so
+    the table-rebuild migration must not be required for the four nullable
+    harvester-live columns. The generic ALTER loop must add them.
+    """
+    conn.execute(
+        """
+        CREATE TABLE settlements (
+            id INTEGER PRIMARY KEY AUTOINCREMENT,
+            city TEXT NOT NULL,
+            target_date TEXT NOT NULL,
+            market_slug TEXT,
+            winning_bin TEXT,
+            settlement_value REAL,
+            settlement_source TEXT,
+            settled_at TEXT,
+            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
+                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
+            temperature_metric TEXT
+                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
+            physical_quantity TEXT,
+            observation_field TEXT
+                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
+            data_version TEXT,
+            provenance_json TEXT,
+            UNIQUE(city, target_date, temperature_metric)
+        )
+        """
+    )
+
+
 def _insert_verified_row(
     conn: sqlite3.Connection, *, city: str, target_date: str, metric: str = "high"
 ) -> None:
@@ -103,6 +150,20 @@ def test_fresh_db_has_new_unique_constraint():
         conn.close()
 
 
+def test_fresh_db_has_harvester_live_schema_parity_columns():
+    """Fresh schema must accept harvester live settlement writes.
+
+    `src.execution.harvester._write_settlement_truth()` inserts
+    pm_bin_lo/pm_bin_hi/unit/settlement_source_type. Fresh DBs must carry the
+    same nullable columns as legacy live DBs and rebuilt REOPEN-2 DBs.
+    """
+    conn = _fresh()
+    try:
+        assert _HARVESTER_LIVE_SETTLEMENT_COLUMNS <= _settlement_columns(conn)
+    finally:
+        conn.close()
+
+
 def test_fresh_db_dual_track_insert_works():
     conn = _fresh()
     try:
@@ -222,6 +283,51 @@ def test_legacy_db_migration_idempotent():
         conn.close()
 
 
+def test_legacy_new_unique_schema_gets_harvester_live_columns_via_alter():
+    """Legacy DBs that already have the new UNIQUE still need nullable
+    harvester-live columns.
+
+    This pins the non-rebuild ALTER path: a DB can have the REOPEN-2
+    UNIQUE(city,target_date,temperature_metric) shape while still lacking
+    pm_bin_lo/pm_bin_hi/unit/settlement_source_type.
+    """
+    conn = sqlite3.connect(":memory:")
+    try:
+        _seed_legacy_post_reopen2_without_harvester_live_columns(conn)
+        conn.execute(
+            """
+            INSERT INTO settlements (
+                city, target_date, authority, temperature_metric,
+                physical_quantity, observation_field, data_version,
+                provenance_json
+            )
+            VALUES (
+                'london', '2026-04-20', 'VERIFIED', 'high',
+                'mx2t6_local_calendar_day_max', 'high_temp',
+                'wu_icao_history_v1', '{}'
+            )
+            """
+        )
+        conn.commit()
+        pre_sql = conn.execute(
+            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
+        ).fetchone()[0]
+        assert "UNIQUE(city, target_date, temperature_metric)" in pre_sql
+        assert not (_HARVESTER_LIVE_SETTLEMENT_COLUMNS <= _settlement_columns(conn))
+
+        init_schema(conn)
+
+        post_sql = conn.execute(
+            "SELECT sql FROM sqlite_master WHERE name='settlements' AND type='table'"
+        ).fetchone()[0]
+        assert "UNIQUE(city, target_date, temperature_metric)" in post_sql
+        assert _HARVESTER_LIVE_SETTLEMENT_COLUMNS <= _settlement_columns(conn)
+        post_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
+        assert post_count == 1
+    finally:
+        conn.close()
+
+
 def test_triggers_survive_table_rebuild():
     """After the REOPEN-2 table-rebuild, all three settlements_* triggers
     must be re-installed (the rebuild drops the old table + its triggers;

```

## Negative production diffs

```text
git diff -- src/contracts/settlement_semantics.py src/execution/harvester.py
```
