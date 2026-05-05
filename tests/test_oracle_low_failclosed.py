# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md Phase α — LOW track fail-closed invariant
# Lifecycle: created=2026-05-05; last_reviewed=2026-05-05; last_reused=2026-05-05
# Purpose: Protect LOW track fail-closed guarantee until LOW oracle snapshot bridge ships.
# Reuse: Confirm LOW metric enforcement in oracle_penalty before accepting LOW bridge PRs.
"""LOW track fail-closed invariant tests."""

from __future__ import annotations

import json

from src.strategy import oracle_penalty


def _redirect_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    oracle_penalty._reset_for_test()
    return data_dir / "oracle_error_rates.json"


def test_low_metric_returns_zero_multiplier(monkeypatch, tmp_path) -> None:
    """LOW track is fail-closed via METRIC_UNSUPPORTED → mult 0.0.

    Architect 2026-05-05: load-bearing invariant. If this test fails,
    a LOW bridge has shipped without an audit of HKO CLMMINT semantics
    (per architect plan, mirror-HIGH symmetry is unsafe). Block any LOW
    Kelly execution until the audit completes.
    """
    path = _redirect_storage(monkeypatch, tmp_path)
    # Write a well-formed HIGH record to verify HIGH still works
    path.write_text(json.dumps({"Chicago": {"high": {"n": 100, "mismatches": 5}}}))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info(city_name="Chicago", temperature_metric="low")

    assert info.penalty_multiplier == 0.0, f"LOW must be fail-closed; got mult={info.penalty_multiplier}"
    assert info.status == oracle_penalty.OracleStatus.METRIC_UNSUPPORTED, f"LOW must route to METRIC_UNSUPPORTED; got {info.status}"


def test_low_block_reason_cites_listener_gap(monkeypatch, tmp_path) -> None:
    """Block reason string is dashboard-stable; do not change without coordinating with ops."""
    path = _redirect_storage(monkeypatch, tmp_path)
    path.write_text(json.dumps({"Tokyo": {"high": {"n": 200, "mismatches": 0}}}))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info(city_name="Tokyo", temperature_metric="low")

    assert info.block_reason is not None, "LOW must have a block_reason for operator visibility"
    reason_lower = info.block_reason.lower()
    assert "low" in reason_lower or "metric" in reason_lower or "unsupported" in reason_lower, (
        f"Block reason must cite LOW or metric unsupported; got: {info.block_reason}"
    )


def test_high_metric_still_works_normally(monkeypatch, tmp_path) -> None:
    """Verify HIGH track is unaffected by LOW fail-close gate."""
    path = _redirect_storage(monkeypatch, tmp_path)
    path.write_text(json.dumps({"Paris": {"high": {"n": 150, "mismatches": 2}}}))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info(city_name="Paris", temperature_metric="high")

    assert info.penalty_multiplier > 0.0, "HIGH must carry normal multiplier"
    assert info.status != oracle_penalty.OracleStatus.METRIC_UNSUPPORTED, "HIGH must not be METRIC_UNSUPPORTED"
