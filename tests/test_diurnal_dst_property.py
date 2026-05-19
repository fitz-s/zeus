# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md PR 5 row; INV-16
# SCAFFOLD ONLY — all tests are @pytest.mark.skip pending PR 5 production code.
# See docs/operations/task_2026-05-17_strategy_vnext_phase0/scaffolds/pr5_scaffold_report.md
"""R-5.2: DST property tests for build_day0_temporal_context.

Three DST archetypes:
  A. London 2026-03-29 spring-forward (clocks: 01:00 → 02:00 GMT→BST).
     Missing hour is 01:00–01:59 local. [Note: brief cited 2026-03-30 — corrected
     here to 2026-03-29, the actual last-Sunday-in-March for 2026.]
  B. Sydney 2025-10-05 spring-forward (AEST→AEDT, clocks: 02:00 → 03:00).
     Missing hour is 02:00–02:59 local.
  C. New York 2026-11-01 fall-back (EDT→EST, clocks: 02:00 → 01:00).
     Ambiguous hour is 01:00–01:59 local. NOT a missing hour — fold=0/1 distinction.

Design note on audit targets (OPEN QUESTION #5)
------------------------------------------------
The original brief described auditing `timedelta(hours=...)` sites in diurnal.py.
Grep confirms diurnal.py has NO timedelta usage (exit code 1). The real DST risk
surface is `_instant_from_local_hour` (src/signal/diurnal.py lines 298-334) which
calls `datetime.combine(target_date, time(hour%24, minute, second), tzinfo=tz)`
without a missing-hour branch. The `is_missing_local_hour` flag is computed via
`_is_missing_local_hour` from src.contracts.dst_semantics but may not be branched
on downstream. Production tests must verify that build_day0_temporal_context
propagates is_missing_local_hour=True into Day0TemporalContext.is_missing_local_hour
for archetype A and B, and is_ambiguous_local_hour=True for archetype C.
"""
import pytest
from datetime import date
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Shared fake DB fixture
# ---------------------------------------------------------------------------

def _fake_conn_for_city(solar_data: dict):
    """Build a fake DB connection with pre-baked solar_daily row."""
    class FakeConn:
        def execute(self, query, params=()):
            query = " ".join(query.split())
            if "FROM diurnal_curves" in query:
                return type("Cursor", (), {
                    "fetchall": lambda self: [
                        {"hour": h, "avg_temp": 10.0 + h, "std_temp": 1.5, "p_high_set": None}
                        for h in range(4, 22)
                    ]
                })()
            if "FROM diurnal_peak_prob" in query:
                return type("Cursor", (), {"fetchone": lambda self: None})()
            if "FROM solar_daily" in query:
                return type("Cursor", (), {"fetchone": lambda self: solar_data})()
            raise AssertionError(f"Unexpected query: {query}")

        def close(self):
            return None

    return FakeConn()


# ---------------------------------------------------------------------------
# Archetype A: London 2026-03-29 spring-forward
# Clocks go forward at 01:00 GMT → 02:00 BST.
# Missing wall-clock hour: 01:00–01:59 local.
# ---------------------------------------------------------------------------

_LONDON_SOLAR_2026_03_29 = {
    "timezone": "Europe/London",
    "sunrise_local": "2026-03-29T06:15+00:00",
    "sunset_local": "2026-03-29T19:28+01:00",  # BST after transition
    "sunrise_utc": "2026-03-29T06:15+00:00",
    "sunset_utc": "2026-03-29T18:28+00:00",
    "utc_offset_minutes": 0,   # UTC at midnight; transitions to +60 at 01:00
    "dst_active": 1,
}


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@patch("src.state.db.get_world_connection")
def test_london_spring_forward_missing_hour_flagged(mock_get_conn) -> None:
    """Archetype A: observation at 01:30 local (spring-forward gap) → is_missing_local_hour=True."""
    from src.signal.diurnal import build_day0_temporal_context

    mock_get_conn.return_value = _fake_conn_for_city(_LONDON_SOLAR_2026_03_29)

    # 01:30 UTC on 2026-03-29 is in the gap (clocks jumped from 01:00→02:00)
    ctx = build_day0_temporal_context(
        "London",
        date(2026, 3, 29),
        "Europe/London",
        observation_time="2026-03-29T01:30:00+00:00",
        observation_source="wu_api",
    )
    assert ctx is not None, "Context must not degrade for valid solar data"
    assert ctx.is_missing_local_hour is True, (
        "01:30 local on 2026-03-29 (London spring-forward) must be flagged as missing"
    )
    assert ctx.is_ambiguous_local_hour is False


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@patch("src.state.db.get_world_connection")
def test_london_normal_hour_not_flagged(mock_get_conn) -> None:
    """Archetype A control: observation at 10:00 BST → no DST flags."""
    from src.signal.diurnal import build_day0_temporal_context

    mock_get_conn.return_value = _fake_conn_for_city(_LONDON_SOLAR_2026_03_29)

    ctx = build_day0_temporal_context(
        "London",
        date(2026, 3, 29),
        "Europe/London",
        observation_time="2026-03-29T09:00:00+00:00",  # 10:00 BST — valid
        observation_source="wu_api",
    )
    assert ctx is not None
    assert ctx.is_missing_local_hour is False
    assert ctx.is_ambiguous_local_hour is False


