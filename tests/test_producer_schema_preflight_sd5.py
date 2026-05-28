# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Blocker G / Preflight P0 (SD5). The producer must
#   REFUSE to run if model_bias_ens_v2 cannot hold a full canonical row, because
#   write_bias_model silently skips missing columns (backward-compat) and would otherwise
#   'succeed' while dropping gate_set_hash / coverage / scale.
"""Relationship test for SD5: producer schema preflight fails closed.

Cross-module invariant: the writer's backward-compat leniency (skip missing columns) must
be paired with a producer-side preflight that refuses to fit on an unmigrated schema —
otherwise the leniency becomes a silent data-integrity hole.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.calibration.ens_bias_repo import (
    MODEL_BIAS_ENS_V2_SCHEMA,
    assert_model_bias_schema_ready,
    init_ens_bias_schema,
)


def test_preflight_raises_when_table_absent():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(RuntimeError, match="does not exist"):
        assert_model_bias_schema_ready(conn)


def test_preflight_raises_on_base_table_without_canonical_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute(MODEL_BIAS_ENS_V2_SCHEMA)  # base table only — no canonical ALTERs
    conn.commit()
    with pytest.raises(RuntimeError, match="missing required"):
        assert_model_bias_schema_ready(conn)


def test_preflight_passes_after_init_ens_bias_schema():
    conn = sqlite3.connect(":memory:")
    init_ens_bias_schema(conn)  # base + all canonical extension columns
    assert_model_bias_schema_ready(conn) is None  # must not raise


def test_preflight_names_the_missing_gate_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute(MODEL_BIAS_ENS_V2_SCHEMA)
    conn.commit()
    with pytest.raises(RuntimeError) as exc:
        assert_model_bias_schema_ready(conn)
    msg = str(exc.value)
    assert "gate_set_hash" in msg and "coverage_months" in msg
