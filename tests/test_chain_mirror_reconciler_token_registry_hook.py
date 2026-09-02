# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Attack F
#   ("/positions 漏 token = 幻零仓 -> durable token registry ... /positions only
#   does discovery"); src/state/chain_mirror_reconciler.py::run_cycle
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: proves the LX-T2-a discovery hook wired into run_cycle registers
#   every token a data-api /positions read reports, and that a registry write
#   failure never aborts the reconcile pass that already has fresh chain facts.

"""Tests for the ctf_token_registry discovery hook in chain_mirror_reconciler.run_cycle."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.state.chain_mirror_reconciler import MirrorFinding, ReconcileReport
from src.state.ctf_token_registry import get_token_registry_row
from src.state.db import init_schema, init_schema_trade_only


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_positions_from_api(self):
        return [
            {
                "token_id": "tokY",
                "condition_id": "cond1",
                "size": 10.0,
                "redeemable": False,
                "current_value": 5.0,
                "side": "BUY",
                "title": "yes",
            },
            {
                "token_id": "tokZ",
                "condition_id": "cond2",
                "size": 3.0,
                "redeemable": False,
                "current_value": 1.5,
                "side": "BUY",
                "title": "yes",
            },
        ]


@pytest.fixture
def trades_db_path(tmp_path):
    path = tmp_path / "trades.db"
    conn = sqlite3.connect(str(path))
    init_schema(conn)
    init_schema_trade_only(conn)
    conn.close()
    return path


def _patch_common(monkeypatch, trades_db_path):
    monkeypatch.setattr("src.config.get_mode", lambda: "live")
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _FakeClient)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda **_kw: sqlite3.connect(str(trades_db_path)),
    )
    events = []

    def _bootstrap_writer(**kwargs):
        events.append(("bootstrap", kwargs))
        return sqlite3.connect(str(trades_db_path))

    monkeypatch.setattr("src.state.db.get_trade_connection", _bootstrap_writer)

    transactions = []

    class _Coordinator:
        def lease(self, dbs, **kwargs):
            transactions.append((tuple(dbs), kwargs))

            @contextmanager
            def _lease():
                events.append(("lease", kwargs))
                yield SimpleNamespace(
                    acquired_at=time.monotonic(),
                    owner=kwargs["owner"],
                    record_commit=lambda **_kw: None,
                )

            return _lease()

    monkeypatch.setattr(
        "src.state.chain_mirror_reconciler._coordinator_for_trade_connection",
        lambda _conn: _Coordinator(),
    )

    def _no_forecasts(*_a, **_kw):
        raise RuntimeError("forecasts unavailable in this test")

    monkeypatch.setattr("src.state.db.get_forecasts_connection_read_only", _no_forecasts)
    return transactions, events


def test_run_cycle_registers_every_positions_token(monkeypatch, trades_db_path):
    from src.state.chain_mirror_reconciler import run_cycle

    transactions, events = _patch_common(monkeypatch, trades_db_path)

    run_cycle()

    conn = sqlite3.connect(str(trades_db_path))
    try:
        tok_y = get_token_registry_row(conn, token_id="tokY")
        tok_z = get_token_registry_row(conn, token_id="tokZ")
    finally:
        conn.close()

    assert tok_y is not None
    assert tok_y.condition_id == "cond1"
    assert tok_y.first_source == "positions_api_discovery"
    assert tok_z is not None
    assert tok_z.condition_id == "cond2"
    assert tok_z.first_source == "positions_api_discovery"
    assert [entry[1]["owner"] for entry in transactions] == [
        "chain_mirror_token_registry",
        "chain_mirror_token_registry",
    ]
    assert all(entry[1]["priority"].value == "background_recovery" for entry in transactions)
    assert all(entry[1]["deadline_ms"] == 250 for entry in transactions)
    assert all(entry[1]["max_hold_ms"] == 250 for entry in transactions)
    assert [kind for kind, _kwargs in events] == [
        "bootstrap", "lease", "bootstrap", "lease"
    ]


def test_run_cycle_survives_registry_write_failure(monkeypatch, trades_db_path):
    """A registry write failure must never abort the reconcile pass (best-effort)."""
    from src.state.chain_mirror_reconciler import run_cycle

    _patch_common(monkeypatch, trades_db_path)

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated ctf_token_registry write failure")

    monkeypatch.setattr("src.state.ctf_token_registry.record_token_seen", _boom)

    # Must not raise.
    run_cycle()


def test_run_cycle_registers_tokens_already_confirmed_on_rerun(monkeypatch, trades_db_path):
    from src.state.chain_mirror_reconciler import run_cycle

    _patch_common(monkeypatch, trades_db_path)

    run_cycle()
    run_cycle()

    conn = sqlite3.connect(str(trades_db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM ctf_token_registry").fetchone()[0]
    finally:
        conn.close()

    # Never duplicates a row for a token already registered.
    assert count == 2


def test_operator_apply_promotes_only_exact_findings_through_position_api(monkeypatch):
    """--apply never reuses its full-book read connection for canonical writes."""
    from scripts import reconcile_chain_mirror as operator

    class _ReadConnection:
        row_factory = None

        def close(self):
            pass

    report = ReconcileReport(
        generated_at="2026-08-28T00:00:00+00:00",
        dry_run=True,
        findings=[
            MirrorFinding(
                classification="size_corrected",
                position_id="position-a",
                asset="token-a",
                writes=True,
            ),
            MirrorFinding(
                classification="foreign",
                position_id=None,
                asset="token-foreign",
                writes=False,
            ),
        ],
    )
    monkeypatch.setattr(operator, "get_trade_connection_read_only", _ReadConnection)
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        lambda: SimpleNamespace(get_positions_from_api=lambda: []),
    )
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: _ReadConnection(),
    )
    monkeypatch.setattr(operator, "load_settlement_lookup", lambda _conn: {})
    observed_apply_flags = []

    def _classify(*_args, **kwargs):
        observed_apply_flags.append(kwargs["apply"])
        return report

    calls = []

    def _apply(position_id, **kwargs):
        calls.append((position_id, kwargs))
        return ReconcileReport(
            generated_at=report.generated_at,
            dry_run=False,
            applied=1,
        )

    monkeypatch.setattr(operator, "reconcile", _classify)
    monkeypatch.setattr(operator, "apply_reconcile_position", _apply)

    result = operator.run(apply=True)

    assert observed_apply_flags == [False]
    assert [position_id for position_id, _kwargs in calls] == ["position-a"]
    assert calls[0][1]["owner"] == "chain_mirror_operator_apply_position"
    assert result["applied"] == 1
    assert result["dry_run"] is False


def test_fallback_size_correction_uses_the_same_bounded_write_seam(
    monkeypatch, trades_db_path
):
    import src.state.chain_mirror_reconciler as mirror

    transactions, events = _patch_common(monkeypatch, trades_db_path)
    finding = MirrorFinding(
        classification="size_corrected",
        position_id="fallback-position",
        asset="fallback-token",
        writes=True,
        details={"chain_size": 1.0},
    )
    observed = []

    def _apply(conn, passed_finding, *, now):
        observed.append((conn, passed_finding, now))
        return True

    monkeypatch.setattr(mirror, "apply_size_correction_finding", _apply)

    assert mirror.apply_size_correction_finding_coordinated(
        finding,
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    ) is True
    assert len(observed) == 1
    assert transactions[0][1]["owner"] == "chain_mirror_size_correction_fallback"
    assert [kind for kind, _kwargs in events] == ["bootstrap", "lease"]


def test_run_cycle_continues_after_one_position_apply_quantum_fails(
    monkeypatch, trades_db_path
):
    import src.state.chain_mirror_reconciler as mirror

    _patch_common(monkeypatch, trades_db_path)
    initial = ReconcileReport(
        generated_at="2026-08-28T00:00:00+00:00",
        dry_run=True,
        findings=[
            MirrorFinding("size_corrected", "first", "tokY", True),
            MirrorFinding("size_corrected", "second", "tokZ", True),
        ],
    )
    monkeypatch.setattr(mirror, "reconcile", lambda *_args, **_kwargs: initial)
    calls = []

    def _apply(position_id, **_kwargs):
        calls.append(position_id)
        if position_id == "first":
            raise RuntimeError("first quantum rolled back")
        return ReconcileReport(
            generated_at=initial.generated_at,
            dry_run=False,
            applied=1,
        )

    monkeypatch.setattr(mirror, "apply_reconcile_position", _apply)

    mirror.run_cycle()

    assert calls == ["first", "second"]


def test_background_chain_mirror_write_chunk_releases_for_monitor_waiter(trades_db_path):
    """A paused chain-mirror chunk cannot overtake a MONITOR after it releases."""
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    coordinator = WriteCoordinator({DBIdentity.TRADE: trades_db_path})
    chunk_open = threading.Event()
    release_chunk = threading.Event()
    monitor_acquired = threading.Event()
    errors = []

    def _chain_mirror_writer():
        try:
            with coordinator.transaction(
                (DBIdentity.TRADE,),
                owner="chain_mirror_reconcile_position",
                priority=WritePriority.BACKGROUND_RECOVERY,
                deadline_ms=250,
                max_hold_ms=250,
            ) as transaction:
                transaction.connection.execute("UPDATE position_current SET updated_at = updated_at WHERE 0")
                chunk_open.set()
                assert release_chunk.wait(timeout=2)
        except BaseException as exc:
            errors.append(exc)

    def _monitor_writer():
        try:
            assert chunk_open.wait(timeout=2)
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="monitor_canonical_append",
                priority=WritePriority.MONITOR,
                deadline_ms=250,
                max_hold_ms=250,
            ):
                monitor_acquired.set()
        except BaseException as exc:
            errors.append(exc)

    chain_thread = threading.Thread(target=_chain_mirror_writer)
    monitor_thread = threading.Thread(target=_monitor_writer)
    chain_thread.start()
    assert chunk_open.wait(timeout=2)
    monitor_thread.start()
    assert not monitor_acquired.wait(timeout=0.05)
    release_chunk.set()
    chain_thread.join(timeout=2)
    monitor_thread.join(timeout=2)
    assert not chain_thread.is_alive()
    assert not monitor_thread.is_alive()
    assert not errors
    assert monitor_acquired.is_set()
