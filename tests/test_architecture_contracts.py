from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load_yaml(path: str) -> dict:
    with open(ROOT / path) as f:
        return yaml.safe_load(f)

def test_principal_authority_files_exist():
    required = [
        "docs/architecture/zeus_durable_architecture_spec.md",
        "docs/governance/zeus_change_control_constitution.md",
        "architecture/kernel_manifest.yaml",
        "architecture/invariants.yaml",
        "architecture/zones.yaml",
        "architecture/negative_constraints.yaml",
    ]
    for rel in required:
        assert (ROOT / rel).exists(), rel

def test_strategy_key_manifest_is_frozen():
    kernel = load_yaml("architecture/kernel_manifest.yaml")
    atom = kernel["semantic_atoms"]["strategy_key"]
    assert atom["frozen"] is True
    assert atom["allowed"] == [
        "settlement_capture",
        "shoulder_sell",
        "center_buy",
        "opening_inertia",
    ]

def test_negative_constraints_include_no_local_close():
    negative = load_yaml("architecture/negative_constraints.yaml")
    ids = {item["id"] for item in negative["constraints"]}
    assert "NC-04" in ids

def test_negative_constraints_cover_strategy_fallback():
    negative = load_yaml("architecture/negative_constraints.yaml")
    ids = {item["id"] for item in negative["constraints"]}
    assert "NC-03" in ids

def test_risk_actions_exist_in_schema():
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS risk_actions" in sql
    assert "threshold_multiplier" in sql
    assert "allocation_multiplier" in sql

def test_schema_has_append_only_triggers():
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    assert "position_events is append-only" in sql

def test_zone_model_declares_k0_and_k3():
    zones = load_yaml("architecture/zones.yaml")
    assert "K0_frozen_kernel" in zones["zones"]
    assert "K3_extension" in zones["zones"]

def test_semgrep_rules_cover_core_forbidden_moves():
    text = (ROOT / "architecture/ast_rules/semgrep_zeus.yml").read_text()
    for rule_id in (
        "zeus-no-direct-close-from-engine",
        "zeus-no-memory-only-control-state",
        "zeus-no-strategy-default-fallback",
    ):
        assert rule_id in text


def _canonical_event() -> dict:
    return {
        "event_id": "evt-1",
        "position_id": "pos-1",
        "event_version": 1,
        "sequence_no": 1,
        "event_type": "POSITION_OPEN_INTENT",
        "occurred_at": "2026-04-03T00:00:00Z",
        "phase_before": None,
        "phase_after": "pending_entry",
        "strategy_key": "center_buy",
        "decision_id": "dec-1",
        "snapshot_id": "snap-1",
        "order_id": None,
        "command_id": None,
        "caused_by": None,
        "idempotency_key": "idem-1",
        "venue_status": None,
        "source_module": "test",
        "payload_json": "{}",
    }


def _canonical_projection() -> dict:
    return {
        "position_id": "pos-1",
        "phase": "pending_entry",
        "trade_id": "trade-1",
        "market_id": "mkt-1",
        "city": "NYC",
        "cluster": "US-Northeast",
        "target_date": "2026-04-01",
        "bin_label": "39-40°F",
        "direction": "buy_yes",
        "unit": "F",
        "size_usd": 10.0,
        "shares": 20.0,
        "cost_basis_usd": 10.0,
        "entry_price": 0.5,
        "p_posterior": 0.6,
        "last_monitor_prob": None,
        "last_monitor_edge": None,
        "last_monitor_market_price": None,
        "decision_snapshot_id": "snap-1",
        "entry_method": "ens_member_counting",
        "strategy_key": "center_buy",
        "edge_source": "center_buy",
        "discovery_mode": "update_reaction",
        "chain_state": "unknown",
        "order_id": None,
        "order_status": None,
        "updated_at": "2026-04-03T00:00:00Z",
    }


def test_canonical_transaction_boundary_helper_is_atomic(tmp_path):
    from src.state.db import append_event_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    event = _canonical_event()
    projection = _canonical_projection()

    append_event_and_project(conn, event, projection)

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    row = conn.execute("SELECT strategy_key, phase FROM position_current WHERE position_id = 'pos-1'").fetchone()
    assert row["strategy_key"] == "center_buy"
    assert row["phase"] == "pending_entry"

    try:
        append_event_and_project(conn, event, projection)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected duplicate event insert to fail")

    row = conn.execute("SELECT strategy_key, phase FROM position_current WHERE position_id = 'pos-1'").fetchone()
    assert row["strategy_key"] == "center_buy"
    assert row["phase"] == "pending_entry"
    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    conn.close()


def test_canonical_transaction_boundary_helper_rejects_mismatched_payloads():
    from src.state.db import append_event_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    bad_event = _canonical_event()
    bad_projection = _canonical_projection()
    bad_projection["phase"] = "active"

    try:
        append_event_and_project(conn, bad_event, bad_projection)
    except ValueError as exc:
        assert "phase mismatch" in str(exc)
    else:
        raise AssertionError("expected mismatched event/projection pair to fail")

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 0
    conn.close()


def test_append_many_and_project_is_atomic():
    from src.state.db import append_many_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    event1 = _canonical_event()
    event2 = dict(_canonical_event())
    event2["event_id"] = "evt-2"
    event2["sequence_no"] = 2
    event2["event_type"] = "ENTRY_ORDER_POSTED"
    event2["phase_before"] = "pending_entry"
    event2["phase_after"] = "active"
    event2["idempotency_key"] = "idem-2"
    projection = _canonical_projection()
    projection["phase"] = "active"

    append_many_and_project(conn, [event1, event2], projection)

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 2
    row = conn.execute("SELECT phase FROM position_current WHERE position_id = 'pos-1'").fetchone()
    assert row["phase"] == "active"
    conn.close()


def test_transaction_boundary_helper_rejects_legacy_init_schema():
    from src.state.db import append_event_and_project, init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    try:
        append_event_and_project(conn, _canonical_event(), _canonical_projection())
    except RuntimeError as exc:
        assert "canonical" in str(exc)
    else:
        raise AssertionError("expected legacy init schema to be rejected")

    conn.close()


def test_transaction_boundary_helper_rejects_incomplete_projection_payload():
    from src.state.db import append_event_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "migrations/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    projection = _canonical_projection()
    projection.pop("updated_at")

    try:
        append_event_and_project(conn, _canonical_event(), projection)
    except ValueError as exc:
        assert "projection missing fields" in str(exc)
    else:
        raise AssertionError("expected incomplete projection payload to fail")

    conn.close()


def test_db_exposes_canonical_transaction_boundary_helpers():
    text = (ROOT / "src/state/db.py").read_text()
    assert "def append_event_and_project" in text
    assert "def append_many_and_project" in text

def test_advisory_gate_workflow_freezes_verdict():
    workflow = load_yaml(".github/workflows/architecture_advisory_gates.yml")
    jobs = workflow["jobs"]
    triggers = workflow.get("on") or workflow.get(True) or {}

    assert "advisory-gate-policy" in jobs
    assert jobs["semgrep-zeus"].get("continue-on-error") is True
    assert jobs["replay-parity"].get("continue-on-error") is True

    trigger_paths = set(triggers["pull_request"]["paths"])
    assert "scripts/_yaml_bootstrap.py" in trigger_paths
    assert "work_packets/**" in trigger_paths

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_advisory_gates.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "policy verdict only; advisory jobs still require separate evidence review" in result.stdout
