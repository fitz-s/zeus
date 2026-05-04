# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §6.P4 + §8 T5 (D-A two-clock unification through MarketPhase axis A) + §8 T2 (Phase A ↔ LifecyclePhase B consistency post-D-A).
"""P4 D-A two-clock unification tests (PLAN_v3 §6.P4).

The two pre-P4 D-A clocks were:

1. ``cycle_runtime.py:1501`` (DAY0_WINDOW transition) —
   ``lead_hours_to_settlement_close <= 6.0`` against city-local
   end-of-target_date.
2. ``cycle_runtime.py:2003`` (candidate filter) —
   ``hours_to_resolution < params['max_hours_to_resolution']`` against
   UTC ``endDate − now``.

These disagreed by ``(24h - city.utc_offset)``. For LA (UTC-8 winter),
clock 1 fired DAY0_WINDOW 18+h AFTER Polymarket trading already
closed (POST_TRADING).

P4 unifies both through ``MarketPhase.SETTLEMENT_DAY``:
``SETTLEMENT_DAY = [city-local 00:00 of target_date, 12:00 UTC of
target_date)``.

Flag ``ZEUS_MARKET_PHASE_DISPATCH`` (shared with P3) gates the
migration. Default OFF preserves byte-equal pre-P4 behavior at both
sites (T6 invariant). Default ON activates the unified phase axis.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.engine.dispatch import (
    filter_market_to_settlement_day,
    market_phase_dispatch_enabled,
    should_enter_day0_window,
)
from src.strategy.market_phase import (
    MarketPhase,
    market_phase_for_decision,
    settlement_day_entry_utc,
)


UTC = timezone.utc


def _market(
    *,
    city_name: str,
    city_timezone: str,
    target_date: str,
    market_end_at: str | None = "2026-05-08T12:00:00Z",
    hours_to_resolution: float | None = 5.5,
) -> dict:
    """Synthetic market dict shaped like ``market_scanner._parse_event``
    output (per critic R3 ATTACK 8 fix in PR #53)."""
    city_obj = SimpleNamespace(name=city_name, timezone=city_timezone)
    return {
        "city": city_obj,
        "target_date": target_date,
        "market_end_at": market_end_at,
        "hours_to_resolution": hours_to_resolution,
        "hours_since_open": 24.0,
        "outcomes": [],
    }


# ---------------------------------------------------------------------- #
# T6 byte-equal flag-OFF preservation (THE merge gate)
# ---------------------------------------------------------------------- #


def test_t6_filter_flag_off_returns_true_so_legacy_filter_is_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 invariant for site 2: with flag OFF,
    ``filter_market_to_settlement_day`` returns True for every market;
    the caller's legacy ``hours_to_resolution`` filter remains the sole
    authority and behavior is byte-equal to pre-P4.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    market = _market(city_name="LA", city_timezone="America/Los_Angeles", target_date="2026-05-08")
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert filter_market_to_settlement_day(market=market, decision_time_utc=decision_time) is True


def test_t6_day0_transition_flag_off_uses_legacy_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 invariant for site 1: with flag OFF, ``should_enter_day0_window``
    returns True iff ``legacy_hours_to_settlement <= 6.0`` regardless of
    market_phase. Byte-equal to pre-P4.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    decision_time = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    # legacy threshold says enter
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="America/Los_Angeles",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=5.5,
    ) is True
    # legacy threshold says don't enter
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="America/Los_Angeles",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=6.5,
    ) is False
    # legacy hours not provided → don't enter
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="America/Los_Angeles",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=None,
    ) is False


def test_t6_day0_transition_flag_off_ignores_phase_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flag-flip safety: even if flag OFF and phase WOULD say
    SETTLEMENT_DAY, the legacy threshold is the sole gate. This pins
    the byte-equal contract.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    # London target_date=2026-05-08, decision_time=2026-05-08 06:00 UTC.
    # London BST = UTC+1 in May, so 06:00 UTC = 07:00 BST. Local target
    # day started at 23:00 UTC of 2026-05-07. So phase = SETTLEMENT_DAY.
    # But legacy_hours_to_settlement=20 says don't enter — flag OFF
    # respects legacy.
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="Europe/London",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=20.0,
    ) is False


# ---------------------------------------------------------------------- #
# Site 2 — flag-ON behavior (candidate filter)
# ---------------------------------------------------------------------- #


def test_filter_flag_on_keeps_settlement_day_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # London target=2026-05-08; SETTLEMENT_DAY entry = 2026-05-07 23:00 UTC
    # (BST 24:00 = UTC 23:00). At decision_time = 2026-05-08 06:00 UTC,
    # phase is SETTLEMENT_DAY.
    market = _market(
        city_name="London",
        city_timezone="Europe/London",
        target_date="2026-05-08",
        market_end_at="2026-05-08T12:00:00Z",
    )
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert filter_market_to_settlement_day(market=market, decision_time_utc=decision_time) is True


