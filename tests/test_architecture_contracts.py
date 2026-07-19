# Created: 2026-04-02
# Last reused/audited: 2026-05-22
# Lifecycle: created=2026-04-02; last_reviewed=2026-05-22; last_reused=2026-05-22
# Purpose: Protect architecture/schema contracts and high-sensitivity DB bootstrap invariants.
# Reuse: Audit touched assertions against architecture manifests and scoped AGENTS before extending.
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel); Wave26 canonical position event env authority
from __future__ import annotations

import json
from dataclasses import asdict
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    with open(ROOT / path) as f:
        return yaml.safe_load(f)


def test_principal_authority_files_exist():
    required = [
        "docs/authority/zeus_current_architecture.md",
        "docs/authority/zeus_change_control_constitution.md",
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


def test_pricing_semantics_guardrail_law_is_registered():
    invariants = load_yaml("architecture/invariants.yaml")
    negative = load_yaml("architecture/negative_constraints.yaml")

    invariant_ids = {item["id"] for item in invariants["invariants"]}
    negative_ids = {item["id"] for item in negative["constraints"]}
    assert {"INV-33", "INV-34", "INV-35", "INV-36"} <= invariant_ids
    assert {"NC-20", "NC-21", "NC-22", "NC-23"} <= negative_ids

    linked_invariants = {
        invariant_id
        for item in negative["constraints"]
        for invariant_id in item.get("invariants", [])
    }
    assert {"INV-33", "INV-34", "INV-35", "INV-36"} <= linked_invariants


def test_strategy_policy_tables_exist_in_schema():
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS strategy_health" in sql
    assert "execution_decay_flag" in sql
    assert "edge_compression_flag" in sql
    assert "CREATE TABLE IF NOT EXISTS risk_actions" in sql
    assert "threshold_multiplier" in sql
    assert "allocation_multiplier" in sql
    # B070: control_overrides is an event-sourced VIEW over control_overrides_history
    assert "CREATE TABLE IF NOT EXISTS control_overrides_history" in sql
    assert "CREATE VIEW IF NOT EXISTS control_overrides AS" in sql


def test_risk_actions_exist_in_schema():
    # INV-05 antibody — `architecture/invariants.yaml:55-56` cites this exact
    # test name. Prior to 2026-05-01 the citation was doc-only (the test did
    # not exist anywhere); the architect/critic/test-engineer reviews flagged
    # this as a P0 invariant with no real enforcement. Added as part of
    # ultrareview25_remediation 2026-05-01 P0-3.
    #
    # INV-05 statement: "Risk must change behavior."
    # INV-05 why: "Advisory-only risk outputs are theater."
    #
    # The antibody asserts (a) the risk_actions table exists, (b) its
    # action_type CHECK domain enumerates ONLY behavior-changing actions —
    # adding 'advisory' / 'log_only' / 'note' would violate the invariant — and
    # (c) the source domain includes 'riskguard' so runtime attribution holds.
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS risk_actions" in sql, (
        "INV-05 violation: risk_actions table missing from kernel schema"
    )

    match = re.search(
        r"action_type\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*action_type\s+IN\s*\((.+?)\)\s*\)",
        sql,
        re.DOTALL,
    )
    assert match, (
        "INV-05 violation: action_type CHECK constraint missing or unparseable; "
        "without the CHECK the domain is unbounded and 'advisory' becomes constructable"
    )
    action_types = {t.strip().strip("'\"") for t in match.group(1).split(",")}

    expected = {"gate", "allocation_multiplier", "threshold_multiplier", "exit_only"}
    assert action_types == expected, (
        f"INV-05 violation: action_type domain drift. Expected {expected}, "
        f"got {action_types}. Adding any value outside this set (in particular "
        "'advisory', 'log_only', 'note', 'warning') violates the invariant — "
        "advisory-only risk outputs are theater per architecture/invariants.yaml:52. "
        "If a new action type is genuinely behavior-changing, update both the "
        "schema and this test, AND extend tests/test_runtime_guards.py to prove "
        "the new action actually gates the executor."
    )

    assert "'riskguard'" in sql, (
        "INV-05 violation: source domain must include 'riskguard' so runtime "
        "rows can be attributed to the riskguard module"
    )
    assert "'active'" in sql, (
        "INV-05 violation: status domain must include 'active' so consumers "
        "can filter live (vs expired/revoked) actions"
    )


# ---------------------------------------------------------------------------
# INV-10 — LLM output is never authority (governance + runtime surface)
# ---------------------------------------------------------------------------

# Forbidden modules: any LLM-vendor SDK that would let `src/` make a runtime
# decision based on a model call. Grouped by vendor to make diagnostics
# readable. Update both this set AND requirements.txt simultaneously if a
# new vendor SDK family appears in the wild.
_FORBIDDEN_LLM_SDK_TOPLEVEL_MODULES = frozenset({
    # OpenAI family
    "openai",
    # Anthropic family
    "anthropic",
    # Google generative-AI family
    "google.generativeai",
    "google_generativeai",
    "vertexai",
    # Aggregators / multi-vendor
    "langchain",
    "langchain_core",
    "langchain_community",
    "litellm",
    "llamaindex",
    "llama_index",
    # Other vendors
    "cohere",
    "together",
    "mistralai",
    "groq",
    "replicate",
})


def _all_imports_in_src() -> set[tuple[str, str]]:
    """Return the set of (module_name, file_path) pairs imported under src/."""
    import ast as _ast

    found: set[tuple[str, str]] = set()
    for py_file in (ROOT / "src").rglob("*.py"):
        try:
            tree = _ast.parse(py_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = str(py_file.relative_to(ROOT))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    found.add((alias.name, rel))
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    found.add((node.module, rel))
    return found


def test_inv10_no_llm_sdk_imports_in_src():
    # INV-10 antibody — `architecture/invariants.yaml:104` claims "LLM output
    # is never authority" but cited only governance artifacts (a script + a
    # doc) with NO pytest antibody. The runtime side of this invariant is
    # encodable structurally: if `src/` imports an LLM SDK, then by
    # definition there's a code path that could let model output influence
    # a trading decision. Walk every Python file in `src/` and assert zero
    # imports from any known LLM vendor SDK family. ULTRAREVIEW25 P1-9c.
    imports = _all_imports_in_src()
    violations: list[str] = []
    for module_name, file_rel in imports:
        # Match either exact name (e.g. `import openai`) or dotted prefix
        # (e.g. `from openai.types import ChatCompletion` → module="openai.types"
        # → top-level "openai" forbidden).
        top = module_name.split(".")[0]
        if module_name in _FORBIDDEN_LLM_SDK_TOPLEVEL_MODULES:
            violations.append(f"{file_rel}: imports {module_name!r}")
        elif top in {m.split(".")[0] for m in _FORBIDDEN_LLM_SDK_TOPLEVEL_MODULES}:
            violations.append(
                f"{file_rel}: imports {module_name!r} "
                f"(top-level {top!r} is forbidden)"
            )

    assert not violations, (
        "INV-10 violation: src/ imports an LLM-vendor SDK. The runtime "
        "must not depend on model output for any trading decision; "
        "generated code is only valid after packet, gates, and evidence "
        "(architecture/invariants.yaml:107). If you genuinely need an LLM "
        "for a non-authority concern (e.g., a debug summarizer), put it in "
        "`scripts/` or `tools/`, not `src/`. Offending sites:\n  "
        + "\n  ".join(violations)
    )


def test_inv10_no_llm_sdk_in_requirements():
    # Sibling antibody: catch the dependency surface even if the import side
    # is currently clean. A new pin in requirements.txt is the canary that
    # an SDK is about to be introduced — fail loudly before the import lands.
    req_text = (ROOT / "requirements.txt").read_text()
    forbidden_pin_prefixes = sorted(
        m.split(".")[0] for m in _FORBIDDEN_LLM_SDK_TOPLEVEL_MODULES
    )
    violations = []
    for line in req_text.splitlines():
        line_clean = line.strip().lower()
        if not line_clean or line_clean.startswith("#"):
            continue
        # pkg name is everything before any version specifier or extras.
        pkg = re.split(r"[<>=!~\[]", line_clean)[0].strip()
        # Normalize hyphens/underscores; PyPI is case-insensitive.
        pkg_norm = pkg.replace("-", "_")
        for forbidden in forbidden_pin_prefixes:
            if pkg_norm == forbidden.replace("-", "_"):
                violations.append(f"  {line.strip()!r}  (pkg={pkg!r})")

    assert not violations, (
        "INV-10 violation: requirements.txt lists an LLM-vendor SDK. "
        "Even if no src/ file imports it today, the dependency surface is "
        "the canary. Remove the pin or move it to a dev-only / tools-only "
        "requirement file. Offending lines:\n" + "\n".join(violations)
    )


def test_inv10_governance_artifacts_exist():
    # Smoke-check the artifacts INV-10 cites under enforced_by.scripts and
    # enforced_by.docs. If either is silently deleted, the invariant becomes
    # documentation-only. ULTRAREVIEW25 P1-9c.
    cited_script = ROOT / "scripts/check_work_packets.py"
    cited_doc = ROOT / "architecture/self_check/zero_context_entry.md"
    assert cited_script.is_file(), (
        f"INV-10 enforced_by.scripts cites {cited_script.relative_to(ROOT)} "
        "but the file is missing. Either restore the script or update the "
        "invariants.yaml citation."
    )
    assert cited_doc.is_file(), (
        f"INV-10 enforced_by.docs cites {cited_doc.relative_to(ROOT)} "
        "but the file is missing. Either restore the doc or update the "
        "invariants.yaml citation."
    )


def test_inv03_append_only_triggers_actually_fire_at_runtime():
    # INV-03 antibody — `architecture/invariants.yaml:29-38` claims
    # "Canonical authority is append-first and projection-backed" but cited
    # only the schema file + `scripts/replay_parity.py`, with NO pytest
    # antibody. Prior to 2026-05-01 the existing
    # `test_schema_has_append_only_triggers` only grepped the SQL text for
    # the comment string `"position_events is append-only"` — it never
    # exercised the triggers. A trigger that was textually mentioned but
    # logically removed would pass that test silently. This test applies the
    # kernel schema to an in-memory DB, inserts a synthetic row into each of
    # the three documented append-only tables, and verifies UPDATE / DELETE
    # raise sqlite3.IntegrityError with the expected diagnostic. ULTRAREVIEW25
    # P1-9a (per repo_review_2026-05-01 SYNTHESIS K-A two-ring enforcement).
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    # ---- position_events ------------------------------------------------
    append_many_and_project(conn, [_canonical_event()], _canonical_projection())

    with pytest.raises(sqlite3.IntegrityError, match=r"position_events is append-only"):
        conn.execute(
            "UPDATE position_events SET event_type = 'TAMPERED' WHERE event_id = 'evt-1'"
        )
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match=r"position_events is append-only"):
        conn.execute("DELETE FROM position_events WHERE event_id = 'evt-1'")
        conn.commit()

    # ---- control_overrides_history --------------------------------------
    conn.execute(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, reason, precedence, operation, recorded_at
        ) VALUES (
            'ovr-1', 'strategy', 'center_buy', 'pause', 'true',
            'p1-9a-antibody', '2026-05-01T00:00:00Z', 'antibody insertion',
            100, 'upsert', '2026-05-01T00:00:00Z'
        )
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match=r"control_overrides_history is append-only"):
        conn.execute(
            "UPDATE control_overrides_history SET value = 'false' WHERE override_id = 'ovr-1'"
        )
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match=r"control_overrides_history is append-only"):
        conn.execute("DELETE FROM control_overrides_history WHERE override_id = 'ovr-1'")
        conn.commit()

    # ---- token_suppression_history --------------------------------------
    conn.execute(
        """
        INSERT INTO token_suppression_history (
            token_id, suppression_reason, source_module,
            created_at, updated_at, recorded_at
        ) VALUES (
            'tok-1', 'operator_quarantine_clear',
            'tests.test_architecture_contracts',
            '2026-05-01T00:00:00Z', '2026-05-01T00:00:00Z',
            '2026-05-01T00:00:00Z'
        )
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match=r"token_suppression_history is append-only"):
        conn.execute(
            "UPDATE token_suppression_history SET suppression_reason = 'TAMPERED' WHERE token_id = 'tok-1'"
        )
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match=r"token_suppression_history is append-only"):
        conn.execute("DELETE FROM token_suppression_history WHERE token_id = 'tok-1'")
        conn.commit()

    conn.close()


def test_inv03_projection_view_reflects_appended_event():
    # Pair-positive antibody for INV-03's "projection-backed" half. The
    # control_overrides VIEW must project the latest recorded_at per
    # override_id from control_overrides_history; appending a NEW row with a
    # later timestamp must shift the VIEW's reading without any explicit
    # write to the projection.
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    conn.executemany(
        """
        INSERT INTO control_overrides_history (
            override_id, target_type, target_key, action_type, value,
            issued_by, issued_at, reason, precedence, operation, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("ovr-2", "strategy", "center_buy", "pause", "true", "p1-9a-antibody",
             "2026-05-01T00:00:00Z", "first", 100, "upsert", "2026-05-01T00:00:00Z"),
            ("ovr-2", "strategy", "center_buy", "pause", "false", "p1-9a-antibody",
             "2026-05-01T01:00:00Z", "second", 100, "upsert", "2026-05-01T01:00:00Z"),
        ],
    )
    conn.commit()

    rows = conn.execute(
        "SELECT override_id, value, reason FROM control_overrides "
        "WHERE override_id = 'ovr-2'"
    ).fetchall()
    assert len(rows) == 1, (
        "INV-03: projection VIEW must collapse history to one row per override_id; "
        f"got {len(rows)} rows."
    )
    assert dict(rows[0]) == {"override_id": "ovr-2", "value": "false", "reason": "second"}, (
        "INV-03: projection VIEW must reflect the LATEST history_id; the "
        "ordering came back wrong, which means the VIEW logic drifted."
    )
    conn.close()


