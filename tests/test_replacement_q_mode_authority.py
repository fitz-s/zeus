# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: FIX 1/FIX 2/FIX 5 (operator-reviewed 2026-06-09) on the replacement chain
#   (docs/authority/replacement_final_form_2026_06_09.md). Relationship tests for the cross-module
#   boundary: the materializer DERIVES an explicit replacement_q_mode into provenance_json, and the
#   EDLI live seam (event_reactor_adapter._replacement_q_mode_live_eligibility) ADMITS real submit
#   ONLY for the fused-Normal modes. Category killed: a posterior that silently fell back to the
#   legacy member-vote soft-anchor q (fusion None / fused-q build failed / flag off) sizing live
#   Kelly under a different probability regime than the release evidence assumes — distinguishable
#   now ONLY by a data-class label the live gate enforces, not by a WARNING log.
"""Relationship tests: replacement q-mode authority + settlement-sigma-floor coherence.

These verify the INVARIANT that holds across the materializer -> live-gate boundary:
  - shadow accrual is ALWAYS preserved (a posterior row materializes even when fusion/fused-q
    fails), so the q-mode is the ONLY thing that gates live eligibility — not row existence.
  - real submit is admitted IFF replacement_q_mode in {FUSED_NORMAL_FULL, FUSED_NORMAL_PARTIAL}
    (or a grandfathered fused_normal_direct row with no mode key).
  - the settlement sigma floor the EMOS path uses ALSO widens the fused-q sigma when present.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import src.data.replacement_forecast_materializer as mod
from src.engine.event_reactor_adapter import _replacement_q_mode_live_eligibility
from tests.test_u0r_history_provider_materializer_wiring import (  # reuse the proven harness
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _request,
    _row,
    _seed_current_single_runs,
    _seed_history,
)


# A 5-decorrelated-provider current set (globals only) → FUSED_NORMAL_FULL.
_FULL_MODELS = [
    "ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless",
    "ukmo_global_deterministic_10km",
]


def _full_live_values() -> dict[str, float]:
    return {
        "gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
        "jma_seamless": 24.0, "ukmo_global_deterministic_10km": 23.3,
    }


def _enable_fused_shape(monkeypatch) -> None:
    from src.config import settings

    monkeypatch.setitem(settings["edli_v1"], "replacement_0_1_fused_q_shape_enabled", True)


class _BundleStub:
    """Minimal stand-in for ReplacementForecastPosteriorBundle — the live gate is a data-class
    check that reads ONLY provenance_json, so the gate is exercisable without a full bundle."""

    def __init__(self, provenance_json: dict) -> None:
        self.provenance_json = provenance_json


def _materialize_provenance(conn) -> dict:
    pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)
    return json.loads(_row(conn, pid)["provenance_json"])


# =====================================================================================
# FIX 1 — explicit q-mode + live gate
# =====================================================================================
def test_fusion_override_raises_shadow_accrues_capture_missing_gate_rejects(monkeypatch) -> None:
    """Fusion override raises -> the posterior STILL materializes (shadow accrual preserved),
    replacement_q_mode == U0R_CAPTURE_MISSING, and the live gate rejects it."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    # Force the override layer to fail-soft to None (its documented contract on any error).
    monkeypatch.setattr(
        mod, "_replacement_u0r_fusion_override", lambda *a, **k: None
    )
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)  # MUST NOT raise — shadow row accrues
    assert prov["replacement_q_mode"] == "U0R_CAPTURE_MISSING"
    assert prov["q_shape"] == "aifs_member_votes_soft_anchor"
    assert prov["capture_status"] == "STALE_HISTORY_ONLY"

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is False
    assert mode == "U0R_CAPTURE_MISSING"


def test_fused_q_build_raises_mode_build_failed_gate_rejects(monkeypatch) -> None:
    """The fused-q construction itself raises (bin_probability_settlement boom) -> mode ==
    FUSED_Q_BUILD_FAILED (DISTINCT from flag-off SOFT_ANCHOR_FALLBACK), gate rejects."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    import src.calibration.emos as emos_mod

    def _boom(**_kw):
        raise RuntimeError("integration exploded")

    monkeypatch.setattr(emos_mod, "bin_probability_settlement", _boom)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)
    assert prov["replacement_q_mode"] == "FUSED_Q_BUILD_FAILED"
    assert prov["q_shape"] == "aifs_member_votes_soft_anchor", "must fail CLOSED to soft-anchor q"
    # a build failure must not leave a stale floor flag set
    assert prov["settlement_sigma_floor_applied"] is False

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is False
    assert mode == "FUSED_Q_BUILD_FAILED"


def test_happy_path_full_mode_gate_admits(monkeypatch) -> None:
    """All 5 decorrelated providers served + fused-q built -> FUSED_NORMAL_FULL; gate admits."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)
    assert prov["replacement_q_mode"] == "FUSED_NORMAL_FULL"
    assert prov["q_shape"] == "fused_normal_direct"
    assert prov["capture_status"] == "FULL_CURRENT"
    assert prov["u0r_fusion"]["decorrelated_providers_served"] == 5
    assert prov["u0r_fusion"]["decorrelated_providers_complete"] is True

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is True
    assert mode == "FUSED_NORMAL_FULL"


