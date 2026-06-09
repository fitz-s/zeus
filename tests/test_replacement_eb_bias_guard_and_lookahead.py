# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07; last_reused=2026-06-07
# Purpose: RED-first relationship + property tests for the two structural fixes on the
#   replacement_0_1 EB bias-shift resolver:
#     ITEM 1 — structural over-correction GUARD (reliability-shrink x magnitude-cap +
#       stability gate) so an implausible/unstable bias (Tokyo -4.847C on small n) is
#       tempered toward 0 while a stable moderate bias (Wellington -2.07, Taipei -1.79)
#       is mostly retained. Property: guarded |shift| <= unguarded |shift| ALWAYS
#       (only-more-conservative). Structural: NO city can ever receive an over-correction.
#     ITEM 2 — self-gate LOOKAHEAD: the resolver serves a VERIFIED row only when its
#       training_cutoff (date) is STRICTLY BEFORE target_date; otherwise None (no
#       correction). The resolver does NOT rely on an external gate.
# Authority basis: SETTLEMENT-VALIDATED 2026-06-07 (real WU, walk-forward, skeptic-confirmed):
#   the served effective_bias_c is a RAW per-city mean applied at full magnitude
#   (scripts/write_promoted_edli_bias.py:73,95 effective_bias_c=eff=errs.mean(),
#   correction_strength=1.0) — NOT an EB posterior — so the guard MUST shrink+cap, not
#   merely gate on significance (Tokyo -4.847/SE=0.66 IS significant; the defect is
#   MAGNITUDE). model_bias_ens carries n_live + residual_sd_c (ens_bias_repo schema), the
#   inputs the guard reuses (ONE-BUILDER; same "untrustworthy bias must be tempered"
#   notion as bias_decay_kelly_haircut). docs/the_path/P2_BLEND.md §3,§4,§5.
"""RED-first tests: structural EB over-correction guard + resolver lookahead self-gate."""

from __future__ import annotations

import math
import sqlite3

import pytest

from src.calibration.ens_bias_repo import init_ens_bias_schema, write_bias_model
from src.calibration.replacement_eb_bias import (
    REPLACEMENT_EB_BIAS_FAMILY,
    bound_eb_bias_shift,
    resolve_replacement_eb_bias_shift_c,
)


# ===========================================================================
# ITEM 1 — STRUCTURAL OVER-CORRECTION GUARD (pure math; the bounded transform).
#   bound_eb_bias_shift(b, n, residual_sd) -> a tempered degC shift.
#   The served bias is a RAW per-city mean at full magnitude, so the guard's job is
#   to shrink the small-n inflation and CAP the implausible magnitude.
# ===========================================================================

# Settlement-validated rows (zeus-world.model_bias_ens, edli_per_city_v1, VERIFIED):
#   (name, effective_bias_c, n_live, residual_sd_c)
TOKYO_JJA6 = ("Tokyo JJA6", -4.847, 7, 1.757)       # implausible: must be capped + shrunk
TOKYO_MAM5 = ("Tokyo MAM5", -3.447, 14, 1.926)      # large: must be capped + shrunk
WELLINGTON_DJF6 = ("Wellington DJF6", -2.074, 7, 1.140)  # stable moderate: retain most
WELLINGTON_SON5 = ("Wellington SON5", -1.149, 14, 0.669)  # stable moderate: retain most
TAIPEI_MAM5 = ("Taipei MAM5", -1.803, 14, 1.331)    # stable moderate: retain most
TAIPEI_JJA6 = ("Taipei JJA6", -1.793, 7, 2.421)     # stable moderate: retain most
TOKYO_LOW6 = ("Tokyo low6", -0.304, 7, 0.568)       # small: near-passthrough (shrunk a bit)

ALL_CASES = [
    TOKYO_JJA6, TOKYO_MAM5, WELLINGTON_DJF6, WELLINGTON_SON5,
    TAIPEI_MAM5, TAIPEI_JJA6, TOKYO_LOW6,
]


def test_guard_caps_implausible_tokyo_bias_toward_zero() -> None:
    # Tokyo JJA6 = -4.847C on n=7 is an over-correction (settlement: 20.0C truth -> 26C miss).
    # The guard MUST temper it: strictly smaller magnitude AND under a hard absolute ceiling.
    _, b, n, sd = TOKYO_JJA6
    g = bound_eb_bias_shift(b, n, sd)
    assert abs(g) < abs(b), "guard must shrink the implausible Tokyo bias"
    assert abs(g) <= 2.5 + 1e-9, "guard must cap magnitude at the absolute ceiling (<=2.5C)"
    # The cap is the load-bearing component here: the raw shrink alone (b*n/(n+kappa)) would
    # still leave > 2.5C, so the magnitude cap must bind.
    assert g == pytest.approx(-2.5, abs=0.01), "Tokyo JJA6 must be pinned to the -2.5C ceiling"
    # Sign preserved (still a cold-warming correction, just bounded).
    assert math.copysign(1.0, g) == math.copysign(1.0, b)


