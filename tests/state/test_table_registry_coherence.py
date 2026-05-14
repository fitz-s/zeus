# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §1.1, §1.2, §3 (REV 4)
#   Antibodies A1, A2 (subset as A4), A8 per PLAN §3
#   INV-37 enforcement per architecture/invariants.yaml::INV-37
"""Registry coherence tests — A1/A2/A4/A8 antibody suite.

ANTIBODY PROOF per Fitz Core Methodology #4 (make category impossible):
  A1 (registry vs sqlite_master bidirectional set-equality):
    Regression injection (a): add a table to YAML that init_schema doesn't create
      -> test fails: missing_from_disk non-empty (registry - sqlite_master).
    Regression injection (b): add a new CREATE TABLE in db.py that YAML doesn't register
      -> test fails: extra_on_disk non-empty (sqlite_master - registry).
    Both directions independently checked — prior A1 failure mode was checking only
    one direction (round-2 critic finding). This test checks BOTH.
  A4 (assert_db_matches_registry FATAL semantics):
    Regression injection: remove a table from DB → RegistryAssertionError raised.
    The test is an antibody-proof that the function raises on mismatch (does NOT
    silently pass). A test that asserts raising is as strong as the error is explicit.
  A8 (no cross-DB write seam outside sanctioned ATTACH path):
    Regression injection: add a function that opens two independent connections
    and writes to both → test grep finds it → fails.
    Checks both the negative pattern (two_conn_cross_write) and the positive
    pattern (get_forecasts_connection_with_world ATTACH usage).
"""
from __future__ import annotations

import ast
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# A1 — Bidirectional set-equality: registry vs sqlite_master
# ---------------------------------------------------------------------------