# ---------------------------------------------------------------------------
# Archetype B: Sydney 2025-10-05 spring-forward
# Clocks go forward at 02:00 AEST → 03:00 AEDT.
# Missing wall-clock hour: 02:00–02:59 local.
# ---------------------------------------------------------------------------

_SYDNEY_SOLAR_2025_10_05 = {
    "timezone": "Australia/Sydney",
    "sunrise_local": "2025-10-05T05:37+10:00",
    "sunset_local": "2025-10-05T18:24+11:00",  # AEDT after transition
    "sunrise_utc": "2025-10-04T19:37+00:00",
    "sunset_utc": "2025-10-05T07:24+00:00",
    "utc_offset_minutes": 600,  # AEST +10 at midnight; transitions to +660 at 02:00
    "dst_active": 1,
}


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@patch("src.state.db.get_world_connection")
def test_sydney_spring_forward_missing_hour_flagged(mock_get_conn) -> None:
    """Archetype B: observation at 02:30 local (spring-forward gap) → is_missing_local_hour=True."""
    from src.signal.diurnal import build_day0_temporal_context

    mock_get_conn.return_value = _fake_conn_for_city(_SYDNEY_SOLAR_2025_10_05)

    # 02:30 local = 16:30 UTC on 2025-10-04 (AEST is UTC+10; gap is at 02:00 local)
    ctx = build_day0_temporal_context(
        "Sydney",
        date(2025, 10, 5),
        "Australia/Sydney",
        observation_time="2025-10-04T16:30:00+00:00",  # 02:30 AEST in gap
        observation_source="wu_api",
    )
    assert ctx is not None
    assert ctx.is_missing_local_hour is True, (
        "02:30 local on 2025-10-05 (Sydney spring-forward) must be flagged as missing"
    )
    assert ctx.is_ambiguous_local_hour is False


# ---------------------------------------------------------------------------
# Archetype C: New York 2026-11-01 fall-back
# Clocks fall back at 02:00 EDT → 01:00 EST.
# Ambiguous hour: 01:00–01:59 local (appears twice).
# This is NOT a missing hour; fold=0/1 distinguishes the two occurrences.
# ---------------------------------------------------------------------------

_NYC_SOLAR_2026_11_01 = {
    "timezone": "America/New_York",
    "sunrise_local": "2026-11-01T07:22-04:00",
    "sunset_local": "2026-11-01T18:00-05:00",  # EST after fall-back
    "sunrise_utc": "2026-11-01T11:22+00:00",
    "sunset_utc": "2026-11-01T23:00+00:00",
    "utc_offset_minutes": -240,  # EDT at midnight; transitions to -300 at 02:00
    "dst_active": 0,  # DST ends on this day
}


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@patch("src.state.db.get_world_connection")
def test_nyc_fall_back_ambiguous_hour_flagged(mock_get_conn) -> None:
    """Archetype C: observation at 01:30 local (fall-back repeat hour) → is_ambiguous_local_hour=True."""
    from src.signal.diurnal import build_day0_temporal_context

    mock_get_conn.return_value = _fake_conn_for_city(_NYC_SOLAR_2026_11_01)

    # 01:30 local first occurrence (EDT) = 05:30 UTC; second occurrence (EST) = 06:30 UTC
    # Build with the first occurrence (fold=0):
    ctx = build_day0_temporal_context(
        "New_York",
        date(2026, 11, 1),
        "America/New_York",
        observation_time="2026-11-01T05:30:00+00:00",  # 01:30 EDT (fold=0)
        observation_source="wu_api",
    )
    assert ctx is not None
    assert ctx.is_ambiguous_local_hour is True, (
        "01:30 local on 2026-11-01 (NYC fall-back) must be flagged as ambiguous"
    )
    assert ctx.is_missing_local_hour is False


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@patch("src.state.db.get_world_connection")
def test_nyc_fall_back_normal_evening_not_flagged(mock_get_conn) -> None:
    """Archetype C control: observation at 14:00 local (after fall-back) → no DST flags."""
    from src.signal.diurnal import build_day0_temporal_context

    mock_get_conn.return_value = _fake_conn_for_city(_NYC_SOLAR_2026_11_01)

    ctx = build_day0_temporal_context(
        "New_York",
        date(2026, 11, 1),
        "America/New_York",
        observation_time="2026-11-01T19:00:00+00:00",  # 14:00 EST — unambiguous
        observation_source="wu_api",
    )
    assert ctx is not None
    assert ctx.is_missing_local_hour is False
    assert ctx.is_ambiguous_local_hour is False
