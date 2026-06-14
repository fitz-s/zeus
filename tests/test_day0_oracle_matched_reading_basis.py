# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/evidence/day0_oracle_false_pause_2026-06-13/diagnosis.md
#   (174 day0 families false-paused; live proof 171/174 flags moved exactly ONE
#   extreme + a clean integer gap = WU coverage starvation, only 3 moved both =
#   real tamper). The per-city divergence threshold
#   (config/wu_metar_divergence.json) was measured on timestamp-MATCHED
#   same-station readings; this suite pins that the tamper test refuses to
#   conclude when WU's window did not actually OBSERVE the local-day extreme it
#   would be compared on, while a fully-covered WU window still pauses on a real
#   divergence (tamper detection preserved).
"""Relationship test (RED-on-revert): the WU-vs-METAR oracle tamper test must
conclude divergence ONLY when WU's running extreme is a comparable quantity —
i.e. when WU's coverage window actually observed the local-day extreme (the
SAME basis the per-city threshold was measured on). A coverage/cadence gap (WU
never observed the pre-dawn LOW because its live timeseries window starts mid-
morning) is NOT tampering and must NOT pause.

Cross-module invariant pinned across day0_oracle_anomaly (the detector) and the
EXISTING Day0 coverage classifier in observation_client
(_compute_day0_coverage_status -> coverage_status), reusing its existing
constants — no new magic threshold:

  COVERAGE-STARVATION ≠ TAMPER. A WU running extreme set over a window that did
  not reach local-day start (coverage_status != "OK") is not comparable -> NONE
  verdict (absence of evidence is not an anomaly — the detector's own doctrine).
  A genuinely tampered/injected feed diverges on a FULLY-COVERED window and
  still pauses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.data.day0_fast_obs import MetarReport
from src.data.day0_oracle_anomaly import (
    _reset_registry_for_tests,
    check_wu_metar_divergence,
    flag_day0_oracle_anomaly,
    is_day0_family_paused,
    clear_day0_oracle_anomaly,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _clean_anomaly_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _tokyo():
    # Tokyo: settlement-FAITHFUL C city (measured empirical_threshold=1.0).
    return SimpleNamespace(
        name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
        wu_station="RJTT", settlement_source_type="wu_icao",
    )


def _metar(station, obs_time, temp_c, *, t_group=True, receipt_offset_min=4.0):
    raw = f"METAR {station} 101200Z 16008KT 10SM 21/15 A3004"
    if t_group:
        raw += " RMK AO2 T02110150"
    return MetarReport(
        station_id=station,
        obs_time=obs_time,
        receipt_time=obs_time + timedelta(minutes=receipt_offset_min),
        temp_c=temp_c,
        metar_type="METAR",
        raw=raw,
    )


def test_cadence_only_overnight_low_does_not_pause():
    """The live false-positive class (Milan/London/Amsterdam, 171/174 flags).

    METAR (full coverage) caught a colder overnight LOW that WU's live
    timeseries window — starting mid-morning (coverage_status="WINDOW_INCOMPLETE")
    — never observed. WU's low_so_far is therefore a mid-morning value, several
    units above METAR's true overnight min.

    Running-extrema basis (OLD): low_delta > threshold -> diverged -> 24h pause.
    Coverage-gated basis (NEW): WU did not observe the LOW -> NOT comparable ->
    compared=False -> no pause.

    RED-on-revert: delete the wu_coverage_status gate and the running-extrema
    comparison resurrects -> this assertion fails (verdict.diverged becomes True,
    compared True).
    """
    base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)  # Jun 10 JST morning
    # METAR has the full local day incl. a cold overnight LOW (18C) WU missed.
    metar_reports = [
        _metar("RJTT", base, 22.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=2), 18.0, t_group=False),  # overnight LOW
        _metar("RJTT", base + timedelta(hours=6), 22.0, t_group=False),
    ]
    wu_last_obs = base + timedelta(hours=6)
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=22.0,
        wu_low_so_far=22.0,  # WU never saw the 18C overnight low -> 4C gap
        wu_last_obs_time=wu_last_obs,
        wu_coverage_status="WINDOW_INCOMPLETE",  # WU window started mid-morning
    )
    assert verdict.compared is False and verdict.diverged is False, (
        "coverage-starved WU window must NOT pause on the un-observed extreme; "
        f"verdict={verdict}"
    )
    assert "wu_side_insufficient_coverage" in verdict.detail


def test_running_extrema_basis_would_have_paused_without_the_gate():
    """RED-on-revert proof, isolated: WITHOUT the WU-coverage gate
    (wu_coverage_status omitted -> legacy path) the running-extrema comparison
    DOES diverge and would pause. This documents the exact behavior the WU gate
    suppresses, so a reviewer can see the pre-fix failure mode is real and the
    gate is what flips it.

    NOTE (2026-06-14): METAR is given FULL local-day coverage (first sample 00:30
    JST) so the divergence isolates the WU side ONLY — otherwise the symmetric
    METAR-start coverage gate (added 2026-06-14) would also legitimately suppress
    it (an uncovered METAR low is not comparable either), and this test could no
    longer attribute the legacy divergence to the WU window alone."""
    metar_reports = [
        _metar("RJTT", datetime(2026, 6, 9, 15, 30, tzinfo=UTC), 18.0, t_group=False),  # 00:30 JST overnight low (METAR saw it)
        _metar("RJTT", datetime(2026, 6, 9, 19, 30, tzinfo=UTC), 20.0, t_group=False),  # 04:30 JST
        _metar("RJTT", datetime(2026, 6, 10, 3, 0, tzinfo=UTC), 22.0, t_group=False),   # 12:00 JST high (both agree)
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=22.0, wu_low_so_far=22.0,  # WU's incomplete window never saw the 18C -> 4C gap
        wu_last_obs_time=datetime(2026, 6, 10, 3, 0, tzinfo=UTC),
        # wu_coverage_status omitted -> legacy running-extrema comparison
    )
    assert verdict.compared is True and verdict.diverged is True, (
        "legacy running-extrema basis diverges on the WU coverage gap (this is the "
        f"pre-fix false-positive the WU coverage gate removes); verdict={verdict}"
    )


def test_genuine_matched_tamper_still_pauses():
    """Tamper detection PRESERVED. A fully-covered WU window (coverage_status
    OK) with a real > threshold divergence vs the same-window METAR extreme must
    still diverge AND pause. Guards against neutering the detector: the coverage
    gate must NOT swallow a real tamper on a covered window."""
    base = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 JST, early local day
    metar_reports = [
        _metar("RJTT", base, 21.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=1), 22.0, t_group=False),
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=27.0,  # WU claims 5C above the same-window METAR max
        wu_low_so_far=21.0,
        wu_last_obs_time=base + timedelta(hours=1),
        wu_coverage_status="OK",  # WU observed the full local day -> comparable
    )
    assert verdict.compared is True and verdict.diverged is True, (
        "a 5C divergence on a FULLY-COVERED WU window is a tamper signal and "
        f"must pause; verdict={verdict}"
    )
    # And the pause actually fires end-to-end.
    flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail=verdict.detail)
    assert is_day0_family_paused("Tokyo", "2026-06-10") is True
    assert clear_day0_oracle_anomaly("Tokyo", "2026-06-10") is True


def test_high_agrees_low_cadence_gap_is_real_live_shape():
    """Reproduce the exact live flag shape: HIGH agrees byte-perfect
    (high_delta would be 0.0) while the LOW differs only because WU's mid-morning
    window never observed the overnight min (coverage_status WINDOW_INCOMPLETE).
    No comparable extreme diverges -> no pause. (Milan/Jinan/Beijing class,
    diagnosis lines 16-25.)"""
    base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
    metar_reports = [
        _metar("RJTT", base + timedelta(hours=3), 20.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=4), 15.0, t_group=False),  # overnight-ish LOW WU missed
        _metar("RJTT", base + timedelta(hours=8), 28.0, t_group=False),  # afternoon HIGH (both agree)
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=28.0,  # HIGH agrees byte-perfect
        wu_low_so_far=20.0,   # WU's window low (never saw the 15C) -> 5C gap
        wu_last_obs_time=base + timedelta(hours=8),
        wu_coverage_status="WINDOW_INCOMPLETE",
    )
    assert verdict.compared is False and verdict.diverged is False, (
        "HIGH-agrees / LOW-coverage-gap (the live 171/174 shape) must not pause; "
        f"verdict={verdict}"
    )


def test_low_coverage_sparse_window_is_also_inconclusive():
    """LOW_COVERAGE (window reached day-start but too few samples) is likewise
    not a comparable WU extreme -> inconclusive, never a pause. Reuses the
    existing classifier value; no new threshold introduced."""
    base = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
    metar_reports = [
        _metar("RJTT", base, 22.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=1), 18.0, t_group=False),
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=22.0, wu_low_so_far=22.0,
        wu_last_obs_time=base + timedelta(hours=1),
        wu_coverage_status="LOW_COVERAGE",
    )
    assert verdict.compared is False and verdict.diverged is False


def test_coverage_none_preserves_legacy_behavior():
    """Backward-compat: when the caller does not thread WU coverage (None), the
    detector keeps its prior running-extrema behavior. This is what every
    existing TestOracleAnomaly case relies on; pin it so the gate is strictly
    additive."""
    base = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
    metar_reports = [
        _metar("RJTT", base, 21.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=1), 22.0, t_group=False),
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=22.0, wu_low_so_far=21.0,
        wu_last_obs_time=base + timedelta(hours=1),
        # wu_coverage_status default None -> legacy path
    )
    assert verdict.compared is True and verdict.diverged is False


# ---------------------------------------------------------------------------
# SYMMETRIC METAR-START COVERAGE GATE (diagnosis 2026-06-14). The WU-side gate
# above handles "WU never observed the extreme". Its mirror image — "METAR never
# observed the dawn LOW because its fast-lane window for the local day STARTED
# mid-day (daemon booted mid-day)" — was unguarded: only a METAR-END gate
# existed. Live signature (Chicago/Seattle/Denver/Miami/NYC, 2026-06-14): WU
# coverage OK, high matched to <0.1 F, low off by 3-10 F purely because METAR's
# 5-sample midday window never reached the pre-dawn low. The low is comparable
# ONLY when METAR's window also started at local-day onset (coverage_status !=
# WINDOW_INCOMPLETE); otherwise WU's full-coverage low is authoritative and the
# low cross-check could not run. The HIGH stays compared (per-extreme gate).
# ---------------------------------------------------------------------------


def test_metar_start_coverage_gap_low_does_not_pause():
    """RED-on-revert. WU has FULL coverage (OK) and observed the true dawn LOW
    (18C). METAR's window for the local day STARTS at local noon (first sample
    12:00 JST -> 12h after local midnight -> WINDOW_INCOMPLETE), so its running
    min is a midday floor (25C), never the dawn low. low_delta_raw = 7C >
    threshold. HIGH agrees byte-perfect.

    Coverage-gated basis (NEW): METAR never observed the low -> low not comparable
    -> diverged=False, compared=True (high WAS comparable). RED-on-revert: delete
    the METAR-start gate and the low (7C) is compared -> diverged becomes True ->
    24h false pause resurrects (the live Chicago/Seattle/Denver class)."""
    base = datetime(2026, 6, 10, 3, 0, tzinfo=UTC)  # 03:00Z == 12:00 JST (noon)
    metar_reports = [
        _metar("RJTT", base, 30.0, t_group=False),               # midday HIGH (both agree)
        _metar("RJTT", base + timedelta(hours=1), 25.0, t_group=False),  # midday min (NOT the dawn low)
        _metar("RJTT", base + timedelta(hours=2), 28.0, t_group=False),
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=30.0,   # HIGH agrees byte-perfect
        wu_low_so_far=18.0,    # WU saw the dawn LOW (18C); METAR's window did not -> 7C gap
        wu_last_obs_time=base + timedelta(hours=2),
        wu_coverage_status="OK",  # WU fully covered the local day
    )
    assert verdict.compared is True and verdict.diverged is False, (
        "METAR-start coverage gap on the LOW (WU saw dawn, METAR window started "
        f"midday) must NOT pause; verdict={verdict}"
    )
    assert "metar_low_coverage=WINDOW_INCOMPLETE" in verdict.detail
    assert "low_delta_raw=7.0" in verdict.detail  # the gap exists but was excluded
    assert "low_delta=None" in verdict.detail


def test_metar_covered_low_tamper_still_pauses():
    """Tamper detection PRESERVED on the low. When METAR's window DID start at
    local-day onset (4 samples from 00:30 JST -> coverage OK), a real >threshold
    low divergence (WU 25C vs METAR-observed 18C dawn low) is comparable and must
    still pause. Guards against the start-gate swallowing a genuine low tamper."""
    metar_reports = [
        _metar("RJTT", datetime(2026, 6, 9, 15, 30, tzinfo=UTC), 18.0, t_group=False),  # 00:30 JST dawn low
        _metar("RJTT", datetime(2026, 6, 9, 17, 30, tzinfo=UTC), 19.0, t_group=False),  # 02:30 JST
        _metar("RJTT", datetime(2026, 6, 9, 19, 30, tzinfo=UTC), 22.0, t_group=False),  # 04:30 JST
        _metar("RJTT", datetime(2026, 6, 10, 3, 0, tzinfo=UTC), 30.0, t_group=False),   # 12:00 JST high
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=30.0,   # high agrees
        wu_low_so_far=25.0,    # WU claims 25C; METAR observed 18C at dawn -> 7C tamper on a COVERED low
        wu_last_obs_time=datetime(2026, 6, 10, 3, 0, tzinfo=UTC),
        wu_coverage_status="OK",
    )
    assert verdict.compared is True and verdict.diverged is True, (
        "a real low divergence on a METAR window that COVERED the dawn low must "
        f"still pause; verdict={verdict}"
    )
    assert "metar_low_coverage=OK" in verdict.detail


def test_metar_start_gap_high_tamper_still_caught():
    """Per-extreme proof: even when METAR's window started midday (low excluded),
    a genuine HIGH divergence is STILL compared and pauses. Guards against the
    fix degenerating into a whole-comparison mute (which would blind the detector
    to high-side tampering on every mid-day-booted family)."""
    base = datetime(2026, 6, 10, 3, 0, tzinfo=UTC)  # 12:00 JST -> WINDOW_INCOMPLETE
    metar_reports = [
        _metar("RJTT", base, 30.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=1), 25.0, t_group=False),
        _metar("RJTT", base + timedelta(hours=2), 28.0, t_group=False),
    ]
    verdict = check_wu_metar_divergence(
        city=_tokyo(), target_date="2026-06-10", metar_reports=metar_reports,
        wu_high_so_far=36.0,   # WU claims 6C above the same-window METAR high -> tamper
        wu_low_so_far=18.0,
        wu_last_obs_time=base + timedelta(hours=2),
        wu_coverage_status="OK",
    )
    assert verdict.compared is True and verdict.diverged is True, (
        "high-side tamper must still be caught even when the METAR low window is "
        f"uncovered; verdict={verdict}"
    )
    assert "metar_low_coverage=WINDOW_INCOMPLETE" in verdict.detail
