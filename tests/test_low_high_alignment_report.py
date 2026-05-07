# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Lifecycle: created=2026-05-07; last_reviewed=2026-05-07; last_reused=2026-05-07
# Authority basis: LOW/HIGH alignment recovery diagnostic plan; derived report only.
# Purpose: Lock read-only before/after LOW/HIGH alignment report semantics.
# Reuse: Re-check scripts/diagnose_low_high_alignment.py output schema before relying on tests.
"""Tests for scripts.low_high_alignment_report."""

from __future__ import annotations

import sqlite3

from scripts.diagnose_low_high_alignment import _connect_for_tests, build_report


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _connect_for_tests(conn)
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            issue_time TEXT,
            source_id TEXT,
            temperature_metric TEXT,
            data_version TEXT,
            training_allowed INTEGER,
            causality_status TEXT
        );

        CREATE TABLE platt_models_v2 (
            model_key TEXT PRIMARY KEY,
            temperature_metric TEXT,
            cluster TEXT,
            season TEXT,
            data_version TEXT,
            input_space TEXT,
            n_samples INTEGER,
            authority TEXT,
            is_active INTEGER,
            param_A REAL,
            cycle TEXT,
            source_id TEXT,
            horizon_profile TEXT
        );
        """
    )
    return conn


def test_report_marks_fresh_v2_schema_contract_outcome_ready() -> None:
    conn = sqlite3.connect(":memory:")
    _connect_for_tests(conn)
    from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415

    apply_v2_schema(conn)

    report = build_report(
        conn,
        db_path=":memory:",
        generated_at="2026-05-07T00:00:00+00:00",
        city_limit=10,
        quarantine_limit=10,
    )

    schema_gap = report["after_contract_recovery_candidate"]["contract_evidence_schema_gap"]
    assert schema_gap["contract_outcome_ready"] is True
    assert schema_gap["missing_required_fields"] == []
    persisted = report["after_contract_recovery_candidate"]["persisted_low_window_evidence"]
    assert persisted["schema_ready"] is True
    assert persisted["contract_proven_training_candidates"] == 0


def test_report_counts_persisted_low_window_evidence_classes() -> None:
    conn = sqlite3.connect(":memory:")
    _connect_for_tests(conn)
    from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415

    apply_v2_schema(conn)
    rows = [
        (
            "FULLY_INSIDE_TARGET_LOCAL_DAY",
            1,
            "[]",
            "2026-06-10T00:00:00+08:00",
            "2026-06-10T06:00:00+08:00",
        ),
        (
            "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY",
            0,
            '["ambiguous_crosses_local_day_boundary"]',
            "2026-06-09T22:00:00+08:00",
            "2026-06-10T04:00:00+08:00",
        ),
        (
            "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY",
            0,
            '["deterministic_reassignment_requires_revision"]',
            "2026-06-09T18:00:00+08:00",
            "2026-06-10T00:00:00+08:00",
        ),
    ]
    for idx, (status, contributes, reasons, start_local, end_local) in enumerate(rows, start=10):
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2 (
                city, target_date, temperature_metric, physical_quantity, observation_field,
                issue_time, available_at, fetch_time, lead_hours, members_json,
                model_version, data_version, training_allowed, causality_status,
                authority, members_unit, city_timezone, settlement_source_type,
                settlement_station_id, settlement_unit, settlement_rounding_policy,
                bin_grid_id, bin_schema_version, forecast_window_start_utc,
                forecast_window_end_utc, forecast_window_start_local,
                forecast_window_end_local, forecast_window_local_day_overlap_hours,
                forecast_window_attribution_status, contributes_to_target_extrema,
                forecast_window_block_reasons_json
            ) VALUES (
                'Kuala Lumpur', ?, 'low', 'mn2t6_local_calendar_day_min', 'low_temp',
                '2026-06-09T00:00:00+00:00', '2026-06-09T08:00:00+00:00',
                '2026-06-09T08:05:00+00:00', 48.0, '[24.0, 24.5]',
                'ENS', 'tigge_mn2t6_local_calendar_day_min_v1', 0, 'OK',
                'VERIFIED', 'degC', 'Asia/Kuala_Lumpur', 'wu_icao',
                'WMKK', 'C', 'wmo_half_up',
                'C_canonical_v1', 'canonical_bin_grid_v1',
                '2026-06-09T18:00:00+00:00',
                '2026-06-10T00:00:00+00:00',
                ?, ?, 6.0, ?, ?, ?
            )
            """,
            (f"2026-06-{idx}", start_local, end_local, status, contributes, reasons),
        )

    report = build_report(
        conn,
        db_path=":memory:",
        generated_at="2026-05-07T00:00:00+00:00",
        city_limit=10,
        quarantine_limit=10,
    )

    persisted = report["after_contract_recovery_candidate"]["persisted_low_window_evidence"]
    assert persisted["schema_ready"] is True
    assert persisted["low_rows"] == 3
    assert persisted["contract_proven_training_candidates"] == 1
    assert persisted["ambiguous_blocked"] == 1
    assert persisted["deterministic_reassignment_candidates"] == 1


