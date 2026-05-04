# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Tests for EntriesBlockRegistry and all 13 block adapters.

For each adapter: 2 tests minimum
  (a) CLEAR state probe
  (b) BLOCKING state probe with reason_format matching GATE_AUDIT.yaml

Plus 3 registry-level tests:
  - enumerate_blocks returns 13 blocks
  - is_clear when none blocking
  - first_blocker priority order
"""

from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
    EntriesBlockRegistry,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _in_memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _in_memory_risk_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            brier REAL,
            accuracy REAL,
            win_rate REAL,
            details_json TEXT,
            checked_at TEXT,
            force_exit_review INTEGER DEFAULT 0
        )
        """
    )
    return conn


def _in_memory_zeus_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS control_overrides_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            override_id TEXT,
            target_type TEXT,
            target_key TEXT,
            action_type TEXT,
            value TEXT,
            issued_by TEXT,
            issued_at TEXT,
            effective_until TEXT,
            reason TEXT,
            precedence INTEGER DEFAULT 0
        )
        """
    )
    # control_overrides VIEW (latest-value projection)
    conn.execute(
        """
        CREATE VIEW IF NOT EXISTS control_overrides AS
        SELECT * FROM control_overrides_history
        WHERE (override_id, issued_at) IN (
            SELECT override_id, MAX(issued_at) FROM control_overrides_history GROUP BY override_id
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            summary_json TEXT NOT NULL
        )
        """
    )
    return conn


def _mock_heartbeat_module(*, allow_submit: bool = True, health: str = "HEALTHY") -> types.ModuleType:
    mod = types.ModuleType("heartbeat_supervisor")
    mod.summary = lambda: {  # type: ignore[attr-defined]
        "health": health,
        "entry": {"allow_submit": allow_submit},
    }
    return mod


def _mock_ws_gap_guard_module(
    *,
    allow_submit: bool = True,
    subscription_state: str = "SUBSCRIBED",
    gap_reason: str = "message_received",
) -> types.ModuleType:
    mod = types.ModuleType("ws_gap_guard")
    mod.summary = lambda: {  # type: ignore[attr-defined]
        "subscription_state": subscription_state,
        "gap_reason": gap_reason,
        "m5_reconcile_required": False,
        "entry": {"allow_submit": allow_submit},
    }
    return mod


def _mock_rollout_gate_module(*, may_submit: bool = True, reason_codes: tuple[str, ...] = ()) -> types.ModuleType:
    from src.control.entry_forecast_rollout import EntryForecastRolloutDecision

    mod = types.ModuleType("entry_forecast_rollout")

    def _eval(*, config: Any, evidence: Any) -> EntryForecastRolloutDecision:
        status = "LIVE_ELIGIBLE" if may_submit else "BLOCKED"
        return EntryForecastRolloutDecision(status, reason_codes)

    mod.evaluate_entry_forecast_rollout_gate = _eval  # type: ignore[attr-defined]
    return mod


def _mock_riskguard_module() -> types.ModuleType:
    from src.riskguard.riskguard import _trailing_loss_reference

    mod = types.ModuleType("riskguard")
    mod._trailing_loss_reference = _trailing_loss_reference  # type: ignore[attr-defined]
    return mod


def _make_deps(
    *,
    state_dir: Path,
    zeus_conn: sqlite3.Connection | None = None,
    risk_conn: sqlite3.Connection | None = None,
    heartbeat_mod: types.ModuleType | None = None,
    ws_gap_mod: types.ModuleType | None = None,
    rollout_mod: types.ModuleType | None = None,
    riskguard_mod: types.ModuleType | None = None,
    env: dict[str, str] | None = None,
) -> RegistryDeps:
    _zeus_conn = zeus_conn or _in_memory_zeus_db()
    _risk_conn = risk_conn or _in_memory_risk_db()
    return RegistryDeps(
        state_dir=state_dir,
        db_connection_factory=lambda: _zeus_conn,
        risk_state_db_connection_factory=lambda: _risk_conn,
        riskguard_module=riskguard_mod or _mock_riskguard_module(),
        heartbeat_module=heartbeat_mod or _mock_heartbeat_module(),
        ws_gap_guard_module=ws_gap_mod or _mock_ws_gap_guard_module(),
        rollout_gate_module=rollout_mod or _mock_rollout_gate_module(),
        env=env or {"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "1"},
    )


# ── Gate 1: fail_closed_tombstone ─────────────────────────────────────────────

def test_gate1_clear_when_tombstone_absent(tmp_path: Path) -> None:
    from src.control.block_adapters.fail_closed_tombstone import FailClosedTombstoneAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = FailClosedTombstoneAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None
    assert block.id == 1


def test_gate1_blocking_when_tombstone_exists(tmp_path: Path) -> None:
    from src.control.block_adapters.fail_closed_tombstone import FailClosedTombstoneAdapter

    (tmp_path / "auto_pause_failclosed.tombstone").write_text("")
    deps = _make_deps(state_dir=tmp_path)
    block = FailClosedTombstoneAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "entries_paused"
    assert block.id == 1


# ── Gate 2: auto_pause_streak ─────────────────────────────────────────────────

def test_gate2_clear_when_no_streak_file(tmp_path: Path) -> None:
    from src.control.block_adapters.auto_pause_streak import AutoPauseStreakAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = AutoPauseStreakAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate2_blocking_when_streak_threshold_met_and_in_window(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from src.control.block_adapters.auto_pause_streak import AutoPauseStreakAdapter

    # Write streak file with count=3 and recent last_seen_at
    streak_data = {
        "reason_code": "SomeException",
        "count": 3,
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "threshold": 3,
        "window_seconds": 300,
    }
    (tmp_path / "auto_pause_streak.json").write_text(json.dumps(streak_data))
    deps = _make_deps(state_dir=tmp_path)
    block = AutoPauseStreakAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "auto_pause:SomeException"


def test_gate2_clear_when_streak_expired(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from src.control.block_adapters.auto_pause_streak import AutoPauseStreakAdapter

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    streak_data = {
        "reason_code": "SomeException",
        "count": 5,
        "first_seen_at": old_time,
        "last_seen_at": old_time,
        "threshold": 3,
        "window_seconds": 300,
    }
    (tmp_path / "auto_pause_streak.json").write_text(json.dumps(streak_data))
    deps = _make_deps(state_dir=tmp_path)
    block = AutoPauseStreakAdapter().probe(deps)
    assert block.state == BlockState.CLEAR


# ── Gate 3: db_control_overrides ─────────────────────────────────────────────

def test_gate3_clear_when_no_db_gate_row(tmp_path: Path) -> None:
    from src.control.block_adapters.db_control_overrides import DbControlOverridesAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = DbControlOverridesAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate3_blocking_when_db_entries_gate_active(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from src.control.block_adapters.db_control_overrides import DbControlOverridesAdapter

    zeus_conn = _in_memory_zeus_db()
    now = datetime.now(timezone.utc).isoformat()
    zeus_conn.execute(
        """
        INSERT INTO control_overrides_history
        (override_id, target_type, target_key, action_type, value, issued_by, issued_at, reason, precedence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "control_plane:global:entries_paused",
            "global",
            "entries",
            "gate",
            "true",
            "operator",
            now,
            "test pause",
            1,
        ),
    )
    zeus_conn.commit()

    deps = _make_deps(state_dir=tmp_path, zeus_conn=zeus_conn)
    block = DbControlOverridesAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert "entries_paused" in (block.blocking_reason or "")


