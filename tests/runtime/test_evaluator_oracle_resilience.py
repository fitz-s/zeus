# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: PLAN_v3 PR-B; oracle fail-closed gate removed in favor of
#                  graceful fallback via oracle_penalty.py. Live trading must
#                  not halt when oracle artifacts missing; oracle is sizing
#                  modifier, not truth gate.
"""Verify evaluator no longer halts when oracle_error_rates.json is missing.

Pre-PR-B behaviour: missing/stale oracle file → ORACLE_EVIDENCE_UNAVAILABLE
rejection on every edge → live trading paused.

Post-PR-B behaviour: missing file → oracle_penalty.get_oracle_info returns
_DEFAULT_OK for every (city, metric) → trades proceed at standard Kelly.
"""
from __future__ import annotations

from pathlib import Path

from src.engine import evaluator as evaluator_module
from src.strategy import oracle_penalty


def test_evaluator_module_has_no_oracle_evidence_gate():
    """The fail-closed gate symbols must no longer exist in the module."""
    assert not hasattr(evaluator_module, "ORACLE_EVIDENCE_PATH")
    assert not hasattr(evaluator_module, "ORACLE_EVIDENCE_MAX_STALENESS_DAYS")
    assert not hasattr(evaluator_module, "_oracle_evidence_rejection_reason")
    assert not hasattr(evaluator_module, "_oracle_evidence_row")


def test_oracle_penalty_returns_ok_when_file_missing(monkeypatch, tmp_path):
    """Graceful fallback contract that PR-B relies on for live resilience."""
    missing_path = tmp_path / "definitely_does_not_exist.json"
    assert not missing_path.exists()
    monkeypatch.setattr(oracle_penalty, "_ORACLE_FILE", missing_path)
    oracle_penalty.reload()

    info = oracle_penalty.get_oracle_info("Chicago", "high")
    assert info.status == oracle_penalty.OracleStatus.OK
    assert info.penalty_multiplier == 1.0


def test_oracle_penalty_reload_picks_up_new_file(monkeypatch, tmp_path):
    """reload() must re-read from disk so cron-written updates take effect
    without daemon restart."""
    import json as _json

    path = tmp_path / "oracle_error_rates.json"
    monkeypatch.setattr(oracle_penalty, "_ORACLE_FILE", path)

    path.write_text(_json.dumps({"Shenzhen": {"high": {"oracle_error_rate": 0.40}}}))
    oracle_penalty.reload()
    info = oracle_penalty.get_oracle_info("Shenzhen", "high")
    assert info.status == oracle_penalty.OracleStatus.BLACKLIST

    path.write_text(_json.dumps({"Shenzhen": {"high": {"oracle_error_rate": 0.0}}}))
    oracle_penalty.reload()
    info = oracle_penalty.get_oracle_info("Shenzhen", "high")
    assert info.status == oracle_penalty.OracleStatus.OK
