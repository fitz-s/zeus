# Lifecycle: created=2026-08-22; last_reviewed=2026-08-28; last_reused=2026-08-28
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
from datetime import UTC, datetime, timedelta
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
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (1,'London','2026-08-22','high','cycle','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO ensemble_snapshots VALUES (1,'London','2026-08-22','high','cycle','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (2,'Tel Aviv','2026-08-22','high','cycle','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO ensemble_snapshots VALUES (2,'Tel Aviv','2026-08-22','high','cycle','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00','2026-08-22T09:00:00+00:00')"
        )


def test_sqlite_factories_close_on_context_exit(cfg: dict) -> None:
    connections = (
        loop.open_ro(Path(cfg["paths"]["trades_db"])),
        loop.memory(cfg),
        loop.memory_ro(cfg),
    )
    for connection in connections:
        with connection as active:
            active.execute("SELECT 1").fetchone()
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_sqlite_factories_close_after_context_exception(cfg: dict) -> None:
    connection = loop.memory(cfg)
    with pytest.raises(RuntimeError, match="boom"):
        with connection:
            raise RuntimeError("boom")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


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


def _position(
    cfg: dict,
    *,
    position_id: str = "p1",
    direction: str = "buy_yes",
    token_id: str = "yes-token",
) -> None:
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                position_id, "active", f"trade-{position_id}", "market-1", "London",
                "2026-08-22", "28C", direction, "C", 10.0, 10.0, 5.0, None,
                0.5, token_id, ("no-token" if token_id == "yes-token" else f"no-{token_id}"), "condition-1", None,
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


def _evidence_quote_triplet(cfg: dict, evidence_id: str, at: str, bid: float | None) -> None:
    stamp = loop.parse_time(at)
    assert stamp is not None
    _quote(cfg, f"{evidence_id}-pre", loop.iso(stamp - timedelta(seconds=1)), 0.08, latest=False)
    _quote(cfg, evidence_id, at, bid)
    _quote(cfg, f"{evidence_id}-post", loop.iso(stamp + timedelta(seconds=1)), 0.08, latest=False)


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


def test_settlement_aggregate_materialization_drift_is_idempotent(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg, payload={"outcome": 0})
    aggregate = {
        **_command_dedup_basis(),
        "execution_fact_command_ids": ["entry-command-before"],
    }
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", lambda *_args, **_kwargs: aggregate)
    assert len(loop.detect(cfg)) == 1
    with loop.memory(cfg) as mem:
        first = mem.execute(
            "SELECT incident_id,evidence_revision FROM incidents "
            "WHERE crossing_kind='settlement_full_loss'"
        ).fetchone()
    aggregate = {
        "filled_cost_basis_usd": 4.99,
        "entry_fill_command_identity_complete": True,
        "execution_fact_command_ids": ["entry-command-after"],
    }
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET realized_pnl_usd=?,updated_at=? WHERE position_id='p-settled'",
            (-4.99, "2026-08-22T11:00:00+00:00"),
        )
    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        second = mem.execute(
            "SELECT incident_id,evidence_revision FROM incidents "
            "WHERE crossing_kind='settlement_full_loss'"
        ).fetchone()
        row_count = mem.execute(
            "SELECT COUNT(*) FROM incidents WHERE crossing_kind='settlement_full_loss'"
        ).fetchone()[0]
    assert second[0] == first[0]
    assert second[1] == first[1]
    assert row_count == 1


def test_settlement_backfill_policy_revision_replays_legacy_consolidation_once(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg, payload={"outcome": 0})
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.row_factory = sqlite3.Row
        position = dict(conn.execute(
            "SELECT * FROM position_current WHERE position_id='p-settled'"
        ).fetchone())
        terminal = conn.execute(
            "SELECT payload_json FROM position_events WHERE event_id='settled-p-settled'"
        ).fetchone()
    payload = json.loads(str(terminal[0]))
    legacy_fingerprint = loop.digest(
        loop.digest(
            position.get("position_id"), position.get("settled_at"),
            position.get("updated_at"), position.get("realized_pnl_usd"),
            position.get("settlement_price"), position.get("shares"),
            position.get("chain_shares"),
        ),
        loop._settlement_economic_identity(position, payload) or "",
    )
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO settlement_backfill_state(position_id,fingerprint,completed,updated_at) "
            "VALUES ('p-settled',?,1,?)",
            (legacy_fingerprint, "2026-08-22T10:00:00+00:00"),
        )
        for incident_id, status in (("legacy-queued", "queued"), ("legacy-running", "running")):
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    incident_id, "hard", "p-settled", incident_id,
                    "settlement_full_loss", "yes-token", "sell_yes", 0.05,
                    "2026-08-22T10:00:00+00:00", 1_000_000.0, status, "blind",
                    "2026-08-22T10:00:00+00:00",
                ),
            )
        mem.commit()

    assert len(loop.detect(cfg)) == 1
    with loop.memory(cfg) as mem:
        first = {
            row["incident_id"]: (row["status"], row["updated_at"])
            for row in mem.execute(
                "SELECT incident_id,status,updated_at FROM incidents "
                "WHERE position_id='p-settled' ORDER BY incident_id"
            )
        }
        state = mem.execute(
            "SELECT fingerprint,completed FROM settlement_backfill_state "
            "WHERE position_id='p-settled'"
        ).fetchone()
    assert first["legacy-queued"][0] == "observing"
    assert first["legacy-running"][0] == "running"
    assert state["completed"] == 1
    assert state["fingerprint"] != legacy_fingerprint

    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        second = {
            row["incident_id"]: (row["status"], row["updated_at"])
            for row in mem.execute(
                "SELECT incident_id,status,updated_at FROM incidents "
                "WHERE position_id='p-settled' ORDER BY incident_id"
            )
        }
    # The first bounded evidence slice may leave the newly consolidated
    # settlement incident blocked; the next slice resolves that exact debt
    # once, then the policy fingerprint prevents another replay.
    changed = [incident_id for incident_id in first if second[incident_id] != first[incident_id]]
    assert len(changed) <= 1
    if changed:
        assert second[changed[0]][0] == "queued"
    assert loop.detect(cfg) == []


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


def test_stable_settlement_consolidates_legacy_duplicates_without_collateral(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled_full_loss(cfg, payload={"outcome": 0})
    monkeypatch.setattr(loop, "_entry_execution_fill_aggregate", _command_dedup_basis)
    assert len(loop.detect(cfg)) == 1
    with loop.memory(cfg) as mem:
        for index in range(13):
            status = "running" if index == 12 else ("queued" if index % 2 else "retry_pending")
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"legacy-{index}", "hard", "p-settled", f"legacy-evidence-{index}",
                    "settlement_full_loss", "yes-token", "sell_yes", 0.05,
                    "2026-08-22T10:00:00+00:00", 1_000_000.0, status, "blind",
                    "2026-08-22T10:00:00+00:00",
                ),
            )
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES ('quote-collateral','hard','p-settled','quote-evidence','below_floor',"
            "'yes-token','sell_yes',.05,?,1,'queued','blind',?)",
            ("2026-08-22T10:00:00+00:00", "2026-08-22T10:00:00+00:00"),
        )
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES ('other-position','hard','other-position','other-evidence','settlement_full_loss',"
            "'yes-token','sell_yes',.05,?,1,'queued','blind',?)",
            ("2026-08-22T10:00:00+00:00", "2026-08-22T10:00:00+00:00"),
        )
        mem.commit()

    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        statuses = {
            row["incident_id"]: row["status"]
            for row in mem.execute(
                "SELECT incident_id,status FROM incidents WHERE incident_id LIKE 'legacy-%'"
            )
        }
        assert sum(status == "observing" for status in statuses.values()) == 12
        assert statuses["legacy-12"] == "running"
        assert mem.execute(
            "SELECT status FROM incidents WHERE incident_id='quote-collateral'"
        ).fetchone()[0] == "queued"
        assert mem.execute(
            "SELECT status FROM incidents WHERE incident_id='other-position'"
        ).fetchone()[0] == "queued"
        mem.execute(
            "UPDATE incidents SET status='retry_pending' WHERE incident_id='legacy-12'"
        )
        mem.commit()
    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT status FROM incidents WHERE incident_id='legacy-12'"
        ).fetchone()[0] == "observing"


def test_rc0_turn_failed_is_failed_and_requeues_incident(cfg: dict) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="turn-failed")
    with loop.memory(cfg) as mem:
        mem.execute("UPDATE incidents SET status='running',stage='diagnosis' WHERE incident_id='turn-failed'")
        mem.commit()
    runtime = Path(cfg["paths"]["runtime"])
    events = runtime / "runs" / "turn-failed.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps({"type": "error", "error": {"message": "You've hit your usage limit. Visit ... or try again at Aug 26th, 2026 10:37 PM."}})
        + "\n"
        + json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Visit ... or try again at Aug 26th, 2026 10:37 PM."}})
        + "\n"
    )
    run = {
        "run_id": "turn-failed", "incident_id": "turn-failed", "stage": "diagnosis",
        "events": str(events), "pid": os.getpid(), "status": "running",
    }
    loop._finish_run_inner(cfg, run, 0)
    assert run["status"] == "failed"
    assert run["terminal_failure"]["kind"] == "provider_quota_limit"
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT status FROM incidents WHERE incident_id='turn-failed'"
        ).fetchone()[0] == "retry_pending"
        reason = mem.execute(
            "SELECT reason FROM incident_transitions WHERE incident_id='turn-failed'"
        ).fetchone()[0]
    assert "codex_terminal_failure:provider_quota_limit" in reason
    backoff = loop._provider_backoff(cfg)
    assert backoff is not None
    assert (loop.parse_time(backoff["next_retry_at"]) - loop.now()).total_seconds() > 23 * 3600


def test_provider_backoff_suppresses_global_launch_but_detector_continues(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    with loop.memory(cfg) as mem:
        loop._set_provider_backoff(
            cfg, mem,
            {"kind": "provider_quota_limit", "reason": "quota", "retry_at": "2099-01-01T00:00:00+00:00"},
        )
        mem.commit()
    _queue_blind_dispatch_debt(cfg, incident_id="blocked-by-provider")
    _position(cfg)
    _evidence_quote_triplet(cfg, "q1", "2026-08-22T09:00:02+00:00", 0.01)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: pytest.fail("provider backoff must block all new model runs"))
    assert loop.dispatch(cfg) == []
    assert loop._dispatch_has_eligible_debt(cfg, []) is False
    provider_incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / "blocked-by-provider"
    assert (provider_incident_dir / "CURRENT").is_file()
    assert loop._evidence_pair_valid(cfg, "blocked-by-provider")

    _quote(cfg, "provider-detector-q", "2026-08-22T09:00:02+00:00", 0.01)
    assert loop.detect(cfg)
    assert any(row["incident_id"] == "blocked-by-provider" for row in _incidents(cfg))


def test_hard_evidence_maintenance_is_capacity_bounded_and_prioritizes_new_active_tel_aviv(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(
        evidence_builds_per_cycle=1,
        evidence_build_budget_ms=5000,
        evidence_max_bytes=1024 * 1024,
    )
    _position(cfg, position_id="tel-old")
    _position(cfg, position_id="tel-new")
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET city='Tel Aviv',updated_at=? WHERE position_id IN ('tel-old','tel-new')",
            ("2026-08-22T12:00:00+00:00",),
        )
    with loop.memory(cfg) as mem:
        for index in range(118):
            position_id = "tel-new" if index == 0 else ("tel-old" if index == 1 else "p1")
            detected_at = "2026-08-22T12:00:00+00:00" if index == 0 else (
                "2026-08-22T11:00:00+00:00" if index == 1 else "2026-08-20T00:00:00+00:00"
            )
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"historical-{index:03d}", "hard", position_id, f"evidence-{index:03d}",
                    "below_floor", "yes-token", "sell_yes", 0.05, detected_at, 1.0,
                    "queued", "blind", detected_at,
                ),
            )
        mem.commit()

    built: list[str] = []
    monkeypatch.setattr(
        loop,
        "build_evidence",
        lambda _cfg, incident_id: built.append(incident_id)
        or Path(_cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db",
    )
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda _cfg, incident_id: incident_id in built)

    first = loop._capture_hard_evidence(cfg, scan_all=True)
    assert first["built"] == ["historical-000"]
    # The queue is deliberately bounded: one slice must not materialize all
    # historical retry work before the newest hard incident is captured.
    assert len(first["deferred"]) == cfg["loop"].get("evidence_queue_batch_size", 32) - 1
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT COUNT(*) FROM controller_debt WHERE kind='evidence_snapshot' AND status='retry_pending'"
        ).fetchone()[0] == cfg["loop"].get("evidence_queue_batch_size", 32) - 1
        assert mem.execute(
            "SELECT COUNT(*) FROM controller_debt WHERE kind='evidence_snapshot' AND status='retry_pending' AND retry_identity != ''"
        ).fetchone()[0] == cfg["loop"].get("evidence_queue_batch_size", 32) - 1

    monkeypatch.setattr(loop, "now", lambda: datetime.now(UTC) + timedelta(seconds=1))
    second = loop._capture_hard_evidence(cfg, scan_all=True)
    assert second["built"] == ["historical-001"]
    assert built == ["historical-000", "historical-001"]


def test_capacity_failure_consumes_attempt_and_fingerprint_defers_only_same_incident(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(evidence_builds_per_cycle=1, evidence_build_budget_ms=5000, evidence_max_bytes=1)
    _position(cfg, position_id="large-a")
    _position(cfg, position_id="large-b")
    with loop.memory(cfg) as mem:
        for index, position_id in enumerate(("large-a", "large-b")):
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"large-{index}", "hard", position_id, f"large-evidence-{index}", "below_floor",
                    "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 2 - index,
                    "queued", "blind", "2026-08-22T12:00:00+00:00",
                ),
            )
        mem.commit()

    calls: list[str] = []
    successful: set[str] = set()

    def oversized_builder(_cfg: dict, incident_id: str) -> Path:
        calls.append(incident_id)
        if incident_id == "large-0":
            raise loop.EvidenceCapacityExceeded("evidence_snapshot_oversized:bytes=61865984")
        successful.add(incident_id)
        return Path(_cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db"

    monkeypatch.setattr(loop, "build_evidence", oversized_builder)
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda _cfg, incident_id: incident_id in successful)
    trigger_batches = [["large-0"], []]
    monkeypatch.setattr(loop, "_detect_trigger", lambda _cfg, *_args: trigger_batches.pop(0))
    monkeypatch.setattr(loop, "_detect_maintenance", lambda _cfg, *_args: [])

    assert loop.detect(cfg) == ["large-0"]
    assert loop._LAST_EVIDENCE_CYCLE["attempted"] == 1
    assert calls == ["large-0"]
    with loop.memory(cfg) as mem:
        debt = mem.execute(
            "SELECT reason,fingerprint,config_fingerprint,capacity_fingerprint,data_fingerprint,attempts "
            "FROM controller_debt WHERE debt_id='evidence_snapshot:large-0'"
        ).fetchone()
    assert str(debt[0]).startswith("evidence_snapshot_capacity_failure:")
    assert all(str(value) for value in debt[1:5])
    assert debt[5] == 1

    monkeypatch.setattr(loop, "now", lambda: datetime.now(UTC) + timedelta(seconds=1))
    assert loop.detect(cfg) == []
    assert loop._LAST_EVIDENCE_CYCLE["built"] == []
    assert calls == ["large-0", "large-0"]
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT attempts FROM controller_debt WHERE debt_id='evidence_snapshot:large-0'"
        ).fetchone()[0] == 2


