# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: #90 calibration-coverage antibody / wiring verdict 2026-06-03
"""Antibody #90: loud calibration-coverage guard — relationship tests.

These tests cross the producer->consumer boundary of the silent-fall-through
category the guard exists to make LOUD:

  * BIAS — a real in-memory world DB + the REAL ``read_bias_model`` reader. A
    city WITH a VERIFIED edli_per_city_v1 row for the current (season, month,
    live_data_version) is covered; a city WITHOUT one silently falls to RAW and
    the guard must enumerate it.  (Relationship: the guard's "covered" predicate
    is the SAME read the live correction path performs.)

  * PLATT — the resolution category (own / borrowed:<cluster> / identity) is
    injected at the ``get_calibrator`` + ``_own_cluster_platt_present`` boundary
    so the own-vs-borrow classification and the severity contract can be proven
    without standing up width-normalized Platt fits.

Severity contract proven both ways:
  * SHADOW (armed=False): WARN-only, never raises, boot proceeds (no-starve).
  * ARMED  (armed=True): any gap RAISES CalibrationCoverageError (fail-closed).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.calibration.ens_bias_repo import init_ens_bias_schema, write_bias_model
import src.observability.calibration_coverage_guard as guard
from src.observability.calibration_coverage_guard import (
    CalibrationCoverageError,
    CoverageGap,
    assert_calibration_coverage,
    calibration_coverage_report,
)

# Pin "today" so season/month are deterministic. 2026-06-15 -> month=6 -> JJA (NH).
_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
_SEASON_NH = "JJA"
_MONTH = 6
_HIGH_LDV = "ecmwf_opendata_mx2t3_local_calendar_day_max"
_LOW_LDV = "ecmwf_opendata_mn2t3_local_calendar_day_min"
_EDLI_FAMILY = "edli_per_city_v1"


def _city(name: str, *, lat: float = 35.0, cluster: str | None = None,
          unit: str = "C") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        lat=lat,
        cluster=cluster or name,
        settlement_unit=unit,
    )


@pytest.fixture
def world_conn():
    """In-memory world DB with the canonical model_bias_ens schema."""
    import os
    import sys

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_ens_bias_schema(c)
    # Canonical extension columns (error_model_family / authority / coverage_months)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from scripts.migrate_model_bias_ens_canonical_fields import migrate

    migrate(c, dry_run=False)
    c.commit()
    return c


def _write_verified_bias(conn, *, city: str, metric: str, ldv: str) -> None:
    """Write a VERIFIED edli_per_city_v1 bias row for the current bucket — the
    EXACT key the guard (and the live reactor) reads back."""
    write_bias_model(
        conn,
        city=city,
        season=_SEASON_NH,
        month=_MONTH,
        metric=metric,
        live_data_version=ldv,
        prior_data_version="tigge_mx2t6_local_calendar_day_max",
        posterior_bias_c=-1.0,
        posterior_sd_c=0.5,
        n_live=30,
        n_prior=120,
        weight_live=0.6,
        estimator="test",
        error_model_family=_EDLI_FAMILY,
        error_model_key=f"{city}|{metric}|{_SEASON_NH}|{_EDLI_FAMILY}",
        bias_c=-1.0,
        bias_sd_c=0.3,
        residual_sd_c=1.0,
        effective_bias_c=-1.0,
        total_residual_sd_c=1.0,
        coverage_months=str(_MONTH),
        authority="VERIFIED",
    )
    conn.commit()


def _patch_platt_all_own(monkeypatch):
    """Every city resolves its Platt to its OWN cluster (no gap)."""
    monkeypatch.setattr(
        guard, "_platt_resolution",
        lambda conn, *, city, today, season, metric: "own",
    )


def _patch_platt(monkeypatch, resolver):
    monkeypatch.setattr(guard, "_platt_resolution", resolver)


# ---------------------------------------------------------------------------
# BIAS layer — real read_bias_model boundary
# ---------------------------------------------------------------------------

def test_missing_bias_row_enumerated_as_raw_fallthrough(world_conn, monkeypatch):
    """A city with NO current-season bias row -> guard reports layer=bias,
    fallback=raw (relationship test over the real read_bias_model reader)."""
    _patch_platt_all_own(monkeypatch)
    # Tokyo has BOTH metric rows; Auckland has NONE (the silent RAW city).
    _write_verified_bias(world_conn, city="Tokyo", metric="high", ldv=_HIGH_LDV)
    _write_verified_bias(world_conn, city="Tokyo", metric="low", ldv=_LOW_LDV)
    cities = [_city("Tokyo", lat=35.0), _city("Auckland", lat=35.0)]

    report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=cities, now=_NOW
    )

    bias_gaps = [g for g in report.gaps if g.layer == "bias"]
    assert {g.city for g in bias_gaps} == {"Auckland"}, report.summary()
    assert all(g.fallback == "raw" for g in bias_gaps)
    assert {g.metric for g in bias_gaps} == {"high", "low"}
    # Tokyo (fully covered on bias + own Platt) produces no gap at all.
    assert not [g for g in report.gaps if g.city == "Tokyo"]


def test_fully_covered_city_is_silent(world_conn, monkeypatch):
    """Bias rows present for both metrics + own Platt -> zero gaps -> ok."""
    _patch_platt_all_own(monkeypatch)
    _write_verified_bias(world_conn, city="Tokyo", metric="high", ldv=_HIGH_LDV)
    _write_verified_bias(world_conn, city="Tokyo", metric="low", ldv=_LOW_LDV)

    report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=[_city("Tokyo")], now=_NOW
    )
    assert report.ok, report.summary()
    assert report.gaps == ()


# ---------------------------------------------------------------------------
# PLATT layer — own / borrowed / identity classification
#
# Critical: Platt gaps are only checked for UNCORRECTED cities (no VERIFIED
# bias row).  Bias-corrected cities early-exit to identity-Platt in the live
# reactor (event_reactor_adapter.py:4699+) — that is CORRECT behavior, NOT a gap.
# ---------------------------------------------------------------------------

def test_bias_corrected_city_identity_platt_not_a_gap(world_conn, monkeypatch):
    """A city WITH a VERIFIED bias row resolving to identity-Platt is NOT flagged.
    The live reactor bypasses get_calibrator for bias-corrected cities, so
    identity-Platt on a corrected city is DESIGNED behaviour, not a gap.
    Armed mode must NOT raise on it."""
    # Write VERIFIED bias rows for both metrics -> city IS bias-corrected.
    _write_verified_bias(world_conn, city="Tokyo", metric="high", ldv=_HIGH_LDV)
    _write_verified_bias(world_conn, city="Tokyo", metric="low", ldv=_LOW_LDV)
    # Platt resolver returns identity (worst case) — must be ignored because
    # the city is corrected and the reactor bypasses Platt for it.
    _patch_platt(monkeypatch, lambda conn, *, city, today, season, metric: "identity")

    report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=[_city("Tokyo")], now=_NOW
    )
    platt_gaps = [g for g in report.gaps if g.layer == "platt"]
    assert platt_gaps == [], (
        "Bias-corrected city with identity-Platt must NOT be reported as a gap: "
        + report.summary()
    )
    # Armed mode also must not raise (only bias is checked, and it's covered).
    assert_calibration_coverage(
        armed=True, conn=world_conn, cities=[_city("Tokyo")], now=_NOW
    )


def test_uncorrected_city_borrowed_platt_is_a_gap(world_conn, monkeypatch):
    """An UNCORRECTED city (no bias row) whose Platt resolves to a foreign
    cluster IS a real Platt gap — the reactor DOES reach get_calibrator for it.
    Armed mode raises."""
    # Auckland: NO bias rows -> uncorrected.
    def _resolver(conn, *, city, today, season, metric):
        return "borrowed:foreign_cluster"

    _patch_platt(monkeypatch, _resolver)
    cities = [_city("Auckland")]

    report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=cities, now=_NOW
    )
    platt_gaps = [g for g in report.gaps if g.layer == "platt"]
    assert platt_gaps, "Uncorrected city with borrowed Platt must be reported: " + report.summary()
    assert all(g.fallback.startswith("borrowed:") for g in platt_gaps)

    # Armed: raises because the city has BOTH bias AND Platt gaps.
    with pytest.raises(CalibrationCoverageError):
        assert_calibration_coverage(armed=True, conn=world_conn, cities=cities, now=_NOW)


def test_borrowed_foreign_cluster_platt_enumerated(world_conn, monkeypatch):
    """A city whose Platt resolves to a foreign cluster -> guard reports
    layer=platt, fallback=borrowed:<cluster>.
    NOTE: bias must be ABSENT for the Platt check to run (uncorrected city)."""
    # Lagos: NO bias rows -> uncorrected -> Platt check applies.

    def _resolver(conn, *, city, today, season, metric):
        return "borrowed:Buenos Aires" if metric == "high" else "own"

    _patch_platt(monkeypatch, _resolver)

    report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=[_city("Lagos")], now=_NOW
    )
    platt_gaps = [g for g in report.gaps if g.layer == "platt"]
    assert len(platt_gaps) == 1
    assert platt_gaps[0].fallback == "borrowed:Buenos Aires"
    assert platt_gaps[0].metric == "high"


def test_identity_by_starvation_platt_enumerated(world_conn, monkeypatch):
    """An UNCORRECTED city whose Platt resolves to identity (starvation) -> reported.
    Bias rows absent -> uncorrected -> Platt check applies."""
    # Jinan: NO bias rows -> uncorrected.
    _patch_platt(monkeypatch, lambda conn, *, city, today, season, metric: "identity")

    report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=[_city("Jinan")], now=_NOW
    )
    platt_gaps = [g for g in report.gaps if g.layer == "platt"]
    assert {g.fallback for g in platt_gaps} == {"identity"}
    assert {g.metric for g in platt_gaps} == {"high", "low"}


# ---------------------------------------------------------------------------
# SEVERITY contract — shadow warn-only vs armed fail-closed
# ---------------------------------------------------------------------------

def test_shadow_warns_does_not_raise_boot_proceeds(world_conn, monkeypatch, caplog):
    """SHADOW + an uncovered city: WARN, do NOT raise, return the report
    (proves the no-starve / boot-proceeds property)."""
    _patch_platt_all_own(monkeypatch)
    # Auckland uncovered on bias (no rows written).
    cities = [_city("Auckland")]

    with caplog.at_level(logging.WARNING):
        report = assert_calibration_coverage(
            armed=False, conn=world_conn, cities=cities, now=_NOW
        )

    assert not report.ok          # gaps exist
    assert len(report.gaps) >= 1  # at least the two bias gaps
    # WARN-only: a per-gap line AND a roll-up were logged (getMessage renders
    # args safely even when the formatted text contains a literal '%').
    warn_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "CALIBRATION_COVERAGE_GAP" in warn_text
    assert "CALIBRATION_COVERAGE_PARTIAL" in warn_text


def test_armed_uncovered_city_raises_fail_closed(world_conn, monkeypatch):
    """ARMED + an uncovered city: RAISE CalibrationCoverageError (fail-closed)."""
    _patch_platt_all_own(monkeypatch)
    cities = [_city("Auckland")]  # no bias rows -> uncovered

    with pytest.raises(CalibrationCoverageError, match="ARMED"):
        assert_calibration_coverage(
            armed=True, conn=world_conn, cities=cities, now=_NOW
        )


def test_armed_fully_covered_does_not_raise(world_conn, monkeypatch):
    """ARMED + a fully-covered city: no raise, returns ok report."""
    _patch_platt_all_own(monkeypatch)
    _write_verified_bias(world_conn, city="Tokyo", metric="high", ldv=_HIGH_LDV)
    _write_verified_bias(world_conn, city="Tokyo", metric="low", ldv=_LOW_LDV)

    report = assert_calibration_coverage(
        armed=True, conn=world_conn, cities=[_city("Tokyo")], now=_NOW
    )
    assert report.ok, report.summary()


def test_shadow_and_armed_enumerate_identical_gaps(world_conn, monkeypatch):
    """RELATIONSHIP: the SAME uncovered city yields the SAME enumerated gap set
    in shadow and armed — only the SEVERITY (warn vs raise) differs. This is the
    cross-boundary invariant: arming does not change WHAT is uncovered, only the
    consequence of it."""
    _patch_platt_all_own(monkeypatch)
    cities = [_city("Auckland")]

    shadow_report = calibration_coverage_report(
        armed=False, conn=world_conn, cities=cities, now=_NOW
    )
    armed_report = calibration_coverage_report(
        armed=True, conn=world_conn, cities=cities, now=_NOW
    )
    shadow_keys = {(g.city, g.metric, g.layer, g.fallback) for g in shadow_report.gaps}
    armed_keys = {(g.city, g.metric, g.layer, g.fallback) for g in armed_report.gaps}
    assert shadow_keys == armed_keys
    assert shadow_keys  # non-empty (Auckland uncovered)
    # And the severity divergence:
    assert assert_calibration_coverage(
        armed=False, conn=world_conn, cities=cities, now=_NOW
    ).gaps  # shadow returns, does not raise
    with pytest.raises(CalibrationCoverageError):
        assert_calibration_coverage(
            armed=True, conn=world_conn, cities=cities, now=_NOW
        )


def test_coverage_gap_describe_format():
    """The LOUD structured signal includes city, metric, season, layer, fallback."""
    g = CoverageGap(
        city="Hong Kong", metric="high", layer="platt",
        season="JJA", fallback="borrowed:Singapore",
    )
    desc = g.describe()
    for token in ("Hong Kong", "high", "JJA", "platt", "borrowed:Singapore"):
        assert token in desc