def test_token_suppression_table_exists_in_kernel_schema():
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS token_suppression" in sql
    assert "suppression_reason" in sql
    assert "source_module" in sql


def test_schema_has_append_only_triggers():
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
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
        "zeus-place-limit-order-gateway-only",
        "zeus-no-direct-venue-command-update",
    ):
        assert rule_id in text


def test_init_schema_creates_venue_command_tables():
    """P1.S1 (INV-28): init_schema() must create both venue_commands and
    venue_command_events tables with the required columns and indexes."""
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    # venue_commands table and columns
    vc_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(venue_commands)").fetchall()
    }
    required_vc_cols = {
        "command_id", "position_id", "decision_id", "idempotency_key",
        "intent_kind", "market_id", "token_id", "side", "size", "price",
        "venue_order_id", "state", "last_event_id", "created_at", "updated_at",
        "review_required_reason",
    }
    missing_vc = required_vc_cols - vc_cols
    assert not missing_vc, f"venue_commands missing columns: {missing_vc}"

    # venue_command_events table and columns
    vce_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(venue_command_events)").fetchall()
    }
    required_vce_cols = {
        "event_id", "command_id", "sequence_no", "event_type",
        "occurred_at", "payload_json", "state_after",
    }
    missing_vce = required_vce_cols - vce_cols
    assert not missing_vce, f"venue_command_events missing columns: {missing_vce}"

    # Key indexes exist
    indexes = {
        row[1]
        for row in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_venue_commands_position" in indexes
    assert "idx_venue_commands_state" in indexes
    assert "idx_venue_commands_decision" in indexes
    assert "idx_venue_command_events_command" in indexes
    assert "idx_venue_command_events_type" in indexes

    conn.close()


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
        "env": "live",
        "payload_json": "{}",
    }


def _canonical_projection() -> dict:
    # Derive the full key set from the canonical column tuple (None default),
    # then override load-bearing values — column additions (entry_ci_width,
    # K3 exit_retry_count/next_exit_retry_at) can never rot this fixture again.
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    payload = {column: None for column in CANONICAL_POSITION_CURRENT_COLUMNS}
    payload.update({
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
        "token_id": None,
        "no_token_id": None,
        # Fix B (2026-05-19): condition_id required for open-phase writes; supply a valid value.
        # upsert_position_current raises NullConditionIdOnOpenPhaseError for NULL on
        # pending_entry/active/day0_window/pending_exit/unknown. Tests using this projection
        # must supply a non-empty condition_id.
        "condition_id": "0xdeadbeef00000000000000000000000000000000000000000000000000000001",
        "order_id": None,
        "order_status": None,
        "updated_at": "2026-04-03T00:00:00Z",
        "temperature_metric": "high",
        # PR #351 D0b: durable authority columns are part of
        # CANONICAL_POSITION_CURRENT_COLUMNS and required by
        # require_payload_fields. NULL is valid for a non-rescue pending entry.
        "fill_authority": None,
        "recovery_authority": None,
        "chain_shares": None,
        # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
        # economics columns. NULL for non-rescue pending entries.
        "chain_avg_price": None,
        "chain_cost_basis_usd": None,
        "chain_seen_at": None,
        "chain_absence_at": None,
        # BUG #128 durable realized-P&L columns (NULL on open positions).
        "realized_pnl_usd": None,
        "exit_price": None,
        "settlement_price": None,
        "settled_at": None,
        "exit_reason": None,
        "exit_retry_count": 0,
    })
    return payload


def test_position_events_direct_insert_requires_explicit_env():
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    event = dict(_canonical_event())
    event.pop("env")
    columns = tuple(event.keys())

    with pytest.raises(
        sqlite3.IntegrityError,
        match="position_events.env is required|NOT NULL constraint failed",
    ):
        conn.execute(
            f"""
            INSERT INTO position_events ({", ".join(columns)})
            VALUES ({", ".join(["?"] * len(columns))})
            """,
            tuple(event[column] for column in columns),
        )

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def _create_execution_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_fact (
            intent_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            posted_at TEXT,
            filled_at TEXT,
            voided_at TEXT,
            submitted_price REAL,
            fill_price REAL,
            shares REAL,
            fill_quality REAL,
            latency_seconds REAL,
            venue_status TEXT,
            terminal_exec_status TEXT
        )
        """
    )
    conn.commit()


def _create_outcome_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_fact (
            position_id TEXT PRIMARY KEY,
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            entered_at TEXT,
            exited_at TEXT,
            settled_at TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            decision_snapshot_id TEXT,
            pnl REAL,
            outcome INTEGER CHECK (outcome IN (0, 1)),
            hold_duration_hours REAL,
            monitor_count INTEGER,
            chain_corrections_count INTEGER
        )
        """
    )
    conn.commit()


def test_canonical_transaction_boundary_helper_is_atomic(tmp_path):
    from src.state.db import append_many_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    event = _canonical_event()
    projection = _canonical_projection()

    append_many_and_project(conn, [event], projection)

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    row = conn.execute(
        "SELECT strategy_key, phase FROM position_current WHERE position_id = 'pos-1'"
    ).fetchone()
    assert row["strategy_key"] == "center_buy"
    assert row["phase"] == "pending_entry"

    try:
        append_many_and_project(conn, [event], projection)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected duplicate event insert to fail")

    row = conn.execute(
        "SELECT strategy_key, phase FROM position_current WHERE position_id = 'pos-1'"
    ).fetchone()
    assert row["strategy_key"] == "center_buy"
    assert row["phase"] == "pending_entry"
    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    conn.close()


def test_canonical_transaction_boundary_helper_rejects_mismatched_payloads():
    from src.state.db import append_many_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    bad_event = _canonical_event()
    bad_projection = _canonical_projection()
    bad_projection["phase"] = "active"

    try:
        append_many_and_project(conn, [bad_event], bad_projection)
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
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
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
    row = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = 'pos-1'"
    ).fetchone()
    assert row["phase"] == "active"
    conn.close()


def test_transaction_boundary_helper_rejects_legacy_init_schema():
    from src.state.db import append_many_and_project, init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    append_many_and_project(conn, [_canonical_event()], _canonical_projection())
    event_count = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
    projection_count = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[
        0
    ]

    assert event_count == 1
    assert projection_count == 1

    conn.close()


def test_init_schema_bootstraps_additive_canonical_support_tables():
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    current_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
    }
    strategy_health_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(strategy_health)").fetchall()
    }
    control_override_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(control_overrides)").fetchall()
    }
    token_suppression_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(token_suppression)").fetchall()
    }

    assert {"position_id", "phase", "strategy_key", "updated_at"}.issubset(
        current_columns
    )
    assert {
        "strategy_key",
        "as_of",
        "execution_decay_flag",
        "edge_compression_flag",
    }.issubset(strategy_health_columns)
    assert {
        "override_id",
        "target_type",
        "target_key",
        "action_type",
        "precedence",
    }.issubset(control_override_columns)
    assert {
        "token_id",
        "suppression_reason",
        "source_module",
        "created_at",
    }.issubset(token_suppression_columns)
    conn.close()


def test_init_schema_does_not_create_legacy_hourly_compatibility_surface():
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    rows = conn.execute(
        """
        SELECT name, type
        FROM sqlite_master
        WHERE name IN ('hourly_observations', 'v_evidence_hourly_observations')
        ORDER BY name
        """
    ).fetchall()

    assert rows == []
    conn.close()


def test_apply_architecture_kernel_schema_bootstraps_fresh_db():
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    apply_architecture_kernel_schema(conn)

    event_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(position_events)").fetchall()
    }
    current_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
    }

    assert {
        "event_id",
        "position_id",
        "sequence_no",
        "strategy_key",
        "payload_json",
    }.issubset(event_columns)
    assert {"position_id", "phase", "strategy_key", "updated_at"}.issubset(
        current_columns
    )

    append_many_and_project(conn, [_canonical_event()], _canonical_projection())
    event_row = conn.execute(
        "SELECT event_id, position_id, strategy_key, event_type FROM position_events"
    ).fetchone()
    projection_row = conn.execute(
        "SELECT position_id, phase, strategy_key FROM position_current WHERE position_id = 'pos-1'"
    ).fetchone()

    assert dict(event_row) == {
        "event_id": "evt-1",
        "position_id": "pos-1",
        "strategy_key": "center_buy",
        "event_type": "POSITION_OPEN_INTENT",
    }
    assert dict(projection_row) == {
        "position_id": "pos-1",
        "phase": "pending_entry",
        "strategy_key": "center_buy",
    }
    conn.close()


def test_apply_architecture_kernel_schema_bootstraps_strategy_policy_tables():
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    apply_architecture_kernel_schema(conn)

    strategy_health_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(strategy_health)").fetchall()
    }
    risk_action_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(risk_actions)").fetchall()
    }
    control_override_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(control_overrides)").fetchall()
    }
    token_suppression_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(token_suppression)").fetchall()
    }

    assert {
        "strategy_key",
        "as_of",
        "open_exposure_usd",
        "risk_level",
        "execution_decay_flag",
        "edge_compression_flag",
    }.issubset(strategy_health_columns)
    assert {
        "action_id",
        "strategy_key",
        "action_type",
        "precedence",
        "status",
    }.issubset(risk_action_columns)
    assert {
        "override_id",
        "target_type",
        "target_key",
        "action_type",
        "precedence",
    }.issubset(control_override_columns)
    assert {
        "token_id",
        "suppression_reason",
        "source_module",
        "created_at",
    }.issubset(token_suppression_columns)

    conn.close()


def test_apply_architecture_kernel_schema_has_no_runtime_callers_outside_db_or_tests():
    forbidden_hits: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if (
            rel in {"src/state/db.py", "src/state/ledger.py"}
            or rel.startswith("tests/")
            or rel.startswith(".claude/")
            or rel.startswith(".omx/")
            or rel.startswith("docs/archives/")
        ):
            continue
        if "apply_architecture_kernel_schema(" in path.read_text(errors="ignore"):
            forbidden_hits.append(rel)

    assert forbidden_hits == []