def test_bounded_builder_installs_shared_progress_budget_before_canonical_queries(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(evidence_build_budget_ms=5000, evidence_max_bytes=8 * 1024 * 1024)
    _position(cfg)
    _quote(cfg, "budget-query", "2026-08-22T09:00:02+00:00", 0.01)
    applied: list[object] = []
    original = loop._apply_evidence_sql_budget

    def traced(conn: sqlite3.Connection, budget=None) -> None:
        applied.append(budget)
        original(conn, budget)

    monkeypatch.setattr(loop, "_apply_evidence_sql_budget", traced)
    incident_id = loop.detect(cfg)[0]
    assert incident_id
    assert len(applied) >= 3
    assert sum(item is not None for item in applied) >= 2


def test_new_position_event_changes_data_fingerprint_and_retries_capacity_debt(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(evidence_builds_per_cycle=1, evidence_build_budget_ms=5000, evidence_max_bytes=1)
    _position(cfg, position_id="fingerprint-position")
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "fingerprint-incident", "hard", "fingerprint-position", "fingerprint-evidence", "below_floor",
                "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0,
                "queued", "blind", "2026-08-22T12:00:00+00:00",
            ),
        )
        mem.commit()
    calls: list[str] = []
    failed = {"fingerprint-incident"}
    successful: set[str] = set()

    def fail_once(_cfg: dict, incident_id: str) -> Path:
        calls.append(incident_id)
        if incident_id in failed:
            failed.remove(incident_id)
            raise loop.EvidenceCapacityExceeded("evidence_snapshot_oversized:bytes=61865984")
        successful.add(incident_id)
        return Path(_cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db"

    monkeypatch.setattr(loop, "build_evidence", fail_once)
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda _cfg, incident_id: incident_id in successful)
    loop._capture_hard_evidence(cfg, ["fingerprint-incident"])
    assert calls == ["fingerprint-incident"]
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "new-fingerprint-event", "fingerprint-position", 1, "MONITOR_REFRESHED",
                "2026-08-22T12:01:00+00:00", None, json.dumps({"new": "evidence"}), "active", "active",
            ),
        )
    with loop.memory(cfg) as mem:
        mem.execute(
            "UPDATE incidents SET evidence_revision=evidence_revision+1 "
            "WHERE incident_id='fingerprint-incident'"
        )
        mem.commit()
    result = loop._capture_hard_evidence(cfg, ["fingerprint-incident"])
    assert result["built"] == ["fingerprint-incident"]
    assert calls == ["fingerprint-incident", "fingerprint-incident"]


def test_real_large_snapshot_hits_tiny_capacity_once_then_next_incident_advances(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(evidence_builds_per_cycle=1, evidence_build_budget_ms=15000, evidence_max_bytes=1024 * 1024)
    _position(cfg, position_id="real-large-a")
    _position(cfg, position_id="real-large-b")
    _quote(cfg, "real-evidence-0", "2026-08-22T09:00:01+00:00", 0.01, latest=False)
    _quote(cfg, "real-evidence-1", "2026-08-22T09:00:02+00:00", 0.01, latest=False)
    _event(
        cfg,
        "large-monitor",
        "real-large-a",
        1,
        "MONITOR_REFRESHED",
        "2026-08-22T09:00:03+00:00",
        phase_before="active",
        phase_after="active",
        payload={"blob": "x" * (59 * 1024 * 1024)},
    )
    with loop.memory(cfg) as mem:
        for index, position_id in enumerate(("real-large-a", "real-large-b")):
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"real-large-{index}", "hard", position_id, f"real-evidence-{index}", "below_floor",
                    "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 2 - index,
                    "queued", "blind", "2026-08-22T12:00:00+00:00",
                ),
            )
        mem.commit()

    calls: list[str] = []
    original_build = loop.build_evidence

    def tracked_build(local_cfg: dict, incident_id: str) -> Path:
        calls.append(incident_id)
        return original_build(local_cfg, incident_id)

    monkeypatch.setattr(loop, "build_evidence", tracked_build)
    trigger_batches = [["real-large-0"], []]
    monkeypatch.setattr(loop, "_detect_trigger", lambda _cfg, *_args: trigger_batches.pop(0))
    monkeypatch.setattr(loop, "_detect_maintenance", lambda _cfg, *_args: [])

    assert loop.detect(cfg) == ["real-large-0"]
    assert calls == ["real-large-0"]
    with loop.memory(cfg) as mem:
        debt = mem.execute(
            "SELECT reason,attempts FROM controller_debt WHERE debt_id='evidence_snapshot:real-large-0'"
        ).fetchone()
    assert str(debt[0]).startswith("evidence_snapshot_capacity_failure:evidence_snapshot_oversized:")
    assert debt[1] == 1
    assert not (Path(cfg["paths"]["runtime"]) / "incidents" / "real-large-0" / "CURRENT").exists()

    monkeypatch.setattr(loop, "now", lambda: datetime.now(UTC) + timedelta(seconds=1))
    assert loop.detect(cfg) == []
    assert calls == ["real-large-0", "real-large-0"]


