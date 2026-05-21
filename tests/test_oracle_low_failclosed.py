# Created: 2026-05-05
# Last reused/audited: 2026-05-21
# Authority basis: architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md Phase α, superseded for LOW by 2026-05-21 live oracle-penalty P0 canonical evidence repair.
# Lifecycle: created=2026-05-05; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Protect LOW metric-specific oracle evidence routing.
# Reuse: Confirm LOW metric remains metric-specific before changing oracle_penalty.
"""LOW track oracle evidence routing tests."""

from __future__ import annotations

import json

from src.strategy import oracle_penalty


def _redirect_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    oracle_penalty._reset_for_test()
    return data_dir / "oracle_error_rates.json"


def test_low_metric_uses_populated_low_record(monkeypatch, tmp_path) -> None:
    """LOW track uses its own oracle evidence when the bridge writes it."""
    path = _redirect_storage(monkeypatch, tmp_path)
    path.write_text(json.dumps({
        "Chicago": {
            "high": {"n": 100, "mismatches": 5},
            "low": {
                "n": 100,
                "mismatches": 0,
                "source_role": "canonical_observation_instants_v2",
            },
        }
    }))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info(city_name="Chicago", temperature_metric="low")

    assert info.penalty_multiplier == 1.0
    assert info.status == oracle_penalty.OracleStatus.OK


def test_low_metric_missing_when_low_record_absent(monkeypatch, tmp_path) -> None:
    """A missing LOW record is MISSING evidence, not a permanent metric ban."""
    path = _redirect_storage(monkeypatch, tmp_path)
    path.write_text(json.dumps({"Tokyo": {"high": {"n": 200, "mismatches": 0}}}))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info(city_name="Tokyo", temperature_metric="low")

    assert info.status == oracle_penalty.OracleStatus.MISSING
    assert info.penalty_multiplier == 0.5
    assert info.block_reason and "absent" in info.block_reason


def test_high_metric_still_works_normally(monkeypatch, tmp_path) -> None:
    """Verify HIGH track is unaffected by LOW fail-close gate."""
    path = _redirect_storage(monkeypatch, tmp_path)
    path.write_text(json.dumps({"Paris": {"high": {"n": 150, "mismatches": 2}}}))
    oracle_penalty._reset_for_test()

    info = oracle_penalty.get_oracle_info(city_name="Paris", temperature_metric="high")

    assert info.penalty_multiplier > 0.0, "HIGH must carry normal multiplier"
    assert info.status != oracle_penalty.OracleStatus.METRIC_UNSUPPORTED, "HIGH must not be METRIC_UNSUPPORTED"