# ── Gate 4: entries_paused_flag ───────────────────────────────────────────────

def test_gate4_clear_when_not_paused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.control.block_adapters.entries_paused_flag import EntriesPausedFlagAdapter
    import src.control.control_plane as cp

    monkeypatch.setattr(cp, "is_entries_paused", lambda: False)
    deps = _make_deps(state_dir=tmp_path)
    block = EntriesPausedFlagAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate4_blocking_when_paused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.control.block_adapters.entries_paused_flag import EntriesPausedFlagAdapter
    import src.control.control_plane as cp

    monkeypatch.setattr(cp, "is_entries_paused", lambda: True)
    deps = _make_deps(state_dir=tmp_path)
    block = EntriesPausedFlagAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "entries_paused"


# ── Gate 5: entries_blocked_reason ────────────────────────────────────────────

def test_gate5_clear_when_no_cycle_or_null_reason(tmp_path: Path) -> None:
    from src.control.block_adapters.entries_blocked_reason import EntriesBlockedReasonAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = EntriesBlockedReasonAdapter().probe(deps)
    assert block.state == BlockState.CLEAR


def test_gate5_blocking_when_last_cycle_had_reason(tmp_path: Path) -> None:
    from src.control.block_adapters.entries_blocked_reason import EntriesBlockedReasonAdapter

    zeus_conn = _in_memory_zeus_db()
    summary = {"entries_blocked_reason": "near_max_exposure"}
    zeus_conn.execute(
        "INSERT INTO cycles (created_at, summary_json) VALUES (?, ?)",
        ("2026-05-04T10:00:00+00:00", json.dumps(summary)),
    )
    zeus_conn.commit()

    deps = _make_deps(state_dir=tmp_path, zeus_conn=zeus_conn)
    block = EntriesBlockedReasonAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "near_max_exposure"