def test_daemon_publishes_fresh_status_before_slow_evidence_maintenance(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    observed: list[dict] = []

    def fake_bootstrap(_cfg: dict) -> dict[str, str]:
        runtime.mkdir(parents=True, exist_ok=True)
        return {"runtime": str(runtime)}

    def slow_detect(_cfg: dict) -> list[str]:
        payload = json.loads((runtime / "status.json").read_text())
        observed.append(payload)
        assert payload["alive"] is True
        assert payload["evidence_maintenance"] == "starting"
        (runtime / "HALT").touch()
        return []

    monkeypatch.setattr(loop, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(loop, "detect", slow_detect)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", lambda *_args: [])
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    assert loop.daemon(cfg) == 0
    assert observed and observed[0]["pid"] == os.getpid()


def test_controller_status_health_rejects_stale_dead_and_wrong_command(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    monkeypatch.setattr(loop, "now", lambda: fixed)
    fresh = {"alive": True, "pid": 123, "at": fixed.isoformat()}
    monkeypatch.setattr(loop, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(loop, "_pid_command", lambda _pid: "/repo/total_loss_loop.py daemon")
    assert loop.controller_status_health(cfg, fresh, observed_at=fixed)["healthy"] is True

    stale = dict(fresh, at=(fixed - timedelta(seconds=6)).isoformat())
    assert loop.controller_status_health(cfg, stale, observed_at=fixed)["reason"] == "controller_status_stale"

    monkeypatch.setattr(loop, "_pid_alive", lambda _pid: False)
    assert loop.controller_status_health(cfg, fresh, observed_at=fixed)["reason"] == "controller_pid_dead"

    monkeypatch.setattr(loop, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(loop, "_pid_command", lambda _pid: "/usr/bin/python unrelated.py daemon")
    assert loop.controller_status_health(cfg, fresh, observed_at=fixed)["reason"] == "controller_command_mismatch"

    monkeypatch.setattr(loop, "_pid_command", lambda _pid: "/repo/total_loss_loop.py daemon")
    loop.atomic_json(Path(cfg["paths"]["runtime"]) / "status.json", fresh)
    assert loop.status(cfg)["controller"]["healthy"] is True


def test_daemon_startup_status_precedes_bounded_large_run_reconcile(
    cfg: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runs = runtime / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    cfg["loop"].update(startup_run_batch_size=64, startup_maintenance_budget_ms=1000)
    with loop.memory(cfg):
        pass
    for index in range(3000):
        loop.atomic_json(
            runs / f"startup-run-{index:04d}.json",
            {"run_id": f"run-{index}", "incident_id": f"incident-{index}", "pid": 999999, "status": "completed"},
        )
    read_count = {"value": 0}
    observed: list[dict] = []
    spawned: list[object] = []
    original_metadata = loop._startup_run_metadata

    def traced_metadata(path: Path) -> dict:
        read_count["value"] += 1
        return original_metadata(path)

    def fake_detect(_cfg: dict) -> list[str]:
        payload = json.loads((runtime / "status.json").read_text())
        observed.append(payload)
        assert payload["alive"] is True
        assert payload["pid"] == os.getpid()
        if len(observed) == 2:
            (runtime / "HALT").touch()
        return []

    codex_home = tmp_path / "startup-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(loop, "_startup_run_metadata", traced_metadata)
    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()))
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    assert loop.daemon(cfg) == 0
    assert len(observed) == 2
    assert observed[0]["phase"] in {"startup_maintenance", "cycle"}
    assert read_count["value"] <= 3 * cfg["loop"]["startup_run_batch_size"]
    assert spawned == []
    assert loop._STARTUP_RUN_CURSOR[str(runtime.resolve())] >= 2 * cfg["loop"]["startup_run_batch_size"]
    checkpoint = json.loads((runtime / "startup-cursor.json").read_text())
    assert checkpoint["cursor"] >= 2 * cfg["loop"]["startup_run_batch_size"]


def test_startup_budget_bounds_real_locked_memory_subprocess_and_settings_io(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    with loop.memory(cfg):
        pass
    lock = sqlite3.connect(cfg["paths"]["runtime"] + "/memory.db", timeout=0.01)
    lock.execute("BEGIN EXCLUSIVE")
    loop._STARTUP_BUDGET = {"deadline": loop.time.monotonic() + 0.05, "max_run_json_bytes": 256 * 1024, "run_batch_size": 64}
    try:
        with pytest.raises(loop.StartupMaintenanceDeferred, match="memory_sqlite"):
            loop.bootstrap(cfg)
    finally:
        loop._STARTUP_BUDGET = None
        lock.rollback()
        lock.close()

    loop._STARTUP_BUDGET = {"deadline": loop.time.monotonic() + 0.03, "max_run_json_bytes": 256 * 1024, "run_batch_size": 64}
    try:
        with pytest.raises(loop.StartupMaintenanceDeferred, match="subprocess_timeout"):
            loop._run_capture(["/bin/sh", "-c", "sleep 1"], cwd=ROOT, timeout=2)
    finally:
        loop._STARTUP_BUDGET = None

    Path(cfg["paths"]["settings"]).write_text(json.dumps({"execution": {"absolute_live_unit_price_min": 0.05}, "padding": "x" * (300 * 1024)}))
    loop._STARTUP_BUDGET = {"deadline": loop.time.monotonic() + 1, "max_run_json_bytes": 256 * 1024, "run_batch_size": 64}
    try:
        with pytest.raises(loop.StartupMaintenanceDeferred, match="file_size"):
            loop.floor_price(cfg)
    finally:
        loop._STARTUP_BUDGET = None


def test_startup_schema_fast_path_handles_live_scale_without_repeating_ddl(
    cfg: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runs = runtime / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    with loop.memory(cfg) as mem:
        for index in range(3377):
            mem.execute(
                "INSERT INTO model_runs(run_id,incident_id,stage,session_id,model,reasoning_effort,"
                "started_at,status,usage_json,events_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"live-run-{index}", f"live-incident-{index}", "blind", None,
                    "test-model", "high", "2026-08-22T09:00:00+00:00", "completed",
                    "x" * 256, str(runtime / "events.jsonl"),
                ),
            )
        mem.commit()
    for index in range(3377):
        (runs / f"live-scale-run-{index:04d}.json").write_text(
            json.dumps(
                {
                    "run_id": f"live-scale-run-{index}",
                    "incident_id": "none",
                    "pid": 999999,
                    "status": "completed",
                    "metadata_padding": "x" * 1024,
                }
            )
            + "\n"
        )
    codex_home = tmp_path / "fast-path-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(loop, "MEMORY_SCHEMA", "THIS MUST NOT EXECUTE")
    cfg["loop"].update(startup_run_batch_size=128, startup_maintenance_budget_ms=250)
    observed_cursors: list[int] = []
    spawned: list[object] = []
    bootstrap_calls = {"value": 0}
    real_sleep = loop.time.sleep
    original_bootstrap_memory = loop._bootstrap_memory_version

    def intentionally_slow_first_bootstrap(local_cfg: dict) -> None:
        bootstrap_calls["value"] += 1
        if bootstrap_calls["value"] == 1:
            real_sleep(0.3)
        original_bootstrap_memory(local_cfg)

    def fake_detect(_cfg: dict) -> list[str]:
        status = json.loads((runtime / "status.json").read_text())
        cursor_path = runtime / "startup-cursor.json"
        if cursor_path.is_file():
            observed_cursors.append(int(json.loads(cursor_path.read_text())["cursor"]))
        assert status["alive"] is True
        assert status["provider_backoff"] is None
        if status.get("startup_maintenance") == "complete":
            debt = json.loads((runtime / "startup-debt.json").read_text())
            checkpoint = json.loads(cursor_path.read_text())
            assert debt["status"] == "resolved"
            assert checkpoint["cursor"] == 3377
            (runtime / "HALT").touch()
        return []

    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "_bootstrap_memory_version", intentionally_slow_first_bootstrap)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", lambda *_args: [])
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_dispatch_has_eligible_debt", lambda *_args: False)
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()))
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    assert loop.daemon(cfg) == 0
    assert observed_cursors
    assert 0 < observed_cursors[0] <= 2 * cfg["loop"]["startup_run_batch_size"]
    assert observed_cursors == sorted(set(observed_cursors))
    assert all(
        later - earlier <= cfg["loop"]["startup_run_batch_size"]
        for earlier, later in zip(observed_cursors, observed_cursors[1:])
    )
    assert observed_cursors[-1] == 3377
    assert spawned == []
    assert bootstrap_calls["value"] == 2


def test_ordinary_complete_memory_open_has_no_schema_ddl(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    with loop.memory(cfg):
        pass
    traces: list[str] = []
    original_connect = loop.sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        if str(args[0]).endswith("memory.db"):
            conn.set_trace_callback(traces.append)
        return conn

    monkeypatch.setattr(loop.sqlite3, "connect", traced_connect)
    with loop.memory(cfg):
        pass
    assert not any(
        any(token in sql.upper() for token in ("DROP INDEX", "CREATE INDEX", "CREATE TABLE", "ALTER TABLE"))
        for sql in traces
    )


def test_wrong_schema_normal_open_records_typed_debt_without_ddl(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    with loop.memory(cfg):
        pass
    with sqlite3.connect(cfg["paths"]["runtime"] + "/memory.db") as conn:
        conn.execute("DROP INDEX idx_incident_queue")
    traces: list[str] = []
    original_connect = loop.sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        if str(args[0]).endswith("memory.db"):
            conn.set_trace_callback(traces.append)
        return conn

    monkeypatch.setattr(loop.sqlite3, "connect", traced_connect)
    with pytest.raises(loop.SchemaMaintenanceDeferred):
        with loop.memory(cfg):
            pass
    assert not any("CREATE INDEX" in sql.upper() or "DROP INDEX" in sql.upper() for sql in traces)
    debt = json.loads((Path(cfg["paths"]["runtime"]) / "schema-debt.json").read_text())
    assert debt["status"] == "retry_pending"


def test_trigger_receipt_failure_is_durable_and_recovers(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _quote(cfg, "receipt-failure-q", "2026-08-22T09:00:02+00:00", 0.01)
    original_atomic = loop.atomic_json

    def fail_receipt(path: Path, payload: object) -> None:
        if path.name == "trigger-committed.json":
            raise OSError("receipt disk")
        original_atomic(path, payload)

    monkeypatch.setattr(loop, "atomic_json", fail_receipt)
    assert loop.detect(cfg)
    with loop.memory(cfg) as mem:
        row = mem.execute("SELECT status,reason FROM controller_debt WHERE kind='trigger_receipt'").fetchone()
    assert row is not None and row[0] == "retry_pending" and "committed_receipt_pending" in row[1]
    (Path(cfg["paths"]["runtime"]) / "trigger-receipt-debt.json").unlink()
    monkeypatch.setattr(loop, "atomic_json", original_atomic)
    loop.detect(cfg)
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM controller_debt WHERE kind='trigger_receipt'").fetchone()[0] == "resolved"


def test_multiple_receipt_debts_recover_by_exact_debt_id(cfg: dict) -> None:
    loop._record_committed_receipt_debt(cfg, ["incident-a"], "committed_receipt_pending:a")
    loop._record_committed_receipt_debt(cfg, ["incident-b"], "committed_receipt_pending:b")
    loop._retry_committed_receipt(cfg, loop.time.monotonic() + 1.0)
    with loop.memory(cfg) as mem:
        rows = mem.execute(
            "SELECT debt_id,status FROM controller_debt WHERE kind='trigger_receipt' ORDER BY debt_id"
        ).fetchall()
    assert len(rows) == 2 and all(row[1] == "resolved" for row in rows)


def test_receipt_projection_failure_keeps_db_truth_repairable(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop._record_committed_receipt_debt(cfg, ["projection-incident"], "committed_receipt_pending:projection")
    original_atomic = loop.atomic_json

    def fail_resolved(path: Path, payload: object) -> None:
        if isinstance(payload, dict) and payload.get("status") == "resolved":
            raise OSError("projection disk")
        original_atomic(path, payload)

    monkeypatch.setattr(loop, "atomic_json", fail_resolved)
    loop._retry_committed_receipt(cfg, loop.time.monotonic() + 1.0)
    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT status,reason FROM controller_debt WHERE debt_id LIKE 'trigger_receipt:%'"
        ).fetchone()
    assert row is not None and row[0] == "resolved" and "projection_pending" in row[1]
    monkeypatch.setattr(loop, "atomic_json", original_atomic)
    loop._retry_committed_receipt(cfg, loop.time.monotonic() + 1.0)
    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT status,reason FROM controller_debt WHERE debt_id LIKE 'trigger_receipt:%'"
        ).fetchone()
    assert row is not None and row[0] == "resolved" and row[1] == "committed_receipt_complete"


def test_identity_fingerprint_expired_budget_defer_is_typed(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "fingerprint-budget-q", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    budget = {"deadline": loop.time.monotonic() - 0.001}
    with pytest.raises(loop.EvidenceCapacityExceeded, match="time_budget"):
        loop._evidence_identity_fingerprints(cfg, incident_id, budget)


def test_postcommit_receipt_deadline_records_pending_debt(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 10.0}
    original_atomic = loop.atomic_json

    def advance_after_receipt(path: Path, payload: object) -> None:
        if path.name == "trigger-committed.json":
            clock["now"] = 11.0
        original_atomic(path, payload)

    monkeypatch.setattr(loop.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(loop, "atomic_json", advance_after_receipt)
    loop._publish_trigger_receipt(cfg, ["postcommit-incident"], 10.5)
    with loop.memory(cfg) as mem:
        row = mem.execute("SELECT status,reason FROM controller_debt WHERE kind='trigger_receipt'").fetchone()
    assert row is not None and row[0] == "retry_pending" and "committed_receipt_pending" in row[1]


def test_trigger_rollback_returns_no_created_truth_or_receipt(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "trigger-lock-q", "2026-08-22T09:00:02+00:00", 0.01)
    with loop.memory(cfg):
        pass
    lock = sqlite3.connect(cfg["paths"]["runtime"] + "/memory.db", timeout=0.1)
    lock.execute("BEGIN EXCLUSIVE")
    cfg["loop"]["trigger_budget_ms"] = 40
    try:
        assert loop._detect_trigger(cfg) == []
    finally:
        lock.rollback()
        lock.close()
    assert not (Path(cfg["paths"]["runtime"]) / "trigger-committed.json").exists()


def test_maintenance_locked_sqlite_is_bounded_to_absolute_slice(cfg: dict) -> None:
    _position(cfg)
    with loop.memory(cfg):
        pass
    lock = sqlite3.connect(cfg["paths"]["runtime"] + "/memory.db", timeout=0.1)
    lock.execute("BEGIN EXCLUSIVE")
    started = loop.time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            loop._detect_maintenance(cfg, loop.time.monotonic() + 0.04)
    finally:
        lock.rollback()
        lock.close()
    assert loop.time.monotonic() - started < 0.75


def test_committed_maintenance_ids_survive_postcommit_deadline(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    receipts: list[list[str]] = []
    captured: list[list[str]] = []
    monkeypatch.setattr(loop, "_detect_trigger", lambda _cfg, *_args: [])
    monkeypatch.setattr(
        loop,
        "_detect_maintenance",
        lambda _cfg, _deadline: loop._MaintenanceOutcome(
            ["maintenance-committed"], postcommit_deferred=True
        ),
    )
    monkeypatch.setattr(
        loop,
        "_publish_trigger_receipt",
        lambda _cfg, ids, _deadline: receipts.append(list(ids)),
    )
    monkeypatch.setattr(
        loop,
        "_capture_hard_evidence",
        lambda _cfg, ids, **_kwargs: captured.append(list(ids))
        or {"built": [], "deferred": []},
    )
    assert loop.detect(cfg) == ["maintenance-committed"]
    assert receipts == [["maintenance-committed"]]
    assert captured[-1] == ["maintenance-committed"]
    status = json.loads((Path(cfg["paths"]["runtime"]) / "status.json").read_text())
    assert status["phase"] == "maintenance_committed"
    assert status["postcommit_deferred"] is True


def test_evidence_queue_cursor_bounds_provider_backoff_capture(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"]["evidence_queue_batch_size"] = 4
    with loop.memory(cfg) as mem:
        for index in range(100):
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"queue-{index:03d}", "hard", "p1", f"queue-q-{index:03d}", "below_floor", "yes-token", "sell_yes", .05,
                 "2026-08-22T09:00:00+00:00", 1, "queued", "blind", "2026-08-22T09:00:00+00:00"),
            )
        mem.commit()
    seen: list[str] = []
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda _cfg, incident_id, **_kwargs: seen.append(incident_id) or True)
    loop._capture_hard_evidence(cfg, scan_all=True)
    assert len(seen) == 4
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT value FROM meta WHERE key='evidence_queue_cursor'").fetchone()[0] == "4"


def test_startup_missing_schema_uses_bounded_migration_not_fast_path(
    cfg: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with loop.memory(cfg) as mem:
        mem.execute("DROP TABLE loop_versions")
        mem.commit()
    codex_home = tmp_path / "migration-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    cfg["loop"]["startup_maintenance_budget_ms"] = 500
    loop._STARTUP_BUDGET = loop._new_startup_budget(cfg)
    try:
        loop.bootstrap(cfg)
    finally:
        loop._STARTUP_BUDGET = None
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='loop_versions'"
        ).fetchone() is not None


def test_startup_wrong_index_contract_is_migrated_before_fast_path(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    with loop.memory(cfg) as mem:
        mem.execute("DROP INDEX idx_incident_crossing")
        mem.execute("CREATE INDEX idx_incident_crossing ON incidents(status)")
        mem.commit()
    cfg["loop"]["startup_maintenance_budget_ms"] = 500
    loop._STARTUP_BUDGET = loop._new_startup_budget(cfg)
    try:
        with loop.memory(cfg) as mem:
            assert loop._startup_schema_complete(mem) is True
            detail = [
                row
                for row in mem.execute("PRAGMA index_xinfo(idx_incident_crossing)").fetchall()
                if int(row[5]) == 1
            ]
            assert [str(row[2]) for row in detail] == [
                "position_id", "crossing_evidence_id", "kind"
            ]
    finally:
        loop._STARTUP_BUDGET = None


def test_startup_spawn_intents_are_batch_scoped_and_eventually_drained(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runs = runtime / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    cfg["loop"].update(startup_run_batch_size=1, startup_maintenance_budget_ms=500)
    with loop.memory(cfg) as mem:
        for index in range(3153):
            mem.execute(
                "INSERT INTO spawn_intents(run_id,incident_id,stage,owner_pid,child_pid,witness_path,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"historical-{index}", f"historical-incident-{index}", "blind", 1, None,
                    str(runtime / "witness" / f"historical-{index}"), "failed",
                    "2026-08-22T09:00:00+00:00", "2026-08-22T09:00:00+00:00",
                ),
            )
        for incident_id in ("batch-incident", "later-incident"):
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?, 'hard', 'p1', ?, 'below_floor', 'yes-token', 'sell_yes', .05, ?, 1, 'running', 'blind', ?)",
                (incident_id, f"q-{incident_id}", "2026-08-22T09:00:00+00:00", "2026-08-22T09:00:00+00:00"),
            )
            mem.execute(
                "INSERT INTO spawn_intents(run_id,incident_id,stage,owner_pid,child_pid,witness_path,state,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"active-{incident_id}", incident_id, "blind", os.getpid(), None,
                    str(runtime / "witness" / incident_id), "pre_spawn",
                    "2026-08-22T09:00:00+00:00", "2026-08-22T09:00:00+00:00",
                ),
            )
        mem.commit()
    (runs / "aaa-batch.json").write_text(
        json.dumps({"run_id": "batch-run", "incident_id": "batch-incident", "pid": 999999, "status": "running"})
    )
    (runs / "zzz-later.json").write_text(
        json.dumps({"run_id": "later-run", "incident_id": "later-incident", "pid": 999999, "status": "running"})
    )
    key = str(runtime.resolve())
    loop._STARTUP_RUN_QUEUE.pop(key, None)
    loop._STARTUP_RUN_CURSOR.pop(key, None)
    loop._STARTUP_RUN_REMAINING.pop(key, None)
    traces: list[str] = []
    original_memory = loop.memory

    def traced_memory(local_cfg: dict):
        conn = original_memory(local_cfg)
        if loop._STARTUP_BUDGET is not None:
            conn.set_trace_callback(traces.append)
        return conn

    monkeypatch.setattr(loop, "memory", traced_memory)
    for expected_cursor in (1, 2):
        loop._STARTUP_BUDGET = loop._new_startup_budget(cfg)
        try:
            assert loop.reconcile_orphan_incidents(cfg) == []
        finally:
            loop._STARTUP_BUDGET = None
        assert loop._STARTUP_RUN_CURSOR[key] == expected_cursor
    spawn_queries = [sql for sql in traces if "FROM spawn_intents" in sql]
    assert spawn_queries
    assert all("incident_id IN" in sql for sql in spawn_queries)
    with loop.memory(cfg) as mem:
        states = mem.execute(
            "SELECT incident_id,state FROM spawn_intents WHERE incident_id IN ('batch-incident','later-incident')"
        ).fetchall()
    assert {tuple(row) for row in states} == {
        ("batch-incident", "pre_spawn"), ("later-incident", "pre_spawn")
    }


def test_startup_debt_fail_closed_blocks_external_dispatch_paths(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop.atomic_json(
        Path(cfg["paths"]["runtime"]) / "startup-debt.json",
        {"kind": "startup_maintenance", "status": "retry_pending", "reason": "locked", "updated_at": loop.iso()},
    )
    monkeypatch.setattr(loop, "reconcile_orphan_incidents", lambda _cfg: pytest.fail("startup debt must gate before reconcile"))
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: pytest.fail("startup debt must prevent spawn"))
    assert loop.dispatch(cfg) == []
    assert loop.dispatch_once(cfg) == []
    assert loop._dispatch_has_eligible_debt(cfg, []) is False


def test_startup_locked_reconcile_keeps_same_batch_for_next_cycle(
    cfg: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runs = runtime / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    cfg["loop"].update(startup_run_batch_size=1, startup_maintenance_budget_ms=100)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES ('startup-orphan', 'hard', 'p1', 'q1', 'below_floor', 'yes-token', 'sell_yes', .05, ?, 1, 'running', 'blind', ?)",
            ("2026-08-22T09:00:00+00:00", "2026-08-22T09:00:00+00:00"),
        )
        mem.commit()
    loop.atomic_json(
        runs / "startup-orphan-run.json",
        {"run_id": "startup-orphan-run", "incident_id": "startup-orphan", "pid": 999999, "status": "running"},
    )
    lock = sqlite3.connect(runtime / "memory.db", timeout=0.01)
    original_bootstrap_memory = loop._bootstrap_memory_version
    armed = {"value": False}

    def arm_reconcile_lock(local_cfg: dict) -> None:
        original_bootstrap_memory(local_cfg)
        if not armed["value"]:
            lock.execute("BEGIN EXCLUSIVE")
            armed["value"] = True

    monkeypatch.setattr(loop, "_bootstrap_memory_version", arm_reconcile_lock)
    observed: list[dict] = []
    spawned: list[object] = []
    codex_home = tmp_path / "startup-lock-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def fake_detect(_cfg: dict) -> list[str]:
        payload = json.loads((runtime / "status.json").read_text())
        observed.append(payload)
        if len(observed) == 1:
            debt = json.loads((runtime / "startup-debt.json").read_text())
            assert payload["alive"] is True
            assert debt["status"] == "retry_pending"
            lock.rollback()
            lock.close()
        else:
            with loop.memory(cfg) as mem:
                status = mem.execute(
                    "SELECT status FROM incidents WHERE incident_id='startup-orphan'"
                ).fetchone()[0]
            assert status == "retry_pending"
            checkpoint = json.loads((runtime / "startup-cursor.json").read_text())
            assert checkpoint["cursor"] == 1
            (runtime / "HALT").touch()
        return []

    monkeypatch.setattr(loop, "detect", fake_detect)
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "poll_runs", lambda *_args: [])
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_dispatch_has_eligible_debt", lambda *_args: False)
    monkeypatch.setattr(loop, "_spawn_dispatch_worker", lambda _cfg: spawned.append(object()))
    monkeypatch.setattr(loop, "_record_cycle_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)

    try:
        assert loop.daemon(cfg) == 0
    finally:
        if lock:
            try:
                lock.rollback()
            except sqlite3.Error:
                pass
            lock.close()
    assert len(observed) == 2
    assert spawned == []


def test_startup_checkpoint_commit_boundary_survives_deadline_race(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runs = runtime / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    loop.atomic_json(
        runs / "checkpoint-race.json",
        {"run_id": "checkpoint-race", "incident_id": "not-running", "pid": 999999, "status": "completed"},
    )
    with loop.memory(cfg):
        pass
    key = str(runtime.resolve())
    loop._STARTUP_RUN_QUEUE.pop(key, None)
    loop._STARTUP_RUN_CURSOR.pop(key, None)
    loop._STARTUP_RUN_REMAINING.pop(key, None)
    loop._STARTUP_BUDGET = {
        "deadline": loop.time.monotonic() + 1.0,
        "max_run_json_bytes": 256 * 1024,
        "run_batch_size": 1,
    }
    original_atomic = loop.atomic_json

    def advance_after_pointer_replace(path: Path, payload: dict) -> None:
        original_atomic(path, payload)
        if path.name == "startup-cursor.json":
            loop._STARTUP_BUDGET["deadline"] = loop.time.monotonic() - 1.0

    monkeypatch.setattr(loop, "atomic_json", advance_after_pointer_replace)
    try:
        assert loop.reconcile_orphan_incidents(cfg) == []
    finally:
        loop._STARTUP_BUDGET = None
    checkpoint = json.loads((runtime / "startup-cursor.json").read_text())
    assert checkpoint["cursor"] == 1
    assert loop._STARTUP_RUN_CURSOR[key] == checkpoint["cursor"]


def test_evidence_failure_debt_recovers_same_incident_without_spawn(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "capture-failure-q", "2026-08-22T09:00:02+00:00", 0.01)
    original = loop.build_evidence
    monkeypatch.setattr(loop, "build_evidence", lambda *_args: (_ for _ in ()).throw(OSError("snapshot disk")))
    incident_id = loop.detect(cfg)[0]
    with loop.memory(cfg) as mem:
        debt = mem.execute(
            "SELECT kind,status FROM controller_debt WHERE debt_id=?",
            (f"evidence_snapshot:{incident_id}",),
        ).fetchone()
    assert tuple(debt) == ("evidence_snapshot", "retry_pending")
    monkeypatch.setattr(loop, "build_evidence", original)
    assert loop.detect(cfg) == []
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT COUNT(*) FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()[0] == 1
        assert mem.execute(
            "SELECT status FROM controller_debt WHERE debt_id=?",
            (f"evidence_snapshot:{incident_id}",),
        ).fetchone()[0] == "resolved"
    assert loop._evidence_pair_valid(cfg, incident_id)


def test_interrupted_fingerprint_uses_emergency_debt_without_killing_scan_or_spawning(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exhausted evidence deadline cannot turn committed incident debt into controller exit."""
    _position(cfg, position_id="emergency-debt")
    _quote(cfg, "emergency-debt-q", "2026-08-22T09:00:02+00:00", 0.01)
    original_fingerprints = loop._evidence_fingerprints
    failed = {"value": False}
    spawned: list[object] = []

    def interrupt_after_incident(_cfg, incident_id, budget):
        if not failed["value"]:
            failed["value"] = True
            budget["deadline"] = loop.time.monotonic() - 1.0
            raise loop.EvidenceCapacityExceeded("evidence_snapshot_deferred:time_budget")
        return original_fingerprints(_cfg, incident_id, budget)

    monkeypatch.setattr(loop, "_evidence_fingerprints", interrupt_after_incident)
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: spawned.append(True))

    created = loop.detect(cfg)
    assert len(created) == 1
    incident_id = created[0]
    with loop.memory(cfg) as mem:
        debt = mem.execute(
            "SELECT kind,status,reason FROM controller_debt WHERE debt_id=?",
            (f"evidence_snapshot:{incident_id}",),
        ).fetchone()
    assert tuple(debt[:2]) == ("evidence_snapshot", "retry_pending")
    assert "evidence_snapshot" in str(debt[2])
    with loop.memory(cfg) as mem:
        incident_state = mem.execute(
            "SELECT stage,status FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
    assert tuple(incident_state) == ("evidence", "blocked")
    receipt = json.loads(
        (Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "emergency-evidence-debt.json").read_text()
    )
    assert receipt["incident_id"] == incident_id
    assert receipt["status"] == "retry_pending"
    assert spawned == []

    monkeypatch.setattr(loop, "now", lambda: datetime.now(UTC) + timedelta(seconds=1))
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda *_args: ("fp", "cfg", "cap", "data"))
    monkeypatch.setattr(
        loop,
        "build_evidence",
        lambda local_cfg, value: Path(local_cfg["paths"]["runtime"]) / "incidents" / value / "evidence.db",
    )
    next_cycle = loop._capture_hard_evidence(
        cfg,
        [incident_id],
        budget=loop._new_evidence_budget(cfg),
    )
    assert next_cycle["built"] == [incident_id]
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT status FROM controller_debt WHERE debt_id=?",
            (f"evidence_snapshot:{incident_id}",),
        ).fetchone()[0] == "resolved"

def test_emergency_evidence_debt_bypasses_expired_memory_guard(cfg: dict) -> None:
    """The independent debt writer never re-enters the exhausted evidence context."""
    incident_id = "expired-memory-debt"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "e1", "below_floor", "yes-token", "sell_yes", 0.05,
             "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    loop._EVIDENCE_BUILD_CONTEXT = {"deadline": loop.time.monotonic() - 1.0}
    try:
        loop._record_evidence_debt(
            cfg, incident_id, "evidence_snapshot_deferred:time_budget",
            fingerprints=("fp", "cfg", "cap", "data"),
        )
    finally:
        loop._EVIDENCE_BUILD_CONTEXT = None
    with loop.memory(cfg) as mem:
        assert mem.execute(
            "SELECT status FROM controller_debt WHERE debt_id=?",
            (f"evidence_snapshot:{incident_id}",),
        ).fetchone()[0] == "retry_pending"

def test_emergency_evidence_debt_reports_typed_degradation_on_short_sqlite_contention(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked emergency write is bounded and degrades the controller instead of exiting."""
    incident_id = "contended-emergency-debt"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "e1", "below_floor", "yes-token", "sell_yes", 0.05,
             "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    locker = sqlite3.connect(Path(cfg["paths"]["runtime"]) / "memory.db")
    locker.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(loop, "_EMERGENCY_EVIDENCE_DEBT_WRITE_SECONDS", 0.001)
    loop._EVIDENCE_BUILD_CONTEXT = {"deadline": loop.time.monotonic() - 1.0}
    try:
        loop._record_evidence_debt(
            cfg, incident_id, "evidence_snapshot_deferred:time_budget",
            fingerprints=("fp", "cfg", "cap", "data"),
        )
    finally:
        loop._EVIDENCE_BUILD_CONTEXT = None
        locker.rollback()
        locker.close()
    receipt = json.loads(
        (Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "emergency-evidence-debt.json").read_text()
    )
    assert receipt["status"] == "controller_degraded"
    assert receipt["reason_code"] == "EVIDENCE_EMERGENCY_DEBT_DB_UNWRITABLE"
    heartbeat = json.loads((Path(cfg["paths"]["runtime"]) / "status.json").read_text())
    assert heartbeat["phase"] == "evidence_controller_degraded"



def test_scan_all_empty_explicit_ids_does_not_create_debt(cfg: dict) -> None:
    result = loop._capture_hard_evidence(cfg, [], scan_all=True)
    assert result["built"] == []
    assert result["deferred"] == []


def test_evidence_capture_materializes_generator_ids_once(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _position(cfg, position_id="generator-position")
    incident_id = "generator-incident"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "generator-position", "generator-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(loop, "build_evidence", lambda local_cfg, value: Path(local_cfg["paths"]["runtime"]) / "incidents" / value / "evidence.db")
    result = loop._capture_hard_evidence(cfg, (value for value in [incident_id]))
    assert result["built"] == [incident_id]
    assert result["deferred"] == []


def test_final_guard_after_successful_pair_does_not_reopen_debt(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _position(cfg, position_id="final-guard-position")
    incident_id = "final-guard-incident"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "final-guard-position", "final-guard-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "blocked", "evidence", "2026-08-22T12:00:00+00:00"),
        )
        mem.execute("INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at) VALUES (?,?,?,?,?)", (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "old", "2026-08-22T12:00:00+00:00"))
        mem.commit()
    validated = {"value": False}
    def pair(_cfg, value, **_kwargs):
        if value == incident_id:
            validated["value"] = True
        return True
    def guard():
        if validated["value"]:
            raise loop.EvidenceCapacityExceeded("final bookkeeping guard")
    monkeypatch.setattr(loop, "_evidence_pair_valid", pair)
    monkeypatch.setattr(loop, "_evidence_guard", guard)
    first = loop._capture_hard_evidence(cfg, [incident_id])
    assert first["deferred"] == [incident_id]
    assert validated["value"] is False
    with loop.memory(cfg) as mem:
        retry_at = loop.parse_time(
            mem.execute(
                "SELECT next_retry_at FROM controller_debt WHERE debt_id=?",
                (f"evidence_snapshot:{incident_id}",),
            ).fetchone()[0]
        )
    assert retry_at is not None
    monkeypatch.setattr(loop, "now", lambda: retry_at + timedelta(seconds=1))
    result = loop._capture_hard_evidence(cfg, [incident_id])
    assert result["deferred"] == []
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM controller_debt WHERE debt_id=?", (f"evidence_snapshot:{incident_id}",)).fetchone()[0] == "resolved"


def test_triple_evidence_persistence_failure_is_visible(cfg: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    incident_id = "triple-persistence-incident"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "triple-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(loop, "atomic_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("persistence full")))
    loop._EVIDENCE_BUILD_CONTEXT = {"deadline": loop.time.monotonic() - 1.0}
    try:
        loop._record_evidence_debt(cfg, incident_id, "evidence_snapshot_deferred:time_budget", fingerprints=("fp", "cfg", "cap", "data"))
    finally:
        loop._EVIDENCE_BUILD_CONTEXT = None
    assert loop._LAST_EVIDENCE_CYCLE["controller_degraded"]["reason_code"] == "EVIDENCE_EMERGENCY_DEBT_PERSISTENCE_FAILED"
    assert "EVIDENCE_CONTROLLER_DEGRADED" in capsys.readouterr().err



def test_expired_scan_all_recovers_queued_debt(cfg: dict) -> None:
    incident_id = "expired-scan-all-incident"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "expired-scan-all-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    budget = {"deadline": loop.time.monotonic() - 1.0, "remaining": 1, "max_bytes": 1024}
    result = loop._capture_hard_evidence(cfg, [], scan_all=True, budget=budget)
    assert incident_id in result["deferred"]
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM controller_debt WHERE debt_id=?", (f"evidence_snapshot:{incident_id}",)).fetchone()[0] == "retry_pending"


def test_early_memory_guard_interrupt_recovers_candidates_without_raise(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    incident_id = "early-memory-guard-incident"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "early-memory-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    calls = {"count": 0}
    original = loop._evidence_guard
    def interrupt_once():
        calls["count"] += 1
        if calls["count"] == 1:
            raise loop.EvidenceCapacityExceeded("early memory guard")
        original()
    monkeypatch.setattr(loop, "_evidence_guard", interrupt_once)
    result = loop._capture_hard_evidence(cfg, [incident_id])
    assert incident_id in result["deferred"]


def test_capture_summary_exposes_triple_degradation(cfg: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    incident_id = "summary-triple-degradation"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "p1", "summary-triple-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    monkeypatch.setattr(loop, "atomic_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("persistence full")))
    result = loop._capture_hard_evidence(cfg, [], scan_all=True, budget={"deadline": loop.time.monotonic() - 1.0, "remaining": 1, "max_bytes": 1024})
    assert result["controller_degraded"]["status"] == "controller_degraded"
    assert "EVIDENCE_CONTROLLER_DEGRADED" in capsys.readouterr().err


def test_reaper_capacity_interrupt_defers_current_and_remaining_ids(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = ["reaper-current", "reaper-remaining"]
    with loop.memory(cfg) as mem:
        for incident_id in ids:
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (incident_id, "hard", "p1", f"{incident_id}-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
            )
        mem.commit()
    monkeypatch.setattr(loop, "_reap_incomplete_generations", lambda *_args: (_ for _ in ()).throw(loop.EvidenceCapacityExceeded("reaper budget")))
    result = loop._capture_hard_evidence(cfg, ids)
    assert set(result["deferred"]) == set(ids)
    with loop.memory(cfg) as mem:
        rows = mem.execute(
            "SELECT debt_id,status FROM controller_debt WHERE debt_id IN (?,?) ORDER BY debt_id",
            tuple(f"evidence_snapshot:{value}" for value in ids),
        ).fetchall()
    assert {str(row[0]) for row in rows} == {f"evidence_snapshot:{value}" for value in ids}
    assert all(str(row[1]) == "retry_pending" for row in rows)


def test_forecast_fingerprint_interrupted_query_is_capacity_debt(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _position(cfg, position_id="forecast-interrupted-position")
    incident_id = "forecast-interrupted-incident"
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", "forecast-interrupted-position", "forecast-interrupted-evidence", "below_floor", "yes-token", "sell_yes", 0.05, "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()
    real_open_ro = loop.open_ro
    forecasts_path = Path(cfg["paths"]["forecasts_db"]).resolve()
    class ForecastConnection:
        def __init__(self, connection):
            self.connection = connection
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, traceback):
            return self.connection.__exit__(exc_type, exc, traceback)
        def set_progress_handler(self, *args):
            return self.connection.set_progress_handler(*args)
        def execute(self, query, *args):
            if "forecast_posteriors" in query:
                raise sqlite3.OperationalError("InTeRrUpTeD")
            return self.connection.execute(query, *args)
    def fake_open_ro(path, **kwargs):
        connection = real_open_ro(path, **kwargs)
        return ForecastConnection(connection) if Path(path).resolve() == forecasts_path else connection
    monkeypatch.setattr(loop, "open_ro", fake_open_ro)
    result = loop._capture_hard_evidence(cfg, [incident_id])
    assert result["deferred"] == [incident_id]
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM controller_debt WHERE debt_id=?", (f"evidence_snapshot:{incident_id}",)).fetchone()[0] == "retry_pending"
def test_pointer_replace_failure_keeps_previous_pair_valid(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "pointer-failure-q", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    prior = (incident_dir / "CURRENT").read_text()
    original_replace = loop.os.replace
    def fail_pointer(src, dst):
        if Path(dst).name == "CURRENT":
            raise OSError("pointer replace")
        original_replace(src, dst)
    monkeypatch.setattr(loop.os, "replace", fail_pointer)
    with pytest.raises(OSError, match="pointer replace"):
        loop.build_evidence(cfg, incident_id)
    assert (incident_dir / "CURRENT").read_text() == prior
    assert loop._evidence_pair_valid(cfg, incident_id)


def test_provider_backoff_expiry_restores_dispatch_eligibility(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({"kind": "provider_quota_limit", "reason": "quota", "next_retry_at": "2000-01-01T00:00:00+00:00"}),
        )
        mem.commit()
    _position(cfg)
    _queue_blind_dispatch_debt(cfg, incident_id="cooldown-expired")
    monkeypatch.setattr(loop, "reconcile_orphan_incidents", lambda _cfg: [])
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_retry_pending", lambda *_args: [])
    monkeypatch.setattr(loop, "_recover_classification_debt", lambda *_args: None)
    monkeypatch.setattr(loop, "_dispatch_repair_waiting", lambda *_args: None)
    claim_count = {"hard": 0}
    def claim_once(_cfg, kind):
        if kind != "hard" or claim_count["hard"]:
            return None
        claim_count["hard"] = 1
        return {"incident_id": "cooldown-expired", "kind": "hard"}
    monkeypatch.setattr(loop, "_claim", claim_once)
    monkeypatch.setattr(
        loop,
        "build_evidence",
        lambda cfg, incident_id: Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db",
    )
    monkeypatch.setattr(
        loop,
        "_evidence_pair_paths",
        lambda cfg, incident_id: (
            Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db",
            Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "manifest.json",
        ),
    )
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda *_args: True)
    monkeypatch.setattr(loop, "read_json", lambda *_args: {"loaded_sha": "sha"})
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: {"run_id": "resumed"})
    assert loop.dispatch(cfg) == ["cooldown-expired"]


def test_dispatch_uses_real_current_generation_pair_and_loaded_sha(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "dispatch-generation-q", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    generation = (incident_dir / "CURRENT").read_text().strip()
    generation_evidence = incident_dir / "generations" / generation / "evidence.db"
    generation_manifest = incident_dir / "generations" / generation / "manifest.json"
    assert generation_evidence.is_file() and generation_manifest.is_file()
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_retry_pending", lambda *_args: [])
    monkeypatch.setattr(loop, "_recover_classification_debt", lambda *_args: None)
    monkeypatch.setattr(loop, "_dispatch_repair_waiting", lambda *_args: None)
    launched: list[str] = []
    monkeypatch.setattr(
        loop,
        "_spawn_run",
        lambda _cfg, **kwargs: launched.append(kwargs["incident_id"]) or {"run_id": "generation-run"},
    )
    original_read_json = loop.read_json
    def loaded_generation(path, default=None):
        if Path(path).name == "manifest.json" and "generations" in Path(path).parts:
            payload = original_read_json(path, default)
            payload["loaded_sha"] = "sha"
            return payload
        return original_read_json(path, default)
    monkeypatch.setattr(loop, "read_json", loaded_generation)
    assert loop.dispatch(cfg) == [incident_id]
    assert launched == [incident_id]


def test_normal_completed_jsonl_has_no_terminal_failure() -> None:
    # The parser must not turn an ordinary successful turn into provider debt.
    path = Path("/tmp") / f"total-loss-success-{os.getpid()}.jsonl"
    path.write_text(
        json.dumps({"type": "thread.started", "thread_id": "session"})
        + "\n"
        + json.dumps({"type": "turn.completed", "usage": {"total_tokens": 1}})
        + "\n"
    )
    try:
        assert loop._parse_terminal_failure(path) is None
    finally:
        path.unlink(missing_ok=True)


def test_dispatch_path_mismatch_records_debt_without_spawn(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="path-mismatch")
    monkeypatch.setattr(loop, "reconcile_orphan_incidents", lambda _cfg: [])
    monkeypatch.setattr(loop, "current_capabilities", lambda _cfg: {"ready": True})
    monkeypatch.setattr(loop, "_running", lambda _cfg: [])
    monkeypatch.setattr(loop, "_retry_pending", lambda *_args: [])
    monkeypatch.setattr(loop, "_recover_classification_debt", lambda *_args: None)
    monkeypatch.setattr(loop, "_dispatch_repair_waiting", lambda *_args: None)
    claim_count = {"hard": 0}
    def claim_once(_cfg, kind):
        if kind != "hard" or claim_count["hard"]:
            return None
        claim_count["hard"] = 1
        return {"incident_id": "path-mismatch", "kind": "hard"}
    monkeypatch.setattr(
        loop,
        "_claim",
        claim_once,
    )
    monkeypatch.setattr(loop, "build_evidence", lambda *_args: Path("/tmp/not-current-generation.db"))
    monkeypatch.setattr(loop, "_evidence_pair_paths", lambda *_args: (Path("/tmp/current-generation.db"), Path("/tmp/current-generation.json")))
    monkeypatch.setattr(loop, "_spawn_run", lambda *_args, **_kwargs: pytest.fail("path mismatch must not spawn"))
    assert loop.dispatch(cfg) == []
    with loop.memory(cfg) as mem:
        row = mem.execute(
            "SELECT kind,status,reason FROM controller_debt WHERE debt_id=?",
            ("evidence_snapshot:path-mismatch",),
        ).fetchone()
    assert tuple(row) == ("evidence_snapshot", "retry_pending", "evidence_snapshot_path_mismatch")


def test_terminal_failure_is_turn_scoped_and_structured_codes_win(
    cfg: dict, tmp_path: Path
) -> None:
    path = tmp_path / "turns.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "turn.started", "turn_id": "old"}),
                json.dumps({"type": "error", "turn_id": "old", "message": "usage limit exceeded"}),
                json.dumps({"type": "turn.started", "turn_id": "current"}),
                json.dumps({"type": "error", "turn_id": "current", "message": "temporary network issue"}),
                json.dumps({"type": "turn.failed", "turn_id": "current", "error": {"code": "internal_error", "message": "failed"}}),
            ]
        )
        + "\n"
    )
    failure = loop._parse_terminal_failure(path)
    assert failure is not None
    assert failure["kind"] == "terminal_failure"

    path.write_text(
        json.dumps({"type": "turn.failed", "error": {"code": "quota_exceeded", "message": "failed"}})
        + "\n"
    )
    assert loop._parse_terminal_failure(path)["kind"] == "provider_quota_limit"

    path.write_text(
        json.dumps({"type": "turn.failed", "error": {"code": "rate_limit_exceeded", "message": "retry later"}})
        + "\n"
    )
    assert loop._parse_terminal_failure(path)["kind"] == "provider_rate_limit"

    path.write_text(
        json.dumps({"type": "error", "error": {"message": "the documentation says you've hit your usage limit"}})
        + "\n"
        + json.dumps({"type": "turn.failed", "error": {"message": "the documentation says you've hit your usage limit"}})
        + "\n"
    )
    assert loop._parse_terminal_failure(path)["kind"] == "terminal_failure"

    for message in ("Rate limit exceeded. Please retry.", "Resource exhausted; retry later.", "Too many requests"):
        path.write_text(
            json.dumps({"type": "turn.failed", "error": {"message": message}}) + "\n"
        )
        failure = loop._parse_terminal_failure(path)
        assert failure["kind"] == "provider_rate_limit"
        cfg["loop"]["provider_cooldown_seconds"] = 2
        with loop.memory(cfg) as mem:
            payload = loop._set_provider_backoff(
                cfg, mem, {**failure, "retry_at": None}
            )
            mem.commit()
        assert (loop.parse_time(payload["next_retry_at"]) - loop.now()).total_seconds() < 10


def test_retry_timestamp_units_and_invalid_values_fall_back_bounded(cfg: dict) -> None:
    cfg["loop"]["provider_cooldown_seconds"] = 2
    seconds = loop._retry_at_from_failure({"retry_after_seconds": 2}, cfg)
    milliseconds = loop._retry_at_from_failure({"retry_after_ms": 2_000}, cfg)
    assert seconds is not None and milliseconds is not None
    assert (loop.parse_time(seconds) - loop.now()).total_seconds() < 10
    assert (loop.parse_time(milliseconds) - loop.now()).total_seconds() < 10
    assert loop._retry_at_from_failure({"retry_after": "not-a-time"}, cfg) is None
    with loop.memory(cfg) as mem:
        payload = loop._set_provider_backoff(
            cfg, mem, {"provider_wide": True, "reason": "invalid retry"}
        )
        mem.commit()
    assert 0 < (loop.parse_time(payload["next_retry_at"]) - loop.now()).total_seconds() < 10


def test_absolute_provider_retry_targets_are_bounded_and_past_falls_back(cfg: dict) -> None:
    cfg["loop"].update(provider_cooldown_seconds=2, max_provider_backoff_seconds=10)
    far_iso = loop._retry_at_from_failure({"retry_at": "2099-01-01T00:00:00+00:00"}, cfg)
    far_epoch = loop._retry_at_from_failure({"retry_at": 4_102_444_800}, cfg)
    past = loop._retry_at_from_failure({"retry_at": "2000-01-01T00:00:00+00:00"}, cfg)
    assert far_iso is not None and far_epoch is not None
    assert 1.0 < (loop.parse_time(far_iso) - loop.now()).total_seconds() <= 10.5
    assert 1.0 < (loop.parse_time(far_epoch) - loop.now()).total_seconds() <= 10.5
    assert past is None
    with loop.memory(cfg) as mem:
        payload = loop._set_provider_backoff(
            cfg, mem, {"retry_at": "2099-01-01T00:00:00+00:00", "reason": "far"}
        )
        mem.commit()
    assert (loop.parse_time(payload["next_retry_at"]) - loop.now()).total_seconds() <= 10.5
    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({"next_retry_at": "2099-01-01T00:00:00+00:00", "reason": "raw-far"}),
        )
        mem.commit()
    migrated = loop._provider_backoff(cfg)
    assert migrated is not None
    assert (loop.parse_time(migrated["next_retry_at"]) - loop.now()).total_seconds() <= 10.5
    with loop.memory(cfg) as mem:
        persisted = json.loads(loop.meta_get(mem, "codex_provider_backoff"))
    assert (loop.parse_time(persisted["next_retry_at"]) - loop.now()).total_seconds() <= 10.5


def test_provider_backoff_reads_do_not_ratchet_and_legacy_policy_migrates_once(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(provider_cooldown_seconds=5, max_provider_backoff_seconds=60)
    fixed = datetime(2026, 8, 23, 20, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(loop, "now", lambda: fixed)
    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({
                "next_retry_at": loop.iso(fixed + timedelta(seconds=1)),
                "kind": "provider_rate_limit",
                "policy_revision": "provider-backoff-v2",
            }),
        )
        mem.commit()
    first = loop._provider_backoff(cfg)
    with loop.memory(cfg) as mem:
        raw_first = loop.meta_get(mem, "codex_provider_backoff")
    second = loop._provider_backoff(cfg)
    with loop.memory(cfg) as mem:
        raw_second = loop.meta_get(mem, "codex_provider_backoff")
    assert first == second
    assert raw_first == raw_second
    monkeypatch.setattr(loop, "now", lambda: fixed + timedelta(seconds=2))
    assert loop._provider_backoff(cfg) is None

    monkeypatch.setattr(loop, "now", lambda: fixed)
    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({
                "next_retry_at": loop.iso(fixed + timedelta(seconds=10)),
                "kind": "provider_quota_limit",
            }),
        )
        mem.commit()
    migrated_quota = loop._provider_backoff(cfg)
    assert migrated_quota["policy_revision"] == "provider-backoff-v2"
    assert loop.parse_time(migrated_quota["next_retry_at"]) == fixed + timedelta(seconds=60)
    assert loop._provider_backoff(cfg) == migrated_quota

    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({
                "next_retry_at": loop.iso(fixed + timedelta(seconds=60)),
                "kind": "provider_rate_limit",
            }),
        )
        mem.commit()
    migrated_rate = loop._provider_backoff(cfg)
    assert loop.parse_time(migrated_rate["next_retry_at"]) == fixed + timedelta(seconds=5)


def test_launch_guard_rechecks_durable_backoff_before_each_spawn(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(loop, "_spawn_run_unlocked", lambda *_args, **_kwargs: calls.append("spawn") or {})
    with loop.memory(cfg) as mem:
        loop._set_provider_backoff(
            cfg, mem,
            {"provider_wide": True, "reason": "quota", "retry_at": "2099-01-01T00:00:00+00:00"},
        )
        mem.commit()
    with pytest.raises(loop.ProviderBackoffActive):
        loop._spawn_run(cfg, incident_id="gated", kind="hard", stage="diagnosis", command=[], cwd=ROOT, prompt="", output=ROOT / "out", events=ROOT / "events")
    assert calls == []


def test_active_backoff_blocks_stale_capability_probe_and_expiry_allows_probe(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({"next_retry_at": "2099-01-01T00:00:00+00:00", "reason": "quota"}),
        )
        mem.commit()
    calls: list[str] = []
    monkeypatch.setattr(loop, "_probe_thread", None)
    monkeypatch.setattr(loop, "probe_capabilities", lambda *_args, **_kwargs: calls.append("probe") or {})
    loop.ensure_capability_probe(cfg)
    assert loop._probe_thread is not None
    loop._probe_thread.join(timeout=2)
    assert calls == []

    with loop.memory(cfg) as mem:
        loop.meta_set(
            mem,
            "codex_provider_backoff",
            json.dumps({"next_retry_at": "2000-01-01T00:00:00+00:00", "reason": "expired"}),
        )
        mem.commit()
    monkeypatch.setattr(loop, "_probe_thread", None)
    loop.ensure_capability_probe(cfg)
    assert loop._probe_thread is not None
    loop._probe_thread.join(timeout=2)
    assert calls == ["probe"]


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
    monkeypatch.setattr(
        loop,
        "build_evidence",
        lambda cfg, incident_id: Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db",
    )
    monkeypatch.setattr(
        loop,
        "_evidence_pair_paths",
        lambda cfg, incident_id: (
            Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "evidence.db",
            Path(cfg["paths"]["runtime"]) / "incidents" / incident_id / "manifest.json",
        ),
    )
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda *_args: True)
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

    def traced_open_ro(path: Path, **kwargs: object):
        conn = original_open_ro(path, **kwargs)
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


def test_no_bid_episode_reuses_canonical_incident_until_floor_recovery(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-episode-1", "2026-08-22T09:00:01+00:00", None)
    loop.detect(cfg)
    with loop.memory(cfg) as mem:
        mem.execute(
            "UPDATE incidents SET status='blocked',stage='evidence' WHERE crossing_kind='no_bid'"
        )
        mem.commit()
    _quote(cfg, "q-episode-incomplete", "2026-08-22T09:00:02+00:00", None)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json='not-json' "
            "WHERE evidence_id='q-episode-incomplete'"
        )
    loop.detect(cfg)
    _quote(cfg, "q-episode-2", "2026-08-22T09:00:03+00:00", None)
    loop.detect(cfg)
    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 1
    assert hard[0]["crossing_evidence_id"] == "q-episode-2"
    assert hard[0]["evidence_revision"] >= 2

    _quote(cfg, "q-recovered", "2026-08-22T09:00:04+00:00", 0.20)
    loop.detect(cfg)
    _quote(cfg, "q-episode-3", "2026-08-22T09:00:05+00:00", None)
    loop.detect(cfg)
    hard = [row for row in _incidents(cfg) if row["kind"] == "hard"]
    assert len(hard) == 2


def test_no_bid_recovery_closes_generation_and_new_position_isolated(cfg: dict) -> None:
    _position(cfg, position_id="episode-a", token_id="episode-a-token")
    _position(cfg, position_id="episode-b", token_id="episode-b-token")
    def observe(evidence_id: str, position_id: str) -> None:
        with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
            position = loop.tracked_positions(trades, history_days=7)[position_id]
            quote = dict(trades.execute(
                "SELECT * FROM execution_feasibility_evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone())
        with loop.memory(cfg) as mem:
            loop._observe_quote(mem, position, quote, 0.05)
            mem.commit()
    _quote(cfg, "a-no-bid-1", "2026-08-22T09:00:01+00:00", None, token="episode-a-token")
    _quote(cfg, "b-no-bid-1", "2026-08-22T09:00:01+00:00", None, token="episode-b-token")
    observe("a-no-bid-1", "episode-a")
    observe("b-no-bid-1", "episode-b")
    _quote(cfg, "a-recovered", "2026-08-22T09:00:02+00:00", 0.20, token="episode-a-token")
    observe("a-recovered", "episode-a")
    _quote(cfg, "a-below", "2026-08-22T09:00:03+00:00", 0.01, token="episode-a-token")
    observe("a-below", "episode-a")
    _quote(cfg, "a-no-bid-2", "2026-08-22T09:00:04+00:00", None, token="episode-a-token")
    observe("a-no-bid-2", "episode-a")
    with loop.memory(cfg) as mem:
        rows = mem.execute(
            "SELECT position_id,crossing_evidence_id FROM incidents "
            "WHERE kind='hard' AND crossing_kind='no_bid' ORDER BY position_id,detected_at"
        ).fetchall()
        states = {
            row[0]: (row[1], row[2])
            for row in mem.execute(
                "SELECT position_id,no_bid_episode_generation,no_bid_episode_open "
                "FROM position_quote_state WHERE position_id IN ('episode-a','episode-b')"
            )
        }
    assert [(row[0], row[1]) for row in rows] == [
        ("episode-a", "a-no-bid-1"),
        ("episode-a", "a-no-bid-2"),
        ("episode-b", "b-no-bid-1"),
    ]
    assert states["episode-a"] == (1, 1)
    assert states["episode-b"] == (0, 1)


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
    monkeypatch.setattr(loop, "build_evidence", lambda _cfg, incident_id: runtime / "incidents" / incident_id / "evidence.db")
    monkeypatch.setattr(
        loop,
        "_evidence_pair_paths",
        lambda _cfg, incident_id: (
            runtime / "incidents" / incident_id / "evidence.db",
            runtime / "incidents" / incident_id / "manifest.json",
            ),
        )
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda *_args: True)
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
    _event(
        cfg, "monitor-3", "p1", 3, "MONITOR_REFRESHED",
        "2026-08-22T09:00:20+00:00",
        phase_before="active", phase_after="active",
        payload={
            "last_monitor_prob": 0.30,
            "last_monitor_market_price": 0.05,
            "last_monitor_prob_is_fresh": True,
            "last_monitor_market_price_is_fresh": True,
        },
    )

    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        (
            probability_velocity,
            market_velocity,
            market_acceleration,
            probability,
            fresh,
            _,
        ) = loop._monitor_dynamics(trades, "p1")

    assert probability_velocity == pytest.approx(0.0)
    assert market_velocity == pytest.approx(-0.015)
    assert market_acceleration == pytest.approx(-0.005)
    assert probability == pytest.approx(0.30)
    assert fresh is True


def test_evidence_db_exposes_timeline_tables_without_copying_canonical_db(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-before", "2026-08-22T09:00:01+00:00", 0.08, latest=False)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)
    _quote(cfg, "q-after", "2026-08-22T09:00:03+00:00", 0.02, latest=False)
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
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    manifest = json.loads((incident_dir / "generations" / (incident_dir / "CURRENT").read_text().strip() / "manifest.json").read_text())
    assert manifest["size_bytes"] == evidence.stat().st_size
    assert manifest["capacity"]["window_days"] <= 7
    assert evidence.stat().st_size < Path(cfg["paths"]["trades_db"]).stat().st_size * 20


def test_quote_evidence_metadata_stream_preserves_required_clocks_and_truthfully_truncates(
    cfg: dict,
) -> None:
    source = inspect.getsource(loop._select_evidence_quotes)
    assert ".fetchall(" not in source
    assert "fetchmany(batch_rows)" in source
    assert "LENGTH(depth_before_json)" in source
    assert "WHERE rowid=?" in source
    _position(cfg)
    _quote(cfg, "strict-pre", "2026-08-22T09:00:01+00:00", 0.08, latest=False)
    _quote(cfg, "strict-crossing", "2026-08-22T09:00:02+00:00", 0.01)
    _quote(cfg, "strict-post", "2026-08-22T09:00:03+00:00", 0.02, latest=False)
    _quote(cfg, "large-trajectory", "2026-08-22T09:00:04+00:00", 0.03, latest=False)
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json=? WHERE evidence_id='large-trajectory'",
            (json.dumps({"bids": [{"price": "0.03", "size": "x" * 8192}]}),),
        )
    incident_id = loop.detect(cfg)[0]
    cfg["loop"].update(evidence_quote_source_max_bytes=4096, evidence_max_bytes=1024 * 1024)
    evidence = loop.build_evidence(cfg, incident_id)
    with sqlite3.connect(evidence) as conn:
        ids = {row[0] for row in conn.execute("SELECT evidence_id FROM price_ticks")}
    assert {"strict-pre", "strict-crossing", "strict-post"} <= ids
    manifest = json.loads((evidence.parent / "manifest.json").read_text())
    selection = manifest["selection"]["quotes"]
    assert selection["critical_crossing_retained"] is True
    assert selection["t_floor_strict_brackets"] == {"pre": "retained", "post": "retained"}
    assert selection["causal_completeness"] == "sampled_not_complete"
    assert selection["truncation_reason"] == "source_payload_limit"
    assert selection["critical_rows_reserved_outside_trajectory_cap"] >= 3


def test_monitor_anchor_cap_missing_critical_and_capacity_fingerprint_are_explicit(
    cfg: dict,
) -> None:
    _position(cfg)
    _quote(cfg, "anchor-pre", "2026-08-22T09:00:01+00:00", 0.08, latest=False)
    _quote(cfg, "anchor-crossing", "2026-08-22T09:00:02+00:00", 0.01)
    _quote(cfg, "anchor-post", "2026-08-22T09:00:03+00:00", 0.02, latest=False)
    incident_id = loop.detect(cfg)[0]
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.executemany(
            "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"monitor-{index}", "p1", index + 1, "MONITOR_REFRESHED",
                    (datetime(2026, 8, 22, 10, tzinfo=UTC) + timedelta(seconds=index)).isoformat(),
                    None,
                    json.dumps({"exit_decision_should_exit": False, "q_version": "q1", "probability_is_fresh": True}),
                    "active", "active",
                )
                for index in range(6500)
            ],
        )
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        monitors = trades.execute("SELECT * FROM position_events WHERE position_id='p1' ORDER BY occurred_at,event_id").fetchall()
        clocks, coverage = loop._select_critical_clock_times(monitors, [], limit=128)
        budget = {"deadline": loop.time.monotonic() + 1.0, "max_bytes": 1024 * 1024}
        with pytest.raises(loop.EvidenceCapacityExceeded, match="required_missing:crossing"):
            loop._select_evidence_quotes(
                trades, token_id="yes-token", crossing_evidence_id="missing", floor_at=loop.parse_time("2026-08-22T09:00:02+00:00"),
                clock_times=clocks, start=loop.parse_time("2026-08-21T00:00:00+00:00"), end=loop.parse_time("2026-08-23T00:00:00+00:00"),
                row_limit=1, cfg=cfg, budget=budget,
            )
    assert coverage["monitor_events_total"] == 6500
    assert len(clocks) <= 128
    assert coverage["clock_candidates_omitted"] == 0
    budget = loop._new_evidence_budget(cfg)
    first = loop._evidence_identity_fingerprints(cfg, incident_id, budget)[0]
    cfg["loop"]["evidence_quote_fetch_batch_rows"] = 7
    second = loop._evidence_identity_fingerprints(cfg, incident_id, loop._new_evidence_budget(cfg))[0]
    assert first != second


