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

from datetime import datetime, timezone

from src.engine import evaluator as evaluator_module
from src.engine.discovery_mode import DiscoveryMode
from src.state.portfolio import PortfolioState
from src.strategy import oracle_penalty


TEST_DECISION_TIME = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)


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


def test_oracle_penalty_reload_swallows_malformed_json(monkeypatch, tmp_path):
    """Codex P1 on PR #40: reload() must NOT propagate parse errors.

    A concurrent bridge write can leave oracle_error_rates.json half-written.
    The evaluator calls reload() every cycle; an unhandled json.JSONDecodeError
    would resurrect the exact halt-live-trading failure PR #40 fixed.
    Contract: malformed file → keep previous cache (or empty) + log warning.
    """
    import json as _json

    path = tmp_path / "oracle_error_rates.json"
    monkeypatch.setattr(oracle_penalty, "_ORACLE_FILE", path)

    path.write_text(_json.dumps({"Shenzhen": {"high": {"oracle_error_rate": 0.0}}}))
    oracle_penalty.reload()
    assert oracle_penalty.get_oracle_info("Shenzhen", "high").status == oracle_penalty.OracleStatus.OK

    path.write_text("{not valid json,,,")
    oracle_penalty.reload()
    assert oracle_penalty.get_oracle_info("Shenzhen", "high").status == oracle_penalty.OracleStatus.OK
    assert oracle_penalty.get_oracle_info("Anywhere", "high").status == oracle_penalty.OracleStatus.OK


def test_oracle_penalty_reload_swallows_bad_value_types(monkeypatch, tmp_path):
    """Bad numeric values (e.g., string instead of float) must not crash reload."""
    import json as _json

    path = tmp_path / "oracle_error_rates.json"
    monkeypatch.setattr(oracle_penalty, "_ORACLE_FILE", path)

    path.write_text(_json.dumps({"Shenzhen": {"high": {"oracle_error_rate": "not-a-number"}}}))
    oracle_penalty.reload()
    assert oracle_penalty.get_oracle_info("Shenzhen", "high").status == oracle_penalty.OracleStatus.OK


def test_evaluate_candidate_produces_decision_when_oracle_file_missing(monkeypatch, tmp_path):
    """Evaluator must not reject or raise when oracle artifact is absent."""
    from tests.test_center_buy_repair import _candidate, _patch_evaluator

    missing_path = tmp_path / "oracle_error_rates.json"
    monkeypatch.setattr(oracle_penalty, "_ORACLE_FILE", missing_path)
    oracle_penalty.reload()
    clob = _patch_evaluator(monkeypatch, entry_price=0.05)

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert decisions
    assert all(decision.rejection_stage != "ORACLE_EVIDENCE_UNAVAILABLE" for decision in decisions)
    assert all(
        "oracle_penalty" not in getattr(decision, "applied_validations", [])
        for decision in decisions
    )
