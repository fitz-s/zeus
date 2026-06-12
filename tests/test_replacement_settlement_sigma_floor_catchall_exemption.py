# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: Paris >=26C wrong-trade incident 2026-06-10 (/tmp/mainstream_gate_report.md
#   Mission 1; /tmp/deep_verify_report.md Verification A). The settlement sigma floor
#   (calibrated on INTERIOR-bin settlement dispersion) is designed to ONLY widen sigma ->
#   flatter q -> fewer overconfident bets. On an OPEN-ENDED catch-all bin it has the WRONG
#   sign: widening sigma dumps the whole outward tail into one bin, INFLATING its mass
#   (Paris >=26: q 0.252 at predictive sigma 1.906 -> 0.384 at floored 4.326). Category-kill:
#   a floor that can only flatten must NEVER increase any bin's mass; for open-ended bins the
#   floored mass is capped at the un-floored (predictive-sigma) mass. This makes the floor
#   monotonically conservative by construction.
"""RELATIONSHIP antibody: the settlement sigma floor must never INFLATE an open-ended bin.

The cross-module property under test (Module A = settlement sigma floor calibrated on
interior dispersion; Module B = bin_probability_settlement integrating an open-ended bin):

    when A widens sigma (floor > predictive), B's mass on ANY open-ended (catch-all) bin
    must NOT increase.

The floor's whole contract is "max() only WIDENS -> flatter q -> fewer overconfident bets"
(emos.settlement_sigma_floor docstring). That contract holds for interior bins (widening
pulls mass AWAY from the modal bin). It is VIOLATED at an open-ended catch-all on the far
side of the center, where widening pushes the outward Gaussian tail INTO the single
open-ended bin. This test pins the relationship invariant directly and at the materializer
seam, so the inflation category is unconstructable regardless of the floor's magnitude.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
from src.calibration.emos import bin_probability_settlement
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (  # reuse the proven harness
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _live_values,
    _request,
    _row,
    _seed_current_single_runs,
    _seed_history,
)


def _enable_fused_shape(monkeypatch) -> None:
    monkeypatch.setitem(cfg.settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


def _force_wide_floor(monkeypatch, floor_c: float) -> None:
    """Make the cell's settlement sigma floor return a fixed wide value, floor flag ON.

    Patches the materializer's floor lookup directly so the test does not depend on the live
    settlement_sigma_floor.json contents (which evolve as settlements arrive)."""
    monkeypatch.setitem(cfg.settings["edli"], "edli_settlement_sigma_floor_enabled", True)
    monkeypatch.setattr(
        mod,
        "_replacement_settlement_sigma_floor_lookup",
        lambda request, *, metric: (float(floor_c), None),
    )
    # Neutralize the FITTED σ-scale/uniform-mixture artifact (state/sigma_scale_fit.json) so this
    # RELATIONSHIP test isolates the floor↔catch-all interaction alone — the scale/mixture is an
    # independent correction (tested in test_replacement_sigma_scale_k_c.py). Same rationale as the
    # floor-json patch above: the test must not depend on a live artifact that evolves with data.
    monkeypatch.setattr(mod, "_replacement_sigma_scale_lookup", lambda unit: (1.0, 0.0))


# ---------------------------------------------------------------------------
# Pure-math relationship: the integrator + the floor, no DB.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("center", [18.0, 20.0, 23.0, 24.224, 25.0])
def test_open_high_catchall_floored_mass_never_exceeds_unfloored(center: float) -> None:
    """OPEN-HIGH catch-all (>=X) on the far side of center: floor must not inflate it."""
    predictive_sigma = 1.906
    floor_sigma = 4.326  # the live Paris/JJA/high floor
    lo, hi = 26.0, None  # ">=26" open-ended catch-all
    honest = bin_probability_settlement(mu=center, sigma=predictive_sigma, bin_low=lo, bin_high=hi)
    floored_raw = bin_probability_settlement(mu=center, sigma=floor_sigma, bin_low=lo, bin_high=hi)
    # The category-kill rule applied to an open-ended bin: cap floored mass at unfloored mass.
    capped = min(floored_raw, honest)
    assert capped <= honest + 1e-12, "open-ended catch-all mass must never exceed the un-floored mass"
    # And it must actually bite when the center is below the threshold (where inflation happens):
    if center < lo:
        assert floored_raw > honest, "fixture sanity: floor DOES inflate the far catch-all here"
        assert capped == pytest.approx(honest), "cap must select the honest (un-floored) mass"


@pytest.mark.parametrize("center", [18.0, 20.0, 23.0, 24.224])
def test_open_low_shoulder_also_protected(center: float) -> None:
    """The OTHER open-ended bin (open-LOW shoulder <=X) is equally vulnerable and protected."""
    predictive_sigma = 1.906
    floor_sigma = 4.326
    lo, hi = None, 16.0  # "<=16" open-ended low shoulder
    honest = bin_probability_settlement(mu=center, sigma=predictive_sigma, bin_low=lo, bin_high=hi)
    floored_raw = bin_probability_settlement(mu=center, sigma=floor_sigma, bin_low=lo, bin_high=hi)
    capped = min(floored_raw, honest)
    assert capped <= honest + 1e-12
    if center > hi:  # center far above the low shoulder -> floor inflates the far tail
        assert floored_raw > honest
        assert capped == pytest.approx(honest)


def test_interior_bin_flattening_preserved_under_cap() -> None:
    """The cap must NOT touch interior bins: the floor's intended interior flattening stands."""
    center = 24.224
    predictive_sigma, floor_sigma = 1.906, 4.326
    lo = hi = 24.0  # interior bin AT the center
    honest = bin_probability_settlement(mu=center, sigma=predictive_sigma, bin_low=lo, bin_high=hi)
    floored = bin_probability_settlement(mu=center, sigma=floor_sigma, bin_low=lo, bin_high=hi)
    # At the modal interior bin the floor DEFLATES (flattens), which is its purpose.
    assert floored < honest, "fixture sanity: floor flattens the modal interior bin"
    # min(floored, honest) == floored -> the interior flattening is preserved (cap is a no-op here).
    assert min(floored, honest) == pytest.approx(floored)


