# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: σ-shape refit report 2026-06-13 (/tmp/agent_report_sigma_refit.md) + task #69 —
#   wire the q-construction consumer to honor an ABSOLUTE σ-floor in step units
#   (σ_core = max(σ_impl·k, floor_steps·step)) when the FITTED σ-scale artifact carries a
#   ``floor_steps`` field. STRICT backward compatibility: the live state/sigma_scale_fit.json has NO
#   floor_steps key, so the floor term MUST be inert (byte-identical q) until the operator swaps the
#   artifact for the candidate (docs/evidence/settlement_guard/sigma_shape_kernel_candidate.json,
#   k=1.0/w=0.0/m=1.0/floor_steps=1.80). Consumer wired:
#   src/data/replacement_forecast_materializer.py::_replacement_sigma_scale_lookup (3-tuple) +
#   the σ-scale seam in _insert_posterior's fused-q build.
"""RED-on-revert relationship antibodies for the absolute σ-floor (floor_steps) wiring.

Category killed: the GATE-2 over-flattened near-center ring caused by the live uniform pedestal +
multiplicative-k form. The refit replaces the multiplicative widen with an ABSOLUTE floor in step
units; this suite proves the CONSUMER honors it, and that an artifact WITHOUT the field is unchanged.

Invariants proven here (each RED if the wiring is reverted, i.e. the ``max(σ_impl·k, floor_steps·step)``
is removed or ``floor_steps`` is ignored):

  1. test_floor_steps_absent_is_byte_identical — a FITTED C artifact that has NO ``floor_steps`` key
     (the current live artifact shape) yields BYTE-IDENTICAL q AND posterior_identity_hash to the
     inert no-artifact baseline; sigma_floor_steps_applied is None. (Guards backward compatibility:
     the live q does NOT change until the operator swaps the artifact.) RED-on-revert: if the default
     for an absent floor_steps were anything other than 0.0 the q would diverge.

  2. test_floor_steps_widens_to_absolute_floor — with floor_steps=1.80 and an over-sharp fused σ
     (σ_impl·k < 1.80·step), σ_core is lifted to 1.80·step: the mode bin q DROPS (the ring re-peaks)
     and sigma_floor_steps_applied == 1.80. RED-on-revert: remove the max() ⇒ floor never binds ⇒
     mode stays over-peaked ⇒ both the mode-drop and the provenance assertion flip.

  3. test_floor_steps_inert_when_forecast_already_wide — with a TINY floor_steps (0.0001) the floor
     never binds (σ_impl·k > floor_steps·step), σ_core == σ_impl·k: q is byte-identical to the
     no-floor case and sigma_floor_steps_applied is None (a floor must never NARROW a wide forecast).
"""
from __future__ import annotations

import json

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _live_values,
    _request,
    _row,
    _seed_current_single_runs,
    _seed_history,
)

# Same model set the precedent σ-scale wiring test uses.
_MODELS = ["ecmwf_ifs", "gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]

from datetime import date


def _enable_fused_shape(monkeypatch) -> None:
    monkeypatch.setitem(cfg.settings["edli"], "replacement_0_1_fused_q_shape_enabled", True)


def _write_artifact(tmp_path, monkeypatch, families: dict) -> str:
    path = tmp_path / "sigma_scale_fit.json"
    path.write_text(json.dumps({"_meta": {"authority": "sigma_shape_kernel_mixture_v1_mle"},
                                "families": families}))
    monkeypatch.setattr(mod, "_SIGMA_SCALE_FIT_PATH", str(path))
    return str(path)


def _no_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "_SIGMA_SCALE_FIT_PATH", str(tmp_path / "does_not_exist.json"))


def _materialize_seeded(conn, monkeypatch):
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    _enable_fused_shape(monkeypatch)
    _seed_history(conn, decision=date(2026, 6, 7), models=_MODELS)
    _seed_current_single_runs(conn, values=_live_values())
    return mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)


def _run(monkeypatch, tmp_path, families) -> tuple[dict, dict, object]:
    """Materialize one posterior under the given artifact families (None = no artifact). Returns
    (q, provenance, posterior_identity_hash)."""
    if families is None:
        _no_artifact(tmp_path, monkeypatch)
    else:
        _write_artifact(tmp_path, monkeypatch, families)
    conn = _conn()
    pid = _materialize_seeded(conn, monkeypatch)
    row = _row(conn, pid)
    return json.loads(row["q_json"]), json.loads(row["provenance_json"]), row["posterior_identity_hash"]


