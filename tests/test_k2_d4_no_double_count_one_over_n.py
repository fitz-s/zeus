# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K2 D4 fix (adversarial-verify finding #2). The D4
#   representativeness-σ fold (event_reactor_adapter._edli_representativeness_sigma_native)
#   added shift_se² = residual_var/n IN QUADRATURE on top of the base σ =
#   total_residual_sd_c. But total_residual_sd_c = sqrt(residual_var + residual_var/n) =
#   σ_resid·sqrt(1 + 1/n) ALREADY contains that 1/n mean-estimation-drift term, so the
#   composed σ became σ_resid²·(1 + 2/n) — the 1/n is double-counted, over-widening
#   q_lcb ~6% at n=7. FIX: base the D4 fold on the IN-SAMPLE residual_sd_c (no 1/n
#   term), not total_residual_sd_c, so the composed σ reconstructs exactly the intended
#   predictive σ = total_residual_sd_c (= sqrt(residual_sd_c² + shift_se²)).
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-05; last_reused=2026-06-05
# Purpose: Relationship antibody — the D4 representativeness-sigma fold must NOT double-count the 1/n mean-drift term already in total_residual_sd_c (Phase-2 K2 D4).
# Reuse: Re-run when _edli_representativeness_sigma_native or ens_error_model residual-sd composition changes.
"""K2 D4 double-count relationship test.

RELATIONSHIP under test: the bias-fit PRODUCER stamps two σ's on model_bias_ens —
the in-sample residual_sd_c (= σ_resid) and the full predictive total_residual_sd_c
(= σ_resid·sqrt(1 + 1/n)). The CONSUMER (the D4 fold) must compose the
representativeness σ so the 1/n mean-location-uncertainty term is counted EXACTLY
ONCE. The invariant across the producer→consumer boundary:

    composed_σ == total_residual_sd_c · scale          (no double-count)

NOT  composed_σ == sqrt(total_residual_sd_c² + shift_se²) · scale  (the 6% bug).

This pins the math at the construction site (flag ON, low-n), independent of the MC.
"""
from __future__ import annotations

import math

import pytest

import src.engine.event_reactor_adapter as adapter


class _City:
    name = "Warsaw"
    lat = 52.0
    settlement_unit = "C"


def _stub_row(*, residual_sd_c: float, n_live: int):
    """A model_bias_ens row with the producer's total = σ_resid·sqrt(1 + 1/n)."""
    total = residual_sd_c * math.sqrt(1.0 + 1.0 / n_live)
    return {
        "residual_sd_c": residual_sd_c,
        "total_residual_sd_c": total,
        "n_live": n_live,
    }


def _sigma_with_flag(monkeypatch, *, flag_on: bool, residual_sd_c: float, n_live: int) -> float:
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["bias_treatment_v2_enabled"] = flag_on
    monkeypatch.setitem(settings._data, "edli", edli)

    row = _stub_row(residual_sd_c=residual_sd_c, n_live=n_live)

    class _FakeConn:
        row_factory = None

        def close(self):
            pass

    # The target fn does LOCAL imports (`from src.X import Y`), so patch the SOURCE
    # module attributes — patching the adapter module would not intercept them.
    import src.calibration.ens_bias_repo as ens_bias_repo
    import src.calibration.manager as cal_manager
    import src.state.db as state_db

    monkeypatch.setattr(ens_bias_repo, "read_bias_model", lambda *a, **k: row)
    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(cal_manager, "season_from_date", lambda *a, **k: "DJF")

    snapshot = {"dataset_id": "edli_per_city_v1"}
    family = type("F", (), {"target_date": "2026-01-15", "metric": "high"})()
    return adapter._edli_representativeness_sigma_native(
        snapshot=snapshot, family=family, city=_City()
    )


def test_d4_fold_does_not_double_count_one_over_n(monkeypatch):
    """Flag ON, low-n (n=7): the composed representativeness σ must equal the
    producer's total_residual_sd_c (the intended predictive σ) — NOT
    sqrt(total² + shift_se²), which double-counts the 1/n term ~6% at n=7."""
    residual_sd_c = 1.0
    n_live = 7

    composed = _sigma_with_flag(
        monkeypatch, flag_on=True, residual_sd_c=residual_sd_c, n_live=n_live
    )

    total = residual_sd_c * math.sqrt(1.0 + 1.0 / n_live)  # the intended predictive σ
    # The fix bases the fold on residual_sd_c (no 1/n), so composed reconstructs total:
    #   sqrt(residual_sd_c² + (residual_sd_c/sqrt(n))²) = sqrt(σ²(1 + 1/n)) = total.
    assert composed == pytest.approx(total, rel=1e-9)

    # And it must NOT equal the double-counted value (the bug).
    double_counted = math.sqrt(total ** 2 + (residual_sd_c / math.sqrt(n_live)) ** 2)
    assert composed != pytest.approx(double_counted, rel=1e-9)
    # The bug over-widens by ~6% at n=7; pin the gap so a regression is visible.
    assert double_counted / total == pytest.approx(math.sqrt(1.125), rel=1e-3)


def test_d4_fold_flag_off_is_byte_identical_to_total(monkeypatch):
    """Flag OFF: the D4 fold never fires; σ is exactly total_residual_sd_c · scale
    (legacy #89 behaviour, byte-identical). The fix changes ONLY the flag-ON math."""
    residual_sd_c = 1.0
    n_live = 7

    composed = _sigma_with_flag(
        monkeypatch, flag_on=False, residual_sd_c=residual_sd_c, n_live=n_live
    )
    total = residual_sd_c * math.sqrt(1.0 + 1.0 / n_live)
    assert composed == pytest.approx(total, rel=1e-12)