def test_filter_flag_on_excludes_pre_settlement_day_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # LA target=2026-05-08; SETTLEMENT_DAY entry = 2026-05-08 07:00 UTC
    # (PDT UTC-7 in May). At decision_time = 2026-05-08 03:00 UTC, phase
    # is PRE_SETTLEMENT_DAY (4h before LA local target_date starts).
    market = _market(
        city_name="LA",
        city_timezone="America/Los_Angeles",
        target_date="2026-05-08",
        market_end_at="2026-05-08T12:00:00Z",
    )
    decision_time = datetime(2026, 5, 8, 3, 0, tzinfo=UTC)
    assert filter_market_to_settlement_day(market=market, decision_time_utc=decision_time) is False


def test_filter_flag_on_excludes_post_trading_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The D-A bug exemplar: legacy filter let LA markets through 18+h
    after Polymarket already closed. Flag ON cleanly excludes them.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # LA target=2026-05-08; Polymarket endDate = 12:00 UTC. At
    # decision_time = 2026-05-09 02:00 UTC (= 19:00 PDT 2026-05-08, 5h
    # before LA city-local end-of-target), legacy hours_to_settlement
    # would be ~5h ⇒ legacy fires DAY0_WINDOW. But Polymarket has been
    # closed for 14h. Phase = POST_TRADING.
    market = _market(
        city_name="LA",
        city_timezone="America/Los_Angeles",
        target_date="2026-05-08",
        market_end_at="2026-05-08T12:00:00Z",
        hours_to_resolution=-14.0,
    )
    decision_time = datetime(2026, 5, 9, 2, 0, tzinfo=UTC)
    assert filter_market_to_settlement_day(market=market, decision_time_utc=decision_time) is False


def test_filter_flag_on_excludes_market_with_none_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    market = {
        "city": None,
        "target_date": "2026-05-08",
        "market_end_at": "2026-05-08T12:00:00Z",
    }
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert filter_market_to_settlement_day(market=market, decision_time_utc=decision_time) is False


def test_filter_flag_on_fail_soft_on_naive_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A naive market_end_at would crash phase computation. Flag ON
    must fail-soft toward exclusion (False), consistent with site 4's
    fail-soft semantics. The legacy filter would have included the
    market; excluding under flag-ON is the safer side.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    market = _market(
        city_name="London",
        city_timezone="Europe/London",
        target_date="2026-05-08",
        market_end_at="2026-05-08T12:00:00",  # naive, no tz suffix
    )
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert filter_market_to_settlement_day(market=market, decision_time_utc=decision_time) is False


# ---------------------------------------------------------------------- #
# Site 1 — flag-ON behavior (DAY0_WINDOW transition)
# ---------------------------------------------------------------------- #


def test_day0_transition_flag_on_fires_in_settlement_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # London target=2026-05-08; phase=SETTLEMENT_DAY at 2026-05-08 06:00.
    # legacy_hours_to_settlement=20 (way more than 6) — flag ON ignores
    # it because phase says SETTLEMENT_DAY.
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="Europe/London",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=20.0,
    ) is True


def test_day0_transition_flag_on_does_not_fire_post_trading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The D-A bug exemplar: pre-P4, legacy 6h threshold fired
    DAY0_WINDOW at 2026-05-09 02:00 UTC for LA target=2026-05-08
    (5h before LA local end-of-target). But Polymarket has been
    POST_TRADING for 14h. Flag ON respects the phase axis and does
    NOT fire the transition.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    decision_time = datetime(2026, 5, 9, 2, 0, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="America/Los_Angeles",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=5.5,  # legacy says ENTER
    ) is False, "post-trading market must NOT enter DAY0_WINDOW under flag ON"


def test_day0_transition_flag_on_does_not_fire_pre_settlement_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # LA target=2026-05-08; phase=PRE_SETTLEMENT_DAY at 2026-05-08 03:00 UTC
    decision_time = datetime(2026, 5, 8, 3, 0, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="America/Los_Angeles",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=20.0,
    ) is False


def test_day0_transition_flag_on_falls_back_to_legacy_on_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: corrupt target_date string under flag ON falls back
    to legacy 6h threshold so a single bad position row does not
    silently freeze it out of the DAY0 transition forever.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    decision_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="not-a-date",
        city_timezone="Europe/London",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=5.5,
    ) is True
    assert should_enter_day0_window(
        target_date_str="not-a-date",
        city_timezone="Europe/London",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=10.0,
    ) is False
    # Critic R5 code-reviewer L1: parse-error path with no legacy hours.
    # The helper should fall through to legacy and report False (no
    # threshold to compare against).
    assert should_enter_day0_window(
        target_date_str="not-a-date",
        city_timezone="Europe/London",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=None,
    ) is False


