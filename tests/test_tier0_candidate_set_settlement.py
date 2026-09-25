# Created: 2026-08-27
# Last reused/audited: 2026-09-25
# Authority basis: reversal_plan_tier0_2026-08-24 items 3 and 7;
#   tier0_selection_lift_preregistration_2026-08-24 frozen data contract;
#   2026-09-25 fold write-lock hold (read-only diff, coordinated CAS writes).
"""Tier-0 candidate sets receive exact canonical settlement labels."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import src.execution.post_trade_capital as ptc
from src.execution.post_trade_capital import (
    _apply_tier0_candidate_label_changes,
    _tier0_candidate_label_changes,
    _tier0_candidate_settlement_labels,
)
from src.state import write_coordinator
from src.state.write_coordinator import DBIdentity, WriteCoordinator

_ROOT = Path(__file__).resolve().parent.parent


def _forecast_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE market_events (
            condition_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, range_low REAL, range_high REAL
        );
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT, authority TEXT
        );
        """
    )
    return conn


def _trade_db(tmp_path: Path, rows) -> Path:
    path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE tier0_candidate_set_provenance (
            row_id INTEGER PRIMARY KEY, settled_y INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO tier0_candidate_set_provenance VALUES (?,?)", rows
    )
    conn.commit()
    conn.close()
    return path


def _settled(path: Path) -> list[tuple[int, int | None]]:
    conn = sqlite3.connect(path)
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT row_id, settled_y "
                "FROM tier0_candidate_set_provenance ORDER BY row_id"
            )
        ]
    finally:
        conn.close()


class _RecordingCoordinator(WriteCoordinator):
    """Real coordinator on the fixture DB that counts write transactions."""

    def __init__(self, path: Path) -> None:
        super().__init__({DBIdentity.TRADE: path})
        self.transactions = 0

    def transaction(self, *args, **kwargs):
        self.transactions += 1
        return super().transaction(*args, **kwargs)


@pytest.fixture
def fold(monkeypatch, tmp_path):
    """Run the production fold against a file-backed trade DB.

    Read-only connections are genuine ``mode=ro``; the only write path is the
    real coordinator transaction, which this fixture counts.
    """

    rows = [(1, None), (2, 0), (3, 1)]
    path = _trade_db(tmp_path, rows)
    coordinator = _RecordingCoordinator(path)
    candidates = [
        {
            "row_id": row_id,
            "market_key": f"m{row_id}",
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": "YES",
        }
        for row_id, _ in rows
    ]
    labels = {"value": [(1, 1), (2, 0), (3, 0)]}

    def _read_only():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _load(conn):
        return [
            {**candidate, "settled_y": settled_y}
            for candidate, (_row_id, settled_y) in zip(
                candidates, conn.execute(
                    "SELECT row_id, settled_y "
                    "FROM tier0_candidate_set_provenance ORDER BY row_id"
                ).fetchall()
            )
        ]

    def _connect_existing(db_path):
        conn = sqlite3.connect(db_path, isolation_level="")
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("src.state.db.get_trade_connection_read_only", _read_only)
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(
        "src.state.db.connect_existing_trade_db_without_journal_bootstrap",
        _connect_existing,
    )
    monkeypatch.setattr(
        "src.state.db.get_trade_connection",
        lambda **_kwargs: pytest.fail("the fold must not open a raw writer"),
    )
    monkeypatch.setattr(ptc, "_load_tier0_candidate_rows", _load)
    monkeypatch.setattr(
        ptc,
        "_tier0_candidate_settlement_labels",
        lambda _conn, loaded: (list(labels["value"]), {"candidate_rows": len(loaded)}),
    )
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: coordinator,
    )
    return type(
        "Fold",
        (),
        {"path": path, "coordinator": coordinator, "labels": labels},
    )


def test_labels_use_verified_point_range_and_shoulder_bounds_for_both_sides():
    conn = _forecast_conn()
    conn.executemany(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        (
            ("point", "Taipei", "2026-08-26", "high", 30.0, 30.0),
            ("below", "Taipei", "2026-08-26", "high", None, 29.0),
            ("above", "Taipei", "2026-08-26", "high", 31.0, None),
        ),
    )
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Taipei", "2026-08-26", "high", 30.0, "C", "VERIFIED"),
    )
    candidates = [
        {
            "row_id": row_id,
            "market_key": market,
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": side,
        }
        for row_id, market, side in (
            (1, "point", "YES"),
            (2, "point", "NO"),
            (3, "below", "YES"),
            (4, "below", "NO"),
            (5, "above", "YES"),
            (6, "above", "NO"),
        )
    ]

    labels, stats = _tier0_candidate_settlement_labels(conn, candidates)

    assert labels == [(1, 1), (2, 0), (3, 0), (4, 1), (5, 0), (6, 1)]
    assert stats == {
        "candidate_rows": 6,
        "verified_market_labels": 3,
        "labels_ready": 6,
        "pending_rows": 0,
        "ambiguous_markets": 0,
        "invalid_truth_rows": 0,
        "invalid_candidate_rows": 0,
    }

    fahrenheit = _forecast_conn()
    fahrenheit.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("range", "Austin", "2026-08-26", "high", 64.0, 65.0),
    )
    fahrenheit.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        ("Austin", "2026-08-26", "high", 65.0, "F", "VERIFIED"),
    )
    range_candidates = [
        {
            "row_id": row_id,
            "market_key": "range",
            "city": "Austin",
            "target_date": "2026-08-26",
            "side": side,
        }
        for row_id, side in ((7, "YES"), (8, "NO"))
    ]

    labels, stats = _tier0_candidate_settlement_labels(
        fahrenheit,
        range_candidates,
    )

    assert labels == [(7, 1), (8, 0)]
    assert stats["verified_market_labels"] == 1
    assert stats["invalid_truth_rows"] == 0


