# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A7 + §6 invariants I1-I8.
"""Authority-rebuild regression antibodies (PLAN.md §A7).

This file is the post-rebuild floor. Every test here pins ONE invariant
the rebuild MUST preserve under future refactors. Detailed packet-
specific tests live in their own files (test_oracle_evidence_status.py
for A3, test_strategy_profile_registry.py for A4, etc.); this file
deduplicates them into a single grep target so:

  - A future packet that touches multiple authority surfaces sees ALL
    rebuild floors in one file (one pytest invocation = full floor).
  - The critic-opus review cycle (PLAN.md §A7 + memory L11
    "feedback_critic_prompt_adversarial_template") can be told
    "verify that THESE 8 tests still pin the rebuild's invariants" —
    a smaller surface than the full test_phase_*/test_oracle_*/etc.
    fan-out and a more targeted attack list.

The 8 invariants
----------------

  I1  Missing oracle file -> NO city returns OK status (Bug review §A floor).
      Pre-A3 the silent-OK rescue from PR #40 hid a missing oracle as
      "all cities OK with mult=1.0". Post-A3 missing -> MISSING with
      mult=0.5 (Beta(1,1) prior posterior_mean = 0.5 by math, not by
      tuning).

  I2  Oracle bridge JSON schema carries (n, mismatches, posterior_mean,
      posterior_upper_95). Pre-A3 the bridge wrote only oracle_error_rate
      (point estimate) — the reader couldn't compute the posterior. The
      schema is the authority surface for every (city, metric) record;
      a regression dropping any of these fields would flip the reader to
      the legacy MISSING fallback.

  I3  StrategyProfile registry parse error -> fail-closed: no live entries
      from ANY strategy. Bug review §D + §E hinge on the registry being
      authoritative; a parse error must NOT silently fall back to a
      hardcoded default, or the divergence-by-construction property is
      lost.

  I4  phase=None under flag ON for a STRICT live caller -> raises
      PhaseAuthorityViolation (Bug review §F floor). Strict mode is the
      live-authority path; silent legacy fallback is the kill-switch path.

  I5  cohort_boundary microsecond inclusivity at PR #51 merge instant.
      Pre-instant -> pre_utc_fix; AT-instant -> post_utc_fix. An off-by-
      one boundary would silently mislabel cron firings within the
      seconds-window of the migration.

  I6  Kelly resolver deterministic on a parametrized fixture matrix.
      Same (key, phase, source, oracle, decision_time, target_date)
      tuple ALWAYS yields the same multiplier. Non-determinism here
      would silently drift sizing across cycles.

  I7  Post-trading market never enters DAY0_WINDOW under flag ON.
      The D-A two-clock unification (PR #53 P4) closed the bug where
      west-of-UTC cities re-fired DAY0 after Polymarket endDate. A
      regression flipping this back is exactly the failure mode P4
      eliminated.

  I8  Storage paths centralized: ZEUS_STORAGE_ROOT override redirects
      EVERY oracle artifact path. The pre-A2 scattered hardcoded paths
      (3 ``Path(__file__).resolve().parent.parent.parent / "data"``
      patterns) created the silent-stale-oracle bug class.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.engine.dispatch import (
    PhaseAuthorityViolation,
    is_settlement_day_dispatch,
    should_enter_day0_window,
)
from src.state.cohort_boundary import (
    ZEUS_PR51_MERGE_INSTANT_UTC,
    cohort_label,
)
from src.state.paths import (
    oracle_artifact_heartbeat_path,
    oracle_data_dir,
    oracle_error_rates_path,
    oracle_snapshot_dir,
    storage_root,
)
from src.strategy import oracle_penalty, strategy_profile
from src.strategy.kelly import phase_aware_kelly_multiplier
from src.strategy.market_phase import MarketPhase
from src.strategy.oracle_estimator import (
    classify,
    posterior_mean,
    posterior_upper_95,
)
from src.strategy.oracle_status import OracleStatus


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch, tmp_path):
    """Each invariant runs against a fresh registry + isolated storage so
    cross-test state cannot mask a regression."""
    strategy_profile._reload_for_test()
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    oracle_penalty._reset_for_test()
    yield
    strategy_profile._reload_for_test()
    oracle_penalty._reset_for_test()


def _write_oracle(tmp_path: Path, payload: dict) -> Path:
    target = tmp_path / "data" / "oracle_error_rates.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload))
    return target


# ─────────────────────────────────────────────────────────────────── #
#  I1 — Missing oracle file: no city returns OK status (Finding A)    #
# ─────────────────────────────────────────────────────────────────── #


def test_I1_missing_oracle_no_city_returns_OK():
    """Floor: a missing oracle_error_rates.json must NOT yield OK for
    any (city, metric). Bug review Finding A: pre-A3 the silent-OK
    rescue from PR #40 returned ``OracleStatus.OK`` with mult=1.0 for
    every absent record; post-A3 absent -> MISSING with mult=0.5
    (Beta(1,1) posterior_mean at N=0 = 0.5 by math, see PLAN.md §5).

    A regression that re-introduces the silent-OK rescue would be the
    canonical failure mode the rebuild closed.
    """
    # No file written under tmp_path -> every city absent.
    cities = ["NYC", "Shenzhen", "Wellington", "London", "Tokyo", "Sao Paulo"]
    for city in cities:
        info = oracle_penalty.get_oracle_info(city, "high")
        assert info.status != OracleStatus.OK, (
            f"{city}/high regressed to OK status on missing file — "
            f"the PR #40 silent-OK rescue this rebuild closed"
        )
        assert info.status == OracleStatus.MISSING
        assert info.penalty_multiplier == 0.5


# ─────────────────────────────────────────────────────────────────── #
#  I2 — Bridge JSON schema carries (n, mismatches, posterior fields)  #
# ─────────────────────────────────────────────────────────────────── #


def test_I2_bridge_schema_carries_evidence_fields(tmp_path):
    """Floor: the post-A3 oracle JSON schema MUST carry n + mismatches
    at the city.metric level. Without them the reader cannot compute
    the Beta-binomial posterior and falls back to MISSING regardless
    of the empirical rate.

    Pre-A3 the bridge wrote only ``oracle_error_rate`` (point estimate);
    A3 added n + mismatches; reader treats records lacking these as
    MISSING. Antibody: a record WITH n+mismatches must classify per
    the posterior, NOT degrade to MISSING.
    """
    # Synthetic post-A3 schema record.
    _write_oracle(tmp_path, {"NYC": {"high": {"n": 200, "mismatches": 4}}})
    info = oracle_penalty.get_oracle_info("NYC", "high")
    # n=200, m=4 -> p95 ≈ 0.046 -> OK (m=0 case wins via classifier... wait, m>=1 here).
    # Actually m=4 > 0 so the classifier goes through the m>=1 branch.
    # p95 = 0.046, < 0.05, so INCIDENTAL.
    assert info.status == OracleStatus.INCIDENTAL
    assert info.n == 200
    assert info.mismatches == 4
    # Round-trip: posterior_mean fields populated, NOT MISSING fallback.
    assert info.posterior_mean > 0.0
    assert info.posterior_upper_95 > 0.0


def test_I2_legacy_record_without_n_degrades_to_MISSING(tmp_path):
    """Inverse of the above: a pre-A3 record (only oracle_error_rate, no
    n) MUST degrade to MISSING. The empirical rate alone cannot bound
    the posterior — accepting it would silently let pre-A3 files drive
    post-A3 sizing without evidence.
    """
    _write_oracle(tmp_path, {"NYC": {"high": {"oracle_error_rate": 0.04}}})
    info = oracle_penalty.get_oracle_info("NYC", "high")
    assert info.status == OracleStatus.MISSING


# ─────────────────────────────────────────────────────────────────── #
#  I3 — Registry parse error: fail-closed on every strategy           #
# ─────────────────────────────────────────────────────────────────── #


def test_I3_registry_parse_error_fails_closed(tmp_path):
    """Floor: a malformed strategy_profile_registry.yaml MUST raise
    RegistrySchemaError at load time, NOT fall back to a hardcoded
    default. The single-source registry property hinges on this:
    silent fallback re-introduces the divergence Bug review §E flagged.
    """
    bad = tmp_path / "broken.yaml"
    bad.write_text("settlement_capture:\n  thesis: ok\n  live_status: maybe_live\n")
    with pytest.raises(strategy_profile.RegistrySchemaError):
        strategy_profile._reload_for_test(bad)


def test_I3_unknown_strategy_kelly_returns_zero():
    """Floor: phase_aware_kelly_multiplier on an unknown strategy_key
    returns 0.0. Live entries gated on this check fail-close — the
    typo'd strategy_key in a caller cannot fall through to a non-zero
    multiplier."""
    from datetime import date as _date

    class _C:
        name = "NYC"
        timezone = "America/New_York"

    mult = phase_aware_kelly_multiplier(
        strategy_key="not_a_strategy",
        market_phase="settlement_day",
        city=_C(),
        temperature_metric="high",
        decision_time_utc=datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc),
        target_local_date=_date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    assert mult == 0.0


# ─────────────────────────────────────────────────────────────────── #
#  I4 — phase=None + flag ON + strict caller raises (Finding F)       #
# ─────────────────────────────────────────────────────────────────── #


class _FakeCandidate:
    def __init__(self, *, market_phase=None, discovery_mode="opening_hunt"):
        self.market_phase = market_phase
        self.discovery_mode = discovery_mode
        self.condition_id = "0xfake"


def test_I4_strict_dispatch_raises_on_phase_none_under_flag_on(monkeypatch):
    """Bug review Finding F floor: a live-authority caller refuses
    silent fallback when phase tagging fails under flag ON. The
    PhaseAuthorityViolation exception is the typed signal.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    candidate = _FakeCandidate(market_phase=None)
    with pytest.raises(PhaseAuthorityViolation, match="market_phase=None"):
        is_settlement_day_dispatch(candidate, strict=True)