# ---------------------------------------------------------------------- #
# T5 — Candidate-filter consistency (PLAN_v3 §8 T5 + INTERNAL Q5 matrix)
# ---------------------------------------------------------------------- #


def _load_cities() -> list[dict]:
    cfg = Path(__file__).resolve().parents[1] / "config" / "cities.json"
    return json.loads(cfg.read_text())["cities"]


def _city_in_settlement_day(
    *,
    city_timezone: str,
    target_local_date: date,
    decision_time_utc: datetime,
) -> bool:
    """Pure expectation: at ``decision_time_utc``, is this city's market
    for ``target_local_date`` in ``MarketPhase.SETTLEMENT_DAY``?
    """
    sd_entry = settlement_day_entry_utc(
        target_local_date=target_local_date,
        city_timezone=city_timezone,
    )
    polymarket_end = datetime.combine(
        target_local_date,
        datetime.min.time().replace(hour=12),
        tzinfo=UTC,
    )
    return sd_entry <= decision_time_utc < polymarket_end


def test_t5_settlement_day_count_matches_internal_q5_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PLAN_v3 §8 T5 + INTERNAL Q5: at every UTC hour, the count of
    cities in ``MarketPhase.SETTLEMENT_DAY`` for a same target_date is
    bounded by [0, 51]. With a single target_date in scope, the
    SETTLEMENT_DAY window is at most 24h wide, so each city is in
    settlement_day for one contiguous interval per target_date.

    This is the relationship-test floor: the candidate filter under
    flag-ON returns the SAME set of (city, target_date) pairs that
    pure phase computation would identify. Pinned via a representative
    UTC-hour matrix instead of an exhaustive 24x51 grid (the test_db
    pattern in this repo is to assert tight bounds, not full grids).
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    cities = _load_cities()
    target_local_date = date(2026, 5, 8)

    # Sample 6 representative UTC hours covering the diurnal sweep:
    # 00, 06, 09, 12, 15, 21 — these straddle the busy bands per F11.
    for hour in (0, 6, 9, 12, 15, 21):
        decision_time = datetime(2026, 5, 8, hour, 0, tzinfo=UTC)

        # Expected: cities whose phase is SETTLEMENT_DAY at this UTC.
        expected_in_phase = {
            c["name"]
            for c in cities
            if _city_in_settlement_day(
                city_timezone=c["timezone"],
                target_local_date=target_local_date,
                decision_time_utc=decision_time,
            )
        }

        # Filter result: synthetic market dict per city, then run filter.
        markets = [
            _market(
                city_name=c["name"],
                city_timezone=c["timezone"],
                target_date="2026-05-08",
                market_end_at="2026-05-08T12:00:00Z",
            )
            for c in cities
        ]
        filtered = [
            m for m in markets
            if filter_market_to_settlement_day(market=m, decision_time_utc=decision_time)
        ]
        filtered_names = {m["city"].name for m in filtered}

        assert filtered_names == expected_in_phase, (
            f"T5 broken at UTC hour {hour:02d}: filter output diverges "
            f"from pure-phase computation. "
            f"Filter set: {filtered_names}, expected: {expected_in_phase}"
        )

        # Bound check per F11: 0 <= count <= 51, and at least 12:00 UTC
        # boundary should have meaningful population (Polymarket
        # endDate boundary).
        assert 0 <= len(filtered) <= len(cities)


def test_t5_count_drops_to_zero_after_polymarket_endDate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At 12:00 UTC of target_date and beyond, ALL cities have
    transitioned to POST_TRADING. The filter must return zero
    candidates — this is the cleanest single-target invariant.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    cities = _load_cities()
    decision_time = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    markets = [
        _market(
            city_name=c["name"],
            city_timezone=c["timezone"],
            target_date="2026-05-08",
            market_end_at="2026-05-08T12:00:00Z",
        )
        for c in cities
    ]
    filtered = [
        m for m in markets
        if filter_market_to_settlement_day(market=m, decision_time_utc=decision_time)
    ]
    assert filtered == [], (
        f"All cities must be POST_TRADING at 12:00 UTC of target_date; "
        f"got {len(filtered)} cities still in filter"
    )


# ---------------------------------------------------------------------- #
# T2 — Phase A ↔ LifecyclePhase B coordination invariant (PLAN_v3 §8 T2)
# ---------------------------------------------------------------------- #


