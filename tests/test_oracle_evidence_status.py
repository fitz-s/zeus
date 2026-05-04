# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A3 (Findings A+B+C closure: missing != OK, posterior bounds, LOW unsupported) + §5 (Beta-binomial posterior derivation) + bug review §A/§B/§C.
"""Oracle evidence-grade regression antibodies (PLAN.md §A3 OK1-OK7 + §A7 I1).

These tests pin the contract that A3 ships:

- A missing oracle file does NOT silently degrade to OK for every city
  (Bug review Finding A: "missing != OK"). PR #40 left this rescue hole
  open by routing absent records to ``_DEFAULT_OK = OracleStatus.OK,
  mult=1.0``. The fix routes them to MISSING with mult=0.5 (= Beta(1,1)
  posterior_mean at N=0; PLAN.md §5 + D-2).

- Zero observed errors at small N is NOT OK. The Beta-binomial posterior
  upper-95 at N=12 m=0 is ~0.21 — wide. The right call is
  INSUFFICIENT_SAMPLE, not OK (Bug review Finding B).

- LOW track is METRIC_UNSUPPORTED until a LOW snapshot bridge ships
  (PLAN.md D-3 + Bug review Finding C). Bridge today only measures
  ``temperature_metric='high'``.

- Malformed JSON degrades the reader to MALFORMED with carry-over of
  the last good multiplier × 0.7 — never raises (PR #40 removed the
  evaluator-side fail-closed gate; oracle is a sizing modifier).

- Posterior bounds are MONOTONE in (m, n): adding errors widens the
  upper bound, adding clean samples narrows it. The classifier must
  not regress to a lower tier when m grows or n shrinks.

The tests use ``ZEUS_STORAGE_ROOT`` env override (PLAN.md §A2) to
redirect oracle paths into pytest's ``tmp_path``, so each test is
isolated and the production ``data/oracle_error_rates.json`` is never
read or written.
"""
from __future__ import annotations

import json

import pytest

from src.strategy import oracle_penalty
from src.strategy.oracle_estimator import (
    classify,
    posterior_mean,
    posterior_upper_95,
)
from src.strategy.oracle_status import OracleStatus


# ── shared fixture ─────────────────────────────────────────────────── #


@pytest.fixture(autouse=True)
def _reset_oracle_module_state(monkeypatch, tmp_path):
    """Each test runs against a fresh ZEUS_STORAGE_ROOT and a freshly
    reset oracle_penalty cache. Prevents cross-test bleed of the
    module-level ``_cache`` / ``_prev_multiplier_cache`` state.
    """
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    oracle_penalty._reset_for_test()
    yield
    oracle_penalty._reset_for_test()


def _write_oracle(tmp_path, payload: dict) -> None:
    """Helper: place a synthetic oracle JSON at the canonical
    ZEUS_STORAGE_ROOT / data / oracle_error_rates.json layout."""
    target = tmp_path / "data" / "oracle_error_rates.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload))


# ── PLAN.md §A3 OK1-OK7 ─────────────────────────────────────────────── #


def test_OK1_missing_file_yields_missing_status_for_every_city(tmp_path):
    """Bug review Finding A: missing oracle file must NOT silently
    return OK with mult=1.0 (PR #40 rescue hole). Closed by A3:
    missing → MISSING with mult=0.5 (Beta(1,1) prior posterior_mean).
    """
    # No file at all under tmp_path — fixture sets ZEUS_STORAGE_ROOT.
    for city in ("NYC", "Shenzhen", "Wellington", "AnyOtherCity"):
        info = oracle_penalty.get_oracle_info(city, "high")
        assert info.status == OracleStatus.MISSING, (
            f"{city}/high should be MISSING when oracle file is absent; "
            f"got {info.status.value}"
        )
        assert info.penalty_multiplier == 0.5, (
            f"{city}/high mult should be 0.5 (= prior posterior_mean); "
            f"got {info.penalty_multiplier}"
        )
        assert info.n == 0
        assert info.mismatches == 0


def test_OK2_unknown_city_in_existing_file_yields_missing(tmp_path):
    """A city absent from a present-but-partial oracle file is MISSING
    (not OK with default-to-zero-rate). The Bug review Finding A floor
    holds even when the file exists — it's per (city, metric).
    """
    _write_oracle(tmp_path, {"NYC": {"high": {"n": 100, "mismatches": 0}}})
    info = oracle_penalty.get_oracle_info("Unknown_City", "high")
    assert info.status == OracleStatus.MISSING
    assert info.penalty_multiplier == 0.5


def test_OK3_low_metric_is_metric_unsupported_with_zero_multiplier(tmp_path):
    """Bug review Finding C + PLAN.md D-3: LOW track has no oracle
    snapshot bridge today. Returns METRIC_UNSUPPORTED with mult=0
    (no live entries on LOW).
    """
    # Even with a fully populated oracle file for the city, LOW must
    # fail the metric gate.
    _write_oracle(
        tmp_path,
        {
            "NYC": {
                "high": {"n": 100, "mismatches": 0},
                "low": {"n": 100, "mismatches": 0},  # bridge would never write this today
            }
        },
    )
    info = oracle_penalty.get_oracle_info("NYC", "low")
    assert info.status == OracleStatus.METRIC_UNSUPPORTED
    assert info.penalty_multiplier == 0.0
    assert info.block_reason and "LOW oracle bridge" in info.block_reason


