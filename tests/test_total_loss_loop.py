# Lifecycle: created=2026-08-22; last_reviewed=2026-08-22; last_reused=2026-08-22
# Purpose: Relationship antibodies for event-time total-loss detection and evidence isolation.
# Reuse: Run whenever detector timing, exposure lifecycle, quote persistence, or Codex orchestration changes.
"""Relationship antibodies for the event-time total-loss loop."""

from __future__ import annotations

import importlib.util
import inspect
import json
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
                observed_at TEXT, local_sequence INTEGER, fill_price TEXT,
                filled_size TEXT
            );
            CREATE TABLE wallet_fill_observations (
                id INTEGER PRIMARY KEY, token_id TEXT, observed_at TEXT,
                price TEXT, size TEXT
            );
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
                "preferred_reasoning": "max",
                "fallback_reasoning": ["xhigh", "high"],
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
) -> None:
    values = (
        evidence_id, f"event-{evidence_id}", "condition-1", token, "YES",
        direction, at, f"book-{evidence_id}", bid, 0.5,
        '{"bids":[],"asks":[]}', at, 1,
    )
    with sqlite3.connect(cfg["paths"]["trades_db"]) as conn:
        conn.execute("INSERT INTO execution_feasibility_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        if latest:
            sell = "sell_no" if token == "no-token" else "sell_yes"
            latest_values = (
                token, sell, evidence_id, f"event-{evidence_id}", "condition-1",
                "YES", at, f"book-{evidence_id}", bid, 0.5,
                '{"bids":[],"asks":[]}', at, 1,
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
def _incidents(cfg: dict) -> list[dict]:
    with loop.memory(cfg) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM incidents ORDER BY detected_at")]


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


def test_hard_incident_suppresses_precursor_creation(cfg: dict) -> None:
    _position(cfg)
    _quote(cfg, "q-low", "2026-08-22T09:00:02+00:00", 0.01)

    loop.detect(cfg)

    assert [row["kind"] for row in _incidents(cfg)] == ["hard"]


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


def test_codex_command_persists_primary_and_places_approval_before_exec(cfg: dict) -> None:
    runtime = Path(cfg["paths"]["runtime"])
    runtime.mkdir(parents=True)
    loop.atomic_json(runtime / "capabilities.json", {"reasoning_effort": "max"})
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
    cfg["profiles"]["test"]["preferred_reasoning"] = "high"

    assert loop._capability_fingerprint(cfg) != before


def test_failed_repair_retry_resumes_persistent_session(cfg: dict, monkeypatch) -> None:
    monkeypatch.setattr(loop, "capabilities", lambda _cfg: {"reasoning_effort": "max"})
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