def test_guard_retains_stable_moderate_biases() -> None:
    # Wellington/Taipei are genuinely cold-biased (settlement: miss->hit when corrected).
    # The guard must RETAIN a meaningful fraction of the correction (not zero them out).
    for name, b, n, sd in (WELLINGTON_DJF6, WELLINGTON_SON5, TAIPEI_MAM5, TAIPEI_JJA6):
        g = bound_eb_bias_shift(b, n, sd)
        # Below the absolute ceiling so the cap never binds for these moderate cities.
        assert abs(g) < 2.5, f"{name}: moderate bias must be below the cap"
        # Retains at least ~40% of the raw cold-correction magnitude (still useful).
        assert abs(g) >= 0.40 * abs(b), f"{name}: guard must retain most of a stable moderate bias"
        # Sign preserved.
        assert math.copysign(1.0, g) == math.copysign(1.0, b)


def test_guard_zeroes_statistically_indistinguishable_bias() -> None:
    # A bias smaller than its own standard error is noise — the guard zeroes it (stability gate).
    # SE = residual_sd/sqrt(n). b=-0.20 with sd=2.0,n=4 -> SE=1.0 -> |b| < z*SE -> 0.
    g = bound_eb_bias_shift(-0.20, 4, 2.0)
    assert g == 0.0, "a sub-SE bias is indistinguishable from 0 -> zeroed"


def test_guard_only_more_conservative_property_exhaustive() -> None:
    # CORE PROPERTY: the guarded shift is NEVER larger in magnitude than the raw shift, for
    # ANY (b, n, residual_sd). This is the structural guarantee that the guard can only make
    # the correction more conservative (smaller / abstaining), never larger — for present
    # rows AND any future row.
    import random

    rng = random.Random(20260607)
    for _ in range(20000):
        b = rng.uniform(-12.0, 12.0)
        n = rng.randint(1, 400)
        sd = rng.uniform(0.0, 6.0)
        g = bound_eb_bias_shift(b, n, sd)
        assert abs(g) <= abs(b) + 1e-9, f"guard widened the shift: b={b} n={n} sd={sd} -> g={g}"
        # Sign is never flipped (a guard that flips sign would trade the wrong way).
        if g != 0.0:
            assert math.copysign(1.0, g) == math.copysign(1.0, b), (
                f"guard flipped sign: b={b} n={n} sd={sd} -> g={g}"
            )


def test_guard_shrinks_more_with_smaller_n() -> None:
    # Reliability shrink: the SAME bias magnitude on fewer samples is tempered MORE.
    # (small-n inflation is exactly the Tokyo artifact). Use a magnitude below the cap so
    # the shrink term — not the cap — is what's being compared.
    b, sd = -2.0, 1.0
    g_small_n = bound_eb_bias_shift(b, 5, sd)
    g_large_n = bound_eb_bias_shift(b, 200, sd)
    assert abs(g_small_n) < abs(g_large_n), "smaller n must shrink the bias more (reliability)"
    assert abs(g_large_n) <= abs(b) + 1e-9


def test_guard_degenerate_inputs_fail_safe_to_zero_or_passthrough() -> None:
    # n<=0 or non-finite -> cannot establish reliability -> 0 (no correction; fail-safe).
    assert bound_eb_bias_shift(-3.0, 0, 1.0) == 0.0
    assert bound_eb_bias_shift(-3.0, -5, 1.0) == 0.0
    assert bound_eb_bias_shift(float("nan"), 10, 1.0) == 0.0
    assert bound_eb_bias_shift(-3.0, 10, float("nan")) == 0.0
    # residual_sd == 0 (degenerate exact evidence): stability gate cannot reject (SE=0);
    # the magnitude cap min(C_ABS, k_sd*0)=0 -> 0 (a zero-residual fit is untrustworthy scale).
    assert bound_eb_bias_shift(-3.0, 10, 0.0) == 0.0


# ===========================================================================
# ITEM 1 (integration) — the RESOLVER applies the guard when the flag is on, reusing the
#   row's own n_live + residual_sd_c. The capped value, not the raw -4.847, is served.
# ===========================================================================

def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    return conn


