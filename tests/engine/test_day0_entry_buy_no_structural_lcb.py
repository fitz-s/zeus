# Created: 2026-06-17
# Last reused/audited: 2026-06-25
# Authority basis: operator Day0 LOW runtime opportunity capture fix; deterministic absorbing
#   observation facts must enter EDLI probability/proof authority without forecast expected-member gates.
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.calibration.qlcb_provenance import _qlcb_float
from src.engine.event_reactor_adapter import (
    _apply_day0_mask_to_generated_probabilities,
)


def _candidate(condition_id: str, low: float | None, high: float | None):
    return SimpleNamespace(
        condition_id=condition_id,
        bin=SimpleNamespace(low=low, high=high),
    )


def _family(*candidates):
    return SimpleNamespace(city="Tokyo", candidates=list(candidates))


def test_low_day0_dead_yes_bin_licenses_structural_buy_no() -> None:
    family = _family(
        _candidate("low20", 20.0, 20.0),
        _candidate("low21", 21.0, 21.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={"metric": "low", "rounded_value": 20.0},
        family=family,
        q_by_condition={"low20": 0.4, "low21": 0.6},
        lcb_by_condition={
            ("low20", "buy_yes"): 0.3,
            ("low20", "buy_no"): 0.0,
            ("low21", "buy_yes"): 0.2,
            ("low21", "buy_no"): 0.0,
        },
    )

    assert q["low21"] == 0.0
    assert _qlcb_float(lcb[("low21", "buy_yes")]) == 0.0
    assert _qlcb_float(lcb[("low21", "buy_no")]) == pytest.approx(1.0)


def test_low_day0_current_record_bin_stays_unresolved_for_buy_no() -> None:
    family = _family(
        _candidate("low19", 19.0, 19.0),
        _candidate("low20", 20.0, 20.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={"metric": "low", "rounded_value": 20.0},
        family=family,
        q_by_condition={"low19": 0.25, "low20": 0.75},
        lcb_by_condition={
            ("low19", "buy_yes"): 0.1,
            ("low19", "buy_no"): 0.0,
            ("low20", "buy_yes"): 0.5,
            ("low20", "buy_no"): 0.0,
        },
    )

    assert q["low20"] > 0.0
    assert _qlcb_float(lcb[("low20", "buy_no")]) == 0.0


def test_low_day0_nonabsorbing_bin_carries_remaining_day_buy_no_lcb() -> None:
    family = _family(
        _candidate("low20", 20.0, 20.0),
        _candidate("low19", 19.0, 19.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={
            "metric": "low",
            "rounded_value": 20.0,
            "_edli_q_source": "day0_remaining_day",
        },
        family=family,
        q_by_condition={"low20": 0.20, "low19": 0.80},
        lcb_by_condition={
            ("low20", "buy_yes"): 0.10,
            ("low20", "buy_no"): 0.75,
            ("low19", "buy_yes"): 0.70,
            ("low19", "buy_no"): 0.15,
        },
    )

    assert q["low20"] == pytest.approx(0.20)
    assert _qlcb_float(lcb[("low20", "buy_no")]) == pytest.approx(0.75)


def test_high_day0_nonabsorbing_bin_carries_remaining_day_buy_no_lcb() -> None:
    family = _family(
        _candidate("high31", 31.0, 31.0),
        _candidate("high32", 32.0, 32.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={
            "metric": "high",
            "rounded_value": 29.0,
            "_edli_q_source": "day0_remaining_day",
        },
        family=family,
        q_by_condition={"high31": 0.20, "high32": 0.80},
        lcb_by_condition={
            ("high31", "buy_yes"): 0.10,
            ("high31", "buy_no"): 0.75,
            ("high32", "buy_yes"): 0.70,
            ("high32", "buy_no"): 0.15,
        },
    )

    assert q["high31"] == pytest.approx(0.20)
    assert _qlcb_float(lcb[("high31", "buy_no")]) == pytest.approx(0.75)


def test_high_day0_dead_yes_bin_licenses_structural_buy_no() -> None:
    family = _family(
        _candidate("high29", 29.0, 29.0),
        _candidate("high30", 30.0, 30.0),
    )

    q, lcb = _apply_day0_mask_to_generated_probabilities(
        payload={"metric": "high", "rounded_value": 30.0},
        family=family,
        q_by_condition={"high29": 0.4, "high30": 0.6},
        lcb_by_condition={
            ("high29", "buy_yes"): 0.2,
            ("high29", "buy_no"): 0.0,
            ("high30", "buy_yes"): 0.4,
            ("high30", "buy_no"): 0.0,
        },
    )

    assert q["high29"] == 0.0
    assert _qlcb_float(lcb[("high29", "buy_yes")]) == 0.0
    assert _qlcb_float(lcb[("high29", "buy_no")]) == pytest.approx(1.0)
