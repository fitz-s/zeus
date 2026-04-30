"""Extended Platt Scaling: P_cal = sigmoid(A * logit(P_raw) + B * lead_days + C).

Spec §3.1-3.2: Three-parameter logistic calibration with lead_days as input feature.
Lead_days is NOT a bucket dimension — it's a Platt input. This triples positive
samples per bucket (45→135) vs the 72-bucket approach.

Bootstrap: 200 parameter sets (A_i, B_i, C_i) for σ_parameter in double-bootstrap CI.
Without bootstrap_params, edge CI is systematically too narrow → overtrading.
"""

import logging

import numpy as np
from sklearn.linear_model import LogisticRegression

from src.config import calibration_n_bootstrap

logger = logging.getLogger(__name__)


# Clamp range for logit transform — prevents log(0) and log(inf)
P_CLAMP_LOW = 0.01
P_CLAMP_HIGH = 0.99

# Compatibility alias for tests and assumption audits.
DEFAULT_N_BOOTSTRAP = calibration_n_bootstrap()
RAW_PROBABILITY_SPACE = "raw_probability"
WIDTH_NORMALIZED_SPACE = "width_normalized_density"


def _finite_positive_width(bin_width: float | None) -> float | None:
    if bin_width is None:
        return None
    try:
        width = float(bin_width)
    except (TypeError, ValueError):
        raise ValueError(
            "bin_width must be finite and >0, or None only for known open-shoulder bins"
        )
    if not np.isfinite(width) or width <= 0:
        raise ValueError(
            "bin_width must be finite and >0, or None only for known open-shoulder bins"
        )
    return width


def normalize_bin_probability_for_calibration(
    p_raw: float,
    *,
    bin_width: float | None = None,
) -> float:
    """Map a bin probability into the Platt input space.

    Finite bins are normalized by their settlement width so Platt sees an
    approximate per-degree density rather than raw probability mass.

    Shoulder bins remain in raw probability space because they are open-ended
    tail events and do not have a finite width. ``bin_width=None`` is reserved
    for those known shoulders; corrupt finite widths must fail closed.
    """
    width = _finite_positive_width(bin_width)
    if width is None:
        return float(p_raw)
    return float(p_raw) / width


def logit_safe(p: float | np.ndarray, eps: float = P_CLAMP_LOW):
    """Finite logit transform with symmetric clipping."""
    p_clamped = np.clip(p, eps, 1.0 - eps)
    return np.log(p_clamped / (1.0 - p_clamped))