def test_single_critical_depth_blob_becomes_typed_capacity_debt(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "blob-pre", "2026-08-22T09:00:01+00:00", 0.08, latest=False)
    _quote(cfg, "blob-crossing", "2026-08-22T09:00:02+00:00", 0.01)
    _quote(cfg, "blob-post", "2026-08-22T09:00:03+00:00", 0.02, latest=False)
    incident_id = loop.detect(cfg)[0]
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE execution_feasibility_evidence SET depth_before_json=? WHERE evidence_id='blob-crossing'",
            (json.dumps({"bids": [{"price": "0.01", "size": "x" * 65536}]}),),
        )
    cfg["loop"]["evidence_quote_source_max_bytes"] = 1024
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        with pytest.raises(loop.EvidenceCapacityExceeded, match="quote_critical_capacity:crossing"):
            loop._select_evidence_quotes(
                trades, token_id="yes-token", crossing_evidence_id="blob-crossing",
                floor_at=loop.parse_time("2026-08-22T09:00:02+00:00"), clock_times=[],
                start=loop.parse_time("2026-08-21T00:00:00+00:00"), end=loop.parse_time("2026-08-23T00:00:00+00:00"),
                row_limit=1, cfg=cfg, budget=loop._new_evidence_budget(cfg),
            )
    assert incident_id


