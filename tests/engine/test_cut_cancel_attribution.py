# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: cut-cancel throughput task (2026-09-29): every cancelled
#   global auction cut names its cancel source and stage.
"""Every cancelled cut is attributed: source and stage on the row and in one log line."""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import logging
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

import src.engine.global_batch_runtime as gbr
from src.engine import global_auction_universe as universe
from src.engine import tier0_auction_corpus as corpus
from src.events.opportunity_event import OpportunityEvent
from src.state.schema.tier0_auction_corpus_schema import ensure_tables

AT = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _empty_queue():
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()
    yield
    corpus._PENDING.clear()
    corpus._OVERFLOW.clear()


def _event() -> OpportunityEvent:
    payload = {
        "city": "Alpha",
        "target_date": "2026-07-11",
        "metric": "high",
        "source_run_id": "run-a",
    }
    return OpportunityEvent(
        event_id="event-alpha",
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Alpha|2026-07-11|high",
        source="test",
        observed_at=AT.isoformat(),
        available_at=AT.isoformat(),
        received_at=AT.isoformat(),
        causal_snapshot_id="snapshot-alpha",
        payload_hash="hash-alpha",
        idempotency_key="idem-alpha",
        priority=0,
        expires_at=None,
        payload_json=json.dumps(payload),
        schema_version=1,
        created_at=AT.isoformat(),
    )


