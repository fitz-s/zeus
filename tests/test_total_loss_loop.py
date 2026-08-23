# Lifecycle: created=2026-08-22; last_reviewed=2026-08-23; last_reused=2026-08-23
# Purpose: Relationship antibodies for event-time total-loss detection and evidence isolation.
# Reuse: Run whenever detector timing, exposure lifecycle, quote persistence, or Codex orchestration changes.
"""Relationship antibodies for the event-time total-loss loop."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("total_loss_loop", ROOT / "total_loss_loop.py")
assert SPEC and SPEC.loader
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def _trade_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY, phase TEXT, trade_id TEXT,
                market_id TEXT, city TEXT, target_date TEXT, bin_label TEXT,
                direction TEXT, unit TEXT, shares REAL, chain_shares REAL,
                cost_basis_usd REAL, realized_pnl_usd REAL, entry_price REAL,
                token_id TEXT, no_token_id TEXT, condition_id TEXT,
                settled_at TEXT, updated_at TEXT, temperature_metric TEXT
            );
            CREATE TABLE execution_feasibility_evidence (
                evidence_id TEXT PRIMARY KEY, event_id TEXT, condition_id TEXT,
                token_id TEXT, outcome_label TEXT, direction TEXT,
                quote_seen_at TEXT, book_hash_before TEXT,
                best_bid_before REAL, best_ask_before REAL,
                depth_before_json TEXT, created_at TEXT, schema_version INTEGER
            );
            CREATE TABLE execution_feasibility_latest (
                token_id TEXT, direction TEXT, evidence_id TEXT, event_id TEXT,
                condition_id TEXT, outcome_label TEXT, quote_seen_at TEXT,
                book_hash_before TEXT, best_bid_before REAL,
                best_ask_before REAL, depth_before_json TEXT, created_at TEXT,
                schema_version INTEGER, PRIMARY KEY(token_id,direction)
            );
            CREATE INDEX idx_execution_feasibility_evidence_token_time
                ON execution_feasibility_evidence(token_id,quote_seen_at);
            CREATE TABLE position_events (
                event_id TEXT PRIMARY KEY, position_id TEXT, sequence_no INTEGER,
                event_type TEXT, occurred_at TEXT, command_id TEXT,
                payload_json TEXT, phase_before TEXT, phase_after TEXT
            );
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY, position_id TEXT, created_at TEXT,
                updated_at TEXT, state TEXT
            );
            CREATE TABLE venue_command_events (
                event_id TEXT PRIMARY KEY, command_id TEXT, sequence_no INTEGER,
                event_type TEXT, occurred_at TEXT, payload_json TEXT,
                state_after TEXT
            );
            CREATE TABLE venue_order_facts (
                fact_id INTEGER PRIMARY KEY, command_id TEXT, observed_at TEXT,
                local_sequence INTEGER
            );
            CREATE TABLE venue_trade_facts (
                trade_fact_id INTEGER PRIMARY KEY, command_id TEXT,
                trade_id TEXT,
                observed_at TEXT, local_sequence INTEGER, fill_price TEXT,
                filled_size TEXT
            );
            CREATE TABLE wallet_fill_observations (
                id INTEGER PRIMARY KEY, token_id TEXT, trade_id TEXT, observed_at TEXT,
                price TEXT, size TEXT
            );
            CREATE INDEX idx_wallet_fill_observations_trade
                ON wallet_fill_observations(trade_id);
            """
        )


