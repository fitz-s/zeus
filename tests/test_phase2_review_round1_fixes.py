# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: PR #55 review round 1 — Copilot reviews #1/#4/#5,
#                  Codex P1 reviews #6/#7
"""Relationship tests for the round-1 PR review fixes.

Each test pins a specific reviewer finding so a future regression that
re-introduces the same bug fails CI.

Round-1 findings covered here:
  * Copilot #1 — authority filter must be threshold (>=), not exact match.
  * Copilot #4 + #5 — horizon_profile must be derived from cycle when
    ens_result producers don't populate it.
  * Codex P1 #7 — issue_time may be a datetime on the registered-ingest
    path, not just a str; phase-2 cycle extraction must handle both.

Other round-1 findings (Copilot #2 store loader, Copilot #3 ensemble_client
guard, Codex P1 #6 transfer-gate calibrator domain) are exercised in
test_phase2_review_round1_fixes_part2.py to keep this file focused.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.calibration.forecast_calibration_domain import (
    ForecastCalibrationDomain,
    derive_phase2_keys_from_ens_result,
)
from src.data.calibration_transfer_policy import (
    _AUTHORITY_RANK,
    _authority_threshold_clause,
    evaluate_calibration_transfer,
)


# ---- Copilot review #1 — authority threshold ordering ---------------------


def test_authority_threshold_clause_unverified_admits_verified():
    clause, params = _authority_threshold_clause("UNVERIFIED")
    assert "authority IN" in clause
    assert "UNVERIFIED" in params
    assert "VERIFIED" in params


def test_authority_threshold_clause_verified_excludes_unverified():
    clause, params = _authority_threshold_clause("VERIFIED")
    assert "authority IN" in clause
    assert "VERIFIED" in params
    assert "UNVERIFIED" not in params


def test_authority_threshold_clause_unknown_tier_fails_closed():
    clause, params = _authority_threshold_clause("FAKE_TIER")
    # Unknown tier → exact-match (so an operator typo never accidentally
    # widens the policy by matching no rows in IN(...)).
    assert "authority = ?" in clause
    assert params == ["FAKE_TIER"]


def test_authority_rank_includes_known_tiers():
    # Guard against silent removal of either tier — would skew the SQL.
    assert _AUTHORITY_RANK["UNVERIFIED"] < _AUTHORITY_RANK["VERIFIED"]


def _make_validated_transfers_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE validated_calibration_transfers (
            transfer_id TEXT PRIMARY KEY,
            train_source_id TEXT, train_cycle_hour_utc TEXT,
            train_horizon_profile TEXT, train_data_version TEXT,
            train_metric TEXT, train_season TEXT,
            test_source_id TEXT, test_cycle_hour_utc TEXT,
            test_horizon_profile TEXT, test_data_version TEXT,
            test_metric TEXT, test_season TEXT,
            authority TEXT,
            validated_at TEXT,
            expires_at TEXT
        )
        """
    )


def _domain(source_id: str, cycle: str, dv: str) -> ForecastCalibrationDomain:
    return ForecastCalibrationDomain(
        source_id=source_id,
        cycle_hour_utc=cycle,
        horizon_profile="full",
        metric="high",
        season="winter",
        data_version=dv,
        # `season` is part of the key but not categorically invalid.
    )