def test_transaction_boundary_helper_rejects_incomplete_projection_payload():
    from src.state.db import append_many_and_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)

    projection = _canonical_projection()
    projection.pop("updated_at")

    try:
        append_many_and_project(conn, [_canonical_event()], projection)
    except ValueError as exc:
        assert "projection missing fields" in str(exc)
    else:
        raise AssertionError("expected incomplete projection payload to fail")

    conn.close()


def test_db_exposes_canonical_transaction_boundary_helpers():
    from src.state import db as state_db
    from src.state import ledger as state_ledger
    from src.state import projection as state_projection

    assert not hasattr(state_db, "append_event_and_project")
    assert not hasattr(state_ledger, "append_event_and_project")
    assert state_db.append_many_and_project is state_ledger.append_many_and_project
    assert (
        state_db.apply_architecture_kernel_schema
        is state_ledger.apply_architecture_kernel_schema
    )
    assert (ROOT / "src/state/ledger.py").exists()
    assert (ROOT / "src/state/projection.py").exists()
    assert hasattr(state_projection, "upsert_position_current")


def test_replay_parity_reports_projection_vs_legacy_export(tmp_path):
    db_path = tmp_path / "zeus.db"
    legacy_path = tmp_path / "positions-legacy.json"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()
    conn.executescript(sql)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'pos-1', 'active', 'trade-1', 'm1', 'NYC', 'US-Northeast', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 10.0, 20.0, 10.0, 0.5, NULL, NULL, NULL, NULL,
            'snap-1', 'ens_member_counting', 'center_buy', 'center_buy', 'update_reaction',
            'unknown', NULL, NULL, '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'pos-2', 'pending_exit', 'trade-2', 'm2', 'NYC', 'US-Northeast', '2026-04-01', '41-42°F',
            'buy_yes', 'F', 12.0, 24.0, 12.0, 0.5, NULL, NULL, NULL, NULL,
            'snap-2', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'update_reaction',
            'unknown', NULL, NULL, '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.commit()
    conn.close()

    legacy_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "trade_id": "trade-1",
                        "strategy": "center_buy",
                        "state": "holding",
                    },
                    {
                        "trade_id": "legacy-only",
                        "strategy": "opening_inertia",
                        "state": "holding",
                    },
                ]
            }
        )
    )

    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "replay_parity.py"),
            "--db",
            str(db_path),
            "--legacy-export",
            str(legacy_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["status"] == "mismatch"
    assert payload["canonical"]["open_positions"] == 2
    assert payload["legacy_exports"][0]["comparison"]["missing_in_canonical"] == [
        "legacy-only"
    ]
    assert payload["legacy_exports"][0]["comparison"]["missing_in_legacy"] == [
        "trade-2"
    ]


def test_replay_parity_reports_staged_missing_tables(tmp_path):
    db_path = tmp_path / "zeus.db"
    sqlite3.connect(str(db_path)).close()

    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "replay_parity.py"),
            "--db",
            str(db_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["status"] == "staged_missing_canonical_tables"
    assert "position_events" in payload["missing_tables"]
    assert "position_current" in payload["missing_tables"]


def test_replay_parity_on_init_schema_bootstrap_advances_beyond_missing_tables(
    tmp_path,
):
    from src.state.db import init_schema

    db_path = tmp_path / "zeus.db"
    legacy_path = tmp_path / "positions-legacy.json"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.close()
    legacy_path.write_text(json.dumps({"positions": []}))

    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "replay_parity.py"),
            "--db",
            str(db_path),
            "--legacy-export",
            str(legacy_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["status"] == "ok"
    assert payload["canonical"]["open_positions"] == 0


def test_init_schema_creates_legacy_and_canonical_event_tables_side_by_side():
    """P9: position_events_legacy deleted. Only canonical position_events remains."""
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "position_events" in tables
    assert "position_events_legacy" not in tables

    canonical_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(position_events)").fetchall()
    }
    assert {"event_id", "position_id", "sequence_no", "payload_json"}.issubset(
        canonical_columns
    )
    conn.close()


def test_db_no_longer_owns_canonical_append_project_bodies():
    text = (ROOT / "src/state/db.py").read_text()
    assert "from src.state.ledger import (" in text
    assert "def append_event_and_project(" not in text
    assert "def append_many_and_project(" not in text
    assert "def apply_architecture_kernel_schema(" not in text


def _strip_canonical_schema(conn):
    """Remove canonical tables to simulate a legacy-only DB."""
    conn.execute("DROP TABLE IF EXISTS position_current")
    conn.execute("DROP TABLE IF EXISTS position_events")
    conn.commit()


def _runtime_position(
    *,
    state: str = "pending_tracked",
    exit_state: str = "",
    chain_state: str = "local_only",
):
    from src.state.portfolio import Position

    return Position(
        trade_id="rt-pos-1",
        market_id="mkt-1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-03",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.5,
        p_posterior=0.6,
        edge=0.1,
        shares=20.0,
        cost_basis_usd=10.0,
        entered_at="2026-04-03T00:05:00Z" if state != "pending_tracked" else "",
        day0_entered_at="2026-04-03T00:06:00Z" if state == "day0_window" else "",
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        state=state,
        order_id="ord-1",
        order_status="filled" if state != "pending_tracked" else "pending",
        order_posted_at="2026-04-03T00:00:00Z",
        chain_state=chain_state,
        exit_state=exit_state,
        # Fix B (2026-05-19): condition_id required for open-phase writes.
        # upsert_position_current raises NullConditionIdOnOpenPhaseError for NULL on
        # pending_entry/active/day0_window/pending_exit/unknown phases.
        condition_id="0xdeadbeef00000000000000000000000000000000000000000000000000000001",
    )


def _install_minimal_venue_commands_lookup_table(conn: sqlite3.Connection) -> None:
    """Install the columns chain reconciliation probes in canonical-kernel fixtures."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS venue_commands (
            venue_order_id TEXT,
            intent_kind TEXT
        )
        """
    )


def test_lifecycle_builders_map_runtime_states_to_canonical_phases():
    from src.engine.lifecycle_events import canonical_phase_for_position

    assert (
        canonical_phase_for_position(_runtime_position(state="pending_tracked"))
        == "pending_entry"
    )
    assert (
        canonical_phase_for_position(
            _runtime_position(state="entered", chain_state="unknown")
        )
        == "active"
    )
    assert (
        canonical_phase_for_position(
            _runtime_position(state="holding", chain_state="synced")
        )
        == "active"
    )
    assert (
        canonical_phase_for_position(
            _runtime_position(state="day0_window", chain_state="synced")
        )
        == "day0_window"
    )
    assert (
        canonical_phase_for_position(
            _runtime_position(state="pending_exit", exit_state="sell_pending")
        )
        == "pending_exit"
    )
    assert (
        canonical_phase_for_position(
            _runtime_position(
                state="holding",
                exit_state="sell_pending",
                chain_state="exit_pending_missing",
            )
        )
        == "pending_exit"
    )
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' is
    # retired from LifecycleState/ChainState. This used to also assert that
    # a legacy row carrying state/chain_state='quarantined' (or
    # chain_state='quarantine_expired') folded to 'active' via the
    # mixed-epoch load bridge (Position.__post_init__'s
    # _normalize_runtime_lifecycle_state / _normalize_runtime_chain_state).
    # BRIDGE RETIREMENT (post-T5-migration cleanup): the T5 schema migration
    # has run, the DB CHECK no longer admits those literals, and the bridge
    # has been deleted — Position(state="quarantined", ...) now raises
    # ValueError at construction instead of remapping, so that scenario is no
    # longer constructible and these assertions are retired. The dispute is
    # tracked by an open ReviewWorkItem, not the phase.
    assert canonical_phase_for_position(_runtime_position(state="voided")) == "voided"
    assert (
        canonical_phase_for_position(_runtime_position(state="economically_closed"))
        == "economically_closed"
    )
    assert canonical_phase_for_position(_runtime_position(state="settled")) == "settled"
    assert (
        canonical_phase_for_position(_runtime_position(state="admin_closed"))
        == "admin_closed"
    )


def test_inv07_lifecycle_grammar_sql_python_consistency():
    # INV-07 antibody — `architecture/invariants.yaml:66-74` claims
    # "Lifecycle grammar is finite and authoritative" but cited only the
    # schema file + a semgrep rule, with NO pytest antibody linking the
    # Python enum to the SQL CHECK clauses. If a future PR adds a new phase
    # to LifecyclePhase but forgets the kernel SQL (or vice versa), every
    # row using the new phase fails IntegrityError at insert time and the
    # error surfaces only on the offending row — i.e., a partial outage.
    # This antibody parses the SQL CHECK clauses directly, compares to the
    # Python enum (minus the UNKNOWN sentinel which is in-flight only and
    # never stored), and fails loudly on any drift. ULTRAREVIEW25 P1-9b.
    from src.state.lifecycle_manager import LifecyclePhase

    sql = (ROOT / "architecture/2026_04_02_architecture_kernel.sql").read_text()

    # The kernel uses the same phase set in three CHECK clauses:
    # position_events.phase_before, position_events.phase_after, and
    # position_current.phase. All three must be identical.
    check_blocks = re.findall(
        r"phase(?:_before|_after)?\s+TEXT\s+(?:NOT\s+NULL\s+)?CHECK\s*\(\s*phase(?:_before|_after)?\s*(?:IS\s+NULL\s+OR\s+phase(?:_before|_after)?\s+)?IN\s*\((.+?)\)\s*\)",
        sql,
        re.DOTALL,
    )
    assert len(check_blocks) >= 3, (
        f"INV-07 schema parse failure: expected 3 phase CHECK clauses (before/after/current), "
        f"found {len(check_blocks)}. Did the schema layout change? Update this regex."
    )

    phase_sets = [
        frozenset(p.strip().strip("'\"") for p in block.split(",") if p.strip())
        for block in check_blocks
    ]
    canonical_sql_phases = phase_sets[0]
    for i, ps in enumerate(phase_sets[1:], start=2):
        assert ps == canonical_sql_phases, (
            f"INV-07 internal SQL drift: phase CHECK block #{i} differs from #1.\n"
            f"  block #1: {sorted(canonical_sql_phases)}\n"
            f"  block #{i}: {sorted(ps)}\n"
            f"All three phase CHECK clauses (phase_before, phase_after, "
            f"position_current.phase) must enumerate the SAME set."
        )

    python_phases = {p.value for p in LifecyclePhase} - {"unknown"}
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' used
    # to be retired from LifecyclePhase while the kernel SQL CHECK clauses
    # still permitted the legacy literal (mixed-epoch bridge window, pending
    # the schema migration). BRIDGE RETIREMENT (post-T5-migration cleanup):
    # the T5 schema migration has run — the kernel SQL CHECK clauses no
    # longer admit 'quarantined' either, so SQL and Python are directly
    # comparable again with no carve-out.
    assert python_phases == canonical_sql_phases, (
        "INV-07 schema-vs-code drift: the kernel SQL phase CHECK clauses and "
        "the Python LifecyclePhase enum disagree.\n"
        f"  in SQL only:    {sorted(canonical_sql_phases - python_phases)}\n"
        f"  in Python only: {sorted(python_phases - canonical_sql_phases)}\n"
        "If you added a new phase, update both sites in lockstep. If you "
        "removed one, audit existing rows AND every consumer of the enum "
        "value before removing from this comparison. The 'unknown' sentinel "
        "is intentionally excluded from the SQL set (in-flight only)."
    )


def test_inv07_fold_rejects_invented_phase_strings():
    # INV-07 sibling antibody: `fold_lifecycle_phase` must refuse phase
    # strings that aren't in the LifecyclePhase enum. Existing test
    # `test_lifecycle_phase_kernel_rejects_illegal_fold` covers
    # known-phase-to-known-phase illegal transitions (e.g., settled→active)
    # but NOT "agent invents a new phase string". The fatal_misread #10
    # ("phase strings invented outside enum") corresponds to exactly this
    # gap. ULTRAREVIEW25 P1-9b.
    from src.state.lifecycle_manager import fold_lifecycle_phase

    with pytest.raises((ValueError, KeyError)):
        fold_lifecycle_phase("active", "MADE_UP_PHASE_2026")
    with pytest.raises((ValueError, KeyError)):
        fold_lifecycle_phase("CREATIVE_PHASE", "active")


