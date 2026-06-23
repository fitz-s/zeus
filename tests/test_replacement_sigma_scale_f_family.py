# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: OPERATOR LAW 2026-06-12 "没有一个人可以在没有数学支持下决定一个 hard coded value" — the
#   σ-scale correction must be MLE-FITTED, never hand-set; AND the universal-correctness mandate
#   (buy_yes must land on the predicted/modal bin for EVERY market, not just Celsius cities). The
#   σ-scale fitter (scripts/fit_sigma_scale.py) REFUSES a unit family below MIN_CELLS=60 settled
#   cells (writes fitted=False → inert) and LICENSES it once it crosses (fitted=True, k from MLE).
#   As of state/sigma_scale_fit.json fitted_at 2026-06-21 the F family crossed the floor
#   (n_cells=100, k=0.7322, w=0.0552; settled d=0 modal-ratio 1.424→1.090). The materializer must
#   honor that licensing UNIFORMLY: the artifact's per-family `fitted` flag is the sole enable, not
#   a hardcoded `unit == "C"` allow-list. A stale defense-in-depth gate that forced every non-C
#   family to (1.0, 0.0) was suppressing the math-licensed F correction, leaving every US
#   (Fahrenheit) city's served posterior too FLAT → buy_yes leaked to deep-OTM tails instead of the
#   predicted bin. These antibodies lock the gate-free contract.
"""Antibodies for F-family σ-scale licensing — the fitted artifact is the SOLE authority.

Category killed: a HARDCODED settlement-unit allow-list (`unit == "C"`) that suppresses a
math-licensed correction for any other family. The licensing decision belongs to the fitter (which
encodes it as the per-family `fitted` flag, gated on n>=MIN_CELLS settled cells), NOT to a unit
literal in the consumer.

Invariants proven (each RED if the `unit != "C"` gate is re-introduced):
  1. A FITTED F family in the artifact → `_effective_unit_sigma_scale("F")` returns its fitted
     (k, w, floor_steps) — NOT inert. (Was forced inert by the stale gate.)
  2. A REFUSED F family (fitted=False, n<60) → inert (1.0, 0.0, 0.0): a family the fitter declined
     stays uncorrected. The refusal floor is preserved (no math support → no correction).
  3. A FITTED C family is unchanged by the gate removal (regression guard).
  4. The effective-scale seam is a pure delegate to the fitted-artifact lookup (no unit literal):
     for ANY unit the fitter licensed, the effective scale equals the lookup. (Structural
     RED-on-revert: a re-introduced `unit != "C"` branch makes F diverge from its lookup value.)
  5. The inline q-build call site carries NO settlement-unit allow-list (source antibody): the
     `_city_unit != "C"` / `!= 'C'` override must not reappear in the materializer.
"""
from __future__ import annotations

import json
from pathlib import Path

import src.config as cfg
import src.data.replacement_forecast_materializer as mod


def _point_artifact(tmp_path: Path, monkeypatch, families: dict) -> None:
    """Write a sigma_scale_fit.json into a tmp state dir and resolve the lookup at it.

    The lookup resolves `runtime_state_path("sigma_scale_fit.json")`; patch that resolver so the
    test is hermetic (never reads the shared live artifact). Mirrors the real resolution path the
    2026-06-23 severed-σ-scale fix introduced.
    """
    path = tmp_path / "sigma_scale_fit.json"
    path.write_text(json.dumps({"_meta": {"authority": "sigma_scale_fit_v1_mle"}, "families": families}))
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)


# The live-shaped fixture: C licensed (n>=60) AND F licensed (n=100>=60), the post-2026-06-21 state.
def _c_and_f_fitted() -> dict:
    return {
        "C": {"fitted": True, "k": 0.671, "w": 0.149, "n_cells": 614},
        "F": {"fitted": True, "k": 0.7322, "w": 0.0552, "n_cells": 100},
    }


def _f_refused() -> dict:
    return {
        "C": {"fitted": True, "k": 0.671, "w": 0.149, "n_cells": 614},
        "F": {"fitted": False, "k": 1.0, "w": 0.0, "n_cells": 47,
              "refusal_reason": "INSUFFICIENT_CELLS:47<60"},
    }


# ---------------------------------------------------------------------------
# 1. Fitted F family is APPLIED (not suppressed by a unit allow-list)
# ---------------------------------------------------------------------------

def test_fitted_f_family_is_applied(monkeypatch, tmp_path) -> None:
    _point_artifact(tmp_path, monkeypatch, _c_and_f_fitted())
    k, w, floor = mod._effective_unit_sigma_scale("F")
    assert k == 0.7322, "a math-LICENSED F family must apply its fitted k (gate removed)"
    assert w == 0.0552
    assert floor == 0.0


# ---------------------------------------------------------------------------
# 2. Refused F family stays inert (no math support → no correction)
# ---------------------------------------------------------------------------

def test_refused_f_family_is_inert(monkeypatch, tmp_path) -> None:
    _point_artifact(tmp_path, monkeypatch, _f_refused())
    assert mod._effective_unit_sigma_scale("F") == (1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 3. Fitted C family unchanged by the gate removal (regression)
# ---------------------------------------------------------------------------

def test_fitted_c_family_unchanged(monkeypatch, tmp_path) -> None:
    _point_artifact(tmp_path, monkeypatch, _c_and_f_fitted())
    k, w, _floor = mod._effective_unit_sigma_scale("C")
    assert k == 0.671
    assert w == 0.149


# ---------------------------------------------------------------------------
# 4. Effective scale is a pure delegate to the fitted-artifact lookup (no unit literal)
# ---------------------------------------------------------------------------

def test_effective_scale_equals_lookup_for_every_fitted_unit(monkeypatch, tmp_path) -> None:
    _point_artifact(tmp_path, monkeypatch, _c_and_f_fitted())
    for unit in ("C", "F"):
        assert mod._effective_unit_sigma_scale(unit) == mod._replacement_sigma_scale_lookup(unit), (
            f"effective scale for {unit} must equal the fitted-artifact lookup — no unit allow-list"
        )


# ---------------------------------------------------------------------------
# 5. Source antibody: the inline q-build carries NO settlement-unit allow-list
# ---------------------------------------------------------------------------

def test_inline_qbuild_has_no_unit_allowlist() -> None:
    src = Path(mod.__file__).read_text(encoding="utf-8")
    # The stale gate forced non-C families inert. Its re-introduction (in any spacing) must fail.
    for needle in ('_city_unit != "C"', "_city_unit != 'C'"):
        assert needle not in src, (
            f"re-introduced settlement-unit allow-list {needle!r}: the fitted artifact's per-family "
            "`fitted` flag is the sole licensing authority (universal-correctness fix 2026-06-23)"
        )
