# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/
#                  DESIGN_PHASE2_5_TRANSFER_POLICY_REPLACEMENT.md
#                  + may4math.md Finding 2 (transfer policy needs OOS evidence)
"""Phase 2.5 contract tests: ForecastCalibrationDomain + evaluate_calibration_transfer.

These are RELATIONSHIP tests, not function tests:
    * exact-match domain → LIVE_ELIGIBLE without consulting validated_transfers
    * categorically-invalid (06z full-horizon) domain → BLOCK regardless of evidence
    * mismatched domain with no evidence row → SHADOW_ONLY
    * mismatched domain with valid evidence row → LIVE_ELIGIBLE + transfer_id
    * mismatched domain with expired evidence (require_unexpired=True) → SHADOW_ONLY
    * mismatched domain with UNVERIFIED authority + minimum=VERIFIED → SHADOW_ONLY
"""

from __future__ import annotations

import sqlite3

import pytest

from src.calibration.forecast_calibration_domain import (
    ForecastCalibrationDomain,
    derive_source_id_from_data_version,
    parse_cycle_from_issue_time,
)
from src.data.calibration_transfer_policy import evaluate_calibration_transfer


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE validated_calibration_transfers (
            transfer_id TEXT PRIMARY KEY,
            train_source_id TEXT, train_cycle_hour_utc TEXT,
            train_horizon_profile TEXT, train_data_version TEXT,
            train_metric TEXT, train_season TEXT,
            test_source_id TEXT, test_cycle_hour_utc TEXT,
            test_horizon_profile TEXT, test_data_version TEXT,
            test_metric TEXT, test_season TEXT,
            n_test_pairs INTEGER NOT NULL,
            validated_at TEXT NOT NULL,
            validated_by TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            expires_at TEXT
        );
        """
    )
    return conn


def _domain(
    *,
    source_id="tigge_mars",
    cycle="00",
    horizon="full",
    metric="high",
    season="JJA",
    data_version="tigge_mx2t6_local_calendar_day_max_v1",
) -> ForecastCalibrationDomain:
    return ForecastCalibrationDomain(
        source_id=source_id,
        cycle_hour_utc=cycle,
        horizon_profile=horizon,
        metric=metric,
        season=season,
        data_version=data_version,
    )


def test_exact_match_returns_live_eligible_without_table_lookup():
    """Exact-match domain is LIVE_ELIGIBLE even with no validated_transfers row.

    Relationship: forecast_domain == calibrator_domain bypasses the transfer
    table entirely. The calibrator was *trained on this exact slice*, so no
    cross-domain evidence is required.
    """
    conn = _make_conn()
    d = _domain()
    res = evaluate_calibration_transfer(
        conn, forecast_domain=d, calibrator_domain=d
    )
    assert res.status == "LIVE_ELIGIBLE"
    assert res.reason_codes == ("DOMAIN_EXACT_MATCH",)
    assert res.matched_transfer_id is None


def test_categorically_invalid_06z_full_horizon_blocks():
    """06z + full-horizon is hard-blocked regardless of evidence.

    TIGGE archive does not carry 06z full-horizon ENS (240+ lead). This must
    BLOCK before the table is even consulted — the data physically does not
    exist for this slice.
    """
    conn = _make_conn()
    bad = _domain(cycle="06", horizon="full")
    good = _domain()
    res = evaluate_calibration_transfer(
        conn, forecast_domain=bad, calibrator_domain=good
    )
    assert res.status == "BLOCK"
    assert "FORECAST_DOMAIN_CATEGORICALLY_INVALID" in res.reason_codes


def test_mismatched_domain_no_evidence_returns_shadow_only():
    """Mismatched domains with no validated_transfers row → SHADOW_ONLY.

    This is the **default** outcome for any new source/cycle combo until OOS
    evidence is collected. Phase 2.5's whole point is making the absence-of-
    evidence case explicit and refusing live sizing in that state.
    """
    conn = _make_conn()
    forecast = _domain(source_id="ecmwf_open_data", cycle="12")
    calibrator = _domain(source_id="tigge_mars", cycle="00")
    res = evaluate_calibration_transfer(
        conn, forecast_domain=forecast, calibrator_domain=calibrator
    )
    assert res.status == "SHADOW_ONLY"
    assert res.reason_codes == ("NO_VALIDATED_TRANSFER",)


def test_mismatched_domain_with_verified_evidence_returns_live_eligible():
    conn = _make_conn()
    forecast = _domain(source_id="ecmwf_open_data", cycle="12",
                       data_version="ecmwf_opendata_mx2t6_local_calendar_day_max_v1")
    calibrator = _domain(source_id="tigge_mars", cycle="00",
                         data_version="tigge_mx2t6_local_calendar_day_max_v1")
    conn.execute(
        """
        INSERT INTO validated_calibration_transfers
        (transfer_id,
         train_source_id, train_cycle_hour_utc, train_horizon_profile,
         train_data_version, train_metric, train_season,
         test_source_id, test_cycle_hour_utc, test_horizon_profile,
         test_data_version, test_metric, test_season,
         n_test_pairs, validated_at, validated_by, authority, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tx-001",
            "tigge_mars", "00", "full",
            "tigge_mx2t6_local_calendar_day_max_v1", "high", "JJA",
            "ecmwf_open_data", "12", "full",
            "ecmwf_opendata_mx2t6_local_calendar_day_max_v1", "high", "JJA",
            500, "2026-05-01T00:00:00+00:00", "operator-fitz", "VERIFIED", None,
        ),
    )
    conn.commit()
    res = evaluate_calibration_transfer(
        conn, forecast_domain=forecast, calibrator_domain=calibrator
    )
    assert res.status == "LIVE_ELIGIBLE"
    assert res.reason_codes == ("VALIDATED_TRANSFER_MATCH",)
    assert res.matched_transfer_id == "tx-001"


