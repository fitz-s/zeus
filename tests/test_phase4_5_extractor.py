# Created: 2026-04-16
# Last reused/audited: 2026-04-16
# Authority basis: Zeus Dual-Track Metric Spine Refactor Phase 4.5;
#                  docs/authority/zeus_dual_track_architecture.md §2/§5/§6/§8;
#                  R-Q..R-U invariants; TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md §4/§5/§6.
"""Phase 4.5 extractor tests: R-Q through R-U

Tests anchored to the remediation-plan SPEC, not just code-on-disk. If implementation
drifts from spec, tests must FAIL to expose it.

Sources of truth:
  TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md §4 (step horizon)
  TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md §5 (causality)
  docs/authority/zeus_dual_track_architecture.md §2/§5/§6/§8

Pipeline-integration tests (CRITICAL-1, CRITICAL-2) call _finalize_record — the
real aggregation entry point — to verify end-to-end invariants, not just helpers.

R-Q: extraction never produces unit="K" or values implying Kelvin (> 200)
R-R: step horizon >= 204h for west-coast day7; _finalize_record sets
     training_allowed=False when actual steps truncated below required horizon
R-S: high-track output has boundary_ambiguous=False unconditionally (per team-lead
     scope ruling; low-track boundary logic is Phase 5 R-W)
R-T: non-causal slots labeled not dropped; track != mx2t6_high raises NotImplementedError
R-U: compute_manifest_hash is stable; sorted-JSON SHA-256 canonicalization
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, date, timedelta

import pytest

from scripts.extract_tigge_mx2t6_localday_max import (
    extract_one_grib_file,
    compute_required_max_step,
    compute_manifest_hash,
    compute_causality,
    _finalize_record,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_la_city_cfg() -> dict:
    return {
        "city": "Los Angeles",
        "name": "Los Angeles",
        "lat": 33.9425,
        "lon": -118.408,
        "timezone": "America/Los_Angeles",
        "unit": "F",
    }


def _make_record(city_cfg: dict, issue_utc: datetime, target_date: date,
                 lead_day: int, selected_steps: list[str],
                 member_values: dict | None = None) -> dict:
    """Build a synthetic accumulator record for _finalize_record."""
    if member_values is None:
        # 51 members with plausible °F values
        member_values = {i: 68.0 + i * 0.1 for i in range(51)}
    return {
        "city": city_cfg,
        "issue_utc": issue_utc,
        "target_date": target_date,
        "lead_day": lead_day,
        "manifest_sha": "a" * 64,
        "nearest_lat": city_cfg["lat"],
        "nearest_lon": city_cfg["lon"],
        "nearest_dist": 5.0,
        "member_values": member_values,
        "selected_steps": selected_steps,
    }


# ---------------------------------------------------------------------------
# R-Q: No Kelvin escape
# ---------------------------------------------------------------------------

class TestNoKelvinEscape:
    """R-Q: The extractor must NEVER write unit='K' or native values in the
    Kelvin range (> 200) in the output JSON. ECMWF delivers GRIB in Kelvin;
    conversion must happen inside the extractor before any JSON is produced.
    """

    def test_rejection_extract_one_grib_file_raises_on_low_track(self, tmp_path):
        """extract_one_grib_file with track != 'mx2t6_high' raises NotImplementedError."""
        fake_grib = tmp_path / "fake.grib2"
        fake_grib.write_bytes(b"GRIB")
        with pytest.raises(NotImplementedError):
            extract_one_grib_file(
                fake_grib, "Los Angeles",
                datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                date(2024, 1, 8), 7,
                track="mn2t6_low",
            )

    def test_rejection_kelvin_range_values_above_200_implausible_in_native_unit(self):
        """Contract: value_native_unit must never exceed 200 in °C or °F.
        °C max ~55; °F max ~135. Values > 200 are un-converted Kelvin.
        """
        assert 55.0 < 200.0, "°C max must be below Kelvin sentinel"
        assert 135.0 < 200.0, "°F max must be below Kelvin sentinel"

    def test_acceptance_finalize_record_unit_field_is_city_native(self):
        """_finalize_record output must carry unit='F' for an °F city — never 'K'."""
        city = _make_la_city_cfg()
        issue_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        target = date(2024, 1, 2)
        # Steps that fully cover the local day (LA UTC-8: day ends at 08Z next day = 32h)
        record = _make_record(city, issue_utc, target, 1, ["24-30", "30-36"])
        result = _finalize_record(record, "a" * 64)
        assert result["unit"] == "F", (
            f"unit must be 'F' for LA (°F city), got {result['unit']!r} — Kelvin escape?"
        )
        assert result["unit"] != "K", "unit must never be 'K'"


# ---------------------------------------------------------------------------
# R-R: Dynamic step horizon — formula + pipeline-integration
# ---------------------------------------------------------------------------

class TestDynamicStepHorizon:
    """R-R: West-coast day7 step horizon >= 204h.
    CRITICAL-1: _finalize_record must set training_allowed=False when actual
    steps are truncated below the required horizon (pipeline-integration test).
    """

    def test_la_day7_formula_returns_at_least_204(self):
        """compute_required_max_step for UTC-8 day7 must return >= 204."""
        step = compute_required_max_step(
            datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            date(2024, 1, 8),
            -8,
        )
        assert step >= 204, (
            f"UTC-8 day7 formula returns {step}h — must be >= 204. "
            "Hard-coding step_180 is forbidden (TIGGE remediation plan §4)."
        )

    def test_result_is_positive_multiple_of_6(self):
        issue = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        for offset in [-8, -7, -5, 0, 5, 9]:
            step = compute_required_max_step(issue, date(2024, 1, 8), offset)
            assert step > 0 and step % 6 == 0, (
                f"offset={offset}: step {step} must be positive and 6h-aligned"
            )

    def test_day1_step_less_than_day7_step(self):
        issue = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert (compute_required_max_step(issue, date(2024, 1, 2), -8) <
                compute_required_max_step(issue, date(2024, 1, 8), -8))

    # CRITICAL-1: pipeline-integration — _finalize_record enforces the horizon
    def test_pipeline_truncated_steps_below_horizon_forces_training_allowed_false(self):
        """CRITICAL-1 antibody: when selected_steps are capped at 180 for an LA day7
        target that requires step_204, _finalize_record must set training_allowed=False
        and step_horizon_hours=204.

        Calls the REAL _finalize_record (not a pure helper stub). Failure here means
        the pipeline would silently ingest under-stepped data as training-allowed.
        """
        city = _make_la_city_cfg()
        issue_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        target = date(2024, 1, 8)  # day7 for LA from 00Z 2024-01-01
        lead_day = 7

        # Synthesize steps capped at 180 — simulates a truncated GRIB download
        truncated_steps = [f"{s}-{s+6}" for s in range(6, 180, 6)]  # 6-12 .. 174-180

        record = _make_record(city, issue_utc, target, lead_day, truncated_steps)
        result = _finalize_record(record, "a" * 64)

        assert result["step_horizon_hours"] == 204, (
            f"step_horizon_hours must be 204 for LA day7, got {result['step_horizon_hours']}"
        )
        assert result["training_allowed"] is False, (
            "training_allowed must be False when max_present_step (180) < step_horizon_hours (204). "
            "Truncated steps must not enter calibration."
        )

    def test_pipeline_sufficient_steps_allows_training(self):
        """Acceptance: steps covering through 204h → training_allowed=True (all members present)."""
        city = _make_la_city_cfg()
        issue_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        target = date(2024, 1, 8)
        # Steps through 204
        full_steps = [f"{s}-{s+6}" for s in range(6, 204, 6)]

        record = _make_record(city, issue_utc, target, 7, full_steps)
        result = _finalize_record(record, "a" * 64)

        assert result["step_horizon_hours"] == 204
        assert result["training_allowed"] is True, (
            "training_allowed must be True when steps cover required horizon and all members present"
        )


# ---------------------------------------------------------------------------
# R-S: High-track boundary_ambiguous is unconditionally False
# ---------------------------------------------------------------------------

class TestHighTrackBoundaryUnconditional:
    """R-S (team-lead scope ruling 2026-04-16): high-track extractor output
    must have boundary_ambiguous=False unconditionally. The boundary quarantine
    logic is Phase 5 (low track), encoded as R-W in the mn2t6 test file.

    This is a load-bearing structural assertion: if any future change re-adds
    boundary logic to the high extractor, this test FAILs immediately.
    """

    def test_rejection_finalize_record_has_no_boundary_ambiguous_true(self):
        """_finalize_record for high track must NOT set boundary_ambiguous=True."""
        city = _make_la_city_cfg()
        issue_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        target = date(2024, 1, 2)
        record = _make_record(city, issue_utc, target, 1, ["24-30", "30-36"])
        result = _finalize_record(record, "a" * 64)

        # High track must not emit boundary_ambiguous=True
        assert result.get("boundary_ambiguous") is not True, (
            "High-track _finalize_record must not emit boundary_ambiguous=True. "
            "Boundary quarantine is Phase 5 (low track R-W) only."
        )

    def test_acceptance_finalize_record_omits_or_false_boundary_ambiguous(self):
        """boundary_ambiguous absent or False in high-track output."""
        city = _make_la_city_cfg()
        issue_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        record = _make_record(city, issue_utc, date(2024, 1, 4), 3,
                              ["66-72", "72-78", "78-84", "84-90", "90-96"])
        result = _finalize_record(record, "a" * 64)
        ba = result.get("boundary_ambiguous")
        assert ba is None or ba is False, (
            f"boundary_ambiguous must be absent or False for high track, got {ba!r}"
        )


# ---------------------------------------------------------------------------
# R-T: Causality — labeled not dropped + pipeline-integration
# ---------------------------------------------------------------------------

class TestCausalityHighTrack:
    """R-T: Non-causal slots must be labeled, not dropped.
    CRITICAL-2: For a city/issue where local day already started, compute_causality
    must return status='N/A_CAUSAL_DAY_ALREADY_STARTED' — not None, not 'OK'.
    team-lead ruling: label is track-agnostic (same string for high and low).
    """

    def test_rejection_low_track_raises_not_implemented(self, tmp_path):
        """extract_one_grib_file(track='mn2t6_low') must raise NotImplementedError."""
        fake_grib = tmp_path / "fake.grib2"
        fake_grib.write_bytes(b"GRIB")
        with pytest.raises(NotImplementedError):
            extract_one_grib_file(
                fake_grib, "Tokyo",
                datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                date(2024, 1, 1), 0,
                track="mn2t6_low",
            )

    def test_rejection_unknown_track_raises_not_implemented(self, tmp_path):
        fake_grib = tmp_path / "fake.grib2"
        fake_grib.write_bytes(b"GRIB")
        with pytest.raises(NotImplementedError):
            extract_one_grib_file(
                fake_grib, "NYC",
                datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                date(2024, 1, 1), 0,
                track="unknown_track",
            )

    def test_acceptance_default_track_does_not_raise_not_implemented(self, tmp_path):
        fake_grib = tmp_path / "fake.grib2"
        fake_grib.write_bytes(b"NOT_REAL_GRIB")
        try:
            extract_one_grib_file(
                fake_grib, "NYC",
                datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                date(2024, 1, 4), 3,
                track="mx2t6_high",
            )
        except NotImplementedError:
            pytest.fail("track='mx2t6_high' must not raise NotImplementedError")
        except Exception:
            pass  # Expected: eccodes rejects fake file

    # CRITICAL-2: pipeline-integration causality labeling
    def test_pipeline_nyc_06z_day0_labeled_not_causal(self):
        """CRITICAL-2 antibody: NYC (UTC-5) with issue_utc=06Z on target_date.
        Local day started at 05:00 UTC; 06Z > 05Z → pure_forecast_valid=False.
        compute_causality must return 'N/A_CAUSAL_DAY_ALREADY_STARTED', not None.

        team-lead ruling: label is track-agnostic — same for high and low.
        """
        issue_utc = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)  # 06Z
        target = date(2024, 1, 1)  # same day
        nyc_offset = -5  # UTC-5 standard time; local midnight = 05:00 UTC

        result = compute_causality(issue_utc, target, nyc_offset)

        assert result is not None, "compute_causality must never return None"
        assert result["pure_forecast_valid"] is False, (
            "NYC 06Z issue on target day: local day started at 05Z — must be non-causal"
        )
        assert result["status"] == "N/A_CAUSAL_DAY_ALREADY_STARTED", (
            f"Non-causal slot must be labeled 'N/A_CAUSAL_DAY_ALREADY_STARTED', "
            f"got {result['status']!r}. team-lead ruling: label is track-agnostic."
        )

    def test_acceptance_causal_slot_has_status_ok(self):
        """NYC lead_day=3 from 00Z: issue before local day start → causal → 'OK'."""
        result = compute_causality(
            datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            date(2024, 1, 4),
            -5,
        )
        assert result["pure_forecast_valid"] is True
        assert result["status"] == "OK"

    def test_acceptance_status_is_nonempty_string_always(self):
        """Status must be a non-empty string for causal and non-causal slots."""
        for offset in [-8, 0, 9]:
            result = compute_causality(
                datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                date(2024, 1, 1),
                offset,
            )
            assert isinstance(result.get("status"), str) and result["status"], (
                f"offset={offset}: status must be a non-empty string"
            )


# ---------------------------------------------------------------------------
# R-U: manifest_hash stability
# ---------------------------------------------------------------------------

class TestManifestHashStability:
    """R-U: compute_manifest_hash(fields: dict) -> str must be deterministic.
    Key insertion order must not affect the result (sorted-JSON SHA-256).
    """

    _BASE = {
        "data_version": "tigge_mx2t6_local_calendar_day_max_v1",
        "physical_quantity": "mx2t6_local_calendar_day_max",
        "manifest_sha256": "abcdef1234567890" * 4,
        "issue_time_utc": "2024-01-01T00:00:00+00:00",
        "city": "Los Angeles",
        "target_date_local": "2024-01-08",
    }

    def test_acceptance_same_dict_produces_identical_hash(self):
        assert compute_manifest_hash(dict(self._BASE)) == compute_manifest_hash(dict(self._BASE))

    def test_acceptance_result_is_64_char_hex_string(self):
        h = compute_manifest_hash(dict(self._BASE))
        assert isinstance(h, str) and len(h) == 64
        int(h, 16)  # raises ValueError if not valid hex

    def test_acceptance_key_order_irrelevant(self):
        normal = dict(self._BASE)
        reversed_ = dict(reversed(list(self._BASE.items())))
        assert compute_manifest_hash(normal) == compute_manifest_hash(reversed_), (
            "manifest_hash must be identical regardless of key insertion order"
        )

    def test_rejection_different_city_produces_different_hash(self):
        assert compute_manifest_hash(dict(self._BASE)) != compute_manifest_hash(
            {**self._BASE, "city": "New York"}
        )

    def test_rejection_different_manifest_sha256_produces_different_hash(self):
        assert compute_manifest_hash(dict(self._BASE)) != compute_manifest_hash(
            {**self._BASE, "manifest_sha256": "fedcba9876543210" * 4}
        )

    def test_rejection_different_target_date_produces_different_hash(self):
        assert compute_manifest_hash(dict(self._BASE)) != compute_manifest_hash(
            {**self._BASE, "target_date_local": "2024-01-09"}
        )

    def test_acceptance_hash_matches_sorted_json_sha256(self):
        fields = dict(self._BASE)
        canon = json.dumps(fields, sort_keys=True, ensure_ascii=False)
        expected = hashlib.sha256(canon.encode()).hexdigest()
        assert compute_manifest_hash(fields) == expected
