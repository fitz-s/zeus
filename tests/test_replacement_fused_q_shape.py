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
    BETWEEN_COHORT_STATUS_SIMULTANEOUS_PROVEN,
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
    current_evidence_shape_semantics_mismatch,
)


def test_frozen_scheme_requires_two_current_provider_families() -> None:
    weights = {"ecmwf_ifs": 0.3, "icon_eu": 0.7}

    assert (
        mod._current_provider_family_count(
            configured_weights=weights,
            values_c_by_source={"ecmwf_ifs": 28.0},
        )
        == 1
    )
    assert (
        mod._current_provider_family_count(
            configured_weights=weights,
            values_c_by_source={"ecmwf_ifs": 28.0, "icon_eu": 29.0},
        )
        == 2
    )


def test_same_provider_aliases_do_not_satisfy_current_pair() -> None:
    assert (
        mod._current_provider_family_count(
            configured_weights={"icon_global": 0.5, "icon_eu": 0.5},
            values_c_by_source={"icon_global": 28.0, "icon_eu": 29.0},
        )
        == 1
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
        provider_cycles={
            "ecmwf_ifs": "2026-07-10T12:00:00+00:00",
            "icon_global": "2026-07-10T12:00:00+00:00",
            "ukmo_global": "2026-07-10T12:00:00+00:00",
        },
    )

    assert shape.ensemble_within_sigma_c == pytest.approx(0.32530930629305355)
    assert shape.provider_between_sigma_c == pytest.approx(0.24711098721020064)
    assert shape.ensemble_member_mean_c == pytest.approx(9.49229000315949)
    assert shape.ensemble_center_delta_c == pytest.approx(-1.5281099968405112)
    assert shape.predictive_sigma_c == pytest.approx(1.5817743667175717)
    assert shape.center_sigma_c >= abs(shape.ensemble_center_delta_c)
    assert shape.semantics_revision == CURRENT_EVIDENCE_SEMANTICS_REVISION
    assert shape.as_payload()["semantics_revision"] == CURRENT_EVIDENCE_SEMANTICS_REVISION
    assert shape.between_cohort_status == BETWEEN_COHORT_STATUS_SIMULTANEOUS_PROVEN
    assert shape.as_payload()["between_cohort_status"] == "SIMULTANEOUS_PROVEN"

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
        provider_cycles={
            "ecmwf_ifs": "2026-07-10T12:00:00+00:00",
            "icon_global": "2026-07-10T12:00:00+00:00",
            "ukmo_global": "2026-07-10T12:00:00+00:00",
        },
    )

    assert shape.ensemble_center_delta_c == pytest.approx(0.0, abs=1e-12)
    assert shape.predictive_sigma_c == pytest.approx(0.4085217065969294)


def test_stale_shape_reuse_preserves_raw_members_and_center_disagreement() -> None:
    """A location shift cannot turn conflicting live evidence into certainty."""

    raw = tuple(range(-25, 26))
    scale = 0.6684296539618892 / statistics.pstdev(raw)
    member_mean = 39.067264811197944
    center = 36.934337
    members = tuple(member_mean + value * scale for value in raw)
    between = 0.26824162695413317
    shape = mod._current_evidence_shape_from_values(
        snapshot_id=1224099,
        source_cycle_time="2026-07-25T00:00:00+00:00",
        source_available_at="2026-07-25T08:25:03.905457+00:00",
        members_c=members,
        provider_values_c={"a": center - between, "b": center + between},
        provider_weights={"a": 0.5, "b": 0.5},
        center_c=center,
        carrier_cycle_time="2026-07-25T06:00:00+00:00",
        provider_cycles={
            "a": "2026-07-25T00:00:00+00:00",
            "b": "2026-07-25T00:00:00+00:00",
        },
    )

    raw_delta = member_mean - center
    assert statistics.fmean(shape.members_c) == pytest.approx(member_mean)
    assert shape.translation_applied is False
    assert shape.stale_shape_reused is True
    assert shape.ens_center_delta_raw_c == pytest.approx(-raw_delta)
    assert shape.ensemble_center_delta_c == pytest.approx(raw_delta)
    assert shape.predictive_sigma_c == pytest.approx(
        math.hypot(0.6684296539618892, between, raw_delta)
    )
    assert shape.center_sigma_c >= abs(raw_delta)
    assert (
        shape.semantics_revision
        == STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
    )
    assert shape.between_cohort_status == BETWEEN_COHORT_STATUS_SIMULTANEOUS_PROVEN


def _shape_for_cycle_gate(
    *,
    provider_values_c: dict[str, float],
    provider_weights: dict[str, float],
    provider_cycles: dict[str, str] | None,
):
    return mod._current_evidence_shape_from_values(
        snapshot_id=7,
        source_cycle_time="2026-07-10T00:00:00+00:00",
        source_available_at="2026-07-10T01:00:00+00:00",
        members_c=tuple(range(20)),
        provider_values_c=provider_values_c,
        provider_weights=provider_weights,
        center_c=10.0,
        provider_cycles=provider_cycles,
    )