def test_evaluate_transfer_unverified_admits_verified_row():
    """Caller passing minimum_authority='UNVERIFIED' should match a stored
    row whose authority is VERIFIED.  Pre-fix this returned SHADOW_ONLY
    because the SQL filter was `authority = ?` (exact-match).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_validated_transfers_table(conn)
    conn.execute(
        """
        INSERT INTO validated_calibration_transfers VALUES
        ('xfer-1','tigge_mars','00','full','tigge_high_v1','high','winter',
         'ecmwf_open_data','12','full','ecmwf_opendata_high_v1','high','winter',
         'VERIFIED','2026-05-04T00:00:00','2026-12-31T00:00:00')
        """
    )
    forecast = _domain("ecmwf_open_data", "12", "ecmwf_opendata_high_v1")
    calibrator = _domain("tigge_mars", "00", "tigge_high_v1")
    result = evaluate_calibration_transfer(
        conn,
        forecast_domain=forecast,
        calibrator_domain=calibrator,
        minimum_authority="UNVERIFIED",
    )
    assert result.status == "LIVE_ELIGIBLE", (
        f"Copilot #1 regression: UNVERIFIED minimum should admit VERIFIED row. "
        f"Got {result.status} ({result.reason_codes})"
    )
    assert result.matched_transfer_id == "xfer-1"


# ---- Copilot reviews #4 + #5 + Codex P1 #7 — phase2 keys derivation -------


def test_derive_phase2_keys_from_str_issue_time_full_horizon():
    ens = {
        "issue_time": "2026-05-04T12:00:00",
        "source_id": "ecmwf_open_data",
    }
    cycle, sid, horizon = derive_phase2_keys_from_ens_result(ens)
    assert cycle == "12"
    assert sid == "ecmwf_open_data"
    assert horizon == "full", "Copilot #4/#5 regression: horizon must derive from cycle"


def test_derive_phase2_keys_from_str_issue_time_short_horizon():
    ens = {
        "issue_time": "2026-05-04T06:00:00",
        "source_id": "ecmwf_open_data",
    }
    cycle, sid, horizon = derive_phase2_keys_from_ens_result(ens)
    assert cycle == "06"
    assert horizon == "short"


def test_derive_phase2_keys_from_datetime_issue_time():
    """Codex P1 #7 — registered-ingest path puts datetime in issue_time.

    Pre-fix: only str was handled, datetime silently → cycle=None →
    schema-default 00z bucket (silent misroute for 12z runs).
    """
    ens = {
        "issue_time": datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc),
        "source_id": "ecmwf_open_data",
    }
    cycle, sid, horizon = derive_phase2_keys_from_ens_result(ens)
    assert cycle == "12", (
        "Codex P1 #7 regression: datetime issue_time must produce cycle='12'"
    )
    assert horizon == "full"


def test_derive_phase2_keys_explicit_horizon_wins_over_derived():
    """When ens_result *does* populate horizon_profile, respect it — don't
    overwrite with the cycle-derived value (forwards-compat for upstream
    producers that learn to populate the field properly)."""
    ens = {
        "issue_time": "2026-05-04T12:00:00",
        "source_id": "ecmwf_open_data",
        "horizon_profile": "short",  # explicit override
    }
    _, _, horizon = derive_phase2_keys_from_ens_result(ens)
    assert horizon == "short"


def test_derive_phase2_keys_returns_none_for_malformed():
    assert derive_phase2_keys_from_ens_result(None) == (None, None, None)
    assert derive_phase2_keys_from_ens_result({}) == (None, None, None)
    assert derive_phase2_keys_from_ens_result(
        {"issue_time": "garbage", "source_id": ""}
    ) == (None, None, None)


# ---- Cross-module relationship: evaluator + monitor_refresh use the helper -


def test_evaluator_imports_derive_phase2_keys_from_ens_result():
    """Both evaluator and monitor_refresh must delegate to the shared helper.

    Structural assertion locks Copilot #4/#5 + Codex P1 #7 — if a future
    refactor re-inlines the issue_time parsing without datetime support,
    this test fails.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    evaluator_src = (root / "src" / "engine" / "evaluator.py").read_text(encoding="utf-8")
    monitor_src = (root / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
    assert "derive_phase2_keys_from_ens_result" in evaluator_src, (
        "Copilot/Codex review fix regression: evaluator no longer uses shared "
        "phase-2-key derivation helper"
    )
    assert "derive_phase2_keys_from_ens_result" in monitor_src, (
        "Copilot/Codex review fix regression: monitor_refresh no longer uses "
        "shared phase-2-key derivation helper"
    )
