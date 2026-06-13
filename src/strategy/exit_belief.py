# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E5a (market-as-second-forecaster logistic stacking; q_exit = E[σ(...)], weights
#   estimated by OUT-OF-SAMPLE log score, never hand-averaged) + raw doc Q1
#   (fit_blended_logistic_forecaster / predict_blended_q reference impls).
#   Plan: docs/evidence/plans/2026-06-13_exit_capability.md.
"""Market-blended exit belief q_exit (E5a — the Denver-class miscalibration fix).

The exit rules (E2-E4, src/strategy/exit_policy.py) are correct ONLY if q_t is
calibrated. The Denver/4-loss class is exactly the failure where the agent
posterior says "still winning" while the market correctly disagrees, and the
system holds the loser. E5a treats the MARKET as a second forecaster and fits a
logistic stacking blend on RESOLVED snapshots:

    logit P(Y=1) = β0 + β_a·logit(q_agent) + β_m·logit(q_market)

q_exit = E[σ(β0 + β_a·logit(q_agent) + β_m·logit(q_market))] is then used in the
exit rule INSTEAD of the raw q_t. The weights are ESTIMATED by out-of-sample log
score, never averaged by hand (NO-CAPS / honest-math law).

This module:
  * ``fit_blended_exit_belief`` — fits β on (y, q_agent, q_market) arrays of
    resolved snapshots; computes the OOS-log-score improvement over raw agent q
    by K-fold and writes ``state/exit_belief_fit.json`` (β, cov, OOS CI, license).
  * ``predict_q_exit`` — at decision time, loads the artifact and predicts q_exit
    from (q_agent, q_market). LICENSE: deploy the blend only when the OOS log-score
    improvement CI > 0; otherwise degrade to the raw agent q with a LOUD source
    label (shadow-only) — same fee-authority degrade pattern as james_stein_blend.

Pure math + a read-only artifact load (no live-DB writes from the predict path).
The fit script may run offline; the artifact load is mtime-cached so the hot exit
path costs only a stat() call.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "state" / "exit_belief_fit.json"

# Evidence staleness: the blend is fit on resolved snapshots; beyond this the
# market/agent relationship may have drifted (regime change).
MAX_ARTIFACT_AGE_DAYS: float = 30.0

# Minimum resolved snapshots to even attempt a fit (data-sufficiency licence,
# honest math — NOT an artificial trading cap). Below this the blend is unfit and
# the predict path degrades to raw agent q.
MIN_RESOLVED_SNAPSHOTS: int = 50

_EPS = 1e-9

_cache: dict[str, object] = {"mtime": None, "artifact": None}


def _clip_prob(q: float, eps: float = _EPS) -> float:
    return float(np.clip(q, eps, 1.0 - eps))


# ---------------------------------------------------------------------------
# Fit (offline; writes state/exit_belief_fit.json)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BlendedExitBeliefFit:
    """Result of a logistic-stacking exit-belief fit."""

    beta: np.ndarray                 # [β0, β_a, β_m]
    beta_cov: np.ndarray             # approximate covariance (inverse Hessian)
    n_snapshots: int
    oos_logscore_improvement: float  # mean OOS log-score(blend) - log-score(agent)
    oos_logscore_improvement_ci: tuple[float, float]  # bootstrap CI of the above
    licensed: bool                   # CI lower bound > 0 ⇒ deploy; else shadow-only
    source: str


def _logistic_fit(
    y: np.ndarray, q_agent: np.ndarray, q_market: np.ndarray, l2: float = 1e-4
) -> tuple[np.ndarray, np.ndarray]:
    """Fit logit P(Y=1) = β0 + β_a·logit(qa) + β_m·logit(qm) (L2-penalized).

    Returns ``(beta, cov)`` (cov = inverse-Hessian approximation). Raw-doc Q1
    reference ``fit_blended_logistic_forecaster``.
    """
    y = np.asarray(y, dtype=float)
    qa = np.clip(np.asarray(q_agent, dtype=float), 1e-6, 1 - 1e-6)
    qm = np.clip(np.asarray(q_market, dtype=float), 1e-6, 1 - 1e-6)
    X = np.column_stack([np.ones_like(y), logit(qa), logit(qm)])

    def nll(beta: np.ndarray) -> float:
        eta = X @ beta
        loss = float(np.sum(np.logaddexp(0.0, eta) - y * eta))
        loss += 0.5 * l2 * float(np.sum(beta[1:] ** 2))
        return loss

    def grad(beta: np.ndarray) -> np.ndarray:
        p = expit(X @ beta)
        g = X.T @ (p - y)
        g[1:] += l2 * beta[1:]
        return g

    res = minimize(nll, np.zeros(X.shape[1]), jac=grad, method="BFGS")
    if not res.success:
        raise RuntimeError(f"exit-belief logistic fit failed: {res.message}")
    return res.x, np.asarray(res.hess_inv)


def _logscore(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Per-observation log score (higher is better): y·log p + (1-y)·log(1-p)."""
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return y * np.log(p) + (1.0 - y) * np.log(1.0 - p)


