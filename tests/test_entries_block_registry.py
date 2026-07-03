# Created: 2026-05-04
# Last reused/audited: 2026-06-28
"""Tests for the runtime entry-block registry.

The registry is an operator snapshot of live entry blockers. It must not carry
retired gates or informational-only probes that are always CLEAR.
"""

from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
    EntriesBlockRegistry,
)


def _risk_db(path: Path, *, level: str = "GREEN") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
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
    conn.execute(
        "INSERT INTO risk_state (level, checked_at, details_json) VALUES (?, ?, ?)",
        (level, "2026-05-04T10:00:00+00:00", json.dumps({"bankroll_truth_source": "polymarket_wallet"})),
    )
    conn.commit()
    return conn


def _world_db(path: Path, *, entries_paused: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
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
        );
        CREATE VIEW IF NOT EXISTS control_overrides AS
        SELECT * FROM control_overrides_history
        WHERE (override_id, issued_at) IN (
            SELECT override_id, MAX(issued_at)
            FROM control_overrides_history
            GROUP BY override_id
        );
        """
    )
    if entries_paused:
        conn.execute(
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
                "2026-05-04T10:00:00+00:00",
                "test pause",
                1,
            ),
        )
    conn.commit()
    return conn


def _module_summary(summary: dict) -> types.ModuleType:
    mod = types.ModuleType("summary_mod")
    mod.summary = lambda: summary  # type: ignore[attr-defined]
    return mod


def _deps(
    tmp_path: Path,
    *,
    risk_level: str = "GREEN",
    entries_paused: bool = False,
    heartbeat_allow: bool = True,
    heartbeat_health: str = "HEALTHY",
    ws_allow: bool = True,
    ws_state: str = "SUBSCRIBED",
) -> RegistryDeps:
    world_path = tmp_path / "world.db"
    risk_path = tmp_path / "risk.db"
    _world_db(world_path, entries_paused=entries_paused).close()
    _risk_db(risk_path, level=risk_level).close()

    def _world_factory() -> sqlite3.Connection:
        conn = sqlite3.connect(str(world_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _risk_factory() -> sqlite3.Connection:
        conn = sqlite3.connect(str(risk_path))
        conn.row_factory = sqlite3.Row
        return conn

    return RegistryDeps(
        db_connection_factory=_world_factory,
        risk_state_db_connection_factory=_risk_factory,
        heartbeat_module=_module_summary({"health": heartbeat_health, "entry": {"allow_submit": heartbeat_allow}}),
        ws_gap_guard_module=_module_summary(
            {"subscription_state": ws_state, "gap_reason": "test", "entry": {"allow_submit": ws_allow}}
        ),
    )


def test_all_adapters_are_current_runtime_blockers_only() -> None:
    from src.control.block_adapters import ALL_ADAPTERS

    names = [cls().name for cls in ALL_ADAPTERS]
    assert names == [
        "control_overrides_history_entries_gate",
        "risk_allows_new_entries_predicate",
        "heartbeat_supervisor_allow_submit",
        "ws_gap_guard_allow_submit",
    ]


def test_registry_enumerates_current_blockers(tmp_path: Path) -> None:
    registry = EntriesBlockRegistry.from_runtime(_deps(tmp_path))
    blocks = registry.enumerate_blocks("all")

    assert len(blocks) == 4
    assert [b.id for b in blocks] == [3, 6, 9, 10]
    assert registry.is_clear(BlockStage.DISCOVERY) is True


def test_db_pause_override_blocks(tmp_path: Path) -> None:
    registry = EntriesBlockRegistry.from_runtime(_deps(tmp_path, entries_paused=True))
    first = registry.first_blocker(BlockStage.DISCOVERY)

    assert first is not None
    assert first.name == "control_overrides_history_entries_gate"
    assert first.state == BlockState.BLOCKING
    assert first.blocking_reason == "entries_paused (from DB gate row)"


def test_risk_level_blocks_when_not_green(tmp_path: Path) -> None:
    registry = EntriesBlockRegistry.from_runtime(_deps(tmp_path, risk_level="RED"))
    blockers = {b.name: b for b in registry.blocking_blocks(BlockStage.DISCOVERY)}

    assert blockers["risk_allows_new_entries_predicate"].blocking_reason == "risk_level=RED"


def test_runtime_health_blocks_are_visible(tmp_path: Path) -> None:
    registry = EntriesBlockRegistry.from_runtime(
        _deps(
            tmp_path,
            heartbeat_allow=False,
            heartbeat_health="LOST",
            ws_allow=False,
            ws_state="DISCONNECTED",
        )
    )
    blockers = {b.name: b for b in registry.blocking_blocks(BlockStage.DISCOVERY)}

    assert blockers["heartbeat_supervisor_allow_submit"].blocking_reason == "heartbeat=LOST"
    assert blockers["ws_gap_guard_allow_submit"].blocking_reason == "ws_gap=DISCONNECTED:test"


def test_block_to_dict_is_json_safe() -> None:
    block = Block(
        id=3,
        name="control_overrides_history_entries_gate",
        category=BlockCategory.DB_CONTROL_PLANE,
        stage=BlockStage.DISCOVERY,
        state=BlockState.CLEAR,
        blocking_reason=None,
        state_source="db:control_overrides_history",
        source_file_line="src/state/db.py",
        owner_module="src.state.db",
        owner_function="query_control_override_state",
        raw_probe={"entries_paused": False},
        notes="test",
    )

    parsed = json.loads(json.dumps(block.to_dict()))
    assert parsed["id"] == 3
    assert parsed["state"] == "clear"
    assert parsed["category"] == "db_control_plane"


def test_adapter_exception_returns_unknown_block(tmp_path: Path) -> None:
    from src.control.block_adapters._base import RegistryDeps as RDeps

    class BrokenAdapter:
        id = 99
        name = "broken"
        category = BlockCategory.DB_CONTROL_PLANE
        stage = BlockStage.DISCOVERY
        source_file_line = "fake:99"

        def probe(self, deps: RDeps) -> Block:
            raise RuntimeError("something went wrong")

    registry = EntriesBlockRegistry([BrokenAdapter()])  # type: ignore[arg-type]
    registry._deps = _deps(tmp_path)  # type: ignore[assignment]

    blocks = registry.enumerate_blocks()
    assert len(blocks) == 1
    assert blocks[0].state == BlockState.UNKNOWN
    assert "RuntimeError" in (blocks[0].blocking_reason or "")
    assert registry.is_clear(BlockStage.DISCOVERY) is False