def test_OK4_malformed_json_degrades_to_malformed_with_carryover(tmp_path):
    """A concurrent-write / corrupted-file scenario must:
    1. Not crash reload (or any caller).
    2. Set the cache state to MALFORMED.
    3. Return records with mult = previous_good × 0.7 so live sizing
       degrades instead of zero-ing out (Bug review §5.2).
    """
    # Step 1: load a clean file so previous_good gets cached.
    _write_oracle(
        tmp_path,
        {"NYC": {"high": {"n": 100, "mismatches": 1}}},
    )
    oracle_penalty.reload()
    pre = oracle_penalty.get_oracle_info("NYC", "high")
    assert pre.status in (OracleStatus.OK, OracleStatus.INCIDENTAL, OracleStatus.CAUTION)
    assert pre.penalty_multiplier > 0.0

    # Step 2: clobber the file mid-write with garbage.
    target = tmp_path / "data" / "oracle_error_rates.json"
    target.write_text("{not valid json,,,")
    oracle_penalty.reload()

    post = oracle_penalty.get_oracle_info("NYC", "high")
    assert post.status == OracleStatus.MALFORMED
    # Carryover: mult = previous × 0.7. previous was pre.penalty_multiplier.
    assert post.penalty_multiplier == pytest.approx(pre.penalty_multiplier * 0.7)


def test_OK5_shenzhen_canonical_blacklist_regression_antibody(tmp_path):
    """The Bug review §5 canonical case: Shenzhen high with n=25 m=10
    must classify as BLACKLIST (posterior_upper_95 ≈ 0.564 > 0.10).
    Antibody: a regression that flips the BLACKLIST predicate to
    require empirical_rate (m/n = 0.40) instead of posterior_upper_95
    would still BLACKLIST this city, but OK6 would catch the symmetric
    bug where small-N is misjudged as OK.
    """
    _write_oracle(tmp_path, {"Shenzhen": {"high": {"n": 25, "mismatches": 10}}})
    info = oracle_penalty.get_oracle_info("Shenzhen", "high")
    assert info.status == OracleStatus.BLACKLIST
    assert info.penalty_multiplier == 0.0
    assert info.posterior_upper_95 > 0.10
    assert info.block_reason and "0.10" in info.block_reason


def test_OK6_zero_error_small_n_is_insufficient_sample_not_OK(tmp_path):
    """Bug review Finding B: 0 errors at N=12 must NOT classify as OK
    just because the empirical rate is 0/12 = 0%. The Beta-binomial
    upper-95 at (m=0, n=12) is ≈ 0.206 — wide enough that we cannot
    rule out a true 20% error rate. Right answer: INSUFFICIENT_SAMPLE
    (mult < 1.0), not OK (mult = 1.0).
    """
    _write_oracle(tmp_path, {"NewlyTrackedCity": {"high": {"n": 12, "mismatches": 0}}})
    info = oracle_penalty.get_oracle_info("NewlyTrackedCity", "high")
    assert info.status == OracleStatus.INSUFFICIENT_SAMPLE
    assert info.penalty_multiplier < 1.0, (
        "INSUFFICIENT_SAMPLE must carry a Kelly haircut; "
        "the rescue PR #40 silent-OK behavior is what A3 closes"
    )
    assert info.penalty_multiplier >= 0.5, (
        "INSUFFICIENT_SAMPLE mult floor is 0.5 (never stingier than MISSING)"
    )


def test_OK7_posterior_upper_95_is_monotone_in_m_and_n(tmp_path):
    """Math sanity: at fixed n, p95 is non-decreasing in m. At fixed m,
    p95 is non-increasing in n. A regression that flipped either
    direction would mis-classify large samples and corrupt the BLACKLIST
    threshold semantics.
    """
    # Fixed n=100. m grows.
    p95_at_n100 = [posterior_upper_95(m, 100) for m in (0, 1, 5, 10, 50, 100)]
    assert p95_at_n100 == sorted(p95_at_n100), (
        f"p95 must be monotone non-decreasing in m at fixed n; got {p95_at_n100}"
    )

    # Fixed m=5. n grows.
    p95_at_m5 = [posterior_upper_95(5, n) for n in (10, 25, 50, 100, 500)]
    assert p95_at_m5 == sorted(p95_at_m5, reverse=True), (
        f"p95 must be monotone non-increasing in n at fixed m; got {p95_at_m5}"
    )


# ── posterior math sanity (exposes raw helpers) ─────────────────────── #


def test_posterior_mean_at_zero_n_is_one_half():
    """The PLAN.md D-2 anchor: Beta(1,1) prior → posterior_mean(0,0) = 0.5.
    This is the math justification for the MISSING multiplier; if this
    drifts, MISSING semantics drift with it.
    """
    assert posterior_mean(0, 0) == pytest.approx(0.5)