def fit_blended_exit_belief(
    y,
    q_agent,
    q_market,
    *,
    n_folds: int = 5,
    n_boot: int = 1000,
    l2: float = 1e-4,
    seed: int = 12345,
) -> BlendedExitBeliefFit:
    """Fit the market-blended exit belief and license it by OOS log score.

    Args:
        y: resolved binary outcomes (1 = held side won) over snapshots.
        q_agent: agent posterior at the snapshot (held-side prob).
        q_market: market-implied prob at the snapshot (held-side, fee/spread adj.).
        n_folds: K for the out-of-sample log-score evaluation.
        n_boot: bootstrap resamples for the OOS-improvement CI.

    Returns:
        BlendedExitBeliefFit. ``licensed`` is True iff the bootstrap CI lower bound
        of (OOS log score of blend − OOS log score of raw agent q) is > 0 — i.e.
        the market blend is a PROVEN out-of-sample improvement (E5a license). Else
        the blend is shadow-only and the predict path uses the raw agent q.

    Raises ValueError below MIN_RESOLVED_SNAPSHOTS (data-sufficiency, honest math).
    """
    y = np.asarray(y, dtype=float).ravel()
    qa = np.asarray(q_agent, dtype=float).ravel()
    qm = np.asarray(q_market, dtype=float).ravel()
    n = y.size
    if not (qa.size == n and qm.size == n):
        raise ValueError("y, q_agent, q_market must be the same length")
    if n < MIN_RESOLVED_SNAPSHOTS:
        raise ValueError(
            f"fit_blended_exit_belief needs >= {MIN_RESOLVED_SNAPSHOTS} resolved "
            f"snapshots, got {n}"
        )

    rng = np.random.default_rng(seed)

    # Full-sample fit for the deployed coefficients.
    beta, cov = _logistic_fit(y, qa, qm, l2=l2)

    # K-fold OOS log-score improvement of the blend over the raw agent q.
    idx = rng.permutation(n)
    folds = np.array_split(idx, n_folds)
    per_obs_improvement = np.full(n, np.nan)
    for k in range(n_folds):
        test = folds[k]
        train = np.concatenate([folds[j] for j in range(n_folds) if j != k])
        if test.size == 0 or train.size < 3:
            continue
        b_k, _ = _logistic_fit(y[train], qa[train], qm[train], l2=l2)
        Xte = np.column_stack(
            [np.ones(test.size), logit(np.clip(qa[test], 1e-6, 1 - 1e-6)),
             logit(np.clip(qm[test], 1e-6, 1 - 1e-6))]
        )
        p_blend = expit(Xte @ b_k)
        ls_blend = _logscore(y[test], p_blend)
        ls_agent = _logscore(y[test], np.clip(qa[test], 1e-12, 1 - 1e-12))
        per_obs_improvement[test] = ls_blend - ls_agent

    valid = per_obs_improvement[np.isfinite(per_obs_improvement)]
    mean_impr = float(np.mean(valid)) if valid.size else float("nan")

    # Bootstrap CI of the mean OOS improvement (preserve the per-obs paired diffs).
    if valid.size >= 2:
        boots = np.empty(n_boot)
        for b in range(n_boot):
            sample = rng.choice(valid, size=valid.size, replace=True)
            boots[b] = float(np.mean(sample))
        ci = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))
    else:
        ci = (float("nan"), float("nan"))

    licensed = bool(np.isfinite(ci[0]) and ci[0] > 0.0)
    source = (
        f"exit_belief_fit n={n} oos_impr={mean_impr:.5f} "
        f"ci=[{ci[0]:.5f},{ci[1]:.5f}] licensed={licensed}"
    )
    return BlendedExitBeliefFit(
        beta=beta,
        beta_cov=cov,
        n_snapshots=n,
        oos_logscore_improvement=mean_impr,
        oos_logscore_improvement_ci=ci,
        licensed=licensed,
        source=source,
    )


