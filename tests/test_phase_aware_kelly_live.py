# Created: 2026-05-04
# Last reused/audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A6 (Phase-aware Kelly LIVE) + PLAN_v3 §6.P5 (resolver formula) + 2026-05-17 live opening_hunt no-order sizing relationship.
"""Phase-aware Kelly LIVE resolver regression antibodies (PLAN.md §A6).

Pre-A6 the Kelly multiplier resolution was a single dict lookup:
``STRATEGY_KELLY_MULTIPLIERS[strategy_key]``. Bug review §6.7 + the
design failure surfaced in PLAN_v3 §3 demanded four authority sources
to combine at open-time:

  m_strategy_phase    = registry.get(key).kelly_for_phase(market_phase)
  m_oracle            = oracle_penalty.get_oracle_info(city, metric).penalty_multiplier
  m_observed_fraction = max(0.3, observed_target_day_fraction(...)) for settlement_capture only; 1.0 for opening_inertia
  m_phase_source      = 0.7 if phase_source == "fallback_f1" else 1.0

  kelly_multiplier    = m_strategy_phase × m_oracle × m_observed_fraction × m_phase_source

These tests pin:

1. Each factor reads from its declared authority (registry, oracle,
   city-local-day arithmetic, evidence) — no factor silently defaults
   to 1.0.
2. Factor short-circuit: an inner factor of 0.0 (e.g. blocked phase,
   blacklisted oracle) returns the whole multiplier as 0.0 without
   spending time on remaining factors.
3. East/west asymmetry: at a fixed UTC instant, eastward cities
   (Wellington) have higher observed_target_day_fraction than
   westward cities (LA) because the target-day's local clock is
   further along.
4. fallback_f1 phase_source applies a 0.7× haircut.
5. observed_target_day_fraction is clamped to [0.0, 1.0] AND the
   resolver applies the 0.3 floor for settlement_capture (so day-start
   cases retain at least 30% Kelly — operator-tunable via
   OBSERVED_FRACTION_MIN). opening_inertia uses market-age/phase policy,
   not target-day observation progress.
6. DST handling: target_local_end - target_local_start is the actual
   local-day length (23h/24h/25h), not a fixed 24h window.
7. Migration policy: this resolver is invoked at NEW open-time;
   existing positions retain whatever multiplier was on
   decision_chain.kelly_multiplier_used at THEIR open-time. (Pinned
   by absence: this test file does not call any "recompute existing
   position" helper because none exists.)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.strategy import oracle_penalty, strategy_profile
from src.strategy.kelly import (
    FALLBACK_F1_HAIRCUT,
    OBSERVED_FRACTION_MIN,
    observed_target_day_fraction,
    phase_aware_kelly_multiplier,
)


@dataclass
class _City:
    """Minimal city stub matching the cycle_runtime city object's
    attribute surface (``name``, ``timezone``)."""
    name: str
    timezone: str


@pytest.fixture(autouse=True)
def _force_fresh_registry_and_oracle(monkeypatch, tmp_path):
    """Each test gets a freshly loaded registry + isolated oracle path."""
    strategy_profile._reload_for_test()
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    oracle_penalty._reset_for_test()
    yield
    strategy_profile._reload_for_test()
    oracle_penalty._reset_for_test()


def _write_oracle_high(tmp_path: Path, city_name: str, *, n: int, m: int) -> None:
    """Plant an oracle record so the resolver picks up a known oracle multiplier."""
    target = tmp_path / "data" / "oracle_error_rates.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({city_name: {"high": {"n": n, "mismatches": m}}}))


# ── observed_target_day_fraction ───────────────────────────────────── #


def test_observed_fraction_at_local_midnight_is_zero():
    """City-local 00:00 of target_date → fraction = 0 (day hasn't started)."""
    decision = datetime(2026, 5, 8, 4, 0, 0, tzinfo=timezone.utc)  # NYC EDT 00:00
    f = observed_target_day_fraction(
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        city_timezone="America/New_York",
    )
    # NYC local 00:00 of 2026-05-08 = 04:00 UTC (EDT, UTC-4).
    assert f == pytest.approx(0.0, abs=1e-6)


def test_observed_fraction_at_local_midday_is_half():
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)  # NYC EDT 12:00
    f = observed_target_day_fraction(
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        city_timezone="America/New_York",
    )
    assert f == pytest.approx(0.5, abs=1e-6)


