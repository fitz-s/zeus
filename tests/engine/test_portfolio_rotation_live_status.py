# Created: 2026-06-18
# Last reused/audited: 2026-06-18
# Authority basis: live redecision repair; non-actuating rotation output must not appear as live action.
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from src.engine.cycle_runtime import _emit_portfolio_rotation_live_status


class _Logger:
    def __init__(self) -> None:
        self.infos: list[tuple] = []
        self.warnings: list[tuple] = []

    def info(self, *args, **kwargs) -> None:
        self.infos.append((args, kwargs))

    def warning(self, *args, **kwargs) -> None:
        self.warnings.append((args, kwargs))


def _deps() -> SimpleNamespace:
    return SimpleNamespace(
        logger=_Logger(),
        _utcnow=lambda: datetime(2026, 6, 7, 6, 30, tzinfo=timezone.utc),
    )


def _create_main_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            bin_label TEXT,
            direction TEXT,
            shares REAL,
            last_monitor_prob REAL,
            last_monitor_market_price REAL,
            token_id TEXT,
            no_token_id TEXT,
            condition_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            position_id TEXT,
            event_type TEXT,
            occurred_at TEXT,
            payload_json TEXT
        )
        """
    )


def _create_world_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE world.no_trade_regret_events (
            event_id TEXT,
            rejection_reason TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            bin_label TEXT,
            direction TEXT,
            q_lcb_5pct REAL,
            c_fee_adjusted REAL,
            p_fill_lcb REAL,
            trade_score REAL,
            token_id TEXT,
            condition_id TEXT,
            created_at TEXT
        )
        """
    )


def test_portfolio_rotation_live_status_does_not_emit_shadow_candidate(tmp_path) -> None:
    world_path = tmp_path / "world.db"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    _create_main_schema(conn)
    _create_world_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current VALUES (
            'pos-1', 'active', 'Seoul', '2026-06-08', 'high',
            'Will the highest temperature in Seoul be 25°C on June 8?',
            'buy_no', 10.0, 0.80, 0.79, 'held-token', '', 'held-condition'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events VALUES (
            'pos-1', 'MONITOR_REFRESHED', '2026-06-07T06:20:00+00:00', ?
        )
        """,
        (json.dumps({"last_monitor_best_bid": 0.79}),),
    )
    conn.execute(
        """
        INSERT INTO world.no_trade_regret_events VALUES (
            'evt-1', 'KELLY_REJECTED:corr_budget', 'Madrid', '2026-06-08',
            'high', 'Will the highest temperature in Madrid be 34°C on June 8?',
            'buy_no', 0.88, 0.55, 1.0, 0.20, 'candidate-token',
            'candidate-condition', '2026-06-07T06:25:00+00:00'
        )
        """
    )
    summary: dict = {}

    _emit_portfolio_rotation_live_status(conn, summary, deps=_deps())

    assert summary["portfolio_rotation_live_status"] == "disabled:no_live_rotation_executor"
    assert sorted(summary) == ["portfolio_rotation_live_status"]


def test_portfolio_rotation_live_status_is_noop_without_connection() -> None:
    summary: dict = {}

    _emit_portfolio_rotation_live_status(None, summary, deps=_deps())

    assert summary["portfolio_rotation_live_status"] == "unavailable:no_connection"