def test_source_clock_selector_uses_exact_city_metadata_and_truthful_payload_cap(cfg: dict) -> None:
    with sqlite3.connect(cfg["paths"]["forecasts_db"]) as conn:
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN payload_json TEXT")
        conn.execute(
            "CREATE INDEX source_city_target_metric_time ON forecast_posteriors(city,target_date,temperature_metric,source_available_at)"
        )
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (10,'London','2026-08-22','28C','2026-08-22T00:00:00+00:00','2026-08-22T10:00:00+00:00','2026-08-22T12:00:00+00:00','2026-08-22T12:00:00+00:00',?)",
            ("small",),
        )
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (11,'London','2026-08-22','28C','2026-08-22T00:00:00+00:00',NULL,'2026-08-22T11:00:00+00:00','2026-08-22T11:00:00+00:00',?)",
            ("x" * (35 * 1024 * 1024),),
        )
        conn.executemany(
            "INSERT INTO forecast_posteriors VALUES (?,?,?,?,?,?,?,?,?)",
            [(index + 12, "Paris", "2026-08-22", "28C", "", "2026-08-22T10:00:00+00:00", "2026-08-22T10:00:00+00:00", "", "decoy") for index in range(20_000)],
        )
        conn.execute(
            "INSERT INTO ensemble_snapshots VALUES (3,'London','2026-08-22','28C','cycle',NULL,NULL,'2026-08-22T10:00:00+00:00','2026-08-22T10:00:00+00:00','2026-08-22T10:00:00+00:00')"
        )
    position = {"city": "London", "target_date": "2026-08-22", "temperature_metric": "28C"}
    with loop.open_ro(Path(cfg["paths"]["forecasts_db"])) as forecasts:
        plan = forecasts.execute(
            "EXPLAIN QUERY PLAN SELECT rowid FROM forecast_posteriors WHERE city=? AND target_date=? AND temperature_metric=? AND (source_available_at BETWEEN ? AND ? OR source_available_at IS NULL)",
            ("London", "2026-08-22", "28C", "2026-08-21T00:00:00+00:00", "2026-08-23T00:00:00+00:00"),
        ).fetchall()
        rows, coverage = loop._source_clock_rows(
            forecasts, position, "2026-08-21T00:00:00+00:00", "2026-08-23T00:00:00+00:00",
            row_limit=10, byte_limit=1024 * 1024, budget={"deadline": loop.time.monotonic() + 5},
        )
    assert "SEARCH" in " ".join(str(value) for row in plan for value in row).upper()
    assert [row["posterior_id"] for row in rows["forecast_posteriors"]] == [10]
    assert rows["ensemble_snapshots"][0]["snapshot_id"] == 3
    assert coverage["truncated"] is True and coverage["reason"] == "source_byte_limit"


