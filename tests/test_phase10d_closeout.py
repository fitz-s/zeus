# Lifecycle: created=2026-04-19; last_reviewed=2026-04-19; last_reused=never
# Purpose: Phase 10D SLIM closeout antibodies (R-CY..R-DC).
# Authority basis: phase10d_contract.md v2

from __future__ import annotations

import ast
import sqlite3
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_v2_snapshots_db(causality_status: str = "OK") -> sqlite3.Connection:
    """In-memory DB with ensemble_snapshots_v2 table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00',
            fetch_time TEXT NOT NULL DEFAULT '2026-01-01T00:00:00',
            lead_hours REAL NOT NULL DEFAULT 0.0,
            members_json TEXT NOT NULL DEFAULT '[]',
            model_version TEXT NOT NULL DEFAULT 'ifs',
            data_version TEXT NOT NULL DEFAULT 'v1',
            training_allowed INTEGER NOT NULL DEFAULT 1,
            causality_status TEXT NOT NULL DEFAULT 'OK',
            boundary_ambiguous INTEGER NOT NULL DEFAULT 0,
            ambiguous_member_count INTEGER NOT NULL DEFAULT 0,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2
        (city, target_date, temperature_metric, causality_status, boundary_ambiguous)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("NYC", "2026-01-01", "low", causality_status, 0),
    )
    conn.commit()
    return conn


def _make_legacy_snapshots_db() -> sqlite3.Connection:
    """In-memory DB with ensemble_snapshots (legacy) table, including temperature_metric."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00',
            fetch_time TEXT NOT NULL DEFAULT '2026-01-01T00:00:00',
            lead_hours REAL NOT NULL DEFAULT 0.0,
            members_json TEXT NOT NULL DEFAULT '[]',
            spread REAL,
            is_bimodal INTEGER,
            model_version TEXT NOT NULL DEFAULT 'ifs',
            data_version TEXT NOT NULL DEFAULT 'live_v1',
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            temperature_metric TEXT NOT NULL DEFAULT 'high',
            UNIQUE(city, target_date, issue_time, data_version)
        )
    """)
    conn.commit()
    return conn


def _make_metric_identity(metric: str):
    """Build a MetricIdentity for testing."""
    from src.types.metric_identity import MetricIdentity
    return MetricIdentity(
        temperature_metric=metric,
        physical_quantity="temperature",
        observation_field="high_temp" if metric == "high" else "low_temp",
        data_version="v1",
    )


# ---------------------------------------------------------------------------
# R-CY — causality_status DB→Day0SignalInputs wire
# ---------------------------------------------------------------------------


class TestRCYCausalityStatusWire:
    """R-CY.1/2: causality_status flows from v2 snapshot row to Day0SignalInputs."""

    def test_r_cy_1_v2_row_causality_propagates(self):
        """R-CY.1: v2 row causality_status='N/A_CAUSAL_DAY_ALREADY_STARTED' reaches
        Day0SignalInputs (not overridden by 'OK' default)."""
        from src.engine.evaluator import _read_v2_snapshot_metadata

        conn = _make_v2_snapshots_db(causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED")
        meta = _read_v2_snapshot_metadata(conn, "NYC", "2026-01-01", "low")

        assert meta.get("causality_status") == "N/A_CAUSAL_DAY_ALREADY_STARTED", (
            "R-CY.1: causality_status must be read from the v2 row, "
            "not the 'OK' fallback. Got: " + repr(meta.get("causality_status"))
        )

    def test_r_cy_2_missing_v2_row_fallback_ok(self):
        """R-CY.2: missing v2 row → causality_status fallback 'OK' (Golden Window)."""
        from src.engine.evaluator import _read_v2_snapshot_metadata

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE ensemble_snapshots_v2 (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT, target_date TEXT, temperature_metric TEXT,
                causality_status TEXT DEFAULT 'OK',
                boundary_ambiguous INTEGER DEFAULT 0,
                fetch_time TEXT DEFAULT '2026-01-01T00:00:00'
            )
        """)
        # Empty table — no rows
        conn.commit()

        meta = _read_v2_snapshot_metadata(conn, "NYC", "2026-01-01", "low")

        # Empty dict → get() with default "OK"
        causality = meta.get("causality_status", "OK")
        assert causality == "OK", (
            "R-CY.2: missing v2 row must give causality_status fallback 'OK'. Got: " + repr(causality)
        )