def test_partial_mode_gate_admits(monkeypatch) -> None:
    """A degraded decorrelated set (4/5) still builds the fused Normal -> FUSED_NORMAL_PARTIAL;
    the gate ADMITS PARTIAL (the constructed shape IS the fused Normal) but the receipt records
    the degraded mode."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    conn = _conn()
    # No UKMO -> 4/5 decorrelated providers.
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(
        conn,
        values={"gfs_global": 23.0, "icon_global": 23.5, "gem_global": 22.5,
                "jma_seamless": 24.0, "icon_eu": 23.2},
    )

    prov = _materialize_provenance(conn)
    assert prov["replacement_q_mode"] == "FUSED_NORMAL_PARTIAL"
    assert prov["q_shape"] == "fused_normal_direct"
    assert prov["capture_status"] == "PARTIAL_CURRENT"

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is True
    assert mode == "FUSED_NORMAL_PARTIAL"


def test_grandfathered_fused_row_without_mode_key_admitted() -> None:
    """A pre-change live row (q_shape == fused_normal_direct, NO replacement_q_mode key) is
    grandfathered as a fused-Normal mode so this change does not brick the 67 existing live rows."""
    grandfathered = {"q_shape": "fused_normal_direct"}  # no replacement_q_mode key
    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(grandfathered))
    assert eligible is True
    assert mode == "FUSED_NORMAL_GRANDFATHERED"


def test_legacy_non_fused_row_without_mode_key_rejected() -> None:
    """A legacy row with the member-vote shape and NO mode key is NOT grandfathered (fail-closed)."""
    legacy = {"q_shape": "aifs_member_votes_soft_anchor"}
    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(legacy))
    assert eligible is False
    assert mode == "NO_Q_MODE_KEY"


# =====================================================================================
# FIX 2 — settlement sigma floor coherence in the fused-q path
# =====================================================================================
def test_floor_present_widens_sigma_q_flatter(monkeypatch) -> None:
    """Floor present AND greater than sigma_pred -> the persisted q is built from the WIDENED
    sigma: strictly flatter (max-bin probability lower) than the same cell with the floor OFF.
    Plus the provenance fields record the floor application."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    from src.config import settings

    # Floor ON (Paris JJA high has an empirical floor ~4.3C >> the ~1.x sigma_pred).
    monkeypatch.setitem(settings["edli_v1"], "edli_settlement_sigma_floor_enabled", True)
    conn_on = _conn()
    _seed_history(conn_on, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn_on, values=_full_live_values())
    pid_on = mod._insert_posterior(conn_on, _request(), metric="high", anchor_id=1)
    prov_on = json.loads(_row(conn_on, pid_on)["provenance_json"])
    q_on = json.loads(_row(conn_on, pid_on)["q_json"])

    assert prov_on["settlement_sigma_floor_applied"] is True
    assert prov_on["settlement_sigma_floor_c"] is not None
    assert prov_on["settlement_sigma_floor_c"] > 0.0
    assert prov_on["replacement_sigma_basis"] == "fused_center_residual_std"
    assert prov_on["settlement_sigma_floor_unavailable_reason"] is None
    # the recorded floor must exceed the raw predictive sigma (else max() is a no-op)
    assert prov_on["settlement_sigma_floor_c"] > prov_on["u0r_fusion"]["predictive_sigma_c"]

    # Floor OFF for the identical cell.
    monkeypatch.setitem(settings["edli_v1"], "edli_settlement_sigma_floor_enabled", False)
    conn_off = _conn()
    _seed_history(conn_off, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn_off, values=_full_live_values())
    pid_off = mod._insert_posterior(conn_off, _request(), metric="high", anchor_id=1)
    prov_off = json.loads(_row(conn_off, pid_off)["provenance_json"])
    q_off = json.loads(_row(conn_off, pid_off)["q_json"])
    assert prov_off["settlement_sigma_floor_applied"] is False

    # A WIDER sigma flattens the Normal: the peak bin gets LESS mass.
    assert max(q_on.values()) < max(q_off.values()), (
        "the floored (wider-sigma) q must be flatter than the unfloored q"
    )


def test_floor_absent_records_reason_does_not_block(monkeypatch) -> None:
    """Floor lookup missing for the cell -> applied=false + a reason recorded; shadow still
    materializes and (with the floor NOT required) the mode stays FUSED_NORMAL_FULL."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    from src.config import settings

    monkeypatch.setitem(settings["edli_v1"], "edli_settlement_sigma_floor_enabled", True)
    monkeypatch.setitem(settings["edli_v1"], "edli_settlement_sigma_floor_required", False)
    # Force the floor lookup to report the cell as absent (no empirical floor).
    monkeypatch.setattr(
        mod,
        "_replacement_settlement_sigma_floor_lookup",
        lambda request, *, metric: (None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test"),
    )
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)
    assert prov["settlement_sigma_floor_applied"] is False
    assert prov["settlement_sigma_floor_c"] is None
    assert prov["settlement_sigma_floor_unavailable_reason"] == "SETTLEMENT_SIGMA_FLOOR_ABSENT:test"
    # floor NOT required -> a missing floor does NOT degrade the mode.
    assert prov["replacement_q_mode"] == "FUSED_NORMAL_FULL"


def test_floor_required_but_absent_degrades_to_partial(monkeypatch) -> None:
    """When edli_settlement_sigma_floor_required is true, a fused-N built WITHOUT an available
    floor degrades the mode to FUSED_NORMAL_PARTIAL (live gate still admits; receipt shows it).
    This honors the flag semantics — no new blocking lane is invented."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    from src.config import settings

    monkeypatch.setitem(settings["edli_v1"], "edli_settlement_sigma_floor_enabled", True)
    monkeypatch.setitem(settings["edli_v1"], "edli_settlement_sigma_floor_required", True)
    monkeypatch.setattr(
        mod,
        "_replacement_settlement_sigma_floor_lookup",
        lambda request, *, metric: (None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test"),
    )
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)
    assert prov["q_shape"] == "fused_normal_direct"
    assert prov["settlement_sigma_floor_applied"] is False
    assert prov["replacement_q_mode"] == "FUSED_NORMAL_PARTIAL"

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is True  # PARTIAL is still live-eligible
    assert mode == "FUSED_NORMAL_PARTIAL"