def test_gate5_clear_when_last_cycle_has_null_reason(tmp_path: Path) -> None:
    from src.control.block_adapters.entries_blocked_reason import EntriesBlockedReasonAdapter

    zeus_conn = _in_memory_zeus_db()
    summary = {"entries_blocked_reason": None}
    zeus_conn.execute(
        "INSERT INTO cycles (created_at, summary_json) VALUES (?, ?)",
        ("2026-05-04T10:00:00+00:00", json.dumps(summary)),
    )
    zeus_conn.commit()

    deps = _make_deps(state_dir=tmp_path, zeus_conn=zeus_conn)
    block = EntriesBlockedReasonAdapter().probe(deps)
    assert block.state == BlockState.CLEAR


# ── Gate 6: risk_level ────────────────────────────────────────────────────────

def test_gate6_clear_when_risk_green(tmp_path: Path) -> None:
    from src.control.block_adapters.risk_level import RiskLevelAdapter

    risk_conn = _in_memory_risk_db()
    risk_conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        ("GREEN", "2026-05-04T10:00:00+00:00", "{}"),
    )
    risk_conn.commit()

    deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
    block = RiskLevelAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate6_blocking_when_risk_not_green(tmp_path: Path) -> None:
    from src.control.block_adapters.risk_level import RiskLevelAdapter

    for level in ("YELLOW", "ORANGE", "RED", "DATA_DEGRADED"):
        risk_conn = _in_memory_risk_db()
        risk_conn.execute(
            "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
            (level, "2026-05-04T10:00:00+00:00", "{}"),
        )
        risk_conn.commit()
        deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
        block = RiskLevelAdapter().probe(deps)
        assert block.state == BlockState.BLOCKING
        assert block.blocking_reason == f"risk_level={level}"


# ── Gate 7: trailing_loss_reference ──────────────────────────────────────────