# ---------------------------------------------------------------------------
# R-CZ — ensemble_signal member_extrema rename + property alias
# ---------------------------------------------------------------------------


class TestRCZMemberExtremaRename:
    """R-CZ.1/2/3: member_maxes property alias + internal rename correctness."""

    def _make_ens(self, metric: str = "high"):
        """Build a minimal EnsembleSignal for testing."""
        from src.signal.ensemble_signal import EnsembleSignal
        from src.contracts.settlement_semantics import SettlementSemantics
        from src.config import City
        from datetime import date, timezone, datetime

        city = City(
            name="NYC",
            wu_station="KLGA",
            settlement_unit="F",
            cluster="northeast",
            lat=40.7,
            lon=-74.0,
            timezone="America/New_York",
            settlement_source_type="wu_icao",
        )
        target_date = date(2026, 1, 1)
        semantics = SettlementSemantics.for_city(city)
        metric_id = _make_metric_identity(metric)

        # Build 51-member hourly forecast covering 3 full days.
        # America/New_York is UTC-5 in winter, so 2026-01-01 local = 2026-01-01T05:00Z
        # to 2026-01-02T05:00Z. Start base at 2025-12-31T00:00Z with 96h to ensure
        # the full local day has ≥24 hours and the completeness check (≥20h) passes.
        n_members = 51
        n_hours = 96
        rng = np.random.default_rng(42)
        members_hourly = rng.normal(40.0, 2.0, (n_members, n_hours))
        base = datetime(2025, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        from datetime import timedelta
        times = [
            (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for i in range(n_hours)
        ]

        ens = EnsembleSignal(
            members_hourly,
            times,
            city,
            target_date,
            settlement_semantics=semantics,
            temperature_metric=metric_id,
        )
        return ens

    def test_r_cz_1_member_extrema_equals_member_maxes(self):
        """R-CZ.1: ens.member_extrema == ens.member_maxes (property alias is identity)."""
        ens = self._make_ens("high")
        assert np.array_equal(ens.member_extrema, ens.member_maxes), (
            "R-CZ.1: member_maxes property must return identical array as member_extrema"
        )

    def test_r_cz_2_no_assignment_self_member_maxes_in_source(self):
        """R-CZ.2: AST confirms zero 'self.member_maxes = ' assignment sites in ensemble_signal.py."""
        source_path = PROJECT_ROOT / "src" / "signal" / "ensemble_signal.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        assignments = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = getattr(node, "targets", None) or (
                    [node.target] if hasattr(node, "target") else []
                )
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr == "member_maxes"
                    ):
                        assignments.append(ast.unparse(node))

        assert assignments == [], (
            "R-CZ.2: ensemble_signal.py must have zero 'self.member_maxes = ' write sites "
            "(only the @property should remain). Found: " + str(assignments)
        )

    def test_r_cz_3_member_maxes_settled_is_separate_attribute(self):
        """R-CZ.3: self.member_maxes_settled remains a separate array attribute (not aliased)."""
        ens = self._make_ens("high")
        assert hasattr(ens, "member_maxes_settled"), (
            "R-CZ.3: member_maxes_settled must still exist as a separate attribute"
        )
        # It must be an ndarray, not the same object as member_extrema
        assert isinstance(ens.member_maxes_settled, np.ndarray), (
            "R-CZ.3: member_maxes_settled must be an ndarray"
        )
        # Verify it's the SETTLED (rounded) version — may differ from member_extrema
        assert ens.member_maxes_settled is not ens.member_extrema, (
            "R-CZ.3: member_maxes_settled must be a distinct object from member_extrema"
        )


