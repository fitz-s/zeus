# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: per-city historical settlement-skill gate
#   (team-lead approved (a) 2026-06-22; live_order_pathology 2026-06-22). Pairs with the
#   selection-aware q_lcb calibrator: the skill gate decides WHICH cities to trade (where our
#   forecast reliably beats the market on prior settled days), the calibrator blocks the
#   adversely-selected toxic tail WITHIN them.
"""RED-first tests for the per-city historical settlement-skill gate (runtime serving rule).

The gate's pre-trade signal is each city's PRIOR settlement-skill track record (Brier-vs-market on
that city's rows settled strictly before the decision time T). The gate ADMITS a city only when its
prior skill is reliably positive: skill > a LEARNED threshold AND track-record length >= a LEARNED
minimum (so the noisy-middle — which flips edge sign at n=91 — ABSTAINS until it has enough history;
reliably-bad cities like Karachi/Houston/Shanghai are BLOCKED by their negative prior skill). No
hard-coded cap; the threshold + min-track-record are learned walk-forward.

Tested here (the artifact-fitting no-leak contract is in tests/decision/test_city_skill_gate_fit.py):
  * ADMIT a reliably-skilled city (deep positive prior skill).
  * BLOCK a reliably-bad city (deep negative prior skill).
  * ABSTAIN the noisy-middle (track-record below the learned minimum) — fail-closed, no new entry.
  * FAIL-CLOSED on absent / malformed / stale artifact or a city absent from the artifact.
  * No price/edge anchoring: the gate decides admit/abstain from skill ONLY (it is a city selector,
    not a probability authority — it never alters q).
"""
from __future__ import annotations

import pytest

from src.decision import city_skill_gate as g


def _artifact(cities: dict, *, min_track: int = 4, skill_floor: float = 0.0,
              version: str = "openmeteo_ecmwf_ifs9_bayes_fusion") -> dict:
    return {
        "_meta": {
            "authority": "city_skill_gate_v1_walkforward",
            "posterior_version": version,
            "min_track_record": min_track,
            "skill_floor": skill_floor,
            "max_target_date": "2099-01-01",
        },
        "cities": cities,
    }


def test_admit_reliably_skilled_city():
    art = _artifact({"Tokyo": {"prior_skill": 0.06, "prior_n": 7}}, min_track=4, skill_floor=0.0)
    v = g.apply_city_skill_gate(city="Tokyo", artifact=art)
    assert v.admit is True
    assert v.abstained is False
    assert v.basis == "CITY_SKILL_ADMIT"


def test_block_reliably_bad_city():
    art = _artifact({"Karachi": {"prior_skill": -0.26, "prior_n": 8}}, min_track=4, skill_floor=0.0)
    v = g.apply_city_skill_gate(city="Karachi", artifact=art)
    assert v.admit is False
    assert v.abstained is True
    assert v.basis == "CITY_SKILL_BLOCKED_NEGATIVE"


def test_abstain_noisy_middle_below_min_track():
    # A city with positive prior skill but too SHORT a track record (below the learned minimum)
    # ABSTAINS — it has not earned a license yet.
    art = _artifact({"Warsaw": {"prior_skill": 0.03, "prior_n": 2}}, min_track=4, skill_floor=0.0)
    v = g.apply_city_skill_gate(city="Warsaw", artifact=art)
    assert v.admit is False
    assert v.abstained is True
    assert v.basis == "CITY_SKILL_THIN_TRACK"


def test_abstain_below_skill_floor():
    # Positive but below the learned floor -> abstain (not reliably skilled enough).
    art = _artifact({"HongKong": {"prior_skill": 0.005, "prior_n": 10}}, min_track=4, skill_floor=0.02)
    v = g.apply_city_skill_gate(city="HongKong", artifact=art)
    assert v.admit is False
    assert v.abstained is True
    assert v.basis == "CITY_SKILL_BELOW_FLOOR"


def test_fail_closed_absent_artifact():
    v = g.apply_city_skill_gate(city="Tokyo", artifact=None)
    assert v.admit is False and v.abstained is True
    assert v.basis == "FAIL_CLOSED_NO_ARTIFACT"


def test_fail_closed_city_absent_from_artifact():
    art = _artifact({"Tokyo": {"prior_skill": 0.06, "prior_n": 7}})
    v = g.apply_city_skill_gate(city="Beijing", artifact=art)
    assert v.admit is False and v.abstained is True
    assert v.basis == "CITY_SKILL_UNKNOWN_CITY"


