# Created: 2026-05-02
# Last reused/audited: 2026-05-04
# Authority basis: PLAN_v3 PR-B (oracle fail-closed gate removed in favor of
#                  graceful fallback via oracle_penalty.py; oracle is sizing
#                  modifier, not truth gate) + docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A2 (path centralization migration; tests use ZEUS_STORAGE_ROOT instead of monkey-patching _ORACLE_FILE).
# Note (PLAN.md §A3 forthcoming): the "_DEFAULT_OK on missing file" contract
# pinned by these tests is the rescue patch from PR #40 that §A3 closes
# (Bug review Finding A: missing != OK). When §A3 lands, these tests will
# need their assertions flipped from status==OK to status==MISSING.
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


def _redirect_storage(monkeypatch, tmp_path):
    """PLAN.md §A2 idiom: redirect oracle paths to tmp_path via env var
    rather than monkey-patching the (now-removed) _ORACLE_FILE constant.
    """
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path / "data" / "oracle_error_rates.json"


def test_oracle_penalty_returns_ok_when_file_missing(monkeypatch, tmp_path):
    """Graceful fallback contract that PR-B relies on for live resilience."""
    target = _redirect_storage(monkeypatch, tmp_path)
    assert not target.exists()
    oracle_penalty.reload()

    info = oracle_penalty.get_oracle_info("Chicago", "high")
    assert info.status == oracle_penalty.OracleStatus.OK
    assert info.penalty_multiplier == 1.0


def test_oracle_penalty_reload_picks_up_new_file(monkeypatch, tmp_path):
    """reload() must re-read from disk so cron-written updates take effect
    without daemon restart."""
    import json as _json

    path = _redirect_storage(monkeypatch, tmp_path)

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

    Note: §A2 ships atomic writers for the bridge so this half-written
    failure mode is now blocked at the writer level too — but the reader
    contract still holds for any non-bridge writer or legacy artifact.
    """
    import json as _json

    path = _redirect_storage(monkeypatch, tmp_path)

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

    path = _redirect_storage(monkeypatch, tmp_path)

    path.write_text(_json.dumps({"Shenzhen": {"high": {"oracle_error_rate": "not-a-number"}}}))
    oracle_penalty.reload()
    assert oracle_penalty.get_oracle_info("Shenzhen", "high").status == oracle_penalty.OracleStatus.OK


def test_evaluate_candidate_produces_decision_when_oracle_file_missing(monkeypatch, tmp_path):
    """Evaluator must not reject or raise when oracle artifact is absent."""
    from tests.test_center_buy_repair import _candidate, _patch_evaluator

    _redirect_storage(monkeypatch, tmp_path)
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