def test_I4_non_strict_remains_failsoft_for_back_compat(monkeypatch):
    """Default (non-strict) dispatch MUST still fall back to legacy on
    phase=None — preserves the migration's "test fixtures and off-cycle
    construction don't break" property. Strict mode is opt-in for
    live-authority callers."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    candidate = _FakeCandidate(market_phase=None, discovery_mode="day0_capture")
    # Non-strict: phase=None -> defers to legacy -> day0_capture -> True
    assert is_settlement_day_dispatch(candidate, strict=False) is True


# ─────────────────────────────────────────────────────────────────── #
#  I5 — cohort_boundary microsecond inclusivity at PR #51 instant     #
# ─────────────────────────────────────────────────────────────────── #


def test_I5_cohort_boundary_microsecond_inclusivity():
    """Floor: PR #51 merge instant `2026-05-04T03:57:08Z` is the cohort
    discriminator. Microsecond before -> pre_utc_fix; AT-instant ->
    post_utc_fix; microsecond after -> post_utc_fix.

    A regression flipping the boundary direction (e.g., changing `<`
    to `<=` in cohort_pre_utc_fix) would silently swap which side of
    the migration window cron firings get attributed to. Multiply that
    by 50 cities × hourly cycles = ~1200 mislabeled rows per day.
    """
    one_us = timedelta(microseconds=1)
    assert cohort_label(ZEUS_PR51_MERGE_INSTANT_UTC - one_us) == "pre_utc_fix"
    assert cohort_label(ZEUS_PR51_MERGE_INSTANT_UTC) == "post_utc_fix"
    assert cohort_label(ZEUS_PR51_MERGE_INSTANT_UTC + one_us) == "post_utc_fix"


def test_I5_cohort_boundary_value_pinned_to_git_fact():
    """Pin the boundary instant to the git-log fact (PR #51 merge time
    e62710e6 -> 2026-05-04T03:57:08Z). A regression that updates this
    constant for a synthetic reason (e.g., daylight-savings adjustment)
    would invalidate every prior attribution report cohort-keyed on the
    old value.
    """
    assert ZEUS_PR51_MERGE_INSTANT_UTC == datetime(
        2026, 5, 4, 3, 57, 8, tzinfo=timezone.utc
    )


# ─────────────────────────────────────────────────────────────────── #
#  I6 — Kelly resolver deterministic on parametrized fixture matrix   #
# ─────────────────────────────────────────────────────────────────── #


@pytest.mark.parametrize(
    "key,phase,phase_source,city_name,city_tz,decision_utc,target_date,expected",
    [
        # settlement_capture × settlement_day × verified_gamma × NYC midday × OK oracle
        ("settlement_capture", "settlement_day", "verified_gamma", "NYC",
         "America/New_York", datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc),
         date(2026, 5, 8), 0.5),
        # settlement_capture × settlement_day × fallback_f1 (0.7×) × NYC midday × OK
        ("settlement_capture", "settlement_day", "fallback_f1", "NYC",
         "America/New_York", datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc),
         date(2026, 5, 8), 0.5 * 0.7),
        # opening_inertia × pre_settlement_day × verified_gamma × NYC midday
        # phase override 0.5 × oracle 1.0 (OK) × fraction 0.5 (midday) ×
        # phase_source 1.0 = 0.25
        ("opening_inertia", "pre_settlement_day", "verified_gamma", "NYC",
         "America/New_York", datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc),
         date(2026, 5, 8), 0.25),
        # blocked phase short-circuits to 0
        ("settlement_capture", "post_trading", "verified_gamma", "NYC",
         "America/New_York", datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc),
         date(2026, 5, 8), 0.0),
        # blocked strategy short-circuits to 0
        ("shoulder_buy", "settlement_day", "verified_gamma", "NYC",
         "America/New_York", datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc),
         date(2026, 5, 8), 0.0),
    ],
)
def test_I6_kelly_resolver_deterministic_matrix(
    tmp_path, key, phase, phase_source, city_name, city_tz, decision_utc, target_date, expected
):
    """Floor: same (key, phase, source, city, decision_time, target_date)
    tuple ALWAYS yields the same multiplier. The resolver is a pure
    function of its inputs — non-determinism here (e.g., from a stray
    ``datetime.now()`` call inside the resolver) would silently drift
    sizing across cycles."""
    _write_oracle(tmp_path, {city_name: {"high": {"n": 200, "mismatches": 0}}})

    class _C:
        def __init__(self, name, tz):
            self.name = name
            self.timezone = tz

    city = _C(city_name, city_tz)
    mult_a = phase_aware_kelly_multiplier(
        strategy_key=key,
        market_phase=phase,
        city=city,
        temperature_metric="high",
        decision_time_utc=decision_utc,
        target_local_date=target_date,
        phase_source=phase_source,
    )
    mult_b = phase_aware_kelly_multiplier(
        strategy_key=key,
        market_phase=phase,
        city=city,
        temperature_metric="high",
        decision_time_utc=decision_utc,
        target_local_date=target_date,
        phase_source=phase_source,
    )
    assert mult_a == mult_b, "resolver must be deterministic for fixed inputs"
    assert mult_a == pytest.approx(expected, abs=1e-6)


# ─────────────────────────────────────────────────────────────────── #
#  I7 — Post-trading market never enters DAY0_WINDOW under flag ON     #
# ─────────────────────────────────────────────────────────────────── #


def test_I7_post_trading_never_enters_day0_under_flag_on(monkeypatch):
    """D-A regression antibody: pre-PR-#53-P4, west-of-UTC cities could
    re-fire DAY0_WINDOW after Polymarket endDate (since the legacy
    ``hours_to_resolution < 6`` clock was UTC-anchored, while DAY0_WINDOW
    transitions used city-local end-of-target_date). The unification at
    PR #53 P4 closed this; A6 made it the live default.

    Antibody: a market in POST_TRADING phase under flag ON MUST return
    False from should_enter_day0_window, even if the legacy
    hours_to_settlement_close hint says otherwise.
    """
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    # decision_time well after Polymarket endDate (12:00 UTC).
    decision_time = datetime(2026, 5, 8, 18, 0, 0, tzinfo=timezone.utc)

    # Even with a legacy hint claiming "1 hour to settlement", phase is
    # POST_TRADING and dispatch must respect that under flag ON.
    result = should_enter_day0_window(
        target_date_str="2026-05-08",
        city_timezone="America/Los_Angeles",
        decision_time_utc=decision_time,
        legacy_hours_to_settlement=1.0,
        legacy_threshold_hours=6.0,
    )
    assert result is False, (
        "post-trading market re-entered DAY0_WINDOW under flag ON — "
        "the D-A two-clock unification regressed"
    )


# ─────────────────────────────────────────────────────────────────── #
#  I8 — Storage paths centralized through paths.py                    #
# ─────────────────────────────────────────────────────────────────── #


def test_I8_storage_root_override_redirects_every_oracle_path(monkeypatch, tmp_path):
    """Floor: a single ZEUS_STORAGE_ROOT env override redirects EVERY
    oracle artifact path coherently. Pre-A2 the 3 callsites
    (oracle_penalty, bridge, listener) had separate hardcoded
    ``Path(__file__).resolve().parent.parent.parent / "data"`` patterns;
    a writer pointed at one root while a reader looked at another
    silently created the stale-oracle bug class.
    """
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    assert storage_root() == tmp_path.resolve()
    assert oracle_data_dir() == tmp_path.resolve() / "data"
    assert oracle_error_rates_path() == tmp_path.resolve() / "data" / "oracle_error_rates.json"
    assert (
        oracle_artifact_heartbeat_path()
        == tmp_path.resolve() / "data" / "oracle_error_rates.heartbeat.json"
    )
    assert oracle_snapshot_dir() == tmp_path.resolve() / "raw" / "oracle_shadow_snapshots"


def test_I8_oracle_penalty_picks_up_storage_root_override(monkeypatch, tmp_path):
    """End-to-end: oracle_penalty._load uses the override root via the
    path builder, NOT a captured-at-import constant. A regression that
    re-introduces a module-level path constant would survive isolated
    unit tests but fail this integration probe."""
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    _write_oracle(tmp_path, {"NYC": {"high": {"n": 25, "mismatches": 10}}})
    oracle_penalty._reset_for_test()
    info = oracle_penalty.get_oracle_info("NYC", "high")
    assert info.status == OracleStatus.BLACKLIST


# ─────────────────────────────────────────────────────────────────── #
#  Cross-rebuild smoke: end-to-end through 3 of the rebuild surfaces  #
# ─────────────────────────────────────────────────────────────────── #


def test_end_to_end_resolver_through_registry_and_oracle(tmp_path):
    """Single-fixture probe that exercises:
      - StrategyProfile registry (A4): kelly_for_phase lookup
      - Oracle evidence-grade (A3): n + mismatches -> Beta-binomial -> status
      - Phase-aware Kelly resolver (A6): combines registry + oracle +
        observed_fraction + phase_source

    A regression in any one of those would surface here as a wrong
    multiplier; the parametrized I6 above pins specific values, this
    smoke test pins the END-TO-END causal chain that links them.
    """
    _write_oracle(tmp_path, {"NYC": {"high": {"n": 200, "mismatches": 4}}})

    class _C:
        name = "NYC"
        timezone = "America/New_York"

    decision = datetime(2026, 5, 8, 16, 0, 0, tzinfo=timezone.utc)  # NYC midday EDT
    mult = phase_aware_kelly_multiplier(
        strategy_key="settlement_capture",
        market_phase="settlement_day",
        city=_C(),
        temperature_metric="high",
        decision_time_utc=decision,
        target_local_date=date(2026, 5, 8),
        phase_source="verified_gamma",
    )
    # m_strategy_phase = registry.kelly_phase_overrides["settlement_day"] = 1.0
    # m_oracle: n=200, m=4 -> p95 ≈ 0.046 -> INCIDENTAL -> mult=1.0
    # m_observed_fraction = 0.5 (NYC midday)
    # m_phase_source = 1.0 (verified_gamma)
    # product = 1.0 × 1.0 × 0.5 × 1.0 = 0.5
    assert mult == pytest.approx(0.5, abs=1e-6)


def test_oracle_estimator_sanity_anchors():
    """Math anchors that the rest of the rebuild rests on. PLAN.md §5
    derivation requires:
      - Beta(1,1) prior posterior_mean(0,0) = 0.5 (= MISSING multiplier)
      - posterior_upper_95 monotone in m and -monotone in n
    A regression that switches the prior or the math would surface
    here before any Kelly multiplier drift makes it to live sizing.
    """
    assert posterior_mean(0, 0) == pytest.approx(0.5)
    assert posterior_upper_95(0, 0) == pytest.approx(0.95, abs=1e-6)

    # Monotone in m at fixed n
    assert posterior_upper_95(0, 100) < posterior_upper_95(5, 100) < posterior_upper_95(50, 100)
    # Anti-monotone in n at fixed m=0
    assert posterior_upper_95(0, 10) > posterior_upper_95(0, 100) > posterior_upper_95(0, 1000)


# ─────────────────────────────────────────────────────────────────── #
#  H2 critic R6: cycle_runtime strategy structures derive from registry  #
# ─────────────────────────────────────────────────────────────────── #


def test_H2_cycle_runtime_canonical_keys_match_registry_live_safe():
    """H2 critic R6 pin: cycle_runtime's canonical strategy set must equal
    ``strategy_profile.live_safe_keys()``. Pre-fix this was a hardcoded
    4-element frozenset at cycle_runtime.py:40 — Bug review §D's
    "strategy identity scattered" anti-pattern.

    The HARDCODE COULD reappear (e.g., a tired engineer adds a 5th
    strategy and back-fills the frozenset for symmetry). That regression
    would split brain between registry and runtime; this test forces
    the equality so the runtime can never drift independently.
    """
    from src.engine import cycle_runtime
    from src.strategy.strategy_profile import live_safe_keys

    assert cycle_runtime._canonical_strategy_keys() == live_safe_keys()


def test_H2_cycle_runtime_inverse_map_matches_registry_dispatch_modes():
    """H2 critic R6 pin: cycle_runtime's discovery_mode -> strategies map
    must equal ``strategy_profile.cycle_axis_dispatch_inverse()``.
    Pre-fix this was a hardcoded dict at cycle_runtime.py:46 — the 7th
    unmigrated site flagged in the rebuild review.

    A future change that adds a strategy to the registry's
    cycle_axis_dispatch_mode field MUST surface in cycle_runtime
    automatically; a regression that re-hardcodes the map breaks that
    contract."""
    from src.engine import cycle_runtime
    from src.strategy.strategy_profile import cycle_axis_dispatch_inverse

    assert cycle_runtime._strategy_keys_by_discovery_mode() == cycle_axis_dispatch_inverse()


def test_H2_cycle_runtime_no_hardcoded_strategy_string_literals():
    """Source-grep antibody: cycle_runtime.py must not contain strategy
    name string literals at module top-level. Catches the
    most-likely-future-regression pattern: someone re-introduces
    ``frozenset({"settlement_capture", ...})`` because it's fast.

    Search is targeted to TOP-LEVEL frozenset/set/dict literals — not
    docstrings (which legitimately reference strategy names) — by
    rejecting only patterns that look like Python set literals
    containing a strategy name within the first 20 lines after the
    initial imports.

    Allowed: function bodies that reference live_safe_keys() etc.
    Allowed: docstrings/comments mentioning strategy names.
    Forbidden: ``CANONICAL_STRATEGY_KEYS = {"settlement_capture", ...}``.
    """
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "engine" / "cycle_runtime.py"
    ).read_text()

    # Find the first def or class line — we only care about module-level
    # data structures BEFORE the first function. (Function bodies may
    # legitimately enumerate strategies for dispatch; module-level
    # hardcodes are the regression we're catching.)
    lines = src.splitlines()
    cutoff = None
    for i, line in enumerate(lines):
        if line.startswith(("def ", "class ")):
            cutoff = i
            break
    assert cutoff is not None, "cycle_runtime.py must define functions"
    header = "\n".join(lines[:cutoff])

    # Reject any hardcoded set/frozenset/list assignment that contains a
    # known live strategy name. The migration replaced these with helper
    # function calls.
    import re
    hardcode_pattern = re.compile(
        r"=\s*(?:frozenset\s*\(\s*\{|\{|\[)[^}\]]*"
        r'"(?:settlement_capture|center_buy|opening_inertia|shoulder_sell)"',
        re.MULTILINE,
    )
    matches = hardcode_pattern.findall(header)
    assert not matches, (
        "Module-level hardcoded strategy set detected in cycle_runtime.py: "
        f"{matches!r}. Use strategy_profile.live_safe_keys() or "
        "strategy_profile.cycle_axis_dispatch_inverse() instead."
    )
