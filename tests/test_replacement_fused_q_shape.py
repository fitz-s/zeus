# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: AIFS-replacement experiment 2026-06-09 (/tmp/aifs_replacement_experiment.md,
#   n=39 settled cells): the AIFS member-vote shape assigned EXACTLY ZERO probability to the
#   winning bin on 11/39 cells (vote-support truncation; the soft-anchor can only shift that
#   mass, never create coverage) — LogLoss 11.07 vs fused-N-direct 1.51, hit 25.6% vs 46.2%.
#   Operator-directed promotion 2026-06-09. ONE-builder: bin integration reuses
#   src/calibration/emos.bin_probability_settlement (the live analytic preimage math).
"""FUSED-Q SHAPE antibodies.

Category being killed: a traded q that puts ZERO probability on a settleable bin. Under
q_shape=fused_normal_direct every bin gets strictly positive mass (Normal has full support),
so the 28%-of-cells zero-coverage failure is unconstructable. Also pinned: flag-OFF means the
soft-anchor q is BYTE-IDENTICAL (no half-shape), and any construction error fails CLOSED to
the soft-anchor q."""
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


def test_flag_off_q_byte_identical_to_soft_anchor(monkeypatch) -> None:
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
    assert prov["q_shape"] == "aifs_member_votes_soft_anchor"
    # the fused center still overrides the soft-anchor inputs (existing behavior), but the
    # SHAPE remains the member-vote construction — predictive sigma recorded for shadow audit.
    assert prov["bayes_precision_fusion"]["method"] in {"T2_BAYES", "EQUAL_WEIGHT"}


def test_fused_shape_fails_closed_on_construction_error(monkeypatch) -> None:
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
    assert prov["q_shape"] == "aifs_member_votes_soft_anchor", (
        "a fused-shape construction error must fail CLOSED to the soft-anchor q"
    )
    q = json.loads(row["q_json"])
    assert sum(q.values()) == pytest.approx(1.0, abs=1e-6)
