# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: Phase 2 migration 2026-05-05; build_platt_refit_preflight_report schema gate.
"""Tests for the Phase 2 schema gate in build_platt_refit_preflight_report.

Stage6 must fail closed if calibration_pairs or platt_models lack the
cycle/source_id/horizon_profile stratification columns added by the Phase 2
migration. Without this gate, stage7 (refit) crashes silently with an
OperationalError on a regressed schema.
"""
import sqlite3

import pytest

from src.state.db import init_schema
from src.state.schema.v2_schema import apply_canonical_schema
from scripts.verify_truth_surfaces import build_platt_refit_preflight_report


def _legacy_calibration_pairs(conn):
    """Create calibration_pairs WITHOUT Phase 2 stratification columns."""
    conn.execute(
        """
        CREATE TABLE calibration_pairs (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            observation_field TEXT,
            range_label TEXT,
            p_raw REAL,
            outcome INTEGER,
            lead_days REAL,
            season TEXT,
            cluster TEXT,
            forecast_available_at TEXT,
            settlement_value REAL,
            decision_group_id TEXT,
            authority TEXT,
            bin_source TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            causality_status TEXT
        )
        """
    )


def _legacy_platt_models(conn):
    """Create platt_models WITHOUT Phase 2 stratification columns (6-tuple UNIQUE)."""
    conn.execute(
        """
        CREATE TABLE platt_models (
            model_id INTEGER PRIMARY KEY AUTOINCREMENT,
            temperature_metric TEXT,
            cluster TEXT,
            season TEXT,
            data_version TEXT,
            input_space TEXT,
            is_active INTEGER,
            UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)
        )
        """
    )


def _phase2_calibration_pairs(conn):
    """Create calibration_pairs WITH Phase 2 stratification columns."""
    conn.execute(
        """
        CREATE TABLE calibration_pairs (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            observation_field TEXT,
            range_label TEXT,
            p_raw REAL,
            outcome INTEGER,
            lead_days REAL,
            season TEXT,
            cluster TEXT,
            forecast_available_at TEXT,
            settlement_value REAL,
            decision_group_id TEXT,
            authority TEXT,
            bin_source TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            cycle TEXT NOT NULL DEFAULT '00',
            source_id TEXT NOT NULL DEFAULT 'tigge_mars',
            horizon_profile TEXT NOT NULL DEFAULT 'full'
        )
        """
    )


def _phase2_platt_models(conn):
    """Create platt_models WITH Phase 2 stratification columns + 9-tuple UNIQUE."""
    conn.execute(
        """
        CREATE TABLE platt_models (
            model_id INTEGER PRIMARY KEY AUTOINCREMENT,
            temperature_metric TEXT,
            cluster TEXT,
            season TEXT,
            data_version TEXT,
            input_space TEXT,
            is_active INTEGER,
            cycle TEXT NOT NULL DEFAULT '00',
            source_id TEXT NOT NULL DEFAULT 'tigge_mars',
            horizon_profile TEXT NOT NULL DEFAULT 'full',
            UNIQUE(temperature_metric, cluster, season, data_version,
                   input_space, is_active, cycle, source_id, horizon_profile)
        )
        """
    )


class TestPhase2SchemaGate:
    def test_preflight_aborts_when_phase2_columns_missing(self, tmp_path):
        """Stage6 aborts with a specific verdict when calibration tables lack Phase 2 columns."""
        db_path = tmp_path / "legacy_schema.db"
        conn = sqlite3.connect(db_path)
        _legacy_calibration_pairs(conn)
        _legacy_platt_models(conn)
        conn.commit()
        conn.close()

        report = build_platt_refit_preflight_report(db_path)

        assert report["ready"] is False
        assert report["verdict"] == "aborted_phase2_migration_unapplied"
        assert "calibration_pairs" in report["reason"]
        assert report["table"] == "calibration_pairs"
        assert set(report["missing_columns"]).issubset({"cycle", "source_id", "horizon_profile"})

    def test_preflight_aborts_when_platt_unique_legacy(self, tmp_path):
        """Stage6 aborts when calibration_pairs has Phase 2 cols but platt UNIQUE is legacy."""
        db_path = tmp_path / "platt_legacy_unique.db"
        conn = sqlite3.connect(db_path)
        _phase2_calibration_pairs(conn)
        _legacy_platt_models(conn)
        conn.commit()
        conn.close()

        report = build_platt_refit_preflight_report(db_path)

        assert report["ready"] is False
        # platt_models also lacks Phase 2 columns — column check fires first
        assert report["verdict"] in (
            "aborted_phase2_migration_unapplied",
            "aborted_platt_unique_not_extended",
        )

    def test_preflight_aborts_platt_unique_not_extended_when_cols_present_but_unique_legacy(
        self, tmp_path
    ):
        """Stage6 aborts with platt_unique verdict when columns exist but UNIQUE is still 6-tuple."""
        db_path = tmp_path / "platt_cols_no_unique.db"
        conn = sqlite3.connect(db_path)
        _phase2_calibration_pairs(conn)
        # platt_models has Phase 2 columns but UNIQUE is still the legacy 6-tuple
        conn.execute(
            """
            CREATE TABLE platt_models (
                model_id INTEGER PRIMARY KEY AUTOINCREMENT,
                temperature_metric TEXT,
                cluster TEXT,
                season TEXT,
                data_version TEXT,
                input_space TEXT,
                is_active INTEGER,
                cycle TEXT NOT NULL DEFAULT '00',
                source_id TEXT NOT NULL DEFAULT 'tigge_mars',
                horizon_profile TEXT NOT NULL DEFAULT 'full',
                UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)
            )
            """
        )
        conn.commit()
        conn.close()

        report = build_platt_refit_preflight_report(db_path)

        assert report["ready"] is False
        assert report["verdict"] == "aborted_platt_unique_not_extended"

    def test_preflight_proceeds_when_phase2_schema_correct(self, tmp_path):
        """Phase 2 gate passes (does not abort) when DB was set up via apply_canonical_schema."""
        db_path = tmp_path / "phase2_correct.db"
        conn = sqlite3.connect(db_path)
        init_schema(conn)
        apply_canonical_schema(conn)
        conn.commit()
        conn.close()

        report = build_platt_refit_preflight_report(db_path)

        # Must NOT have been aborted by the new gate
        assert report.get("verdict") not in (
            "aborted_phase2_migration_unapplied",
            "aborted_platt_unique_not_extended",
        )
        # ready may still be False (empty tables) — that's fine; we only verify
        # the Phase 2 gate did not fire