def test_observed_fraction_clamped_below_zero():
    decision = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)  # before target_date local start
    f = observed_target_day_fraction(
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        city_timezone="America/New_York",
    )
    assert f == 0.0


def test_observed_fraction_clamped_above_one():
    decision = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)  # well after target_date local end
    f = observed_target_day_fraction(
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        city_timezone="America/New_York",
    )
    assert f == 1.0


def test_observed_fraction_naive_datetime_rejected():
    naive = datetime(2026, 5, 8, 12, 0, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        observed_target_day_fraction(
            decision_time_utc=naive,
            target_local_date=date(2026, 5, 8),
            city_timezone="America/New_York",
        )


def test_observed_fraction_east_west_asymmetry():
    """At fixed UTC instant, an east-of-UTC city's local-day-fraction
    is greater than a west-of-UTC city's. PLAN_v3 §3 east-west
    asymmetry rationale + Bug review §6.7."""
    fixed_instant = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    f_wellington = observed_target_day_fraction(
        decision_time_utc=fixed_instant,
        target_local_date=date(2026, 5, 8),
        city_timezone="Pacific/Auckland",
    )
    f_la = observed_target_day_fraction(
        decision_time_utc=fixed_instant,
        target_local_date=date(2026, 5, 8),
        city_timezone="America/Los_Angeles",
    )
    assert f_wellington > f_la, (
        f"east-of-UTC fraction ({f_wellington:.3f}) must exceed west-of-UTC "
        f"fraction ({f_la:.3f}) at the same UTC instant"
    )


def test_observed_fraction_dst_spring_forward_day():
    """On a DST spring-forward day the local day is 23h elapsed UTC.
    The denominator must be the ACTUAL UTC duration (23h), not a fixed
    24h — anchoring on 24h would underreport the fraction at midday on
    DST days, mispricing settlement_capture entries.

    NYC 2026-03-08 spring-forward: local 02:00 EST -> 03:00 EDT
    (1 wall-clock hour skipped). The actual local day in UTC terms:
      target_local_start = NYC 00:00 EST = 05:00 UTC on 2026-03-08
      target_local_end   = NYC 00:00 EDT = 04:00 UTC on 2026-03-09
      total UTC duration = 23h

    At decision_time = 15:30 UTC (= NYC 11:30 EDT after the jump):
      elapsed_utc = 15:30 - 05:00 = 10.5h
      fraction = 10.5 / 23 ≈ 0.4565

    A regression that uses wall-clock subtraction (Python's default for
    ZoneInfo-aware datetimes) would yield 11.5/24 = 0.4792 — close but
    silently biased. The 0.005 tolerance is tight enough to catch that
    drift.
    """
    decision = datetime(2026, 3, 8, 15, 30, 0, tzinfo=timezone.utc)
    f = observed_target_day_fraction(
        decision_time_utc=decision,
        target_local_date=date(2026, 3, 8),
        city_timezone="America/New_York",
    )
    expected = 10.5 / 23.0
    assert f == pytest.approx(expected, abs=0.005)


# ── phase_aware_kelly_multiplier — happy paths ─────────────────────── #


def test_resolver_settlement_capture_full_kelly(tmp_path):
    """settlement_capture × SETTLEMENT_DAY × verified_gamma × OK oracle ×
    midday fraction = 1.0 × 1.0 × 0.5 × 1.0 = 0.5 (modulo small DST/tz).
    """
    _write_oracle_high(tmp_path, "NYC", n=200, m=0)  # OK status (p95<0.05)
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)  # NYC 12:00 EDT

    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    # m_strategy_phase=1.0 (settlement_capture × settlement_day) ×
    # m_oracle=1.0 (OK status) × m_fraction=0.5 (midday) × m_phase_source=1.0 = 0.5
    assert mult == pytest.approx(0.5, abs=1e-6)


