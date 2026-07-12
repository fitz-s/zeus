# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Relationship tests for TemporalPolicy (calendar-derived facts, freshness, axis orthogonality).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md
"""Relationship tests for TemporalPolicy — RED first, then GREEN after implementation.

Tests verify cross-module invariants between config/source_release_calendar.yaml
and src/data/source_time.py. Write these tests BEFORE implementation so they
fail (ImportError) before the module exists, confirming RED state.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = REPO_ROOT / "config" / "source_release_calendar.yaml"


def _load_calendar() -> list[dict]:
    with CALENDAR_PATH.open() as f:
        data = yaml.safe_load(f)
    return data["entries"]


# ---------------------------------------------------------------------------
# Test 1: safe_fetch_not_before matches calendar default_lag_minutes
# ---------------------------------------------------------------------------

def test_temporal_policy_safe_fetch_matches_calendar() -> None:
    """Relationship: TemporalPolicy.safe_fetch_not_before(issue) == issue + timedelta(minutes=entry.safe_fetch.default_lag_minutes).

    For every calendar entry that carries a top-level safe_fetch.default_lag_minutes,
    load_temporal_policy must produce a TemporalPolicy whose safe_fetch_not_before()
    returns exactly issue + that lag.
    """
    from datetime import datetime, timedelta, timezone

    from src.data.source_time import load_temporal_policy

    entries = _load_calendar()
    issue = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)

    for entry in entries:
        safe_fetch_block = entry.get("safe_fetch", {})
        default_lag = safe_fetch_block.get("default_lag_minutes")
        if default_lag is None:
            continue  # skip entries without a top-level safe_fetch

        calendar_id = entry["calendar_id"]
        policy = load_temporal_policy(calendar_id)
        expected = issue + timedelta(minutes=default_lag)
        actual = policy.safe_fetch_not_before(issue)

        assert actual == expected, (
            f"calendar_id={calendar_id!r}: "
            f"expected issue + {default_lag}min = {expected!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: live calendar entries have safe_fetch and non-trivial max_source_lag
# ---------------------------------------------------------------------------

def test_calendar_entries_have_safe_fetch_for_live() -> None:
    """Relationship: every calendar entry with live_authorization=true has a valid
    safe_fetch.default_lag_minutes and a positive max_source_lag_seconds.

    This ensures load_temporal_policy can always compute freshness thresholds
    and safe_fetch_not_before without a None-failure for any live-authorized entry.
    """
    from src.data.source_time import load_temporal_policy

    entries = _load_calendar()

    for entry in entries:
        if not entry.get("live_authorization", False):
            continue

        calendar_id = entry["calendar_id"]
        policy = load_temporal_policy(calendar_id)

        # safe_fetch_lag must be positive
        assert policy.safe_fetch_lag_minutes > 0, (
            f"calendar_id={calendar_id!r}: live entry must have safe_fetch_lag_minutes > 0, "
            f"got {policy.safe_fetch_lag_minutes}"
        )

        # freshness thresholds must be positive and ordered
        assert policy.degraded_after_seconds > 0, (
            f"calendar_id={calendar_id!r}: degraded_after_seconds must be > 0"
        )
        assert policy.expired_after_seconds > 0, (
            f"calendar_id={calendar_id!r}: expired_after_seconds must be > 0"
        )
        assert policy.degraded_after_seconds < policy.expired_after_seconds, (
            f"calendar_id={calendar_id!r}: degraded must come before expired"
        )


# ---------------------------------------------------------------------------
# Test 3: PartialPolicy and LateArrivalPolicy are DISTINCT axes (anti-conflation antibody)
# ---------------------------------------------------------------------------

def test_partial_and_late_arrival_are_distinct_axes() -> None:
    """Antibody: the partial-completeness axis (calendar partial_policy:
    BLOCK_LIVE/ALLOW) must NOT be conflated with the late-write
    disposition axis (LateArrivalPolicy: replace/append/hold/ignore/backfill).

    A prior draft collapsed these into one enum; this locks them apart by value sets.
    """
    from src.data.source_time import LateArrivalPolicy, PartialPolicy

    partial_values = {p.value for p in PartialPolicy}
    late_values = {p.value for p in LateArrivalPolicy}

    assert partial_values == {"BLOCK_LIVE", "ALLOW"}, partial_values
    assert late_values == {
        "replace_same_idempotency_key",
        "append_revision",
        "hold",
        "ignore_if_live_closed",
        "backfill_only",
    }, late_values
    # No overlap — the two axes share no member.
    assert partial_values.isdisjoint(late_values)


# ---------------------------------------------------------------------------
# Test 4: TimePlane enumerates the 12 distinct time coordinates (not source families)
# ---------------------------------------------------------------------------

def test_time_plane_enumerates_twelve_distinct_clocks() -> None:
    """Antibody: TimePlane must enumerate the distinct *time coordinates* (write-time
    vs event-time vs issue/release), not source families. The whole module's purpose —
    detecting 'fresh write-time masking stale event-time' — depends on COLLECTION/IMPORT
    being separate planes from EVENT/SOURCE_ISSUE.
    """
    from src.data.source_time import TimePlane

    values = {p.value for p in TimePlane}
    assert values == {
        "scheduler", "source_issue", "source_release", "source_publish",
        "event", "local_day", "collection", "import",
        "readiness", "market", "blockchain", "artifact",
    }, values
    # Write-time and event-time planes must be distinct members.
    assert TimePlane.COLLECTION != TimePlane.EVENT
    assert TimePlane.IMPORT != TimePlane.SOURCE_ISSUE


# ---------------------------------------------------------------------------
# Test 5: late-arrival disposition derives safely from authority axes
# ---------------------------------------------------------------------------

def test_loader_leaves_late_arrival_unset() -> None:
    """The calendar carries no late-arrival axis, so the loader must NOT populate
    late_arrival_policy (leaving it None) — deriving it would smuggle a hardcoded
    axis-coupling into a 'zero hardcoded facts' module.
    """
    from src.data.source_time import load_temporal_policy

    for cid in (
        "ecmwf_open_data_mx2t6_high",
        "tigge_archive_backfill",
        "openmeteo_previous_runs_best_match",
    ):
        assert load_temporal_policy(cid).late_arrival_policy is None


def test_default_late_arrival_helper_is_fail_safe() -> None:
    """The EXPLICIT helper (not the loader) maps authority axes to a safe disposition:
    backfill/SHADOW sources can never replace a live row; a live BLOCK_LIVE source
    replaces on its idempotency key.
    """
    from src.data.source_time import (
        LateArrivalPolicy,
        PartialPolicy,
        default_late_arrival_for,
    )

    assert default_late_arrival_for(PartialPolicy.BLOCK_LIVE, backfill_only=True) is (
        LateArrivalPolicy.BACKFILL_ONLY
    )
    assert default_late_arrival_for(PartialPolicy.BLOCK_LIVE, backfill_only=False) is (
        LateArrivalPolicy.REPLACE_SAME_IDEMPOTENCY_KEY
    )


# ---------------------------------------------------------------------------
# Test 6: error path + freshness boundary + coerce fail-safe (critic antibodies)
# ---------------------------------------------------------------------------

def test_unknown_calendar_id_raises_keyerror() -> None:
    """load_temporal_policy must fail closed (KeyError) on an unknown calendar_id,
    never return a silent default."""
    import pytest as _pytest

    from src.data.source_time import load_temporal_policy

    with _pytest.raises(KeyError):
        load_temporal_policy("does_not_exist_calendar_id")


def test_freshness_state_boundary_band() -> None:
    """Lock the [degraded, expired) band: CURRENT below degraded, DEGRADED inside the
    band, EXPIRED exactly AT the ceiling (fail-closed). Guards a future < -> <= flip."""
    from src.data.source_time import load_temporal_policy

    p = load_temporal_policy("ecmwf_open_data_mx2t6_high")  # max lag 108000s; 0.8x=86400
    deg = p.degraded_after_seconds
    exp = p.expired_after_seconds
    assert p.freshness_state(deg - 1) == "CURRENT"
    assert p.freshness_state(deg) == "DEGRADED"
    assert p.freshness_state(exp - 1) == "DEGRADED"
    assert p.freshness_state(exp) == "EXPIRED"          # expired AT the ceiling
    assert p.freshness_state(exp + 1) == "EXPIRED"


def test_unknown_partial_policy_coerces_to_block_live() -> None:
    """An absent/unknown partial_policy must coerce to the safest value (BLOCK_LIVE),
    never to ALLOW."""
    from src.data.source_time import PartialPolicy, _coerce_partial_policy

    assert _coerce_partial_policy(None) is PartialPolicy.BLOCK_LIVE
    assert _coerce_partial_policy("NONSENSE") is PartialPolicy.BLOCK_LIVE
    assert _coerce_partial_policy("BLOCK_LIVE") is PartialPolicy.BLOCK_LIVE
