# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Preflight P0-P8 (SD6). The MC entry gate must verify
#   the P0-P3 code antibodies are wired (schema, gate hash, coverage-mandatory, insufficiency)
#   and fail closed when any is missing.
"""Tests for scripts/mc_entry_gate.py — the P0-P3 go/no-go MC gate."""
from __future__ import annotations

import sqlite3

from src.calibration.ens_bias_repo import init_ens_bias_schema
from scripts.mc_entry_gate import check_mc_entry_gates


def _make_world_db(path, migrated: bool):
    conn = sqlite3.connect(str(path))
    if migrated:
        init_ens_bias_schema(conn)
    else:
        # base table only — no canonical columns -> P0 must fail
        conn.execute("CREATE TABLE model_bias_ens_v2 (city TEXT, season TEXT, metric TEXT)")
        conn.commit()
    conn.close()


def test_all_gates_pass_on_migrated_db(tmp_path):
    db = tmp_path / "zeus-world.db"
    _make_world_db(db, migrated=True)
    r = check_mc_entry_gates(str(db))
    assert r["P0_schema_ready"][0], r["P0_schema_ready"][1]
    assert r["P1_gate_hash"][0], r["P1_gate_hash"][1]
    assert r["P2_coverage_mandatory"][0], r["P2_coverage_mandatory"][1]
    assert r["P3_insufficiency_wide"][0], r["P3_insufficiency_wide"][1]
    assert r["overall"][0]


def test_p0_fails_on_unmigrated_schema(tmp_path):
    db = tmp_path / "zeus-world.db"
    _make_world_db(db, migrated=False)
    r = check_mc_entry_gates(str(db))
    assert not r["P0_schema_ready"][0], "P0 must fail on a schema missing canonical columns"
    assert not r["overall"][0], "overall must FAIL when any gate fails"
    # the code-behaviour gates still pass regardless of the target DB
    assert r["P1_gate_hash"][0]
    assert r["P2_coverage_mandatory"][0]
    assert r["P3_insufficiency_wide"][0]


def test_p0_fails_on_missing_db(tmp_path):
    db = tmp_path / "does-not-exist.db"
    r = check_mc_entry_gates(str(db))
    assert not r["P0_schema_ready"][0]
    assert not r["overall"][0]