def test_gate7_clear_when_qualifying_rows_exist(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from src.control.block_adapters.trailing_loss_reference import TrailingLossReferenceAdapter

    risk_conn = _in_memory_risk_db()
    # Insert a row older than 24h lookback window
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    risk_conn.execute(
        """
        INSERT INTO risk_state (level, checked_at, details_json)
        VALUES (?, ?, ?)
        """,
        ("GREEN", old_time, json.dumps({"bankroll_truth_source": "polymarket_wallet"})),
    )
    risk_conn.commit()

    deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
    block = TrailingLossReferenceAdapter().probe(deps)
    assert block.state == BlockState.CLEAR


def test_gate7_blocking_when_no_qualifying_rows(tmp_path: Path) -> None:
    from src.control.block_adapters.trailing_loss_reference import TrailingLossReferenceAdapter

    risk_conn = _in_memory_risk_db()
    # Empty DB
    deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
    block = TrailingLossReferenceAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "DATA_DEGRADED (risk_level=DATA_DEGRADED)"


def test_gate7_blocking_when_only_non_qualifying_rows(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from src.control.block_adapters.trailing_loss_reference import TrailingLossReferenceAdapter

    risk_conn = _in_memory_risk_db()
    # Row within the lookback window but without bankroll_truth_source
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    risk_conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        ("GREEN", old_time, json.dumps({"bankroll_truth_source": "legacy_field"})),
    )
    risk_conn.commit()

    deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
    block = TrailingLossReferenceAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING


# ── Gate 8: bankroll_truth_source ────────────────────────────────────────────

def test_gate8_clear_when_latest_row_has_polymarket_wallet(tmp_path: Path) -> None:
    from src.control.block_adapters.bankroll_truth_source import BankrollTruthSourceAdapter

    risk_conn = _in_memory_risk_db()
    risk_conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        ("GREEN", "2026-05-04T10:00:00+00:00", json.dumps({"bankroll_truth_source": "polymarket_wallet"})),
    )
    risk_conn.commit()

    deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
    block = BankrollTruthSourceAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate8_blocking_when_latest_row_lacks_field(tmp_path: Path) -> None:
    from src.control.block_adapters.bankroll_truth_source import BankrollTruthSourceAdapter

    risk_conn = _in_memory_risk_db()
    risk_conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        ("GREEN", "2026-05-04T10:00:00+00:00", json.dumps({})),
    )
    risk_conn.commit()

    deps = _make_deps(state_dir=tmp_path, risk_conn=risk_conn)
    block = BankrollTruthSourceAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert "DATA_DEGRADED" in (block.blocking_reason or "")


def test_gate8_blocking_when_empty_db(tmp_path: Path) -> None:
    from src.control.block_adapters.bankroll_truth_source import BankrollTruthSourceAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = BankrollTruthSourceAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING


# ── Gate 9: heartbeat_health ─────────────────────────────────────────────────

def test_gate9_clear_when_allow_submit_true(tmp_path: Path) -> None:
    from src.control.block_adapters.heartbeat_health import HeartbeatHealthAdapter

    deps = _make_deps(
        state_dir=tmp_path,
        heartbeat_mod=_mock_heartbeat_module(allow_submit=True, health="HEALTHY"),
    )
    block = HeartbeatHealthAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate9_blocking_when_allow_submit_false(tmp_path: Path) -> None:
    from src.control.block_adapters.heartbeat_health import HeartbeatHealthAdapter

    for health in ("DEGRADED", "LOST", "STARTING"):
        deps = _make_deps(
            state_dir=tmp_path,
            heartbeat_mod=_mock_heartbeat_module(allow_submit=False, health=health),
        )
        block = HeartbeatHealthAdapter().probe(deps)
        assert block.state == BlockState.BLOCKING
        assert block.blocking_reason == f"heartbeat={health}"


# ── Gate 10: ws_gap_guard ────────────────────────────────────────────────────

def test_gate10_clear_when_allow_submit_true(tmp_path: Path) -> None:
    from src.control.block_adapters.ws_gap_guard import WsGapGuardAdapter

    deps = _make_deps(
        state_dir=tmp_path,
        ws_gap_mod=_mock_ws_gap_guard_module(
            allow_submit=True,
            subscription_state="SUBSCRIBED",
            gap_reason="message_received",
        ),
    )
    block = WsGapGuardAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


def test_gate10_blocking_when_disconnected(tmp_path: Path) -> None:
    from src.control.block_adapters.ws_gap_guard import WsGapGuardAdapter

    deps = _make_deps(
        state_dir=tmp_path,
        ws_gap_mod=_mock_ws_gap_guard_module(
            allow_submit=False,
            subscription_state="DISCONNECTED",
            gap_reason="not_configured",
        ),
    )
    block = WsGapGuardAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "ws_gap=DISCONNECTED:not_configured"