def test_unverified_or_unit_inconsistent_truth_never_labels_a_candidate():
    conn = _forecast_conn()
    conn.executemany(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        (
            ("unverified", "Taipei", "2026-08-26", "high", 30.0, 30.0),
            ("wrong-unit", "Taipei", "2026-08-26", "low", 25.0, 25.0),
        ),
    )
    conn.executemany(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?)",
        (
            ("Taipei", "2026-08-26", "high", 30.0, "C", "UNVERIFIED"),
            ("Taipei", "2026-08-26", "low", 25.0, "F", "VERIFIED"),
        ),
    )
    candidates = [
        {
            "row_id": 1,
            "market_key": "unverified",
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": "YES",
        },
        {
            "row_id": 2,
            "market_key": "wrong-unit",
            "city": "Taipei",
            "target_date": "2026-08-26",
            "side": "YES",
        },
    ]

    labels, stats = _tier0_candidate_settlement_labels(conn, candidates)

    assert labels == []
    assert stats["pending_rows"] == 2
    assert stats["invalid_truth_rows"] == 1


def test_label_diff_is_computed_from_the_read_only_snapshot():
    candidates = [
        {"row_id": 1, "settled_y": None},
        {"row_id": 2, "settled_y": 0},
        {"row_id": 3, "settled_y": 1},
    ]

    assert _tier0_candidate_label_changes(
        candidates, ((1, 1), (2, 0), (3, 0))
    ) == [(1, None, 1), (3, 1, 0)]


def test_changed_rows_fill_and_correct_and_refold_is_idempotent(fold):
    stats = ptc.run_tier0_candidate_settlement_fold()

    assert stats == {
        "candidate_rows": 3,
        "unchanged": 1,
        "filled": 1,
        "corrected": 1,
        "cas_lost": 0,
    }
    assert _settled(fold.path) == [(1, 1), (2, 0), (3, 0)]
    assert fold.coordinator.transactions == 1

    again = ptc.run_tier0_candidate_settlement_fold()

    assert again["unchanged"] == 3
    assert again["filled"] == again["corrected"] == again["cas_lost"] == 0
    assert fold.coordinator.transactions == 1


def test_unchanged_fold_takes_no_write_transaction(fold):
    """The live incident: 132,451 unchanged labels held the writer 170 s."""

    # Row 1 is still pending (no VERIFIED truth), so it carries no label.
    fold.labels["value"] = [(2, 0), (3, 1)]

    stats = ptc.run_tier0_candidate_settlement_fold()

    assert stats["unchanged"] == 2
    assert stats["filled"] == stats["corrected"] == stats["cas_lost"] == 0
    assert fold.coordinator.transactions == 0


def test_diff_runs_while_another_writer_holds_the_lock(fold):
    """Reads never queue behind the writer: the diff completes under a held lock."""

    blocker = sqlite3.connect(fold.path, isolation_level=None, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        fold.labels["value"] = [(2, 0), (3, 1)]
        stats = ptc.run_tier0_candidate_settlement_fold()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert stats["unchanged"] == 2
    assert fold.coordinator.transactions == 0


def test_concurrent_change_between_read_and_write_is_not_clobbered(tmp_path, monkeypatch):
    path = _trade_db(tmp_path, [(1, None), (2, 0)])
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: WriteCoordinator({DBIdentity.TRADE: path}),
    )
    monkeypatch.setattr(
        "src.state.db.connect_existing_trade_db_without_journal_bootstrap",
        lambda db_path: sqlite3.connect(db_path, isolation_level=""),
    )
    # Snapshot saw row 1 NULL and row 2 at 0; row 1 was labelled 0 since.
    concurrent = sqlite3.connect(path)
    concurrent.execute(
        "UPDATE tier0_candidate_set_provenance SET settled_y = 0 WHERE row_id = 1"
    )
    concurrent.commit()
    concurrent.close()

    stats = _apply_tier0_candidate_label_changes([(1, None, 1), (2, 0, 1)])

    assert stats == {"filled": 0, "corrected": 1, "cas_lost": 1}
    assert _settled(path) == [(1, 0), (2, 1)]


def test_label_writes_are_chunked_into_bounded_transactions(fold, monkeypatch):
    monkeypatch.setattr(ptc, "_TIER0_LABEL_WRITE_CHUNK", 1)

    stats = ptc.run_tier0_candidate_settlement_fold()

    assert stats["filled"] + stats["corrected"] == 2
    assert fold.coordinator.transactions == 2


def test_post_trade_daemon_runs_fold_every_five_minutes_after_harvester():
    source = (_ROOT / "src/ingest/post_trade_capital_daemon.py").read_text()
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        job_id = keywords.get("id")
        if isinstance(job_id, ast.Constant) and job_id.value == (
            "tier0_candidate_settlement_fold"
        ):
            calls.append(keywords)
    assert len(calls) == 1
    keywords = calls[0]
    assert isinstance(keywords["minutes"], ast.Constant)
    assert keywords["minutes"].value == 5
    assert isinstance(keywords["max_instances"], ast.Constant)
    assert keywords["max_instances"].value == 1
    assert isinstance(keywords["coalesce"], ast.Constant)
    assert keywords["coalesce"].value is True
    assert "next_run_time" in keywords
