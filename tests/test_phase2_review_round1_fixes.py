# Created: 2026-05-04
# Last reused/audited: 2026-05-04 (post PR #56 merge)
# Authority basis: PR #55 review round 1 — Copilot reviews #4/#5,
#                  Codex P1 review #7. Authority-threshold tests
#                  (Copilot #1) and transfer-policy tests were dropped
#                  when PR #56 superseded the calibration_transfer_policy
#                  Phase 2.5 stack with MarketPhaseEvidence.
"""Relationship tests for surviving PR #55 review-round-1 fixes.

Covers:
  * Copilot #4 + #5 — horizon_profile must be derived from cycle when
    ens_result producers don't populate it.
  * Codex P1 #7 — issue_time may be a datetime on the registered-ingest
    path, not just a str; phase-2 cycle extraction must handle both.

Both fixes live in
``src.calibration.forecast_calibration_domain.derive_phase2_keys_from_ens_result``,
which both evaluator and monitor_refresh delegate to.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.calibration.forecast_calibration_domain import (
    derive_phase2_keys_from_ens_result,
)


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