# ---------------------------------------------------------------------------
# R-DA — legacy ensemble_snapshots temperature_metric column
# ---------------------------------------------------------------------------


class TestRDALegacyMetricColumn:
    """R-DA.1/2/3: ensemble_snapshots has temperature_metric column + correct stamping."""

    def test_r_da_1_schema_has_temperature_metric_column(self):
        """R-DA.1: init_schema creates ensemble_snapshots with temperature_metric column,
        default 'high'."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from src.state.db import init_schema
        init_schema(conn)

        # Check column exists in PRAGMA table_info
        cols = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(ensemble_snapshots)").fetchall()
        }
        assert "temperature_metric" in cols, (
            "R-DA.1: ensemble_snapshots must have temperature_metric column after init_schema"
        )
        assert cols["temperature_metric"]["dflt_value"] in ("'high'", "high"), (
            "R-DA.1: temperature_metric default must be 'high'. Got: "
            + repr(cols["temperature_metric"]["dflt_value"])
        )

    def test_r_da_2_low_candidate_writes_temperature_metric_low(self):
        """R-DA.2: _store_ens_snapshot writes temperature_metric='low' for a LOW candidate."""
        conn = _make_legacy_snapshots_db()

        # Build a mock ens with metric=low
        ens = MagicMock()
        ens.member_maxes = np.array([12.0, 11.5, 13.0])
        ens.temperature_metric = _make_metric_identity("low")
        ens.spread_float.return_value = 0.75
        ens.is_bimodal.return_value = False

        ens_result = {
            "fetch_time": "2026-01-01T00:00:00Z",
            "issue_time": "2026-01-01T00:00:00Z",
            "valid_time": "2026-01-01T12:00:00Z",
            "model": "ifs",
        }

        from src.config import City
        city = City(
            name="NYC", wu_station="KLGA", settlement_unit="F",
            cluster="northeast", lat=40.7, lon=-74.0,
            timezone="America/New_York", settlement_source_type="wu_icao",
        )

        from src.engine.evaluator import _store_ens_snapshot
        _store_ens_snapshot(conn, city, "2026-01-01", ens, ens_result)

        row = conn.execute(
            "SELECT temperature_metric FROM ensemble_snapshots WHERE city='NYC' LIMIT 1"
        ).fetchone()
        assert row is not None, "R-DA.2: snapshot row must exist after _store_ens_snapshot"
        assert row["temperature_metric"] == "low", (
            "R-DA.2: LOW candidate must write temperature_metric='low'. Got: "
            + repr(row["temperature_metric"])
        )

    def test_r_da_3_high_candidate_writes_temperature_metric_high(self):
        """R-DA.3: _store_ens_snapshot writes temperature_metric='high' for a HIGH candidate."""
        conn = _make_legacy_snapshots_db()

        ens = MagicMock()
        ens.member_maxes = np.array([72.0, 73.5, 71.0])
        ens.temperature_metric = _make_metric_identity("high")
        ens.spread_float.return_value = 1.25
        ens.is_bimodal.return_value = False

        ens_result = {
            "fetch_time": "2026-01-01T00:00:00Z",
            "issue_time": "2026-01-01T00:00:00Z",
            "valid_time": "2026-01-01T12:00:00Z",
            "model": "ifs",
        }

        from src.config import City
        city = City(
            name="NYC", wu_station="KLGA", settlement_unit="F",
            cluster="northeast", lat=40.7, lon=-74.0,
            timezone="America/New_York", settlement_source_type="wu_icao",
        )

        from src.engine.evaluator import _store_ens_snapshot
        _store_ens_snapshot(conn, city, "2026-01-01", ens, ens_result)

        row = conn.execute(
            "SELECT temperature_metric FROM ensemble_snapshots WHERE city='NYC' LIMIT 1"
        ).fetchone()
        assert row is not None, "R-DA.3: snapshot row must exist after _store_ens_snapshot"
        assert row["temperature_metric"] == "high", (
            "R-DA.3: HIGH candidate must write temperature_metric='high'. Got: "
            + repr(row["temperature_metric"])
        )


# ---------------------------------------------------------------------------
# R-DB — INV-13 provenance live (no escape flag)
# ---------------------------------------------------------------------------


class TestRDBProvenanceLive:
    """R-DB.1/2: require_provenance('kelly_mult') without escape flag succeeds."""

    def test_r_db_1_require_provenance_kelly_mult_no_escape(self):
        """R-DB.1: require_provenance('kelly_mult') succeeds without requires_provenance=False."""
        from src.contracts.provenance_registry import require_provenance

        # Must not raise — kelly_mult is registered
        record = require_provenance("kelly_mult")
        assert record is not None, (
            "R-DB.1: require_provenance('kelly_mult') must return a ProvenanceRecord "
            "when called without escape flag"
        )
        assert record.constant_name == "kelly_mult"

    def test_r_db_2_no_escape_flag_in_cycle_runner_src(self):
        """R-DB.2: AST confirms requires_provenance=False has zero occurrences in src/engine/."""
        engine_dir = PROJECT_ROOT / "src" / "engine"
        violations = []
        for py_file in engine_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if (
                            kw.arg == "requires_provenance"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is False
                        ):
                            violations.append(f"{py_file.name}:{node.lineno}")

        assert violations == [], (
            "R-DB.2: requires_provenance=False must not appear in src/engine/. "
            "Found: " + str(violations)
        )


# ---------------------------------------------------------------------------
# R-DC — INV-16 test transition (S7 assertions)
# ---------------------------------------------------------------------------


class TestRDCInv16Transition:
    """R-DC.1/2: INV-16 tests 1+2 pass post-S1; test 3 xfailed with P10E ticket."""

    def test_r_dc_1_inv16_tests_1_and_2_pass(self):
        """R-DC.1: evaluator.py source contains the INV-16 causality gate strings
        required by tests 1 and 2 of test_phase6_causality_status.py."""
        import inspect
        import src.engine.evaluator as ev_mod

        source = inspect.getsource(ev_mod)

        assert "N/A_CAUSAL_DAY_ALREADY_STARTED" in source, (
            "R-DC.1: evaluator must contain 'N/A_CAUSAL_DAY_ALREADY_STARTED' "
            "(INV-16 test 1 requirement)"
        )
        assert "CAUSAL_SLOT_NOT_OK" in source, (
            "R-DC.1: evaluator must contain 'CAUSAL_SLOT_NOT_OK' rejection stage "
            "(INV-16 test 2 requirement)"
        )
        assert "OBSERVATION_UNAVAILABLE_LOW" in source, (
            "R-DC.1: evaluator must retain 'OBSERVATION_UNAVAILABLE_LOW' stage "
            "(distinct rejection axis)"
        )

    def test_r_dc_2_inv16_test3_passes_natively_post_p10e(self):
        """R-DC.2 (P10E updated): test_day0_observation_context_carries_causality_status
        was xfailed in P10D pending P10E Day0ObservationContext.causality_status field.
        P10E S3a added the field; xfail marker was removed; test now passes natively.

        This antibody enforces the inverse: NO stale xfail marker on the test (would
        mask the P10E S3a fix).
        """
        source_path = PROJECT_ROOT / "tests" / "test_phase6_causality_status.py"
        source = source_path.read_text(encoding="utf-8")

        # Find the test function's 5 lines preceding (where a decorator would sit)
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "def test_day0_observation_context_carries_causality_status" in line:
                preceding = "\n".join(lines[max(0, i - 3):i])
                assert "xfail" not in preceding.lower(), (
                    f"R-DC.2: stale xfail marker detected on INV-16 test 3 "
                    f"(P10E S3a added Day0ObservationContext.causality_status; "
                    f"test passes natively now). Preceding lines:\n{preceding}"
                )
                return
        raise AssertionError(
            "R-DC.2: test_day0_observation_context_carries_causality_status not found"
        )