def test_t2_phase_a_b_coordination_at_day0_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T2 (axis A ↔ axis B): the per-position DAY0_WINDOW transition
    fires exactly when the market enters MarketPhase.SETTLEMENT_DAY.
    Under flag ON, this is now a *direct* relationship rather than an
    indirect 6h threshold.

    Concrete: London target=2026-05-08, SETTLEMENT_DAY entry =
    2026-05-07 23:00 UTC (BST 24:00 = UTC+1). One microsecond before:
    not yet. At entry: yes.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # 1 second before SETTLEMENT_DAY entry — should NOT fire.
    just_before = datetime(2026, 5, 7, 22, 59, 59, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="Europe/London",
        decision_time_utc=just_before,
        legacy_hours_to_settlement=25.0,
    ) is False

    # At SETTLEMENT_DAY entry — should fire.
    at_entry = datetime(2026, 5, 7, 23, 0, 0, tzinfo=UTC)
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="Europe/London",
        decision_time_utc=at_entry,
        legacy_hours_to_settlement=24.0,
    ) is True


def test_t2_phase_a_b_coordination_at_post_trading_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At Polymarket endDate (12:00 UTC), market exits SETTLEMENT_DAY
    into POST_TRADING. After this instant, no new DAY0_WINDOW
    transitions should fire even if legacy threshold says otherwise.
    Pins F1 + the D-A bug closure.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    just_after_endDate = datetime(2026, 5, 8, 12, 0, 1, tzinfo=UTC)
    # Tokyo target=2026-05-08: city-local end = 2026-05-08 15:00 UTC
    # (JST UTC+9). At 12:00:01 UTC, legacy hours_to_settlement = ~3h
    # ⇒ legacy fires DAY0_WINDOW. Phase: POST_TRADING.
    assert should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="Asia/Tokyo",
        decision_time_utc=just_after_endDate,
        legacy_hours_to_settlement=2.99,
    ) is False, "POST_TRADING must not enter DAY0_WINDOW under flag ON"


# ---------------------------------------------------------------------- #
# Flag-axis consistency: P3 + P4 share one flag (single-switch invariant)
# ---------------------------------------------------------------------- #


def test_p3_p4_share_dispatch_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per dispatch.py module docstring: P3 and P4 share one flag so an
    operator activating one cannot accidentally leave the other
    behind. This pins that invariant — both helpers read the same env
    variable.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    assert market_phase_dispatch_enabled() is True
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    assert market_phase_dispatch_enabled() is False


# ---------------------------------------------------------------------- #
# Critic R5 R4-A4-M1 carry-forward verification
# ---------------------------------------------------------------------- #


def test_attribution_drift_flag_aware_deferral_at_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critic R5 reported R4 A4-M1 as UNRESOLVED at HEAD because line
    148 of attribution_drift.py still has the legacy clause. This is
    correct AT line 148 but misleading: the flag-aware deferral at
    line 144-145 returns None BEFORE line 148 runs when flag is ON.
    The legacy clause at line 148 is correctly the flag-OFF path.
    This test pins both branches end-to-end so future reviewers don't
    re-flag this as unresolved.
    """
    from src.state.attribution_drift import (
        AttributionSignature,
        _infer_strategy_from_signature,
    )

    sig = AttributionSignature(
        position_id="pos-r5-verify",
        label_strategy="settlement_capture",
        inferred_strategy=None,
        bin_topology="point",
        direction="buy_yes",
        discovery_mode="day0_capture",
        bin_label="0-1",
        is_label_inferable=False,
    )

    # Flag ON → returns None at line 145 BEFORE legacy clause runs.
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    assert _infer_strategy_from_signature(sig) is None, (
        "R4 A4-M1: with flag ON, _infer_strategy_from_signature MUST "
        "defer (return None) — flag-aware return at line 144-145 "
        "executes BEFORE the legacy clause at line 148"
    )

    # Flag OFF → reaches the legacy clause at line 148 normally.
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    assert _infer_strategy_from_signature(sig) == "settlement_capture", (
        "R4 A4-M1: with flag OFF, the legacy clause at line 148 "
        "remains authoritative — discovery_mode='day0_capture' → "
        "'settlement_capture'"
    )


# ---------------------------------------------------------------------- #
# Critic R5 code-reviewer M1 — flag stamped on cycle summary
# ---------------------------------------------------------------------- #


def test_cycle_summary_records_dispatch_flag_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code-reviewer M1: when site 2 candidate filter runs, the cycle
    summary must record the flag state so downstream cohort
    attribution can explain step-changes in candidate count when the
    operator flips ZEUS_MARKET_PHASE_DISPATCH. Without this, the
    substrate log shows post-filter count with no audit trail.

    This test exercises the stamping logic by reading the source
    directly (the cycle is too heavy-weight to spin up here; the
    integration belongs to a runtime-level test which is out of P4
    scope per PLAN_v3 §6.P4 + critic R5 A10).
    """
    src_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "engine" / "cycle_runtime.py"
    )
    src = src_path.read_text()
    assert 'summary["market_phase_dispatch_flag"] = flag_on' in src, (
        "M1: cycle_runtime.py must stamp the dispatch flag on summary "
        "at the candidate filter site (around the if/else at line ~2036)"
    )
