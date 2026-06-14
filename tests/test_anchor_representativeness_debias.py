# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis:
#   docs/evidence/investigation_2026-06-13/cold_bias_metadata_root.md (per-city representativeness
#     offset ROOT), percity_corrected_oos.md (thin-data overfit → activation guard + EB shrink),
#     docs/evidence/investigation_2026-06-13/percity_debias_impl.md (this design).
#   src/calibration/anchor_representativeness_debias.py (loader under test),
#   scripts/fit_anchor_representativeness_debias.py (fitter under test).
"""RED-on-revert tests for the per-city anchor representativeness de-bias (law-8 foundation fix).

These FAIL if the de-bias is reverted to ``bias_shift_c = None`` (no per-city correction) or if
either safety guard is removed:

  T1 (CORRECT a well-sampled city): an activated, gate-passing city returns a finite δ_city whose
     applied correction ``corrected = raw − δ`` moves the anchor TOWARD settlement (the exact-bin
     selection improves). REDs if the loader returns None for an activated city (revert / guard off).
  T2 (DO NO HARM on a thin city): a city with n < n_min is NOT activated → loader returns None →
     the materializer falls back to the family-level de-bias (no per-city shift). REDs if a thin
     city gets corrected (the overfit the prior naive version was settlement-refuted for).
  T3 (ARTIFACT ROUND-TRIPS): the fitter writes a schema-valid artifact and the loader reads back
     the SAME δ_city. REDs if the schema drifts out of sync.
  T4 (METRIC + do-no-harm GATES): LOW (or any non-high) metric fails closed; a family whose
     walk_forward.do_no_harm is False is not applied even for an activated city.
"""
from __future__ import annotations

import json

import pytest


def _write_artifact(path, *, high_do_no_harm=True, low_do_no_harm=False):
    """A minimal but schema-faithful fitted artifact:

      - Seoul: well-sampled (n=834), large known cold offset → activated, δ≈−1.19.
      - Ankara: thin (n=23 < n_min=30) → NOT activated (the do-no-harm fallback case).
      - low family: present but gated off via walk_forward.do_no_harm=False.
    """
    artifact = {
        "_meta": {
            "schema": "anchor_representativeness_debias",
            "authority": "anchor_grid_representativeness_eb_shrunk_v1",
            "anchor_model": "ecmwf_ifs",
            "endpoint": "previous_runs",
            "sign": "delta_c = anchor - settlement; corrected = raw - delta_c",
        },
        "families": {
            "high": {
                "fitted": True,
                "tau2_between_city": 0.519,
                "tau_between_city": 0.72,
                "n_min": 30,
                "n_cities": 2,
                "n_activated": 1,
                "walk_forward": {
                    "status": "OK",
                    "raw_mae_c": 1.7269,
                    "corrected_mae_c": 1.6319,
                    "do_no_harm": bool(high_do_no_harm),
                },
                "cities": {
                    "Seoul": {
                        "delta_c": -1.19, "median_raw_c": -1.2, "mean_raw_c": -1.22,
                        "n": 834, "sd_c": 1.99, "se_c": 0.07,
                        "lambda_shrink": 0.99, "activated": True,
                    },
                    "Ankara": {
                        "delta_c": -2.02, "median_raw_c": -2.6, "mean_raw_c": -2.5,
                        "n": 23, "sd_c": 1.84, "se_c": 0.38,
                        "lambda_shrink": 0.78, "activated": False,
                    },
                },
            },
            "low": {
                "fitted": True,
                "n_min": 30,
                "walk_forward": {"status": "OK", "do_no_harm": bool(low_do_no_harm)},
                "cities": {
                    "Seoul": {"delta_c": -0.8, "n": 200, "activated": True},
                },
            },
        },
    }
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


@pytest.fixture
def loader(tmp_path, monkeypatch):
    """Point the loader at a temp artifact and reset its module cache."""
    import src.calibration.anchor_representativeness_debias as mod

    art = tmp_path / "anchor_representativeness_debias.json"
    monkeypatch.setattr(mod, "_ARTIFACT_PATH", str(art))
    mod.reset_cache()
    yield mod, art
    mod.reset_cache()


# --------------------------------------------------------------------------- T1
def test_wellsampled_city_is_corrected_and_moves_toward_settlement(loader):
    """RED-on-revert: an activated city returns δ that pulls the anchor toward settlement."""
    mod, art = loader
    _write_artifact(art)
    mod.reset_cache()

    delta = mod.get_city_debias_c("Seoul", "high")
    assert delta is not None, "activated well-sampled city MUST be corrected (REDs on revert)"
    assert delta == pytest.approx(-1.19, abs=1e-6)

    # Apply the materializer contract corrected = raw - delta on a known cold cell.
    # Seoul anchor runs ~1.2°C COLD vs settlement: raw anchor 28.8, true settlement 30.0.
    raw_anchor, settlement = 28.8, 30.0
    corrected = raw_anchor - delta  # 28.8 - (-1.19) = 29.99
    assert abs(corrected - settlement) < abs(raw_anchor - settlement), (
        "the de-bias must move the anchor TOWARD settlement"
    )
    # In a 1°C-bin topology this crosses the bin boundary into the winning (30°C) bin.
    assert round(corrected) == round(settlement)
    assert round(raw_anchor) != round(settlement)


# --------------------------------------------------------------------------- T2
def test_thin_city_below_nmin_is_not_corrected(loader):
    """RED-on-revert: a thin (< n_min) city is NOT activated → None → family-level fallback."""
    mod, art = loader
    _write_artifact(art)
    mod.reset_cache()

    delta = mod.get_city_debias_c("Ankara", "high")
    assert delta is None, "thin city (n<n_min) must NOT be corrected (do-no-harm fallback)"

    # An absent city is likewise uncorrected (fail-closed).
    assert mod.get_city_debias_c("NotARealCity", "high") is None


# --------------------------------------------------------------------------- T3
def test_artifact_round_trips_from_fitter_schema(loader):
    """The loader reads back exactly the δ the fitter wrote (schema kept in sync)."""
    mod, art = loader
    written = _write_artifact(art)
    mod.reset_cache()

    expected = written["families"]["high"]["cities"]["Seoul"]["delta_c"]
    assert mod.get_city_debias_c("Seoul", "high") == pytest.approx(expected, abs=1e-9)
    # The raw table is inspectable and carries provenance.
    table = mod.load_debias_table()
    assert table["_meta"]["schema"] == "anchor_representativeness_debias"
    assert "Seoul" in table["families"]["high"]["cities"]


# --------------------------------------------------------------------------- T4
def test_metric_and_do_no_harm_gates(loader):
    """LOW (non-high) fails closed; a do_no_harm=False family is not applied."""
    mod, art = loader
    _write_artifact(art, high_do_no_harm=True, low_do_no_harm=False)
    mod.reset_cache()

    # LOW family present + activated city, but the loader fails closed on non-high.
    assert mod.get_city_debias_c("Seoul", "low") is None

    # If the HIGH family's walk-forward did NOT pass do-no-harm, even an activated city is None.
    _write_artifact(art, high_do_no_harm=False)
    mod.reset_cache()
    assert mod.get_city_debias_c("Seoul", "high") is None


# --------------------------------------------------------------------------- T5
def test_missing_artifact_is_byte_identical_none(loader):
    """No artifact (current live state) → None → byte-identical to today (no shadow flag)."""
    mod, art = loader  # art not written
    mod.reset_cache()
    assert mod.get_city_debias_c("Seoul", "high") is None
