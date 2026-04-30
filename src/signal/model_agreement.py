"""Model agreement: ECMWF vs GFS conflict detection.

Spec §2.2: JSD-based with argmax modal comparison.
GFS is NEVER blended into probability — conflict detection only.
KL divergence is explicitly forbidden (asymmetric).
"""

import numpy as np
from scipy.spatial.distance import jensenshannon


# JSD thresholds (spec §2.2)
JSD_AGREE = 0.02
JSD_SOFT_DISAGREE = 0.08

# Mode gap threshold (in bin indices)
MODE_GAP_CONFLICT = 2
PROBABILITY_SUM_ATOL = 1e-6


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
    ecmwf_p = _validated_probability_vector("ECMWF", ecmwf_p)
    gfs_p = _validated_probability_vector("GFS", gfs_p)

    if len(ecmwf_p) != len(gfs_p):
        raise ValueError(
            f"Vector length mismatch: ECMWF={len(ecmwf_p)}, GFS={len(gfs_p)}"
        )

    # scipy.jensenshannon returns sqrt(JSD); square to recover true JSD
    jsd = jensenshannon(ecmwf_p, gfs_p) ** 2

    ecmwf_mode = int(np.argmax(ecmwf_p))
    gfs_mode = int(np.argmax(gfs_p))
    mode_gap = abs(ecmwf_mode - gfs_mode)

    if jsd < JSD_AGREE and mode_gap <= 1:
        return "AGREE"
    elif jsd < JSD_SOFT_DISAGREE or mode_gap <= 1:
        return "SOFT_DISAGREE"
    else:
        return "CONFLICT"


def compute_jsd(p: np.ndarray, q: np.ndarray) -> float:
    """Compute Jensen-Shannon Divergence between two probability vectors.

    Returns the true JSD (not sqrt). Range [0, ln(2)] ≈ [0, 0.693].
    """
    p = _validated_probability_vector("p", p)
    q = _validated_probability_vector("q", q)
    if len(p) != len(q):
        raise ValueError(f"Vector length mismatch: p={len(p)}, q={len(q)}")
    return float(jensenshannon(p, q) ** 2)