# ---------------------------------------------------------------------------
# 1. BACKWARD COMPATIBILITY: floor_steps ABSENT -> byte-identical to inert baseline
# ---------------------------------------------------------------------------

def test_floor_steps_absent_is_byte_identical(monkeypatch, tmp_path) -> None:
    """A FITTED C artifact with k=1.0, w=0.0 and NO floor_steps key must reproduce the inert
    no-artifact baseline BYTE-FOR-BYTE (q AND posterior_identity_hash) — the live path is unchanged
    until the operator swaps in an artifact that carries floor_steps."""
    q_base, prov_base, h_base = _run(monkeypatch, tmp_path, None)
    q_noflo, prov_noflo, h_noflo = _run(
        monkeypatch, tmp_path, {"C": {"fitted": True, "k": 1.0, "w": 0.0}}
    )

    assert q_noflo == q_base, "artifact without floor_steps must yield byte-identical q to baseline"
    assert h_noflo == h_base, "posterior_identity_hash must be unchanged (live q does not move)"
    assert prov_noflo.get("sigma_floor_steps_applied") is None
    assert prov_base.get("sigma_floor_steps_applied") is None
    assert sum(q_noflo.values()) == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. THE FLOOR BINDS: floor_steps=1.80 widens an over-sharp sigma to the absolute floor
# ---------------------------------------------------------------------------

def test_floor_steps_widens_to_absolute_floor(monkeypatch, tmp_path) -> None:
    """floor_steps=1.80 with an over-sharp fused σ (σ_impl·k < 1.80·step) lifts σ_core to 1.80·step:
    the mode bin q DROPS (ring re-peaks) and sigma_floor_steps_applied == 1.80.

    RED-on-revert: removing the max(σ_impl·k, floor_steps·step) ⇒ floor never binds ⇒ the mode stays
    over-peaked (q_floor[mode] == q_base[mode]) and sigma_floor_steps_applied stays None ⇒ both
    assertions below flip."""
    q_base, _prov_base, _h_base = _run(monkeypatch, tmp_path, None)
    q_floor, prov_floor, _h_floor = _run(
        monkeypatch, tmp_path, {"C": {"fitted": True, "k": 1.0, "w": 0.0, "floor_steps": 1.80}}
    )

    mode_bin = max(q_base, key=q_base.get)
    assert prov_floor.get("sigma_floor_steps_applied") == pytest.approx(1.80), (
        "the absolute σ-floor must bind for an over-sharp forecast (floor_steps·step > σ_impl·k)"
    )
    assert q_floor[mode_bin] < q_base[mode_bin], (
        "the σ-floor must FLATTEN the over-peaked mode bin (widen σ_core -> lower mode mass)"
    )
    assert sum(q_floor.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 <= v <= 1.0 and v == v for v in q_floor.values()), "no NaN / out-of-range q"


# ---------------------------------------------------------------------------
# 3. INERT WHEN ALREADY WIDE: a floor must never NARROW a forecast wider than the floor
# ---------------------------------------------------------------------------

def test_floor_steps_inert_when_forecast_already_wide(monkeypatch, tmp_path) -> None:
    """A tiny floor_steps (0.0001) is far below the fused σ, so σ_impl·k > floor_steps·step and the
    floor does NOT bind: q is byte-identical to the no-floor case and sigma_floor_steps_applied is
    None. The max() can only widen, never narrow an already-wide forecast."""
    q_noflo, _prov_noflo, h_noflo = _run(
        monkeypatch, tmp_path, {"C": {"fitted": True, "k": 1.0, "w": 0.0}}
    )
    q_tiny, prov_tiny, h_tiny = _run(
        monkeypatch, tmp_path, {"C": {"fitted": True, "k": 1.0, "w": 0.0, "floor_steps": 0.0001}}
    )

    assert prov_tiny.get("sigma_floor_steps_applied") is None, (
        "a floor below the forecast σ must NOT bind (and must not be recorded as applied)"
    )
    assert q_tiny == q_noflo, "a non-binding floor must leave q byte-identical"
    assert h_tiny == h_noflo