def test_lifecycle_phase_kernel_exposes_exact_p5_vocabulary():
    from src.state.lifecycle_manager import LIFECYCLE_PHASE_VOCABULARY

    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' is
    # retired from LifecyclePhase (no writer mints it going forward). The T5
    # schema migration has run and the kernel SQL CHECK clauses no longer
    # admit the legacy literal either (see
    # test_inv07_lifecycle_grammar_sql_python_consistency).
    assert LIFECYCLE_PHASE_VOCABULARY == (
        "pending_entry",
        "active",
        "day0_window",
        "pending_exit",
        "economically_closed",
        "settled",
        "voided",
        "admin_closed",
        "unknown",
    )


def test_lifecycle_phase_kernel_accepts_current_canonical_builder_folds():
    from src.state.lifecycle_manager import fold_lifecycle_phase

    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): folds targeting
    # 'quarantined' are removed — QUARANTINED is retired from LifecyclePhase
    # and no builder mints it going forward (REPLACEMENT PHASE LAW: a
    # position keeps its TRUE phase; the dispute lives in a ReviewWorkItem).
    allowed = [
        (None, "pending_entry"),
        ("pending_entry", "pending_entry"),
        ("pending_entry", "active"),
        ("pending_entry", "day0_window"),
        ("active", "active"),
        ("active", "settled"),
        ("day0_window", "day0_window"),
        ("day0_window", "settled"),
        ("pending_exit", "pending_exit"),
        ("pending_exit", "settled"),
        ("economically_closed", "economically_closed"),
        ("economically_closed", "settled"),
        ("settled", "settled"),
        ("voided", "voided"),
        ("admin_closed", "admin_closed"),
    ]

    for phase_before, phase_after in allowed:
        assert fold_lifecycle_phase(phase_before, phase_after).value == phase_after


def test_lifecycle_phase_kernel_rejects_illegal_fold():
    from src.state.lifecycle_manager import fold_lifecycle_phase

    with pytest.raises(ValueError, match="illegal lifecycle phase fold"):
        fold_lifecycle_phase("settled", "active")


def test_entry_builder_emits_pending_entry_batch_and_projection():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.lifecycle_manager import LifecyclePhase

    events, projection = build_entry_canonical_write(
        _runtime_position(state="pending_tracked"),
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )

    assert [event["event_type"] for event in events] == [
        "POSITION_OPEN_INTENT",
        "ENTRY_ORDER_POSTED",
    ]
    assert events[0]["phase_after"] == "pending_entry"
    assert events[1]["phase_before"] == "pending_entry"
    assert events[1]["order_id"] == "ord-1"
    assert projection["phase"] == "pending_entry"
    assert projection["order_status"] == "pending"


def test_entry_builder_emits_filled_batch_and_projection_that_append_cleanly():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import (
        append_many_and_project,
        apply_architecture_kernel_schema,
    )
    from src.state.lifecycle_manager import LifecyclePhase

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    events, projection = build_entry_canonical_write(
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )

    assert [event["event_type"] for event in events] == [
        "POSITION_OPEN_INTENT",
        "ENTRY_ORDER_POSTED",
        "ENTRY_ORDER_FILLED",
    ]
    assert events[-1]["phase_after"] == "active"
    assert projection["phase"] == "active"

    append_many_and_project(conn, events, projection)
    row = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    projection_row = conn.execute(
        "SELECT phase, strategy_key, order_status FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert [(r["event_type"], r["sequence_no"]) for r in row] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
    ]
    assert dict(projection_row) == {
        "phase": "active",
        "strategy_key": "center_buy",
        "order_status": "filled",
    }


def test_position_current_projection_persists_token_identity():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import (
        append_many_and_project,
        apply_architecture_kernel_schema,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    from src.state.lifecycle_manager import LifecyclePhase
    pos = _runtime_position(state="entered", chain_state="unknown")
    pos.token_id = "yes-token-canonical"
    pos.no_token_id = "no-token-canonical"
    pos.condition_id = "condition-canonical"

    events, projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-token",
        source_module="src.engine.cycle_runtime",
    )
    assert projection["token_id"] == "yes-token-canonical"
    assert projection["no_token_id"] == "no-token-canonical"
    assert projection["condition_id"] == "condition-canonical"

    append_many_and_project(conn, events, projection)
    row = conn.execute(
        """
        SELECT token_id, no_token_id, condition_id
        FROM position_current
        WHERE position_id = 'rt-pos-1'
        """
    ).fetchone()

    assert dict(row) == {
        "token_id": "yes-token-canonical",
        "no_token_id": "no-token-canonical",
        "condition_id": "condition-canonical",
    }


def test_kernel_schema_adds_token_identity_columns_to_existing_position_current():
    from src.state.ledger import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            trade_id TEXT,
            market_id TEXT,
            city TEXT,
            cluster TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            unit TEXT,
            size_usd REAL,
            shares REAL,
            cost_basis_usd REAL,
            entry_price REAL,
            p_posterior REAL,
            last_monitor_prob REAL,
            last_monitor_edge REAL,
            last_monitor_market_price REAL,
            decision_snapshot_id TEXT,
            entry_method TEXT,
            strategy_key TEXT NOT NULL,
            edge_source TEXT,
            discovery_mode TEXT,
            chain_state TEXT,
            order_id TEXT,
            order_status TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )

    apply_architecture_kernel_schema(conn)
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
    }

    assert {"token_id", "no_token_id", "condition_id"}.issubset(columns)
    conn.close()


def test_kernel_schema_migrates_existing_token_suppression_reason_check():
    from src.state.ledger import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE token_suppression (
            token_id TEXT PRIMARY KEY,
            condition_id TEXT,
            suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                'operator_quarantine_clear',
                'settled_position'
            )),
            source_module TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "resolved-token",
            "cond-resolved",
            "operator_quarantine_clear",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
            "{}",
        ),
    )

    apply_architecture_kernel_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "chain-only-token",
            "cond-chain",
            "chain_only_quarantined",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
            "{}",
        ),
    )
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "auto-resolved-token",
            "cond-auto-resolved",
            "chain_only_auto_resolved_match",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
            "{}",
        ),
    )
    rows = conn.execute(
        "SELECT token_id, suppression_reason FROM token_suppression ORDER BY token_id"
    ).fetchall()

    assert [dict(row) for row in rows] == [
        {
            "token_id": "auto-resolved-token",
            "suppression_reason": "chain_only_auto_resolved_match",
        },
        {"token_id": "chain-only-token", "suppression_reason": "chain_only_quarantined"},
        {"token_id": "resolved-token", "suppression_reason": "operator_quarantine_clear"},
    ]
    conn.close()


def test_b071_migration_upgrades_history_alias_without_losing_audit_metadata():
    from scripts.migrate_b071_token_suppression_to_history import migrate

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE token_suppression_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            condition_id TEXT,
            suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                'operator_quarantine_clear', 'chain_only_quarantined', 'settled_position'
            )),
            source_module TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            operation TEXT NOT NULL DEFAULT 'record' CHECK (operation IN ('record', 'migrated')),
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX idx_token_suppression_history_id_time
            ON token_suppression_history(token_id, history_id DESC);
        CREATE TRIGGER token_suppression_history_no_update
        BEFORE UPDATE ON token_suppression_history
        BEGIN SELECT RAISE(ABORT, 'token_suppression_history is append-only'); END;
        CREATE TRIGGER token_suppression_history_no_delete
        BEFORE DELETE ON token_suppression_history
        BEGIN SELECT RAISE(ABORT, 'token_suppression_history is append-only'); END;
        CREATE VIEW token_suppression_current AS
        SELECT token_id, condition_id, suppression_reason, source_module,
               created_at, updated_at, evidence_json
        FROM token_suppression_history h1
        WHERE history_id = (
            SELECT MAX(history_id) FROM token_suppression_history h2
            WHERE h2.token_id = h1.token_id
        );
        CREATE VIEW token_suppression AS
        SELECT token_id, condition_id, suppression_reason, source_module,
               created_at, updated_at, evidence_json
        FROM token_suppression_current;
        """
    )
    preserved = [
        (7, "tok-a", "cond-a", "chain_only_quarantined", "old", "2026-04-01", "2026-04-02", "{\"a\": 1}", "migrated", "2026-04-03"),
        (11, "tok-a", "cond-a", "operator_quarantine_clear", "new", "2026-04-04", "2026-04-05", "{\"a\": 2}", "record", "2026-04-06"),
    ]
    conn.executemany(
        """
        INSERT INTO token_suppression_history (
            history_id, token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json, operation, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        preserved,
    )
    conn.commit()

    # B071 must compose with a caller-owned transaction: a later failure can
    # roll its schema rebuild back without losing the original audit surface.
    conn.execute("BEGIN")
    assert migrate(conn, apply=True, drop_legacy=False)["rows_migrated"] == 0
    conn.rollback()
    assert "chain_only_auto_resolved_match" not in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'token_suppression_history'"
    ).fetchone()[0]
    assert [tuple(row) for row in conn.execute(
        "SELECT history_id, token_id, condition_id, suppression_reason, source_module, "
        "created_at, updated_at, evidence_json, operation, recorded_at "
        "FROM token_suppression_history ORDER BY history_id"
    )] == preserved
    assert {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
    } == {"token_suppression", "token_suppression_current"}

    assert migrate(conn, apply=True, drop_legacy=False)["rows_migrated"] == 0
    rows = conn.execute(
        """
        SELECT history_id, token_id, condition_id, suppression_reason, source_module,
               created_at, updated_at, evidence_json, operation, recorded_at
        FROM token_suppression_history ORDER BY history_id
        """
    ).fetchall()
    assert [tuple(row) for row in rows] == preserved
    assert "chain_only_auto_resolved_match" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'token_suppression_history'"
    ).fetchone()[0]
    assert {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    } == {"token_suppression_history_no_update", "token_suppression_history_no_delete"}
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_token_suppression_history_id_time'"
    ).fetchone() is not None
    assert {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
    } == {"token_suppression", "token_suppression_current"}
    assert conn.execute(
        "SELECT suppression_reason FROM token_suppression WHERE token_id = 'tok-a'"
    ).fetchone()["suppression_reason"] == "operator_quarantine_clear"
    conn.execute(
        """
        INSERT INTO token_suppression_history (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json, operation, recorded_at
        ) VALUES ('tok-auto', '', 'chain_only_auto_resolved_match', 'test',
                  '2026-04-07', '2026-04-07', '{}', 'record', '2026-04-07')
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE token_suppression_history SET source_module = 'mutated'")

    assert migrate(conn, apply=True, drop_legacy=False)["rows_migrated"] == 0
    assert [row["history_id"] for row in conn.execute(
        "SELECT history_id FROM token_suppression_history ORDER BY history_id"
    )] == [7, 11, 12]
    conn.close()


def test_b071_migration_upgrades_legacy_table_then_cuts_over_to_alias_view(monkeypatch):
    from scripts.migrate_b071_token_suppression_to_history import migrate

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE token_suppression (
            token_id TEXT PRIMARY KEY,
            condition_id TEXT,
            suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
                'operator_quarantine_clear', 'chain_only_quarantined', 'settled_position'
            )),
            source_module TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO token_suppression VALUES (
            'legacy-token', 'legacy-condition', 'chain_only_quarantined', 'legacy',
            '2026-04-01', '2026-04-02', '{}'
        );
        """
    )

    first = migrate(conn, apply=True, drop_legacy=False)
    assert first["rows_migrated"] == 1
    assert conn.execute(
        "SELECT type FROM sqlite_master WHERE name = 'token_suppression'"
    ).fetchone()["type"] == "table"
    assert conn.execute(
        "SELECT operation, recorded_at FROM token_suppression_history WHERE token_id = 'legacy-token'"
    ).fetchone()["operation"] == "migrated"
    assert "chain_only_auto_resolved_match" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'token_suppression'"
    ).fetchone()["sql"]

    monkeypatch.setenv("ZEUS_DESTRUCTIVE_CONFIRMED", "1")
    second = migrate(conn, apply=True, drop_legacy=True)
    assert second["rows_migrated"] == 0
    assert second["drop_performed"] is True
    assert conn.execute(
        "SELECT type FROM sqlite_master WHERE name = 'token_suppression'"
    ).fetchone()["type"] == "view"
    assert conn.execute(
        "SELECT token_id FROM token_suppression WHERE token_id = 'legacy-token'"
    ).fetchone()["token_id"] == "legacy-token"
    conn.close()


