# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: U0R_BAYES_SPEC.md §4 (T3 equal-weight fallback) + u0r_bayes.equal_weight
#   docstring ("If a prior is supplied, blend it as one more equal member"). Fix of the
#   2026-06-09 wiring-audit MED defect: capture passed anchor_z with anchor_tau0=None for a
#   thin (<MIN_TRAIN) anchor history and its comment claimed "the fusion floors tau0", but
#   fuse_u0r_posterior required BOTH non-None -> the ECMWF 0.1 anchor CENTER (the strongest
#   single model, Exp O) was silently DROPPED from every EQUAL_WEIGHT cell.
"""RELATIONSHIP tests: capture -> fusion thin-anchor retention.

Cross-module invariant (capture_u0r_instruments -> fuse_u0r_posterior boundary):
  ANY cell whose capture carries a finite anchor center MUST produce a fused posterior whose
  used_models includes the anchor — the anchor value participates regardless of history depth.
  Thin history (< MIN_TRAIN) demotes the anchor from T2 prior to ONE equal member with
  conservative (TAU0_FLOOR * LOWN_INFLATE)^2 variance; it NEVER silently removes it.

Consistency law being enforced: a zero-history GLOBAL participates LOWN-inflated in
equal-weight; the zero-history ANCHOR must get the same treatment, not deletion.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pytest

from src.data.u0r_multimodel_capture import (
    ModelHistory,
    capture_u0r_instruments,
)
from src.forecast.u0r_bayes import (
    ANCHOR_MODEL,
    LOWN_INFLATE,
    MIN_TRAIN,
    SIGMA_FLOOR,
    TAU0_FLOOR,
    ModelInstrument,
    fuse_u0r_posterior,
)

# Tokyo: outside every regional polygon -> likelihood = the 4 decorrelated globals with
# icon_global as the single DWD-ICON rep (no regional/EU complications in the math).
TOKYO = (35.68, 139.69)
RUN = datetime(2026, 6, 8, 0, 0, tzinfo=timezone.utc)
TARGET = date(2026, 6, 9)

GLOBAL_VALUE_C = 10.0
ANCHOR_CENTER_C = 0.0   # far from the globals so anchor-retention moves the mean visibly.


def _zero_residual_history(model: str, n: int) -> ModelHistory:
    """n walk-forward rows with residual exactly 0 -> EB bias 0 -> z == raw value."""
    dates = tuple(f"2026-05-{d:02d}" for d in range(1, n + 1))
    return ModelHistory(
        model=model,
        forecast_values=(GLOBAL_VALUE_C,) * n,
        settlement_values=(GLOBAL_VALUE_C,) * n,
        target_dates=dates,
    )


def _live_fetch_all_globals(*, model: str, **_kw) -> float | None:
    return GLOBAL_VALUE_C


def _capture(history_models: list[str], n: int):
    provider = lambda **_kw: {m: _zero_residual_history(m, n) for m in history_models}  # noqa: E731
    return capture_u0r_instruments(
        city="Tokyo", metric="high", latitude=TOKYO[0], longitude=TOKYO[1],
        timezone_name="Asia/Tokyo", run=RUN, target_local_date=TARGET, lead_days=1,
        anchor_z_corrected=ANCHOR_CENTER_C,
        history_provider=provider, live_fetch=_live_fetch_all_globals,
    )


# ---- RELATIONSHIP: thin anchor history (0 < n_train < MIN_TRAIN) ----------------------
def test_thin_anchor_history_center_retained_through_capture_to_fusion() -> None:
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless"]
    cap = _capture(models, n=5)  # 5 < MIN_TRAIN
    # Capture contract preconditions: thin anchor -> center kept, NO trusted tau0.
    assert cap.anchor_z == pytest.approx(ANCHOR_CENTER_C)
    assert cap.anchor_tau0 is None
    assert cap.has_extras

    fused = fuse_u0r_posterior(
        anchor_z=cap.anchor_z, anchor_tau0=cap.anchor_tau0,
        likelihood=cap.likelihood, disagree_var=cap.disagree_var,
    )
    assert fused.method == "EQUAL_WEIGHT"  # thin anchor is NOT promoted to the T2 prior
    assert ANCHOR_MODEL in fused.used_models, (
        "the anchor CENTER must participate in the equal-weight blend; silently dropping the "
        "strongest model because its history is thin is the 2026-06-09 anchor-drop defect"
    )
    k = len(cap.likelihood) + 1
    expected_mu = (sum(ins.z for ins in cap.likelihood) + ANCHOR_CENTER_C) / k
    assert fused.mu == pytest.approx(expected_mu, abs=1e-9)
    # Discriminating: WITHOUT the anchor member the mean would be exactly GLOBAL_VALUE_C.
    assert fused.mu < GLOBAL_VALUE_C - 1.0


# ---- RELATIONSHIP: ZERO anchor history (provider has no anchor key at all) -------------
def test_zero_anchor_history_center_still_retained() -> None:
    models = ["gfs_global", "icon_global", "gem_global", "jma_seamless"]  # no ecmwf_ifs
    cap = _capture(models, n=30)
    # Same-rule consistency: a zero-history global participates (LOWN-inflated); the
    # zero-history anchor center must too — capture must NOT null it out.
    assert cap.anchor_z == pytest.approx(ANCHOR_CENTER_C)
    assert cap.anchor_tau0 is None

    fused = fuse_u0r_posterior(
        anchor_z=cap.anchor_z, anchor_tau0=cap.anchor_tau0,
        likelihood=cap.likelihood, disagree_var=cap.disagree_var,
    )
    assert fused.method == "EQUAL_WEIGHT"
    assert ANCHOR_MODEL in fused.used_models
    k = len(cap.likelihood) + 1
    expected_mu = (sum(ins.z for ins in cap.likelihood) + ANCHOR_CENTER_C) / k
    assert fused.mu == pytest.approx(expected_mu, abs=1e-9)


# ---- RELATIONSHIP: trusted anchor (>= MIN_TRAIN) keeps the T2 path (regression) --------
def test_trusted_anchor_history_still_reaches_t2_bayes() -> None:
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless"]
    cap = _capture(models, n=MIN_TRAIN + 5)
    assert cap.anchor_z is not None and cap.anchor_tau0 is not None
    fused = fuse_u0r_posterior(
        anchor_z=cap.anchor_z, anchor_tau0=cap.anchor_tau0,
        likelihood=cap.likelihood, disagree_var=cap.disagree_var,
    )
    assert fused.method == "T2_BAYES"
    assert fused.used_models[0] == ANCHOR_MODEL


# ---- FUNCTION: fuse-level math of the thin-anchor equal member --------------------------
def _thin_global(model: str, z: float) -> ModelInstrument:
    return ModelInstrument(
        model=model, z=z, train_residuals=(0.0,) * 5, n_train=5,
        residuals_by_date={f"2026-05-{d:02d}": 0.0 for d in range(1, 6)},
    )


def test_fuse_thin_anchor_is_one_equal_member_with_conservative_variance() -> None:
    instruments = [_thin_global(m, GLOBAL_VALUE_C) for m in ("gfs_global", "icon_global", "jma_seamless")]
    fused = fuse_u0r_posterior(
        anchor_z=ANCHOR_CENTER_C, anchor_tau0=None, likelihood=instruments, disagree_var=0.0,
    )
    assert fused.method == "EQUAL_WEIGHT"
    assert fused.anchor_model == ANCHOR_MODEL
    assert fused.used_models == (ANCHOR_MODEL, "gfs_global", "icon_global", "jma_seamless")
    assert fused.mu == pytest.approx((3 * GLOBAL_VALUE_C + ANCHOR_CENTER_C) / 4, abs=1e-9)  # 7.5
    # sd: 3 globals at SIGMA_FLOOR^2 (zero residual std -> floor) + anchor at the conservative
    # thin variance (TAU0_FLOOR * LOWN_INFLATE)^2, equal-weight: sum(var)/k^2.
    expected_var = (3 * SIGMA_FLOOR ** 2 + (TAU0_FLOOR * LOWN_INFLATE) ** 2) / 16
    assert fused.sd == pytest.approx(math.sqrt(expected_var), abs=1e-9)


def test_fuse_anchor_none_unchanged_plain_mean() -> None:
    instruments = [_thin_global(m, GLOBAL_VALUE_C) for m in ("gfs_global", "icon_global", "jma_seamless")]
    fused = fuse_u0r_posterior(
        anchor_z=None, anchor_tau0=None, likelihood=instruments, disagree_var=0.0,
    )
    assert fused.method == "EQUAL_WEIGHT"
    assert fused.anchor_model is None
    assert ANCHOR_MODEL not in fused.used_models
    assert fused.mu == pytest.approx(GLOBAL_VALUE_C, abs=1e-9)


def test_fuse_nonfinite_anchor_treated_as_absent() -> None:
    instruments = [_thin_global(m, GLOBAL_VALUE_C) for m in ("gfs_global", "icon_global", "jma_seamless")]
    fused = fuse_u0r_posterior(
        anchor_z=float("nan"), anchor_tau0=None, likelihood=instruments, disagree_var=0.0,
    )
    assert fused.method == "EQUAL_WEIGHT"
    assert ANCHOR_MODEL not in fused.used_models
    assert fused.mu == pytest.approx(GLOBAL_VALUE_C, abs=1e-9)
    assert np.isfinite(fused.mu)


def test_fuse_thin_anchor_alone_no_instruments_returns_single_member() -> None:
    # Previously raised ValueError (have_anchor required tau0). A finite thin-anchor center
    # with zero surviving instruments is a valid degenerate equal-weight of ONE member.
    fused = fuse_u0r_posterior(
        anchor_z=ANCHOR_CENTER_C, anchor_tau0=None, likelihood=[], disagree_var=0.0,
    )
    assert fused.method == "EQUAL_WEIGHT"
    assert fused.used_models == (ANCHOR_MODEL,)
    assert fused.mu == pytest.approx(ANCHOR_CENTER_C)
    assert fused.sd == pytest.approx(TAU0_FLOOR * LOWN_INFLATE, abs=1e-9)