def _run(trade_conn, **kwargs):
    return gbr.process_current_global_batch(
        (_event(),),
        decision_time=AT,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade_conn,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: pytest.fail("a cancelled cut prepares nothing"),
        actuate_winner=lambda *_: pytest.fail("a cancelled cut never actuates"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: AT,
        **kwargs,
    )


def _queued_cut_row(trade_conn) -> dict:
    """The one cut this batch recorded: still queued, or flushed to the table."""

    queued, _overflow = corpus.pending_cuts(gbr._decision_log_connection_key(trade_conn))
    if queued:
        assert len(queued) == 1
        built, _candidates = queued[0].rows()
        return dict(zip(corpus._CUT_COLUMNS, built.cut_row))
    trade_conn.row_factory = sqlite3.Row
    rows = trade_conn.execute("SELECT * FROM tier0_auction_cut").fetchall()
    assert len(rows) == 1
    return dict(rows[0])


def test_probe_label_names_the_source_and_bare_true_is_unattributed():
    assert universe.cancel_source("day0_hard_fact:in_scope") == "day0_hard_fact:in_scope"
    assert universe.cancel_source(True) == "unattributed"
    assert universe.cancel_source("  ") == "unattributed"


def test_work_checkpoint_carries_the_probe_label_into_work_deferred():
    context = universe.WorkContext(
        deadline_monotonic=None, cancel_requested=lambda: "monitor_handoff"
    )
    with pytest.raises(universe.WorkDeferred) as caught:
        context.checkpoint("prepare_family:alpha")
    assert caught.value.source == "monitor_handoff"
    assert caught.value.stage == "prepare_family:alpha"
    expired = universe.WorkContext(deadline_monotonic=0.0, monotonic=lambda: 1.0)
    with pytest.raises(universe.WorkDeferred) as caught:
        expired.checkpoint("book_fetch")
    assert caught.value.source == "deadline"


def test_scope_scan_cancel_is_written_to_the_cut_row_and_logged_once(
    tmp_path, monkeypatch, caplog
):
    fired = [False]

    def preempted_scope(**kwargs):
        fired[0] = True  # the Day0 fact lands while the scan runs
        requested = kwargs["cancelled"]()
        raise universe.GlobalAuctionScopeCancelled(
            "GLOBAL_SELECTION_CANCELLED", source=universe.cancel_source(requested)
        )

    monkeypatch.setattr(gbr, "scan_current_global_auction_scope", preempted_scope)
    trade = sqlite3.connect(tmp_path / "trade.db")
    caplog.set_level(logging.INFO, logger=gbr._LOG.name)

    result = _run(
        trade,
        selection_cancelled=lambda: fired[0] and "day0_hard_fact:scope_unknown",
    )

    assert result.receipts["event-alpha"].reason == (
        "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"
    )
    row = _queued_cut_row(trade)
    assert row["status"] == "INCOMPLETE"
    assert row["cancel_source"] == "day0_hard_fact:scope_unknown"
    assert row["cancel_stage"] == "scope_scan"
    lines = [r.getMessage() for r in caplog.records if "cut cancelled" in r.getMessage()]
    assert len(lines) == 1
    assert "source=day0_hard_fact:scope_unknown stage=scope_scan" in lines[0]


def test_supersession_is_attributed_to_the_superseding_fact(tmp_path, monkeypatch):
    from types import SimpleNamespace

    scope = SimpleNamespace(events_by_family=(), family_keys=())
    monkeypatch.setattr(gbr, "scan_current_global_auction_scope", lambda **_: scope)
    trade = sqlite3.connect(tmp_path / "trade.db")

    result = _run(trade, epoch_superseded=lambda: "wake:forecast_posterior_advanced")

    assert result.receipts["event-alpha"].reason == (
        "GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT"
    )
    row = _queued_cut_row(trade)
    assert row["cancel_source"] == "epoch_superseded:wake:forecast_posterior_advanced"


def test_uncancelled_cut_row_carries_no_cancel_attribution(tmp_path):
    trade = sqlite3.connect(tmp_path / "trade.db")
    gbr._queue_unreceipted_tier0_cut(
        trade,
        reason="GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY",
        decision_at_utc=AT,
        economic_cut_completed=False,
        event_count=1,
        fractional_kelly_multiplier=Decimal("0.25"),
        buy_candidates_enabled=True,
        cancel_source="must-not-land",
        cancel_stage="must-not-land",
    )
    row = _queued_cut_row(trade)
    assert row["status"] == "NO_CANDIDATES"
    assert row["cancel_source"] is None and row["cancel_stage"] is None


def test_cancel_columns_are_added_to_a_live_table_and_written(tmp_path):
    conn = sqlite3.connect(tmp_path / "trade.db")
    # A live table created before the migration lacks both columns.
    conn.execute(
        """
        CREATE TABLE tier0_auction_cut (
            cut_seq INTEGER PRIMARY KEY, cut_id TEXT NOT NULL UNIQUE,
            selection_epoch_identity TEXT, status TEXT NOT NULL, reason TEXT,
            decision_at_utc TEXT NOT NULL, selection_policy_identity TEXT NOT NULL,
            full_scope_family_count INTEGER NOT NULL,
            eligible_family_count INTEGER NOT NULL, candidate_count INTEGER NOT NULL,
            winner_candidate_id TEXT, decision_log_id INTEGER,
            payload_encoding TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            payload BLOB NOT NULL, created_at TEXT NOT NULL
        )
        """
    )
    ensure_tables(conn)
    ensure_tables(conn)  # idempotent
    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(tier0_auction_cut)")}
    assert {"cancel_source", "cancel_stage"} <= columns
    corpus.write_cut(
        conn,
        corpus.build_unreceipted_cut(
            reason="DEFERRED_PREEMPTED",
            decision_at_utc=AT,
            selection_policy_identity="policy",
            economic_cut_completed=False,
            detail={},
            cancel_source="monitor_handoff",
            cancel_stage="selection_snapshot:fence_wait",
        ),
        decision_log_id=None,
    )
    assert conn.execute(
        "SELECT cancel_source, cancel_stage FROM tier0_auction_cut"
    ).fetchone() == ("monitor_handoff", "selection_snapshot:fence_wait")


def test_restart_schema_materialization_adds_the_cancel_columns(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "deploy_live_cut_cancel_attribution", _ROOT / "scripts" / "deploy_live.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    conn = sqlite3.connect(tmp_path / "trade.db", isolation_level=None)
    module._ensure_restart_trade_schemas(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(tier0_auction_cut)")}
    assert {"cancel_source", "cancel_stage"} <= columns
