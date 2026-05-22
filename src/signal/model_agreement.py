"""Model agreement: ECMWF vs GFS conflict detection.

Spec §2.2: JSD-based with argmax modal comparison.
GFS is NEVER blended into probability — conflict detection only.
KL divergence is explicitly forbidden (asymmetric).
"""

import numpy as np
from dataclasses import dataclass, asdict
import json
from scipy.spatial.distance import jensenshannon


# JSD thresholds (spec §2.2)
JSD_AGREE = 0.02
JSD_SOFT_DISAGREE = 0.08

# Mode gap threshold (in bin indices)
MODE_GAP_CONFLICT = 2
PHYSICAL_TEMP_GAP_CONFLICT = 2.0
CANDIDATE_SUPPORT_FLOOR = 0.03
PROBABILITY_SUM_ATOL = 1e-6


@dataclass(frozen=True)
class CrosscheckComparableContext:
    primary_source_id: str
    primary_issue_time: str
    primary_valid_window: tuple[str, str]
    primary_target_local_date: str
    crosscheck_source_id: str
    crosscheck_issue_time: str
    crosscheck_valid_window: tuple[str, str]
    crosscheck_target_local_date: str
    local_day_mapping_equal: bool
    horizon_delta_hours: float | None
    comparable: bool
    non_comparable_reason: str = ""

    def to_detail_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ModelConflictEvidence:
    primary_model: str
    crosscheck_model: str
    primary_source_run_id: str
    crosscheck_source_run_id: str
    issue_time_primary: str
    issue_time_crosscheck: str
    local_day_window_primary: str
    local_day_window_crosscheck: str
    jsd: float
    primary_mode_index: int
    crosscheck_mode_index: int
    mode_gap: int
    primary_mode_label: str
    crosscheck_mode_label: str
    expected_value_primary: float
    expected_value_crosscheck: float
    expected_value_gap_degf: float
    mode_temp_gap_degf: float
    candidate_support_index: int | None
    candidate_crosscheck_probability: float | None
    candidate_supported_by_crosscheck: bool | None
    classification: str
    live_action: str

    def to_detail_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def _validated_probability_vector(name: str, values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=float)
    if vector.ndim != 1:
        raise ValueError(f"{name} probability vector must be 1-dimensional")
    if vector.size == 0:
        raise ValueError(f"{name} probability vector must be non-empty")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} probability vector must contain only finite values")
    if np.any(vector < 0.0):
        raise ValueError(f"{name} probability vector must not contain negative values")
    total = float(vector.sum())
    if total <= 0.0:
        raise ValueError(f"{name} probability vector must have positive probability mass")
    if not np.isclose(total, 1.0, atol=PROBABILITY_SUM_ATOL, rtol=0.0):
        raise ValueError(f"{name} probability vector must sum to 1.0, got {total:.12g}")
    return vector


def _labels_for_vector(bin_labels: list[str] | tuple[str, ...] | None, n: int) -> list[str]:
    if bin_labels is None:
        return [str(i) for i in range(n)]
    labels = [str(label) for label in bin_labels]
    if len(labels) != n:
        raise ValueError(f"bin_labels length mismatch: labels={len(labels)}, vector={n}")
    return labels


def _centers_for_vector(bin_centers: list[float] | tuple[float, ...] | np.ndarray | None, n: int) -> np.ndarray:
    if bin_centers is None:
        return np.arange(n, dtype=float)
    centers = np.asarray(bin_centers, dtype=float)
    if centers.shape != (n,):
        raise ValueError(f"bin_centers length mismatch: centers={len(centers)}, vector={n}")
    if not np.all(np.isfinite(centers)):
        raise ValueError("bin_centers must contain only finite values")
    return centers