def write_exit_belief_fit(fit: BlendedExitBeliefFit, path: str | Path | None = None) -> Path:
    """Serialize a fit to ``state/exit_belief_fit.json`` (offline fitter writes this)."""
    target = Path(path) if path is not None else ARTIFACT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "beta": [float(b) for b in np.asarray(fit.beta).ravel()],
        "beta_cov": [[float(v) for v in row] for row in np.asarray(fit.beta_cov)],
        "n_snapshots": int(fit.n_snapshots),
        "oos_logscore_improvement": float(fit.oos_logscore_improvement),
        "oos_logscore_improvement_ci": [float(c) for c in fit.oos_logscore_improvement_ci],
        "licensed": bool(fit.licensed),
        "source": fit.source,
        "fitted_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }
    target.write_text(json.dumps(payload, indent=2))
    return target


# ---------------------------------------------------------------------------
# Predict (hot exit path; read-only mtime-cached artifact load)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class QExitResult:
    """The exit belief used by the exit rule."""

    q_exit: float           # the prob fed to exit_fraction_binary
    q_agent: float          # the raw agent posterior (for shadow comparison)
    q_market: float         # the market-implied prob
    blend_applied: bool     # True ⇒ q_exit is the fitted blend; False ⇒ raw agent
    source: str             # provenance / degrade-reason label


def _load_artifact(path: Path) -> dict | None:
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    if _cache["mtime"] == mtime and _cache["artifact"] is not None:
        return _cache["artifact"]  # type: ignore[return-value]
    try:
        artifact = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(artifact, dict):
        return None
    _cache["mtime"] = mtime
    _cache["artifact"] = artifact
    return artifact


def predict_q_exit(
    q_agent: float,
    q_market: float | None,
    *,
    path: str | Path | None = None,
    n_mc: int = 0,
    require_license: bool = True,
) -> QExitResult:
    """Predict the exit belief q_exit, degrading to raw agent q when unlicensed.

    The blend is applied ONLY when (a) the artifact is present, fresh, and fit on
    >= MIN_RESOLVED_SNAPSHOTS, AND (b) it is OOS-log-score-licensed (CI > 0) when
    ``require_license`` is True. Otherwise q_exit = raw agent q with a LOUD source
    label (shadow-only). This is the fee-authority / james_stein degrade pattern —
    a thin market-snapshot join or an unproven blend NEVER silently overrides the
    agent posterior.

    ``q_market`` None ⇒ no market second-forecaster available ⇒ raw agent q.
    """
    qa = _clip_prob(q_agent)
    if q_market is None or not np.isfinite(q_market):
        return QExitResult(qa, qa, float("nan"), False, "q_exit_degrade_no_market_prob")
    qm = _clip_prob(q_market)

    target = Path(path) if path is not None else ARTIFACT_PATH
    artifact = _load_artifact(target)
    if artifact is None:
        return QExitResult(qa, qa, qm, False, "q_exit_degrade_artifact_missing")

    # Staleness
    try:
        age_days = (__import__("time").time() - os.path.getmtime(target)) / 86400.0
    except OSError:
        age_days = float("inf")
    if age_days > MAX_ARTIFACT_AGE_DAYS:
        return QExitResult(qa, qa, qm, False, f"q_exit_degrade_stale_age_days={age_days:.0f}")

    try:
        n_snap = int(artifact.get("n_snapshots") or 0)
    except (TypeError, ValueError):
        n_snap = 0
    if n_snap < MIN_RESOLVED_SNAPSHOTS:
        return QExitResult(qa, qa, qm, False, f"q_exit_degrade_thin_snapshots_n={n_snap}")

    if require_license and not bool(artifact.get("licensed")):
        return QExitResult(qa, qa, qm, False, "q_exit_degrade_unlicensed_oos_logscore")

    try:
        beta = np.asarray(artifact["beta"], dtype=float).ravel()
    except (KeyError, TypeError, ValueError):
        return QExitResult(qa, qa, qm, False, "q_exit_degrade_beta_unparseable")
    if beta.size != 3:
        return QExitResult(qa, qa, qm, False, "q_exit_degrade_beta_shape")

    x = np.array([1.0, logit(qa), logit(qm)])
    cov_raw = artifact.get("beta_cov")
    if n_mc > 0 and cov_raw is not None:
        try:
            cov = np.asarray(cov_raw, dtype=float)
            rng = np.random.default_rng(0)
            draws = rng.multivariate_normal(beta, cov, size=n_mc)
            q_exit = _clip_prob(float(np.mean(expit(draws @ x))))
        except (ValueError, TypeError, np.linalg.LinAlgError):
            q_exit = _clip_prob(float(expit(x @ beta)))
    else:
        q_exit = _clip_prob(float(expit(x @ beta)))

    return QExitResult(
        q_exit, qa, qm, True,
        f"q_exit_blend_applied n={n_snap} licensed=True",
    )
