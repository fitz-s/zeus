# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: capital-gated per-city rho-mix serving (frontier-consult validated, fitter side done).
#   SUPERSEDES the prior "hard-swap" contract (the materializer served any per-city (k,w) directly — an
#   UNSAFE swap that harmed ~40% of cities). The new contract: `_effective_unit_sigma_scale` returns the
#   GLOBAL family pair ONLY; the per-city candidate (k_eb, w_eb, score_capital) is exposed SEPARATELY by
#   `_replacement_city_candidate_lookup` and served via a capital-gated MIXTURE rho = 1-exp(-C/W), so a
#   city with no earned capital (or no "cities" key) is byte-identical to global.
"""Per-city sigma-scale reader: the GLOBAL lookup ignores the city; the city candidate is read
separately and only when it carries positive earned OOS score capital. An artifact with no per-city
layer keeps the global pair, byte-identical to the prior global-only behavior."""
from __future__ import annotations

import json

import src.config as _cfg
import src.data.replacement_forecast_materializer as mat


def _write(tmp_path, fam):
    art = tmp_path / "sigma_scale_fit.json"
    art.write_text(json.dumps({"_meta": {}, "families": {"C": fam}}))
    return art


# ---------------------------------------------------------------------------
# GLOBAL lookup: `_effective_unit_sigma_scale` returns the FAMILY pair ONLY — it no longer
# hard-swaps the per-city (k,w). Existing callers / the global q build are unchanged.
# ---------------------------------------------------------------------------


def test_global_lookup_returns_family_pair_with_cities_present(tmp_path, monkeypatch):
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": 4.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    # The GLOBAL effective scale is the family pair — it is city-independent (no hard swap). The
    # per-city Taipei pair lives ONLY in the candidate lookup, served via the rho mixture.
    k, w, _ = mat._effective_unit_sigma_scale("C")
    assert round(k, 2) == 0.70 and round(w, 2) == 0.15


def test_global_lookup_no_cities_key_is_byte_identical(tmp_path, monkeypatch):
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    k, w, _ = mat._effective_unit_sigma_scale("C")
    assert round(k, 2) == 0.70 and round(w, 2) == 0.15


# ---------------------------------------------------------------------------
# CITY CANDIDATE lookup: separate, capital-gated. Returns {k, w, score_capital} | None.
# ---------------------------------------------------------------------------


def test_city_candidate_with_positive_capital_returned(tmp_path, monkeypatch):
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": 4.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    cand = mat._replacement_city_candidate_lookup("C", "Taipei")
    assert cand is not None
    assert round(cand["k"], 2) == 0.91
    assert round(cand["w"], 2) == 0.07
    assert cand["score_capital"] == 4.0


def test_city_candidate_non_positive_capital_is_none(tmp_path, monkeypatch):
    # score_capital <= 0 => no candidate (rho would be 0 anyway; the lookup signals "serve global").
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": 0.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    assert mat._replacement_city_candidate_lookup("C", "Taipei") is None
    art2 = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                             "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": -1.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art2)
    assert mat._replacement_city_candidate_lookup("C", "Taipei") is None


def test_city_candidate_missing_capital_key_is_none(tmp_path, monkeypatch):
    # No score_capital key => cannot license a mix => None (defensive; the fitter always writes it).
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    assert mat._replacement_city_candidate_lookup("C", "Taipei") is None


def test_city_candidate_absent_city_is_none(tmp_path, monkeypatch):
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": 4.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    assert mat._replacement_city_candidate_lookup("C", "Nowhere") is None


def test_city_candidate_no_cities_key_is_none(tmp_path, monkeypatch):
    # The current live artifact has no "cities" key -> candidate lookup must be None (pure global).
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    assert mat._replacement_city_candidate_lookup("C", "Taipei") is None


def test_city_candidate_unfitted_family_is_none(tmp_path, monkeypatch):
    # A refused family (fitted=False) licenses nothing — no global scale, no city candidate.
    art = _write(tmp_path, {"fitted": False, "refusal_reason": "INSUFFICIENT_CELLS:10<60",
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": 4.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    assert mat._replacement_city_candidate_lookup("C", "Taipei") is None


def test_city_candidate_no_city_arg_is_none(tmp_path, monkeypatch):
    art = _write(tmp_path, {"fitted": True, "k": 0.70, "w": 0.15,
                            "cities": {"Taipei": {"k": 0.91, "w": 0.07, "score_capital": 4.0}}})
    monkeypatch.setattr(_cfg, "runtime_state_path", lambda name: art)
    assert mat._replacement_city_candidate_lookup("C", None) is None
