# Created: 2026-06-09
# Last reused or audited: 2026-06-17
# Authority basis: AIFS-replacement experiment 2026-06-09 (/tmp/aifs_replacement_experiment.md,
#   n=39 settled cells): the AIFS member-vote shape assigned EXACTLY ZERO probability to the
#   winning bin on 11/39 cells (vote-support truncation; the soft-anchor can only shift that
#   mass, never create coverage) — LogLoss 11.07 vs fused-N-direct 1.51, hit 25.6% vs 46.2%.
#   Operator-directed promotion 2026-06-09. ONE-builder: bin integration reuses
#   src/calibration/emos.bin_probability_settlement (the live analytic preimage math).
#   2026-06-17 (operator directive "drop aifs"): AIFS is no longer the fail-closed fallback. When
#   the certified fused-q SHAPE does not build, the fallback is the fused-CENTER-only Normal
#   (q_shape=fused_center_only_normal, center=mu*, ZERO AIFS pull) — NEVER the cold 0.8-AIFS
#   soft-anchor q. The byte-identical-soft-anchor assertion is superseded accordingly.
"""FUSED-Q SHAPE antibodies.

Category being killed: a traded q that puts ZERO probability on a settleable bin. Under
q_shape=fused_normal_direct every bin gets strictly positive mass (Normal has full support),
so the 28%-of-cells zero-coverage failure is unconstructable. Also pinned (2026-06-17 AIFS-drop):
when the certified fused-q shape does NOT build but a fused center exists, the fallback q is the
fused-center-only Normal (zero AIFS pull), NEVER the cold 0.8-AIFS soft-anchor q."""
from __future__ import annotations

import json
from datetime import date

import pytest

import src.data.replacement_forecast_materializer as mod
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
    from src.config import settings

    monkeypatch.setitem(settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


def _disable_fused_shape(monkeypatch) -> None:
    from src.config import settings

    monkeypatch.setitem(settings["edli"], "replacement_0_1_fused_q_shape_enabled", False)


def _materialize(conn):
    return mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)


def test_fused_shape_q_has_full_support_no_zero_bins(monkeypatch) -> None:
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] == "fused_normal_direct"
    assert prov["bayes_precision_fusion"]["predictive_sigma_c"] is not None
    assert prov["bayes_precision_fusion"]["predictive_sigma_c"] >= 1.0  # conservative floor
    q = json.loads(row["q_json"])
    assert q, "q must be non-empty"
    for bin_id, p in q.items():
        assert p > 0.0, (
            f"bin {bin_id} got ZERO probability — the exact category the fused-Normal shape "
            "exists to kill (11/39 settled cells lost to this under the AIFS shape)"
        )
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-9)


def test_flag_off_fused_shape_falls_to_fused_center_only_never_cold_aifs(monkeypatch) -> None:
    # AIFS-DROP CONTRACT (operator directive 2026-06-17): with the certified fused-q SHAPE flag OFF
    # but a fused CENTER present, the fallback q is the fused-center-only Normal (center=mu*, zero
    # AIFS pull) — NOT the cold 0.8-AIFS soft-anchor q. This REPLACES the old
    # "flag-off == byte-identical soft-anchor" assertion (the cold fallback is the thing being killed).
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _disable_fused_shape(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] == "fused_center_only_normal", (
        "fused-shape flag OFF with a fused center must fall to the fused-center-only Normal, "
        "NEVER the cold AIFS soft-anchor q"
    )
    assert prov["replacement_q_mode"] == "FUSED_CENTER_ONLY_NORMAL"
    # The fused center still drives the q; predictive sigma recorded for shadow audit.
    assert prov["bayes_precision_fusion"]["method"] in {"T2_BAYES", "EQUAL_WEIGHT"}
    # Full Normal support: every bin strictly positive (no zero-coverage category), summing to 1.
    q = json.loads(row["q_json"])
    assert q and all(p > 0.0 for p in q.values())
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)


def test_fused_shape_total_integrator_failure_never_cold_aifs(monkeypatch) -> None:
    # AIFS-DROP CONTRACT (operator directive 2026-06-17): when the settlement integrator is TOTALLY
    # broken, BOTH the certified fused-q shape AND the fused-center-only Normal fail to build (both
    # use bin_probability_settlement). The row must then carry the honest UNIFORM placeholder seed —
    # NEVER the cold 0.8-AIFS soft-anchor q. (Previously this test asserted fail-closed TO the
    # soft-anchor q; that cold pull is exactly what the directive kills.)
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    import src.calibration.emos as emos_mod

    def _boom(**_kw):
        raise RuntimeError("integration exploded")

    monkeypatch.setattr(emos_mod, "bin_probability_settlement", _boom)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] != "aifs_member_votes_soft_anchor", (
        "a total fused construction failure must NEVER serve the cold 0.8-AIFS soft-anchor q"
    )
    assert prov["q_shape"] == "uniform_placeholder_pending_fused"
    q = json.loads(row["q_json"])
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)
    # Uniform: every bin equal mass (the honest max-entropy seed, not an AIFS-shaped distribution).
    _vals = list(q.values())
    assert all(v == pytest.approx(_vals[0], abs=1e-9) for v in _vals)


