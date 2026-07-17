# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   §Consult v2 (f) — CP effective-n member-dependence correction; operator law
#   fail-open (artifact absent => byte-identical) + conservative-only widening.
"""Serving tests for the CP effective-n member-dependence correction.

Covers: artifact-absent byte-identity to the exact integer Clopper-Pearson
bound; conservativeness grid (UCB monotone non-decreasing in rho, never below
the rho=0 bound); k==n edge stays exactly 1.0; zero-hit floor widening;
per-metric lookup with pooled/max-rho fallback for unknown metric; loader
integrity (sha256 mismatch => identity).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
from scipy.special import betaincinv

from src.data.replacement_forecast_materializer import (
    _finite_evidence_binomial_ucb,
    _finite_evidence_zero_hit_ucb_floor,
)
from src.forecast import ens_member_dependence as emd


def _write_artifact(tmp_dir: Path, metrics: dict[str, dict]) -> None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema_version": 1, "metrics": metrics}, sort_keys=True)
    fname = "ens_member_dependence_test.json"
    (tmp_dir / fname).write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    (tmp_dir / "ACTIVE.json").write_text(
        json.dumps({"artifact": fname, "sha256": sha}), encoding="utf-8"
    )
    emd._load_active_artifact.cache_clear()


def _artifact_dir(monkeypatch, tmp_path: Path, metrics: dict[str, dict] | None) -> None:
    d = tmp_path / "emd"
    monkeypatch.setenv(emd.ENV_ARTIFACT_DIR, str(d))
    emd._load_active_artifact.cache_clear()
    if metrics is not None:
        _write_artifact(d, metrics)


def _integer_cp(k: int, n: int, alpha: float = 0.05) -> float:
    return 1.0 if k == n else float(betaincinv(k + 1, n - k, 1.0 - alpha))


def test_artifact_absent_is_byte_identical_integer_cp(monkeypatch, tmp_path) -> None:
    _artifact_dir(monkeypatch, tmp_path, None)
    assert emd.member_dependence_rho("high") == 0.0
    for n in (2, 10, 51):
        for k in range(n + 1):
            assert _finite_evidence_binomial_ucb(k, n, metric="high") == _integer_cp(k, n)
    # The pinned exact zero-hit identity the existing symmetry tests rely on.
    assert math.isclose(
        _finite_evidence_zero_hit_ucb_floor(51), 1.0 - 0.05 ** (1.0 / 51.0)
    )


def test_rho_zero_artifact_is_byte_identical(monkeypatch, tmp_path) -> None:
    _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": 0.0}})
    for n in (10, 51):
        for k in range(n + 1):
            assert _finite_evidence_binomial_ucb(k, n, metric="high") == _integer_cp(k, n)


@pytest.mark.parametrize("rho", [0.1, 0.3, 0.7])
@pytest.mark.parametrize("n", [10, 51])
def test_conservative_only_widening_grid(monkeypatch, tmp_path, rho, n) -> None:
    """UCB_rho >= UCB_0 for every k in 0..n; k==n stays exactly 1.0."""
    _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": rho}})
    for k in range(n + 1):
        corrected = _finite_evidence_binomial_ucb(k, n, metric="high")
        baseline = _integer_cp(k, n)
        assert corrected >= baseline - 1e-15, (k, n, rho)
        assert 0.0 < corrected <= 1.0
    assert _finite_evidence_binomial_ucb(n, n, metric="high") == 1.0


def test_ucb_monotone_in_rho(monkeypatch, tmp_path) -> None:
    n, k = 51, 3
    prev = _integer_cp(k, n)
    for rho in (0.05, 0.2, 0.5, 0.9):
        _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": rho}})
        cur = _finite_evidence_binomial_ucb(k, n, metric="high")
        assert cur >= prev - 1e-15
        prev = cur


def test_zero_hit_floor_widens_under_rho(monkeypatch, tmp_path) -> None:
    _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": 0.3}})
    widened = _finite_evidence_zero_hit_ucb_floor(51, metric="high")
    exact = 1.0 - 0.05 ** (1.0 / 51.0)
    # n_eff = 51/(1+50*0.3) = 3.1875; zero-hit UCB = 1 - alpha^(1/n_eff).
    n_eff = 51.0 / (1.0 + 50.0 * 0.3)
    assert widened > exact
    assert math.isclose(widened, 1.0 - 0.05 ** (1.0 / n_eff), rel_tol=1e-12)


def test_per_metric_rho_and_pooled_max_fallback(monkeypatch, tmp_path) -> None:
    _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": 0.2}, "low": {"rho": 0.5}})
    assert emd.member_dependence_rho("high") == 0.2
    assert emd.member_dependence_rho("low") == 0.5
    # Unknown / None metric => MAX fitted rho (conservative pooled fallback).
    assert emd.member_dependence_rho(None) == 0.5
    assert emd.member_dependence_rho("unknown") == 0.5
    # Clamped to [0, 1].
    _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": 1.7}})
    assert emd.member_dependence_rho("high") == 1.0


def test_sha256_mismatch_fails_open_to_identity(monkeypatch, tmp_path) -> None:
    _artifact_dir(monkeypatch, tmp_path, {"high": {"rho": 0.4}})
    d = tmp_path / "emd"
    pointer = json.loads((d / "ACTIVE.json").read_text(encoding="utf-8"))
    pointer["sha256"] = "0" * 64
    (d / "ACTIVE.json").write_text(json.dumps(pointer), encoding="utf-8")
    emd._load_active_artifact.cache_clear()
    assert emd.member_dependence_rho("high") == 0.0
    assert _finite_evidence_binomial_ucb(0, 51, metric="high") == _integer_cp(0, 51)
