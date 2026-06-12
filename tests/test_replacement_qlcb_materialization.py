# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §1d-§1e (fused-N-direct q,
#   σ_pred = sqrt(fused.sd² + σ_resid²)); root-cause /tmp/candidate_missing_rootcause.md (NULL
#   q_lcb_json → live LCB authority falls back to Wilson-over-AIFS-votes → under-certifies below ask
#   → every proof killed → EVENT_BOUND_SELECTED_CANDIDATE_MISSING). ONE-builder: bin integration is
#   the SAME preimage math as src/calibration/emos.bin_probability_settlement.
"""Q_LCB / Q_UCB MATERIALIZATION antibodies (fused-center parameter-uncertainty bootstrap).

Category being killed: a fused replacement posterior that ships with q_lcb_json/q_ucb_json NULL,
which forces the live LCB authority onto the Wilson-over-AIFS-votes fallback (the machinery of the
REPLACED chain) — under-certifying below ask for nearly all bins and discarding every candidate.

The bootstrap draws μ_i ~ N(μ*, fused.sd) (CENTER uncertainty only — σ_resid already lives inside
σ_pred, so no double-count), integrates the settlement bins via the ONE integrator, and takes the
per-bin 5th/95th percentile as q_lcb/q_ucb. Pinned here:
  - per-bin q_lcb ≤ q_point ≤ q_ucb (the ProbabilityUncertainty invariant on the BOUND)
  - basis recorded as fused_center_bootstrap_p05
  - deterministic with the seeded rng (stable provenance bounds)
  - a Milan-incident-shaped posterior exposes far-tail fragility (q_lcb << q for an off-mode bin)
  - construction failure → NULL + warning (fail-soft; never WORSE than the Wilson status quo)
  - integration: the fused-q path populates q_lcb_json/q_ucb_json columns + provenance role/basis.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import pytest

import src.data.replacement_forecast_materializer as mod
from src.data.replacement_forecast_materializer import _build_fused_q_bounds
from src.calibration.emos import bin_probability_settlement
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (  # reuse the proven harness
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _live_values,
    _request,
    _seed_current_single_runs,
    _seed_history,
)

_MODELS = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]


def _enable_fused_shape(monkeypatch) -> None:
    from src.config import settings

    monkeypatch.setitem(settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


def _materialize(conn):
    return mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)


def _bound_row(conn, posterior_id: int):
    # The shared _row helper does not SELECT the bound columns; query them explicitly here.
    return conn.execute(
        "SELECT q_json, q_lcb_json, q_ucb_json, provenance_json "
        "FROM forecast_posteriors WHERE posterior_id = ?",
        (posterior_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Unit: _build_fused_q_bounds — the bootstrap math.
# ---------------------------------------------------------------------------

def _milan_bins() -> list[AifsTemperatureBin]:
    # Settlement bin ladder around the Milan-incident high. Interior bins are point-labeled
    # (lower_c == upper_c) + open shoulders, matching real market topology.
    interior = [AifsTemperatureBin(bin_id=str(t), lower_c=float(t), upper_c=float(t)) for t in range(22, 31)]
    return (
        [AifsTemperatureBin(bin_id="le21", lower_c=None, upper_c=21.0)]
        + interior
        + [AifsTemperatureBin(bin_id="ge31", lower_c=31.0, upper_c=None)]
    )


def _point_q(bins, *, mu: float, sigma: float, half_step: float) -> dict[str, float]:
    raw = {
        b.bin_id: bin_probability_settlement(
            mu=mu, sigma=sigma, bin_low=b.lower_c, bin_high=b.upper_c, half_step=half_step
        )
        for b in bins
    }
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def test_bounds_ordering_qlcb_le_qpoint_le_qucb_per_bin() -> None:
    bins = _milan_bins()
    mu, sig_pred, sig_center, hs = 26.4, 2.0, 0.7, 0.5
    qpt = _point_q(bins, mu=mu, sigma=sig_pred, half_step=hs)
    lcb, ucb = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=sig_center, predictive_sigma_c=sig_pred,
        bins=bins, half_step=hs, q_point=qpt,
    )
    assert set(lcb) == set(qpt) == set(ucb)
    for b in bins:
        k = b.bin_id
        assert 0.0 <= lcb[k] <= qpt[k] + 1e-9, f"q_lcb must be in [0, q_point] for {k}"
        assert qpt[k] - 1e-9 <= ucb[k], f"q_ucb must be >= q_point for {k}"


def test_bounds_basis_and_provenance_constant() -> None:
    # The basis label is the operator-agreed provenance marker; pin it so a rename is loud.
    assert mod._QLCB_BASIS == "fused_center_bootstrap_p05"
    assert mod._QLCB_BOOTSTRAP_DRAWS >= 100  # enough draws for a stable 5th/95th percentile


def test_bounds_deterministic_under_seeded_rng() -> None:
    bins = _milan_bins()
    mu, sig_pred, sig_center, hs = 26.4, 2.0, 0.7, 0.5
    qpt = _point_q(bins, mu=mu, sigma=sig_pred, half_step=hs)
    lcb1, ucb1 = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=sig_center, predictive_sigma_c=sig_pred,
        bins=bins, half_step=hs, q_point=qpt,
    )
    lcb2, ucb2 = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=sig_center, predictive_sigma_c=sig_pred,
        bins=bins, half_step=hs, q_point=qpt,
    )
    assert lcb1 == lcb2 and ucb1 == ucb2, "seeded rng must produce byte-stable bounds"


def test_milan_incident_far_tail_bound_exposes_fragility() -> None:
    # Milan-incident shape: μ*=26.4, σ_pred≈2. The 24°C bin is 2.4°C off the mode — a far tail.
    # The bound MUST collapse well below the point mass there: center wander (fused.sd) drags the
    # tail probability around far more than the well-determined center bins. Quantify: q_lcb < q/2.
    bins = _milan_bins()
    mu, sig_pred, sig_center, hs = 26.4, 2.0, 0.7, 0.5
    qpt = _point_q(bins, mu=mu, sigma=sig_pred, half_step=hs)
    lcb, _ = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=sig_center, predictive_sigma_c=sig_pred,
        bins=bins, half_step=hs, q_point=qpt,
    )
    assert lcb["24"] < qpt["24"] / 2.0, (
        f"far-tail bin q_lcb={lcb['24']:.5f} must be << q_point={qpt['24']:.5f} (the bound must "
        "expose tail fragility — this is what the Wilson fallback could not certify)"
    )
    # The mode bin is well-determined; its bound should stay much closer to the point.
    assert lcb["26"] > qpt["26"] / 2.0, "mode bin q_lcb should stay near the point (low fragility)"


def test_zero_center_uncertainty_bounds_equal_point() -> None:
    # center_sigma == 0 means a perfectly-determined center: every draw is μ*, so q_lcb == q_ucb ==
    # q_point per bin. The bound degenerates to the point (correct — no parameter uncertainty).
    bins = _milan_bins()
    mu, sig_pred, hs = 26.4, 2.0, 0.5
    qpt = _point_q(bins, mu=mu, sigma=sig_pred, half_step=hs)
    lcb, ucb = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=0.0, predictive_sigma_c=sig_pred,
        bins=bins, half_step=hs, q_point=qpt,
    )
    for b in bins:
        k = b.bin_id
        assert lcb[k] == pytest.approx(qpt[k], abs=1e-9)
        assert ucb[k] == pytest.approx(qpt[k], abs=1e-9)


def test_bounds_raise_on_nonpositive_predictive_sigma() -> None:
    bins = _milan_bins()
    qpt = {b.bin_id: 1.0 / len(bins) for b in bins}
    with pytest.raises(ValueError):
        _build_fused_q_bounds(
            mu_star=26.0, center_sigma_c=0.7, predictive_sigma_c=0.0,
            bins=bins, half_step=0.5, q_point=qpt,
        )


# ---------------------------------------------------------------------------
# Integration: _insert_posterior populates q_lcb_json / q_ucb_json on the fused-q path.
# ---------------------------------------------------------------------------

def test_fused_path_populates_qlcb_qucb_columns(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_MODELS)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _bound_row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] == "fused_normal_direct"

    assert row["q_lcb_json"] is not None, "fused-q path must materialize a REAL q_lcb_json (not NULL)"
    assert row["q_ucb_json"] is not None, "fused-q path must materialize a REAL q_ucb_json (not NULL)"
    q = json.loads(row["q_json"])
    q_lcb = json.loads(row["q_lcb_json"])
    q_ucb = json.loads(row["q_ucb_json"])
    assert set(q_lcb) == set(q) == set(q_ucb), "bound key-sets must match the q key-set"
    for bin_id in q:
        assert 0.0 <= q_lcb[bin_id] <= q[bin_id] + 1e-9, f"q_lcb<=q_point invariant for {bin_id}"
        assert q[bin_id] - 1e-9 <= q_ucb[bin_id], f"q_point<=q_ucb invariant for {bin_id}"

    assert prov["q_lcb_json_role"] == "fused_center_bootstrap_lcb"
    assert prov["q_ucb_json_role"] == "fused_center_bootstrap_ucb"
    assert prov["q_lcb_basis"] == "fused_center_bootstrap_p05"
    assert prov["q_lcb_bootstrap_draws"] == mod._QLCB_BOOTSTRAP_DRAWS


def test_bound_construction_failure_fails_soft_to_null(monkeypatch) -> None:
    # The bootstrap must NOT roll back the fused q point on failure — q_lcb/q_ucb stay NULL (Wilson
    # fallback, status quo) + a loud WARNING. This guarantees a bound failure can never make the live
    # path WORSE than today.
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

    def _boom(**_kw):
        raise RuntimeError("bootstrap exploded")

    monkeypatch.setattr(mod, "_build_fused_q_bounds", _boom)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_MODELS)
    _seed_current_single_runs(conn, values=_live_values())

    with _capture_warning("zeus.replacement_bayes_precision_fusion") as records:
        pid = _materialize(conn)
    row = _bound_row(conn, pid)
    prov = json.loads(row["provenance_json"])

    # Fused q point survives (the q_shape gain is NOT regressed).
    assert prov["q_shape"] == "fused_normal_direct"
    q = json.loads(row["q_json"])
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)
    # Bounds fail soft to NULL == Wilson-fallback status quo.
    assert row["q_lcb_json"] is None
    assert row["q_ucb_json"] is None
    assert prov["q_lcb_json_role"] == "absent_no_calibrated_lcb_available"
    assert prov["q_ucb_json_role"] == "absent_no_calibrated_ucb_available"
    assert prov["q_lcb_basis"] is None
    assert any("q_lcb/q_ucb bootstrap skipped" in r.getMessage() for r in records), (
        "a bound failure must emit a loud WARNING (anti-silent-sink)"
    )


class _capture_warning:
    """Context manager capturing WARNING+ records on a named logger."""

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)
        self._records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.setLevel(logging.WARNING)
        self._handler.emit = self._records.append  # type: ignore[assignment]

    def __enter__(self) -> list[logging.LogRecord]:
        self._prev_level = self._logger.level
        self._logger.setLevel(logging.WARNING)
        self._logger.addHandler(self._handler)
        return self._records

    def __exit__(self, *_exc) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prev_level)