# ---------------------------------------------------------------------------
# AIFS-DROP RED-on-revert antibodies (operator directive 2026-06-17 "drop aifs").
# These fail RED if the materializer re-introduces a HARD AIFS dependency or the cold
# 0.8-AIFS soft-anchor fail-closed fallback.
# ---------------------------------------------------------------------------


def _materialize_no_aifs(conn):
    """A materialization request with NO AIFS extraction (the drop-AIFS live posture)."""
    import dataclasses

    req = dataclasses.replace(
        _request(),
        aifs_extraction=None,
        aifs_source_run_id=None,
        aifs_source_available_at=None,
        aifs_artifact_id=None,
    )
    return mod._insert_posterior(conn, req, metric="high", anchor_id=1)


def test_no_aifs_extraction_still_materializes_fused_posterior(monkeypatch) -> None:
    # RED-on-revert: with the AIFS extraction ABSENT but a valid fused-q input present, the
    # materializer MUST still produce a posterior from the multi-model fused Normal (q_shape=
    # fused_normal_direct). If AIFS is re-made a hard dependency, _insert_posterior raises here.
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize_no_aifs(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    # The fused Normal materialized WITHOUT any AIFS extraction.
    assert prov["q_shape"] == "fused_normal_direct", (
        "fused-q path must materialize a posterior with NO AIFS extraction present"
    )
    assert prov["aifs_present"] is False
    assert prov["aifs_identity"] is None
    assert prov["aifs_probabilities"] == {}
    assert prov["aifs_member_count"] == 0
    q = json.loads(row["q_json"])
    assert q and all(p > 0.0 for p in q.values())
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)


def test_no_aifs_fused_build_failure_falls_to_fused_center_only_never_cold_aifs(monkeypatch) -> None:
    # RED-on-revert: AIFS absent AND the certified fused-q SHAPE flag off. The fallback MUST be the
    # fused-center-only Normal (center=mu*, zero AIFS pull) — there is no AIFS substrate to pull
    # toward at all, and the served q must be the honest fused center, never a cold soft-anchor.
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _disable_fused_shape(monkeypatch)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize_no_aifs(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] == "fused_center_only_normal"
    assert prov["replacement_q_mode"] == "FUSED_CENTER_ONLY_NORMAL"
    assert prov["aifs_present"] is False
    # No AIFS member-vote bounds attach to a fused row: bounds stay NULL (non-tradeable, honest).
    bounds = conn.execute(
        "SELECT q_lcb_json, q_ucb_json FROM forecast_posteriors WHERE posterior_id = ?", (pid,)
    ).fetchone()
    assert bounds["q_lcb_json"] is None and bounds["q_ucb_json"] is None
    q = json.loads(row["q_json"])
    assert q and all(p > 0.0 for p in q.values())
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)


def test_predictive_sigma_none_does_not_fabricate_spread_from_center_uncertainty(monkeypatch) -> None:
    # AIFS-DROP CORRECTNESS (operator directive 2026-06-17; reinforced by frontier design review):
    # when the fused override's predictive_sigma_c is None (residual substrate too thin), the
    # fused-center-only Normal must NOT be built by substituting anchor_sigma_c (the fused CENTER
    # uncertainty) for the predictive SETTLEMENT spread — that conflates two distinct quantities.
    # The honest result is the uniform placeholder seed (non-tradeable), NEVER a fabricated-spread
    # Normal and NEVER the cold AIFS soft-anchor q.
    import dataclasses

    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)

    _orig = mod._replacement_bayes_precision_fusion_override

    def _override_no_sigma(*args, **kwargs):
        ov = _orig(*args, **kwargs)
        if ov is None:
            return None
        # Strip predictive_sigma_c -> the thin-substrate posture; anchor_sigma_c stays set.
        return dataclasses.replace(ov, predictive_sigma_c=None)

    monkeypatch.setattr(mod, "_replacement_bayes_precision_fusion_override", _override_no_sigma)
    conn = _conn()
    models = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
    _seed_history(conn, decision=date(2026, 6, 7), models=models)
    _seed_current_single_runs(conn, values=_live_values())
    pid = _materialize_no_aifs(conn)
    row = _row(conn, pid)
    prov = json.loads(row["provenance_json"])
    assert prov["q_shape"] == "uniform_placeholder_pending_fused", (
        "predictive_sigma None must NOT fabricate a fused-center Normal from center uncertainty"
    )
    assert prov["q_shape"] != "aifs_member_votes_soft_anchor"
    assert prov["replacement_q_mode"] != "FUSED_CENTER_ONLY_NORMAL"
    q = json.loads(row["q_json"])
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)
    _vals = list(q.values())
    assert all(v == pytest.approx(_vals[0], abs=1e-9) for v in _vals)
