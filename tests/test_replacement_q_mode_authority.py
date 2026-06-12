# Created: 2026-06-09
# Last reused or audited: 2026-06-10
# Authority basis: FIX 1/FIX 2/FIX 5 (operator-reviewed 2026-06-09) + P0 grandfather-revoked
#   (operator directive 2026-06-10). Relationship tests for the cross-module boundary: the
#   materializer DERIVES an explicit replacement_q_mode into provenance_json, and the EDLI live
#   seam (event_reactor_adapter._replacement_q_mode_live_eligibility) ADMITS real submit ONLY for
#   the fused-Normal modes. Category killed: a posterior that silently fell back to the legacy
#   member-vote soft-anchor q (fusion None / fused-q build failed / flag off) sizing live Kelly
#   under a different probability regime than the release evidence assumes — distinguishable now
#   ONLY by a data-class label the live gate enforces, not by a WARNING log.
#   P0 grandfather-revoked: old DB rows with q_shape="fused_normal_direct" and no explicit
#   replacement_q_mode key are no longer live-eligible (FUSED_NORMAL_GRANDFATHER_REVOKED). They
#   carry no q_lcb/q_ucb bounds and would mix fused-Normal q with Wilson/AIFS q_lcb — the
#   two-measures disease that caused the Milan wrong order. Rematerialization required.
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
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (  # reuse the proven harness
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

    monkeypatch.setitem(settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


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
    replacement_q_mode == BAYES_PRECISION_FUSION_CAPTURE_MISSING, and the live gate rejects it."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    # Force the override layer to fail-soft to None (its documented contract on any error).
    monkeypatch.setattr(
        mod, "_replacement_bayes_precision_fusion_override", lambda *a, **k: None
    )
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)  # MUST NOT raise — shadow row accrues
    assert prov["replacement_q_mode"] == "BAYES_PRECISION_FUSION_CAPTURE_MISSING"
    assert prov["q_shape"] == "aifs_member_votes_soft_anchor"
    assert prov["capture_status"] == "STALE_HISTORY_ONLY"

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is False
    assert mode == "BAYES_PRECISION_FUSION_CAPTURE_MISSING"


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
    assert prov["bayes_precision_fusion"]["decorrelated_providers_served"] == 5
    assert prov["bayes_precision_fusion"]["decorrelated_providers_complete"] is True

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


def test_grandfathered_fused_row_without_mode_key_rejected() -> None:
    """P0 grandfather-revoked (operator directive 2026-06-10): a pre-change DB row with
    q_shape="fused_normal_direct" and NO replacement_q_mode key is NO LONGER live-eligible.
    These rows have no q_lcb/q_ucb bounds and would size Kelly under fused-Normal q +
    Wilson/AIFS q_lcb — the two-measures disease. FUSED_NORMAL_GRANDFATHER_REVOKED is the
    rejection reason; rows must rematerialize to get proper bounds and be admitted."""
    grandfathered = {"q_shape": "fused_normal_direct"}  # no replacement_q_mode key
    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(grandfathered))
    assert eligible is False
    assert mode == "FUSED_NORMAL_GRANDFATHER_REVOKED"


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
    sigma: strictly flatter (max-bin probability lower) than the same cell with NO floor cell.
    Plus the provenance fields record the floor application.

    Wave-2 item 6 (2026-06-12): the floor is applied by PER-CELL DATA AVAILABILITY (no flag).
    The unfloored comparison is now produced by making the floor cell ABSENT (lookup -> None),
    not by toggling a deleted flag."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

    # Floor cell PRESENT (Paris JJA high has an empirical floor ~4.3C >> the ~1.x sigma_pred).
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
    assert prov_on["settlement_sigma_floor_c"] > prov_on["bayes_precision_fusion"]["predictive_sigma_c"]

    # Floor cell ABSENT for the identical cell -> the floor is inert (no widening).
    monkeypatch.setattr(
        mod,
        "_replacement_settlement_sigma_floor_lookup",
        lambda request, *, metric: (None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test"),
    )
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

    monkeypatch.setitem(settings["edli"], "edli_settlement_sigma_floor_enabled", True)
    monkeypatch.setitem(settings["edli"], "edli_settlement_sigma_floor_required", False)
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


def test_floor_absent_no_longer_degrades_to_partial(monkeypatch) -> None:
    """Wave-2 item 6 (2026-06-12): the settlement-floor-REQUIRED mode-degrade
    (edli_settlement_sigma_floor_required) is DELETED. A fused-N built WITHOUT an available
    floor cell stays FUSED_NORMAL_FULL — a missing floor is data-availability-inert and never
    degrades the q-mode. (The former required=True -> PARTIAL path is gone.)"""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

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
    # A missing floor no longer degrades the mode (the required-flag PARTIAL path is deleted).
    assert prov["replacement_q_mode"] == "FUSED_NORMAL_FULL"

    eligible, mode = _replacement_q_mode_live_eligibility(_BundleStub(prov))
    assert eligible is True
    assert mode == "FUSED_NORMAL_FULL"


# =====================================================================================
# PR#403 FIX — bounds required for live eligibility (both seams)
# =====================================================================================

from src.engine.event_reactor_adapter import _replacement_q_mode_live_eligibility as _q_mode_elig


class _BundleStubWithBounds:
    """Extends _BundleStub with q_lcb/q_ucb attributes for the second-gate bound-presence check."""

    def __init__(
        self,
        provenance_json: dict,
        *,
        q_lcb: object = None,
        q_ucb: object = None,
    ) -> None:
        self.provenance_json = provenance_json
        self.q_lcb = q_lcb
        self.q_ucb = q_ucb


def _check_live_gate(bundle) -> tuple[bool, str]:
    """Exercise the full live-gate seam: q_mode eligibility + bounds-presence check.

    Mirrors the two-check sequence in _replacement_authority_probability_and_fdr_proof:
      1. _replacement_q_mode_live_eligibility
      2. bounds presence / basis check (only reached for live-eligible modes)
    Returns (eligible, rejection_reason) — True/"OK" on pass.

    NOTE: grandfathered rows (q_shape=fused_normal_direct, no mode key) are rejected by the
    FIRST gate (FUSED_NORMAL_GRANDFATHER_REVOKED) — the grandfather branch is deleted. The
    second bounds check is only reached for rows that passed the first gate (FULL/PARTIAL).
    """
    from typing import Mapping
    eligible, q_mode = _replacement_q_mode_live_eligibility(bundle)
    if not eligible:
        return False, f"REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE#{q_mode}"
    prov = getattr(bundle, "provenance_json", None) or {}
    q_shape = str(prov.get("q_shape") if isinstance(prov, Mapping) else "")
    # Second gate: bounds required for fused_normal_direct shape rows that passed the first gate.
    needs_bounds = q_shape == "fused_normal_direct"
    if needs_bounds:
        qlcb = getattr(bundle, "q_lcb", None) or {}
        qucb = getattr(bundle, "q_ucb", None) or {}
        lcb_basis = prov.get("q_lcb_basis") if isinstance(prov, Mapping) else None
        bounds_ok = (
            isinstance(qlcb, Mapping) and bool(qlcb)
            and isinstance(qucb, Mapping) and bool(qucb)
            and lcb_basis == "fused_center_bootstrap_p05"
        )
        if not bounds_ok:
            return False, (
                f"REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE#FUSED_NORMAL_BOUNDS_MISSING"
                f":q_shape={q_shape}:q_mode={q_mode}:lcb_basis={lcb_basis}"
            )
    return True, "OK"


def test_bounds_failure_materializes_fused_normal_bounds_missing_mode(monkeypatch) -> None:
    """PR#403: when the fused-q point succeeds but _build_fused_q_bounds raises, the mode MUST
    be FUSED_NORMAL_BOUNDS_MISSING (NOT FULL/PARTIAL). Shadow row still materializes (point q intact).
    Category killed: a FULL/PARTIAL row with NULL q_lcb_json was live-eligible before this fix,
    letting buy_yes fall back to Wilson — the two-measures disease (fused-Normal q + legacy LCB)."""
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

    def _boom(**_kw):
        raise RuntimeError("bootstrap exploded")

    monkeypatch.setattr(mod, "_build_fused_q_bounds", _boom)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=_FULL_MODELS)
    _seed_current_single_runs(conn, values=_full_live_values())

    prov = _materialize_provenance(conn)  # must NOT raise — shadow accrual preserved

    # Point q shape is intact (the fused-Normal shape gain is not regressed).
    assert prov["q_shape"] == "fused_normal_direct", (
        "bounds failure must NOT roll back the fused q point"
    )
    # Mode is FUSED_NORMAL_BOUNDS_MISSING — distinct from FUSED_Q_BUILD_FAILED.
    assert prov["replacement_q_mode"] == "FUSED_NORMAL_BOUNDS_MISSING", (
        "bounds failure must produce FUSED_NORMAL_BOUNDS_MISSING, not FULL/PARTIAL"
    )
    assert prov["q_lcb_basis"] is None
    assert prov["q_lcb_json_role"] == "absent_no_calibrated_lcb_available"

    # Live gate (first check only — q_mode) rejects this mode.
    eligible, mode = _q_mode_elig(_BundleStub(prov))
    assert eligible is False
    assert mode == "FUSED_NORMAL_BOUNDS_MISSING"


