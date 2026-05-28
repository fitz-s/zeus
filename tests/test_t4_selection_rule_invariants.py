# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: operator redesign 2026-05-28 — accept rule for candidate model selection.
#   Relationship test (Fitz methodology: relationship tests BEFORE implementation). The
#   cross-module invariant pinned here: a correction enters the live-bound selection ONLY IF,
#   on held-out data, it beats raw on >=2/3 proper scores AND its bootstrap improvement LCB>0
#   AND it does not catastrophically regress any cohort. Otherwise raw identity is chosen.
#   This is the antibody: "promote a correction that did not beat raw OOS" must be unwritable.
import importlib.util
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "score_error_model_candidates.py"
_spec = importlib.util.spec_from_file_location("score_error_model_candidates", _MOD_PATH)
sec = importlib.util.module_from_spec(_spec)
# Register before exec: Py3.14 dataclasses._is_type resolves field annotations via
# sys.modules[cls.__module__] under `from __future__ import annotations`.
sys.modules["score_error_model_candidates"] = sec
_spec.loader.exec_module(sec)

choose_candidate = sec.choose_candidate

# raw baseline the corrections must beat (lower = better for all three).
RAW = {"logloss": 1.000, "rps": 0.500, "brier": 0.250}


def _better(delta):
    """raw minus delta on every metric => a candidate that beats raw on all 3."""
    return {"logloss": 1.000 - delta, "rps": 0.500 - delta, "brier": 0.250 - delta}


def test_clear_winner_is_selected():
    """Beats raw on all 3, LCB>0, not catastrophic => correction chosen."""
    d = choose_candidate(
        candidate_metrics={"live_bias": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"live_bias": 0.05},
        catastrophic={"live_bias": False},
    )
    assert d.chosen == "live_bias"
    assert d.raw_is_default is False
    assert d.beats_raw_count["live_bias"] == 3


def test_only_one_metric_win_falls_back_to_raw():
    """Beats raw on 1/3 (< 2) => raw, even with healthy LCB."""
    cand = {"logloss": 0.900, "rps": 0.600, "brier": 0.300}  # only logloss better
    d = choose_candidate(
        candidate_metrics={"scale_only": cand},
        raw_metrics=RAW,
        improvement_lcb={"scale_only": 0.05},
        catastrophic={"scale_only": False},
    )
    assert d.chosen == "raw"
    assert d.raw_is_default is True
    assert d.beats_raw_count["scale_only"] == 1


def test_infold_win_but_nonpositive_lcb_falls_back_to_raw():
    """Beats raw on 3/3 but bootstrap LCB<=0 (overfit / not OOS-robust) => raw."""
    d = choose_candidate(
        candidate_metrics={"transported": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"transported": -0.01},
        catastrophic={"transported": False},
    )
    assert d.chosen == "raw"
    assert d.raw_is_default is True


def test_zero_lcb_is_not_strictly_positive_falls_back_to_raw():
    """LCB exactly 0 is not > 0 => raw (strict inequality boundary)."""
    d = choose_candidate(
        candidate_metrics={"prior_bias": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"prior_bias": 0.0},
        catastrophic={"prior_bias": False},
    )
    assert d.chosen == "raw"


def test_catastrophic_regression_vetoes_even_a_winner():
    """Beats raw 3/3, LCB>0, but a cohort catastrophically regresses => raw (hard veto)."""
    d = choose_candidate(
        candidate_metrics={"hierarchical_fallback": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"hierarchical_fallback": 0.05},
        catastrophic={"hierarchical_fallback": True},
    )
    assert d.chosen == "raw"
    assert d.raw_is_default is True


def test_no_candidates_is_raw():
    d = choose_candidate({}, RAW, {}, {})
    assert d.chosen == "raw"
    assert d.passing == []


def test_exactly_two_of_three_is_eligible():
    """The >=2 boundary: beats raw on exactly 2/3 (logloss+rps, brier worse) => eligible."""
    cand = {"logloss": 0.900, "rps": 0.400, "brier": 0.300}  # 2 better, brier worse
    d = choose_candidate(
        candidate_metrics={"live_bias": cand},
        raw_metrics=RAW,
        improvement_lcb={"live_bias": 0.03},
        catastrophic={"live_bias": False},
    )
    assert d.chosen == "live_bias"
    assert d.beats_raw_count["live_bias"] == 2


def test_among_passing_pick_max_lcb():
    """Two candidates both clear the gate => the one with the larger LCB (more robust
    worst-case OOS gain) wins, NOT the one with the bigger in-fold win."""
    d = choose_candidate(
        candidate_metrics={
            "candA": _better(0.20),  # bigger in-fold win on all 3
            "candB": {"logloss": 0.950, "rps": 0.470, "brier": 0.300},  # 2/3 only
        },
        raw_metrics=RAW,
        improvement_lcb={"candA": 0.02, "candB": 0.08},  # B more robust OOS
        catastrophic={"candA": False, "candB": False},
    )
    assert set(d.passing) == {"candA", "candB"}
    assert d.chosen == "candB"


def test_nan_metric_is_not_credited_as_a_win():
    """A NaN candidate score must not count as beating raw. Here only brier is a real win
    (1/3) => raw."""
    cand = {"logloss": float("nan"), "rps": 0.600, "brier": 0.200}  # rps worse, brier better
    d = choose_candidate(
        candidate_metrics={"transported": cand},
        raw_metrics=RAW,
        improvement_lcb={"transported": 0.05},
        catastrophic={"transported": False},
    )
    assert d.chosen == "raw"
    assert d.beats_raw_count["transported"] == 1


def test_raw_in_candidate_dict_never_selected_over_itself():
    """If raw identity is passed among candidates it is skipped (cannot beat itself)."""
    d = choose_candidate(
        candidate_metrics={"raw": RAW, "live_bias": _better(0.10)},
        raw_metrics=RAW,
        improvement_lcb={"raw": 0.0, "live_bias": 0.05},
        catastrophic={},
    )
    assert d.chosen == "live_bias"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