def test_resolver_fallback_f1_haircut(tmp_path):
    """phase_source=fallback_f1 applies the 0.7× haircut."""
    _write_oracle_high(tmp_path, "NYC", n=200, m=0)
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)

    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="fallback_f1",
    )
    # 1.0 × 1.0 × 0.5 × 0.7 = 0.35
    assert mult == pytest.approx(0.5 * FALLBACK_F1_HAIRCUT, abs=1e-6)


def test_resolver_observed_fraction_floor_at_day_start(tmp_path):
    """At city-local 00:00 the raw fraction is 0; resolver clamps at
    OBSERVED_FRACTION_MIN (0.3)."""
    _write_oracle_high(tmp_path, "NYC", n=200, m=0)
    nyc = _City(name="NYC", timezone="America/New_York")
    # NYC 00:00 EDT = 04:00 UTC.
    decision = datetime(2026, 5, 8, 4, 0, 0, tzinfo=timezone.utc)

    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    # 1.0 × 1.0 × max(0.3, 0.0) × 1.0 = 0.3 (the floor)
    assert mult == pytest.approx(OBSERVED_FRACTION_MIN, abs=1e-6)


def test_opening_inertia_pre_settlement_day_ignores_target_day_observed_fraction(tmp_path):
    """opening_inertia is an opening-tick alpha, not an observation-speed alpha.

    Pre-settlement-day opening_hunt candidates must not be multiplied by target-day
    observed fraction. The strategy profile already applies half Kelly for this
    phase; adding the target-day floor again suppresses live opening orders before
    the target day can begin.
    """
    _write_oracle_high(tmp_path, "NYC", n=200, m=0)
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)

    mult = phase_aware_kelly_multiplier(
        strategy_key="opening_inertia",
        market_phase="pre_settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )

    assert mult == pytest.approx(0.5, abs=1e-6)


def test_opening_inertia_missing_oracle_keeps_oracle_penalty_without_fraction(tmp_path):
    """Missing oracle remains a 0.5 penalty, but not 0.5 × observed-day floor."""
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)

    mult = phase_aware_kelly_multiplier(
        strategy_key="opening_inertia",
        market_phase="pre_settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )

    assert mult == pytest.approx(0.25, abs=1e-6)


# ── short-circuits ─────────────────────────────────────────────────── #


def test_resolver_unknown_strategy_returns_zero(tmp_path):
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)
    mult = phase_aware_kelly_multiplier(
        strategy_key="not_a_strategy",
        market_phase="settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert mult == 0.0


def test_resolver_blocked_phase_short_circuits(tmp_path):
    """settlement_capture × post_trading = registry override 0.0; resolver
    returns 0.0 without consulting oracle."""
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)
    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="post_trading",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert mult == 0.0


def test_resolver_blacklisted_oracle_short_circuits(tmp_path):
    """Oracle BLACKLIST → penalty_multiplier=0; resolver returns 0
    even when strategy + phase + fraction would otherwise yield nonzero."""
    _write_oracle_high(tmp_path, "Shenzhen", n=25, m=10)  # BLACKLIST
    shz = _City(name="Shenzhen", timezone="Asia/Shanghai")
    decision = datetime(2026, 5, 8, 4, 0, 0, tzinfo=timezone.utc)

    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=shz,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert mult == 0.0


def test_resolver_low_metric_unsupported_returns_zero(tmp_path):
    """temperature_metric=low → oracle returns METRIC_UNSUPPORTED with
    penalty_multiplier=0; resolver short-circuits to 0 (PLAN.md D-3)."""
    _write_oracle_high(tmp_path, "NYC", n=200, m=0)
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)
    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=nyc,
        temperature_metric="low",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert mult == 0.0


def test_resolver_missing_oracle_applies_half_kelly(tmp_path):
    """Oracle MISSING → penalty_multiplier=0.5 (Beta(1,1) prior posterior_mean).
    Combined: 1.0 × 0.5 × 0.5 × 1.0 = 0.25.
    """
    # No oracle file under tmp_path -> all cities MISSING.
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)  # midday

    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert mult == pytest.approx(0.25, abs=1e-6)


# ── east/west asymmetry round-trip through the full resolver ───────── #