def test_report_quantifies_low_boundary_recovery_upper_bound() -> None:
    conn = _make_conn()
    conn.executemany(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, data_version,
            training_allowed, causality_status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("Kuala Lumpur", "2026-06-10", "low", "tigge_mn2t6_v1", 1, "OK"),
            ("Kuala Lumpur", "2026-06-11", "low", "tigge_mn2t6_v1", 0, "REJECTED_BOUNDARY_AMBIGUOUS"),
            ("Kuala Lumpur", "2026-06-12", "low", "tigge_mn2t6_v1", 0, "REJECTED_BOUNDARY_AMBIGUOUS"),
            ("Jakarta", "2026-06-10", "low", "ecmwf_opendata_mn2t6_v1", 0, "REJECTED_BOUNDARY_AMBIGUOUS"),
            ("Tokyo", "2026-06-10", "high", "tigge_mx2t6_v1", 1, "OK"),
            ("Tokyo", "2026-06-11", "high", "tigge_mx2t6_v1", 1, "OK"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO platt_models_v2 (
            model_key, temperature_metric, cluster, season, data_version,
            input_space, n_samples, authority, is_active, param_A,
            cycle, source_id, horizon_profile
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("high_tokyo", "high", "Tokyo", "JJA", "tigge_mx2t6_v1", "width_normalized_density", 200, "VERIFIED", 1, 1.0, "00", "tigge_mars", "full"),
            ("low_kl", "low", "Kuala Lumpur", "JJA", "tigge_mn2t6_v1", "width_normalized_density", 25, "VERIFIED", 1, 1.0, "00", "tigge_mars", "full"),
            ("low_bad", "low", "Jakarta", "JJA", "tigge_mn2t6_v1", "width_normalized_density", 18, "QUARANTINED", 1, -0.2, "00", "tigge_mars", "full"),
        ],
    )

    report = build_report(
        conn,
        db_path=":memory:",
        generated_at="2026-05-07T00:00:00+00:00",
        city_limit=10,
        quarantine_limit=10,
    )

    assert report["derived_context_only"] is True
    assert report["live_behavior_changed"] is False
    after = report["after_contract_recovery_candidate"]
    assert after["safe_without_persisted_window_metadata"]["additional_training_snapshots"] == 0
    policy = after["low_recovery_data_version_policy"]
    assert policy["metric_axis"] == "low"
    assert "contract-window evidence" in policy["pair_rebuild_requirement"]
    assert policy["live_promotion"] == "not_authorized_by_this_report"
    upper = after["upper_bound_if_all_boundary_rejections_became_contract_proven"]
    assert upper["low_baseline_training_snapshots"] == 1
    assert upper["low_boundary_rejected_snapshots"] == 3
    assert upper["low_upper_bound_training_snapshots"] == 4
    schema_gap = after["contract_evidence_schema_gap"]
    assert schema_gap["contract_outcome_ready"] is False
    assert schema_gap["alias_satisfied_fields"]["target_local_date"] == "target_date"
    assert "forecast_window_start_utc" in schema_gap["missing_required_fields"]
    assert after["persisted_low_window_evidence"]["schema_ready"] is False


def test_report_exposes_quarantined_negative_a_and_no_regression_gates() -> None:
    conn = _make_conn()
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, data_version,
            training_allowed, causality_status
        ) VALUES ('Jakarta', '2026-06-10', 'low', 'tigge_mn2t6_v1', 0, 'REJECTED_BOUNDARY_AMBIGUOUS')
        """
    )
    conn.execute(
        """
        INSERT INTO platt_models_v2 (
            model_key, temperature_metric, cluster, season, data_version,
            input_space, n_samples, authority, is_active, param_A,
            cycle, source_id, horizon_profile
        ) VALUES (
            'low_jakarta_bad', 'low', 'Jakarta', 'JJA', 'tigge_mn2t6_v1',
            'width_normalized_density', 18, 'QUARANTINED', 1, -0.4,
            '00', 'tigge_mars', 'full'
        )
        """
    )

    report = build_report(
        conn,
        db_path=":memory:",
        generated_at="2026-05-07T00:00:00+00:00",
        city_limit=10,
        quarantine_limit=10,
    )

    quarantined = report["before_current_baseline"]["quarantined_negative_a_active"]
    assert quarantined[0]["model_key"] == "low_jakarta_bad"
    gates = set(report["after_contract_recovery_candidate"]["no_regression_gates"])
    assert "no_adjacent_day_low_into_target_day_training" in gates
    assert "trade_drift_required_before_live_promotion" in gates
