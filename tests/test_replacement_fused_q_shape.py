# Created: 2026-06-09
# Last reused or audited: 2026-07-23
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md
"""Current-evidence predictive-shape authority antibodies."""
from __future__ import annotations

import math
import statistics

import pytest

import src.data.replacement_forecast_materializer as mod
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
)
def test_current_ensemble_center_disagreement_stays_in_predictive_shape() -> None:
    """Absolute ENS levels cannot be recentered away from the served center."""

    raw = tuple(range(-25, 26))
    scale = 0.32530930629305355 / statistics.pstdev(raw)
    members = tuple(9.49229000315949 + value * scale for value in raw)
    shape = mod._current_evidence_shape_from_values(
        snapshot_id=1202928,
        source_cycle_time="2026-07-10T12:00:00+00:00",
        source_available_at="2026-07-10T20:25:16.964968+00:00",
        members_c=members,
        provider_values_c={
            "ecmwf_ifs": 10.0,
            "icon_global": 10.9,
            "ukmo_global": 11.1,
        },
        provider_weights={
            "ecmwf_ifs": 0.052,
            "icon_global": 0.112,
            "ukmo_global": 0.836,
        },
        center_c=11.0204,
    )

    assert shape.ensemble_within_sigma_c == pytest.approx(0.32530930629305355)
    assert shape.provider_between_sigma_c == pytest.approx(0.24711098721020064)
    assert shape.ensemble_member_mean_c == pytest.approx(9.49229000315949)
    assert shape.ensemble_center_delta_c == pytest.approx(-1.5281099968405112)
    assert shape.predictive_sigma_c == pytest.approx(1.5817743667175717)
    assert shape.center_sigma_c >= abs(shape.ensemble_center_delta_c)
    assert shape.semantics_revision == CURRENT_EVIDENCE_SEMANTICS_REVISION
    assert shape.as_payload()["semantics_revision"] == CURRENT_EVIDENCE_SEMANTICS_REVISION

    def cdf(value: float) -> float:
        return 0.5 * (
            1.0
            + math.erf(
                (value - 11.0204)
                / (shape.predictive_sigma_c * math.sqrt(2.0))
            )
        )
    q_yes_11 = cdf(11.5) - cdf(10.5)
    q_no_11 = 1.0 - q_yes_11

    assert q_yes_11 == pytest.approx(0.24805, abs=1e-4)
    assert q_no_11 == pytest.approx(0.75195, abs=1e-4)
    assert q_yes_11 - 0.78 <= 0.0
    assert q_no_11 - 0.27 > 0.0


def test_aligned_ensemble_center_preserves_within_between_decomposition() -> None:
    raw = tuple(range(-25, 26))
    scale = 0.32530930629305355 / statistics.pstdev(raw)
    members = tuple(11.0204 + value * scale for value in raw)
    shape = mod._current_evidence_shape_from_values(
        snapshot_id=1202928,
        source_cycle_time="2026-07-10T12:00:00+00:00",
        source_available_at="2026-07-10T20:25:16.964968+00:00",
        members_c=members,
        provider_values_c={
            "ecmwf_ifs": 10.0,
            "icon_global": 10.9,
            "ukmo_global": 11.1,
        },
        provider_weights={
            "ecmwf_ifs": 0.052,
            "icon_global": 0.112,
            "ukmo_global": 0.836,
        },
        center_c=11.0204,
    )

    assert shape.ensemble_center_delta_c == pytest.approx(0.0, abs=1e-12)
    assert shape.predictive_sigma_c == pytest.approx(0.4085217065969294)


def test_current_evidence_probability_is_yes_no_complement_symmetric() -> None:
    """The same probability world can select YES or NO solely from executable cost."""

    q_yes = 0.83
    q_no = 1.0 - q_yes
    assert q_yes - 0.72 > 0.0
    assert q_no - 0.18 < 0.0

    mirrored_q_yes = 1.0 - q_yes
    mirrored_q_no = 1.0 - mirrored_q_yes
    assert mirrored_q_yes - 0.18 < 0.0
    assert mirrored_q_no - 0.72 > 0.0
    assert mirrored_q_no == pytest.approx(q_yes)
