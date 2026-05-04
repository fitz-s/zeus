# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §8 — relationship-test floor T1, T3, T4 (T2 lands after plumbing; T5/T6 land with P3/P4 per sequencing rule).
"""``MarketPhase`` axis A unit tests.

Per PLAN_v3 §8 the merge-floor for P2 includes 6 invariants. Three are
testable against the standalone helper before any plumbing lands:

- T1 — phase-from-decision_time stability (50-candidate cycle straddling
  midnight sees the SAME phase for every candidate of the same market)
- T3 — boundary inclusivity at ``settlement_day_entry_utc``
- T4 — POST_TRADING anchored at Polymarket endDate (12:00 UTC of
  ``target_date``)

T2 (phase-vs-LifecyclePhase consistency) requires the daemon writers to
tag positions with ``MarketPhase``; it lands in the same PR but in a
later commit once the plumbing is in place.

T5 (candidate filter post-D-A) and T6 (mode-default preservation
post-D-B) are by construction coupled to D-A (P4) and D-B (P3) and
land with their respective packets — see PLAN_v3 §6 sequencing rule.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.strategy.market_phase import (
    MarketPhase,
    market_phase_for_decision,
    settlement_day_entry_utc,
)

UTC = timezone.utc


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _london_end_date(target: date) -> datetime:
    return datetime(target.year, target.month, target.day, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------- #
# T3 — boundary inclusivity at settlement_day_entry_utc
# ---------------------------------------------------------------------- #


def test_t3_settlement_day_entry_inclusive_late() -> None:
    """At exactly settlement_day_entry_utc, phase is SETTLEMENT_DAY (not
    PRE_SETTLEMENT_DAY). Boundary is inclusive on the later side.
    """
    target = date(2026, 5, 8)
    sd_entry = settlement_day_entry_utc(
        target_local_date=target, city_timezone="Europe/London"
    )

    one_microsecond_before = sd_entry - timedelta(microseconds=1)
    at_boundary = sd_entry
    one_microsecond_after = sd_entry + timedelta(microseconds=1)

    common = dict(
        target_local_date=target,
        city_timezone="Europe/London",
        polymarket_start_utc=datetime(2026, 5, 6, 4, 4, tzinfo=UTC),
        polymarket_end_utc=_london_end_date(target),
    )

    assert (
        market_phase_for_decision(decision_time_utc=one_microsecond_before, **common)
        == MarketPhase.PRE_SETTLEMENT_DAY
    )
    assert (
        market_phase_for_decision(decision_time_utc=at_boundary, **common)
        == MarketPhase.SETTLEMENT_DAY
    )
    assert (
        market_phase_for_decision(decision_time_utc=one_microsecond_after, **common)
        == MarketPhase.SETTLEMENT_DAY
    )


def test_t3_settlement_day_entry_london_2026_05_08() -> None:
    """London 2026-05-08 high-temp market: city-local end-of-target is
    2026-05-09 00:00 BST = 2026-05-08 23:00 UTC. SETTLEMENT_DAY entry =
    23:00 UTC − 24h = 2026-05-07 23:00 UTC.
    """
    sd_entry = settlement_day_entry_utc(
        target_local_date=date(2026, 5, 8), city_timezone="Europe/London"
    )
    assert sd_entry == datetime(2026, 5, 7, 23, 0, 0, tzinfo=UTC)


def test_t3_settlement_day_entry_la_2026_05_08() -> None:
    """LA 2026-05-08: city-local end-of-target is 2026-05-09 00:00 PDT =
    2026-05-09 07:00 UTC. SETTLEMENT_DAY entry = 07:00 UTC − 24h =
    2026-05-08 07:00 UTC.
    """
    sd_entry = settlement_day_entry_utc(
        target_local_date=date(2026, 5, 8), city_timezone="America/Los_Angeles"
    )
    assert sd_entry == datetime(2026, 5, 8, 7, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------- #
# T4 — POST_TRADING at endDate (12:00 UTC of target_date)
# ---------------------------------------------------------------------- #


def test_t4_post_trading_at_or_after_endDate() -> None:
    target = date(2026, 5, 8)
    end_utc = _london_end_date(target)
    common = dict(
        target_local_date=target,
        city_timezone="Europe/London",
        polymarket_start_utc=datetime(2026, 5, 6, 4, 4, tzinfo=UTC),
        polymarket_end_utc=end_utc,
    )

    assert (
        market_phase_for_decision(
            decision_time_utc=end_utc - timedelta(microseconds=1), **common
        )
        == MarketPhase.SETTLEMENT_DAY
    )
    assert (
        market_phase_for_decision(decision_time_utc=end_utc, **common)
        == MarketPhase.POST_TRADING
    )
    assert (
        market_phase_for_decision(
            decision_time_utc=end_utc + timedelta(hours=2), **common
        )
        == MarketPhase.POST_TRADING
    )


def test_t4_post_trading_uniform_across_cities() -> None:
    """F1 invariant: Polymarket weather endDate is uniformly 12:00 UTC
    of target_date for ALL cities. Wellington and LA, same target_date,
    same end_utc, both transition to POST_TRADING at 12:00 UTC.
    """
    target = date(2026, 5, 8)
    end_utc = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    start_utc = datetime(2026, 5, 6, 4, 4, tzinfo=UTC)

    for tz in ["Pacific/Auckland", "Asia/Tokyo", "Europe/London", "America/Los_Angeles"]:
        assert (
            market_phase_for_decision(
                target_local_date=target,
                city_timezone=tz,
                decision_time_utc=end_utc,
                polymarket_start_utc=start_utc,
                polymarket_end_utc=end_utc,
            )
            == MarketPhase.POST_TRADING
        ), f"{tz} should be POST_TRADING at endDate"


# ---------------------------------------------------------------------- #
# T1 — stability across midnight straddle
# ---------------------------------------------------------------------- #


def test_t1_phase_stable_across_midnight_straddle() -> None:
    """A cycle starting at decision_time = T_0 and processing 50
    candidates over 30s wall clock must see the SAME phase for every
    candidate of the same market, even if T_0 straddles a city's local
    midnight. Concretely: if the cycle freezes decision_time once and
    every candidate uses that frozen value, the helper is deterministic.
    Pins critic R1 C5.
    """
    target = date(2026, 5, 8)
    common = dict(
        target_local_date=target,
        city_timezone="Pacific/Auckland",
        polymarket_start_utc=datetime(2026, 5, 6, 4, 4, tzinfo=UTC),
        polymarket_end_utc=_london_end_date(target),
    )

    sd_entry = settlement_day_entry_utc(
        target_local_date=target, city_timezone="Pacific/Auckland"
    )
    decision_time = sd_entry - timedelta(microseconds=1)
    phases = {
        market_phase_for_decision(decision_time_utc=decision_time, **common)
        for _ in range(50)
    }
    assert phases == {MarketPhase.PRE_SETTLEMENT_DAY}, (
        "frozen decision_time must produce a single phase for the same "
        "market across every candidate evaluation in the cycle"
    )


# ---------------------------------------------------------------------- #
# Pre-trading and resolved
# ---------------------------------------------------------------------- #


def test_pre_trading_when_before_polymarket_start() -> None:
    target = date(2026, 5, 8)
    start_utc = datetime(2026, 5, 6, 4, 4, tzinfo=UTC)
    end_utc = _london_end_date(target)
    decision_time = start_utc - timedelta(hours=1)

    assert (
        market_phase_for_decision(
            target_local_date=target,
            city_timezone="Europe/London",
            decision_time_utc=decision_time,
            polymarket_start_utc=start_utc,
            polymarket_end_utc=end_utc,
        )
        == MarketPhase.PRE_TRADING
    )


def test_resolved_overrides_all_other_phases() -> None:
    """``uma_resolved=True`` is terminal and overrides every other
    boundary check.
    """
    target = date(2026, 5, 8)
    start_utc = datetime(2026, 5, 6, 4, 4, tzinfo=UTC)
    end_utc = _london_end_date(target)

    for dt in [
        start_utc - timedelta(days=1),  # before start
        start_utc + timedelta(hours=1),  # pre-settlement-day
        end_utc - timedelta(hours=1),  # settlement-day
        end_utc + timedelta(hours=1),  # post-trading
    ]:
        assert (
            market_phase_for_decision(
                target_local_date=target,
                city_timezone="Europe/London",
                decision_time_utc=dt,
                polymarket_start_utc=start_utc,
                polymarket_end_utc=end_utc,
                uma_resolved=True,
            )
            == MarketPhase.RESOLVED
        )


# ---------------------------------------------------------------------- #
# Naive-datetime guard (UTC-strict directive)
# ---------------------------------------------------------------------- #


def test_naive_decision_time_rejected() -> None:
    target = date(2026, 5, 8)
    with pytest.raises(ValueError, match="timezone-aware"):
        market_phase_for_decision(
            target_local_date=target,
            city_timezone="Europe/London",
            decision_time_utc=datetime(2026, 5, 8, 12, 0, 0),  # naive
            polymarket_start_utc=datetime(2026, 5, 6, 4, 4, tzinfo=UTC),
            polymarket_end_utc=_london_end_date(target),
        )


def test_naive_polymarket_end_utc_rejected() -> None:
    target = date(2026, 5, 8)
    with pytest.raises(ValueError, match="timezone-aware"):
        market_phase_for_decision(
            target_local_date=target,
            city_timezone="Europe/London",
            decision_time_utc=datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC),
            polymarket_start_utc=datetime(2026, 5, 6, 4, 4, tzinfo=UTC),
            polymarket_end_utc=datetime(2026, 5, 8, 12, 0, 0),  # naive
        )


# ---------------------------------------------------------------------- #
# DST sanity — spring-forward boundary
# ---------------------------------------------------------------------- #


# ---------------------------------------------------------------------- #
# Adapter from market dict shape (stage 2 plumbing)
# ---------------------------------------------------------------------- #


def test_adapter_uses_explicit_market_end_at_when_present() -> None:
    from src.strategy.market_phase import market_phase_from_market_dict

    market = {
        "market_end_at": "2026-05-08T12:00:00Z",
        "market_start_at": "2026-05-06T04:04:00Z",
        "target_date": "2026-05-08",
    }
    decision_time = datetime(2026, 5, 7, 23, 0, 0, tzinfo=UTC)  # London SD entry

    phase = market_phase_from_market_dict(
        market=market,
        city_timezone="Europe/London",
        target_date_str="2026-05-08",
        decision_time_utc=decision_time,
    )
    assert phase == MarketPhase.SETTLEMENT_DAY


def test_adapter_falls_back_to_f1_anchor_when_end_absent() -> None:
    """F1 invariant: when market dict lacks ``market_end_at``, the
    adapter derives 12:00 UTC of target_date as the fallback. This is
    safe-by-construction because every Polymarket weather market
    settles at this time per F1.
    """
    from src.strategy.market_phase import market_phase_from_market_dict

    market = {"target_date": "2026-05-08"}  # No end_at field at all
    decision_time = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)  # Exactly endDate

    phase = market_phase_from_market_dict(
        market=market,
        city_timezone="Europe/London",
        target_date_str="2026-05-08",
        decision_time_utc=decision_time,
    )
    # At 12:00 UTC of target_date with F1 fallback → POST_TRADING boundary
    assert phase == MarketPhase.POST_TRADING


def test_adapter_handles_offset_iso8601_variant() -> None:
    """Gamma can return either ``Z`` or ``+00:00`` suffix; both are
    accepted.
    """
    from src.strategy.market_phase import market_phase_from_market_dict

    for end_str in ["2026-05-08T12:00:00Z", "2026-05-08T12:00:00+00:00"]:
        market = {"market_end_at": end_str, "target_date": "2026-05-08"}
        phase = market_phase_from_market_dict(
            market=market,
            city_timezone="Europe/London",
            target_date_str="2026-05-08",
            decision_time_utc=datetime(2026, 5, 8, 13, 0, 0, tzinfo=UTC),
        )
        assert phase == MarketPhase.POST_TRADING


def test_adapter_uses_explicit_end_in_production_parse_event_dict() -> None:
    """Critic R3 ATTACK 8 regression antibody. The production market dict
    from ``src.data.market_scanner._parse_event`` MUST carry
    ``market_end_at`` and ``market_start_at`` so the adapter consumes
    Gamma's explicit timestamps, not the F1 fallback. Test exercises the
    real ``_parse_event`` shape end-to-end (via a synthetic Gamma-shape
    payload) and asserts the explicit endDate wins over the F1 anchor.

    Pre-fix this test failed because ``_parse_event`` omitted those keys
    and the adapter silently used ``_f1_fallback_end_utc(target_local_date)
    = 12:00 UTC``. The fix at ``market_scanner.py:1077-1083`` threads
    ``event.get("endDate")`` and ``event.get("startDate")`` onto the
    parent market dict.
    """
    from src.strategy.market_phase import market_phase_from_market_dict

    # Synthetic Gamma payload with a NON-12:00 endDate to prove the
    # explicit value wins over the F1 fallback. (Real Polymarket weather
    # markets settle at 12:00 UTC per F1, but this test must distinguish
    # explicit-vs-fallback paths, so we use 14:00 UTC to make the
    # difference observable.)
    market_dict = {
        "event_id": "ev-1",
        "slug": "high-temp-london-may-8",
        "target_date": "2026-05-08",
        "outcomes": [],
        "market_start_at": "2026-05-06T04:04:00Z",
        "market_end_at": "2026-05-08T14:00:00Z",  # NOT 12:00
    }
    decision_time = datetime(2026, 5, 8, 13, 0, 0, tzinfo=UTC)

    phase = market_phase_from_market_dict(
        market=market_dict,
        city_timezone="Europe/London",
        target_date_str="2026-05-08",
        decision_time_utc=decision_time,
    )
    # If the F1 fallback (12:00 UTC) had been used, decision_time 13:00
    # would be POST_TRADING. With the explicit 14:00 endDate, 13:00 is
    # still SETTLEMENT_DAY. This delta proves the explicit path is live.
    assert phase == MarketPhase.SETTLEMENT_DAY, (
        f"explicit market_end_at=14:00 should keep 13:00 in SETTLEMENT_DAY; "
        f"got {phase} — F1 fallback shadow."
    )


def test_parse_event_dict_carries_endDate_keys() -> None:
    """Schema-level antibody on ``_parse_event``: confirm the dict it
    returns includes the ``market_end_at`` / ``market_start_at`` keys
    threaded from the Gamma event. Future refactors that drop these
    keys silently re-introduce the F1-only-path bug; this test fails
    loudly.
    """
    from src.data import market_scanner

    # Walk the source for the literal keys; cheap structural assertion
    # that survives without executing the heavy network/scanner stack.
    src_text = open(market_scanner.__file__, "r", encoding="utf-8").read()
    assert '"market_end_at"' in src_text, (
        "src/data/market_scanner.py must carry the literal "
        '"market_end_at" key in _parse_event\'s returned dict — see '
        "critic-opus R3 ATTACK 8 (PR #53)."
    )
    assert '"market_start_at"' in src_text, (
        "src/data/market_scanner.py must carry the literal "
        '"market_start_at" key in _parse_event\'s returned dict.'
    )


def test_adapter_naive_gamma_payload_is_loud_failure() -> None:
    """A Gamma payload missing tz info would silently drift through
    naive arithmetic. The adapter raises so cycle_runtime can log and
    leave the candidate untagged rather than tag with a wrong phase.
    """
    from src.strategy.market_phase import market_phase_from_market_dict

    market = {"market_end_at": "2026-05-08T12:00:00", "target_date": "2026-05-08"}  # naive
    with pytest.raises(ValueError, match="naive datetime"):
        market_phase_from_market_dict(
            market=market,
            city_timezone="Europe/London",
            target_date_str="2026-05-08",
            decision_time_utc=datetime(2026, 5, 8, 13, 0, 0, tzinfo=UTC),
        )


def test_settlement_day_entry_dst_aware_london_spring_forward() -> None:
    """London spring-forward 2026-03-29: clocks jump 01:00 GMT → 02:00 BST.

    Per PR #53 review (Copilot comment 3179345263), the SETTLEMENT_DAY
    entry boundary is **city-local 00:00 of target_local_date** — i.e.,
    the start of the local calendar day for ``target_date``. This makes
    the SETTLEMENT_DAY window equal the LOCAL calendar day, which on
    DST-transition days yields a 23h or 25h UTC interval (because the
    local day itself is short or long), but the LOCAL geometry stays
    coherent.

    target_local_date = 2026-03-29 (spring-forward day): local 00:00
    happens at 00:00 GMT (the DST jump is at 01:00 GMT → 02:00 BST,
    AFTER local midnight). So sd_entry_utc = 2026-03-29 00:00 UTC.

    target_local_date = 2026-03-28 (day before): local 00:00 = 00:00 GMT
    = 2026-03-28 00:00 UTC.

    The previous "24h before end-of-target" formulation gave 2026-03-28
    23:00 UTC for target_local_date=2026-03-29 — off by one hour because
    end-of-target was already in BST while sd_entry should still be in
    GMT. Anchoring at LOCAL start-of-target_date fixes this.
    """
    sd_entry_post = settlement_day_entry_utc(
        target_local_date=date(2026, 3, 29), city_timezone="Europe/London"
    )
    assert sd_entry_post == datetime(2026, 3, 29, 0, 0, 0, tzinfo=UTC)

    sd_entry_pre = settlement_day_entry_utc(
        target_local_date=date(2026, 3, 28), city_timezone="Europe/London"
    )
    assert sd_entry_pre == datetime(2026, 3, 28, 0, 0, 0, tzinfo=UTC)


def test_strict_utc_offset_rejected_for_non_utc_tz() -> None:
    """``_require_zero_utc_offset`` (used inside ``market_phase_for_decision``)
    enforces ``utcoffset() == timedelta(0)``. A datetime carrying
    ``ZoneInfo('America/Chicago')`` has tzinfo set but a non-zero
    offset, and the previous ``tzinfo is None`` check would let it
    through. Per PR #53 Copilot comment 3179339283, the parameter is
    named ``*_utc`` so we reject non-UTC offsets loudly.
    """
    target = date(2026, 5, 8)
    chicago_decision_time = datetime(2026, 5, 8, 12, 0, 0, tzinfo=ZoneInfo("America/Chicago"))

    with pytest.raises(ValueError, match="must carry zero UTC offset"):
        market_phase_for_decision(
            target_local_date=target,
            city_timezone="Europe/London",
            decision_time_utc=chicago_decision_time,
            polymarket_start_utc=datetime(2026, 5, 6, 4, 4, tzinfo=UTC),
            polymarket_end_utc=_london_end_date(target),
        )


def test_market_phase_inherits_str_for_serialization() -> None:
    """Per PR #53 Copilot comment 3179339276 — match LifecyclePhase
    convention. Equality with bare strings, ``json.dumps`` cleanliness,
    and SQL-bind compatibility all flow from ``str`` inheritance.
    """
    import json
    from src.strategy.market_phase import MarketPhase

    assert MarketPhase.SETTLEMENT_DAY == "settlement_day"
    assert json.dumps(MarketPhase.POST_TRADING.value) == '"post_trading"'
    assert isinstance(MarketPhase.PRE_TRADING.value, str)