def analyze_model_agreement(
    primary_p: np.ndarray,
    crosscheck_p: np.ndarray,
    *,
    primary_model: str = "ECMWF",
    crosscheck_model: str = "GFS",
    bin_labels: list[str] | tuple[str, ...] | None = None,
    bin_centers: list[float] | tuple[float, ...] | np.ndarray | None = None,
    primary_source_run_id: str = "",
    crosscheck_source_run_id: str = "",
    issue_time_primary: str = "",
    issue_time_crosscheck: str = "",
    local_day_window_primary: str = "",
    local_day_window_crosscheck: str = "",
    candidate_support_index: int | None = None,
    candidate_support_floor: float = CANDIDATE_SUPPORT_FLOOR,
) -> ModelConflictEvidence:
    """Return the model-agreement classification with live-money evidence.

    The legacy classifier used bin-index modal distance as a hard-conflict
    proxy. Weather bin topology makes that unsafe: open shoulders, unequal bin
    widths, and flat distributions can move argmax indices without a meaningful
    physical temperature disagreement. This evidence object keeps the old JSD
    signal but requires physical separation before a live hard reject.
    """

    primary = _validated_probability_vector(primary_model, primary_p)
    crosscheck = _validated_probability_vector(crosscheck_model, crosscheck_p)
    if len(primary) != len(crosscheck):
        raise ValueError(
            f"Vector length mismatch: {primary_model}={len(primary)}, {crosscheck_model}={len(crosscheck)}"
        )

    labels = _labels_for_vector(bin_labels, len(primary))
    centers = _centers_for_vector(bin_centers, len(primary))
    jsd = compute_jsd(primary, crosscheck)
    primary_mode = int(np.argmax(primary))
    crosscheck_mode = int(np.argmax(crosscheck))
    mode_gap = abs(primary_mode - crosscheck_mode)
    expected_primary = float(np.dot(primary, centers))
    expected_crosscheck = float(np.dot(crosscheck, centers))
    expected_gap = abs(expected_primary - expected_crosscheck)
    mode_temp_gap = abs(float(centers[primary_mode]) - float(centers[crosscheck_mode]))

    candidate_crosscheck_probability: float | None = None
    candidate_supported_by_crosscheck: bool | None = None
    if candidate_support_index is not None:
        if candidate_support_index < 0 or candidate_support_index >= len(crosscheck):
            raise ValueError(
                f"candidate_support_index out of range: {candidate_support_index}"
            )
        candidate_crosscheck_probability = float(crosscheck[candidate_support_index])
        candidate_supported_by_crosscheck = (
            candidate_crosscheck_probability >= float(candidate_support_floor)
        )

    if jsd < JSD_AGREE and mode_gap <= 1:
        classification = "AGREE"
        live_action = "allow"
    elif jsd < JSD_SOFT_DISAGREE or mode_gap <= 1:
        classification = "SOFT_DISAGREE"
        live_action = "haircut"
    else:
        physically_separated = (
            expected_gap >= PHYSICAL_TEMP_GAP_CONFLICT
            or mode_temp_gap >= PHYSICAL_TEMP_GAP_CONFLICT
        )
        candidate_not_supported = (
            candidate_supported_by_crosscheck is False
            if candidate_supported_by_crosscheck is not None
            else True
        )
        if physically_separated and candidate_not_supported:
            classification = "CONFLICT"
            live_action = "reject"
        else:
            classification = "SOFT_DISAGREE"
            live_action = "haircut"

    return ModelConflictEvidence(
        primary_model=primary_model,
        crosscheck_model=crosscheck_model,
        primary_source_run_id=primary_source_run_id,
        crosscheck_source_run_id=crosscheck_source_run_id,
        issue_time_primary=issue_time_primary,
        issue_time_crosscheck=issue_time_crosscheck,
        local_day_window_primary=local_day_window_primary,
        local_day_window_crosscheck=local_day_window_crosscheck,
        jsd=float(jsd),
        primary_mode_index=primary_mode,
        crosscheck_mode_index=crosscheck_mode,
        mode_gap=mode_gap,
        primary_mode_label=labels[primary_mode],
        crosscheck_mode_label=labels[crosscheck_mode],
        expected_value_primary=expected_primary,
        expected_value_crosscheck=expected_crosscheck,
        expected_value_gap_degf=expected_gap,
        mode_temp_gap_degf=mode_temp_gap,
        candidate_support_index=candidate_support_index,
        candidate_crosscheck_probability=candidate_crosscheck_probability,
        candidate_supported_by_crosscheck=candidate_supported_by_crosscheck,
        classification=classification,
        live_action=live_action,
    )


def model_agreement(ecmwf_p: np.ndarray, gfs_p: np.ndarray) -> str:
    """Classify agreement level between ECMWF and GFS probability vectors.

    Args:
        ecmwf_p: ECMWF P_raw vector, shape (n_bins,), sums to 1.0
        gfs_p: GFS P_raw vector, shape (n_bins,), sums to 1.0

    Returns:
        "AGREE" — proceed normally
        "SOFT_DISAGREE" — widen CI, raise edge threshold
        "CONFLICT" — skip market entirely
    """
    return analyze_model_agreement(ecmwf_p, gfs_p).classification


def compute_jsd(p: np.ndarray, q: np.ndarray) -> float:
    """Compute Jensen-Shannon Divergence between two probability vectors.

    Returns the true JSD (not sqrt). Range [0, ln(2)] ≈ [0, 0.693].
    """
    p = _validated_probability_vector("p", p)
    q = _validated_probability_vector("q", q)
    if len(p) != len(q):
        raise ValueError(f"Vector length mismatch: p={len(p)}, q={len(q)}")
    return float(jensenshannon(p, q) ** 2)