@pytest.mark.parametrize("table", ("forecast_posteriors", "ensemble_snapshots"))
def test_source_clock_selector_rejects_missing_required_table(cfg: dict, table: str) -> None:
    with sqlite3.connect(cfg["paths"]["forecasts_db"]) as conn:
        conn.execute(f"DROP TABLE {table}")
    with loop.open_ro(Path(cfg["paths"]["forecasts_db"])) as forecasts:
        with pytest.raises(loop.EvidenceCapacityExceeded, match=f"required_missing:{table}:schema"):
            loop._source_clock_rows(
                forecasts,
                {"city": "London", "target_date": "2026-08-22", "temperature_metric": "high"},
                "2026-08-21T00:00:00+00:00", "2026-08-23T00:00:00+00:00",
                row_limit=1, byte_limit=1024, budget={"deadline": loop.time.monotonic() + 1},
            )


def test_no_bid_crossing_is_retained_outside_trajectory_window(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "outside-no-bid", "2026-08-20T09:00:00+00:00", None, latest=False)
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        rows, selection = loop._select_evidence_quotes(
            trades, token_id="yes-token", crossing_evidence_id="outside-no-bid", floor_at=None,
            clock_times=[], start=loop.parse_time("2026-08-22T00:00:00+00:00"),
            end=loop.parse_time("2026-08-23T00:00:00+00:00"), row_limit=1,
            cfg=cfg, budget=loop._new_evidence_budget(cfg),
        )
    assert [row["evidence_id"] for row in rows] == ["outside-no-bid"]
    assert selection["critical_crossing_required"] is True
    assert selection["critical_crossing_retained"] is True


def test_missing_t_floor_boundary_bracket_is_typed_capacity_debt(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "boundary-crossing", "2026-08-22T09:00:02+00:00", 0.01, latest=False)
    with loop.open_ro(Path(cfg["paths"]["trades_db"])) as trades:
        with pytest.raises(loop.EvidenceCapacityExceeded, match="required_missing:t_floor_strict_pre"):
            loop._select_evidence_quotes(
                trades, token_id="yes-token", crossing_evidence_id="boundary-crossing",
                floor_at=loop.parse_time("2026-08-22T09:00:02+00:00"), clock_times=[],
                start=loop.parse_time("2026-08-21T00:00:00+00:00"),
                end=loop.parse_time("2026-08-23T00:00:00+00:00"), row_limit=1,
                cfg=cfg, budget=loop._new_evidence_budget(cfg),
            )


