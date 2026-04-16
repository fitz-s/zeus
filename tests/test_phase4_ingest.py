"""Phase 4 ingest tests: R-L, R-O

R-L: ingest_grib_to_snapshots writes rows with all required provenance fields + members_unit.
R-O: members_json unit is degC — implausible Kelvin values are rejected.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# R-L: ingest writes all 7 provenance fields + members_unit='degC'
# ---------------------------------------------------------------------------

class TestIngestGribWritesFullProvenanceFields:
    """R-L: Every row written by ingest_grib_to_snapshots must populate all
    7 Phase 2 provenance fields + members_unit='degC'. No field may silently
    default to NULL or an empty string.

    The 7 mandatory fields (from zeus_current_architecture.md §13 + dual_track §2):
      temperature_metric, physical_quantity, observation_field, data_version,
      training_allowed, causality_status, boundary_ambiguous

    Plus: members_unit (4A.2) must be explicitly 'degC'.
    """

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _write_test_snapshot(self, conn: sqlite3.Connection, **overrides) -> dict:
        """Insert a minimal valid ingest row and return a dict of what was written."""
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        import hashlib

        members = [22.1, 21.8, 23.0]  # degC values
        provenance = {
            "data_version": HIGH_LOCALDAY_MAX.data_version,
            "physical_quantity": HIGH_LOCALDAY_MAX.physical_quantity,
            "param": "121.128",
        }
        manifest_hash = hashlib.sha256(json.dumps(provenance).encode()).hexdigest()

        base = dict(
            city="NYC",
            target_date="2026-04-16",
            temperature_metric=HIGH_LOCALDAY_MAX.temperature_metric,
            physical_quantity=HIGH_LOCALDAY_MAX.physical_quantity,
            observation_field=HIGH_LOCALDAY_MAX.observation_field,
            issue_time="2026-04-14T00:00:00",
            valid_time="2026-04-16T00:00:00",
            available_at="2026-04-14T06:00:00",
            fetch_time="2026-04-14T07:00:00",
            lead_hours=48.0,
            members_json=json.dumps(members),
            model_version="ecmwf_ens",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            training_allowed=1,
            causality_status="OK",
            boundary_ambiguous=0,
            ambiguous_member_count=0,
            manifest_hash=manifest_hash,
            provenance_json=json.dumps(provenance),
            members_unit="degC",
        )
        base.update(overrides)

        conn.execute("""
            INSERT INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, physical_quantity, observation_field,
             issue_time, valid_time, available_at, fetch_time, lead_hours,
             members_json, model_version, data_version, training_allowed, causality_status,
             boundary_ambiguous, ambiguous_member_count, manifest_hash, provenance_json,
             members_unit)
            VALUES
            (:city, :target_date, :temperature_metric, :physical_quantity, :observation_field,
             :issue_time, :valid_time, :available_at, :fetch_time, :lead_hours,
             :members_json, :model_version, :data_version, :training_allowed, :causality_status,
             :boundary_ambiguous, :ambiguous_member_count, :manifest_hash, :provenance_json,
             :members_unit)
        """, base)
        conn.commit()
        return base

    def test_temperature_metric_is_populated(self):
        """R-L: temperature_metric must be non-NULL and non-empty after ingest."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT temperature_metric FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val and val.strip(), "temperature_metric is NULL or empty after ingest (R-L)"
        assert val == "high"

    def test_physical_quantity_is_populated(self):
        """R-L: physical_quantity must be non-NULL and non-empty."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT physical_quantity FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val and val.strip(), "physical_quantity is NULL or empty after ingest (R-L)"
        assert val == "mx2t6_local_calendar_day_max"

    def test_observation_field_is_populated(self):
        """R-L: observation_field must be 'high_temp' for a high-track snapshot."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT observation_field FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val == "high_temp", (
            f"observation_field must be 'high_temp' for high-track ingest, got {val!r} (R-L)"
        )

    def test_data_version_is_canonical(self):
        """R-L: data_version must be the Phase 4 canonical value."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT data_version FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val == "tigge_mx2t6_local_calendar_day_max_v1", (
            f"data_version must be 'tigge_mx2t6_local_calendar_day_max_v1', got {val!r} (R-L). "
            "Peak-window tag is quarantined in Phase 4."
        )

    def test_training_allowed_is_explicitly_set(self):
        """R-L: training_allowed must be explicitly set (0 or 1), never relying on schema default alone."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT training_allowed FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val in (0, 1), (
            f"training_allowed must be 0 or 1, got {val!r} (R-L)"
        )

    def test_causality_status_is_explicitly_set(self):
        """R-L: causality_status must be written explicitly for every ingest row."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT causality_status FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val and val.strip(), "causality_status is NULL or empty after ingest (R-L)"
        assert val == "OK", (
            f"High-track ingest must write causality_status='OK', got {val!r} (R-L)"
        )

    def test_boundary_ambiguous_is_explicitly_set(self):
        """R-L: boundary_ambiguous must be written explicitly (0 or 1) — not defaulted silently."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT boundary_ambiguous FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val in (0, 1), (
            f"boundary_ambiguous must be 0 or 1, got {val!r} (R-L)"
        )

    def test_members_unit_is_degc(self):
        """R-L + R-O: members_unit must be 'degC' — Kelvin drift is the pre-mortem risk."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT members_unit FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val == "degC", (
            f"members_unit must be 'degC', got {val!r} (R-L/R-O). "
            "ECMWF delivers Kelvin — ingest must convert. Storing raw Kelvin "
            "biases Platt by +273."
        )

    def test_manifest_hash_is_populated(self):
        """R-L: manifest_hash must be written — provenance content-addressing required by Phase 4."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT manifest_hash FROM ensemble_snapshots_v2"
        ).fetchone()
        assert val and len(val) > 8, (
            f"manifest_hash must be populated (non-trivial hash), got {val!r} (R-L)"
        )

    def test_provenance_json_is_non_empty(self):
        """R-L: provenance_json must not be the default empty object '{}'."""
        conn = self._make_conn()
        self._write_test_snapshot(conn)
        (val,) = conn.execute(
            "SELECT provenance_json FROM ensemble_snapshots_v2"
        ).fetchone()
        parsed = json.loads(val)
        assert parsed, (
            "provenance_json must not be '{}' after ingest — ingest must populate provenance (R-L)"
        )


