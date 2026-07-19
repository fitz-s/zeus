# Created: 2026-06-17
# Authority basis: operator delta-package v2 (real_upgrade #4) — day0_source_health predicate.
"""Contract tests for the Day0 source-health classifier (8 states)."""
from __future__ import annotations

from src.data.day0_coverage_proof import Day0CoverageProof
from src.data.day0_source_health import (
    Day0SourceFacts,
    day0_source_health,
    is_live_admissible,
)


def _proof(status: str = "FULL_THROUGH_DECISION") -> Day0CoverageProof:
    return Day0CoverageProof(
        status=status, first_sample_utc="x", last_sample_utc="y", coverage_through_utc="y",
        max_gap_minutes=60.0, expected_cadence_minutes=60.0, sample_count=12,
        dst_day_length_hours=24.0, proof_source="aviationweather_metar",
    )


def _facts(**kw) -> Day0SourceFacts:
    base = dict(
        settlement_source_type="wu_icao", fast_obs_supported=True, fast_obs_fresh=True,
        fast_obs_present=True, wu_present=True, divergence_paused=False, anomaly_paused=False,
        coverage_proof=_proof(), has_publication_clock=True,
    )
    base.update(kw)
    return Day0SourceFacts(**base)


def test_unknown_without_proof_or_clock() -> None:
    assert day0_source_health(_facts(coverage_proof=None)) == "UNKNOWN"
    assert day0_source_health(_facts(has_publication_clock=False)) == "UNKNOWN"


def test_unsupported_source_hko_no_wu() -> None:
    assert day0_source_health(_facts(settlement_source_type="hko", fast_obs_supported=False, wu_present=False)) == "UNSUPPORTED_SOURCE"


def test_unsupported_source_noaa_no_wu() -> None:
    assert day0_source_health(_facts(settlement_source_type="noaa", fast_obs_supported=False, wu_present=False)) == "UNSUPPORTED_SOURCE"


def test_divergence_pause_dominates() -> None:
    assert day0_source_health(_facts(divergence_paused=True)) == "DIVERGENCE_PAUSED"
    assert day0_source_health(_facts(anomaly_paused=True)) == "DIVERGENCE_PAUSED"


def test_incomplete_coverage_blocks() -> None:
    for s in ("WINDOW_INCOMPLETE", "LOW_COVERAGE", "GAP_INCOMPLETE", "GAP_SUSPECT"):
        assert day0_source_health(_facts(coverage_proof=_proof(s))) == "WINDOW_INCOMPLETE"


def test_degraded_fast_stale_no_wu() -> None:
    assert day0_source_health(_facts(fast_obs_fresh=False, wu_present=False)) == "DEGRADED_FAST_STALE"


def test_stale_fast_with_wu_downgrades_to_wu_only() -> None:
    assert day0_source_health(_facts(fast_obs_fresh=False, wu_present=True)) == "OK_WU_ONLY"


def test_ok_fast_and_wu() -> None:
    assert day0_source_health(_facts()) == "OK_FAST_AND_WU"


def test_ok_fast_only() -> None:
    assert day0_source_health(_facts(wu_present=False)) == "OK_FAST_ONLY"


def test_ok_wu_only_when_fast_unsupported() -> None:
    assert day0_source_health(_facts(fast_obs_supported=False, fast_obs_present=False, wu_present=True)) == "OK_WU_ONLY"


def test_admissibility_default_is_strongest_states_only() -> None:
    assert is_live_admissible("OK_FAST_AND_WU")
    assert is_live_admissible("OK_FAST_ONLY")
    assert not is_live_admissible("OK_WU_ONLY")
    assert not is_live_admissible("DEGRADED_FAST_STALE")
    assert not is_live_admissible("WINDOW_INCOMPLETE")