def test_incomplete_generation_is_reaped_without_disturbing_current_pair(cfg: dict) -> None:
    cfg["loop"]["evidence_generation_reap_age_seconds"] = 0
    _position(cfg)
    _evidence_quote_triplet(cfg, "reap-q", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    incomplete = incident_dir / "generations" / "interrupted-generation"
    incomplete.mkdir(parents=True)
    (incomplete / ".evidence.db.tmp").write_bytes(b"partial")
    assert loop._evidence_pair_valid(cfg, incident_id)
    loop._capture_hard_evidence(cfg, [incident_id])
    assert not incomplete.exists()
    assert loop._evidence_pair_valid(cfg, incident_id)


def test_pair_validation_uses_manifest_stat_gate_without_full_read_each_cycle(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "pair-stat", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("pair validation must not read full DB"))
    assert loop._evidence_pair_valid(cfg, incident_id)


def test_pair_hash_mismatch_uses_shared_deadline_and_records_typed_defer(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "pair-timeout", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    pair = loop._evidence_pair_paths(cfg, incident_id)
    assert pair is not None
    evidence = pair[0]
    stat = evidence.stat()
    os.utime(evidence, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    with pytest.raises(loop.EvidenceCapacityExceeded, match="evidence_pair_hash_deferred"):
        loop._evidence_pair_valid(cfg, incident_id, budget={"deadline": loop.time.monotonic() - 1})
    os.utime(evidence, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert loop._evidence_pair_valid(cfg, incident_id)


def test_coverage_timeout_is_typed_and_never_publishes_new_current_pair(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "coverage-timeout", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    prior = (incident_dir / "CURRENT").read_text()
    monkeypatch.setattr(
        loop,
        "_evidence_coverage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            loop.EvidenceCapacityExceeded("evidence_coverage_deferred:time_budget")
        ),
    )
    with pytest.raises(loop.EvidenceCapacityExceeded, match="evidence_coverage_deferred"):
        loop.build_evidence(cfg, incident_id)
    assert (incident_dir / "CURRENT").read_text() == prior
    assert len(list((incident_dir / "generations").iterdir())) == 1
    assert loop._evidence_pair_valid(cfg, incident_id)


def test_hash_exception_cleans_orphan_generation_immediately(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "hash-orphan", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    prior = (incident_dir / "CURRENT").read_text()
    monkeypatch.setattr(
        loop,
        "_stream_evidence_hash",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            loop.EvidenceCapacityExceeded("evidence_pair_hash_deferred:time_budget")
        ),
    )
    with pytest.raises(loop.EvidenceCapacityExceeded, match="evidence_pair_hash_deferred"):
        loop.build_evidence(cfg, incident_id)
    assert (incident_dir / "CURRENT").read_text() == prior
    assert len(list((incident_dir / "generations").iterdir())) == 1
    assert loop._evidence_pair_valid(cfg, incident_id)


@pytest.mark.parametrize("boundary", ("hash", "manifest"))
def test_publish_boundaries_guard_same_budget_and_keep_old_current(
    cfg: dict, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, f"publish-{boundary}", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    incident_dir = Path(cfg["paths"]["runtime"]) / "incidents" / incident_id
    prior = (incident_dir / "CURRENT").read_text()
    real_monotonic = loop.time.monotonic
    expired = False

    def monotonic() -> float:
        return real_monotonic() + (1000.0 if expired else 0.0)

    monkeypatch.setattr(loop.time, "monotonic", monotonic)
    if boundary == "hash":
        original_hash = loop._stream_evidence_hash

        def expire_after_hash(path: Path, budget: dict) -> str:
            nonlocal expired
            result = original_hash(path, budget)
            expired = True
            return result

        monkeypatch.setattr(loop, "_stream_evidence_hash", expire_after_hash)
    else:
        original_write_text = Path.write_text

        def expire_after_manifest(path: Path, data: str, *args, **kwargs):
            nonlocal expired
            result = original_write_text(path, data, *args, **kwargs)
            if path.name == ".manifest.json.tmp":
                expired = True
            return result

        monkeypatch.setattr(Path, "write_text", expire_after_manifest)

    with pytest.raises(loop.EvidenceCapacityExceeded, match="time_budget"):
        loop.build_evidence(cfg, incident_id)
    assert (incident_dir / "CURRENT").read_text() == prior
    assert loop._evidence_pair_valid(cfg, incident_id)
    assert len(list((incident_dir / "generations").iterdir())) == 1


def test_pair_identity_sqlite_interrupt_is_typed_defer(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "identity-timeout", "2026-08-22T09:00:02+00:00", 0.01)
    incident_id = loop.detect(cfg)[0]
    monkeypatch.setattr(
        loop,
        "_apply_evidence_sql_budget",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("interrupted")),
    )
    with pytest.raises(loop.EvidenceCapacityExceeded, match="evidence_pair_identity_deferred"):
        loop._evidence_pair_valid(cfg, incident_id, budget={"deadline": loop.time.monotonic() + 1})


def test_evidence_wallet_fills_follow_command_trade_ids_without_token_scan(
    cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _position(cfg)
    _evidence_quote_triplet(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)
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

    def traced_open_ro(path: Path, **kwargs: object):
        conn = original_open_ro(path, **kwargs)
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


def _queue_evidence_retry_incident(cfg: dict, incident_id: str, position_id: str) -> None:
    _position(cfg, position_id=position_id)
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, "hard", position_id, f"{incident_id}-evidence", "below_floor", "yes-token", "sell_yes", .05,
             "2026-08-22T12:00:00+00:00", 1.0, "blocked", "evidence", "2026-08-22T12:00:00+00:00"),
        )
        mem.commit()


def test_evidence_retry_backoff_skips_heavy_query_and_attempt_before_due(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg["loop"]["evidence_retry_base_seconds"] = 60
    _queue_evidence_retry_incident(cfg, "retry-backoff", "retry-position")
    heavy_calls: list[str] = []
    build_calls: list[str] = []
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda _cfg, incident_id, _budget: heavy_calls.append(incident_id) or ("full", "cfg", "cap", "data"))
    monkeypatch.setattr(loop, "build_evidence", lambda *_args: build_calls.append("attempt") or (_ for _ in ()).throw(loop.EvidenceCapacityExceeded("oversized")))
    loop._capture_hard_evidence(cfg, ["retry-backoff"])
    with loop.memory(cfg) as mem:
        first = mem.execute("SELECT attempts,updated_at,next_retry_at,retry_identity FROM controller_debt WHERE debt_id=?", ("evidence_snapshot:retry-backoff",)).fetchone()
    loop._capture_hard_evidence(cfg, ["retry-backoff"])
    with loop.memory(cfg) as mem:
        second = mem.execute("SELECT attempts,updated_at,next_retry_at,retry_identity FROM controller_debt WHERE debt_id=?", ("evidence_snapshot:retry-backoff",)).fetchone()
    assert heavy_calls == ["retry-backoff"]
    assert build_calls == ["attempt"]
    assert tuple(second) == tuple(first)


def test_evidence_retry_identity_change_bypasses_backoff(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _queue_evidence_retry_incident(cfg, "retry-identity", "identity-position")
    heavy_calls: list[str] = []
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda _cfg, incident_id, _budget: heavy_calls.append(incident_id) or ("full", "cfg", "cap", "data"))
    attempts = {"count": 0}
    def build(local_cfg: dict, incident_id: str) -> Path:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise loop.EvidenceCapacityExceeded("oversized")
        return Path(local_cfg["paths"]["runtime"]) / incident_id / "evidence.db"
    monkeypatch.setattr(loop, "build_evidence", build)
    loop._capture_hard_evidence(cfg, ["retry-identity"])
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute("UPDATE position_current SET updated_at=? WHERE position_id=?", ("2026-08-22T13:00:00+00:00", "identity-position"))
    with loop.memory(cfg) as mem:
        mem.execute(
            "UPDATE incidents SET evidence_revision=evidence_revision+1 "
            "WHERE incident_id='retry-identity'"
        )
        mem.commit()
    loop._capture_hard_evidence(cfg, ["retry-identity"])
    assert heavy_calls == ["retry-identity", "retry-identity"]
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM controller_debt WHERE debt_id=?", ("evidence_snapshot:retry-identity",)).fetchone()[0] == "resolved"


def test_evidence_debt_fifo_keeps_new_hard_incident_first(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg["loop"]["evidence_builds_per_cycle"] = 3
    for incident_id, position_id in (("debt-a", "debt-position-a"), ("debt-b", "debt-position-b"), ("new-hard", "new-position")):
        _queue_evidence_retry_incident(cfg, incident_id, position_id)
    with loop.memory(cfg) as mem:
        for incident_id, retry_at in (("debt-a", "2026-08-22T12:00:02+00:00"), ("debt-b", "2026-08-22T12:00:01+00:00")):
            mem.execute("INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,retry_identity,next_retry_at) VALUES (?,?,?,?,?,?,?)", (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "old", "2026-08-22T12:00:00+00:00", incident_id, retry_at))
        mem.commit()
    order: list[str] = []
    monkeypatch.setattr(loop, "_evidence_pair_valid", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda *_args: ("full", "cfg", "cap", "data"))
    monkeypatch.setattr(loop, "build_evidence", lambda local_cfg, incident_id: order.append(incident_id) or Path(local_cfg["paths"]["runtime"]) / incident_id / "evidence.db")
    result = loop._capture_hard_evidence(cfg, ["new-hard"], scan_all=True)
    assert result["built"] == ["new-hard", "debt-b"]
    assert order == result["built"]


def test_detect_uses_one_shared_evidence_capture(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], object]] = []
    monkeypatch.setattr(loop, "_detect_trigger", lambda *_args: ["trigger-id"])
    monkeypatch.setattr(loop, "_detect_maintenance", lambda *_args: ["maintenance-id"])
    monkeypatch.setattr(loop, "_publish_trigger_receipt", lambda *_args: None)
    monkeypatch.setattr(loop, "_capture_hard_evidence", lambda _cfg, ids, **kwargs: calls.append((list(ids), kwargs.get("budget"))) or {"built": [], "deferred": []})
    assert loop.detect(cfg) == ["maintenance-id", "trigger-id"]
    assert len(calls) == 1
    assert calls[0][0] == ["maintenance-id", "trigger-id"]


def test_orphan_reconcile_syncs_terminal_model_ledger_and_preserves_live_runtime(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="ledger-terminal")
    with loop.memory(cfg) as mem:
        for run_id, stage in (("ledger-terminal-run", "diagnosis"), ("ledger-live-run", "classification")):
            mem.execute("INSERT INTO model_runs(run_id,incident_id,stage,session_id,model,reasoning_effort,started_at,status,events_path) VALUES (?,?,?,?,?,?,?,?,?)", (run_id, "ledger-terminal", stage, None, "model", "high", "2026-08-22T12:00:00+00:00", "running", str(Path(cfg["paths"]["runtime"]) / "events")))
        mem.commit()
    runs = Path(cfg["paths"]["runtime"]) / "runs"
    loop.atomic_json(runs / "ledger-terminal-run.json", {"run_id": "ledger-terminal-run", "incident_id": "ledger-terminal", "status": "completed", "completed_at": "2026-08-22T12:01:00+00:00"})
    loop.atomic_json(runs / "ledger-live-run.json", {"run_id": "ledger-live-run", "incident_id": "ledger-terminal", "pid": 123, "status": "running"})
    monkeypatch.setattr(loop, "_pid_alive", lambda pid: pid == 123)
    assert loop.reconcile_orphan_incidents(cfg) == []
    with loop.memory(cfg) as mem:
        rows = mem.execute("SELECT run_id,status FROM model_runs ORDER BY run_id").fetchall()
    assert [tuple(row) for row in rows] == [("ledger-live-run", "running"), ("ledger-terminal-run", "completed")]


def test_orphan_reconcile_missing_model_json_fails_stale_ledger(cfg: dict) -> None:
    _queue_blind_dispatch_debt(cfg, incident_id="ledger-missing")
    with loop.memory(cfg) as mem:
        mem.execute("INSERT INTO model_runs(run_id,incident_id,stage,session_id,model,reasoning_effort,started_at,status,events_path) VALUES (?,?,?,?,?,?,?,?,?)", ("ledger-missing-run", "ledger-missing", "diagnosis", None, "model", "high", "2026-08-22T12:00:00+00:00", "running", str(Path(cfg["paths"]["runtime"]) / "events")))
        mem.commit()
    assert loop.reconcile_orphan_incidents(cfg) == []
    with loop.memory(cfg) as mem:
        assert mem.execute("SELECT status FROM model_runs WHERE run_id='ledger-missing-run'").fetchone()[0] == "failed"


def test_expired_budget_retry_backoff_is_stable_until_identity_changes(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queue_evidence_retry_incident(cfg, "expired-budget", "expired-position")
    fixed = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(loop, "now", lambda: fixed)
    heavy_calls: list[str] = []
    monkeypatch.setattr(
        loop,
        "_evidence_fingerprints",
        lambda *_args: heavy_calls.append("heavy") or pytest.fail("expired retry must not use full fingerprint"),
    )
    expired = {"deadline": loop.time.monotonic() - 1.0, "remaining": 1, "max_bytes": 1024}
    first = loop._capture_hard_evidence(cfg, ["expired-budget"], budget=expired)
    assert first["deferred"] == ["expired-budget"]
    with loop.memory(cfg) as mem:
        before = mem.execute(
            "SELECT attempts,updated_at,next_retry_at,retry_identity FROM controller_debt WHERE debt_id=?",
            ("evidence_snapshot:expired-budget",),
        ).fetchone()
    assert before[0] == 1
    assert loop.parse_time(before[2]) > fixed
    second = loop._capture_hard_evidence(
        cfg,
        ["expired-budget"],
        budget={"deadline": loop.time.monotonic() - 1.0, "remaining": 1, "max_bytes": 1024},
    )
    assert second["deferred"] == ["expired-budget"]
    with loop.memory(cfg) as mem:
        unchanged = mem.execute(
            "SELECT attempts,updated_at,next_retry_at,retry_identity FROM controller_debt WHERE debt_id=?",
            ("evidence_snapshot:expired-budget",),
        ).fetchone()
    assert tuple(unchanged) == tuple(before)
    assert heavy_calls == []
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute(
            "UPDATE position_current SET updated_at=? WHERE position_id=?",
            ("2026-08-24T13:00:00+00:00", "expired-position"),
        )
    with loop.memory(cfg) as mem:
        mem.execute(
            "UPDATE incidents SET evidence_revision=evidence_revision+1 WHERE incident_id=?",
            ("expired-budget",),
        )
        mem.commit()
    changed = loop._capture_hard_evidence(
        cfg,
        ["expired-budget"],
        budget={"deadline": loop.time.monotonic() - 1.0, "remaining": 1, "max_bytes": 1024},
    )
    assert changed["deferred"] == ["expired-budget"]
    with loop.memory(cfg) as mem:
        after_change = mem.execute(
            "SELECT attempts,retry_identity FROM controller_debt WHERE debt_id=?",
            ("evidence_snapshot:expired-budget",),
        ).fetchone()
    assert after_change[0] == 2
    assert after_change[1] != before[3]


def test_large_not_due_debt_slice_skips_pair_and_heavy_queries(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(evidence_queue_batch_size=700, evidence_builds_per_cycle=3)
    _position(cfg, position_id="bulk-retry-position")
    debt_ids = [f"bulk-debt-{index:03d}" for index in range(700)]
    with loop.memory(cfg) as mem:
        for incident_id in debt_ids:
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (incident_id, "hard", "bulk-retry-position", f"{incident_id}-evidence", "below_floor", "yes-token", "sell_yes", .05,
                 "2026-08-24T12:00:00+00:00", 1.0, "blocked", "evidence", "2026-08-24T12:00:00+00:00"),
            )
            mem.execute(
                "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,retry_identity,next_retry_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "old", "2026-08-24T12:00:00+00:00", f"identity:{incident_id}", "2099-01-01T00:00:00+00:00"),
            )
        mem.commit()
    fixed = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(loop, "now", lambda: fixed)
    pair_calls: list[str] = []
    heavy_calls: list[str] = []
    monkeypatch.setattr(loop, "_evidence_identity_fingerprints", lambda _cfg, incident_id, _budget: (f"identity:{incident_id}", "cfg", "cap", "data"))
    monkeypatch.setattr(loop, "_capture_pair_valid", lambda _cfg, incident_id, _budget: pair_calls.append(incident_id) or False)
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda _cfg, incident_id, _budget: heavy_calls.append(incident_id) or ("full", "cfg", "cap", "data"))
    first = loop._capture_hard_evidence(cfg, [], scan_all=True)
    assert len(first["deferred"]) == 700
    assert pair_calls == []
    assert heavy_calls == []

    _queue_evidence_retry_incident(cfg, "bulk-new-hard", "bulk-new-position")
    build_calls: list[str] = []
    monkeypatch.setattr(loop, "build_evidence", lambda local_cfg, incident_id: build_calls.append(incident_id) or Path(local_cfg["paths"]["runtime"]) / incident_id / "evidence.db")
    with loop.memory(cfg) as mem:
        mem.execute("UPDATE controller_debt SET status='resolved' WHERE kind='evidence_snapshot' AND debt_id NOT IN (?,?)", ("evidence_snapshot:bulk-debt-000", "evidence_snapshot:bulk-debt-001"))
        loop.meta_set(mem, "evidence_queue", json.dumps(["bulk-new-hard"]))
        mem.commit()
    loop._capture_hard_evidence(cfg, ["bulk-new-hard"])
    assert build_calls == ["bulk-new-hard"]

    with loop.memory(cfg) as mem:
        incident = mem.execute(
            "SELECT incident_id,position_id,crossing_evidence_id,evidence_revision "
            "FROM incidents WHERE incident_id='bulk-debt-000'"
        ).fetchone()
        mem.execute(
            "UPDATE controller_debt SET retry_identity=? WHERE debt_id=?",
            (loop._memory_only_evidence_retry_identity(cfg, incident), "evidence_snapshot:bulk-debt-000"),
        )
        mem.execute(
            "UPDATE incidents SET evidence_revision=evidence_revision+1 "
            "WHERE incident_id='bulk-debt-000'"
        )
        mem.execute("UPDATE controller_debt SET next_retry_at=? WHERE debt_id=?", ("2099-01-01T00:00:00+00:00", "evidence_snapshot:bulk-debt-001"))
        loop.meta_set(mem, "evidence_queue", json.dumps(["bulk-debt-000"]))
        mem.commit()
    assert loop._memory_only_evidence_due_filter(
        cfg,
        ["bulk-debt-000"],
        created_order=["bulk-debt-000"],
        debt_order={"bulk-debt-000": 0},
    )[0] == ["bulk-debt-000"]
    monkeypatch.setattr(
        loop,
        "_evidence_retry_state",
        lambda *_args: (("full", "cfg", "cap", "data"), None),
    )
    loop._capture_hard_evidence(cfg, ["bulk-debt-000"])
    with loop.memory(cfg) as mem:
        mem.execute("UPDATE controller_debt SET next_retry_at=? WHERE debt_id=?", ("2020-01-01T00:00:00+00:00", "evidence_snapshot:bulk-debt-001"))
        loop.meta_set(mem, "evidence_queue", json.dumps(["bulk-debt-001"]))
        mem.commit()
    loop._capture_hard_evidence(cfg, ["bulk-debt-001"])
    assert pair_calls == ["bulk-new-hard", "bulk-debt-000", "bulk-debt-001"]
    assert heavy_calls == ["bulk-new-hard", "bulk-debt-000", "bulk-debt-001"]
    assert build_calls == ["bulk-new-hard", "bulk-debt-000", "bulk-debt-001"]


def test_memory_gate_upgrades_legacy_and_skips_not_due_without_canonical_open(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """100 legacy/not-due rows are settled in memory before any canonical read."""
    cfg["loop"].update(evidence_queue_batch_size=128, evidence_retry_base_seconds=60)
    fixed = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(loop, "now", lambda: fixed)
    with loop.memory(cfg) as mem:
        for index in range(100):
            incident_id = f"memory-gate-{index:03d}"
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (incident_id, "hard", f"p-{index}", f"e-{index}", "below_floor", "yes-token", "sell_yes", .05,
                 fixed.isoformat(), 1.0, "blocked", "evidence", fixed.isoformat()),
            )
            if index < 50:
                mem.execute(
                    "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,attempts,retry_identity,next_retry_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "legacy", fixed.isoformat(), 0,
                     "", None),
                )
            else:
                mem.execute(
                    "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,attempts,retry_identity,next_retry_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "not_due", fixed.isoformat(), 0,
                     f"stable-{index}", "2099-01-01T00:00:00+00:00"),
                )
        mem.commit()
    opens: list[Path] = []
    real_open_ro = loop.open_ro

    def counted_open_ro(path: Path, **kwargs):
        opens.append(Path(path))
        return real_open_ro(path, **kwargs)

    monkeypatch.setattr(loop, "open_ro", counted_open_ro)
    monkeypatch.setattr(
        loop, "_capture_pair_valid", lambda *_args, **_kwargs: pytest.fail("not-due must not validate pair")
    )
    monkeypatch.setattr(
        loop, "_evidence_fingerprints", lambda *_args, **_kwargs: pytest.fail("not-due must not fingerprint")
    )
    first = loop._capture_hard_evidence(cfg, [], scan_all=True)
    second = loop._capture_hard_evidence(cfg, [], scan_all=True)
    assert len(first["deferred"]) == 100
    assert len(second["deferred"]) == 100
    assert opens == []
    with loop.memory(cfg) as mem:
        rows = mem.execute(
            "SELECT attempts,retry_identity,next_retry_at FROM controller_debt "
            "WHERE kind='evidence_snapshot' ORDER BY debt_id"
        ).fetchall()
    assert len(rows) == 100
    assert all(int(row[0]) == 0 for row in rows)
    assert all(str(row[1]) for row in rows)
    assert all(loop.parse_time(str(row[2])) > fixed for row in rows)


