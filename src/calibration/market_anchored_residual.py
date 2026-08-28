# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator") + "Key consult corrections
#   adopted": no per-bucket isotonic, no per-city params, lead gets 3 regularized
#   intercepts, parity with market never unlocks Kelly (beta=0 => market prob).
"""Market-anchored walk-forward residual calibrator.

    logit(r_hat) = logit(p0) + alpha_lead[lead_bucket]
                   + beta * clip(logit(q_raw) - logit(p0), -D, D)

p0 is the decision-time market side price. For HISTORICAL evaluation this is
``settlement_attribution.market_in_bin_prob`` — the in-bin market probability
derived from our own fill price. It is a proxy for true decision-time
top-of-book until item 3 (decision certificate: explicit p0 provenance)
lands; anything computed from it inherits that proxy's noise (fill price is
observed AFTER the decision, and only for our own side).

alpha_lead is one regularized intercept per lead bucket (day0/day1/day2,
lead = target_date - decision_date in days; an unseen lead value fails
closed to no calibrated output rather than guessing). beta is ONE global
residual-information coefficient shared across leads and cities (per-city
params are explicitly rejected by the plan: city is a robustness veto, not
a calibration axis).

An L2 penalty pulls alpha_lead and beta toward zero — beta=0 means "q_raw
carries no information beyond the market price" and the calibrator degrades
to the market price itself (plus a small per-lead intercept). This is a
deliberate prior: parity with the market must never manufacture edge.

This module is DB-agnostic: it consumes plain (p0, q_raw, lead_bucket, y)
tuples and datetimes, never opens a database itself. Callers (the walk-
forward report script, tests) are responsible for extracting those from
settlement_attribution and deriving lead_bucket via ``lead_bucket_of``.

No live wiring: nothing in this module is imported by the entry path.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Mapping, Sequence

import numpy as np

from src.decision_kernel.canonicalization import stable_hash

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (plan item 9, verbatim).
# ---------------------------------------------------------------------------

# Clip bound on the logit residual (logit q_raw - logit p0) fed to beta.
CLIP_D = 3.0

# Probability clip applied to p0 and q_raw before any logit transform —
# matches scripts/scoreboard_panels.py's clip bounds so P1/this calibrator
# never disagree on where a probability is treated as certain.
P_CLIP_LO = 0.005
P_CLIP_HI = 0.995

# L2 penalty strength grid; walk_forward() selects one value from this grid
# using only walk-forward predictions made on an early "tuning" slice of the
# timeline (never the report's evaluation tail — see walk_forward()).
LAMBDA_GRID: tuple[float, ...] = (0.1, 1.0, 10.0)

# Hard clamp on the fitted beta (reversal_plan_tier0_2026-08-24.md item 26,
# external review verdict). Walk-forward evidence: raw q loses 0.3507 paired
# log loss to the market; the calibrated r removes 97.2% of that gap; beta was
# never observed above ~0.10-0.12 in walk-forward fits. An unclamped refit can
# silently amplify a source-side change (e.g. a forecast provenance shift) into
# an outsized weight on q_raw, which this clamp forecloses regardless of what
# any future refit's IRLS solve returns.
BETA_MIN = 0.0
BETA_MAX = 0.12

# lead = (target_date - decision_date).days. Only these three buckets are
# modeled; any other lead value (including negative, or >=3) fails closed —
# lead_bucket_of returns None and the row is excluded from fit/predict,
# never silently folded into an existing bucket or raising a KeyError.
LEAD_BUCKETS: tuple[str, ...] = ("day0", "day1", "day2")

# A decision date with fewer prior settled rows than this cannot support a
# 4-parameter fit; walk_forward() excludes rows on such a date rather than
# fitting on a near-empty, unstable sample.
MIN_TRAIN_ROWS = 20

# Fraction of the (chronologically sorted) unique decision dates used as the
# lambda-selection "train fold". The remaining dates are the untouched
# evaluation tail — lambda is never chosen using them.
DEFAULT_TUNING_FRACTION = 0.4

_IRLS_MAX_ITER = 50
_IRLS_TOL = 1e-10


def clip_p(p: float) -> float:
    return min(max(p, P_CLIP_LO), P_CLIP_HI)


def logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def lead_bucket_of(decision_date: date, target_date: date) -> str | None:
    """Map (decision_date, target_date) to a modeled lead bucket, or None.

    None is the fail-closed signal for an unmodeled lead (e.g. lead=3, or a
    negative lead from bad data) — callers must treat it as "no calibrated
    output available", never guess a nearest bucket.
    """
    lead = (target_date - decision_date).days
    if lead == 0:
        return "day0"
    if lead == 1:
        return "day1"
    if lead == 2:
        return "day2"
    return None


def _is_finite_number(value: object) -> bool:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


# ---------------------------------------------------------------------------
# Frozen artifact.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidualCalibratorArtifact:
    """A fitted, content-addressed set of calibrator parameters."""

    alpha: Mapping[str, float]  # lead_bucket -> intercept, keys == lead_buckets
    beta: float
    lambda_: float
    clip_d: float
    p_clip: tuple[float, float]
    lead_buckets: tuple[str, ...]
    training_cutoff: str  # ISO8601 UTC — no row with settled_at >= this was used to fit
    n_train: int
    n_excluded: int
    excluded_reasons: Mapping[str, int]
    param_hash: str

    def predict(self, p0: float, q_raw: float, lead_bucket: str | None) -> float | None:
        return apply_artifact(self, p0, q_raw, lead_bucket)


def _param_hash(
    *,
    alpha: Mapping[str, float],
    beta: float,
    lambda_: float,
    clip_d: float,
    p_clip: tuple[float, float],
    lead_buckets: tuple[str, ...],
    training_cutoff: str,
) -> str:
    """sha256 of the canonical parameter JSON — excludes provenance counts.

    Two artifacts with identical (alpha, beta, lambda, clip bounds,
    training_cutoff) hash identically regardless of how many rows were seen
    or excluded; n_train/n_excluded are provenance, not parameters.
    """
    return stable_hash(
        {
            "alpha": dict(alpha),
            "beta": beta,
            "lambda": lambda_,
            "clip_d": clip_d,
            "p_clip": list(p_clip),
            "lead_buckets": list(lead_buckets),
            "training_cutoff": training_cutoff,
        }
    )


def apply_artifact(
    artifact: ResidualCalibratorArtifact, p0: float, q_raw: float, lead_bucket: str | None
) -> float | None:
    """Apply a fitted artifact to one (p0, q_raw, lead_bucket) triple.

    Fails closed (returns None) for an unmodeled lead_bucket, or non-finite
    p0/q_raw — never guesses, never raises KeyError.
    """
    if lead_bucket not in artifact.alpha:
        return None
    if not _is_finite_number(p0) or not _is_finite_number(q_raw):
        return None
    p0_c = clip_p(float(p0))
    q_c = clip_p(float(q_raw))
    x = max(-artifact.clip_d, min(artifact.clip_d, logit(q_c) - logit(p0_c)))
    z = logit(p0_c) + artifact.alpha[lead_bucket] + artifact.beta * x
    return min(max(sigmoid(z), artifact.p_clip[0]), artifact.p_clip[1])


# ---------------------------------------------------------------------------
# fit() — single ridge-logistic-with-offset fit at a fixed lambda.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FitRow:
    p0: float | None
    q_raw: float | None
    lead_bucket: str | None
    y: int | None


def _design_row(row: FitRow) -> tuple[np.ndarray, float, float] | None:
    """Build (feature_vector, offset, y) for one row, or None if invalid.

    feature_vector = [1{day0}, 1{day1}, 1{day2}, clipped_logit_residual].
    Invalid: unmodeled lead_bucket, non-finite p0/q_raw, or y not in {0,1}.
    """
    if row.lead_bucket not in LEAD_BUCKETS:
        return None
    if not _is_finite_number(row.p0) or not _is_finite_number(row.q_raw):
        return None
    if row.y not in (0, 1):
        return None
    p0_c = clip_p(float(row.p0))
    q_c = clip_p(float(row.q_raw))
    x = max(-CLIP_D, min(CLIP_D, logit(q_c) - logit(p0_c)))
    onehot = [1.0 if row.lead_bucket == bucket else 0.0 for bucket in LEAD_BUCKETS]
    features = np.array([*onehot, x], dtype=np.float64)
    offset = logit(p0_c)
    return features, offset, float(row.y)


def _fit_irls(
    X: np.ndarray, y: np.ndarray, offset: np.ndarray, lambda_: float
) -> np.ndarray:
    """Ridge-penalized Newton-Raphson logistic regression with a fixed offset.

    Minimizes -loglik(beta) + (lambda_/2)*||beta||^2 where
    mu = sigmoid(offset + X @ beta). Zero-initialized (deterministic start);
    the L2 term keeps the Hessian positive-definite even under separable
    data, so this converges without a rank-deficiency special case.
    """
    n_params = X.shape[1]
    beta = np.zeros(n_params, dtype=np.float64)
    identity = np.eye(n_params)
    for _ in range(_IRLS_MAX_ITER):
        eta = offset + X @ beta
        mu = 1.0 / (1.0 + np.exp(-eta))
        grad = X.T @ (mu - y) + lambda_ * beta
        w = mu * (1.0 - mu)
        hessian = (X * w[:, None]).T @ X + lambda_ * identity
        delta = np.linalg.solve(hessian, grad)
        beta = beta - delta
        if np.max(np.abs(delta)) < _IRLS_TOL:
            break
    return beta


def fit(
    rows: Sequence[FitRow],
    *,
    lambda_: float,
    training_cutoff: str,
) -> ResidualCalibratorArtifact:
    """Fit one artifact at a fixed lambda on the given rows.

    Rows failing validity (unmodeled lead, non-finite p0/q_raw, bad y) are
    excluded and counted in the returned artifact's excluded_reasons —
    never silently dropped, never crash the fit.
    """
    excluded_reasons: dict[str, int] = {
        "unmapped_lead_bucket": 0,
        "invalid_probability": 0,
        "invalid_outcome": 0,
    }
    design_rows: list[tuple[np.ndarray, float, float]] = []
    for row in rows:
        if row.lead_bucket not in LEAD_BUCKETS:
            excluded_reasons["unmapped_lead_bucket"] += 1
            continue
        if not _is_finite_number(row.p0) or not _is_finite_number(row.q_raw):
            excluded_reasons["invalid_probability"] += 1
            continue
        if row.y not in (0, 1):
            excluded_reasons["invalid_outcome"] += 1
            continue
        built = _design_row(row)
        assert built is not None  # validity already checked above
        design_rows.append(built)

    n_excluded = sum(excluded_reasons.values())
    lead_buckets = LEAD_BUCKETS
    if not design_rows:
        alpha = {bucket: 0.0 for bucket in lead_buckets}
        beta = 0.0
    else:
        X = np.stack([r[0] for r in design_rows])
        offset = np.array([r[1] for r in design_rows], dtype=np.float64)
        y = np.array([r[2] for r in design_rows], dtype=np.float64)
        coef = _fit_irls(X, y, offset, lambda_)
        alpha = {bucket: float(coef[i]) for i, bucket in enumerate(lead_buckets)}
        raw_beta = float(coef[-1])
        beta = min(max(raw_beta, BETA_MIN), BETA_MAX)
        if beta != raw_beta:
            _LOG.warning(
                "market-anchored fit beta clamped: raw=%.6f clamped=%.6f "
                "training_cutoff=%s n_train=%d",
                raw_beta,
                beta,
                training_cutoff,
                len(design_rows),
            )

    p_clip = (P_CLIP_LO, P_CLIP_HI)
    param_hash = _param_hash(
        alpha=alpha,
        beta=beta,
        lambda_=lambda_,
        clip_d=CLIP_D,
        p_clip=p_clip,
        lead_buckets=lead_buckets,
        training_cutoff=training_cutoff,
    )
    return ResidualCalibratorArtifact(
        alpha=alpha,
        beta=beta,
        lambda_=lambda_,
        clip_d=CLIP_D,
        p_clip=p_clip,
        lead_buckets=lead_buckets,
        training_cutoff=training_cutoff,
        n_train=len(design_rows),
        n_excluded=n_excluded,
        excluded_reasons=excluded_reasons,
        param_hash=param_hash,
    )


# ---------------------------------------------------------------------------
# walk_forward() — expanding-window refit per decision day, no look-ahead.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardRow:
    """One settlement_attribution-derived row for the walk-forward.

    decision_at: the row's decision-time timestamp (tz-aware UTC), used both
    to bucket the row into a decision_date refit group and, via that date's
    start-of-day cutoff, to exclude it from any OTHER row's training set
    when that other row's cutoff falls before this row settled.
    settled_at: the row's settlement timestamp (tz-aware UTC) — the walk-
    forward law: no training row may have settled_at >= the predicted row's
    decision-date cutoff.
    """

    row_id: str
    p0: float | None
    q_raw: float | None
    lead_bucket: str | None
    y: int | None
    decision_at: datetime | None
    settled_at: datetime | None


@dataclass(frozen=True)
class WalkForwardPrediction:
    row_id: str
    decision_date: str | None  # ISO date of the refit bucket, or None if excluded pre-bucketing
    r_hat: float | None  # None = no calibrated output (coverage-counted)
    p0: float | None
    q_raw: float | None
    lambda_used: float | None
    excluded_reason: str | None


@dataclass(frozen=True)
class WalkForwardResult:
    predictions: tuple[WalkForwardPrediction, ...]
    lambda_selected: float
    lambda_selection_used_tuning: bool
    beta_trajectory: tuple[tuple[str, float], ...]  # (decision_date, beta) per successful refit
    final_artifact: ResidualCalibratorArtifact | None
    n_excluded_total: int
    excluded_reasons: Mapping[str, int]


def _cutoff_at(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _fit_row_of(row: WalkForwardRow) -> FitRow:
    return FitRow(p0=row.p0, q_raw=row.q_raw, lead_bucket=row.lead_bucket, y=row.y)


def _refit_and_predict_for_date(
    d: date,
    rows_on_date: list[WalkForwardRow],
    train_rows: list[WalkForwardRow],
    lambda_: float,
    min_train_rows: int,
) -> tuple[list[WalkForwardPrediction], ResidualCalibratorArtifact | None]:
    if len(train_rows) < min_train_rows:
        preds = [
            WalkForwardPrediction(
                row_id=row.row_id,
                decision_date=d.isoformat(),
                r_hat=None,
                p0=row.p0,
                q_raw=row.q_raw,
                lambda_used=None,
                excluded_reason="insufficient_training_data",
            )
            for row in rows_on_date
        ]
        return preds, None

    cutoff_iso = _cutoff_at(d).isoformat().replace("+00:00", "Z")
    artifact = fit(
        [_fit_row_of(r) for r in train_rows],
        lambda_=lambda_,
        training_cutoff=cutoff_iso,
    )
    preds = []
    for row in rows_on_date:
        r_hat = apply_artifact(artifact, row.p0, row.q_raw, row.lead_bucket)
        preds.append(
            WalkForwardPrediction(
                row_id=row.row_id,
                decision_date=d.isoformat(),
                r_hat=r_hat,
                p0=row.p0,
                q_raw=row.q_raw,
                lambda_used=lambda_,
                excluded_reason=None if r_hat is not None else "unmapped_lead_bucket_or_invalid_input",
            )
        )
    return preds, artifact


def _logloss(y: int, p: float) -> float:
    p_c = clip_p(p)
    return -(y * math.log(p_c) + (1 - y) * math.log(1.0 - p_c))


def walk_forward(
    rows: Sequence[WalkForwardRow],
    *,
    lambda_grid: tuple[float, ...] = LAMBDA_GRID,
    min_train_rows: int = MIN_TRAIN_ROWS,
    tuning_fraction: float = DEFAULT_TUNING_FRACTION,
) -> WalkForwardResult:
    """Expanding-window walk-forward: one refit per unique decision date.

    No-look-ahead law: the refit for decision date D trains ONLY on rows
    whose settled_at is strictly before D's start-of-day UTC cutoff — a row
    settled on or after that cutoff can never influence a prediction made at
    D, however far in the future it eventually gets appended to ``rows``.

    Lambda selection (plan item 9: "selected by walk-forward log-loss on
    train folds only — NO tuning on the evaluation tail"): the earliest
    ``tuning_fraction`` of unique decision dates are walk-forward-predicted
    once per lambda in ``lambda_grid``; the lambda with the lowest mean
    log-loss on THOSE (already out-of-sample, by the no-look-ahead law)
    predictions is fixed and used for the full walk-forward, including the
    untouched remaining "evaluation tail" dates. If there are too few tuning
    dates to select meaningfully, falls back to the grid's middle value.
    """
    excluded_reasons: dict[str, int] = {
        "missing_decision_at": 0,
        "missing_settled_at": 0,
    }
    valid_rows: list[WalkForwardRow] = []
    for row in rows:
        if row.decision_at is None:
            excluded_reasons["missing_decision_at"] += 1
            continue
        if row.settled_at is None:
            excluded_reasons["missing_settled_at"] += 1
            continue
        valid_rows.append(row)

    by_date: dict[date, list[WalkForwardRow]] = {}
    for row in valid_rows:
        d = row.decision_at.astimezone(timezone.utc).date()
        by_date.setdefault(d, []).append(row)
    unique_dates = sorted(by_date.keys())

    def run_all_dates(lambda_: float, dates: list[date]) -> list[WalkForwardPrediction]:
        out: list[WalkForwardPrediction] = []
        for d in dates:
            cutoff = _cutoff_at(d)
            train_rows = [r for r in valid_rows if r.settled_at.astimezone(timezone.utc) < cutoff]
            preds, _ = _refit_and_predict_for_date(d, by_date[d], train_rows, lambda_, min_train_rows)
            out.extend(preds)
        return out

    n_tuning = int(len(unique_dates) * tuning_fraction)
    tuning_dates = unique_dates[:n_tuning]
    lambda_selection_used_tuning = False
    if len(tuning_dates) >= 3:
        best_lambda = lambda_grid[0]
        best_loss = math.inf
        for cand in lambda_grid:
            tuning_preds = run_all_dates(cand, tuning_dates)
            scored = [
                (p, r.y)
                for p, r in zip(tuning_preds, [row for d in tuning_dates for row in by_date[d]])
                if p.r_hat is not None
            ]
            if not scored:
                continue
            mean_loss = statistics_mean_logloss(scored)
            if mean_loss < best_loss:
                best_loss = mean_loss
                best_lambda = cand
        if best_loss < math.inf:
            lambda_selection_used_tuning = True
        lambda_selected = best_lambda
    else:
        lambda_selected = lambda_grid[len(lambda_grid) // 2]

    all_predictions: list[WalkForwardPrediction] = []
    beta_trajectory: list[tuple[str, float]] = []
    final_artifact: ResidualCalibratorArtifact | None = None
    for d in unique_dates:
        cutoff = _cutoff_at(d)
        train_rows = [r for r in valid_rows if r.settled_at.astimezone(timezone.utc) < cutoff]
        preds, artifact = _refit_and_predict_for_date(
            d, by_date[d], train_rows, lambda_selected, min_train_rows
        )
        all_predictions.extend(preds)
        if artifact is not None:
            beta_trajectory.append((d.isoformat(), artifact.beta))
            final_artifact = artifact

    for prediction in all_predictions:
        if prediction.excluded_reason is not None:
            excluded_reasons[prediction.excluded_reason] = (
                excluded_reasons.get(prediction.excluded_reason, 0) + 1
            )

    total_excluded = sum(excluded_reasons.values())
    return WalkForwardResult(
        predictions=tuple(all_predictions),
        lambda_selected=lambda_selected,
        lambda_selection_used_tuning=lambda_selection_used_tuning,
        beta_trajectory=tuple(beta_trajectory),
        final_artifact=final_artifact,
        n_excluded_total=total_excluded,
        excluded_reasons=excluded_reasons,
    )


def statistics_mean_logloss(scored: list[tuple[WalkForwardPrediction, int]]) -> float:
    losses = [_logloss(int(y), p.r_hat) for p, y in scored if y is not None]
    if not losses:
        return math.inf
    return sum(losses) / len(losses)