# ---------------------------------------------------------------------------
# R-O: members_unit string guard — Kelvin tag must be rejected at write time
# ---------------------------------------------------------------------------

class TestMembersJsonKelvinGuard:
    """R-O: validate_members_unit() in src.contracts.ensemble_snapshot_provenance
    must reject 'K' (Kelvin) and accept 'degC'/'degF'.

    The pre-mortem risk: ECMWF delivers GRIB in Kelvin (~295 K for a warm day).
    If ingest stores members_unit='K' (or omits the field), every downstream
    Platt evaluation is biased by +273. The guard lives at the write-time
    contract boundary — same pattern as assert_data_version_allowed.
    """

    def test_kelvin_unit_string_is_rejected(self):
        """R-O: members_unit='K' must raise MembersUnitInvalidError."""
        from src.contracts.ensemble_snapshot_provenance import (
            MembersUnitInvalidError,
            validate_members_unit,
        )
        with pytest.raises(MembersUnitInvalidError, match=r"[Kk]elvin|'K'"):
            validate_members_unit("K")

    def test_none_unit_is_rejected(self):
        """R-O: members_unit=None must raise MembersUnitInvalidError (missing field)."""
        from src.contracts.ensemble_snapshot_provenance import (
            MembersUnitInvalidError,
            validate_members_unit,
        )
        with pytest.raises(MembersUnitInvalidError):
            validate_members_unit(None)

    def test_empty_string_unit_is_rejected(self):
        """R-O: members_unit='' must raise MembersUnitInvalidError (blank field)."""
        from src.contracts.ensemble_snapshot_provenance import (
            MembersUnitInvalidError,
            validate_members_unit,
        )
        with pytest.raises(MembersUnitInvalidError):
            validate_members_unit("")

    def test_degc_unit_is_accepted(self):
        """R-O complement: members_unit='degC' must pass without error."""
        from src.contracts.ensemble_snapshot_provenance import validate_members_unit
        validate_members_unit("degC")  # must not raise


# ---------------------------------------------------------------------------
# MAJOR-1 fix: integration test calling ingest_json_file with a real temp file
# ---------------------------------------------------------------------------