def _write_row(
    conn, *, city, season, month, metric, ldv, eff_c, n_live, residual_sd_c,
    weight_live=1.0, authority="VERIFIED", training_cutoff="2026-05-01",
):
    write_bias_model(
        conn,
        city=city, season=season, month=month, metric=metric,
        live_data_version=ldv, prior_data_version=None,
        posterior_bias_c=eff_c, posterior_sd_c=residual_sd_c,
        n_live=n_live, n_prior=0, weight_live=weight_live,
        estimator="a4_canonical_per_city_settled",
        error_model_family=REPLACEMENT_EB_BIAS_FAMILY,
        bias_c=eff_c, effective_bias_c=eff_c, residual_sd_c=residual_sd_c,
        total_residual_sd_c=residual_sd_c, authority=authority,
        gate_set_hash=None, coverage_months=str(month),
        training_cutoff=training_cutoff,
    )
    conn.commit()


def test_resolver_serves_guarded_not_raw_tokyo() -> None:
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-4.847, n_live=7, residual_sd_c=1.757)
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is not None
    # The resolver must apply the guard: the served shift is the bounded value, not -4.847.
    assert abs(shift) < 4.847
    assert shift == pytest.approx(bound_eb_bias_shift(-4.847, 7, 1.757))


def test_resolver_retains_guarded_wellington() -> None:
    conn = _world_conn()
    _write_row(conn, city="Wellington", season="DJF", month=6, metric="high",
               ldv="dv1", eff_c=-2.074, n_live=7, residual_sd_c=1.140)
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Wellington", season="DJF", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is not None
    assert shift == pytest.approx(bound_eb_bias_shift(-2.074, 7, 1.140))
    assert abs(shift) >= 0.40 * 2.074, "stable moderate bias is retained, not zeroed"


def test_resolver_guarded_is_only_more_conservative_than_raw() -> None:
    # Cross-module property: whatever the row says, the resolver never serves a LARGER
    # magnitude than the raw effective_bias_c.
    conn = _world_conn()
    for city, eff, n, sd in (
        ("A", -4.847, 7, 1.757), ("B", -2.074, 7, 1.14), ("C", -1.793, 7, 2.421),
        ("D", -0.304, 7, 0.568), ("E", 3.0, 5, 1.0),
    ):
        _write_row(conn, city=city, season="JJA", month=6, metric="high",
                   ldv="dv1", eff_c=eff, n_live=n, residual_sd_c=sd)
        shift = resolve_replacement_eb_bias_shift_c(
            conn, city=city, season="JJA", month=6, metric="high",
            live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
        )
        if shift is not None:
            assert abs(shift) <= abs(eff) + 1e-9, f"{city}: served shift larger than raw"


# ===========================================================================
# ITEM 2 — SELF-GATE LOOKAHEAD. The resolver must refuse a row whose training_cutoff is
#   NOT strictly before the target_date (no external gate dependency).
# ===========================================================================

def test_resolver_none_when_cutoff_equals_target() -> None:
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff="2026-06-07")
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is None, "cutoff == target is lookahead -> None"


def test_resolver_none_when_cutoff_after_target() -> None:
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff="2026-06-10")
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is None, "cutoff after target is lookahead -> None"


def test_resolver_serves_when_cutoff_strictly_before_target() -> None:
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff="2026-06-06")
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is not None, "cutoff strictly before target is causal -> served"
    assert shift == pytest.approx(bound_eb_bias_shift(-2.0, 20, 1.0))


def test_resolver_handles_iso_datetime_cutoff() -> None:
    # Real rows carry ISO datetime cutoffs (e.g. '2026-06-04T06:38:35+00:00'). The date
    # portion must be compared. A 2026-06-04 cutoff is causal for a 2026-06-07 target.
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff="2026-06-04T06:38:35.296908+00:00")
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is not None
    # And an ISO datetime cutoff on the SAME calendar day as target is lookahead -> None.
    conn2 = _world_conn()
    _write_row(conn2, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff="2026-06-07T06:38:35.296908+00:00")
    shift2 = resolve_replacement_eb_bias_shift_c(
        conn2, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift2 is None


def test_resolver_none_when_cutoff_null() -> None:
    # FAIL-CLOSED: a row with no training_cutoff cannot prove causality -> None.
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff=None)
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C", target_date="2026-06-07",
    )
    assert shift is None, "NULL cutoff cannot prove causality -> fail-closed None"


def test_resolver_no_target_date_skips_lookahead_filter() -> None:
    # Backward compat: when no target_date is supplied the lookahead filter is not applied
    # (the caller is responsible). This preserves the existing call signature behaviour.
    conn = _world_conn()
    _write_row(conn, city="Tokyo", season="JJA", month=6, metric="high",
               ldv="dv1", eff_c=-2.0, n_live=20, residual_sd_c=1.0,
               training_cutoff="2026-06-10")
    shift = resolve_replacement_eb_bias_shift_c(
        conn, city="Tokyo", season="JJA", month=6, metric="high",
        live_data_version="dv1", settlement_unit="C",
    )
    assert shift is not None, "no target_date -> lookahead gate is not applied (legacy)"
