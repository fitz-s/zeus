# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: may4math F3 oracle beta-binomial posterior
# Lifecycle: created=2026-05-04; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: Protect beta-binomial oracle posterior policy for missing, low-N, unsupported, and blacklist cases.
# Reuse: Confirm oracle_penalty posterior thresholds before changing Kelly or DDD oracle uncertainty semantics.
"""Oracle posterior policy tests."""

from __future__ import annotations

import json

from src.strategy import oracle_penalty


def _redirect_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    oracle_penalty._reset_for_test()
    return data_dir / "oracle_error_rates.json"


def test_small_n_zero_mismatch_is_insufficient_sample() -> None:
    info = oracle_penalty.summarize_oracle_posterior(n=12, mismatches=0, metric="high")

    assert info.status == oracle_penalty.OracleStatus.INSUFFICIENT_SAMPLE
    assert info.posterior_upper_95 > 0.15
    assert info.penalty_multiplier < 1.0


def test_large_n_zero_mismatch_can_be_ok() -> None:
    info = oracle_penalty.summarize_oracle_posterior(n=200, mismatches=0, metric="high")

    assert info.status == oracle_penalty.OracleStatus.OK
    assert info.posterior_upper_95 < 0.03
    assert info.penalty_multiplier == 1.0


def test_high_mismatch_posterior_blacklists() -> None:
    info = oracle_penalty.summarize_oracle_posterior(n=100, mismatches=15, metric="high")

    assert info.status == oracle_penalty.OracleStatus.BLACKLIST
    assert info.penalty_multiplier == 0.0
    assert info.posterior_prob_gt_10 > 0.80


def test_missing_artifact_is_not_ok(monkeypatch, tmp_path) -> None:
    missing = _redirect_storage(monkeypatch, tmp_path)
    assert not missing.exists()

    info = oracle_penalty.get_oracle_info("Chicago", "high")

    assert info.status == oracle_penalty.OracleStatus.MISSING
    assert info.penalty_multiplier < 1.0


def test_low_track_without_low_measurement_is_metric_unsupported(monkeypatch, tmp_path) -> None:
    path = _redirect_storage(monkeypatch, tmp_path)
    path.write_text(json.dumps({"Tokyo": {"high": {"n": 200, "mismatches": 0}}}))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info("Tokyo", "low")

    assert info.status == oracle_penalty.OracleStatus.METRIC_UNSUPPORTED
    assert info.penalty_multiplier < 1.0