def test_unverified_evidence_with_default_minimum_returns_shadow_only():
    """authority='UNVERIFIED' rows do not satisfy minimum_authority='VERIFIED'.

    Relationship: validated_transfers has a flag for shadow-tier evidence
    (UNVERIFIED) vs operator-blessed evidence (VERIFIED). Default
    minimum_authority='VERIFIED' refuses to size on shadow-tier rows.
    """
    conn = _make_conn()
    forecast = _domain(source_id="ecmwf_open_data", cycle="12")
    calibrator = _domain(source_id="tigge_mars", cycle="00")
    conn.execute(
        """
        INSERT INTO validated_calibration_transfers
        (transfer_id,
         train_source_id, train_cycle_hour_utc, train_horizon_profile,
         train_data_version, train_metric, train_season,
         test_source_id, test_cycle_hour_utc, test_horizon_profile,
         test_data_version, test_metric, test_season,
         n_test_pairs, validated_at, validated_by, authority, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tx-002",
            "tigge_mars", "00", "full",
            "tigge_mx2t6_local_calendar_day_max_v1", "high", "JJA",
            "ecmwf_open_data", "12", "full",
            "ecmwf_opendata_mx2t6_local_calendar_day_max_v1", "high", "JJA",
            300, "2026-05-01T00:00:00+00:00", "operator-fitz", "UNVERIFIED", None,
        ),
    )
    conn.commit()
    res = evaluate_calibration_transfer(
        conn, forecast_domain=forecast, calibrator_domain=calibrator
    )
    assert res.status == "SHADOW_ONLY"


def test_missing_validated_transfers_table_returns_shadow_only():
    """If the migration hasn't run, behave as if no evidence exists.

    Conservative degradation: a missing table cannot be confused with a
    populated-but-unmatched table. Both default to SHADOW_ONLY.
    """
    conn = sqlite3.connect(":memory:")  # no table
    forecast = _domain(source_id="ecmwf_open_data")
    calibrator = _domain(source_id="tigge_mars")
    res = evaluate_calibration_transfer(
        conn, forecast_domain=forecast, calibrator_domain=calibrator
    )
    assert res.status == "SHADOW_ONLY"
    assert res.reason_codes == ("VALIDATED_TRANSFERS_TABLE_MISSING",)


def test_parse_cycle_from_issue_time_handles_zulu_and_offset():
    assert parse_cycle_from_issue_time("2026-05-02T12:00:00+00:00") == "12"
    assert parse_cycle_from_issue_time("2026-05-02T00:00:00Z") == "00"
    assert parse_cycle_from_issue_time(None) is None
    assert parse_cycle_from_issue_time("not-a-date") is None
    assert parse_cycle_from_issue_time("2026-05-02T06:00:00") == "06"


def test_derive_source_id_from_data_version_known_prefixes():
    assert derive_source_id_from_data_version(
        "tigge_mx2t6_local_calendar_day_max_v1"
    ) == "tigge_mars"
    assert derive_source_id_from_data_version(
        "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"
    ) == "ecmwf_open_data"
    assert derive_source_id_from_data_version("openmeteo_v1") is None
    assert derive_source_id_from_data_version(None) is None
    assert derive_source_id_from_data_version("") is None