# ── Gate 11: evaluator_rollout_gate ──────────────────────────────────────────

def test_gate11_clear_when_may_submit_true(tmp_path: Path) -> None:
    from src.control.block_adapters.evaluator_rollout_gate import EvaluatorRolloutGateAdapter

    rollout_mod = _mock_rollout_gate_module(may_submit=True, reason_codes=("ENTRY_FORECAST_LIVE_APPROVED",))
    # Create a mock evidence file so the adapter doesn't fail on missing file
    evidence_path = tmp_path / "entry_forecast_promotion_evidence.json"
    from src.data.live_entry_status import LiveEntryForecastStatus
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
    from src.config import EntryForecastRolloutMode

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-1",
        g1_evidence_id="g1-1",
        calibration_promotion_approved=True,
        canary_success_evidence_id="canary-1",
        status_snapshot=LiveEntryForecastStatus(
            status="LIVE_ELIGIBLE",
            blockers=(),
            executable_row_count=2,
            producer_readiness_count=2,
            producer_live_eligible_count=2,
        ),
    )
    write_promotion_evidence(evidence, path=evidence_path)

    deps = _make_deps(
        state_dir=tmp_path,
        rollout_mod=rollout_mod,
        env={"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "1"},
    )
    block = EvaluatorRolloutGateAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None
    assert block.stage == BlockStage.EVALUATOR


def test_gate11_blocking_when_evidence_missing(tmp_path: Path) -> None:
    from src.control.block_adapters.evaluator_rollout_gate import EvaluatorRolloutGateAdapter

    rollout_mod = _mock_rollout_gate_module(
        may_submit=False,
        reason_codes=("ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING",),
    )
    deps = _make_deps(
        state_dir=tmp_path,
        rollout_mod=rollout_mod,
        env={"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "1"},
    )
    block = EvaluatorRolloutGateAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"


def test_gate11_clear_when_gate_flag_off(tmp_path: Path) -> None:
    from src.control.block_adapters.evaluator_rollout_gate import EvaluatorRolloutGateAdapter

    deps = _make_deps(
        state_dir=tmp_path,
        env={"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "0"},
    )
    block = EvaluatorRolloutGateAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


# ── Gate 12: promotion_evidence_file ─────────────────────────────────────────

def test_gate12_blocking_when_evidence_file_absent(tmp_path: Path) -> None:
    from src.control.block_adapters.promotion_evidence_file import PromotionEvidenceFileAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = PromotionEvidenceFileAdapter().probe(deps)
    assert block.state == BlockState.BLOCKING
    assert block.blocking_reason == "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"


def test_gate12_clear_when_evidence_file_present(tmp_path: Path) -> None:
    from src.control.block_adapters.promotion_evidence_file import PromotionEvidenceFileAdapter
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
    from src.data.live_entry_status import LiveEntryForecastStatus

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-1",
        g1_evidence_id="g1-1",
        calibration_promotion_approved=True,
        canary_success_evidence_id=None,
        status_snapshot=LiveEntryForecastStatus(
            status="LIVE_ELIGIBLE",
            blockers=(),
            executable_row_count=1,
            producer_readiness_count=1,
            producer_live_eligible_count=1,
        ),
    )
    write_promotion_evidence(evidence, path=tmp_path / "entry_forecast_promotion_evidence.json")

    deps = _make_deps(state_dir=tmp_path)
    block = PromotionEvidenceFileAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None


# ── Gate 13: rollout_gate_env_var ────────────────────────────────────────────

def test_gate13_always_clear_when_gate_on(tmp_path: Path) -> None:
    from src.control.block_adapters.rollout_gate_env_var import RolloutGateEnvVarAdapter

    deps = _make_deps(state_dir=tmp_path, env={"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "1"})
    block = RolloutGateEnvVarAdapter().probe(deps)
    assert block.state == BlockState.CLEAR
    assert block.blocking_reason is None
    assert block.raw_probe["gate_on"] is True


def test_gate13_always_clear_even_when_gate_off(tmp_path: Path) -> None:
    """Per spec: gate 13 is informational only — never BLOCKING by itself."""
    from src.control.block_adapters.rollout_gate_env_var import RolloutGateEnvVarAdapter

    deps = _make_deps(state_dir=tmp_path, env={"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "0"})
    block = RolloutGateEnvVarAdapter().probe(deps)
    # Per spec: never BLOCKING by itself
    assert block.state == BlockState.CLEAR
    assert block.raw_probe["gate_on"] is False


# ── Registry-level tests ──────────────────────────────────────────────────────

def _make_registry_with_all_clear(tmp_path: Path) -> EntriesBlockRegistry:
    """Create a registry where all gates are expected to return CLEAR.

    Uses shared in-memory DBs. Each factory call returns the SAME connection
    object (in-memory DBs are identity-keyed), so adapters must NOT close it —
    but our adapters do call conn.close(). To handle this, we use
    sqlite3.connect(":memory:") + NOT closing between calls by sharing one
    object that tolerates close() being called (it's a no-op on a truly
    disconnected conn in the test context).

    Instead, we use a real temp-file DB so that close() + re-open works.
    """
    from src.control.block_adapters import ALL_ADAPTERS
    from datetime import datetime, timezone

    # Use file-backed temp DBs so factory can be called multiple times
    risk_db_path = tmp_path / "risk_state_test.db"
    zeus_db_path = tmp_path / "zeus_test.db"

    # Seed risk DB
    from datetime import timedelta as _td

    risk_conn = sqlite3.connect(str(risk_db_path))
    risk_conn.row_factory = sqlite3.Row
    risk_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            brier REAL,
            accuracy REAL,
            win_rate REAL,
            details_json TEXT,
            checked_at TEXT,
            force_exit_review INTEGER DEFAULT 0
        )
        """
    )
    # Gate 7 queries WHERE checked_at <= (now - 24h), so the row must be older than 24h.
    old_checked_at = (datetime.now(timezone.utc) - _td(hours=25)).isoformat()
    risk_conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        (
            "GREEN",
            old_checked_at,
            json.dumps({"bankroll_truth_source": "polymarket_wallet"}),
        ),
    )
    # Also add a current row for gate 6 (reads latest row regardless of age)
    risk_conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        (
            "GREEN",
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"bankroll_truth_source": "polymarket_wallet"}),
        ),
    )
    risk_conn.commit()
    risk_conn.close()

    # Seed zeus DB
    zeus_conn_setup = sqlite3.connect(str(zeus_db_path))
    zeus_conn_setup.row_factory = sqlite3.Row
    zeus_conn_setup.executescript(
        """
        CREATE TABLE IF NOT EXISTS control_overrides_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            override_id TEXT, target_type TEXT, target_key TEXT,
            action_type TEXT, value TEXT, issued_by TEXT, issued_at TEXT,
            effective_until TEXT, reason TEXT, precedence INTEGER DEFAULT 0
        );
        CREATE VIEW IF NOT EXISTS control_overrides AS
        SELECT * FROM control_overrides_history
        WHERE (override_id, issued_at) IN (
            SELECT override_id, MAX(issued_at) FROM control_overrides_history GROUP BY override_id
        );
        CREATE TABLE IF NOT EXISTS cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            summary_json TEXT NOT NULL
        );
        """
    )
    zeus_conn_setup.execute(
        "INSERT INTO cycles (created_at, summary_json) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), json.dumps({"entries_blocked_reason": None})),
    )
    zeus_conn_setup.commit()
    zeus_conn_setup.close()

    def _zeus_factory() -> sqlite3.Connection:
        c = sqlite3.connect(str(zeus_db_path))
        c.row_factory = sqlite3.Row
        return c

    def _risk_factory() -> sqlite3.Connection:
        c = sqlite3.connect(str(risk_db_path))
        c.row_factory = sqlite3.Row
        return c

    deps = RegistryDeps(
        state_dir=tmp_path,
        db_connection_factory=_zeus_factory,
        risk_state_db_connection_factory=_risk_factory,
        riskguard_module=_mock_riskguard_module(),
        heartbeat_module=_mock_heartbeat_module(allow_submit=True, health="HEALTHY"),
        ws_gap_guard_module=_mock_ws_gap_guard_module(
            allow_submit=True,
            subscription_state="SUBSCRIBED",
            gap_reason="message_received",
        ),
        rollout_gate_module=_mock_rollout_gate_module(
            may_submit=True,
            reason_codes=("ENTRY_FORECAST_LIVE_APPROVED",),
        ),
        env={"ZEUS_ENTRY_FORECAST_ROLLOUT_GATE": "1"},
    )

    registry = EntriesBlockRegistry([adapter_cls() for adapter_cls in ALL_ADAPTERS])
    registry._deps = deps
    return registry


def test_registry_enumerate_returns_13_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.control.control_plane as cp

    # Create a valid evidence file for gate 12
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
    from src.data.live_entry_status import LiveEntryForecastStatus

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-1",
        g1_evidence_id="g1-1",
        calibration_promotion_approved=True,
        canary_success_evidence_id=None,
        status_snapshot=LiveEntryForecastStatus(
            status="LIVE_ELIGIBLE",
            blockers=(),
            executable_row_count=1,
            producer_readiness_count=1,
            producer_live_eligible_count=1,
        ),
    )
    write_promotion_evidence(evidence, path=tmp_path / "entry_forecast_promotion_evidence.json")

    monkeypatch.setattr(cp, "is_entries_paused", lambda: False)

    registry = _make_registry_with_all_clear(tmp_path)
    blocks = registry.enumerate_blocks("all")
    assert len(blocks) == 13
    ids = [b.id for b in blocks]
    assert ids == list(range(1, 14))


def test_registry_is_clear_when_no_blockers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.control.control_plane as cp

    # Create valid evidence file
    from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
    from src.control.entry_forecast_promotion_evidence_io import write_promotion_evidence
    from src.data.live_entry_status import LiveEntryForecastStatus

    evidence = EntryForecastPromotionEvidence(
        operator_approval_id="op-1",
        g1_evidence_id="g1-1",
        calibration_promotion_approved=True,
        canary_success_evidence_id=None,
        status_snapshot=LiveEntryForecastStatus(
            status="LIVE_ELIGIBLE",
            blockers=(),
            executable_row_count=1,
            producer_readiness_count=1,
            producer_live_eligible_count=1,
        ),
    )
    write_promotion_evidence(evidence, path=tmp_path / "entry_forecast_promotion_evidence.json")

    monkeypatch.setattr(cp, "is_entries_paused", lambda: False)

    registry = _make_registry_with_all_clear(tmp_path)
    # Discovery-stage gates should be clear
    # (gates 11-13 are EVALUATOR stage and gate 11 needs evidence file)
    blockers_discovery = registry.blocking_blocks(BlockStage.DISCOVERY)
    # All discovery blockers should be clear
    for b in blockers_discovery:
        assert False, f"Unexpected blocker: {b.id} {b.name} {b.blocking_reason}"


def test_registry_first_blocker_priority_order(tmp_path: Path) -> None:
    """Verify FILE_FAIL_CLOSED beats DB_CONTROL_PLANE beats RISKGUARD etc."""
    from src.control.block_adapters._base import RegistryDeps as RDeps
    from src.control.entries_block_registry import Block, BlockCategory, BlockStage, BlockState

    # Create synthetic adapters that return blocking blocks at different priorities
    class FakeFileAdapter:
        id = 1
        name = "fake_file"
        category = BlockCategory.FILE_FAIL_CLOSED
        stage = BlockStage.DISCOVERY
        source_file_line = "fake:1"

        def probe(self, deps: RDeps) -> Block:
            return Block(
                id=1, name="fake_file", category=BlockCategory.FILE_FAIL_CLOSED,
                stage=BlockStage.DISCOVERY, state=BlockState.BLOCKING,
                blocking_reason="entries_paused",
                state_source="file:tombstone", source_file_line="fake:1",
                owner_module="fake", owner_function="fake",
                raw_probe={}, notes="",
            )

    class FakeRiskAdapter:
        id = 6
        name = "fake_risk"
        category = BlockCategory.RISKGUARD
        stage = BlockStage.DISCOVERY
        source_file_line = "fake:6"

        def probe(self, deps: RDeps) -> Block:
            return Block(
                id=6, name="fake_risk", category=BlockCategory.RISKGUARD,
                stage=BlockStage.DISCOVERY, state=BlockState.BLOCKING,
                blocking_reason="risk_level=RED",
                state_source="db:risk_state", source_file_line="fake:6",
                owner_module="fake", owner_function="fake",
                raw_probe={}, notes="",
            )

    registry = EntriesBlockRegistry([FakeFileAdapter(), FakeRiskAdapter()])  # type: ignore[arg-type]
    # Inject dummy deps (probe ignores them in these fakes)
    registry._deps = _make_deps(state_dir=tmp_path)  # type: ignore[assignment]

    first = registry.first_blocker(BlockStage.DISCOVERY)
    assert first is not None
    assert first.category == BlockCategory.FILE_FAIL_CLOSED
    assert first.blocking_reason == "entries_paused"


# ── ALL_ADAPTERS count check ──────────────────────────────────────────────────

def test_all_adapters_count_is_13() -> None:
    from src.control.block_adapters import ALL_ADAPTERS

    assert len(ALL_ADAPTERS) == 13
    ids = [cls().id for cls in ALL_ADAPTERS]
    assert ids == list(range(1, 14)), f"IDs must be 1-13 in order, got: {ids}"


# ── Block.to_dict serialization ───────────────────────────────────────────────

def test_block_to_dict_is_json_safe(tmp_path: Path) -> None:
    from src.control.block_adapters.fail_closed_tombstone import FailClosedTombstoneAdapter

    deps = _make_deps(state_dir=tmp_path)
    block = FailClosedTombstoneAdapter().probe(deps)
    d = block.to_dict()
    # Must be JSON-serializable
    serialized = json.dumps(d)
    parsed = json.loads(serialized)
    assert parsed["id"] == 1
    assert parsed["state"] == "clear"
    assert parsed["category"] == "file_fail_closed"


# ── Fail-closed on adapter exception ─────────────────────────────────────────

def test_adapter_exception_returns_unknown_block(tmp_path: Path) -> None:
    from src.control.block_adapters._base import RegistryDeps as RDeps

    class BrokenAdapter:
        id = 99
        name = "broken"
        category = BlockCategory.FILE_FAIL_CLOSED
        stage = BlockStage.DISCOVERY
        source_file_line = "fake:99"

        def probe(self, deps: RDeps) -> Block:
            raise RuntimeError("something went wrong")

    registry = EntriesBlockRegistry([BrokenAdapter()])  # type: ignore[arg-type]
    registry._deps = _make_deps(state_dir=tmp_path)  # type: ignore[assignment]

    blocks = registry.enumerate_blocks()
    assert len(blocks) == 1
    assert blocks[0].state == BlockState.UNKNOWN
    assert "RuntimeError" in (blocks[0].blocking_reason or "")


def test_is_clear_treats_unknown_as_blocking(tmp_path: Path) -> None:
    from src.control.block_adapters._base import RegistryDeps as RDeps

    class UnknownAdapter:
        id = 99
        name = "unknown"
        category = BlockCategory.FILE_FAIL_CLOSED
        stage = BlockStage.DISCOVERY
        source_file_line = "fake:99"

        def probe(self, deps: RDeps) -> Block:
            raise ValueError("connection error")

    registry = EntriesBlockRegistry([UnknownAdapter()])  # type: ignore[arg-type]
    registry._deps = _make_deps(state_dir=tmp_path)  # type: ignore[assignment]

    assert registry.is_clear(BlockStage.DISCOVERY) is False