def test_memory_gate_saturates_huge_legacy_attempt_without_canonical_open(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"].update(
        evidence_queue_batch_size=8,
        evidence_retry_base_seconds=1,
        evidence_retry_max_seconds=300,
    )
    fixed = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(loop, "now", lambda: fixed)
    assert [loop._evidence_retry_delay(cfg, attempts) for attempts in range(5)] == [1, 1, 2, 4, 8]
    assert loop._evidence_retry_delay(cfg, 2607) == 300
    with loop.memory(cfg) as mem:
        mem.execute(
            "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
            "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("huge-legacy-debt", "hard", "p-huge", "e-huge", "below_floor", "yes-token", "sell_yes", .05,
             fixed.isoformat(), 1.0, "blocked", "evidence", fixed.isoformat()),
        )
        mem.execute(
            "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,attempts,retry_identity,next_retry_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("evidence_snapshot:huge-legacy-debt", "evidence_snapshot", "retry_pending", "legacy", fixed.isoformat(),
             2607, "", None),
        )
        mem.commit()
    opens: list[Path] = []
    real_open_ro = loop.open_ro

    def counted_open_ro(path: Path, **kwargs):
        opens.append(Path(path))
        return real_open_ro(path, **kwargs)

    monkeypatch.setattr(loop, "open_ro", counted_open_ro)
    monkeypatch.setattr(loop, "_capture_pair_valid", lambda *_args, **_kwargs: pytest.fail("legacy upgrade must not validate pair"))
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda *_args, **_kwargs: pytest.fail("legacy upgrade must not fingerprint"))
    result = loop._capture_hard_evidence(cfg, [], scan_all=True)
    assert result["deferred"] == ["huge-legacy-debt"]
    assert opens == []
    with loop.memory(cfg) as mem:
        debt = mem.execute(
            "SELECT attempts,retry_identity,next_retry_at FROM controller_debt WHERE debt_id=?",
            ("evidence_snapshot:huge-legacy-debt",),
        ).fetchone()
    assert int(debt["attempts"]) == 2607
    assert str(debt["retry_identity"])
    assert loop.parse_time(str(debt["next_retry_at"])) == fixed + timedelta(seconds=300)


def test_memory_gate_runs_one_due_lane_and_still_constructs_new_incident(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only one true due lane is expensive; an incident with no debt is new work."""
    cfg["loop"].update(evidence_queue_batch_size=16, evidence_builds_per_cycle=8)
    due_ids = ["due-lane-a", "due-lane-b"]
    with loop.memory(cfg) as mem:
        for incident_id in [*due_ids, "new-lane"]:
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (incident_id, "hard", f"{incident_id}-position", f"{incident_id}-evidence", "below_floor", "yes-token", "sell_yes", .05,
                 "2026-08-22T12:00:00+00:00", 1.0, "queued", "blind", "2026-08-22T12:00:00+00:00"),
            )
        for incident_id in due_ids:
            mem.execute(
                "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,retry_identity,next_retry_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "due", "2026-08-22T12:00:00+00:00", incident_id,
                 "2000-01-01T00:00:00+00:00"),
            )
        mem.commit()
    calls: list[str] = []
    fingerprints = ("retry", "cfg", "cap", "data")
    monkeypatch.setattr(loop, "_evidence_retry_state", lambda *_args: (fingerprints, None))
    monkeypatch.setattr(loop, "_capture_pair_valid", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(loop, "_evidence_fingerprints", lambda *_args: fingerprints)
    monkeypatch.setattr(
        loop,
        "build_evidence",
        lambda _cfg, incident_id: calls.append(incident_id)
        or Path(_cfg["paths"]["runtime"]) / incident_id / "evidence.db",
    )
    due = loop._capture_hard_evidence(cfg, due_ids, scan_all=True)
    assert due["built"][0] == "new-lane"
    assert len([value for value in due["built"] if value in due_ids]) == 1
    assert calls == due["built"]

    calls.clear()
    with loop.memory(cfg) as mem:
        mem.execute("UPDATE controller_debt SET status='resolved' WHERE kind='evidence_snapshot'")
        mem.commit()
    new = loop._capture_hard_evidence(cfg, ["new-lane"])
    assert new["built"] == ["new-lane"]
    assert calls == ["new-lane"]


def test_memory_gate_ignores_raw_trade_revision_while_not_due(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw trades DB update cannot bypass a memory-owned future retry gate."""
    incident_id = "raw-trade-not-due"
    _queue_evidence_retry_incident(cfg, incident_id, "raw-trade-position")
    with loop.memory(cfg) as mem:
        incident = mem.execute(
            "SELECT incident_id,position_id,crossing_evidence_id,evidence_revision "
            "FROM incidents WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
        mem.execute(
            "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,retry_identity,next_retry_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                f"evidence_snapshot:{incident_id}",
                "evidence_snapshot",
                "retry_pending",
                "not_due",
                "2026-08-25T12:00:00+00:00",
                loop._memory_only_evidence_retry_identity(cfg, incident),
                "2099-01-01T00:00:00+00:00",
            ),
        )
        mem.commit()
    with sqlite3.connect(cfg["paths"]["trades_db"]) as trades:
        trades.execute(
            "UPDATE position_current SET updated_at=? WHERE position_id=?",
            ("2026-08-25T12:01:00+00:00", "raw-trade-position"),
        )
    opens: list[Path] = []
    real_open_ro = loop.open_ro
    monkeypatch.setattr(
        loop,
        "open_ro",
        lambda path, **kwargs: opens.append(Path(path)) or real_open_ro(path, **kwargs),
    )
    monkeypatch.setattr(
        loop, "_capture_pair_valid", lambda *_args, **_kwargs: pytest.fail("raw change must not validate")
    )
    monkeypatch.setattr(
        loop, "_evidence_fingerprints", lambda *_args, **_kwargs: pytest.fail("raw change must not fingerprint")
    )
    result = loop._capture_hard_evidence(cfg, [incident_id])
    assert result["deferred"] == [incident_id]
    assert opens == []


def test_recovery_indexes_cover_exact_ordering_and_bound_large_debt(cfg: dict) -> None:
    cfg["loop"]["evidence_queue_batch_size"] = 3
    with loop.memory(cfg) as mem:
        for index in range(80):
            incident_id = f"recovery-hard-{index:03d}"
            mem.execute(
                "INSERT INTO incidents(incident_id,kind,position_id,crossing_evidence_id,crossing_kind,"
                "held_token_id,held_direction,floor_price,detected_at,priority,status,stage,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (incident_id, "hard", f"p-{index}", f"e-{index}", "below_floor", "yes-token", "sell_yes", .05,
                 f"2026-08-22T12:{index % 60:02d}:00+00:00", float(index), "queued", "evidence", "2026-08-22T12:00:00+00:00"),
            )
            mem.execute(
                "INSERT INTO controller_debt(debt_id,kind,status,reason,updated_at,next_retry_at) VALUES (?,?,?,?,?,?)",
                (f"evidence_snapshot:{incident_id}", "evidence_snapshot", "retry_pending", "bounded", "2026-08-22T12:00:00+00:00",
                 None if index % 2 == 0 else "2026-08-22T13:00:00+00:00"),
            )
        hard_plan = mem.execute(
            "EXPLAIN QUERY PLAN SELECT incident_id FROM incidents WHERE kind='hard' "
            "ORDER BY CASE WHEN status IN ('queued','running','retry_pending') THEN 0 ELSE 1 END, "
            "priority DESC, detected_at DESC, incident_id LIMIT ?",
            (3,),
        ).fetchall()
        debt_plan = mem.execute(
            "EXPLAIN QUERY PLAN SELECT debt_id FROM controller_debt WHERE kind='evidence_snapshot' AND status='retry_pending' "
            "ORDER BY next_retry_at IS NOT NULL, next_retry_at, debt_id LIMIT ?",
            (3,),
        ).fetchall()
        mem.commit()
    hard_text = " ".join(str(value) for row in hard_plan for value in row).upper()
    debt_text = " ".join(str(value) for row in debt_plan for value in row).upper()
    assert "IDX_EVIDENCE_RECOVERY_HARD" in hard_text
    assert "IDX_EVIDENCE_RECOVERY_DEBT" in debt_text
    assert "TEMP B-TREE" not in hard_text
    assert "TEMP B-TREE" not in debt_text
    assert "SCAN CONTROLLER_DEBT" not in debt_text
    recovered, error = loop._recover_evidence_candidate_ids(cfg, limit=3)
    assert error is None
    assert len(recovered) == 3


def test_recovery_interrupt_preserves_known_candidate_with_typed_degradation(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"]["evidence_queue_batch_size"] = 2
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        loop,
        "_memory_only_evidence_due_filter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(loop.EvidenceCapacityExceeded("time_budget")),
    )
    monkeypatch.setattr(
        loop,
        "_recover_evidence_candidate_ids",
        lambda *_args, **_kwargs: ([], "OperationalError:interrupted"),
    )
    monkeypatch.setattr(
        loop,
        "_record_evidence_debt",
        lambda _cfg, incident_id, reason, **_kwargs: recorded.append((incident_id, reason)),
    )
    result = loop._capture_hard_evidence(cfg, ["known-hard"])
    assert result["deferred"] == ["known-hard"]
    assert recorded == [("known-hard", "evidence_snapshot_capacity_failure:time_budget")]
    assert result["controller_degraded"] == {
        "status": "controller_degraded",
        "reason_code": "EVIDENCE_CANDIDATE_RECOVERY_FAILED",
        "error": "OperationalError:interrupted",
    }


def test_exhausted_recovery_bounds_caller_first_debt_writes_and_records_remainder(
    cfg: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg["loop"]["evidence_queue_batch_size"] = 2
    caller_known = ["caller-0", "caller-1", "caller-2", "caller-3"]
    recorded: list[str] = []
    prior_context = {"deadline": loop.time.monotonic() + 60.0}
    monkeypatch.setattr(
        loop,
        "_recover_evidence_candidate_ids",
        lambda *_args, **_kwargs: (["recovered-0", "recovered-1"], "OperationalError:interrupted"),
    )
    monkeypatch.setattr(
        loop,
        "_memory_only_evidence_due_filter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            loop.EvidenceCapacityExceeded("time_budget")
        ),
    )
    monkeypatch.setattr(
        loop,
        "_record_evidence_debt",
        lambda _cfg, incident_id, _reason, **_kwargs: recorded.append(incident_id),
    )
    loop._EVIDENCE_BUILD_CONTEXT = prior_context
    try:
        result = loop._capture_hard_evidence(
            cfg,
            caller_known,
            budget={"deadline": loop.time.monotonic() - 1.0, "bytes": 0},
        )
    finally:
        assert loop._EVIDENCE_BUILD_CONTEXT is prior_context
        loop._EVIDENCE_BUILD_CONTEXT = None

    receipt = json.loads(
        (Path(cfg["paths"]["runtime"]) / loop._EVIDENCE_RECOVERY_REMAINDER_RECEIPT).read_text()
    )
    assert recorded == ["caller-0", "caller-1"]
    assert len(recorded) == cfg["loop"]["evidence_queue_batch_size"]
    assert receipt["kind"] == "evidence_snapshot_recovery_remainder"
    assert receipt["incident_ids"] == ["caller-2", "caller-3", "recovered-0", "recovered-1"]
    assert result["deferred"] == [*caller_known, "recovered-0", "recovered-1"]
