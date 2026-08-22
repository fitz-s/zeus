# Created: 2026-08-22
# Last reused/audited: 2026-08-22
# Authority basis: operator-requested deterministic full-loss trigger and isolated-memory investigation loop.
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "full_loss_loop", REPO_ROOT / "scripts" / "watch_full_loss_investigation.py"
)
assert SPEC and SPEC.loader
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def _db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE position_current (
            position_id TEXT, phase TEXT, market_id TEXT, city TEXT,
            target_date TEXT, bin_label TEXT, direction TEXT, unit TEXT,
            shares REAL, cost_basis_usd REAL, realized_pnl_usd REAL,
            entry_price REAL, p_posterior REAL, last_monitor_prob REAL,
            last_monitor_edge REAL, last_monitor_market_price REAL,
            last_monitor_best_bid REAL, last_monitor_best_ask REAL,
            strategy_key TEXT, chain_state TEXT, token_id TEXT,
            no_token_id TEXT, condition_id TEXT, order_id TEXT,
            settled_at TEXT, settlement_price REAL, exit_price REAL,
            exit_reason TEXT, decision_law_id TEXT, position_origin TEXT
        )"""
    )
    connection.commit()
    connection.close()


def _insert(path: Path, position_id: str, cost: float, pnl: float, settled_at: str) -> None:
    connection = sqlite3.connect(path)
    columns = [part.strip() for part in loop.LOSS_COLUMNS.split(",")]
    row = {column: None for column in columns}
    row.update(
        position_id=position_id,
        phase="settled",
        market_id=f"market-{position_id}",
        city="Test City",
        target_date="2026-08-22",
        bin_label="Test question",
        direction="buy_yes",
        unit="C",
        shares=10.0,
        cost_basis_usd=cost,
        realized_pnl_usd=pnl,
        entry_price=0.2,
        strategy_key="forecast_qkernel_entry",
        token_id=f"token-{position_id}",
        condition_id=f"condition-{position_id}",
        settled_at=settled_at,
        settlement_price=0.0,
        exit_price=0.0,
        exit_reason="SETTLEMENT",
    )
    marks = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO position_current ({','.join(columns)}) VALUES ({marks})",
        [row[column] for column in columns],
    )
    connection.commit()
    connection.close()


def _bootstrap(tmp_path: Path, db: Path) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    workspace = tmp_path / "isolated"
    result = loop.bootstrap_workspace(
        workspace,
        repo_root=repo,
        repair_worktree=None,
        lookback_hours=48,
        model="test-model",
    )
    config = result["config"]
    config["trades_db"] = str(db)
    loop.atomic_json(workspace / "runtime" / "config.json", config)
    return workspace, config


def test_detects_exact_threshold_once_and_ignores_non_loss(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    now = loop.iso_now()
    _insert(db, "loss", 10.0, -9.5, now)
    _insert(db, "not-loss", 10.0, -9.49, now)
    workspace, config = _bootstrap(tmp_path, db)

    first = loop.detect(workspace, config)
    second = loop.detect(workspace, config)

    assert len(first) == 1
    assert second == []
    incident = loop.read_json(workspace / "runtime" / "incidents" / f"{first[0]}.json", {})
    assert incident["loss"]["position_id"] == "loss"
    assert incident["status"] == "pending"


def test_activation_and_seven_day_window_are_both_enforced(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    _insert(db, "old", 10.0, -10.0, (loop.utc_now() - timedelta(days=3)).isoformat())
    workspace, config = _bootstrap(tmp_path, db)

    assert loop.detect(workspace, config) == []


def test_batch_is_deterministic_and_marks_each_incident_batched(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    _insert(db, "a", 10.0, -10.0, loop.iso_now())
    _insert(db, "b", 20.0, -20.0, loop.iso_now())
    workspace, config = _bootstrap(tmp_path, db)
    loop.detect(workspace, config)

    batch = loop.create_batch(workspace, config)

    assert batch is not None
    assert len(batch["incident_ids"]) == 2
    for ident in batch["incident_ids"]:
        row = loop.read_json(workspace / "runtime" / "incidents" / f"{ident}.json", {})
        assert row["status"] == "batched"
        assert row["batch_id"] == batch["batch_id"]


def test_dedicated_rules_and_ephemeral_prompt_forbid_memory_mixing(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    workspace, config = _bootstrap(tmp_path, db)

    rules = (workspace / "AGENTS.md").read_text()
    prompt = (workspace / "INVESTIGATOR_PROMPT.md").read_text()
    assert "Never read or write ~/.codex/memories" in rules
    assert "continuous timeline" in prompt
    assert "Never infer" in prompt
    assert "A personalized rule" in prompt
    codex_home = Path(config["codex_home"])
    assert codex_home.parent == workspace
    assert not (codex_home / "memories").exists()


def test_live_or_dirty_repair_target_degrades_to_read_only(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    config = {"repo_root": str(live), "repair_worktree": str(live)}

    repo, mode, reason = loop.repair_context(config)

    assert repo == live
    assert mode == "read_only"
    assert reason == "repair_worktree_is_live"


def test_validated_result_updates_only_dedicated_memory(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    _insert(db, "loss", 10.0, -10.0, loop.iso_now())
    workspace, config = _bootstrap(tmp_path, db)
    [ident] = loop.detect(workspace, config)
    batch = loop.create_batch(workspace, config)
    assert batch is not None
    run_dir = workspace / "runs" / batch["batch_id"]
    run_dir.mkdir(parents=True)
    result = {
        "schema_version": 1,
        "batch_id": batch["batch_id"],
        "batch_status": "investigated",
        "evidence_delta_digest": "new monitor gap evidence",
        "stop_reason": "one falsifiable next action",
        "incidents": [{
            "incident_id": ident,
            "position_id": "loss",
            "classification": "new_root",
            "root_cause_id": "monitor-gap-v1",
            "preventability": "engine_preventable",
            "confidence": 0.9,
            "uncertainty_quadrant": "known_known",
            "earliest_causal_divergence": "2026-08-22T00:00:00Z",
            "last_executable_exit": "2026-08-22T00:01:00Z bid=0.20",
            "upstream_decision_assessment": "forecast and posterior still require event-time audit",
            "causal_timeline": [],
            "evidence_refs": ["position_events:event-1"],
            "falsifier": "show a fresh monitor event",
            "repair_spec": {
                "invariant": "all active positions refresh",
                "affected_surfaces": ["monitor"],
                "antibody": "cadence relationship test",
                "proposed_change": "repair scheduler coverage",
                "redecision_behavior": "refresh on every cycle",
                "stop_and_plan": False,
            },
        }],
        "root_cause_updates": [{
            "root_cause_id": "monitor-gap-v1",
            "signature": "fresh bid without monitor",
            "causal_invariant": "every held position is revisited",
            "antibody": "cadence relationship test",
            "repair_status": "proposed",
            "fix_sha": None,
        }],
        "next_actions": [{
            "action": "add antibody",
            "owner": "repair executor",
            "evidence_gate": "test fails before fix",
            "priority": 0,
        }],
    }
    result_path = run_dir / "last_message.json"
    result_path.write_text(json.dumps(result))
    loop.atomic_json(workspace / "runtime" / "active.json", {
        "batch_id": batch["batch_id"],
        "result_path": str(result_path),
        "run_dir": str(run_dir),
    })

    assert loop.complete_batch(workspace, 0)
    incident = loop.read_json(workspace / "runtime" / "incidents" / f"{ident}.json", {})
    registry = loop.read_json(workspace / "memory" / "root_causes" / "registry.json", {})
    assert incident["status"] == "investigated"
    assert incident["root_cause_id"] == "monitor-gap-v1"
    assert registry["causes"]["monitor-gap-v1"]["occurrence_count"] == 1


def test_orphan_batch_recovers_pending_incidents(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    _insert(db, "loss", 10.0, -10.0, loop.iso_now())
    workspace, config = _bootstrap(tmp_path, db)
    [ident] = loop.detect(workspace, config)
    batch = loop.create_batch(workspace, config)
    assert batch is not None

    recovered = loop.recover_orphan_batches(workspace)

    assert recovered == [batch["batch_id"]]
    incident = loop.read_json(workspace / "runtime" / "incidents" / f"{ident}.json", {})
    assert incident["status"] == "pending"
    assert "batch_id" not in incident


def test_result_rejects_path_traversal_root_cause_id(tmp_path: Path) -> None:
    batch = {"batch_id": "batch", "incident_ids": ["incident"]}
    result = {
        "schema_version": 1,
        "batch_id": "batch",
        "incidents": [{"incident_id": "incident", "root_cause_id": "../../other-memory"}],
        "root_cause_updates": [],
    }

    try:
        loop._validate_result(result, batch)
    except ValueError as exc:
        assert "unsafe root_cause_id" in str(exc)
    else:
        raise AssertionError("unsafe root cause id was accepted")

    valid = {**result, "incidents": [{
        "incident_id": "incident",
        "root_cause_id": "RC-20260822-GLOBAL-REAUCTION-DRAIN-STALLED",
    }]}
    loop._validate_result(valid, batch)


def test_repair_tracking_requires_live_proof(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    workspace, _config = _bootstrap(tmp_path, db)
    registry_path = workspace / "memory" / "root_causes" / "registry.json"
    loop.atomic_json(registry_path, {
        "schema_version": 1,
        "causes": {"monitor-gap-v1": {"root_cause_id": "monitor-gap-v1", "occurrence_count": 2}},
    })

    try:
        loop.record_repair(
            workspace,
            root_cause_id="monitor-gap-v1",
            repair_status="live_verified",
            fix_sha="abc123",
            evidence=[],
            antibodies=[],
        )
    except ValueError as exc:
        assert "requires at least one" in str(exc)
    else:
        raise AssertionError("live verification without evidence was accepted")

    monkeypatch.setattr(
        loop,
        "loaded_fix_proof",
        lambda _workspace, fix_sha: f"loaded:{fix_sha}",
    )
    cause = loop.record_repair(
        workspace,
        root_cause_id="monitor-gap-v1",
        repair_status="live_verified",
        fix_sha="abc123",
        evidence=["deployment_freshness.boot_sha=abc123"],
        antibodies=["tests/test_monitor_gap.py"],
    )
    assert cause["repair_status"] == "live_verified"
    assert cause["repair_transitions"][-1]["fix_sha"] == "abc123"
    assert cause["repair_evidence"][0] == "loaded:abc123"


def test_output_schema_is_strict_at_every_object_boundary() -> None:
    def visit(node: object) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            if node_type == "object" or (isinstance(node_type, list) and "object" in node_type):
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(loop.OUTPUT_SCHEMA)


def test_incident_identity_survives_economic_correction() -> None:
    base = {
        "position_id": "position-1",
        "settled_at": "2026-08-22T00:00:00+00:00",
        "cost_basis_usd": 10.0,
        "realized_pnl_usd": -10.0,
    }
    corrected = {**base, "cost_basis_usd": 9.5, "realized_pnl_usd": -9.5}
    assert loop.incident_id(base) == loop.incident_id(corrected)


def test_three_identical_runner_failures_circuit_break_until_contract_changes(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    _db(db)
    workspace, config = _bootstrap(tmp_path, db)
    ident = "incident"
    batch_id = "batch"
    incident_path = workspace / "runtime" / "incidents" / f"{ident}.json"
    batch_path = workspace / "runtime" / "batches" / f"{batch_id}.json"
    run_dir = workspace / "runs" / batch_id
    run_dir.mkdir(parents=True)
    (run_dir / "codex.jsonl").write_text("same deterministic failure\n")
    loop.atomic_json(incident_path, {
        "incident_id": ident,
        "status": "batched",
        "attempts": 0,
        "loss": {"position_id": "position", "settled_at": loop.iso_now()},
    })
    loop.atomic_json(batch_path, {"batch_id": batch_id, "incident_ids": [ident], "status": "running"})

    for attempt in range(3):
        loop.atomic_json(workspace / "runtime" / "active.json", {
            "batch_id": batch_id,
            "result_path": str(run_dir / "missing.json"),
            "run_dir": str(run_dir),
        })
        assert not loop.complete_batch(workspace, 1)
        incident = loop.read_json(incident_path, {})
        if attempt < 2:
            assert incident["status"] == "pending"
            incident["status"] = "batched"
            loop.atomic_json(incident_path, incident)

    blocked = loop.read_json(incident_path, {})
    assert blocked["status"] == "runner_blocked"
    assert blocked["same_failure_count"] == 3

    config["model"] = "changed-model"
    loop.atomic_json(workspace / "runtime" / "config.json", config)
    assert loop.reset_blocked_if_contract_changed(workspace, config) == [ident]
    assert loop.read_json(incident_path, {})["status"] == "pending"


def test_daemon_identity_binds_repo_head_and_script_digest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        loop,
        "_git",
        lambda _repo, *_args: type("Result", (), {"returncode": 0, "stdout": "abc123\n"})(),
    )
    identity = loop.daemon_code_identity(tmp_path)
    assert identity["repo_sha"] == "abc123"
    assert identity["script_path"].endswith("scripts/watch_full_loss_investigation.py")
    assert len(identity["script_sha256"]) == 64