def test_settlement_builder_emits_settled_event_and_projection_that_append_cleanly():
    from src.engine.lifecycle_events import (
        build_entry_canonical_write,
        build_settlement_canonical_write,
    )
    from src.state.ledger import (
        append_many_and_project,
        apply_architecture_kernel_schema,
    )
    from src.state.lifecycle_manager import LifecyclePhase

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    entry_events, entry_projection = build_entry_canonical_write(
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    settled_pos = _runtime_position(state="settled", chain_state="synced")
    settled_pos.last_exit_at = "2026-04-03T01:00:00Z"
    settled_pos.exit_price = 1.0
    settled_pos.pnl = 10.0
    settled_pos.exit_reason = "SETTLEMENT"

    settlement_events, settlement_projection = build_settlement_canonical_write(
        settled_pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=4,
        phase_before="active",
        source_module="src.execution.harvester",
    )

    append_many_and_project(conn, settlement_events, settlement_projection)

    event_row = conn.execute(
        "SELECT event_type, sequence_no, phase_before, phase_after, payload_json FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    projection_row = conn.execute(
        "SELECT phase, strategy_key FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert event_row["event_type"] == "SETTLED"
    assert event_row["sequence_no"] == 4
    assert event_row["phase_before"] == "active"
    assert event_row["phase_after"] == "settled"
    payload = json.loads(event_row["payload_json"])
    assert payload["contract_version"] == "position_settled.v1"
    assert payload["winning_bin"] == "39-40°F"
    assert payload["outcome"] == 1
    assert payload["exit_reason"] == "SETTLEMENT"
    assert dict(projection_row) == {
        "phase": "settled",
        "strategy_key": "center_buy",
    }
    conn.close()


def test_economic_close_builder_emits_event_and_projection_that_append_cleanly():
    from src.engine.lifecycle_events import (
        build_economic_close_canonical_write,
        build_entry_canonical_write,
    )
    from src.state.ledger import (
        append_many_and_project,
        apply_architecture_kernel_schema,
    )
    from src.state.lifecycle_manager import LifecyclePhase

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    entry_events, entry_projection = build_entry_canonical_write(
        _runtime_position(state="day0_window", chain_state="unknown"),
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    closed_pos = _runtime_position(state="economically_closed", chain_state="unknown")
    closed_pos.day0_entered_at = "2026-04-03T00:06:00Z"
    closed_pos.last_exit_at = "2026-04-03T02:00:00Z"
    closed_pos.exit_price = 0.46
    closed_pos.pnl = 1.5
    closed_pos.exit_reason = "forward edge failed"
    closed_pos.exit_state = "sell_filled"

    events, projection = build_economic_close_canonical_write(
        closed_pos,
        sequence_no=4,
        phase_before="pending_exit",
        source_module="src.execution.exit_lifecycle",
    )

    append_many_and_project(conn, events, projection)

    event_row = conn.execute(
        "SELECT event_type, sequence_no, phase_before, phase_after, payload_json FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    projection_row = conn.execute(
        "SELECT phase, strategy_key FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert event_row["event_type"] == "EXIT_ORDER_FILLED"
    assert event_row["sequence_no"] == 4
    assert event_row["phase_before"] == "pending_exit"
    assert event_row["phase_after"] == "economically_closed"
    payload = json.loads(event_row["payload_json"])
    assert payload["exit_price"] == pytest.approx(0.46)
    assert payload["pnl"] == pytest.approx(1.5)
    assert payload["exit_reason"] == "forward edge failed"
    assert dict(projection_row) == {
        "phase": "economically_closed",
        "strategy_key": "center_buy",
    }
    conn.close()


def test_settlement_builder_accepts_pending_exit_fold():
    from src.engine.lifecycle_events import build_settlement_canonical_write

    settled_pos = _runtime_position(state="settled", chain_state="synced")
    settled_pos.last_exit_at = "2026-04-03T01:00:00Z"
    settled_pos.exit_price = 1.0
    settled_pos.pnl = 10.0
    settled_pos.exit_reason = "SETTLEMENT"

    events, projection = build_settlement_canonical_write(
        settled_pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=4,
        phase_before="pending_exit",
        source_module="src.execution.harvester",
    )

    assert events[0]["phase_before"] == "pending_exit"
    assert events[0]["phase_after"] == "settled"
    assert projection["phase"] == "settled"


def test_reconciliation_rescue_builder_emits_chain_synced_event_and_projection_that_append_cleanly():
    from src.engine.lifecycle_events import (
        build_entry_canonical_write,
        build_reconciliation_rescue_canonical_write,
    )
    from src.state.ledger import (
        append_many_and_project,
        apply_architecture_kernel_schema,
    )
    from src.state.lifecycle_manager import LifecyclePhase

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    _install_minimal_venue_commands_lookup_table(conn)

    pending_pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pending_pos.entry_order_id = "ord-1"
    pending_pos.order_id = "ord-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pending_pos,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    rescued_pos = _runtime_position(state="entered", chain_state="synced")
    rescued_pos.entry_order_id = "ord-1"
    rescued_pos.order_id = "ord-1"
    rescued_pos.condition_id = "cond-1"
    rescued_pos.entry_fill_verified = True
    rescued_pos.entered_at = "2026-04-03T00:10:00Z"

    rescue_events, rescue_projection = build_reconciliation_rescue_canonical_write(
        rescued_pos,
        chain_synced_at="2026-04-03T00:10:00Z",
        sequence_no=3,
        source_module="src.state.chain_reconciliation",
    )
    append_many_and_project(conn, rescue_events, rescue_projection)

    event_row = conn.execute(
        "SELECT event_type, sequence_no, phase_before, phase_after, payload_json FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    projection_row = conn.execute(
        "SELECT phase, strategy_key, chain_state, order_status FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert event_row["event_type"] == "CHAIN_SYNCED"
    assert event_row["sequence_no"] == 3
    assert event_row["phase_before"] == "pending_entry"
    assert event_row["phase_after"] == "active"
    payload = json.loads(event_row["payload_json"])
    assert payload["reason"] == "pending_fill_rescued"
    assert payload["entry_fill_verified"] is True
    assert payload["condition_id"] == "cond-1"
    assert payload["from_state"] == "pending_tracked"
    assert payload["to_state"] == "entered"
    assert payload["rescue_condition_id"] == "cond-1"
    assert payload["historical_entry_method"] == "ens_member_counting"
    assert payload["historical_selected_method"] == "ens_member_counting"
    assert payload["applied_validations"] == []
    assert dict(projection_row) == {
        "phase": "active",
        "strategy_key": "center_buy",
        "chain_state": "synced",
        "order_status": "filled",
    }
    conn.close()


def test_reconciliation_rescue_builder_preserves_legacy_rescue_provenance_fields():
    from src.engine.lifecycle_events import build_reconciliation_rescue_canonical_write

    rescued_pos = _runtime_position(state="entered", chain_state="synced")
    rescued_pos.entry_order_id = "ord-1"
    rescued_pos.order_id = "ord-1"
    rescued_pos.condition_id = "cond-1"
    rescued_pos.entry_fill_verified = True
    rescued_pos.entered_at = "2026-04-03T00:10:00Z"
    rescued_pos.applied_validations = ["spread_ok", "kelly_ok"]

    events, projection = build_reconciliation_rescue_canonical_write(
        rescued_pos,
        chain_synced_at="2026-04-03T00:10:00Z",
        sequence_no=3,
        source_module="src.state.chain_reconciliation",
    )

    payload = json.loads(events[0]["payload_json"])
    assert payload == {
        "status": "entered",
        "source": "chain_reconciliation",
        "reason": "pending_fill_rescued",
        "from_state": "pending_tracked",
        "to_state": "entered",
        "entry_order_id": "ord-1",
        "entry_method": "ens_member_counting",
        "selected_method": "ens_member_counting",
        "historical_entry_method": "ens_member_counting",
        "historical_selected_method": "ens_member_counting",
        "applied_validations": ["spread_ok", "kelly_ok"],
        "entry_fill_verified": True,
        "shares": 20.0,
        "cost_basis_usd": 10.0,
        "size_usd": 10.0,
        "condition_id": "cond-1",
        "rescue_condition_id": "cond-1",
        "order_status": "filled",
        "chain_state": "synced",
    }
    assert projection["phase"] == "active"


def test_chain_size_corrected_builder_emits_chain_size_corrected_event_and_projection_that_append_cleanly():
    from src.engine.lifecycle_events import (
        build_chain_size_corrected_canonical_write,
        build_entry_canonical_write,
    )
    from src.state.ledger import (
        append_many_and_project,
        apply_architecture_kernel_schema,
    )
    from src.state.lifecycle_manager import LifecyclePhase

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="synced")
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    pos.chain_verified_at = "2026-04-03T00:20:00Z"
    pos.chain_shares = 22.0
    pos.shares = 22.0
    pos.cost_basis_usd = 11.0
    pos.size_usd = 11.0
    pos.condition_id = "cond-1"

    events, projection = build_chain_size_corrected_canonical_write(
        pos,
        local_shares_before=20.0,
        sequence_no=4,
        source_module="src.state.chain_reconciliation",
        phase_after=LifecyclePhase.ACTIVE.value,
    )
    append_many_and_project(conn, events, projection)

    event_row = conn.execute(
        "SELECT event_type, sequence_no, phase_before, phase_after, payload_json FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(event_row["payload_json"])
    projection_row = conn.execute(
        "SELECT phase, shares, cost_basis_usd, size_usd FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert event_row["event_type"] == "CHAIN_SIZE_CORRECTED"
    assert event_row["sequence_no"] == 4
    assert event_row["phase_before"] == "active"
    assert event_row["phase_after"] == "active"
    assert payload["reason"] == "chain_size_corrected"
    assert payload["local_shares_before"] == 20.0
    assert payload["chain_shares_after"] == 22.0
    assert dict(projection_row) == {
        "phase": "active",
        "shares": 22.0,
        "cost_basis_usd": 11.0,
        "size_usd": 11.0,
    }
    conn.close()


# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): build_chain_quarantined_canonical_write
# was dead (zero production callers — the only chain-only writer path is the
# typed ChainOnlyFact in src.state.chain_reconciliation) and is deleted along
# with the retired PositionPhase.QUARANTINED / LifecycleState.QUARANTINED /
# ChainState.{QUARANTINED,QUARANTINE_EXPIRED,ENTRY_AUTHORITY_QUARANTINED}
# members. The tests that exercised it are removed with it.
    assert projection["strategy_key"] == "center_buy"


def test_lifecycle_builder_module_exists():
    text = (ROOT / "src/engine/lifecycle_events.py").read_text()
    assert "def canonical_phase_for_position" in text
    assert "def build_position_current_projection" in text
    assert "def build_entry_canonical_write" in text
    assert "def build_economic_close_canonical_write" in text
    assert "def build_settlement_canonical_write" in text
    assert "def build_reconciliation_rescue_canonical_write" in text


def test_log_trade_entry_degrades_cleanly_on_canonical_bootstrap_db():
    from src.state.db import apply_architecture_kernel_schema, log_trade_entry

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    log_trade_entry(conn, _runtime_position(state="entered", chain_state="unknown"))

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 0
    conn.close()


def test_log_execution_report_degrades_cleanly_on_canonical_bootstrap_db():
    from src.state.db import apply_architecture_kernel_schema, log_execution_report

    class _Result:
        status = "filled"
        reason = None
        submitted_price = 0.5
        fill_price = 0.5
        shares = 20.0
        timeout_seconds = None
        filled_at = "2026-04-03T00:05:00Z"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    _create_execution_fact_table(conn)

    log_execution_report(
        conn, _runtime_position(state="entered", chain_state="unknown"), _Result()
    )

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    fact_row = conn.execute(
        """
        SELECT position_id, order_role, fill_price, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-pos-1:entry'
        """
    ).fetchone()
    assert dict(fact_row) == {
        "position_id": "rt-pos-1",
        "order_role": "entry",
        "fill_price": 0.5,
        "terminal_exec_status": "filled",
    }
    conn.close()


def test_log_trade_entry_still_fails_loudly_on_malformed_legacy_position_events_schema():
    from src.state.db import log_trade_entry

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL
        );
        """
    )

    try:
        log_trade_entry(conn, _runtime_position(state="entered", chain_state="unknown"))
    except RuntimeError as exc:
        assert "legacy runtime position_events schema not installed" in str(exc)
    else:
        raise AssertionError("expected malformed legacy schema to fail loudly")

    conn.close()


def test_log_execution_report_still_fails_loudly_on_malformed_legacy_position_events_schema():
    from src.state.db import log_execution_report

    class _Result:
        status = "filled"
        reason = None
        submitted_price = 0.5
        fill_price = 0.5
        shares = 20.0
        timeout_seconds = None
        filled_at = "2026-04-03T00:05:00Z"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL
        );
        """
    )

    try:
        log_execution_report(
            conn, _runtime_position(state="entered", chain_state="unknown"), _Result()
        )
    except RuntimeError as exc:
        assert "legacy runtime position_events schema not installed" in str(exc)
    else:
        raise AssertionError("expected malformed legacy schema to fail loudly")

    conn.close()


def test_entry_telemetry_sequence_degrades_cleanly_on_canonical_bootstrap_db():
    from src.state.db import (
        apply_architecture_kernel_schema,
        log_execution_report,
        log_trade_entry,
    )

    class _Result:
        status = "filled"
        reason = None
        submitted_price = 0.5
        fill_price = 0.5
        shares = 20.0
        timeout_seconds = None
        filled_at = "2026-04-03T00:05:00Z"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="unknown")
    log_trade_entry(conn, pos)
    log_execution_report(conn, pos, _Result())

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 0
    conn.close()


def test_log_settlement_event_degrades_cleanly_on_canonical_bootstrap_db():
    from src.state.db import apply_architecture_kernel_schema, log_settlement_event

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    _create_outcome_fact_table(conn)

    pos = _runtime_position(state="settled", chain_state="synced")
    pos.last_exit_at = "2026-04-03T01:00:00Z"
    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    outcome_row = conn.execute(
        """
        SELECT position_id, strategy_key, settled_at, outcome
        FROM outcome_fact
        WHERE position_id = 'rt-pos-1'
        """
    ).fetchone()
    assert dict(outcome_row) == {
        "position_id": "rt-pos-1",
        "strategy_key": "center_buy",
        "settled_at": "2026-04-03T01:00:00Z",
        "outcome": 1,
    }
    conn.close()


def test_log_settlement_event_still_fails_loudly_on_malformed_legacy_position_events_schema():
    from src.state.db import log_settlement_event

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL
        );
        """
    )

    pos = _runtime_position(state="settled", chain_state="synced")
    pos.last_exit_at = "2026-04-03T01:00:00Z"

    try:
        log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    except RuntimeError as exc:
        assert "legacy runtime position_events schema not installed" in str(exc)
    else:
        raise AssertionError("expected malformed legacy schema to fail loudly")

    conn.close()


def test_log_reconciled_entry_event_degrades_cleanly_on_canonical_bootstrap_db():
    from src.state.db import (
        apply_architecture_kernel_schema,
        log_reconciled_entry_event,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="synced")
    log_reconciled_entry_event(
        conn,
        pos,
        timestamp="2026-04-03T00:10:00Z",
        details={"reason": "pending_fill_rescued"},
    )

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def test_log_reconciled_entry_event_still_fails_loudly_on_malformed_legacy_position_events_schema():
    from src.state.db import log_reconciled_entry_event

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE position_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL
        );
        """
    )

    try:
        log_reconciled_entry_event(
            conn,
            _runtime_position(state="entered", chain_state="synced"),
            timestamp="2026-04-03T00:10:00Z",
            details={"reason": "pending_fill_rescued"},
        )
    except RuntimeError as exc:
        assert "legacy runtime position_events schema not installed" in str(exc)
    else:
        raise AssertionError("expected malformed legacy schema to fail loudly")

    conn.close()


def test_log_reconciled_entry_event_still_fails_loudly_on_hybrid_drift_schema():
    from src.state.db import (
        apply_architecture_kernel_schema,
        log_reconciled_entry_event,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    conn.executescript(
        """
        ALTER TABLE position_events ADD COLUMN runtime_trade_id TEXT;
        ALTER TABLE position_events ADD COLUMN position_state TEXT;
        ALTER TABLE position_events ADD COLUMN strategy TEXT;
        ALTER TABLE position_events ADD COLUMN source TEXT;
        ALTER TABLE position_events ADD COLUMN details_json TEXT;
        ALTER TABLE position_events ADD COLUMN timestamp TEXT;
        """
    )

    try:
        log_reconciled_entry_event(
            conn,
            _runtime_position(state="entered", chain_state="synced"),
            timestamp="2026-04-03T00:10:00Z",
            details={"reason": "pending_fill_rescued"},
        )
    except Exception:
        pass
    else:
        raise AssertionError("expected hybrid drift schema to fail loudly")

    conn.close()


def test_reconciliation_pending_fill_path_degrades_cleanly_on_canonical_bootstrap_after_query_compat():
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import apply_architecture_kernel_schema
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pos.entry_order_id = "ord-1"
    pos.order_id = "ord-1"
    pos.condition_id = "cond-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]
    # match buy_yes token
    portfolio.positions[0].token_id = ""
    portfolio.positions[0].no_token_id = ""
    portfolio.positions[0].token_id = "tok-1"
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["rescued_pending"] == 0
    assert stats["skipped_pending_missing_canonical_baseline"] == 1
    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    assert portfolio.positions[0].state.value == "pending_tracked"
    conn.close()


def test_reconciliation_pending_fill_path_writes_canonical_rows_when_prior_history_exists():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    _install_minimal_venue_commands_lookup_table(conn)

    pending_pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pending_pos.entry_order_id = "ord-1"
    pending_pos.order_id = "ord-1"
    pending_pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pending_pos,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    # PR #352 (Part-3): PR #351's D0 split rescue into trade-verified (CHAIN_SYNCED)
    # vs balance-only (VENUE_POSITION_OBSERVED), discriminated by a linked venue
    # trade fact. This test asserts the trade-verified path, so it must provide
    # the fill fact the discriminator (_pending_entry_has_linked_fill_fact) reads
    # from venue_trade_facts — without it the rescue is balance-only.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS venue_trade_facts (
            venue_order_id TEXT, state TEXT, source TEXT,
            filled_size REAL, fill_price REAL,
            observed_at TEXT, local_sequence INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO venue_trade_facts (venue_order_id, state, source, filled_size, "
        "fill_price, observed_at, local_sequence) VALUES "
        "('ord-1', 'CONFIRMED', 'REST', 20.0, 0.5, '2026-04-03T00:00:00Z', 1)"
    )

    portfolio = PortfolioState(positions=[pending_pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["rescued_pending"] == 1
    event_rows = conn.execute(
        "SELECT event_type, sequence_no, phase_before, phase_after FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    projection_row = conn.execute(
        "SELECT phase, strategy_key, chain_state, order_status FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert [(row["event_type"], row["sequence_no"]) for row in event_rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("CHAIN_SYNCED", 3),
    ]
    assert event_rows[-1]["phase_before"] == "pending_entry"
    assert event_rows[-1]["phase_after"] == "active"
    assert dict(projection_row) == {
        "phase": "active",
        "strategy_key": "center_buy",
        "chain_state": "synced",
        "order_status": "filled",
    }
    conn.close()


def test_reconciliation_pending_fill_path_preserves_legacy_behavior_on_legacy_db():
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import init_schema, query_position_events
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pos.entry_order_id = "ord-1"
    pos.order_id = "ord-1"
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["rescued_pending"] == 1
    events = query_position_events(conn, "rt-pos-1")
    assert events == []
    conn.close()


def test_reconciliation_pending_fill_dual_write_failure_after_legacy_steps_is_explicit(
    monkeypatch,
):
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    _install_minimal_venue_commands_lookup_table(conn)

    pending_pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pending_pos.entry_order_id = "ord-1"
    pending_pos.order_id = "ord-1"
    pending_pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pending_pos,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pending_pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    def _boom(*args, **kwargs):
        raise RuntimeError("append-failed")

    monkeypatch.setattr("src.state.db.append_many_and_project", _boom)

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "canonical reconciliation rescue dual-write failed" in str(exc)
    else:
        raise AssertionError(
            "expected canonical reconciliation rescue failure to surface explicitly"
        )

    event_rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    assert [(row["event_type"], row["sequence_no"]) for row in event_rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
    ]
    assert portfolio.positions[0].state.value == "pending_tracked"
    conn.close()


def test_reconciliation_pending_fill_path_legacy_sync_failure_is_explicit_before_in_memory_mutation(
    monkeypatch,
):
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import init_schema, query_position_events
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pos.entry_order_id = "ord-1"
    pos.order_id = "ord-1"
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    def _boom(*args, **kwargs):
        raise RuntimeError("legacy-sync-failed")

    monkeypatch.setattr("src.state.db.update_trade_lifecycle", _boom)

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["rescued_pending"] == 1
    assert getattr(portfolio.positions[0].state, "value", portfolio.positions[0].state) == "entered"
    assert query_position_events(conn, "rt-pos-1") == []
    conn.close()


def test_reconciliation_pending_fill_path_legacy_event_failure_is_explicit_before_in_memory_mutation(
    monkeypatch,
):
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import init_schema, query_position_events
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pos.entry_order_id = "ord-1"
    pos.order_id = "ord-1"
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    monkeypatch.setattr(
        "src.state.db.update_trade_lifecycle", lambda *args, **kwargs: None
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("legacy-event-failed")

    monkeypatch.setattr("src.state.db.log_reconciled_entry_event", _boom)

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["rescued_pending"] == 1
    assert getattr(portfolio.positions[0].state, "value", portfolio.positions[0].state) == "entered"
    assert query_position_events(conn, "rt-pos-1") == []
    conn.close()


def test_reconciliation_pending_fill_path_still_fails_loudly_on_hybrid_drift_schema():
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import apply_architecture_kernel_schema
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    conn.executescript(
        """
        ALTER TABLE position_events ADD COLUMN runtime_trade_id TEXT;
        ALTER TABLE position_events ADD COLUMN position_state TEXT;
        ALTER TABLE position_events ADD COLUMN strategy TEXT;
        ALTER TABLE position_events ADD COLUMN source TEXT;
        ALTER TABLE position_events ADD COLUMN details_json TEXT;
        ALTER TABLE position_events ADD COLUMN timestamp TEXT;
        """
    )

    pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pos.entry_order_id = "ord-1"
    pos.order_id = "ord-1"
    pos.condition_id = "cond-1"
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "hybrid position_events schema" in str(exc)
    else:
        raise AssertionError("expected hybrid drift reconciliation path to fail loudly")

    conn.close()


def test_reconciliation_size_correction_path_writes_canonical_rows_when_prior_history_exists():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="unknown")
    pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=22.0,
            avg_price=0.44,
            cost=11.0,
            condition_id="cond-1",
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["updated"] == 1
    rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    projection_row = conn.execute(
        "SELECT phase, shares, cost_basis_usd, size_usd FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()
    assert [(row["event_type"], row["sequence_no"]) for row in rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
        ("CHAIN_SIZE_CORRECTED", 4),
    ]
    assert dict(projection_row) == {
        "phase": "active",
        "shares": 22.0,
        "cost_basis_usd": 11.0,
        "size_usd": 11.0,
    }
    assert portfolio.positions[0].shares == 22.0
    conn.close()


def test_reconciliation_size_correction_uses_canonical_current_to_avoid_repeated_correction_storm():
    from src.engine.lifecycle_events import (
        build_chain_size_corrected_canonical_write,
        build_entry_canonical_write,
    )
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    stale_pos = _runtime_position(state="entered", chain_state="unknown")
    stale_pos.token_id = "tok-1"
    stale_pos.shares = 20.0
    entry_events, entry_projection = build_entry_canonical_write(
        stale_pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    corrected_pos = _runtime_position(state="entered", chain_state="synced")
    corrected_pos.token_id = "tok-1"
    corrected_pos.shares = 22.0
    corrected_pos.chain_shares = 22.0
    corrected_pos.chain_verified_at = "2026-04-03T00:20:00Z"
    corrected_pos.cost_basis_usd = 11.0
    corrected_pos.size_usd = 11.0
    corrected_events, corrected_projection = build_chain_size_corrected_canonical_write(
        corrected_pos,
        local_shares_before=20.0,
        sequence_no=4,
        source_module="src.state.chain_reconciliation",
        phase_after=LifecyclePhase.ACTIVE.value,
    )
    append_many_and_project(conn, corrected_events, corrected_projection)

    portfolio = PortfolioState(positions=[stale_pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=22.0,
            avg_price=0.44,
            cost=11.0,
            condition_id="cond-1",
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    projection_row = conn.execute(
        "SELECT phase, shares, cost_basis_usd, size_usd FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()
    assert stats["updated"] == 0
    assert [(row["event_type"], row["sequence_no"]) for row in rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
        ("CHAIN_SIZE_CORRECTED", 4),
    ]
    assert dict(projection_row) == {
        "phase": "active",
        "shares": 22.0,
        "cost_basis_usd": 11.0,
        "size_usd": 11.0,
    }
    assert portfolio.positions[0].shares == 22.0
    conn.close()


def test_reconciliation_restores_synced_state_when_chain_economics_already_match():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="local_only")
    pos.token_id = "tok-1"
    pos.shares = 5.48
    pos.cost_basis_usd = 4.0552
    pos.size_usd = 4.0552
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.execute(
        """
        UPDATE position_current
           SET chain_shares = 5.48,
               chain_avg_price = 0.74,
               chain_cost_basis_usd = 4.0552,
               chain_seen_at = '2026-06-19T11:04:24+00:00',
               chain_state = 'local_only'
         WHERE position_id = 'rt-pos-1'
        """
    )

    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=5.48,
            avg_price=0.74,
            cost=4.0552,
            condition_id="cond-1",
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    row = conn.execute(
        """
        SELECT chain_state, chain_shares, chain_avg_price, chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'rt-pos-1'
        """
    ).fetchone()
    event = conn.execute(
        """
        SELECT event_type, payload_json
          FROM position_events
         WHERE position_id = 'rt-pos-1'
         ORDER BY sequence_no DESC LIMIT 1
        """
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert stats.get("chain_observation_persisted") == 1
    assert row["chain_state"] == "synced"
    assert row["chain_shares"] == pytest.approx(5.48)
    assert row["chain_avg_price"] == pytest.approx(0.74)
    assert row["chain_cost_basis_usd"] == pytest.approx(4.0552)
    assert event["event_type"] == "CHAIN_SIZE_CORRECTED"
    assert payload["reason"] == "chain_economics_observed"
    conn.close()


def test_reconciliation_size_correction_path_preserves_legacy_behavior_on_legacy_db():
    """P0b (2026-07-04): a size mismatch with no pre-existing position_current
    row (legacy/pre-canonical position) is no longer quarantined — chain size
    is truth, applied in-memory, with no durable row to correct yet (see
    docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 follow-up)."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import init_schema, query_position_events
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    pos = _runtime_position(state="entered", chain_state="unknown")
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=22.0,
            avg_price=0.44,
            cost=11.0,
            condition_id="cond-1",
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    assert stats["updated"] == 0
    assert stats["skipped_size_correction_missing_canonical_baseline"] == 1
    assert portfolio.positions[0].shares == 22.0
    assert getattr(portfolio.positions[0].state, "value", portfolio.positions[0].state) == "entered"
    assert query_position_events(conn, "rt-pos-1") == []
    conn.close()


def test_reconciliation_size_correction_no_baseline_persists_durable_review():
    """P0b (2026-07-04): an unresolved size mismatch with NO canonical baseline
    at all (no position_current row yet for this position_id) is no longer
    quarantined nor tagged size_mismatch_unresolved — that invented durable
    state is retired. Chain size is truth in-memory
    (corrected.shares == chain.size); there is no durable row to correct, so
    the chain-mirror-shaped writer legitimately no-ops (returns False) and no
    event is persisted for a row that was never canonically created."""
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import apply_architecture_kernel_schema
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="unknown")
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=22.0,
            avg_price=0.44,
            cost=11.0,
            condition_id="cond-1",
        )
    ]

    stats = reconcile(portfolio, chain_positions, conn=conn)

    # Size correction itself still skipped (no canonical baseline to correct).
    assert stats["updated"] == 0
    assert stats["skipped_size_correction_missing_canonical_baseline"] == 1
    assert portfolio.positions[0].shares == 22.0
    event_types = [
        r[0] for r in conn.execute(
            "SELECT event_type FROM position_events WHERE position_id='rt-pos-1'"
        ).fetchall()
    ]
    assert event_types == []
    pc = conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id='rt-pos-1'"
    ).fetchone()
    assert pc is None, "no canonical row existed before reconcile; none is fabricated by it"
    conn.close()


def test_reconciliation_size_correction_hybrid_drift_fails_before_new_canonical_rows():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    pos = _runtime_position(state="entered", chain_state="unknown")
    pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.executescript(
        """
        ALTER TABLE position_events ADD COLUMN runtime_trade_id TEXT;
        ALTER TABLE position_events ADD COLUMN position_state TEXT;
        ALTER TABLE position_events ADD COLUMN strategy TEXT;
        ALTER TABLE position_events ADD COLUMN source TEXT;
        ALTER TABLE position_events ADD COLUMN details_json TEXT;
        ALTER TABLE position_events ADD COLUMN timestamp TEXT;
        """
    )

    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=22.0,
            avg_price=0.44,
            cost=11.0,
            condition_id="cond-1",
        )
    ]

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "hybrid position_events schema" in str(exc)
    else:
        raise AssertionError(
            "expected hybrid drift size-correction path to fail loudly"
        )

    rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    assert [(row["event_type"], row["sequence_no"]) for row in rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
    ]
    assert portfolio.positions[0].shares == 20.0
    conn.close()


def test_reconciliation_size_correction_failure_is_explicit_before_in_memory_mutation(
    monkeypatch,
):
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    pos = _runtime_position(state="entered", chain_state="unknown")
    pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1",
            size=22.0,
            avg_price=0.44,
            cost=11.0,
            condition_id="cond-1",
        )
    ]

    def _boom(*args, **kwargs):
        raise RuntimeError("append-failed")

    monkeypatch.setattr("src.state.db.append_many_and_project", _boom)

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "canonical reconciliation size-correction dual-write failed" in str(exc)
    else:
        raise AssertionError(
            "expected size-correction dual-write failure to surface explicitly"
        )

    rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    assert [(row["event_type"], row["sequence_no"]) for row in rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
    ]
    assert portfolio.positions[0].shares == 20.0
    conn.close()


def test_reconciliation_pending_fill_path_hybrid_drift_fails_before_new_canonical_rows():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    pending_pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pending_pos.entry_order_id = "ord-1"
    pending_pos.order_id = "ord-1"
    pending_pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pending_pos,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.executescript(
        """
        ALTER TABLE position_events ADD COLUMN runtime_trade_id TEXT;
        ALTER TABLE position_events ADD COLUMN position_state TEXT;
        ALTER TABLE position_events ADD COLUMN strategy TEXT;
        ALTER TABLE position_events ADD COLUMN source TEXT;
        ALTER TABLE position_events ADD COLUMN details_json TEXT;
        ALTER TABLE position_events ADD COLUMN timestamp TEXT;
        """
    )

    portfolio = PortfolioState(positions=[pending_pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "hybrid position_events schema" in str(exc)
    else:
        raise AssertionError("expected hybrid drift reconciliation path to fail loudly")

    rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    assert [(row["event_type"], row["sequence_no"]) for row in rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
    ]
    conn.close()


def test_reconciliation_pending_fill_path_fails_loudly_when_canonical_projection_missing():
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import apply_architecture_kernel_schema
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, decision_id, snapshot_id, order_id,
            command_id, caused_by, idempotency_key, venue_status, source_module, env, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt-missing-projection",
            "rt-pos-1",
            1,
            1,
            "POSITION_OPEN_INTENT",
            "2026-04-03T00:00:00Z",
            None,
            "pending_entry",
            "center_buy",
            "dec-1",
            "snap-1",
            None,
            None,
            None,
            "idem-missing-projection",
            None,
            "test",
            "live",
            "{}",
        ),
    )

    pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pos.entry_order_id = "ord-1"
    pos.order_id = "ord-1"
    pos.token_id = "tok-1"
    portfolio = PortfolioState(positions=[pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "missing current projection" in str(exc)
    else:
        raise AssertionError(
            "expected missing canonical projection baseline to fail loudly"
        )

    conn.close()


def test_reconciliation_pending_fill_path_fails_loudly_when_canonical_projection_phase_mismatches():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import append_many_and_project, apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    pending_pos = _runtime_position(state="pending_tracked", chain_state="local_only")
    pending_pos.entry_order_id = "ord-1"
    pending_pos.order_id = "ord-1"
    pending_pos.token_id = "tok-1"
    entry_events, entry_projection = build_entry_canonical_write(
        pending_pos,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    conn.execute(
        "UPDATE position_current SET phase = 'day0_window' WHERE position_id = 'rt-pos-1'"
    )

    portfolio = PortfolioState(positions=[pending_pos])
    chain_positions = [
        ChainPosition(
            token_id="tok-1", size=20.0, avg_price=0.5, cost=10.0, condition_id="cond-1"
        )
    ]

    try:
        reconcile(portfolio, chain_positions, conn=conn)
    except RuntimeError as exc:
        assert "phase mismatch" in str(exc)
    else:
        raise AssertionError("expected phase mismatch baseline to fail loudly")

    conn.close()


def test_chronicler_log_event_degrades_cleanly_on_canonical_bootstrap_db():
    from src.state.chronicler import log_event
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    log_event(conn, "SETTLEMENT", "trade-1", {"ok": True})

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def test_chronicler_log_event_still_fails_loudly_when_chronicle_missing_outside_canonical_bootstrap():
    from src.state.chronicler import log_event

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    try:
        log_event(conn, "SETTLEMENT", "trade-1", {"ok": True})
    except sqlite3.OperationalError as exc:
        assert "chronicle" in str(exc).lower()
    else:
        raise AssertionError("expected missing chronicle table to fail loudly")

    conn.close()


def test_chronicler_log_event_still_fails_loudly_on_hybrid_drift_schema_without_chronicle():
    from src.state.chronicler import log_event
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    conn.executescript(
        """
        ALTER TABLE position_events ADD COLUMN runtime_trade_id TEXT;
        ALTER TABLE position_events ADD COLUMN position_state TEXT;
        ALTER TABLE position_events ADD COLUMN strategy TEXT;
        ALTER TABLE position_events ADD COLUMN source TEXT;
        ALTER TABLE position_events ADD COLUMN details_json TEXT;
        ALTER TABLE position_events ADD COLUMN timestamp TEXT;
        """
    )

    try:
        log_event(conn, "SETTLEMENT", "trade-1", {"ok": True})
    except RuntimeError as exc:
        assert "hybrid position_events schema" in str(exc)
    else:
        raise AssertionError("expected hybrid drift schema to fail loudly")

    conn.close()


def test_harvester_settlement_path_writes_canonical_rows_on_canonical_bootstrap_after_p1_6d():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.execution.harvester import _settle_positions
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="synced")
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    event_rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    projection_row = conn.execute(
        "SELECT phase, strategy_key FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert [(row["event_type"], row["sequence_no"]) for row in event_rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
        ("SETTLED", 4),
    ]
    assert dict(projection_row) == {
        "phase": "settled",
        "strategy_key": "center_buy",
    }
    conn.close()


def test_harvester_settlement_path_skips_canonical_write_without_prior_canonical_history():
    from src.execution.harvester import _settle_positions
    from src.state.db import apply_architecture_kernel_schema
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="synced")
    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 0
    conn.close()


def test_harvester_settlement_path_preserves_legacy_behavior_on_legacy_db():
    from src.execution.harvester import _settle_positions
    from src.state.db import init_schema, query_position_events
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    pos = _runtime_position(state="entered", chain_state="synced")
    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    events = query_position_events(conn, "rt-pos-1")
    assert events == []
    conn.close()


def test_harvester_settlement_dual_write_failure_after_legacy_steps_is_explicit(
    monkeypatch,
):
    from src.execution.harvester import _settle_positions
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="entered", chain_state="synced")
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pos])

    def _boom(*args, **kwargs):
        raise RuntimeError("append-failed")

    monkeypatch.setattr("src.state.db.append_many_and_project", _boom)

    try:
        _settle_positions(
            conn,
            portfolio,
            city="NYC",
            target_date="2026-04-03",
            winning_label="39-40°F",
            settlement_records=[],
            strategy_tracker=None,
        )
    except RuntimeError as exc:
        assert "canonical settlement dual-write failed" in str(exc)
    else:
        raise AssertionError(
            "expected canonical settlement dual-write failure to surface explicitly"
        )

    event_rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    assert [(row["event_type"], row["sequence_no"]) for row in event_rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
    ]
    conn.close()


def test_harvester_settlement_path_uses_day0_window_as_phase_before_when_applicable():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.execution.harvester import _settle_positions
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="day0_window", chain_state="synced")
    pos.day0_entered_at = "2026-04-03T00:06:00Z"
    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        phase_after=LifecyclePhase.DAY0_WINDOW.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    event_row = conn.execute(
        "SELECT phase_before, phase_after FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    assert dict(event_row) == {
        "phase_before": "day0_window",
        "phase_after": "settled",
    }
    conn.close()


def test_harvester_settlement_path_uses_economically_closed_phase_before_when_applicable():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.execution.harvester import _settle_positions
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="economically_closed", chain_state="synced")
    pos.exit_price = 0.46
    pos.exit_reason = "forward edge failed"
    pos.pnl = 1.5
    pos.last_exit_at = "2026-04-03T00:30:00Z"
    entry_events, entry_projection = build_entry_canonical_write(
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    event_row = conn.execute(
        "SELECT phase_before, phase_after, payload_json FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(event_row["payload_json"])
    assert {
        "phase_before": event_row["phase_before"],
        "phase_after": event_row["phase_after"],
    } == {
        "phase_before": "economically_closed",
        "phase_after": "settled",
    }
    assert payload["exit_reason"] == "SETTLEMENT"
    conn.close()


def test_harvester_snapshot_source_logging_degrades_cleanly_on_canonical_bootstrap_after_chronicle_compat():
    from src.execution.harvester import _log_snapshot_context_resolution
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    _log_snapshot_context_resolution(
        conn,
        city="NYC",
        target_date="2026-04-03",
        snapshot_contexts=[
            {
                "decision_snapshot_id": "snap-1",
                "source": "position_events",
                "authority_level": "durable_event",
                "is_degraded": False,
                "degraded_reason": "",
                "learning_snapshot_ready": True,
            }
        ],
        dropped_rows=[],
    )

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def test_architecture_kernel_bootstrap_installs_settlement_command_readiness():
    from src.state.db import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    apply_architecture_kernel_schema(conn)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "settlement_commands",
        "settlement_command_events",
        "settlement_schema_migrations",
    }.issubset(tables)
    conn.close()


def test_harvester_settlement_path_settles_pending_exit_residual_exposure():
    from src.execution.harvester import _settle_positions
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="pending_exit", chain_state="exit_pending_missing")
    pos.exit_state = "sell_pending"
    pos.exit_reason = "forward edge failed"
    pos.token_id = "tok-pending-settled"
    entry_events, entry_projection = build_entry_canonical_write(
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    assert portfolio.positions == []
    event_row = conn.execute(
        "SELECT phase_before, phase_after FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    assert dict(event_row) == {
        "phase_before": "pending_exit",
        "phase_after": "settled",
    }
    conn.close()


def test_harvester_settlement_path_allows_backoff_exhausted_positions_to_settle():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.execution.harvester import _settle_positions
    from src.state.db import apply_architecture_kernel_schema, append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    pos = _runtime_position(state="pending_exit", chain_state="exit_pending_missing")
    pos.exit_state = "backoff_exhausted"
    pos.exit_reason = "forward edge failed"
    pos.exit_price = 0.46
    pos.pnl = 1.5
    pos.token_id = "tok-settled"
    entry_events, entry_projection = build_entry_canonical_write(
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        source_module="src.engine.cycle_runtime",
    )
    append_many_and_project(conn, entry_events, entry_projection)
    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40°F",
        settlement_records=[],
        strategy_tracker=None,
    )

    assert settled == 1
    event_row = conn.execute(
        "SELECT phase_before, phase_after FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no DESC LIMIT 1"
    ).fetchone()
    assert dict(event_row) == {
        "phase_before": "pending_exit",
        "phase_after": "settled",
    }
    suppression_row = conn.execute(
        """
        SELECT suppression_reason, source_module
        FROM token_suppression
        WHERE token_id = 'tok-settled'
        """
    ).fetchone()
    assert dict(suppression_row) == {
        "suppression_reason": "settled_position",
        "source_module": "src.execution.harvester",
    }
    conn.close()


def test_harvester_settlement_skips_stale_in_memory_pos_when_position_current_shows_settled():
    """P6 u2014 settlement iterator from position_current.

    When the in-memory portfolio shows a position as economically_closed but
    position_current already shows phase=settled, _settle_positions must NOT
    write a new SETTLED event or decrement the portfolio.
    """
    from src.execution.harvester import _settle_positions
    from src.state.db import apply_architecture_kernel_schema
    from src.state.portfolio import PortfolioState

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    # Stale in-memory position: snapshot says economically_closed
    pos = _runtime_position(state="economically_closed", chain_state="synced")
    pos.exit_price = 0.92
    pos.exit_reason = "forward edge failed"
    pos.pnl = 4.2
    pos.last_exit_at = "2026-04-03T01:00:00Z"

    # position_current (authoritative) already shows this position as settled
    conn.execute(
        """INSERT INTO position_current
           (position_id, trade_id, city, target_date, bin_label, direction,
            size_usd, entry_price, p_posterior, strategy_key, phase, updated_at, temperature_metric)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("rt-pos-1", "rt-pos-1", "NYC", "2026-04-03", "39-40\u00b0F", "buy_yes",
         10.0, 0.5, 0.6, "center_buy", "settled", "2026-04-03T01:00:00Z", "high"),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])

    settled = _settle_positions(
        conn,
        portfolio,
        city="NYC",
        target_date="2026-04-03",
        winning_label="39-40\u00b0F",
        settlement_records=[],
        strategy_tracker=None,
    )

    # P6 guard: stale in-memory economically_closed must NOT produce a second settlement
    assert settled == 0, "position_current phase=settled must prevent re-settlement"
    # The in-memory position was NOT removed from the portfolio
    assert len(portfolio.positions) == 1, "portfolio must be unchanged when settlement skipped"
    # No canonical SETTLED event written
    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def test_cycle_runtime_entry_dual_write_helper_skips_when_canonical_schema_absent():
    from src.engine.cycle_runtime import _dual_write_canonical_entry_if_available
    from src.state.db import init_schema
    from src.state.lifecycle_manager import LifecyclePhase

    class _Logger:
        def debug(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

    class _Deps:
        logger = _Logger()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    # Drop canonical tables to simulate legacy-only DB
    conn.execute("DROP TABLE IF EXISTS position_current")
    conn.execute("DROP TABLE IF EXISTS position_events")
    conn.commit()

    wrote = _dual_write_canonical_entry_if_available(
        conn,
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        deps=_Deps(),
    )

    assert wrote is False
    # Canonical tables were dropped; verify no canonical table was recreated
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='position_events'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_cycle_runtime_entry_dual_write_helper_appends_canonical_batch_when_schema_present():
    from src.engine.cycle_runtime import _dual_write_canonical_entry_if_available
    from src.state.db import apply_architecture_kernel_schema
    from src.state.lifecycle_manager import LifecyclePhase

    class _Logger:
        def debug(self, *args, **kwargs):
            return None

    class _Deps:
        logger = _Logger()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)

    wrote = _dual_write_canonical_entry_if_available(
        conn,
        _runtime_position(state="entered", chain_state="unknown"),
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        deps=_Deps(),
    )

    assert wrote is True
    rows = conn.execute(
        "SELECT event_type, sequence_no FROM position_events WHERE position_id = 'rt-pos-1' ORDER BY sequence_no"
    ).fetchall()
    projection_row = conn.execute(
        "SELECT phase, strategy_key, order_status FROM position_current WHERE position_id = 'rt-pos-1'"
    ).fetchone()

    assert [(r["event_type"], r["sequence_no"]) for r in rows] == [
        ("POSITION_OPEN_INTENT", 1),
        ("ENTRY_ORDER_POSTED", 2),
        ("ENTRY_ORDER_FILLED", 3),
    ]
    assert dict(projection_row) == {
        "phase": "active",
        "strategy_key": "center_buy",
        "order_status": "filled",
    }
    conn.close()


def test_cycle_runtime_entry_sequence_writes_canonical_entry_events():
    from src.engine.cycle_runtime import _dual_write_canonical_entry_if_available
    from src.state.db import (
        apply_architecture_kernel_schema,
        log_execution_report,
        log_trade_entry,
    )
    from src.state.lifecycle_manager import LifecyclePhase

    class _Logger:
        def debug(self, *args, **kwargs):
            return None

    class _Deps:
        logger = _Logger()

    class _Result:
        status = "filled"
        reason = None
        submitted_price = 0.5
        fill_price = 0.5
        shares = 20.0
        timeout_seconds = None
        filled_at = "2026-04-03T00:05:00Z"

    pos = _runtime_position(state="entered", chain_state="unknown")

    canonical_conn = sqlite3.connect(":memory:")
    canonical_conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(canonical_conn)
    log_trade_entry(canonical_conn, pos)
    wrote_canonical = _dual_write_canonical_entry_if_available(
        canonical_conn,
        pos,
        phase_after=LifecyclePhase.ACTIVE.value,
        decision_id="dec-1",
        deps=_Deps(),
    )
    log_execution_report(canonical_conn, pos, _Result())
    assert wrote_canonical is True
    assert (
        canonical_conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        == 3
    )
    assert (
        canonical_conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
        == 1
    )
    canonical_conn.close()


def test_advisory_gate_workflow_freezes_verdict():
    workflow = load_yaml(".github/workflows/architecture_advisory_gates.yml")
    jobs = workflow["jobs"]
    triggers = workflow.get("on") or workflow.get(True) or {}

    assert "advisory-gate-policy" in jobs
    assert jobs["semgrep-zeus"].get("continue-on-error") is True
    assert jobs["replay-parity"].get("continue-on-error") is True

    trigger_paths = set(triggers["pull_request"]["paths"])
    assert "scripts/_yaml_bootstrap.py" in trigger_paths
    assert "docs/work_packets/**" in trigger_paths

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_advisory_gates.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "policy verdict only; advisory jobs still require separate evidence review"
        in result.stdout
    )
