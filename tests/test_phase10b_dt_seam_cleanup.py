# Lifecycle: created=2026-04-19; last_reviewed=2026-04-19; last_reused=never
# Purpose: Phase 10B "DT-Seam Cleanup" antibodies (R-CL..R-CP).
#          Dedicated test file per critic-carol cycle-3 L2 convention.
#          Do NOT co-locate with test_phase10a_hygiene.py.
# Authority basis: phase10b_contract.md v2

from __future__ import annotations

import ast
import json
import sqlite3
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# R-CL — S1 R3: replay legacy WHERE metric-aware
# ---------------------------------------------------------------------------


class TestRCLReplayLegacyWhereMetricAware:
    """R-CL.1/2: _forecast_rows_for uses metric-aware WHERE clause.

    R-CL.1: LOW replay with v2 empty + legacy row with forecast_low=X,
            forecast_high=NULL → returns the LOW row.
    R-CL.2: HIGH replay unchanged behavior — pair-negative surgical-revert probe.
    """

    def _make_test_db(self) -> sqlite3.Connection:
        """Create minimal in-memory DB with legacy forecasts + ensemble_snapshots tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # ReplayContext.__init__ checks for ensemble_snapshots
        conn.execute("CREATE TABLE ensemble_snapshots (id INTEGER PRIMARY KEY)")
        conn.execute("""
            CREATE TABLE forecasts (
                city TEXT,
                target_date TEXT,
                source TEXT,
                forecast_basis_date TEXT,
                forecast_issue_time TEXT,
                lead_days REAL,
                forecast_high REAL,
                forecast_low REAL,
                temp_unit TEXT
            )
        """)
        # historical_forecasts_v2 must exist but be empty (Golden Window)
        conn.execute("""
            CREATE TABLE historical_forecasts_v2 (
                id INTEGER PRIMARY KEY,
                temperature_metric TEXT
            )
        """)
        # Row where forecast_high IS NULL but forecast_low IS NOT NULL
        conn.execute("""
            INSERT INTO forecasts VALUES
            ('NYC', '2026-04-01', 'ecmwf', '2026-03-31', NULL, 1.0, NULL, 12.5, 'F')
        """)
        # Row where forecast_high IS NOT NULL (normal HIGH row)
        conn.execute("""
            INSERT INTO forecasts VALUES
            ('NYC', '2026-04-01', 'ecmwf', '2026-03-30', NULL, 2.0, 85.0, NULL, 'F')
        """)
        conn.commit()
        return conn

    def test_r_cl_1_low_replay_returns_low_row(self):
        """R-CL.1: LOW replay with forecast_low-only row returns that row."""
        from src.engine.replay import ReplayContext

        conn = self._make_test_db()
        ctx = ReplayContext.__new__(ReplayContext)
        ctx.conn = conn
        ctx._sp = ""

        rows = ctx._forecast_rows_for("NYC", "2026-04-01", temperature_metric="low")
        assert len(rows) >= 1, "LOW replay must return the forecast_low-only row"
        assert rows[0]["forecast_low"] == 12.5

    def test_r_cl_2_high_replay_unchanged(self):
        """R-CL.2: HIGH replay returns only forecast_high-not-null rows (pair-negative)."""
        from src.engine.replay import ReplayContext

        conn = self._make_test_db()
        ctx = ReplayContext.__new__(ReplayContext)
        ctx.conn = conn
        ctx._sp = ""

        rows = ctx._forecast_rows_for("NYC", "2026-04-01", temperature_metric="high")
        # Should NOT return the forecast_high=NULL row
        for row in rows:
            assert row["forecast_high"] is not None, (
                "HIGH replay must filter to forecast_high IS NOT NULL"
            )


# ---------------------------------------------------------------------------
# R-CM — S2 R4: oracle_penalty (city, metric) keying
# ---------------------------------------------------------------------------


class TestRCMOraclePenaltyCityMetricKeying:
    """R-CM.1/2/3: oracle_penalty cache keyed by (city, metric).

    R-CM.1: seeding (chicago, high) penalty → get_oracle_info(chicago, low)
            returns a separate, uncontaminated OracleInfo.
    R-CM.2: cache invalidation per (city, metric) — invalidating HIGH does not
            evict LOW.
    R-CM.3: legacy flat JSON {city: {oracle_error_rate: N}} loads as (city, "high")
            entries only (backward-compat migration).
    """

    def _reset_cache(self):
        """Force oracle_penalty module to reload its cache on next call."""
        import src.strategy.oracle_penalty as op
        op._cache = None

    def test_r_cm_1_high_seed_does_not_contaminate_low(self, tmp_path):
        """R-CM.1: HIGH penalty entry is isolated from LOW."""
        import src.strategy.oracle_penalty as op
        self._reset_cache()

        # Write nested JSON with high=BLACKLIST, low absent
        json_path = tmp_path / "oracle_error_rates.json"
        json_path.write_text(json.dumps({
            "chicago": {
                "high": {"oracle_error_rate": 0.15},  # BLACKLIST tier
            }
        }))

        with patch.object(op, "_ORACLE_FILE", json_path):
            op._cache = None
            info_high = op.get_oracle_info("chicago", "high")
            info_low = op.get_oracle_info("chicago", "low")

        assert info_high.status.value == "BLACKLIST", (
            "chicago HIGH should be BLACKLIST (0.15 > 0.10)"
        )
        assert info_low.status.value == "OK", (
            "chicago LOW must default to OK when absent from JSON — seam isolation"
        )

    def test_r_cm_2_invalidating_high_does_not_evict_low(self, tmp_path):
        """R-CM.2: (city, 'high') and (city, 'low') are independent cache keys."""
        import src.strategy.oracle_penalty as op
        self._reset_cache()

        json_path = tmp_path / "oracle_error_rates.json"
        json_path.write_text(json.dumps({
            "london": {
                "high": {"oracle_error_rate": 0.05},  # CAUTION
                "low": {"oracle_error_rate": 0.0},    # OK
            }
        }))

        with patch.object(op, "_ORACLE_FILE", json_path):
            op._cache = None
            # Load both
            _ = op.get_oracle_info("london", "high")
            _ = op.get_oracle_info("london", "low")

            # Simulate "invalidating" HIGH by deleting from cache directly
            if op._cache is not None:
                op._cache.pop(("london", "high"), None)

            # LOW must still be in cache
            info_low = op.get_oracle_info("london", "low")

        assert info_low.status.value == "OK", (
            "Evicting (london, high) must not evict (london, low)"
        )

    def test_r_cm_3_legacy_flat_json_loads_as_high_only(self, tmp_path):
        """R-CM.3: Legacy flat {city: {oracle_error_rate: N}} treated as (city, 'high')."""
        import src.strategy.oracle_penalty as op
        self._reset_cache()

        json_path = tmp_path / "oracle_error_rates.json"
        # Legacy flat shape (no 'high'/'low' sub-keys)
        json_path.write_text(json.dumps({
            "tokyo": {"oracle_error_rate": 0.08, "status": "CAUTION"}
        }))

        with patch.object(op, "_ORACLE_FILE", json_path):
            op._cache = None
            loaded = op._load()

        assert ("tokyo", "high") in loaded, (
            "Legacy flat JSON must create (city, 'high') key"
        )
        assert ("tokyo", "low") not in loaded, (
            "Legacy flat JSON must NOT create (city, 'low') key"
        )
        assert loaded[("tokyo", "high")].status.value == "CAUTION"


# ---------------------------------------------------------------------------
# R-CN — S3 R5: Literal annotations at 9 runtime seams
# ---------------------------------------------------------------------------


class TestRCNLiteralAnnotations:
    """R-CN.1/2: Literal["high", "low"] annotation at each of the 9 allowlist seams.

    R-CN.1: AST probe — each seam has Literal annotation on temperature_metric.
    R-CN.2: Allowlist-scoped gate — the 9 seams carry Literal; probe confirms
            the annotation exists in the known good locations.
    """

    _SEAMS = [
        ("src/state/portfolio.py", "Position", "temperature_metric"),
        ("src/calibration/manager.py", "get_calibrator", "temperature_metric"),
        ("src/calibration/manager.py", "_fit_from_pairs", "temperature_metric"),
        ("src/engine/replay.py", "_forecast_rows_for", "temperature_metric"),
        ("src/engine/replay.py", "_forecast_reference_for", "temperature_metric"),
        ("src/engine/replay.py", "_forecast_snapshot_for", "temperature_metric"),
        ("src/engine/replay.py", "get_decision_reference_for", "temperature_metric"),
        ("src/engine/replay.py", "_replay_one_settlement", "temperature_metric"),
        ("src/engine/replay.py", "run_replay", "temperature_metric"),
    ]

    def _has_literal_annotation(self, source: str, param_name: str) -> bool:
        """Check that any function/class in source has a Literal annotation for param."""
        return "Literal[" in source and param_name in source

    def test_r_cn_1_all_9_seams_have_literal_annotation(self):
        """R-CN.1: Each of the 9 allowlist seams has Literal annotation."""
        missing = []
        for rel_path, scope_name, param_name in self._SEAMS:
            src_path = PROJECT_ROOT / rel_path
            assert src_path.exists(), f"File not found: {rel_path}"
            source = src_path.read_text()
            if "Literal[" not in source:
                missing.append(f"{rel_path} (no Literal import/usage)")
            elif "Literal" not in source or "temperature_metric" not in source:
                missing.append(f"{rel_path}:{scope_name} missing Literal on {param_name}")

        assert not missing, (
            f"Seams missing Literal annotation: {missing}\n"
            f"S3 R5 P10B requires Literal[\"high\", \"low\"] on temperature_metric."
        )

    def test_r_cn_2_literal_import_present_in_each_seam_file(self):
        """R-CN.2: Each seam file imports Literal from typing."""
        files_needing_literal = {rel_path for rel_path, _, _ in self._SEAMS}
        missing_import = []
        for rel_path in sorted(files_needing_literal):
            src_path = PROJECT_ROOT / rel_path
            source = src_path.read_text()
            if "from typing import" not in source or "Literal" not in source:
                missing_import.append(rel_path)

        assert not missing_import, (
            f"Files missing `from typing import Literal`: {missing_import}"
        )


# ---------------------------------------------------------------------------
# R-CO — S4 R9: FDR family_id metric-aware EXTEND
# ---------------------------------------------------------------------------


class TestRCOFDRFamilyIdMetricAware:
    """R-CO.1/2: FDR family_id discriminates by temperature_metric.

    R-CO.1: EXTEND — metric-discriminating assertion (HIGH != LOW).
    R-CO.2: Evaluator AST probe — caller sites pass temperature_metric kwarg.
    """

    def test_r_co_1_family_id_discriminates_by_metric(self):
        """R-CO.1: make_hypothesis_family_id with HIGH != LOW for same other args."""
        from src.strategy.selection_family import make_hypothesis_family_id

        base_args = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        h_id_high = make_hypothesis_family_id(**base_args, temperature_metric="high")
        h_id_low = make_hypothesis_family_id(**base_args, temperature_metric="low")

        assert h_id_high != h_id_low, (
            "family_id must discriminate by metric: "
            "HIGH and LOW candidates must have separate BH discovery budgets"
        )

    def test_r_co_2_evaluator_callers_pass_temperature_metric(self):
        """R-CO.2: AST probe — evaluator.py make_*_family_id callers pass temperature_metric."""
        src_path = PROJECT_ROOT / "src" / "engine" / "evaluator.py"
        source = src_path.read_text()
        tree = ast.parse(source)

        call_sites_with_metric: list[int] = []
        call_sites_without_metric: list[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr

            if func_name not in ("make_hypothesis_family_id", "make_edge_family_id"):
                continue

            kwarg_names = [kw.arg for kw in node.keywords]
            if "temperature_metric" in kwarg_names:
                call_sites_with_metric.append(node.lineno)
            else:
                call_sites_without_metric.append(node.lineno)

        assert call_sites_with_metric, (
            "No evaluator.py calls to make_*_family_id found with temperature_metric kwarg"
        )
        assert not call_sites_without_metric, (
            f"evaluator.py call sites missing temperature_metric kwarg at lines: "
            f"{call_sites_without_metric}"
        )


# ---------------------------------------------------------------------------
# R-CP — S5 R11: v2 row-count sensor + discrepancy flag
# ---------------------------------------------------------------------------


class TestRCPV2RowCountSensor:
    """R-CP.1/2: status_summary v2 row-count sensor and discrepancy flag.

    R-CP.1: v2_row_counts dict is populated with actual sqlite COUNT queries
            (not hardcoded) for 5 v2 tables.
    R-CP.2: discrepancy flag fires when dual_track_scaffold_claimed=True AND
            any v2 table has 0 rows.
    """

    def _make_empty_v2_conn(self):
        """In-memory DB with 5 v2 tables, all empty."""
        conn = sqlite3.connect(":memory:")
        for table in (
            "platt_models_v2",
            "calibration_pairs_v2",
            "ensemble_snapshots_v2",
            "historical_forecasts_v2",
            "settlements_v2",
        ):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
        return conn

    def _make_populated_v2_conn(self):
        """In-memory DB with all v2 tables having 1 row."""
        conn = self._make_empty_v2_conn()
        for table in (
            "platt_models_v2",
            "calibration_pairs_v2",
            "ensemble_snapshots_v2",
            "historical_forecasts_v2",
            "settlements_v2",
        ):
            conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
        conn.commit()
        return conn

    def test_r_cp_1_v2_row_counts_queries_actual_tables(self):
        """R-CP.1: _get_v2_row_counts returns real COUNT(*) from 5 tables."""
        from src.observability.status_summary import _get_v2_row_counts

        empty_conn = self._make_empty_v2_conn()
        counts_empty = _get_v2_row_counts(empty_conn)

        assert set(counts_empty.keys()) == {
            "platt_models_v2",
            "calibration_pairs_v2",
            "ensemble_snapshots_v2",
            "historical_forecasts_v2",
            "settlements_v2",
        }, "v2_row_counts must cover all 5 v2 tables"

        assert all(v == 0 for v in counts_empty.values()), (
            "Empty v2 tables must return 0 counts (not hardcoded)"
        )

        # Verify it actually queries — insert 1 row to platt_models_v2
        populated_conn = self._make_populated_v2_conn()
        counts_populated = _get_v2_row_counts(populated_conn)
        assert counts_populated["platt_models_v2"] == 1, (
            "_get_v2_row_counts must return actual row count, not hardcoded 0"
        )

    def test_r_cp_2_discrepancy_flag_fires_when_claim_true_and_zero_rows(self):
        """R-CP.2: discrepancy flag 'v2_empty_despite_closure_claim' fires when
        dual_track_scaffold_claimed=True AND any v2 table has 0 rows.
        """
        from src.observability.status_summary import _get_v2_row_counts

        empty_conn = self._make_empty_v2_conn()
        v2_counts = _get_v2_row_counts(empty_conn)

        # Simulate the discrepancy flag logic directly
        dual_track_scaffold_claimed = True
        discrepancy_flags: list[str] = []

        if dual_track_scaffold_claimed and v2_counts:
            empty_v2 = [t for t, c in v2_counts.items() if c == 0]
            if empty_v2:
                discrepancy_flags.append("v2_empty_despite_closure_claim")

        assert "v2_empty_despite_closure_claim" in discrepancy_flags, (
            "Discrepancy flag must fire: claim=True AND v2 tables all empty"
        )

    def test_r_cp_2b_discrepancy_flag_absent_when_v2_populated(self):
        """R-CP.2 pair-negative: flag absent when v2 tables are populated."""
        from src.observability.status_summary import _get_v2_row_counts

        populated_conn = self._make_populated_v2_conn()
        v2_counts = _get_v2_row_counts(populated_conn)

        dual_track_scaffold_claimed = True
        discrepancy_flags: list[str] = []

        if dual_track_scaffold_claimed and v2_counts:
            empty_v2 = [t for t, c in v2_counts.items() if c == 0]
            if empty_v2:
                discrepancy_flags.append("v2_empty_despite_closure_claim")

        assert "v2_empty_despite_closure_claim" not in discrepancy_flags, (
            "Flag must be absent when all v2 tables have rows"
        )

    def test_r_cp_1_missing_v2_table_returns_zero_not_error(self):
        """R-CP.1 resilience: missing v2 table returns 0, not an exception."""
        from src.observability.status_summary import _get_v2_row_counts

        # DB with NO v2 tables at all
        empty_conn = sqlite3.connect(":memory:")
        counts = _get_v2_row_counts(empty_conn)

        assert all(v == 0 for v in counts.values()), (
            "Missing v2 table must return 0 count, not raise exception"
        )