class ExtendedPlattCalibrator:
    """Platt calibrator with lead_days as second input feature.

    Spec §3.2: P_cal = sigmoid(A * logit(P_raw) + B * lead_days + C)
    """

    def __init__(self):
        self.A: float = 0.0
        self.B: float = 0.0
        self.C: float = 0.0
        self.n_samples: int = 0
        self.fitted: bool = False
        self.bootstrap_params: list[tuple[float, float, float]] = []
        self.input_space: str = RAW_PROBABILITY_SPACE

    def fit(
        self,
        p_raw: np.ndarray,
        lead_days: np.ndarray,
        outcomes: np.ndarray,
        bin_widths: np.ndarray | None = None,
        decision_group_ids: np.ndarray | None = None,
        n_bootstrap: int | None = None,
        regularization_C: float = 1.0,
        rng: np.random.Generator | None = None,
    ) -> None:
        """Fit Platt model on (p_raw, lead_days, outcome) triples.

        Spec §3.3 maturity gate controls regularization_C:
        - n >= 50: C=1.0 (standard)
        - 15 <= n < 50: C=0.1 (strong regularization)
        - n < 15: don't call fit() — use P_raw directly

        Args:
            p_raw: raw probabilities, shape (n,)
            lead_days: forecast lead in days, shape (n,)
            outcomes: binary outcomes (0/1), shape (n,)
            bin_widths: optional finite bin widths for width-aware normalization
            n_bootstrap: number of bootstrap parameter sets
            regularization_C: sklearn LogisticRegression C parameter
        """
        if n_bootstrap is None:
            n_bootstrap = calibration_n_bootstrap()
        if len(p_raw) < 15:
            raise ValueError(
                f"Cannot fit Platt with n={len(p_raw)} < 15. "
                f"Per spec §3.3: use P_raw directly for n < 15."
            )
        if rng is None:
            rng = np.random.default_rng()
        group_ids = None
        unique_groups = None
        if decision_group_ids is not None:
            group_ids = np.asarray(decision_group_ids, dtype=object)
            if len(group_ids) != len(outcomes):
                raise ValueError(
                    "decision_group_ids must have same length as outcomes: "
                    f"{len(group_ids)} != {len(outcomes)}"
                )
            if any(group_id is None or str(group_id) == "" for group_id in group_ids):
                raise ValueError("decision_group_ids must not contain null/empty values")
            unique_groups = np.array(sorted({str(group_id) for group_id in group_ids}), dtype=object)
            if len(unique_groups) < 15:
                raise ValueError(
                    f"Cannot fit Platt with n_eff={len(unique_groups)} < 15. "
                    f"Per spec §3.3: use P_raw directly for n_eff < 15."
                )

        X = self._build_features(p_raw, lead_days, bin_widths=bin_widths)
        self.input_space = (
            WIDTH_NORMALIZED_SPACE if bin_widths is not None else RAW_PROBABILITY_SPACE
        )

        # Primary fit
        lr = self._fit_lr(X, outcomes, regularization_C)
        self.A = float(lr.coef_[0][0])
        self.B = float(lr.coef_[0][1])
        self.C = float(lr.intercept_[0])
        self.n_samples = len(unique_groups) if unique_groups is not None else len(outcomes)
        self.fitted = True

        # Bootstrap parameter uncertainty (spec §3.1)
        self.bootstrap_params = []
        for _ in range(n_bootstrap):
            if group_ids is not None and unique_groups is not None:
                sampled_groups = rng.choice(unique_groups, len(unique_groups), replace=True)
                idx = np.concatenate([
                    np.flatnonzero(group_ids == group_id)
                    for group_id in sampled_groups
                ])
            else:
                idx = rng.choice(len(outcomes), len(outcomes), replace=True)
            try:
                lr_b = self._fit_lr(X[idx], outcomes[idx], regularization_C)
                self.bootstrap_params.append((
                    float(lr_b.coef_[0][0]),
                    float(lr_b.coef_[0][1]),
                    float(lr_b.intercept_[0]),
                ))
            except Exception as e:
                # Bootstrap sample may be degenerate (all same class).
                # Skip this iteration — we'll have < n_bootstrap params.
                logger.warning("Bootstrap iteration skipped (degenerate sample): %s", e)
                continue

    def predict(self, p_raw: float, lead_days: float) -> float:
        """Calibrate a single P_raw value.

        Spec §3.2: P_cal = sigmoid(A * logit(P_raw) + B * lead_days + C)

        Returns: calibrated probability in [0.001, 0.999].
        Per CLAUDE.md: clamp output outside [0.001, 0.999] + log.
        """
        if not self.fitted:
            raise RuntimeError("Calibrator not fitted. Call fit() first.")

        logit = logit_safe(p_raw)
        z = self.A * logit + self.B * lead_days + self.C
        p_cal = 1.0 / (1.0 + np.exp(-z))

        return float(np.clip(p_cal, 0.001, 0.999))

    def predict_for_bin(
        self,
        p_raw: float,
        lead_days: float,
        *,
        bin_width: float | None = None,
    ) -> float:
        """Calibrate a bin probability in the same input space used at fit time."""
        if self.input_space == WIDTH_NORMALIZED_SPACE:
            p_raw = normalize_bin_probability_for_calibration(p_raw, bin_width=bin_width)
        return self.predict(p_raw, lead_days)

    @staticmethod
    def _build_features(
        p_raw: np.ndarray,
        lead_days: np.ndarray,
        *,
        bin_widths: np.ndarray | None = None,
    ) -> np.ndarray:
        """Build feature matrix: [logit(P_raw), lead_days]."""
        if bin_widths is not None:
            p_raw = np.array([
                normalize_bin_probability_for_calibration(float(p), bin_width=w)
                for p, w in zip(p_raw, bin_widths)
            ], dtype=np.float64)
        logits = logit_safe(p_raw)
        return np.column_stack([logits, lead_days])

    @staticmethod
    def _fit_lr(
        X: np.ndarray, y: np.ndarray, C: float
    ) -> LogisticRegression:
        lr = LogisticRegression(C=C, solver="lbfgs", max_iter=1000)
        lr.fit(X, y)
        return lr


def calibrate_and_normalize(
    p_raw_vector: np.ndarray,
    calibrator: ExtendedPlattCalibrator,
    lead_days: float,
    bin_widths: np.ndarray | list[float | None] | None = None,
) -> np.ndarray:
    """Calibrate all bins and re-normalize to sum=1.0.

    Spec §3.5: Platt is trained per-bin, not jointly. After independent
    calibration, the vector may not sum to 1.0. Enforce the constraint.

    Returns: np.ndarray shape (n_bins,), sums to 1.0
    """
    widths = list(bin_widths) if bin_widths is not None else [None] * len(p_raw_vector)
    p_cal = np.array([
        calibrator.predict_for_bin(float(p), lead_days, bin_width=widths[i])
        for i, p in enumerate(p_raw_vector)
    ])

    total = p_cal.sum()
    if total > 0:
        p_cal = p_cal / total

    return p_cal
