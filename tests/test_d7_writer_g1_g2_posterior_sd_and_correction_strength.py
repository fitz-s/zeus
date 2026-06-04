# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K2 writer fixes G1/G2 (task #167). The D-7 bias writer stamped
#   two columns wrong: bias_sd_c = residual_sd_c (the per-day scatter), and
#   correction_strength = 1.0 (hardcoded). G1: bias_sd_c must be the POSTERIOR SD of the
#   bias-MEAN estimate (= residual_sd / sqrt(n)), the uncertainty in WHERE the shift sits.
#   G2: correction_strength must be the shrinkage lambda actually applied (= effective/raw),
#   so a heavily-shrunk noisy city records strength<1, not a false 1.0.
"""Writer G1/G2 relationship tests for scripts/write_d7_rolling_edli_bias.py.

These pin the stored provenance fields against their definitions. They are RED before the
writer fix (bias_sd_c == residual_sd_c, correction_strength == 1.0) and GREEN after.
"""
from __future__ import annotations

import math

import pytest

import scripts.write_d7_rolling_edli_bias as d7
from src.calibration.ens_bias_repo import read_bias_model
from src.calibration.manager import season_from_date

# Reuse the module-level test helpers' DB fixtures by importing them.
from tests.test_d7_rolling_edli_bias import (  # noqa: E402
    OPD,
    _snap,
    _settle,
    fc_conn,
    world_conn,
)


def _seed_and_write(fc_conn, world_conn, city, residuals, *, settle=20.0):
    """Seed `len(residuals)` settled days (forecast mean = settle+resid), compute, write."""
    now = "2026-06-10T00:00:00+00:00"
    days = [
        ("2026-06-02", "2026-06-03T18:00:00+00:00"),
        ("2026-06-01", "2026-06-02T18:00:00+00:00"),
        ("2026-05-31", "2026-06-01T18:00:00+00:00"),
        ("2026-05-29", "2026-05-30T18:00:00+00:00"),
        ("2026-05-27", "2026-05-28T18:00:00+00:00"),
        ("2026-05-25", "2026-05-26T18:00:00+00:00"),
        ("2026-05-24", "2026-05-25T18:00:00+00:00"),
    ]
    for (d, sat), resid in zip(days, residuals):
        mean = settle + resid
        _snap(fc_conn, city, d, [mean - 1.0, mean, mean + 1.0], unit="C",
              lead=96.0, avail=f"{d}T00:00:00Z")
        _settle(fc_conn, city, d, settle, unit="C", settled_at=sat)
    out = d7.compute_city_bias(fc_conn, city=city, metric="high",
                               data_version=OPD, now_iso=now, window_days=10, min_n=3)
    assert out is not None
    d7.write_city_bias(world_conn, city=city, metric="high", data_version=OPD,
                       bias=out, now_iso=now)
    world_conn.commit()
    row = read_bias_model(
        world_conn, city=city, season=season_from_date("2026-06-02"), metric="high",
        live_data_version=OPD, month=6, target_month=6,
        authority="VERIFIED", error_model_family="edli_per_city_v1",
    )
    assert row is not None
    return out, row


# ---------------------------------------------------------------------------
# G1 — bias_sd_c is the POSTERIOR SD of the mean (residual_sd / sqrt(n)), NOT residual_sd_c
# ---------------------------------------------------------------------------
def test_bias_sd_c_is_posterior_not_residual(fc_conn, world_conn):
    # A real, consistent bias with spread: residuals around +3 with scatter.
    residuals = [4.0, 2.0, 3.5, 2.5, 3.0, 3.2, 2.8]
    out, row = _seed_and_write(fc_conn, world_conn, "Tokyo", residuals)
    resid_sd = float(row["residual_sd_c"])
    bias_sd = float(row["bias_sd_c"])
    n = int(row["n_live"])
    assert n >= 3
    # The defect: bias_sd_c == residual_sd_c. The fix: bias_sd_c == residual_sd / sqrt(n).
    expected_post_sd = resid_sd / math.sqrt(n)
    assert bias_sd == pytest.approx(expected_post_sd, rel=1e-6), (
        f"bias_sd_c={bias_sd} should be the POSTERIOR mean-SD {expected_post_sd} "
        f"(residual_sd {resid_sd} / sqrt(n={n})), not the raw residual scatter"
    )
    # And it must be STRICTLY narrower than the per-day scatter (a mean is better-determined
    # than a single day) whenever n>1.
    if n > 1 and resid_sd > 0:
        assert bias_sd < resid_sd


# ---------------------------------------------------------------------------
# G2 — correction_strength is the shrinkage lambda (effective/raw), NOT hardcoded 1.0
# ---------------------------------------------------------------------------
def test_correction_strength_is_effective_over_raw(fc_conn, world_conn):
    # A NOISY city: residuals straddle zero so the significance-shrink pulls effective well
    # below raw -> correction_strength = effective/raw < 1.
    residuals = [3.0, -2.0, 2.5, -1.5, 1.0, -0.5, 0.5]
    out, row = _seed_and_write(fc_conn, world_conn, "Seoul", residuals)
    raw = float(out["raw_bias_c"])
    eff = float(out["effective_bias_c"])
    cs = float(row["correction_strength"])
    assert abs(raw) > 1e-9, "test setup: raw bias must be non-zero"
    expected_cs = eff / raw
    assert cs == pytest.approx(expected_cs, rel=1e-6), (
        f"correction_strength={cs} should equal effective/raw={expected_cs} "
        f"(eff={eff}, raw={raw}), not a hardcoded 1.0"
    )
    # The shrink MUST have done something on a noisy city.
    assert cs < 1.0


def test_correction_strength_unity_when_no_shrink(fc_conn, world_conn):
    # A clean, strongly-significant city: shrink barely moves eff -> strength ~ 1.0.
    residuals = [3.0, 3.1, 2.9, 3.05, 2.95, 3.0, 3.02]
    out, row = _seed_and_write(fc_conn, world_conn, "Taipei", residuals)
    raw = float(out["raw_bias_c"])
    eff = float(out["effective_bias_c"])
    cs = float(row["correction_strength"])
    assert cs == pytest.approx(eff / raw, rel=1e-6)
    assert cs > 0.9  # near-unity on a clean strong signal