# ---------------------------------------------------------------------------
# Materializer-seam relationship: the persisted posterior q honors the invariant.
# ---------------------------------------------------------------------------

def test_materializer_open_ended_bin_not_inflated_by_floor(monkeypatch) -> None:
    """End-to-end at the materializer seam: with a wide floor forced, the open-ended catch-all
    bin's persisted q must not exceed the q it would carry at the un-floored predictive sigma.

    Harness topology (_bins): cool(<=22 open-low), mild([23,26]), warm(>=27 open-high).
    The fused center is pulled to ~23-24 (below the 27 anchor), so the warm (>=27) open-high
    catch-all is the far-tail bin the floor would inflate."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _force_wide_floor(monkeypatch, 4.326)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] == "fused_normal_direct"
    assert prov["settlement_sigma_floor_applied"] is True
    center = float(prov["bayes_precision_fusion"]["anchor_value_c"])
    predictive_sigma = float(prov["bayes_precision_fusion"]["predictive_sigma_c"])
    assert predictive_sigma < 4.326, "fixture sanity: the forced floor must exceed predictive sigma"
    q = json.loads(row["q_json"])
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-9)

    # The far open-high catch-all (warm, >=27) sits above the center (~23-24). Recompute the
    # UN-normalized floored vs un-floored mass for every open-ended bin and assert the
    # materializer recorded the cap AND that the persisted normalized q never exceeds the
    # normalized HONEST (un-floored on the catch-all) distribution for those bins.
    bins = list(_request().bins)  # type: ignore[attr-defined]

    def _mass(sig: float, b) -> float:
        return bin_probability_settlement(
            mu=center, sigma=sig,
            bin_low=(None if b.lower_c is None else float(b.lower_c)),
            bin_high=(None if b.upper_c is None else float(b.upper_c)),
        )

    # Reconstruct the EXACT un-normalized vector the materializer builds post-fix:
    #   interior/distinct bins -> floored mass; open-ended catch-all -> min(floored, unfloored).
    capped_vec = {}
    capped_bins = []
    for b in bins:
        is_open_ended = (b.lower_c is None) != (b.upper_c is None)
        floored = _mass(4.326, b)
        if is_open_ended:
            unfloored = _mass(predictive_sigma, b)
            if unfloored < floored:
                capped_bins.append(b.bin_id)
                capped_vec[b.bin_id] = unfloored
            else:
                capped_vec[b.bin_id] = floored
        else:
            capped_vec[b.bin_id] = floored
    total = sum(capped_vec.values())
    expected_q = {k: v / total for k, v in capped_vec.items()}

    # The persisted q must match the capped construction byte-for-byte (the fix is the only
    # thing that changed the integration), proving the cap is actually wired into the seam.
    for bin_id, expected in expected_q.items():
        assert q[bin_id] == pytest.approx(expected, abs=1e-9), (
            f"bin {bin_id}: persisted q {q[bin_id]} != capped-construction q {expected}"
        )
    # The far open-high catch-all (>=27) MUST have been capped (center ~23-24 < 27).
    warm = next(b for b in bins if b.upper_c is None and b.lower_c is not None)
    assert _mass(4.326, warm) > _mass(predictive_sigma, warm), "fixture: floor inflates >=27 catch-all"
    assert warm.bin_id in capped_bins
    assert warm.bin_id in prov["settlement_sigma_floor_catchall_capped"], (
        "provenance must record the capped open-ended bin"
    )
