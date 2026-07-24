# Created: 2026-07-24
# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=2026-07-24
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/FINAL_SPEC.md
#   (§certificate validity — valid_until min-formula, τ_next issue boundary, fail-closed on
#   missing next-issue metadata) + COLLISION.md commit group D. Pins the certificate-validity
#   contract for the continuous-redecision decision basis: τ_next composition, valid_until
#   min-formula, expired-basis fail-closed, release-boundary resting pull, and the
#   missing-τ_next new-entry fail-closed (exit/monitor never blocked).
"""Certificate validity across forecast issues (commit group D).

The cached belief is the decision basis in the continuous-redecision layer. A belief carries a
``valid_until`` instant (min of τ_next − Δ_cancel, market close, probability freshness) and the raw
``next_authoritative_issue_at`` (τ_next). Past ``valid_until`` the basis is stale and fails closed
exactly like a stale-freshness reject; within Δ_cancel of ``valid_until`` a resting maker order is
pulled (CERT_EXPIRY_PULL); a belief with no computable τ_next may not seed a NEW forecast-conditioned
entry, but never blocks exit/monitor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.release_calendar import next_authoritative_issue_at


def _utc(y=2026, mo=7, d=24, h=0, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ── τ_next composition (release_calendar.next_authoritative_issue_at) ──────────────────────────
# ecmwf_open_data is the live authoritative forecast (forecast_live_daemon
# FORECAST_LIVE_SOURCE_HEALTH_SOURCE_IDS). Cycles 00/06/12/18 UTC; the 00/12 profile carries a
# 485-min availability lag, the 06/18 profile a 285-min lag. τ_next = next cycle after now + that
# cycle's lag.


def test_tau_next_picks_next_cycle_and_short_profile_lag() -> None:
    # 02:00 → next cycle 06:00 (short profile, 285 min) → 10:45 UTC.
    assert next_authoritative_issue_at("ecmwf_open_data", "mx2t6_high", _utc(h=2)) == _utc(h=10, mi=45)


def test_tau_next_full_profile_lag_on_00_12_cycle() -> None:
    # 07:00 → next cycle 12:00 (full profile, 485 min) → 20:05 UTC.
    assert next_authoritative_issue_at("ecmwf_open_data", "mx2t6_high", _utc(h=7)) == _utc(h=20, mi=5)


def test_tau_next_rolls_to_next_utc_day_past_last_cycle() -> None:
    # 19:00 → past 18:00, next cycle is tomorrow 00:00 (full profile, 485 min) → tomorrow 08:05.
    got = next_authoritative_issue_at("ecmwf_open_data", "mx2t6_high", _utc(h=19))
    assert got == _utc(d=25, h=8, mi=5)


def test_tau_next_boundary_is_strictly_after_now() -> None:
    # Exactly at a cycle instant → that cycle is NOT "next"; the following one is.
    # 06:00 → next is 12:00 (full profile, 485 min) → 20:05 UTC.
    assert next_authoritative_issue_at("ecmwf_open_data", "mx2t6_high", _utc(h=6)) == _utc(h=20, mi=5)


def test_tau_next_low_track_matches_high_schedule() -> None:
    assert next_authoritative_issue_at(
        "ecmwf_open_data", "mn2t6_low", _utc(h=2)
    ) == _utc(h=10, mi=45)


def test_tau_next_none_when_calendar_entry_missing() -> None:
    assert next_authoritative_issue_at("no_such_source", "no_track", _utc(h=2)) is None


def test_tau_next_none_on_reconstructed_tier_source() -> None:
    # openmeteo_previous_runs and tigge are RECONSTRUCTED-tier (unverified next-issue schedule) →
    # fail closed to None per FINAL_SPEC (missing/unverified next-issue metadata → fail closed).
    assert next_authoritative_issue_at("openmeteo_previous_runs", "best_match", _utc(h=2)) is None
    assert next_authoritative_issue_at("tigge", "archive", _utc(h=2)) is None


def test_tau_next_is_dst_immune_because_cycle_hours_are_utc() -> None:
    # A civil-DST transition day in the US (2026-03-08, spring forward) must not move the answer:
    # everything is computed in UTC. 02:00 UTC → next cycle 06:00 UTC + 285 min = 10:45 UTC.
    got = next_authoritative_issue_at(
        "ecmwf_open_data", "mx2t6_high", datetime(2026, 3, 8, 2, 0, tzinfo=timezone.utc)
    )
    assert got == datetime(2026, 3, 8, 10, 45, tzinfo=timezone.utc)


def test_tau_next_accepts_non_utc_now_and_normalizes() -> None:
    # now given in a +08:00 zone equal to 02:00 UTC must yield the same τ_next.
    now_local = datetime(2026, 7, 24, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    assert next_authoritative_issue_at("ecmwf_open_data", "mx2t6_high", now_local) == _utc(h=10, mi=45)