def test_prefixed_row_full_mode_null_bounds_rejected_by_second_gate() -> None:
    """PR#403 belt-and-braces: a row materialized BEFORE the fix has mode=FUSED_NORMAL_FULL
    but NULL q_lcb_json (bundle q_lcb/q_ucb empty). The second gate catches this and rejects
    with FUSED_NORMAL_BOUNDS_MISSING. No code path may write FULL/PARTIAL and NULL bounds now,
    but any rows already in the DB before the fix must be caught here."""
    pre_fix_prov = {
        "q_shape": "fused_normal_direct",
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": None,  # NULL — bounds not yet materialized
        "q_lcb_json_role": "absent_no_calibrated_lcb_available",
    }
    # q_mode gate passes (FULL is live-eligible), but the second check must catch absent bounds.
    bundle = _BundleStubWithBounds(pre_fix_prov, q_lcb=None, q_ucb=None)
    eligible, reason = _check_live_gate(bundle)
    assert eligible is False
    assert "FUSED_NORMAL_BOUNDS_MISSING" in reason


def test_grandfathered_row_rejected_by_first_gate() -> None:
    """P0 grandfather-revoked (operator directive 2026-06-10): a grandfathered row (pre-key,
    q_shape=fused_normal_direct, no mode key) is rejected by the FIRST gate with
    FUSED_NORMAL_GRANDFATHER_REVOKED — the grandfather branch has been deleted. The second
    gate is never reached. The next materialization will write proper bounds and mode key."""
    grandfathered_prov = {
        "q_shape": "fused_normal_direct",
        # No replacement_q_mode key (pre FIX-1 materialization).
        "q_lcb_basis": None,
    }
    bundle = _BundleStubWithBounds(grandfathered_prov, q_lcb=None, q_ucb=None)
    # First gate now rejects: FUSED_NORMAL_GRANDFATHER_REVOKED (not eligible).
    eligible_mode, mode = _q_mode_elig(_BundleStub(grandfathered_prov))
    assert eligible_mode is False
    assert mode == "FUSED_NORMAL_GRANDFATHER_REVOKED"
    # Full gate path also rejects, reason contains the mode tag.
    eligible, reason = _check_live_gate(bundle)
    assert eligible is False
    assert "FUSED_NORMAL_GRANDFATHER_REVOKED" in reason


def test_happy_path_full_mode_with_bounds_passes_both_gates() -> None:
    """PR#403 happy-path confirmation: FUSED_NORMAL_FULL + proper bounds + correct basis passes
    both gates. Ensures the fix does not regress the working flow."""
    happy_prov = {
        "q_shape": "fused_normal_direct",
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": "fused_center_bootstrap_p05",
        "q_lcb_json_role": "fused_center_bootstrap_lcb",
    }
    q_lcb = {"bin_25": 0.12, "bin_26": 0.18, "bin_27": 0.10}
    q_ucb = {"bin_25": 0.22, "bin_26": 0.25, "bin_27": 0.20}
    bundle = _BundleStubWithBounds(happy_prov, q_lcb=q_lcb, q_ucb=q_ucb)
    eligible, reason = _check_live_gate(bundle)
    assert eligible is True
    assert reason == "OK"