class TestA1RegistryVsSqliteMaster:
    """A1 antibody: registry declared tables == tables init_schema creates.

    Uses INDEPENDENTLY SOURCED data:
    - LHS: architecture/db_table_ownership.yaml (registry loader)
    - RHS: sqlite_master from :memory: init_schema / init_schema_forecasts
    Neither side derives from the other.
    """

    def test_a1_world_side_bidirectional(self):
        """World registry == init_schema_world_only sqlite_master (both directions).

        Regression coverage:
        (a) Registry has table X not created by init_schema → missing_from_disk
            (registry X not in sqlite_master) → assertion fails.
        (b) init_schema creates table Y not in registry → extra_on_disk
            (sqlite_master Y not in registry) → assertion fails.
        Both directions independently checked.
        """
        from src.state.db import init_schema_world_only
        from src.state.table_registry import DBIdentity, SchemaClass, _REGISTRY, tables_for

        # LHS: registry-declared world tables (non-legacy_archived only)
        registry_world = tables_for(DBIdentity.WORLD)

        # Also exclude tables that appear as legacy_archived on world (ghost copies)
        # — these exist on disk but are explicitly excluded from the set-equality check
        legacy_archived_world = frozenset(
            name
            for (name, db_id), entry in _REGISTRY.items()
            if db_id == DBIdentity.WORLD and entry.schema_class == SchemaClass.LEGACY_ARCHIVED
        )

        # RHS: sqlite_master from fresh :memory: init — independent source
        conn = sqlite3.connect(":memory:")
        init_schema_world_only(conn)
        disk_tables = frozenset(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        conn.close()

        # Exclude legacy_archived from disk side too (they exist but are ghost copies)
        disk_tables_non_ghost = disk_tables - legacy_archived_world

        # Direction 1: missing_from_disk = registry says it exists, init doesn't create it
        missing_from_disk = registry_world - disk_tables_non_ghost
        # Direction 2: extra_on_disk = init creates it, registry doesn't know about it
        extra_on_disk = disk_tables_non_ghost - registry_world

        assert not missing_from_disk, (
            f"A1 WORLD FAIL (direction 1): registry declares these tables but "
            f"init_schema_world_only() doesn't create them: {sorted(missing_from_disk)}. "
            f"Add CREATE TABLE to init_schema or remove from registry YAML."
        )
        assert not extra_on_disk, (
            f"A1 WORLD FAIL (direction 2): init_schema_world_only() creates these tables "
            f"but registry doesn't declare them: {sorted(extra_on_disk)}. "
            f"Add entries to architecture/db_table_ownership.yaml."
        )

    def test_a1_forecasts_side_bidirectional(self, tmp_path):
        """Forecasts registry == init_schema_forecasts sqlite_master (both directions).

        init_schema_forecasts uses ATTACH path against world.db, so we must
        provide a real on-disk world DB. Uses tmp_path for isolation.
        """
        import src.state.db as db_mod
        from src.state.table_registry import DBIdentity, tables_for

        # LHS: registry-declared forecasts tables (non-legacy_archived only)
        registry_forecasts = tables_for(DBIdentity.FORECASTS)

        # Build world.db on disk (needed by init_schema_forecasts ATTACH path)
        wc_path = tmp_path / "zeus-world.db"
        fc_path = tmp_path / "zeus-forecasts.db"
        conn_w = sqlite3.connect(str(wc_path))
        db_mod.init_schema(conn_w)
        conn_w.close()

        # Redirect ZEUS_WORLD_DB_PATH / ZEUS_FORECASTS_DB_PATH for ATTACH to resolve
        orig_w = db_mod.ZEUS_WORLD_DB_PATH
        orig_f = db_mod.ZEUS_FORECASTS_DB_PATH
        try:
            db_mod.ZEUS_WORLD_DB_PATH = wc_path
            db_mod.ZEUS_FORECASTS_DB_PATH = fc_path
            conn_f = sqlite3.connect(str(fc_path))
            db_mod.init_schema_forecasts(conn_f)
            disk_tables = frozenset(
                row[0]
                for row in conn_f.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            )
            conn_f.close()
        finally:
            db_mod.ZEUS_WORLD_DB_PATH = orig_w
            db_mod.ZEUS_FORECASTS_DB_PATH = orig_f

        # Direction 1: missing_from_disk
        missing_from_disk = registry_forecasts - disk_tables
        # Direction 2: extra_on_disk
        extra_on_disk = disk_tables - registry_forecasts

        assert not missing_from_disk, (
            f"A1 FORECASTS FAIL (direction 1): registry declares these tables but "
            f"init_schema_forecasts() doesn't create them: {sorted(missing_from_disk)}. "
            f"Add CREATE TABLE to init_schema_forecasts or remove from registry YAML."
        )
        assert not extra_on_disk, (
            f"A1 FORECASTS FAIL (direction 2): init_schema_forecasts() creates these tables "
            f"but registry doesn't declare them: {sorted(extra_on_disk)}. "
            f"Add forecast_class entries to architecture/db_table_ownership.yaml."
        )

    def test_a1_forecast_tables_constant_matches_registry(self):
        """_FORECAST_TABLES in db.py matches tables_for_class(FORECAST_CLASS) in registry.

        A THIRD source comparison: the _FORECAST_TABLES tuple literal in db.py
        must be a subset of the registry's forecast_class set.
        (It can be a subset because _FORECAST_TABLES is used for the ATTACH path,
        not necessarily ALL forecast tables.)
        """
        import src.state.db as db_mod
        from src.state.table_registry import SchemaClass, tables_for_class

        db_constant = frozenset(db_mod._FORECAST_TABLES)
        registry_fc = tables_for_class(SchemaClass.FORECAST_CLASS)

        # _FORECAST_TABLES must be subset of registry forecast_class
        not_in_registry = db_constant - registry_fc
        assert not not_in_registry, (
            f"A1 CONSTANT FAIL: db._FORECAST_TABLES contains tables not in registry "
            f"as forecast_class: {sorted(not_in_registry)}. "
            f"Update architecture/db_table_ownership.yaml or db._FORECAST_TABLES."
        )

        # Registry forecast_class must be subset of _FORECAST_TABLES (full coverage)
        not_in_constant = registry_fc - db_constant
        assert not not_in_constant, (
            f"A1 CONSTANT FAIL: registry has forecast_class tables not in "
            f"db._FORECAST_TABLES: {sorted(not_in_constant)}. "
            f"Add to _FORECAST_TABLES or reclassify in registry YAML."
        )


# ---------------------------------------------------------------------------
# A4 — assert_db_matches_registry FATAL semantics
# ---------------------------------------------------------------------------

class TestA4AssertDbMatchesRegistry:
    """A4 antibody: assert_db_matches_registry raises RegistryAssertionError on mismatch.

    Per PLAN §1.1: fail-closed FATAL per INV-05. No advisory mode.

    Antibody proof: if this test passes, it means the function DOES raise on mismatch.
    If a future engineer makes assert_db_matches_registry advisory (warns instead
    of raises), this test fails because pytest.raises won't catch a warning.
    """

    def test_a4_raises_on_missing_table(self):
        """RegistryAssertionError raised when a world_class table is missing from DB.

        Regression injection: remove a table from disk — simulates incomplete migration
        or wrong connection passed. assert_db_matches_registry must FATAL, not warn.
        """
        from src.state.db import init_schema_world_only
        from src.state.table_registry import DBIdentity, RegistryAssertionError, assert_db_matches_registry

        conn = sqlite3.connect(":memory:")
        init_schema_world_only(conn)
        # Drop one world_class table to simulate missing table
        conn.execute("DROP TABLE IF EXISTS data_coverage")
        conn.commit()

        with pytest.raises(RegistryAssertionError, match="data_coverage"):
            assert_db_matches_registry(conn, DBIdentity.WORLD)
        conn.close()

    def test_a4_raises_on_extra_ghost_table(self):
        """RegistryAssertionError raised when DB has a table not in registry.

        Simulates a new table added via CREATE TABLE but not registered in YAML.
        The function must catch both failure modes (missing AND extra).
        """
        from src.state.db import init_schema_world_only
        from src.state.table_registry import DBIdentity, RegistryAssertionError, assert_db_matches_registry

        conn = sqlite3.connect(":memory:")
        init_schema_world_only(conn)
        # Add an unregistered ghost table
        conn.execute("CREATE TABLE ghost_unregistered_xyz (id INTEGER PRIMARY KEY)")
        conn.commit()

        with pytest.raises(RegistryAssertionError, match="ghost_unregistered_xyz"):
            assert_db_matches_registry(conn, DBIdentity.WORLD)
        conn.close()

    def test_a4_passes_on_correct_world_schema(self):
        """assert_db_matches_registry passes (no exception) on correct world schema.

        Positive-path antibody: confirms the function doesn't false-positive on
        a correctly initialized world DB.
        """
        from src.state.db import init_schema_world_only
        from src.state.table_registry import DBIdentity, assert_db_matches_registry

        conn = sqlite3.connect(":memory:")
        init_schema_world_only(conn)

        # Must NOT raise (correct schema matches registry)
        assert_db_matches_registry(conn, DBIdentity.WORLD)
        conn.close()

    def test_a4_passes_on_correct_forecasts_schema(self, tmp_path):
        """assert_db_matches_registry passes on correct forecasts schema."""
        import src.state.db as db_mod
        from src.state.table_registry import DBIdentity, assert_db_matches_registry

        wc_path = tmp_path / "zeus-world.db"
        fc_path = tmp_path / "zeus-forecasts.db"
        conn_w = sqlite3.connect(str(wc_path))
        db_mod.init_schema(conn_w)
        conn_w.close()

        orig_w = db_mod.ZEUS_WORLD_DB_PATH
        orig_f = db_mod.ZEUS_FORECASTS_DB_PATH
        try:
            db_mod.ZEUS_WORLD_DB_PATH = wc_path
            db_mod.ZEUS_FORECASTS_DB_PATH = fc_path
            conn_f = sqlite3.connect(str(fc_path))
            db_mod.init_schema_forecasts(conn_f)
            assert_db_matches_registry(conn_f, DBIdentity.FORECASTS)
            conn_f.close()
        finally:
            db_mod.ZEUS_WORLD_DB_PATH = orig_w
            db_mod.ZEUS_FORECASTS_DB_PATH = orig_f

    def test_a4_column_shape_check_raises_on_missing_column(self):
        """assert_db_matches_registry raises when required column is missing.

        data_coverage has required_columns declared. Dropping a required column
        (data_source) must FATAL with RegistryAssertionError.
        """
        from src.state.db import init_schema_world_only
        from src.state.table_registry import DBIdentity, RegistryAssertionError, assert_db_matches_registry

        conn = sqlite3.connect(":memory:")
        init_schema_world_only(conn)
        # Recreate data_coverage without 'data_source' (a required column per registry)
        conn.executescript("""
            DROP TABLE IF EXISTS data_coverage;
            CREATE TABLE data_coverage (
                data_table TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                sub_key TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (data_table, city, target_date, sub_key)
            );
        """)
        conn.commit()

        with pytest.raises(RegistryAssertionError, match="data_source"):
            assert_db_matches_registry(conn, DBIdentity.WORLD)
        conn.close()


# ---------------------------------------------------------------------------
# A8 — No cross-DB write seam outside sanctioned ATTACH path (INV-37)
# ---------------------------------------------------------------------------

class TestA8NoCrossDbWriteTransaction:
    """A8 antibody: AST/grep check that no two-independent-connection cross-DB writes exist.

    INV-37 forbids writing to both world.db and forecasts.db via two independent
    connections in the same logical transaction. The sanctioned exception is
    get_forecasts_connection_with_world (ATTACH+SAVEPOINT atomicity).

    Antibody proof:
    If a new function opens get_world_connection() + get_forecasts_connection()
    and writes to both (two-connection cross-DB write), this test fails.
    The check is: any src/ function that calls BOTH get_world_connection AND
    get_forecasts_connection (or their write-class equivalents) in the same
    function body is a violation unless it uses get_forecasts_connection_with_world.
    """

    def test_a8_no_cross_db_write_transaction_in_src(self):
        """No src/ function body opens both get_world_connection and get_forecasts_connection.

        INV-37: cross-DB writes via two independent connections are forbidden.
        The sanctioned pattern is get_forecasts_connection_with_world (ATTACH+SAVEPOINT).

        Heuristic: scan all src/ Python files for functions that have BOTH
        'get_world_connection' and 'get_forecasts_connection' (the bare one, not
        _with_world) in the same function body. A function touching both is a
        latent two-connection cross-DB write seam.

        Exclusions (whole-file allowlist):
        - src/state/db.py: defines the helpers (not a call site)
        - src/state/connection_pair.py: ConnectionTriple factory (no write path)

        Per-function allowlist for legitimate same-function coexistence:
        (a function that opens both for independent non-cross-DB purposes is OK
        as long as it does NOT write to both in a single logical operation)
        - Populate this list only when a genuine false-positive is found.
        """
        WHOLE_FILE_ALLOWLIST = {
            "src/state/db.py",
            "src/state/connection_pair.py",
            # hole_scanner.main() opens both conns but writes ONLY to world.db
            # (data_coverage via world_conn). forecasts_conn is read-only
            # (_get_physical_table_keys for DataTable.OBSERVATIONS: SELECT only).
            # This is not a cross-DB write seam; adding here as a genuine false-positive.
            # Authority: docs/operations/task_2026-05-14_k1_followups/PLAN.md §2 P3 C4
            "src/data/hole_scanner.py",
        }

        violations: list[str] = []

        # Walk all src/*.py files
        for py_file in sorted((_REPO_ROOT / "src").rglob("*.py")):
            rel = str(py_file.relative_to(_REPO_ROOT))
            if rel in WHOLE_FILE_ALLOWLIST:
                continue
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Only check files that contain both strings at all (quick pre-filter)
            if "get_world_connection" not in source:
                continue
            if "get_forecasts_connection" not in source:
                continue

            # AST walk: find any function/method that uses both
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                func_src = ast.unparse(node)
                has_world = "get_world_connection" in func_src
                # Exclude the _with_world variant — it IS the sanctioned helper
                bare_forecasts = "get_forecasts_connection(" in func_src or \
                                 "get_forecasts_connection()" in func_src
                # Only flag if bare (non-_with_world) forecasts conn AND world conn in same fn
                if has_world and bare_forecasts:
                    violations.append(
                        f"{rel}::{node.name} (line {node.lineno})"
                    )

        assert not violations, (
            f"A8/INV-37: these src/ functions open both get_world_connection AND "
            f"bare get_forecasts_connection in the same function body — latent "
            f"two-connection cross-DB write seam. Use get_forecasts_connection_with_world "
            f"for cross-DB atomic writes: {violations}"
        )

    def test_a8_attach_helper_is_used_for_cross_db_obs_write(self):
        """The daily-obs writer uses get_forecasts_connection_with_world (not bare conn).

        Positive-path antibody: confirms the P0 fix is structurally in place.
        If _k2_daily_obs_tick or _k2_startup_catch_up reverts to get_world_connection
        or bare get_forecasts_connection, this test fails.
        """
        result = subprocess.run(
            ["grep", "-n", "get_forecasts_connection_with_world", "src/ingest_main.py"],
            capture_output=True, text=True,
            cwd=str(_REPO_ROOT),
        )
        lines = result.stdout.strip().splitlines()
        assert len(lines) >= 2, (
            f"A8 POSITIVE FAIL: expected >= 2 uses of get_forecasts_connection_with_world "
            f"in src/ingest_main.py (one for _k2_daily_obs_tick, one for "
            f"_k2_startup_catch_up). Found: {lines}. "
            f"P0 fix may have been reverted."
        )


# ---------------------------------------------------------------------------
# Registry loader failure semantics (import-time FATAL per §1.1)
# ---------------------------------------------------------------------------

class TestRegistryLoaderFatalSemantics:
    """Verify registry load raises at import time on structural violations."""

    def test_loader_rejects_duplicate_name_db_pair(self, tmp_path, monkeypatch):
        """Registry loader raises ValueError on duplicate (name, db) pair."""
        import src.state.table_registry as reg_mod

        bad_yaml = tmp_path / "bad_registry.yaml"
        bad_yaml.write_text(
            "schema_version: 1\n"
            "tables:\n"
            "  - name: foo\n"
            "    db: world\n"
            "    schema_class: world_class\n"
            "    schema_version_owner: SCHEMA_VERSION\n"
            "    created_by: init_schema\n"
            "    pk_col: null\n"
            "  - name: foo\n"
            "    db: world\n"
            "    schema_class: world_class\n"
            "    schema_version_owner: SCHEMA_VERSION\n"
            "    created_by: init_schema\n"
            "    pk_col: null\n"
        )
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", bad_yaml)
        with pytest.raises(ValueError, match="duplicate"):
            reg_mod._load_registry()

    def test_loader_rejects_unknown_db_enum(self, tmp_path, monkeypatch):
        """Registry loader raises ValueError on unknown db value."""
        import src.state.table_registry as reg_mod

        bad_yaml = tmp_path / "bad_registry.yaml"
        bad_yaml.write_text(
            "schema_version: 1\n"
            "tables:\n"
            "  - name: foo\n"
            "    db: invalid_db_name\n"
            "    schema_class: world_class\n"
            "    schema_version_owner: SCHEMA_VERSION\n"
            "    created_by: init_schema\n"
            "    pk_col: null\n"
        )
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", bad_yaml)
        with pytest.raises(ValueError, match="unknown db value"):
            reg_mod._load_registry()

    def test_loader_rejects_missing_required_field(self, tmp_path, monkeypatch):
        """Registry loader raises ValueError on entry missing required fields."""
        import src.state.table_registry as reg_mod

        bad_yaml = tmp_path / "bad_registry.yaml"
        bad_yaml.write_text(
            "schema_version: 1\n"
            "tables:\n"
            "  - name: foo\n"
            "    db: world\n"
        )
        monkeypatch.setattr(reg_mod, "_REGISTRY_PATH", bad_yaml)
        with pytest.raises(ValueError, match="missing required fields"):
            reg_mod._load_registry()
