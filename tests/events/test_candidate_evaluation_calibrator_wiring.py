# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: live-path wiring of the selection-calibrator + city-skill gate into
#   CandidateEvaluation.admitted (team-lead 2026-06-22; live_order_pathology 2026-06-22).
"""CandidateEvaluation.admitted reflects the live selection calibrator and explicit city-skill gate."""
from __future__ import annotations

import pytest

from src.events.candidate_evaluation import CandidateEvaluation
from src.decision import selection_calibrator as sc
from src.decision import city_skill_gate as csg


def _yes_artifact(*, raw_yes: float = 0.90, hit_rate: float = 0.90):
    bidx, _ = sc.raw_prob_bucket(raw_yes)
    return {
        "_meta": {"posterior_version": sc.DEFAULT_POSTERIOR_VERSION, "min_n": 30},
        "cells": {f"YES|L1|nonmodal|pb{bidx}": {"n": 200, "hit_rate": hit_rate}},
    }


def _base(**over):
    kw = dict(
        candidate_id="c1", family_id="f1", condition_id="cond1", token_id="t1",
        direction="buy_yes", bin_label="16C", execution_price=0.70, q_posterior=0.90,
        q_lcb_5pct=0.83, c_cost_95pct=0.71, p_fill_lcb=0.9, trade_score=0.08, p_value=0.01,
        passed_prefilter=True, native_quote_available=True, kelly_size_usd=10.0,
        same_bin_yes_posterior=0.10,
        selection_calibrator_artifact=_yes_artifact(),
    )
    kw.update(over)
    return CandidateEvaluation(**kw)


def test_new_fields_are_optional_default_none():
    ev = _base()
    assert ev.city is None
    assert ev.admitted is True


def test_selection_calibrator_is_live_without_env_flag(monkeypatch):
    monkeypatch.delenv("ZEUS_SELECTION_CALIBRATOR_LIVE", raising=False)
    monkeypatch.delenv("ZEUS_CITY_SKILL_GATE_LIVE", raising=False)
    ev = _base(city="Karachi")
    assert ev.calibrated_admission_q_lcb <= ev.q_lcb_5pct
    assert sc.selection_calibrator_live_enabled() is True
    assert ev.admitted is True


def test_city_skill_gate_blocks_stable_bad_city_with_explicit_artifact():
    art = {
        "_meta": {"posterior_version": csg.DEFAULT_POSTERIOR_VERSION, "min_track_record": 4, "skill_floor": 0.0},
        "cities": {"Karachi": {"prior_skill": -0.26, "prior_n": 5, "stable_bad": True}},
    }
    ev = _base(city="Karachi", city_skill_artifact=art)
    assert ev.city_skill_block_reason is not None
    assert ev.admitted is False  # blocked by the stable-bad city gate


def test_city_skill_missing_artifact_is_not_in_execution_path():
    ev = _base(city="Karachi", city_skill_artifact=None)
    assert ev.city_skill_block_reason is None
    assert ev.admitted is True


def test_city_skill_gate_allows_good_city_with_explicit_artifact():
    art = {
        "_meta": {"posterior_version": csg.DEFAULT_POSTERIOR_VERSION, "min_track_record": 4, "skill_floor": 0.0},
        "cities": {"Tokyo": {"prior_skill": 0.06, "prior_n": 7, "stable_good": True}},
    }
    ev = _base(city="Tokyo", city_skill_artifact=art)
    assert ev.city_skill_block_reason is None
    assert ev.admitted is True


def test_calibrator_deflates_qlcb_and_blocks_toxic_no():
    # The toxic-NO cell: raw NO prob ~0.875 -> q_posterior(YES-in-bin) ~0.125. Use a candidate whose
    # NO raw prob lands in the toxic bucket.
    raw_no = 0.875
    bidx, _ = sc.raw_prob_bucket(raw_no)
    art = {
        "_meta": {"posterior_version": sc.DEFAULT_POSTERIOR_VERSION, "min_n": 30},
        "cells": {f"NO|L1|nonmodal|pb{bidx}": {"n": 104, "hit_rate": 0.679}},
    }
    # q_posterior is the YES-in-bin belief; NO raw prob = 1 - q_posterior.
    ev = _base(direction="buy_no", q_posterior=1.0 - raw_no, q_lcb_5pct=0.83,
               execution_price=0.70, selection_calibrator_artifact=art)
    # The calibrator-deflated admission q_lcb is below the 0.70 cost -> not admitted.
    assert ev.calibrated_admission_q_lcb < 0.83
    assert ev.calibrated_admission_q_lcb - 0.70 <= 0.0
    assert ev.admitted is False


def test_buy_yes_genuine_edge_survives_live_calibrator(monkeypatch):
    monkeypatch.delenv("ZEUS_SELECTION_CALIBRATOR_LIVE", raising=False)
    raw_yes = 0.45
    bidx, _ = sc.raw_prob_bucket(raw_yes)
    art = {
        "_meta": {"posterior_version": sc.DEFAULT_POSTERIOR_VERSION, "min_n": 30},
        "cells": {f"YES|L1|modal|pb{bidx}": {"n": 200, "hit_rate": 0.46}},
    }
    ev = _base(
        direction="buy_yes",
        q_posterior=raw_yes,
        q_lcb_5pct=0.42,
        execution_price=0.30,
        bin_class="modal",
        selection_calibrator_artifact=art,
    )
    assert ev.calibrated_admission_q_lcb > 0.30
    assert ev.admitted is True