def test_resolver_east_west_asymmetry_at_fixed_utc(tmp_path):
    """Wellington vs LA at the same UTC instant: Wellington's resolver
    output exceeds LA's because Wellington's local target_day is further
    along — phase=settlement_day, oracle=OK, but fraction differs.
    """
    _write_oracle_high(tmp_path, "Wellington", n=200, m=0)
    _write_oracle_high(tmp_path, "Los Angeles", n=200, m=0)

    wellington = _City(name="Wellington", timezone="Pacific/Auckland")
    la = _City(name="Los Angeles", timezone="America/Los_Angeles")
    decision = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

    # Reload oracle state since we wrote two records.
    oracle_penalty._reset_for_test()

    m_wellington = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=wellington,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    m_la = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=la,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert m_wellington > m_la, (
        f"Wellington Kelly ({m_wellington:.3f}) must exceed LA Kelly "
        f"({m_la:.3f}) at the same UTC instant — east-of-UTC fraction higher"
    )


# ── deterministic fixture matrix ───────────────────────────────────── #


@pytest.mark.parametrize(
    "phase,phase_source,oracle_n,oracle_m,expected_floor",
    [
        ("settlement_day",     "verified_gamma", 200, 0,  0.3),    # full path; fraction floor at day start
        ("settlement_day",     "fallback_f1",    200, 0,  0.21),   # 0.3 × 0.7
        ("pre_settlement_day", "verified_gamma", 200, 0,  0.15),   # 0.5 × 1.0 × 0.3
        ("post_trading",       "verified_gamma", 200, 0,  0.0),    # registry blocks
        ("resolved",           "onchain_resolved", 200, 0, 0.0),   # registry blocks
    ],
)
def test_resolver_parametrized_floor_matrix(
    tmp_path, phase, phase_source, oracle_n, oracle_m, expected_floor
):
    """At day-start (UTC = NYC local 00:00) the fraction clamps to 0.3.
    Each (phase, phase_source) pair yields a known floor product. A
    regression that drops a factor would fail one or more rows."""
    _write_oracle_high(tmp_path, "NYC", n=oracle_n, m=oracle_m)
    nyc = _City(name="NYC", timezone="America/New_York")
    decision = datetime(2026, 5, 8, 4, 0, 0, tzinfo=timezone.utc)  # NYC 00:00

    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase=phase,
        city=nyc,
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source=phase_source,
    )
    assert mult == pytest.approx(expected_floor, abs=1e-6)


# ─────────────────────────────────────────────────────────────────── #
#  M4 critic R6: positive antibody against migration-by-absence regress  #
# ─────────────────────────────────────────────────────────────────── #


def test_M4_kelly_module_has_no_recompute_existing_position_helper():
    """M4 critic R6 pin: the migration policy in §7 of this file's
    docstring says "existing positions retain whatever multiplier was on
    decision_chain.kelly_multiplier_used at THEIR open-time". This was
    pre-fix "pinned by absence" — no test prevented a future PR from
    adding a recompute helper.

    Positive antibody: enumerate every public callable in src.strategy.kelly
    and assert that none looks like a "recompute kelly for an existing
    open position" function. The grep is loose intentionally (matches
    common naming patterns like recompute_*, rebalance_*, *_for_open_position)
    so a future regression can't sneak in under a creative name.

    If the team intentionally adds such a helper, this test must be
    updated and migration policy reviewed: do we recompute every open
    position on every resolver change? If yes, document the migration
    strategy explicitly. If no, keep the prohibition.
    """
    import inspect
    from src.strategy import kelly

    forbidden_substrings = (
        "recompute",
        "rebalance",
        "recompute_kelly",
        "for_open_position",
        "for_existing_position",
        "retroactive",
        "backfill_kelly",
    )

    public_callables = [
        name for name, obj in inspect.getmembers(kelly)
        if callable(obj) and not name.startswith("_")
    ]
    offenders = [
        name for name in public_callables
        if any(sub in name.lower() for sub in forbidden_substrings)
    ]
    assert not offenders, (
        f"Detected potential recompute-existing-position helper in "
        f"src.strategy.kelly: {offenders!r}. Migration policy (file "
        f"docstring §7) forbids retroactive Kelly recomputation. If "
        f"this was intentional, update both the test and the policy."
    )