def test_current_shape_requires_provider_cycle_provenance() -> None:
    with pytest.raises(ValueError, match="complete provider cycle provenance"):
        _shape_for_cycle_gate(
            provider_values_c={"ecmwf_ifs": 10.0, "icon_eu": 11.0},
            provider_weights={"ecmwf_ifs": 0.5, "icon_eu": 0.5},
            provider_cycles=None,
        )


@pytest.mark.parametrize(
    ("provider_values_c", "provider_weights", "provider_cycles", "message"),
    [
        (
            {"ecmwf_ifs": 10.0, "icon_eu": 11.0},
            {"ecmwf_ifs": 0.5, "icon_eu": 0.5},
            {
                "ecmwf_ifs": "2026-07-10T00:00:00+00:00",
                "icon_eu": "2026-07-10T06:00:00+00:00",
            },
            "simultaneous provider families",
        ),
        (
            {"ecmwf_ifs": 10.0, "hko_hk": 11.0},
            {"ecmwf_ifs": 0.5, "hko_hk": 0.5},
            {
                "ecmwf_ifs": "2026-07-10T00:00:00+00:00",
                "hko_hk": "2026-07-10T03:30:00+00:00",
            },
            "simultaneous provider families",
        ),
    ],
)
def test_current_shape_blocks_non_simultaneous_provider_cycles(
    provider_values_c: dict[str, float],
    provider_weights: dict[str, float],
    provider_cycles: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _shape_for_cycle_gate(
            provider_values_c=provider_values_c,
            provider_weights=provider_weights,
            provider_cycles=provider_cycles,
        )


@pytest.mark.parametrize(
    "provider_cycles",
    [
        {"ecmwf_ifs": "2026-07-10T00:00:00+00:00"},
        {
            "ecmwf_ifs": "2026-07-10T00:00:00+00:00",
            "icon_eu": "not-a-cycle",
        },
    ],
)
def test_current_shape_blocks_missing_or_unparseable_provider_cycle(
    provider_cycles: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        _shape_for_cycle_gate(
            provider_values_c={"ecmwf_ifs": 10.0, "icon_eu": 11.0},
            provider_weights={"ecmwf_ifs": 0.5, "icon_eu": 0.5},
            provider_cycles=provider_cycles,
        )


def test_current_shape_blocks_one_provider() -> None:
    with pytest.raises(ValueError, match="at least two weighted providers"):
        _shape_for_cycle_gate(
            provider_values_c={"ecmwf_ifs": 10.0},
            provider_weights={"ecmwf_ifs": 1.0},
            provider_cycles={"ecmwf_ifs": "2026-07-10T00:00:00+00:00"},
        )


def test_current_shape_blocks_one_provider_family_alias() -> None:
    with pytest.raises(ValueError, match="simultaneous provider families"):
        _shape_for_cycle_gate(
            provider_values_c={"icon_global": 10.0, "icon_eu": 11.0},
            provider_weights={"icon_global": 0.5, "icon_eu": 0.5},
            provider_cycles={
                "icon_global": "2026-07-10T00:00:00+00:00",
                "icon_eu": "2026-07-10T00:00:00+00:00",
            },
        )


def test_current_shape_excludes_old_provider_and_records_exact_cohort() -> None:
    shape = _shape_for_cycle_gate(
        provider_values_c={
            "ecmwf_ifs": 10.0,
            "icon_eu": 11.0,
            "ukmo_global": 20.0,
        },
        provider_weights={
            "ecmwf_ifs": 1.0 / 3.0,
            "icon_eu": 1.0 / 3.0,
            "ukmo_global": 1.0 / 3.0,
        },
        provider_cycles={
            "ecmwf_ifs": "2026-07-10T06:00:00+00:00",
            "icon_eu": "2026-07-10T06:00:00+00:00",
            "ukmo_global": "2026-07-10T00:00:00+00:00",
        },
    )

    assert shape.between_cohort_status == "SIMULTANEOUS_PROVEN"
    assert shape.between_cohort_models == ("ecmwf_ifs", "icon_eu")
    assert shape.between_cohort_excluded == ("ukmo_global",)
    assert shape.as_payload()["between_cohort_status"] == "SIMULTANEOUS_PROVEN"
    assert shape.as_payload()["between_cohort_models"] == ("ecmwf_ifs", "icon_eu")
    assert shape.as_payload()["between_cohort_excluded"] == ("ukmo_global",)
    assert shape.provider_between_sigma_c == pytest.approx(math.sqrt(0.5))


def test_old_shape_revisions_are_not_current_authority() -> None:
    assert current_evidence_shape_semantics_mismatch(
        {
            "bayes_precision_fusion": {
                "current_evidence_shape": {
                    "semantics_revision": "ensemble_center_scenarios_v3",
                }
            }
        }
    ) is True
    assert current_evidence_shape_semantics_mismatch(
        {
            "bayes_precision_fusion": {
                "current_evidence_shape": {
                    "semantics_revision": "stale_ensemble_absolute_disagreement_v1",
                    "shape_lag_hours": 6.0,
                    "stale_shape_reused": True,
                }
            }
        }
    ) is True


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
