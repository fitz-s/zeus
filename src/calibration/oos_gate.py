# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P3 "build gate inputs"; CRITIC_statistical S1-S4. The inputs the
#   candidate accept-gate (scripts/score_error_model_candidates.choose_candidate) consumes,
#   computed correctly for autocorrelated, multiple-tested, thin OOS evidence.
"""OOS accept-gate statistics.

Fixes the four statistical defects the gate inputs had (or lacked entirely):
  S4 — date-blocked folds: the same settlement target_date must never split across train/test.
  S3 — daily autocorrelation: forecast errors persist day-to-day, so an IID bootstrap is
       anticonservative. Use a MOVING-BLOCK bootstrap on the date-ordered improvement series,
       and report an AR(1) effective sample size.
  S2 — a real bootstrap LCB of mean improvement (the legacy gate had n_bootstrap=0).
  S1 — Benjamini-Hochberg FDR across the bucket×candidate family (≈168 tests) so spurious
       per-bucket "wins" do not get adopted by chance.

All "improvement" series are (score_raw − score_candidate) per target_date (lower score is
better, so positive = candidate beats raw). One ordered value per date.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

import numpy as np


def date_blocked_folds(target_dates: Sequence[str], k: int) -> list[int]:
    """Assign each record a fold in [0, k) keyed by its target_date.

    Records sharing a target_date ALWAYS land in the same fold (S4: no same-day leakage across
    the train/test split). Deterministic via a stable hash of the date string.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    folds: list[int] = []
    for d in target_dates:
        h = int(hashlib.sha1(str(d).encode()).hexdigest(), 16)
        folds.append(h % k)
    return folds


def lag1_autocorr(x: Sequence[float]) -> float:
    """Lag-1 autocorrelation of a series. Returns 0.0 for constant/degenerate input."""
    a = np.asarray(x, dtype=float)
    n = a.size
    if n < 2:
        return 0.0
    m = a.mean()
    denom = float(((a - m) ** 2).sum())
    if denom == 0.0:
        return 0.0
    num = float(((a[1:] - m) * (a[:-1] - m)).sum())
    return num / denom


def effective_sample_size(x: Sequence[float]) -> float:
    """AR(1) effective sample size n_eff = n·(1−ρ)/(1+ρ), clamped to [1, n] (S3).

    iid (ρ≈0) → n_eff≈n; strong positive autocorrelation (ρ→1) → n_eff≪n.
    """
    a = np.asarray(x, dtype=float)
    n = a.size
    if n <= 1:
        return float(n)
    rho = lag1_autocorr(a)
    factor = (1.0 - rho) / (1.0 + rho) if rho > -1.0 else float("inf")
    n_eff = n * factor
    return float(min(max(n_eff, 1.0), float(n)))


def moving_block_bootstrap_lcb(
    improvement: Sequence[float],
    *,
    alpha: float = 0.05,
    n_boot: int = 2000,
    block_len: int | None = None,
    seed: int = 0,
) -> tuple[float, float]:
    """One-sided lower confidence bound of the MEAN improvement, plus a p-value for H0: mean≤0.

    Moving-block bootstrap (block length L≈n^(1/3) by default) on the date-ordered series so
    cross-day autocorrelation is preserved in the resamples (S3). LCB is the α-quantile of the
    bootstrap means (α=0.05 → 95% one-sided). p_value = fraction of bootstrap means ≤ 0.
    Accept a candidate only if LCB > 0.
    """
    a = np.asarray(improvement, dtype=float)
    n = a.size
    if n == 0:
        raise ValueError("improvement series is empty")
    if n == 1:
        return (float(a[0]), 0.0 if a[0] > 0 else 1.0)

    L = block_len if block_len is not None else max(1, round(n ** (1.0 / 3.0)))
    L = min(L, n)
    num_blocks = math.ceil(n / L)
    max_start = n - L  # inclusive
    rng = np.random.default_rng(seed)

    means = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=num_blocks)
        sample = np.concatenate([a[s : s + L] for s in starts])[:n]
        means[b] = sample.mean()

    lcb = float(np.quantile(means, alpha))
    p_value = float(np.mean(means <= 0.0))
    return (lcb, p_value)


def bh_fdr_accept(pvalues: dict[str, float], q: float = 0.10) -> set[str]:
    """Benjamini-Hochberg: return the names significant at false-discovery rate q (S1).

    Controls expected false discoveries across the whole bucket×candidate family — a per-bucket
    p<q is NOT enough when many buckets are tested.
    """
    if not pvalues:
        return set()
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    k_max = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= (i / m) * q:
            k_max = i
    if k_max == 0:
        return set()
    return {name for name, _ in items[:k_max]}