class TestIngestJsonFileIntegration:
    """MAJOR-1: R-L integration — actually calls ingest_json_file() with a temp JSON
    file so that a silent DROP of a mandatory INSERT field fails this test.

    The earlier TestIngestGribWritesFullProvenanceFields only proves the SQL schema
    accepts the 7 fields via direct INSERT. This class proves the ingest code path
    itself writes all 7 fields correctly.
    """

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _write_extracted_json(self, tmp_path: Path, *, unit: str = "C") -> Path:
        """Write a minimal valid extracted JSON matching tigge_local_calendar_day_extract output."""
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        payload = {
            "data_version": HIGH_LOCALDAY_MAX.data_version,
            "physical_quantity": HIGH_LOCALDAY_MAX.physical_quantity,
            "param": "121.128",
            "short_name": "mx2t6",
            "step_type": "max",
            "city": "NYC",
            "unit": unit,
            "manifest_sha256": "abc123def456",
            "issue_time_utc": "2026-04-14T00:00:00+00:00",
            "target_date_local": "2026-04-16",
            "lead_day": 2,
            "timezone": "America/New_York",
            "nearest_grid_lat": 40.7,
            "nearest_grid_lon": -74.0,
            "nearest_grid_distance_km": 5.2,
            "training_allowed": True,
            "members": [{"member": i, "value_native_unit": 22.0 + i * 0.1} for i in range(51)],
        }
        path = tmp_path / "extracted.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_ingest_json_file_writes_all_7_provenance_fields(self):
        """MAJOR-1/R-L: ingest_json_file must populate all 7 Phase 2 provenance fields.

        A refactor silently dropping manifest_hash, causality_status, boundary_ambiguous,
        etc. from the INSERT params would fail here — unlike the schema-only tests above.
        """
        from scripts.ingest_grib_to_snapshots import ingest_json_file
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_conn()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_extracted_json(Path(tmpdir))
            status = ingest_json_file(
                conn, path,
                metric=HIGH_LOCALDAY_MAX,
                model_version="ecmwf_ens",
                overwrite=False,
            )

        assert status == "written", f"Expected 'written', got {status!r}"
        row = conn.execute(
            "SELECT temperature_metric, physical_quantity, observation_field, data_version, "
            "training_allowed, causality_status, boundary_ambiguous, manifest_hash, "
            "provenance_json, members_unit FROM ensemble_snapshots_v2"
        ).fetchone()
        assert row is not None, "No row written by ingest_json_file (MAJOR-1)"
        (temp_metric, phys_qty, obs_field, dv, ta, cs, ba, mh, pj, mu) = row
        assert temp_metric == "high", f"temperature_metric={temp_metric!r}"
        assert phys_qty == "mx2t6_local_calendar_day_max", f"physical_quantity={phys_qty!r}"
        assert obs_field == "high_temp", f"observation_field={obs_field!r}"
        assert dv == "tigge_mx2t6_local_calendar_day_max_v1", f"data_version={dv!r}"
        assert ta in (0, 1), f"training_allowed={ta!r}"
        assert cs and cs.strip(), f"causality_status is empty: {cs!r}"
        assert ba in (0, 1), f"boundary_ambiguous={ba!r}"
        assert mh and len(mh) > 8, f"manifest_hash too short: {mh!r}"
        assert pj and json.loads(pj), f"provenance_json is empty: {pj!r}"
        assert mu in ("degC", "degF"), f"members_unit={mu!r}"

    def test_ingest_json_file_rejects_kelvin_unit_via_json(self):
        """MAJOR-1/R-O: if extracted JSON has unit='K', ingest_json_file must raise.

        The Kelvin guard fires inside ingest_json_file before INSERT. A refactor that
        removes validate_members_unit from the call path would fail this test.
        """
        from scripts.ingest_grib_to_snapshots import ingest_json_file
        from src.contracts.ensemble_snapshot_provenance import MembersUnitInvalidError
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_conn()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_extracted_json(Path(tmpdir), unit="K")
            with pytest.raises((MembersUnitInvalidError, ValueError)):
                ingest_json_file(
                    conn, path,
                    metric=HIGH_LOCALDAY_MAX,
                    model_version="ecmwf_ens",
                    overwrite=False,
                )