def _forecast_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
                temperature_metric TEXT, source_cycle_time TEXT,
                source_available_at TEXT, computed_at TEXT, recorded_at TEXT
            );
            CREATE TABLE ensemble_snapshots (
                snapshot_id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
                temperature_metric TEXT, source_cycle_time TEXT, issue_time TEXT,
                source_available_at TEXT, available_at TEXT, fetch_time TEXT,
                recorded_at TEXT
            );
            """
        )


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    trades = tmp_path / "trades.db"
    forecasts = tmp_path / "forecasts.db"
    settings = tmp_path / "settings.json"
    _trade_db(trades)
    _forecast_db(forecasts)
    settings.write_text(json.dumps({"execution": {"absolute_live_unit_price_min": 0.05}}))
    return {
        "loop": {
            "history_days": 7,
            "floor_config_key": "execution.absolute_live_unit_price_min",
            "default_floor": 0.05,
            "hard_slots": 1,
            "precursor_slots": 1,
            "poll_ms": 250,
        },
        "active": {"profile": "test"},
        "profiles": {
            "test": {
                "model": "gpt-5.6-sol",
                "preferred_reasoning": "high",
                "fallback_reasoning": [],
            }
        },
        "paths": {
            "trades_db": str(trades),
            "forecasts_db": str(forecasts),
            "settings": str(settings),
            "runtime": str(tmp_path / ".total_loss"),
            "prompt": str(ROOT / "total_loss_prompt.md"),
            "deploy_script": str(ROOT / "scripts" / "deploy_live.py"),
            "pr_monitor": str(ROOT / "scripts" / "pr_monitor.py"),
        },
        "delivery": {"base_branch": "live", "branch_prefix": "test/total-loss"},
        "capital_lane": {"agent_nice": 15},
    }


def _position(cfg: dict, *, position_id: str = "p1", direction: str = "buy_yes") -> None:
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                position_id, "active", f"trade-{position_id}", "market-1", "London",
                "2026-08-22", "28C", direction, "C", 10.0, 10.0, 5.0, None,
                0.5, "yes-token", "no-token", "condition-1", None,
                "2026-08-22T09:00:00+00:00", "high",
            ),
        )


def _quote(
    cfg: dict,
    evidence_id: str,
    at: str,
    bid: float | None,
    *,
    token: str = "yes-token",
    direction: str = "buy_yes",
    latest: bool = True,
    depth_bid: float | None | object = ...,
) -> None:
    resolved_depth_bid = bid if depth_bid is ... else depth_bid
    depth = (
        {"bids": [], "asks": []}
        if resolved_depth_bid is None
        else {
            "bids": [{"price": str(resolved_depth_bid), "size": "100"}],
            "asks": [],
        }
    )
    values = (
        evidence_id, f"event-{evidence_id}", "condition-1", token, "YES",
        direction, at, f"book-{evidence_id}", bid, 0.5,
        json.dumps(depth), at, 1,
    )
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute("INSERT INTO execution_feasibility_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        if latest:
            sell = "sell_no" if token == "no-token" else "sell_yes"
            latest_values = (
                token, sell, evidence_id, f"event-{evidence_id}", "condition-1",
                "YES", at, f"book-{evidence_id}", bid, 0.5,
                json.dumps(depth), at, 1,
            )
            conn.execute(
                "INSERT OR REPLACE INTO execution_feasibility_latest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                latest_values,
            )


def _event(
    cfg: dict,
    event_id: str,
    position_id: str,
    sequence_no: int,
    event_type: str,
    at: str,
    *,
    phase_before: str | None,
    phase_after: str | None,
    payload: dict | None = None,
) -> None:
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event_id, position_id, sequence_no, event_type, at, None,
                json.dumps(payload or {}), phase_before, phase_after,
            ),
        )


def _settled_full_loss(cfg: dict, *, position_id: str = "p-settled", payload: dict | None = None) -> None:
    _position(cfg, position_id=position_id)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET phase='settled',realized_pnl_usd=-5.0,settled_at=? "
            "WHERE position_id=?",
            ("2026-08-22T10:00:00+00:00", position_id),
        )
    _event(
        cfg, f"settled-{position_id}", position_id, 2, "SETTLED",
        "2026-08-22T10:00:00+00:00", phase_before="active", phase_after="settled",
        payload=payload or {"outcome": 0, "payout_id": f"payout-{position_id}"},
    )


def _command_dedup_basis(*_args, **_kwargs) -> dict:
    return {
        "filled_cost_basis_usd": 5.0,
        "entry_fill_command_identity_complete": True,
    }


def _incidents(cfg: dict) -> list[dict]:
    with loop.memory(cfg) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM incidents ORDER BY detected_at")]


def _queue_blind_dispatch_debt(cfg: dict, *, incident_id: str = "dispatch-debt") -> None:
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES (?, 'hard', 'p1', 'q1', 'below_floor', 'yes-token', 'sell_yes', .05, ?, 1, 'queued', 'blind', ?)",
            (incident_id, "2026-08-22T09:00:00+00:00", "2026-08-22T09:00:00+00:00"),
        )
        mem.commit()


def test_crossing_below_floor_creates_one_hard_incident(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q1", "2026-08-22T09:00:01+00:00", 0.08, latest=False)
    _quote(cfg, "q2", "2026-08-22T09:00:02+00:00", 0.04)

    first = loop.detect(cfg)
    second = loop.detect(cfg)

    rows = _incidents(cfg)
    assert len([row for row in rows if row["kind"] == "hard"]) == 1
    assert rows[0]["crossing_evidence_id"] == "q2"
    assert rows[0]["t_floor"] == "2026-08-22T09:00:02+00:00"
    assert first
    assert second == []


def test_settlement_full_loss_is_idempotent_and_keeps_floor_fields_null(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg)
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)

    first = loop.detect(cfg)
    second = loop.detect(cfg)
    rows = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert first and second == [] and len(rows) == 1
    assert rows[0]["crossing_kind"] == "settlement_full_loss"
    assert rows[0]["observed_bid"] is None
    assert rows[0]["t_floor"] is None
    evidence = loop.build_evidence(cfg, rows[0]["incident_id"])
    with sqlite3.connect(evidence) as conn:
        settled = conn.execute(
            "SELECT settled_at FROM settlement_facts"
        ).fetchall()
    assert settled == [("2026-08-22T10:00:00+00:00",)]


def test_settlement_identity_survives_projection_and_payload_enrichment(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg, payload={"outcome": 0})
    aggregate = {
        **_command_dedup_basis(),
        "execution_fact_command_ids": ["entry-command-1"],
    }
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", lambda *_args, **_kwargs: aggregate)
    assert len(loop.detect(cfg)) == 1
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET updated_at=?,realized_pnl_usd=?,shares=? "
            "WHERE position_id='p-settled'",
            ("2026-08-22T11:00:00+00:00", -4.99, 9.9),
        )
        conn.execute(
            "UPDATE position_events SET payload_json=? WHERE event_id='settled-p-settled'",
            (json.dumps({
                "outcome": 0,
                "payout_id": "payout-stable",
                "settlement_source": "gamma",
                "source_receipt": "enriched-later",
            }),),
        )
    assert loop.detect(cfg) == []
    rows = [row for row in _incidents(cfg) if row["crossing_kind"] == "settlement_full_loss"]
    assert len(rows) == 1


def test_repeated_chain_mirror_settled_events_are_exactly_once(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg, payload={"outcome": 0, "payout_id": "payout-1"})
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)
    assert len(loop.detect(cfg)) == 1
    _event(
        cfg, "settled-p-settled-2", "p-settled", 3, "SETTLED",
        "2026-08-22T11:00:00+00:00", phase_before="settled", phase_after="settled",
        payload={"outcome": 0, "payout_id": "payout-2"},
    )
    assert loop.detect(cfg) == []
    rows = [row for row in _incidents(cfg) if row["crossing_kind"] == "settlement_full_loss"]
    assert len(rows) == 1


def test_canonical_settlement_correction_revises_existing_loss_incident(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg, payload={"outcome": 0})
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)
    assert len(loop.detect(cfg)) == 1
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_events SET payload_json=? WHERE event_id='settled-p-settled'",
            (json.dumps({"outcome": 1}),),
        )
    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        status = mem.execute(
            "SELECT status FROM incidents WHERE crossing_kind='settlement_full_loss'"
        ).fetchone()[0]
        reason = mem.execute(
            "SELECT reason FROM incident_transitions "
            "WHERE reason='canonical_settlement_no_longer_full_loss'"
        ).fetchone()[0]
    assert status == "observing"
    assert reason == "canonical_settlement_no_longer_full_loss"


@pytest.mark.parametrize(
    ("payload", "shares", "partial"),
    [
        ({"outcome": 1, "payout_id": "winner"}, 10.0, False),
        ({"outcome": 0, "payout_id": "dust"}, 0.001, False),
        ({"outcome": 0, "payout_id": "partial"}, 10.0, True),
    ],
)
def test_settlement_full_loss_excludes_winner_dust_and_partial_exit(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
    shares: float,
    partial: bool,
) -> None:
    _settled_full_loss(cfg, payload=payload)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute("UPDATE position_current SET shares=?,chain_shares=?", (shares, shares))
    if partial:
        _event(
            cfg, "partial-exit", "p-settled", 1, "EXIT_ORDER_FILLED",
            "2026-08-22T09:59:00+00:00", phase_before="active", phase_after="active",
        )
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)
    assert not [row for row in loop.detect(cfg) if row]
    assert _incidents(cfg) == []


def test_retry_event_stem_is_bounded_and_not_chained(cfg: dict) -> None:
    first = loop._bounded_retry_events(
        cfg, incident_id="x" * 200, stage="repair_feedback"
    )
    second = loop._bounded_retry_events(
        cfg, incident_id="x" * 200, stage="repair_feedback"
    )
    assert first == second
    assert "retry-retry" not in first.name
    assert len(first.name) < 100


def test_claimed_incident_returns_to_retry_pending_on_spawn_oserror(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="spawn-oserror")
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ok": True})
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_retry_pending", lambda *_args: [])
    monkeypatch.setattr(loop, "_recover_classification_debt", lambda *_args: None)
    monkeypatch.setattr(loop, "_dispatch_repair_waiting", lambda *_args: None)
    monkeypatch.setattr(loop, "build_evidence", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    assert loop.dispatch(cfg) == []
    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT status FROM incidents WHERE incident_id='spawn-oserror'"
        ).fetchone()
        reason = mem.execute(
            "SELECT reason FROM incident_transitions WHERE incident_id='spawn-oserror'"
        ).fetchone()[0]
    assert row[0] == "retry_pending"
    assert "spawn_persistence_failed:OSError" in reason


def test_orphan_reconciliation_preserves_live_and_reclaims_dead(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="orphan-live")
    with loop.memory(cfg) as mem:
        mem.execute("UPDATE incidents SET status='running' WHERE incident_id='orphan-live'")
        mem.commit()
    run_path = Path(cfg["paths"]["runtime"]) / "runs" / "live.json"
    loop.atomic_json(run_path, {"incident_id": "orphan-live", "run_id": "live", "pid": 123, "status": "running"})
    monkeypatch.setattr(loop, "_pid_alive", lambda _pid: True)
    assert loop.reconcile_orphan_incidents(cfg) == []
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM incidents WHERE incident_id='orphan-live'").fetchone()[0] == "running"
    monkeypatch.setattr(loop, "_pid_alive", lambda _pid: False)
    assert loop.reconcile_orphan_incidents(cfg) == ["orphan-live"]
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM incidents WHERE incident_id='orphan-live'").fetchone()[0] == "retry_pending"


def test_dispatch_claims_hard_blind_before_repair_waiting(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ok": True})
    monkeypatch.setattr(loop, "reconcile_orphan_incidents", lambda _cfg: [])
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_retry_pending", lambda *_args: [])
    monkeypatch.setattr(loop, "_recover_classification_debt", lambda *_args: None)
    monkeypatch.setattr(loop, "_claim", lambda _cfg, kind: order.append(f"claim:{kind}") or (None if kind == "precursor" else {"incident_id": "hard", "kind": "hard"}))
    monkeypatch.setattr(loop, "build_evidence", lambda *_args: Path("/tmp/evidence.db"))
    monkeypatch.setattr(loop, "read_json", lambda *_args: {"loaded_sha": "sha"})
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: {"run_id": "hard-run"})
    monkeypatch.setattr(loop, "_dispatch_repair_waiting", lambda *_args: order.append("repair") or None)
    assert loop.dispatch(cfg) == ["hard"]
    assert order.index("claim:hard") < order.index("repair")


def test_spawn_intent_witness_blocks_reclaim_until_ambiguity_is_resolved(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="spawn-crash-gap")
    with loop.memory(cfg) as mem:
        mem.execute("UPDATE incidents SET status='running' WHERE incident_id='spawn-crash-gap'")
        mem.commit()
    run_id = "spawn-crash-gap-run"
    witness_fd, witness_path = loop._acquire_spawn_witness(cfg, run_id)
    loop._create_spawn_intent(
        cfg, run_id=run_id, incident_id="spawn-crash-gap", stage="diagnosis",
        witness_path=witness_path,
    )
    monkeypatch.setattr(loop, "_pid_alive", lambda _pid: False)
    assert loop.reconcile_orphan_incidents(cfg) == []
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT status FROM incidents WHERE incident_id='spawn-crash-gap'"
        ).fetchone()[0] == "running"
    loop._release_spawn_witness(cfg, run_id, witness_path)
    with loop.memory(cfg) as mem:
        mem.execute(
            "UPDATE spawn_intents SET created_at=? WHERE run_id=?",
            ("2026-08-22T00:00:00+00:00", run_id),
        )
        mem.commit()
    assert loop.reconcile_orphan_incidents(cfg) == ["spawn-crash-gap"]


def test_missing_execution_fact_schema_is_typed_controller_debt(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg)
    monkeypatch.setattr(
        loop,
        "_entry_execution_fill_aggregate",
        lambda *_args: (_ for _ in ()).throw(
            loop.ExecutionFactCapabilityError("execution_fact_schema_unavailable:no such table")
        ),
    )
    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        debt = mem.execute(
            "SELECT kind,status,reason FROM controller_debt WHERE debt_id='execution_fact_schema'"
        ).fetchone()
    assert tuple(debt) == ("execution_fact", "blocked", "execution_fact_schema_unavailable:no such table")
    assert _incidents(cfg) == []


def test_settlement_basis_pending_is_retried_without_freezing_backfill_cursor(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg)
    aggregate: dict | None = None

    def delayed_basis(*_args, **_kwargs) -> dict | None:
        return aggregate

    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", delayed_basis)
    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT COUNT(*) FROM settlement_backfill_state").fetchone()[0] == 0
        debt = mem.execute(
            "SELECT status FROM controller_debt WHERE debt_id='settlement_basis:p-settled'"
        ).fetchone()
    assert debt[0] == "retry_pending"

    aggregate = _command_dedup_basis()
    created = loop.detect(cfg)
    assert len(created) == 1
    assert loop.detect(cfg) == []
    assert len([row for row in _incidents(cfg) if row["kind"] == "hard"]) == 1


def test_settlement_backfill_cursor_recovers_configured_older_loss_without_default_flood(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET settled_at=?,updated_at=? WHERE position_id='p-settled'",
            ("2026-08-01T10:00:00+00:00", "2026-08-01T10:00:00+00:00"),
        )
        conn.execute(
            "UPDATE position_events SET occurred_at=? WHERE event_id='settled-p-settled'",
            ("2026-08-01T10:00:00+00:00",),
        )
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)
    assert loop.detect(cfg) == []
    cfg["loop"]["settlement_backfill_days"] = 30
    first = loop.detect(cfg)
    second = loop.detect(cfg)
    assert first and second == []
    assert len([row for row in _incidents(cfg) if row["kind"] == "hard"]) == 1


def test_initial_quote_cursor_uses_primary_key_max_without_scan(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _position(cfg)
    _quote(cfg, "q-current", "2026-08-22T09:00:02+00:00", 0.20)
    queries: list[str] = []
    original_open_ro = loop.open_ro

    def traced_open_ro(path: Path):
        conn = original_open_ro(path)
        if Path(path) == Path(cfg["paths"]["trades_db"]):
            conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr(loop, "open_ro", traced_open_ro)
    loop.detect(cfg)

    cursor_queries = [query for query in queries if "execution_feasibility_evidence" in query]
    assert any("SELECT MAX(rowid) FROM execution_feasibility_evidence" in query for query in cursor_queries)
    assert not any("ORDER BY rowid DESC LIMIT 1" in query for query in cursor_queries)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT MAX(rowid) FROM execution_feasibility_evidence"
        ).fetchall()
    plan_text = " ".join(str(column) for row in plan for column in row).upper()
    assert "SCAN EXECUTION_FEASIBILITY_EVIDENCE" not in plan_text


def test_daemon_keeps_detecting_while_dispatch_worker_is_busy(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    detected: list[int] = []
    spawned: list[object] = []

    class BusyDispatchWorker:
        pid = 4242

        def poll(self) -> None:
            return None

    def fake_bootstrap(_cfg: dict) -> dict[str, str]:
        runtime.mkdir(parents=True, exist_ok=True)
        return {"runtime": str(runtime)}

    def fake_detect(_cfg: dict) -> list[str]:
        detected.append(len(detected) + 1)
        if len(detected) == 2:
            (runtime / "HALT").touch()
        return []

    monkeypatch.setattr(loop, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "dispatch", lambda _cfg: pytest.fail("daemon must not synchronously dispatch"))
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", lambda *_args: [])
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()) or BusyDispatchWorker())
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_terminate_process_group", lambda _pid: None)
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    _queue_blind_dispatch_debt(cfg)

    assert loop.daemon(cfg) == 0
    assert detected == [1, 2]
    assert len(spawned) == 1


def test_daemon_does_not_spawn_dispatch_worker_without_durable_debt(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    detected: list[int] = []
    spawned: list[object] = []
    debt_checks: list[object] = []

    def fake_bootstrap(_cfg: dict) -> dict[str, str]:
        runtime.mkdir(parents=True, exist_ok=True)
        return {"runtime": str(runtime)}

    def fake_detect(_cfg: dict) -> list[str]:
        detected.append(len(detected) + 1)
        if len(detected) == 3:
            (runtime / "HALT").touch()
        return []

    monkeypatch.setattr(loop, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", lambda *_args: [])
    monkeypatch.setattr(loop, "_dispatch_has_eligible_debt", lambda *_args: debt_checks.append(object()) or False)
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()))
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    assert loop.daemon(cfg) == 0
    assert detected == [1, 2, 3]
    assert spawned == []
    assert len(debt_checks) == 1


def test_daemon_owns_missing_capability_probe_before_dispatch(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    detected: list[int] = []
    probes: list[object] = []
    spawned: list[object] = []

    class BusyDispatchWorker:
        pid = 4244

        def poll(self) -> None:
            return None

    def fake_bootstrap(_cfg: dict) -> dict[str, str]:
        runtime.mkdir(parents=True, exist_ok=True)
        return {"runtime": str(runtime)}

    def fake_detect(_cfg: dict) -> list[str]:
        detected.append(len(detected) + 1)
        if len(detected) == 3:
            (runtime / "HALT").touch()
        return []

    monkeypatch.setattr(loop, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: None if len(detected) < 3 else {"ready": True})
    monkeypatch.setattr(loop, "ensure_capability_probe", lambda _cfg: probes.append(object()))
    monkeypatch.setattr(loop, "poll_runs", lambda *_args: [])
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()) or BusyDispatchWorker())
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_terminate_process_group", lambda _pid: None)
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    _queue_blind_dispatch_debt(cfg, incident_id="capability-debt")

    assert loop.daemon(cfg) == 0
    assert detected == [1, 2, 3]
    assert len(probes) == 2
    assert len(spawned) == 1


def test_model_completion_wakes_eligible_dispatch_once(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    detected: list[int] = []
    spawned: list[object] = []
    poll_calls = 0

    class BusyDispatchWorker:
        pid = 4245

        def poll(self) -> None:
            return None

    def fake_bootstrap(_cfg: dict) -> dict[str, str]:
        runtime.mkdir(parents=True, exist_ok=True)
        return {"runtime": str(runtime)}

    def fake_detect(_cfg: dict) -> list[str]:
        detected.append(len(detected) + 1)
        if len(detected) == 2:
            (runtime / "HALT").touch()
        return []

    def fake_poll(_cfg: dict, _running: list[dict]) -> list[str]:
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls == 1:
            _queue_blind_dispatch_debt(cfg, incident_id="completion-debt")
            return ["model-run"]
        return []

    monkeypatch.setattr(loop, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", fake_poll)
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()) or BusyDispatchWorker())
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_terminate_process_group", lambda _pid: None)
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    assert loop.daemon(cfg) == 0
    assert detected == [1, 2]
    assert len(spawned) == 1


def test_dispatch_eligibility_waits_for_stage_retry_due_time(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    (runtime / "runs").mkdir(parents=True)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES ('retry-debt', 'hard', 'p1', 'q1', 'below_floor', 'yes-token', 'sell_yes', .05, ?, 1, 'retry_pending', 'diagnosis', ?)",
            ("2026-08-22T09:00:00+00:00", "2026-08-22T09:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(loop, "now", lambda: loop.parse_time("2026-08-22T10:00:00+00:00"))
    loop.atomic_json(
        runtime / "runs" / "retry.json",
        {
            "incident_id": "retry-debt",
            "stage": "diagnosis",
            "command": ["codex", "exec"],
            "controller": True,
            "completed_at": "2026-08-22T09:59:30+00:00",
        },
    )

    assert loop._dispatch_has_eligible_debt(cfg, []) is False

    loop.atomic_json(
        runtime / "runs" / "retry.json",
        {
            "incident_id": "retry-debt",
            "stage": "diagnosis",
            "command": ["codex", "exec"],
            "controller": True,
            "completed_at": "2026-08-22T09:58:00+00:00",
        },
    )
    assert loop._dispatch_has_eligible_debt(cfg, []) is True


def test_dispatch_eligibility_reads_memory_without_schema_maintenance(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="readonly-debt")
    monkeypatch.setattr(
        loop,
        "memory",
        lambda _cfg: pytest.fail("eligibility must not open writable schema memory"),
    )

    assert loop._dispatch_has_eligible_debt(cfg, []) is True


def test_daemon_records_dispatch_failures_without_blocking_next_detect(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    detected: list[int] = []
    statuses: list[dict] = []
    poll_calls = 0
    spawn_calls = 0

    class BusyDispatchWorker:
        pid = 4243

        def poll(self) -> None:
            return None

    def fake_bootstrap(_cfg: dict) -> dict[str, str]:
        runtime.mkdir(parents=True, exist_ok=True)
        return {"runtime": str(runtime)}

    def fake_detect(_cfg: dict) -> list[str]:
        detected.append(len(detected) + 1)
        if len(detected) == 4:
            (runtime / "HALT").touch()
        return [f"wake-{detected[-1]}"] if len(detected) < 4 else []

    def fake_poll(_cfg: dict, _running: list[dict]) -> list[str]:
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls == 1:
            raise RuntimeError("poll unavailable")
        return []

    def fake_spawn(_cfg: dict) -> BusyDispatchWorker:
        nonlocal spawn_calls
        spawn_calls += 1
        if spawn_calls == 1:
            raise RuntimeError("worker spawn unavailable")
        return BusyDispatchWorker()

    def capture_atomic(path: Path, payload: dict) -> None:
        if path.name == "status.json":
            statuses.append(dict(payload))

    monkeypatch.setattr(loop, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "dispatch", lambda _cfg: pytest.fail("daemon must not synchronously dispatch"))
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", fake_poll)
    monkeypatch.setattr(loop, "_dispatch_has_eligible_debt", lambda *_args: True)
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", fake_spawn)
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_terminate_process_group", lambda _pid: None)
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop, "atomic_json", capture_atomic)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    assert loop.daemon(cfg) == 0
    active = [status for status in statuses if status.get("alive") is True]
    assert detected == [1, 2, 3, 4]
    assert any(status.get("dispatch_error") == "RuntimeError: poll unavailable" for status in active)
    assert any(status.get("dispatch_error") == "RuntimeError: worker spawn unavailable" for status in active)
    assert active[-1]["dispatch_error"] is None


def test_missing_active_floor_fails_closed(cfg: dict) -> None:
    Path(cfg["paths"]["settings"]).write_text("{}")

    with pytest.raises(RuntimeError, match="active execution floor unavailable"):
        loop.detect(cfg)

    assert _incidents(cfg) == []


def test_first_observation_below_floor_is_immediate_incident(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)

    loop.detect(cfg)

    row = _incidents(cfg)[0]
    assert row["kind"] == "hard"
    assert row["crossing_kind"] == "below_floor"


def test_absent_bid_is_hard_no_book_incident_without_fabricated_floor_time(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-none", "2026-08-22T09:00:02+00:00", None)

    loop.detect(cfg)

    row = _incidents(cfg)[0]
    assert row["crossing_kind"] == "no_bid"
    assert row["t_floor"] is None


def test_velocity_uses_token_time_index_for_latest_three_quotes(cfg: dict) -> None:
    for evidence_id, at, bid in (
        ("velocity-1", "2026-08-22T09:00:01+00:00", 0.80),
        ("velocity-2", "2026-08-22T09:00:02+00:00", 0.70),
        ("velocity-3", "2026-08-22T09:00:03+00:00", 0.60),
        ("velocity-4", "2026-08-22T09:00:04+00:00", 0.50),
    ):
        _quote(cfg, evidence_id, at, bid, latest=False)

    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT quote_seen_at,best_bid_before "
            "FROM execution_feasibility_evidence "
            "WHERE token_id=? AND direction=? "
            "AND best_bid_before IS NOT NULL "
            "ORDER BY quote_seen_at DESC,rowid DESC LIMIT 3",
            ("yes-token", "buy_yes"),
        ).fetchall()
        newest = conn.execute(
            "SELECT quote_seen_at FROM execution_feasibility_evidence "
            "WHERE token_id=? AND direction=? "
            "AND best_bid_before IS NOT NULL "
            "ORDER BY quote_seen_at DESC,rowid DESC LIMIT 3",
            ("yes-token", "buy_yes"),
        ).fetchall()
        velocity, acceleration = loop._velocity(conn, "yes-token", "buy_yes")

    plan_text = " ".join(str(column) for row in plan for column in row).upper()
    assert "USING INDEX IDX_EXECUTION_FEASIBILITY_EVIDENCE_TOKEN_TIME" in plan_text
    assert "SCAN EXECUTION_FEASIBILITY_EVIDENCE" not in plan_text
    assert "TEMP B-TREE" not in plan_text
    assert [row[0] for row in newest] == [
        "2026-08-22T09:00:04+00:00",
        "2026-08-22T09:00:03+00:00",
        "2026-08-22T09:00:02+00:00",
    ]
    assert velocity == pytest.approx(-0.10)
    assert acceleration == pytest.approx(0.0)


def test_depth_top_bid_overrides_conflicting_zero_scalar(cfg: dict) -> None:
    _position(cfg)
    _quote(
        cfg,
        "q-scalar-split",
        "2026-08-22T09:00:02+00:00",
        0.0,
        depth_bid=0.999,
    )

    loop.detect(cfg)

    assert not [row for row in _incidents(cfg) if row["kind"] == "hard"]
    with loop.memory(cfg) as conn:
        state = conn.execute(
            "SELECT best_bid,quote_status,below_floor FROM position_quote_state "
            "WHERE position_id='p1'"
        ).fetchone()
    assert tuple(state) == (0.999, "quote_integrity_conflict", 0)


def test_missing_or_malformed_depth_is_not_no_bid(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-malformed", "2026-08-22T09:00:02+00:00", None)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json='not-json' "
            "WHERE evidence_id='q-malformed'"
        )
        conn.execute(
            "UPDATE execution_feasibility_latest SET depth_before_json='not-json' "
            "WHERE evidence_id='q-malformed'"
        )

    loop.detect(cfg)

    assert not [row for row in _incidents(cfg) if row["kind"] == "hard"]
    with loop.memory(cfg) as conn:
        status = conn.execute(
            "SELECT quote_status FROM position_quote_state WHERE position_id='p1'"
        ).fetchone()[0]
    assert status == "quote_incomplete"


def test_incomplete_latest_uses_prior_authoritative_quote_for_precursor_only(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-complete", "2026-08-22T09:00:01+00:00", 0.20, direction="buy_yes")
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        position = loop.tracked_positions(trades, history_days=7)["p1"]
        complete = dict(trades.execute(
            "SELECT * FROM execution_feasibility_evidence WHERE evidence_id='q-complete'"
        ).fetchone())
    with loop.memory(cfg) as mem:
        assert loop._observe_quote(mem, position, complete, 0.05) is None
        mem.commit()
    _quote(cfg, "q-incomplete", "2026-08-22T09:00:02+00:00", 0.20, direction="sell_yes")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json=NULL "
            "WHERE evidence_id='q-incomplete'"
        )
        conn.execute(
            "UPDATE execution_feasibility_latest SET depth_before_json=NULL "
            "WHERE evidence_id='q-incomplete'"
        )
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT evidence_id FROM execution_feasibility_evidence "
            "WHERE token_id=? AND direction=? AND depth_before_json IS NOT NULL "
            "ORDER BY quote_seen_at DESC,rowid DESC LIMIT 1",
            ("yes-token", "buy_yes"),
        ).fetchall()

    loop.detect(cfg)

    plan_text = " ".join(str(column) for row in plan for column in row).upper()
    assert "USING INDEX IDX_EXECUTION_FEASIBILITY_EVIDENCE_TOKEN_TIME" in plan_text
    assert "SCAN EXECUTION_FEASIBILITY_EVIDENCE" not in plan_text
    assert "TEMP B-TREE" not in plan_text
    precursor = [row for row in _incidents(cfg) if row["kind"] == "precursor"]
    assert len(precursor) == 1
    assert precursor[0]["crossing_evidence_id"] == "q-complete"
    with loop.memory(cfg) as conn:
        state = conn.execute(
            "SELECT quote_status,best_bid FROM position_quote_state WHERE position_id='p1'"
        ).fetchone()
    assert tuple(state) == ("quote_incomplete", 0.20)

    _quote(cfg, "q-hard", "2026-08-22T09:00:03+00:00", 0.04, direction="sell_yes")
    loop.detect(cfg)

    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 1
    assert hard[0]["crossing_evidence_id"] == "q-hard"


def test_precursor_uses_buy_no_carrier_when_sell_no_latest_is_incomplete(cfg: dict) -> None:
    _position(cfg, direction="buy_no")
    _quote(cfg, "old-sell", "2026-08-22T09:00:00+00:00", 0.60, token="no-token", direction="sell_no")
    _quote(cfg, "buy-carrier", "2026-08-22T09:00:02+00:00", 0.20, token="no-token", direction="buy_no")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO execution_feasibility_latest "
            "(token_id,direction,evidence_id,event_id,condition_id,outcome_label,quote_seen_at,"
            "book_hash_before,best_bid_before,best_ask_before,depth_before_json,created_at,schema_version) "
            "SELECT token_id,direction,evidence_id,event_id,condition_id,outcome_label,quote_seen_at,"
            "book_hash_before,best_bid_before,best_ask_before,depth_before_json,created_at,schema_version "
            "FROM execution_feasibility_evidence WHERE evidence_id='buy-carrier'"
        )
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        position = loop.tracked_positions(trades, history_days=7)["p1"]
        carrier = dict(trades.execute(
            "SELECT * FROM execution_feasibility_evidence WHERE evidence_id='buy-carrier'"
        ).fetchone())
    with loop.memory(cfg) as mem:
        assert loop._observe_quote(mem, position, carrier, 0.05) is None
        mem.commit()
    _quote(cfg, "sell-incomplete", "2026-08-22T09:00:02+00:00", 0.20, token="no-token", direction="sell_no")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json=NULL "
            "WHERE evidence_id='sell-incomplete'"
        )
        conn.execute(
            "UPDATE execution_feasibility_latest SET depth_before_json=NULL "
            "WHERE evidence_id='sell-incomplete'"
        )
        latest_depth = dict(conn.execute(
            "SELECT direction,depth_before_json FROM execution_feasibility_latest "
            "WHERE token_id='no-token'"
        ).fetchall())

    assert latest_depth["buy_no"] is not None
    assert latest_depth["sell_no"] is None

    loop.detect(cfg)

    precursor = [row for row in _incidents(cfg) if row["kind"] == "precursor"]
    assert len(precursor) == 1
    assert precursor[0]["crossing_evidence_id"] == "buy-carrier"
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        latest = loop._latest_quotes(trades, [position])["p1"]
    assert latest["evidence_id"] == "buy-carrier"
    assert latest["_current_quote"]["evidence_id"] == "sell-incomplete"


def test_precursor_uses_buy_carrier_when_sell_latest_is_absent(cfg: dict) -> None:
    _position(cfg, direction="buy_no")
    _quote(cfg, "buy-carrier", "2026-08-22T09:00:02+00:00", 0.20, token="no-token", direction="buy_no")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO execution_feasibility_latest "
            "(token_id,direction,evidence_id,event_id,condition_id,outcome_label,quote_seen_at,"
            "book_hash_before,best_bid_before,best_ask_before,depth_before_json,created_at,schema_version) "
            "SELECT token_id,direction,evidence_id,event_id,condition_id,outcome_label,quote_seen_at,"
            "book_hash_before,best_bid_before,best_ask_before,depth_before_json,created_at,schema_version "
            "FROM execution_feasibility_evidence WHERE evidence_id='buy-carrier'"
        )
        conn.execute(
            "DELETE FROM execution_feasibility_latest WHERE token_id='no-token' AND direction='sell_no'"
        )

    loop.detect(cfg)

    precursor = [row for row in _incidents(cfg) if row["kind"] == "precursor"]
    assert len(precursor) == 1
    assert precursor[0]["crossing_evidence_id"] == "buy-carrier"
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        position = loop.tracked_positions(trades, history_days=7)["p1"]
        latest = loop._latest_quotes(trades, [position])["p1"]
    assert latest["evidence_id"] == "buy-carrier"
    assert latest["_current_quote"] is None


def test_incomplete_quote_does_not_hide_following_no_bid_transition(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-healthy", "2026-08-22T09:00:01+00:00", 0.20)
    loop.detect(cfg)
    _quote(cfg, "q-malformed", "2026-08-22T09:00:02+00:00", None)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json='not-json' "
            "WHERE evidence_id='q-malformed'"
        )
        conn.execute(
            "UPDATE execution_feasibility_latest SET depth_before_json='not-json' "
            "WHERE evidence_id='q-malformed'"
        )
    loop.detect(cfg)
    _quote(cfg, "q-no-bid", "2026-08-22T09:00:03+00:00", None)

    loop.detect(cfg)

    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 1
    assert hard[0]["crossing_kind"] == "no_bid"
    assert hard[0]["crossing_evidence_id"] == "q-no-bid"


@pytest.mark.parametrize(
    "depth",
    (
        {"bids": [{"price": "bad", "size": "100"}], "asks": []},
        {
            "bids": [
                {"price": "bad", "size": "100"},
                {"price": "0.04", "size": "100"},
            ],
            "asks": [],
        },
    ),
)
def test_malformed_depth_level_cannot_fabricate_hard_crossing(
    cfg: dict,
    depth: dict,
) -> None:
    _position(cfg)
    _quote(cfg, "q-bad-level", "2026-08-22T09:00:02+00:00", None)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        encoded = json.dumps(depth)
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json=? "
            "WHERE evidence_id='q-bad-level'",
            (encoded,),
        )
        conn.execute(
            "UPDATE execution_feasibility_latest SET depth_before_json=? "
            "WHERE evidence_id='q-bad-level'",
            (encoded,),
        )

    loop.detect(cfg)

    assert not [row for row in _incidents(cfg) if row["kind"] == "hard"]
    with loop.memory(cfg) as conn:
        status = conn.execute(
            "SELECT quote_status FROM position_quote_state WHERE position_id='p1'"
        ).fetchone()[0]
    assert status == "quote_incomplete"


def test_unrepresentable_residual_dust_does_not_create_hard_incident(cfg: dict) -> None:
    _position(cfg)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET shares=?,chain_shares=?,cost_basis_usd=? "
            "WHERE position_id='p1'",
            (0.001426, 0.001426, 0.0004278),
        )
    _quote(cfg, "q-dust", "2026-08-22T09:00:02+00:00", 0.001)

    loop.detect(cfg)

    assert _incidents(cfg) == []


def test_zero_chain_fact_does_not_fall_back_to_stale_local_shares(cfg: dict) -> None:
    _position(cfg)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET shares=10,chain_shares=0 WHERE position_id='p1'"
        )
    _quote(cfg, "q-chain-zero", "2026-08-22T09:00:02+00:00", 0.001)

    loop.detect(cfg)

    assert _incidents(cfg) == []


def test_blind_legacy_scalar_depth_split_is_retired_before_dispatch(cfg: dict) -> None:
    _position(cfg)
    _quote(
        cfg,
        "q-legacy-split",
        "2026-08-22T09:00:02+00:00",
        0.0,
        depth_bid=0.999,
    )
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades, loop.memory(cfg) as mem:
        position = loop._position_with_exposure(trades, "p1")
        assert position is not None
        loop._insert_incident(
            mem,
            position=position,
            evidence_id="q-legacy-split",
            quote_seen_at="2026-08-22T09:00:02+00:00",
            bid=0.0,
            floor=0.05,
            kind="hard",
            priority=1_000_000.0,
        )
        mem.commit()

    loop.detect(cfg)

    row = _incidents(cfg)[0]
    assert (row["status"], row["stage"]) == ("observing", "observing")
    with loop.memory(cfg) as mem:
        reason = mem.execute(
            "SELECT reason FROM incident_transitions WHERE incident_id=?",
            (row["incident_id"],),
        ).fetchone()[0]
    assert reason == "detector_revalidated:quote_integrity_conflict"


def test_revalidation_uses_incident_floor_not_changed_current_floor(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-historical", "2026-08-22T09:00:02+00:00", 0.04)
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades, loop.memory(cfg) as mem:
        position = loop._position_with_exposure(trades, "p1")
        assert position is not None
        loop._insert_incident(
            mem,
            position=position,
            evidence_id="q-historical",
            quote_seen_at="2026-08-22T09:00:02+00:00",
            bid=0.04,
            floor=0.05,
            kind="hard",
            priority=1_000_000.0,
        )
        mem.commit()
    Path(cfg["paths"]["settings"]).write_text(
        json.dumps({"execution": {"absolute_live_unit_price_min": 0.03}})
    )

    loop.detect(cfg)

    row = _incidents(cfg)[0]
    assert (row["status"], row["stage"], row["floor_price"]) == (
        "queued",
        "blind",
        0.05,
    )


def test_revalidation_cas_does_not_retire_concurrently_claimed_incident(
    cfg: dict,
    monkeypatch,
) -> None:
    _position(cfg)
    _quote(
        cfg,
        "q-race-split",
        "2026-08-22T09:00:02+00:00",
        0.0,
        depth_bid=0.999,
    )
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades, loop.memory(cfg) as mem:
        position = loop._position_with_exposure(trades, "p1")
        assert position is not None
        incident_id = loop._insert_incident(
            mem,
            position=position,
            evidence_id="q-race-split",
            quote_seen_at="2026-08-22T09:00:02+00:00",
            bid=0.0,
            floor=0.05,
            kind="hard",
            priority=1_000_000.0,
        )
        assert incident_id is not None
        mem.commit()
        original = loop.reconcile_held_quote

        def claim_during_reconciliation(quote):
            mem.execute(
                "UPDATE incidents SET status='running' WHERE incident_id=?",
                (incident_id,),
            )
            return original(quote)

        monkeypatch.setattr(loop, "reconcile_held_quote", claim_during_reconciliation)
        assert loop.revalidate_blind_hard_incidents(mem, trades) == 0
        mem.commit()
        row = mem.execute(
            "SELECT status,stage FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        transitions = mem.execute(
            "SELECT COUNT(*) FROM incident_transitions WHERE incident_id=?",
            (incident_id,),
        ).fetchone()[0]

    assert tuple(row) == ("running", "blind")
    assert transitions == 0


def test_buy_no_maps_to_no_token_sell_bid(cfg: dict) -> None:
    _position(cfg, direction="buy_no")
    _quote(cfg, "q-no", "2026-08-22T09:00:02+00:00", 0.03, token="no-token", direction="buy_no")

    loop.detect(cfg)

    row = _incidents(cfg)[0]
    assert row["held_token_id"] == "no-token"
    assert row["held_direction"] == "sell_no"


def test_loss_audit_quote_set_unions_open_exposure_and_unsettled_exit() -> None:
    from src.ingest.price_channel_ingest import (
        _edli_current_loss_audit_token_ids,
        _edli_publish_global_exit_audit_token_ids,
        _edli_publish_held_quote_audit_token_ids,
    )

    try:
        _edli_publish_held_quote_audit_token_ids({"held-a", "shared"})
        _edli_publish_global_exit_audit_token_ids({"exit-b", "shared"})
        assert _edli_current_loss_audit_token_ids() == {"held-a", "exit-b", "shared"}
    finally:
        _edli_publish_held_quote_audit_token_ids(set())
        _edli_publish_global_exit_audit_token_ids(set())


def test_held_rest_refresh_wires_lossless_append_callback() -> None:
    from src.ingest.price_channel_ingest import _edli_refresh_held_position_quote_evidence

    source = inspect.getsource(_edli_refresh_held_position_quote_evidence)
    assert "append_evidence_token_ids=_edli_current_loss_audit_token_ids" in source


def test_no_hard_incident_uses_idle_capacity_for_top_precursor(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q1", "2026-08-22T09:00:01+00:00", 0.30, latest=False)
    _quote(cfg, "q2", "2026-08-22T09:00:02+00:00", 0.20)

    loop.detect(cfg)

    assert [row["kind"] for row in _incidents(cfg)] == ["precursor"]


def test_precursor_identity_is_stable_across_quotes_while_hard_crossing_stays_evidence_bound(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "precursor-1", "2026-08-22T09:00:01+00:00", 0.30)
    loop.detect(cfg)
    _quote(cfg, "precursor-2", "2026-08-22T09:00:02+00:00", 0.20)
    loop.detect(cfg)

    precursor = [row for row in _incidents(cfg) if row["kind"] == "precursor"]
    assert len(precursor) == 1
    assert precursor[0]["incident_id"] == loop.digest("precursor", "p1")
    assert precursor[0]["crossing_evidence_id"] == "precursor-2"
    assert precursor[0]["evidence_revision"] == 2

    _quote(cfg, "hard-later", "2026-08-22T09:00:04+00:00", 0.04)
    loop.detect(cfg)
    _quote(cfg, "hard-earlier", "2026-08-22T09:00:03+00:00", 0.03, latest=False)
    loop.detect(cfg)

    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 1
    assert hard[0]["incident_id"] == loop.digest("p1", "hard-later")
    assert hard[0]["crossing_evidence_id"] == "hard-earlier"
    assert hard[0]["evidence_revision"] == 2


def test_precursor_refresh_does_not_rebind_running_or_retry_pending_evidence(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "precursor-claimed", "2026-08-22T09:00:01+00:00", 0.30)
    loop.detect(cfg)
    incident_id = loop.digest("precursor", "p1")
    mem = loop.memory(cfg)
    mem.execute("UPDATE incidents SET status='running' WHERE incident_id=?", (incident_id,))
    mem.commit()
    mem.close()

    _quote(cfg, "precursor-newer", "2026-08-22T09:00:02+00:00", 0.20)
    loop.detect(cfg)
    running = next(row for row in _incidents(cfg) if row["incident_id"] == incident_id)
    assert running["crossing_evidence_id"] == "precursor-claimed"
    assert running["evidence_revision"] == 1

    mem = loop.memory(cfg)
    mem.execute("UPDATE incidents SET status='retry_pending' WHERE incident_id=?", (incident_id,))
    mem.commit()
    mem.close()
    _quote(cfg, "precursor-latest", "2026-08-22T09:00:03+00:00", 0.10)
    loop.detect(cfg)
    retrying = next(row for row in _incidents(cfg) if row["incident_id"] == incident_id)
    assert retrying["crossing_evidence_id"] == "precursor-claimed"
    assert retrying["evidence_revision"] == 1


def test_hard_incident_suppresses_precursor_creation(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)

    loop.detect(cfg)

    assert [row["kind"] for row in _incidents(cfg)] == ["hard"]


def test_hard_incident_does_not_starve_other_position_precursor(cfg: dict) -> None:
    _position(cfg, position_id="p1")
    _position(cfg, position_id="p2")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET token_id='yes-token-2', no_token_id='no-token-2' WHERE position_id='p2'"
        )
    _quote(cfg, "p1-hard", "2026-08-22T09:00:02+00:00", 0.01)
    _quote(
        cfg,
        "p2-precursor",
        "2026-08-22T09:00:03+00:00",
        0.20,
        token="yes-token-2",
    )

    loop.detect(cfg)

    rows = _incidents(cfg)
    assert [(row["kind"], row["position_id"]) for row in rows] == [
        ("hard", "p1"),
        ("precursor", "p2"),
    ]


def test_tel_aviv_precursor_precedes_hard_crossing_without_duplicate(cfg: dict) -> None:
    _position(cfg)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute("UPDATE position_current SET city='Tel Aviv' WHERE position_id='p1'")
    _quote(cfg, "tel-aviv-precursor", "2026-08-22T09:00:01+00:00", 0.28)

    loop.detect(cfg)
    assert [(row["kind"], row["position_id"]) for row in _incidents(cfg)] == [
        ("precursor", "p1"),
    ]

    _quote(cfg, "tel-aviv-crossing", "2026-08-22T09:00:02+00:00", 0.04)
    loop.detect(cfg)
    rows = _incidents(cfg)
    assert {(row["kind"], row["position_id"]) for row in rows} == {
        ("hard", "p1"),
        ("precursor", "p1"),
    }
    assert sum(row["kind"] == "precursor" for row in rows) == 1


def test_claim_prefers_current_positive_exposure_and_fails_closed_without_trades(cfg: dict, monkeypatch) -> None:
    _position(cfg, position_id="current")
    _position(cfg, position_id="settled")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute("UPDATE position_current SET phase='settled' WHERE position_id='settled'")
    with loop.memory(cfg) as mem:
        for incident_id, position_id, detected_at in (
            ("current-incident", "current", "2026-08-22T09:00:00+00:00"),
            ("settled-incident", "settled", "2026-08-22T10:00:00+00:00"),
        ):
            mem.execute(
                """
                INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,
                    held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at)
                VALUES (?, 'hard', ?, ?, 'below_floor', 'yes-token', 'sell_yes', .05, ?, 1, 'queued', 'blind', ?)
                """,
                (incident_id, position_id, incident_id, detected_at, detected_at),
            )
        mem.commit()

    assert loop._claim(cfg, "hard")["incident_id"] == "current-incident"
    monkeypatch.setattr(loop, "open_ro", lambda _path: (_ for _ in ()).throw(sqlite3.OperationalError()))
    assert loop._claim(cfg, "hard") is None


def test_controller_retry_consumes_its_kind_slot_without_blocking_precursor(
    cfg: dict, monkeypatch
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    claims: list[str] = []
    launched: list[str] = []
    controller = {"incident_id": "hard-controller", "kind": "hard", "controller": True}
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ok": True})
    monkeypatch.setattr(loop, "_running", lambda _cfg: [controller])
    monkeypatch.setattr(loop, "_retry_pending", lambda *_args: [])
    monkeypatch.setattr(loop, "_recover_classification_debt", lambda *_args: None)
    monkeypatch.setattr(loop, "_dispatch_repair_waiting", lambda *_args: None)
    monkeypatch.setattr(
        loop,
        "_claim",
        lambda _cfg, kind: claims.append(kind) or (
            {"incident_id": "precursor-ready", "kind": kind}
            if kind == "precursor" and claims.count(kind) == 1
            else None
        ),
    )
    monkeypatch.setattr(loop, "build_evidence", lambda _cfg, incident_id: runtime / f"{incident_id}.db")
    original_read_json = loop.read_json
    monkeypatch.setattr(
        loop,
        "read_json",
        lambda path, default=None: {"loaded_sha": "current"}
        if Path(path).name == "manifest.json" else original_read_json(path, default),
    )
    monkeypatch.setattr(
        loop,
        "_spawn_run",
        lambda _cfg, **kwargs: launched.append(kwargs["incident_id"]) or {"run_id": "precursor-run"},
    )

    assert loop.dispatch(cfg) == ["precursor-ready"]
    assert "hard" not in claims
    assert launched == ["precursor-ready"]


def test_historical_backfill_ignores_low_quote_before_entry(cfg: dict) -> None:
    _position(cfg)
    _event(
        cfg, "entry", "p1", 1, "ENTRY_ORDER_FILLED",
        "2026-08-22T09:00:10+00:00",
        phase_before="pending_entry", phase_after="active",
    )
    _quote(cfg, "pre-entry-low", "2026-08-22T09:00:01+00:00", 0.01, latest=False)
    _quote(cfg, "held-healthy", "2026-08-22T09:00:11+00:00", 0.20)

    loop.detect(cfg)

    assert not [row for row in _incidents(cfg) if row["kind"] == "hard"]


def test_historical_backfill_ignores_low_quote_after_exposure_closed(cfg: dict) -> None:
    _position(cfg)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET phase='economically_closed',updated_at=? WHERE position_id='p1'",
            ("2026-08-22T09:01:00+00:00",),
        )
    _event(
        cfg, "entry", "p1", 1, "ENTRY_ORDER_FILLED",
        "2026-08-22T09:00:10+00:00",
        phase_before="pending_entry", phase_after="active",
    )
    _event(
        cfg, "exit", "p1", 2, "EXIT_ORDER_FILLED",
        "2026-08-22T09:00:30+00:00",
        phase_before="pending_exit", phase_after="economically_closed",
    )
    _quote(cfg, "post-close-low", "2026-08-22T09:00:31+00:00", 0.01)

    loop.detect(cfg)

    assert not [row for row in _incidents(cfg) if row["kind"] == "hard"]


def test_terminal_projection_time_bounds_backfill_without_terminal_event(cfg: dict) -> None:
    _position(cfg)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET phase='settled',settled_at=? WHERE position_id='p1'",
            ("2026-08-22T09:00:30+00:00",),
        )
    _event(
        cfg, "entry", "p1", 1, "ENTRY_ORDER_FILLED",
        "2026-08-22T09:00:10+00:00",
        phase_before="pending_entry", phase_after="active",
    )
    _quote(cfg, "post-settle-low", "2026-08-22T09:00:31+00:00", 0.01)

    loop.detect(cfg)

    assert not [row for row in _incidents(cfg) if row["kind"] == "hard"]


def test_empty_startup_cursor_consumes_first_later_evidence_row(cfg: dict) -> None:
    _position(cfg)
    loop.detect(cfg)
    _quote(cfg, "transient-low", "2026-08-22T09:00:01+00:00", 0.01, latest=False)

    loop.detect(cfg)

    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 1
    assert hard[0]["crossing_evidence_id"] == "transient-low"


def test_out_of_order_quote_corrects_earliest_floor_without_regressing_latest_state(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "later-low", "2026-08-22T09:00:02+00:00", 0.04)
    loop.detect(cfg)
    _quote(cfg, "earlier-low", "2026-08-22T09:00:01+00:00", 0.03, latest=False)

    loop.detect(cfg)

    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 1
    assert hard[0]["crossing_evidence_id"] == "earlier-low"
    assert hard[0]["t_floor"] == "2026-08-22T09:00:01+00:00"
    with loop.memory(cfg) as mem:
        latest_state = mem.execute(
            "SELECT evidence_id,quote_seen_at FROM position_quote_state WHERE position_id='p1'"
        ).fetchone()
    assert tuple(latest_state) == ("later-low", "2026-08-22T09:00:02+00:00")


def test_monitor_dynamics_detect_market_moving_before_probability(cfg: dict) -> None:
    _position(cfg)
    _event(
        cfg, "monitor-1", "p1", 1, "MONITOR_REFRESHED",
        "2026-08-22T09:00:00+00:00",
        phase_before="active", phase_after="active",
        payload={
            "last_monitor_prob": 0.30,
            "last_monitor_market_price": 0.30,
            "last_monitor_prob_is_fresh": True,
            "last_monitor_market_price_is_fresh": True,
        },
    )
    _event(
        cfg, "monitor-2", "p1", 2, "MONITOR_REFRESHED",
        "2026-08-22T09:00:10+00:00",
        phase_before="active", phase_after="active",
        payload={
            "last_monitor_prob": 0.30,
            "last_monitor_market_price": 0.20,
            "last_monitor_prob_is_fresh": True,
            "last_monitor_market_price_is_fresh": True,
        },
    )

    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        probability_velocity, market_velocity, probability, fresh, _ = loop._monitor_dynamics(
            trades,
            "p1",
        )

    assert probability_velocity == pytest.approx(0.0)
    assert market_velocity == pytest.approx(-0.01)
    assert probability == pytest.approx(0.30)
    assert fresh is True


def test_evidence_db_exposes_timeline_tables_without_copying_canonical_db(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]

    evidence = loop.build_evidence(cfg, incident_id)

    with sqlite3.connect(evidence) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "incident", "position", "price_ticks", "probability_ticks",
            "source_clocks", "monitor_events", "exit_decisions",
            "venue_commands", "order_facts", "trade_facts", "fills",
            "daemon_health", "code_versions", "config_snapshot",
        } <= tables
        assert conn.execute("SELECT COUNT(*) FROM price_ticks").fetchone()[0] >= 1
    assert evidence.stat().st_size < Path(cfg["paths"]["trades_db"]).stat().st_size * 20


def test_evidence_wallet_fills_follow_command_trade_ids_without_token_scan(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _position(cfg)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
            ("exit-command", "p1", "2026-08-22T09:00:03+00:00", "2026-08-22T09:00:03+00:00", "submitted"),
        )
        conn.execute(
            "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?)",
            (1, "exit-command", "trade-match", "2026-08-22T09:00:04+00:00", 1, "0.01", "10"),
        )
        conn.executemany(
            "INSERT INTO wallet_fill_observations VALUES (?,?,?,?,?,?)",
            [
                (1, "yes-token", "trade-match", "2026-08-22T09:00:05+00:00", "0.01", "10"),
                (2, "yes-token", "decoy-trade", "2026-08-22T09:00:06+00:00", "0.01", "999"),
            ],
        )

    queries: list[str] = []
    original_open_ro = loop.open_ro

    def traced_open_ro(path: Path):
        conn = original_open_ro(path)
        if Path(path) == Path(cfg["paths"]["trades_db"]):
            conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr(loop, "open_ro", traced_open_ro)
    evidence = loop.build_evidence(cfg, incident_id)

    wallet_queries = [query for query in queries if "wallet_fill_observations" in query]
    assert len(wallet_queries) == 1
    assert "WHERE trade_id IN ('trade-match')" in wallet_queries[0]
    assert "token_id" not in wallet_queries[0]
    with sqlite3.connect(evidence) as conn:
        rows = [json.loads(row[0]) for row in conn.execute("SELECT raw_json FROM fills")]
    assert [row["trade_id"] for row in rows] == ["trade-match"]


def test_codex_command_persists_primary_and_places_approval_before_exec(cfg: dict) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runtime.mkdir(parents=True)
    loop.atomic_json(runtime / "capabilities.json", {"reasoning_effort": "high"})
    schema = runtime / "schema.json"
    output = runtime / "output.json"
    loop.atomic_json(schema, {"type": "object"})

    command = loop._codex_exec_base(
        cfg,
        sandbox="read-only",
        cwd=ROOT,
        schema=schema,
        output=output,
        persistent=True,
    )

    assert command[1:4] == ["-a", "never", "exec"]
    assert "--ephemeral" not in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "features.memories=false" in command


def test_runtime_is_single_repo_local_directory(cfg: dict) -> None:
    result = loop.bootstrap(cfg)

    assert Path(result["runtime"]) == Path(cfg["paths"]["runtime"])
    assert Path(result["memory"]).parent == Path(cfg["paths"]["runtime"])


def test_unverified_delivery_claim_cannot_complete_incident(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    with loop.memory(cfg) as mem:
        loop.transition(mem, incident_id, "delivery", reason="test")
        mem.commit()

    loop._after_delivery(
        cfg,
        {"incident_id": incident_id, "run_id": "delivery-run", "kind": "hard"},
        {
            "incident_id": incident_id,
            "status": "merged",
            "pr": "https://github.com/fitz-s/zeus/pull/1",
            "head_sha": "not-a-sha",
            "merge_sha": "also-not-a-sha",
            "verification": [],
            "blocker": None,
        },
    )

    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT stage,status FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
    assert tuple(row) == ("delivery", "retry_pending")


def test_capability_fingerprint_changes_with_profile_content(cfg: dict) -> None:
    before = loop._capability_fingerprint(cfg)
    cfg["profiles"]["test"]["model"] = "gpt-5.6-luna"

    assert loop._capability_fingerprint(cfg) != before


@pytest.mark.parametrize("effort", ["medium", "xhigh", "max", "ultra"])
def test_current_capabilities_rejects_any_non_high_effort(
    cfg: dict,
    effort: str,
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runtime.mkdir(parents=True)
    loop.atomic_json(
        runtime / "capabilities.json",
        {
            "model": "gpt-5.6-sol",
            "reasoning_effort": effort,
            "structured_output_ok": True,
            "workspace_write_ok": True,
            "delivery_network_ok": True,
            "resume_ok": True,
            "multi_agent_ok": True,
        },
    )
    loop.atomic_json(
        runtime / "capability-fingerprint.json",
        {"value": loop._capability_fingerprint(cfg)},
    )

    assert loop.current_capabilities(cfg) is None


def test_codex_exec_rejects_non_high_override(cfg: dict) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runtime.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="reasoning_effort=high"):
        loop._codex_exec_base(
            cfg,
            sandbox="read-only",
            cwd=ROOT,
            schema=runtime / "schema.json",
            output=runtime / "output.json",
            persistent=True,
            reasoning_effort="max",
        )


def test_untyped_retry_cannot_replay_persisted_non_high_command(cfg: dict) -> None:
    with pytest.raises(RuntimeError, match="without a typed stage"):
        loop._retry_command(
            cfg,
            {
                "stage": "unknown",
                "command": ["codex", "-c", 'model_reasoning_effort="max"'],
            },
        )


def test_failed_repair_retry_resumes_persistent_session(cfg: dict, monkeypatch) -> None:
    monkeypatch.setattr(loop, "capabilities", lambda _cfg: {"reasoning_effort": "high"})
    output = Path(cfg["paths"]["runtime"]) / "patch.json"
    command = loop._retry_command(
        cfg,
        {
            "stage": "repair",
            "session_id": "session-123",
            "output": str(output),
            "command": ["must-not-replay-original"],
        },
    )

    assert command[1:5] == ["-a", "never", "exec", "resume"]
    assert command[5] == "session-123"
    assert "must-not-replay-original" not in command


@pytest.mark.parametrize(
    ("changed_path", "conclusion", "expected_blocker"),
    [
        ("src/engine/monitor_refresh.py", "FAILURE", "pr_checks_not_green:money-path-required"),
        ("src/engine/monitor_refresh.py", "SKIPPED", "pr_checks_not_green:money-path-required"),
        ("config/settings.json", "SUCCESS", "automation_forbidden_paths:config/settings.json"),
        ("src/execution/command_bus.py", "SUCCESS", "automation_forbidden_paths:src/execution/command_bus.py"),
        ("src/risk_allocator/example.py", "SUCCESS", "automation_forbidden_paths:src/risk_allocator/example.py"),
        ("src/strategy/risk_limits.py", "SUCCESS", "automation_forbidden_paths:src/strategy/risk_limits.py"),
        ("src/state/schema_introspection.py", "SUCCESS", "automation_forbidden_paths:src/state/schema_introspection.py"),
        ("scripts/migrate_example.py", "SUCCESS", "automation_forbidden_paths:scripts/migrate_example.py"),
        ("src/state/lifecycle_manager.py", "SUCCESS", "automation_forbidden_paths:src/state/lifecycle_manager.py"),
        ("src/state/venue_command_repo.py", "SUCCESS", "automation_forbidden_paths:src/state/venue_command_repo.py"),
    ],
)
def test_controller_rejects_unsafe_merged_pr(
    cfg: dict,
    monkeypatch,
    changed_path: str,
    conclusion: str,
    expected_blocker: str,
) -> None:
    incident_id = "incident-delivery-proof"
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    incident_dir.mkdir(parents=True)
    head = "a" * 40
    merge = "b" * 40
    loop.atomic_json(
        incident_dir / "delivery.json",
        {
            "incident_id": incident_id,
            "status": "merged",
            "pr": "1",
            "head_sha": head,
            "merge_sha": merge,
            "verification": [],
            "blocker": None,
        },
    )
    pr_fact = {
        "state": "MERGED",
        "headRefOid": head,
        "mergeCommit": {"oid": merge},
        "statusCheckRollup": [
            {"name": "money-path-required", "status": "COMPLETED", "conclusion": conclusion}
        ],
        "reviews": [],
    }
    def fake_capture(command, **kwargs):  # noqa: ANN001, ARG001
        if command[:3] == ["gh", "pr", "view"]:
            payload = pr_fact
        elif command[:3] == ["gh", "repo", "view"]:
            payload = {"nameWithOwner": "fitz-s/zeus"}
        elif command[:2] == ["gh", "api"]:
            payload = [[{"filename": changed_path, "status": "modified"}]]
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(loop, "_run_capture", fake_capture)

    assert loop.deploy_incident(cfg, incident_id) == 0

    result = json.loads((incident_dir / "production.json").read_text())
    assert result["status"] == "blocked"
    assert result["blocker"] == expected_blocker


def test_repair_requires_preprovisioned_managed_worktree(cfg: dict, monkeypatch) -> None:
    monkeypatch.delenv("ZEUS_TOTAL_LOSS_REPAIR_WORKTREE", raising=False)

    with pytest.raises(RuntimeError, match="managed repair worktree is not provisioned"):
        loop._worktree(cfg, "incident-1")


def test_slow_dispatch_does_not_create_detector_budget_breach(cfg: dict) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runtime.mkdir(parents=True)

    loop._record_cycle_latency(cfg, detector_elapsed=0.01, total_elapsed=3.0)

    assert not (runtime / "detector-budget-breach.json").exists()
    latency = json.loads((runtime / "cycle-latency.json").read_text())
    assert latency["detector_ms"] == 10.0
    assert latency["total_ms"] == 3000.0


def _classified_incident(cfg: dict, *, avoidable: float, preventable_at: str | None) -> str:
    incident_id = "classified-incident"
    runtime = Path(cfg["paths"]["runtime"])
    incident_dir = runtime / "incidents" / incident_id
    incident_dir.mkdir(parents=True)
    loop.atomic_json(
        incident_dir / "diagnosis.json",
        {
            "incident_id": incident_id,
            "causal_seam": "test seam",
            "changed_symbols": ["src.engine.monitor_refresh"],
            "evidence_refs": ["evidence.db:test"],
            "earliest_preventable_time": preventable_at,
            "capital_counterfactual": {"avoidable_loss_usd": avoidable},
        },
    )
    classification = {
        "incident_id": incident_id,
        "root_id": "root-test",
        "relation": "new_root",
        "mechanism_fingerprint": "test",
    }
    loop.atomic_json(incident_dir / "classification.json", classification)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "running", "classification", "2026-08-22T10:00:00+00:00"),
        )
        mem.commit()
    return incident_id


def test_zero_avoidable_loss_never_enters_repair(cfg: dict, monkeypatch) -> None:
    incident_id = _classified_incident(cfg, avoidable=0.0, preventable_at=None)
    monkeypatch.setattr(loop, "_worktree", lambda *_args, **_kwargs: pytest.fail("repair worktree used"))
    classification = json.loads(
        (Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "classification.json").read_text()
    )

    loop._after_classification(
        cfg, {"incident_id": incident_id, "kind": "hard", "run_id": "run"}, classification
    )

    with loop.memory(cfg) as mem:
        row = mem.execute("SELECT stage,status FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
    assert tuple(row) == ("observing", "observing")


def test_preventable_loss_queues_repair_without_claiming_workspace(cfg: dict, monkeypatch) -> None:
    incident_id = _classified_incident(
        cfg, avoidable=3.0, preventable_at="2026-08-22T09:59:00+00:00"
    )
    monkeypatch.setattr(loop, "_worktree", lambda *_args, **_kwargs: pytest.fail("repair worktree used"))
    classification = json.loads(
        (Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "classification.json").read_text()
    )

    loop._after_classification(
        cfg, {"incident_id": incident_id, "kind": "hard", "run_id": "run"}, classification
    )

    with loop.memory(cfg) as mem:
        row = mem.execute("SELECT stage,status FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
    assert tuple(row) == ("repair_waiting", "queued")


def test_fresh_review_uses_structured_ephemeral_exec_not_invalid_exec_review(
    cfg: dict, monkeypatch
) -> None:
    incident_id = "review-command"
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    incident_dir.mkdir(parents=True)
    loop.atomic_json(Path(cfg["paths"]["runtime"]) / "capabilities.json", {"reasoning_effort": "high"})
    captured = {}
    monkeypatch.setattr(loop, "_ensure_repair_commit", lambda *_args: "a" * 40)

    def capture_spawn(*_args, **kwargs):
        captured.update(kwargs)
        return {"run_id": "review-run", "session_id": None}

    monkeypatch.setattr(loop, "_spawn_run", capture_spawn)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "running", "repair", "2026-08-22T10:00:00+00:00"),
        )
        mem.commit()

    loop._after_repair(
        cfg,
        {"incident_id": incident_id, "kind": "hard", "stage": "repair", "cwd": str(ROOT), "run_id": "repair-run"},
        {"status": "patch_ready", "replay": {"passed": True}, "commit_sha": None},
    )

    command = captured["command"]
    assert command[1:3] == ["-a", "never"]
    assert "exec" in command
    assert "review" not in command
    assert "--ephemeral" in command


def test_blocking_review_starts_fresh_workspace_write_feedback(cfg: dict, monkeypatch) -> None:
    incident_id = "review-feedback"
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    incident_dir.mkdir(parents=True)
    loop.atomic_json(Path(cfg["paths"]["runtime"]) / "capabilities.json", {"reasoning_effort": "high"})
    captured = {}

    def capture_spawn(*_args, **kwargs):
        captured.update(kwargs)
        return {"run_id": "feedback-run", "session_id": None}

    monkeypatch.setattr(loop, "_spawn_run", capture_spawn)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "running", "review", "2026-08-22T10:00:00+00:00"),
        )
        mem.commit()

    loop._after_review(
        cfg,
        {"incident_id": incident_id, "kind": "hard", "cwd": str(ROOT), "repair_session_id": "old-read-only"},
        {"blocking": True, "findings": [], "coverage": "test"},
    )

    command = captured["command"]
    assert "resume" not in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert captured.get("session_id") is None
    assert f"incident_id={incident_id}" in captured["prompt"]


def test_feedback_retry_is_fresh_and_preserves_exact_incident_envelope(
    cfg: dict, monkeypatch
) -> None:
    incident_id = "full-incident-identity-123456"
    runtime = Path(cfg["paths"]["runtime"])
    events = runtime / "incidents" / incident_id / "feedback.jsonl"
    events.parent.mkdir(parents=True)
    events.with_suffix(".prompt.md").write_text("repair the reviewed finding")
    output = events.with_name("patch.json")
    loop.atomic_json(runtime / "capabilities.json", {"reasoning_effort": "high"})
    prior = {
        "incident_id": incident_id,
        "stage": "repair_feedback",
        "session_id": "contaminated-session",
        "cwd": str(events.parent),
        "output": str(output),
        "events": str(events),
        "command": ["codex", "exec", "resume", "contaminated-session"],
        "completed_at": "2026-08-22T09:00:00+00:00",
        "status": "failed",
    }
    loop.atomic_json(runtime / "runs" / "prior.json", prior)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "retry_pending", "repair_feedback", "2026-08-22T09:00:00+00:00"),
        )
        mem.commit()
    captured = {}

    def capture_spawn(*_args, **kwargs):
        captured.update(kwargs)
        return {"run_id": "fresh-feedback", "session_id": None}

    monkeypatch.setattr(loop, "_spawn_run", capture_spawn)
    monkeypatch.setattr(loop, "now", lambda: loop.parse_time("2026-08-22T10:05:00+00:00"))

    assert loop._retry_pending(cfg, []) == [incident_id]
    assert "resume" not in captured["command"]
    assert f"incident_id={incident_id}" in captured["prompt"]
    assert captured["session_id"] is None


def test_retry_does_not_start_second_writer_in_same_worktree(
    cfg: dict, monkeypatch, tmp_path: Path
) -> None:
    incident_id = "feedback-waiting"
    runtime = Path(cfg["paths"]["runtime"])
    events = runtime / "incidents" / incident_id / "feedback.jsonl"
    events.parent.mkdir(parents=True)
    events.with_suffix(".prompt.md").write_text("repair")
    prior = {
        "incident_id": incident_id,
        "stage": "repair_feedback",
        "cwd": str(tmp_path),
        "output": str(events.with_name("patch.json")),
        "events": str(events),
        "command": ["codex", "exec"],
        "completed_at": "2026-08-22T09:00:00+00:00",
        "status": "failed",
    }
    loop.atomic_json(runtime / "runs" / "prior.json", prior)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "retry_pending", "repair_feedback", "2026-08-22T09:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: pytest.fail("second writer spawned"))
    monkeypatch.setattr(loop, "now", lambda: loop.parse_time("2026-08-22T10:05:00+00:00"))
    running = [{
        "incident_id": "other-repair",
        "stage": "repair",
        "cwd": str(tmp_path),
        "status": "running",
    }]

    assert loop._retry_pending(cfg, running) == []


def test_blocking_review_defers_feedback_while_worktree_writer_runs(
    cfg: dict, monkeypatch, tmp_path: Path
) -> None:
    incident_id = "review-must-wait"
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    incident_dir.mkdir(parents=True)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "running", "review", "2026-08-22T10:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(
        loop,
        "_running",
        lambda _cfg: [{
            "incident_id": "other-repair",
            "stage": "repair",
            "cwd": str(tmp_path),
            "status": "running",
        }],
    )
    monkeypatch.setattr(
        loop,
        "_spawn_run",
        lambda *_args, **_kwargs: pytest.fail("concurrent feedback writer spawned"),
    )

    loop._after_review(
        cfg,
        {
            "incident_id": incident_id,
            "kind": "hard",
            "cwd": str(tmp_path),
            "run_id": "review-run",
        },
        {"blocking": True, "findings": ["fix me"], "coverage": "test"},
    )

    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT stage,status FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
    assert tuple(row) == ("review", "retry_pending")


def test_writer_lease_is_atomic_per_canonical_cwd(cfg: dict, tmp_path: Path) -> None:
    worktree = tmp_path / "writer-worktree"
    worktree.mkdir()

    loop._acquire_writer_lease(
        cfg,
        cwd=worktree,
        run_id="writer-one",
        stage="repair",
    )
    with pytest.raises(loop.WriterLeaseBusy, match="workspace writer busy"):
        loop._acquire_writer_lease(
            cfg,
            cwd=worktree / ".",
            run_id="writer-two",
            stage="repair_feedback",
        )

    loop._release_writer_lease(cfg, cwd=worktree, run_id="writer-one")
    loop._acquire_writer_lease(
        cfg,
        cwd=worktree,
        run_id="writer-two",
        stage="production",
    )
    loop._release_writer_lease(cfg, cwd=worktree, run_id="writer-two")


def test_production_defers_when_atomic_writer_lease_is_busy(
    cfg: dict, monkeypatch
) -> None:
    incident_id = "delivery-waits-for-production-lease"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,t_floor,floor_price,observed_bid,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "q1", "below_floor", "yes", "sell_yes",
             "2026-08-22T10:00:00+00:00", 0.05, 0.04, "2026-08-22T10:00:00+00:00",
             1_000_000.0, "running", "delivery", "2026-08-22T10:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(
        loop,
        "_spawn_controller_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            loop.WriterLeaseBusy("production busy")
        ),
    )

    loop._after_delivery(
        cfg,
        {
            "incident_id": incident_id,
            "kind": "hard",
            "run_id": "delivery-run",
        },
        {
            "status": "merged",
            "pr": "https://example.test/pr/1",
            "head_sha": "a" * 40,
            "merge_sha": "b" * 40,
        },
    )

    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT stage,status FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
    assert tuple(row) == ("delivery", "retry_pending")


def test_post_bind_record_failure_terminates_child_and_releases_lease(
    cfg: dict, monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, object]] = []

    class Child:
        pid = 424242

    monkeypatch.setattr(
        loop.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Child(),
    )
    monkeypatch.setattr(
        loop,
        "_acquire_writer_lease",
        lambda *_args, **kwargs: calls.append(("acquire", kwargs["run_id"])),
    )
    monkeypatch.setattr(
        loop,
        "_bind_writer_lease_child",
        lambda *_args, **kwargs: calls.append(("bind", kwargs["child_pid"])),
    )
    monkeypatch.setattr(
        loop,
        "_terminate_process_group",
        lambda pid: calls.append(("terminate", pid)),
    )
    monkeypatch.setattr(
        loop,
        "_release_writer_lease",
        lambda *_args, **kwargs: calls.append(("release", kwargs["run_id"])),
    )
    monkeypatch.setattr(
        loop,
        "atomic_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        loop._spawn_controller_run(
            cfg,
            incident_id="persistence-failure",
            kind="hard",
            stage="production",
            command=["controller"],
            cwd=tmp_path,
            output=tmp_path / "production.json",
            events=tmp_path / "controller.jsonl",
        )

    assert [name for name, _value in calls] == [
        "acquire",
        "bind",
        "terminate",
        "release",
    ]


def test_dead_child_completed_run_lease_is_reclaimable(
    cfg: dict, tmp_path: Path
) -> None:
    worktree = tmp_path / "reclaim-worktree"
    worktree.mkdir()
    runtime = Path(cfg["paths"]["runtime"])
    loop.atomic_json(
        runtime / "runs" / "old-run.json",
        {
            "run_id": "old-run",
            "status": "completed",
            "lease_finalization_complete": True,
        },
    )
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO workspace_writer_leases"
            "(cwd,run_id,stage,owner_pid,child_pid,lock_path,acquired_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                str(worktree.resolve()),
                "old-run",
                "repair",
                os.getpid(),
                999999,
                str(tmp_path / "old-run.lock"),
                "2026-08-22T00:00:00+00:00",
            ),
        )
        mem.commit()

    loop._acquire_writer_lease(
        cfg,
        cwd=worktree,
        run_id="new-run",
        stage="repair_feedback",
    )
    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT run_id FROM workspace_writer_leases WHERE cwd=?",
            (str(worktree.resolve()),),
        ).fetchone()
    assert row["run_id"] == "new-run"
    loop._release_writer_lease(cfg, cwd=worktree, run_id="new-run")


def test_completed_child_lease_stays_busy_until_post_child_callback_finishes(
    cfg: dict, tmp_path: Path
) -> None:
    worktree = tmp_path / "finalizing-worktree"
    worktree.mkdir()
    runtime = Path(cfg["paths"]["runtime"])
    loop.atomic_json(
        runtime / "runs" / "finalizing-run.json",
        {"run_id": "finalizing-run", "status": "completed"},
    )
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO workspace_writer_leases"
            "(cwd,run_id,stage,owner_pid,child_pid,lock_path,acquired_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                str(worktree.resolve()),
                "finalizing-run",
                "repair",
                os.getpid(),
                999999,
                str(tmp_path / "finalizing-run.lock"),
                "2026-08-22T00:00:00+00:00",
            ),
        )
        mem.commit()

    with pytest.raises(loop.WriterLeaseBusy, match="workspace writer busy"):
        loop._acquire_writer_lease(
            cfg,
            cwd=worktree,
            run_id="too-early",
            stage="repair_feedback",
        )
    loop._release_writer_lease(
        cfg,
        cwd=worktree,
        run_id="finalizing-run",
    )


def test_repair_branch_provisioning_occurs_after_atomic_lease_before_popen(
    cfg: dict, monkeypatch, tmp_path: Path
) -> None:
    order: list[str] = []
    runtime = Path(cfg["paths"]["runtime"])
    loop.atomic_json(runtime / "capabilities.json", {"reasoning_effort": "high"})
    monkeypatch.setattr(
        loop,
        "capabilities",
        lambda _cfg: {"reasoning_effort": "high"},
    )

    class Child:
        pid = 515151

    monkeypatch.setattr(
        loop,
        "_acquire_writer_lease",
        lambda *_args, **_kwargs: order.append("lease"),
    )
    monkeypatch.setattr(
        loop,
        "_ensure_writer_worktree_branch",
        lambda *_args, **_kwargs: order.append("branch"),
    )
    monkeypatch.setattr(
        loop.subprocess,
        "Popen",
        lambda *_args, **_kwargs: order.append("popen") or Child(),
    )
    monkeypatch.setattr(
        loop,
        "_bind_writer_lease_child",
        lambda *_args, **_kwargs: order.append("bind"),
    )

    record = loop._spawn_run(
        cfg,
        incident_id="branch-ordering",
        kind="hard",
        stage="repair",
        command=["codex", "exec"],
        cwd=tmp_path,
        prompt="repair",
        output=tmp_path / "patch.json",
        events=tmp_path / "repair.jsonl",
        workspace_branch="test/total-loss/branch-order",
    )

    assert order == ["lease", "branch", "popen", "bind"]
    assert record["workspace_branch"] == "test/total-loss/branch-order"


def test_orphan_child_kernel_lock_closes_popen_bind_crash_gap(
    cfg: dict, tmp_path: Path
) -> None:
    worktree = tmp_path / "orphan-worktree"
    worktree.mkdir()
    lease_fd = loop._acquire_writer_lease(
        cfg,
        cwd=worktree,
        run_id="orphan-run",
        stage="repair",
    )
    child = subprocess.Popen(
        [loop.sys.executable, "-c", "import time; time.sleep(30)"],
        pass_fds=(lease_fd,),
        start_new_session=True,
    )
    parent_fd = loop._writer_lease_lock_fds.pop("orphan-run")
    os.close(parent_fd)
    with loop.memory(cfg) as mem:
        mem.execute(
            "UPDATE workspace_writer_leases SET owner_pid=?,child_pid=NULL "
            "WHERE run_id='orphan-run'",
            (999999,),
        )
        mem.commit()

    try:
        with pytest.raises(loop.WriterLeaseBusy, match="workspace writer busy"):
            loop._acquire_writer_lease(
                cfg,
                cwd=worktree,
                run_id="must-not-reclaim",
                stage="repair_feedback",
            )
    finally:
        os.killpg(child.pid, 15)
        child.wait(timeout=5)

    loop._acquire_writer_lease(
        cfg,
        cwd=worktree,
        run_id="after-orphan-exit",
        stage="repair_feedback",
    )
    loop._release_writer_lease(
        cfg,
        cwd=worktree,
        run_id="after-orphan-exit",
    )


def test_retry_preserves_only_same_incident_branch_owned_dirty_patch(
    cfg: dict, monkeypatch, tmp_path: Path
) -> None:
    branch = "test/total-loss/owned-dirty"

    def capture(command, *, cwd, **_kwargs):
        assert cwd == tmp_path.resolve()
        if command[:3] == ["git", "branch", "--show-current"]:
            return subprocess.CompletedProcess(command, 0, branch + "\n", "")
        if command[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, " M src/owned.py\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(loop, "_run_capture", capture)

    loop._ensure_writer_worktree_branch(
        cfg,
        cwd=tmp_path,
        branch=branch,
        allow_owned_dirty=True,
    )
    with pytest.raises(RuntimeError, match="worktree is dirty"):
        loop._ensure_writer_worktree_branch(
            cfg,
            cwd=tmp_path,
            branch="test/total-loss/other-incident",
            allow_owned_dirty=True,
        )