def test_posterior_upper_95_at_zero_n_is_zero_point_95():
    """At N=0 with Beta(1,1) prior, the 95% upper credible bound is
    exactly the 95th percentile of the uniform distribution = 0.95.
    """
    assert posterior_upper_95(0, 0) == pytest.approx(0.95, abs=1e-6)


def test_classify_stale_overrides_clean_counts(tmp_path):
    """An artifact older than 7 days is STALE regardless of how clean
    the counts look. PLAN.md §5 ordering — STALE check fires before
    the m=0 vs m≥1 split.
    """
    # Direct call to classify, since artifact_age_hours is a parameter.
    status = classify(0, 100, artifact_age_hours=8 * 24.0)  # 8 days old
    assert status == OracleStatus.STALE


# ── policy-table multiplier values ─────────────────────────────────── #


def test_multiplier_table_constants(tmp_path):
    """Pin the live Kelly-policy multipliers to PLAN.md §A3's spec
    (operator-readable). A regression that changes any of these
    values is changing live trading behavior — the test forces the
    drift to surface in code review.
    """
    # OK case (m=0, n=60 sufficient sample, p95 < 0.05).
    _write_oracle(tmp_path, {"OK_City": {"high": {"n": 60, "mismatches": 0}}})
    info = oracle_penalty.get_oracle_info("OK_City", "high")
    assert info.status == OracleStatus.OK
    assert info.penalty_multiplier == 1.0

    # INCIDENTAL case (m=1, n=200, p95 ≈ 0.024 < 0.05).
    _write_oracle(tmp_path, {"Incidental_City": {"high": {"n": 200, "mismatches": 1}}})
    oracle_penalty._reset_for_test()
    info = oracle_penalty.get_oracle_info("Incidental_City", "high")
    assert info.status == OracleStatus.INCIDENTAL
    assert info.penalty_multiplier == 1.0

    # BLACKLIST case (m=10, n=25, p95 > 0.10).
    _write_oracle(tmp_path, {"Bad_City": {"high": {"n": 25, "mismatches": 10}}})
    oracle_penalty._reset_for_test()
    info = oracle_penalty.get_oracle_info("Bad_City", "high")
    assert info.status == OracleStatus.BLACKLIST
    assert info.penalty_multiplier == 0.0


def test_caution_multiplier_is_linearly_capped_at_0_97(tmp_path):
    """CAUTION: mult = min(0.97, 1 - p95). The 0.97 cap exists so the
    smallest CAUTION still shows a visible Kelly haircut in logs (the
    0.999 case wouldn't be distinguishable from OK otherwise).
    """
    # Construct counts that yield p95 just barely above 0.05.
    # We need m≥1 and 0.05 < p95 ≤ 0.10. Try n=200 m=4 (p95 ≈ 0.046)
    # then n=120 m=3 (p95 ≈ 0.063) — adjust until in range.
    # n=100 m=3: p95 = ?
    n, m = 100, 3
    p95 = posterior_upper_95(m, n)
    assert 0.05 < p95 <= 0.10, f"need a CAUTION case; got p95={p95}"
    _write_oracle(tmp_path, {"Caution_City": {"high": {"n": n, "mismatches": m}}})
    info = oracle_penalty.get_oracle_info("Caution_City", "high")
    assert info.status == OracleStatus.CAUTION
    expected = min(0.97, 1.0 - p95)
    assert info.penalty_multiplier == pytest.approx(expected, abs=1e-6)
    assert info.penalty_multiplier <= 0.97


# ── reload behavior with the new schema ─────────────────────────────── #


def test_legacy_flat_file_loads_without_n_yields_missing(tmp_path):
    """Files written by the pre-A3 bridge carry only ``oracle_error_rate``
    at the top level (no n / mismatches). Reading those records gives
    MISSING (mult 0.5) until the next bridge run writes the new schema.
    This is intentional — the empirical rate alone can't bound the
    posterior, so we treat the legacy-shape record as no-evidence.
    """
    # Legacy flat shape (pre-PR53 oracle_error_rates.json layout).
    _write_oracle(tmp_path, {"LegacyCity": {"oracle_error_rate": 0.04}})
    info = oracle_penalty.get_oracle_info("LegacyCity", "high")
    assert info.status == OracleStatus.MISSING, (
        "Legacy flat record without n/mismatches → MISSING; the empirical "
        "rate alone is not enough to commit to a tier"
    )


def test_reload_does_not_raise_on_missing_file(tmp_path):
    """PR #40 floor: oracle_penalty.reload() is called every cycle by
    the evaluator. A missing-file raise here would resurrect the exact
    halt-live-trading regression PR #40 closed.
    """
    # No file under tmp_path. reload must succeed silently.
    oracle_penalty.reload()
    info = oracle_penalty.get_oracle_info("Anywhere", "high")
    assert info.status == OracleStatus.MISSING