def test_fail_closed_stale_version():
    art = _artifact({"Tokyo": {"prior_skill": 0.06, "prior_n": 7}}, version="SOME_OLD")
    v = g.apply_city_skill_gate(
        city="Tokyo", artifact=art, expected_posterior_version="openmeteo_ecmwf_ifs9_bayes_fusion"
    )
    assert v.admit is False and v.abstained is True
    assert v.basis == "FAIL_CLOSED_STALE_VERSION"


def test_fail_closed_malformed_cell():
    art = _artifact({"Tokyo": {"prior_skill": "nope", "prior_n": 7}})
    v = g.apply_city_skill_gate(city="Tokyo", artifact=art)
    assert v.admit is False and v.abstained is True
    assert v.basis == "FAIL_CLOSED_MALFORMED"


def test_seam_helper_default_off_is_noop(monkeypatch):
    monkeypatch.delenv("ZEUS_CITY_SKILL_GATE_LIVE", raising=False)
    # Flag OFF -> the seam helper licenses everything (no-op; wiring it in changes nothing live).
    assert g.city_skill_gate_admits(city="Karachi", artifact=_artifact({"Karachi": {"prior_skill": -0.3, "prior_n": 9}})) is True


def test_seam_helper_live_blocks_bad_city(monkeypatch):
    monkeypatch.setenv("ZEUS_CITY_SKILL_GATE_LIVE", "1")
    art = _artifact({"Karachi": {"prior_skill": -0.3, "prior_n": 9}})
    assert g.city_skill_gate_admits(city="Karachi", artifact=art) is False
    art2 = _artifact({"Tokyo": {"prior_skill": 0.06, "prior_n": 7}})
    assert g.city_skill_gate_admits(city="Tokyo", artifact=art2) is True


# --------------------------------------------------------------------------------------------------
# Both-halves-confirmed BLOCK (loss-reduction mode): only block a TEMPORALLY-STABLE loser.
# --------------------------------------------------------------------------------------------------

def test_block_only_confirmed_stable_bad_city():
    # Karachi negative in BOTH halves -> stable_bad=True -> blocked.
    art = _artifact({"Karachi": {"prior_skill": -0.26, "prior_n": 5, "stable_bad": True}})
    v = g.apply_city_skill_gate(city="Karachi", artifact=art, require_stable_bad_to_block=True)
    assert v.admit is False and v.abstained is True
    assert v.basis == "CITY_SKILL_BLOCKED_STABLE_BAD"


def test_aggregate_negative_but_not_confirmed_stable_is_not_hard_blocked():
    # A city negative in aggregate but NOT confirmed negative-both-halves (stable_bad False/absent):
    # in block-only loss-reduction mode it is NOT a confirmed stable loser. It still does not ADMIT
    # (negative skill), but its basis marks it UNCONFIRMED so the loss-reduction gate does not list it.
    art = _artifact({"Houston": {"prior_skill": -0.4, "prior_n": 3, "stable_bad": False}})
    v = g.apply_city_skill_gate(city="Houston", artifact=art, require_stable_bad_to_block=True)
    assert v.admit is False  # negative skill still never admits
    assert v.basis == "CITY_SKILL_NEGATIVE_UNCONFIRMED"


def test_stable_good_city_never_blocked_in_loss_reduction_mode():
    # Tokyo/London are stable-good -> must NEVER be blocked. (With require_stable_bad_to_block they
    # admit normally since positive skill + track record.)
    art = _artifact({"Tokyo": {"prior_skill": 0.06, "prior_n": 7, "stable_bad": False, "stable_good": True}})
    v = g.apply_city_skill_gate(city="Tokyo", artifact=art, require_stable_bad_to_block=True)
    assert v.admit is True and v.basis == "CITY_SKILL_ADMIT"


def test_blocked_cities_helper_lists_only_confirmed_stable_bad():
    art = _artifact({
        "Karachi": {"prior_skill": -0.26, "prior_n": 5, "stable_bad": True},
        "Houston": {"prior_skill": -0.4, "prior_n": 3, "stable_bad": False},
        "Tokyo": {"prior_skill": 0.06, "prior_n": 7, "stable_good": True},
    })
    blocked = g.confirmed_blocked_cities(artifact=art)
    assert blocked == ["Karachi"]
