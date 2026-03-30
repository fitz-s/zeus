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

logger = logging.getLogger(__name__)


# Clamp range for logit transform — prevents log(0) and log(inf)
P_CLAMP_LOW = 0.01
P_CLAMP_HIGH = 0.99

# Default bootstrap iterations for parameter uncertainty
DEFAULT_N_BOOTSTRAP = 200


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

    def fit(
        self,
        p_raw: np.ndarray,
        lead_days: np.ndarray,
        outcomes: np.ndarray,
        n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
        regularization_C: float = 1.0,
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
            n_bootstrap: number of bootstrap parameter sets
            regularization_C: sklearn LogisticRegression C parameter
        """
        if len(p_raw) < 15:
            raise ValueError(
                f"Cannot fit Platt with n={len(p_raw)} < 15. "
                f"Per spec §3.3: use P_raw directly for n < 15."
            )

        X = self._build_features(p_raw, lead_days)

        # Primary fit
        lr = self._fit_lr(X, outcomes, regularization_C)
        self.A = float(lr.coef_[0][0])
        self.B = float(lr.coef_[0][1])
        self.C = float(lr.intercept_[0])
        self.n_samples = len(outcomes)
        self.fitted = True

        # Bootstrap parameter uncertainty (spec §3.1)
        self.bootstrap_params = []
        rng = np.random.default_rng()
        for _ in range(n_bootstrap):
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

        p_clamped = np.clip(p_raw, P_CLAMP_LOW, P_CLAMP_HIGH)
        logit = np.log(p_clamped / (1 - p_clamped))
        z = self.A * logit + self.B * lead_days + self.C
        p_cal = 1.0 / (1.0 + np.exp(-z))

        return float(np.clip(p_cal, 0.001, 0.999))

    @staticmethod
    def _build_features(p_raw: np.ndarray, lead_days: np.ndarray) -> np.ndarray:
        """Build feature matrix: [logit(P_raw), lead_days]."""
        p_clamped = np.clip(p_raw, P_CLAMP_LOW, P_CLAMP_HIGH)
        logits = np.log(p_clamped / (1 - p_clamped))
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
) -> np.ndarray:
    """Calibrate all bins and re-normalize to sum=1.0.

    Spec §3.5: Platt is trained per-bin, not jointly. After independent
    calibration, the vector may not sum to 1.0. Enforce the constraint.

    Returns: np.ndarray shape (n_bins,), sums to 1.0
    """
    p_cal = np.array([
        calibrator.predict(float(p), lead_days) for p in p_raw_vector
    ])

    total = p_cal.sum()
    if total > 0:
        p_cal = p_cal / total

    return p_cal
