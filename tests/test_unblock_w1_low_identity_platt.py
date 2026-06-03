# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: docs/operations task WAVE-1 (unblock-W1) W1-T2 — LOW
#   identity-Platt registration. LOW metric returns (None, 4) from
#   get_calibrator (src/calibration/manager.py ~959) → the receipt dies at
#   CALIBRATION_AUTHORITY_EVIDENCE_MISSING. This registers identity-Platt rows
#   for LOW (calibration_method='identity_full_transport_v1', the SAME route
#   HIGH already uses) so LOW returns (cal, 1). NOT EMOS (EMOS is HIGH-only).
"""W1-T2 RED relationship test (RT-3): LOW identity-Platt route.

A LOW family → get_calibrator(metric='low') returns (cal, 1) with
calibration_method='identity_full_transport_v1', NOT (None, 4); no
CALIBRATION_AUTHORITY_EVIDENCE_MISSING. RED today: with no LOW Platt rows the
LOW primary-bucket lookup misses and get_calibrator falls to
``if temperature_metric == "low": return None, 4``.

The register script (scripts/register_low_identity_platt.py) writes the
identity rows; this test invokes its row-registration entry against a temp DB
(NEVER live world.db) and asserts the resulting (cal, level).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.calibration.manager import get_calibrator
from src.calibration.platt import IdentityCalibrator, IDENTITY_CALIBRATION_METHOD
from src.config import City
from src.state.db import get_connection, init_schema
from src.state.schema.v2_schema import apply_canonical_schema

_NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="NYC",
    settlement_unit="F", wu_station="KLGA",
)
_TARGET_DATE = "2026-06-15"  # JJA season for NYC (NH)


def _make_conn(tmp_path: Path, name: str = "low_identity") -> sqlite3.Connection:
    conn = get_connection(tmp_path / f"{name}.db")
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


class TestRT3LowIdentityRoute:
    def test_red_low_returns_none_4_before_registration(self, tmp_path):
        """RED baseline: with NO LOW Platt rows, get_calibrator('low') → (None, 4)."""
        conn = _make_conn(tmp_path)
        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="low")
        assert cal is None and level == 4, (
            f"RED expected (None, 4) before registration, got ({cal!r}, {level}). "
            "If this is no longer (None,4), the LOW gap has changed."
        )

    def test_green_low_returns_identity_level1_after_registration(self, tmp_path):
        """After the register script writes LOW identity rows, get_calibrator('low')
        → (IdentityCalibrator, 1) with calibration_method=identity_full_transport_v1."""
        from scripts.register_low_identity_platt import register_low_identity_rows

        conn = _make_conn(tmp_path)
        # source_id=None → legacy TIGGE LOW data_version bucket (00/tigge_mars/full).
        n = register_low_identity_rows(
            conn,
            clusters=[_NYC.cluster],
            seasons=["JJA"],
            dry_run=False,
        )
        assert n >= 1, "register_low_identity_rows wrote zero rows"

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="low")
        assert cal is not None, (
            "LOW still returned None after identity registration "
            "(CALIBRATION_AUTHORITY_EVIDENCE_MISSING would still fire)."
        )
        assert level == 1, f"expected level=1 for identity LOW, got {level}"
        assert isinstance(cal, IdentityCalibrator), (
            f"expected IdentityCalibrator, got {type(cal).__name__}"
        )
        assert getattr(cal, "calibration_method", None) == IDENTITY_CALIBRATION_METHOD

    def test_dry_run_writes_nothing(self, tmp_path):
        """--dry-run path must NOT mutate the DB (LOW stays (None, 4))."""
        from scripts.register_low_identity_platt import register_low_identity_rows

        conn = _make_conn(tmp_path)
        planned = register_low_identity_rows(
            conn,
            clusters=[_NYC.cluster],
            seasons=["JJA"],
            dry_run=True,
        )
        assert planned >= 1, "dry-run should report ≥1 planned row"
        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="low")
        assert cal is None and level == 4, (
            "dry-run must not write rows; LOW should still be (None, 4)."
        )

    def test_rows_visible_under_frozen_pin(self, tmp_path):
        """Rows are written with recorded_at <= frozen_as_of so the frozen-pin
        load filter (store.py: AND recorded_at<=?) does not make them invisible.

        We assert directly that recorded_at predates the canonical pin instant.
        """
        from scripts.register_low_identity_platt import (
            FROZEN_AS_OF,
            register_low_identity_rows,
        )

        conn = _make_conn(tmp_path)
        register_low_identity_rows(
            conn, clusters=[_NYC.cluster], seasons=["JJA"], dry_run=False
        )
        rows = conn.execute(
            "SELECT recorded_at FROM platt_models WHERE temperature_metric='low' "
            "AND calibration_method=?",
            (IDENTITY_CALIBRATION_METHOD,),
        ).fetchall()
        assert rows, "no LOW identity rows found"
        for (recorded_at,) in rows:
            assert recorded_at <= FROZEN_AS_OF, (
                f"recorded_at={recorded_at!r} is after the frozen pin {FROZEN_AS_OF!r}; "
                "the frozen-pin load filter would hide this row."
            )
