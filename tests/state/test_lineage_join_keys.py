# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: FIX_SEV1_BUNDLE.md §F2 + FIX_F25_DSI.md — lineage NULL FK antibody

from __future__ import annotations

import re

import pytest

from src.state.db import (
    get_connection,
    init_schema,
    log_selection_family_fact,
    log_selection_hypothesis_fact,
)


# ---------------------------------------------------------------------------
# F2 antibody: selection_hypothesis_fact.decision_id not null after write
# ---------------------------------------------------------------------------

def test_selection_hypothesis_fact_decision_id_not_null_after_write(tmp_path):
    """F2: log_selection_hypothesis_fact must persist decision_id when provided."""
    conn = get_connection(tmp_path / "lineage_f2.db")
    init_schema(conn)

    # selection_hypothesis_fact has FK on family_id → selection_family_fact
    log_selection_family_fact(
        conn,
        family_id="test-fam-001",
        cycle_mode="opening_hunt",
        decision_snapshot_id="snap-001",
        city="NYC",
        target_date="2026-06-01",
        strategy_key="center_buy",
        discovery_mode="opening_hunt",
        created_at="2026-05-17T00:00:00Z",
        meta={"tested_hypotheses": 1},
    )

    log_selection_hypothesis_fact(
        conn,
        hypothesis_id="test-hyp-001",
        family_id="test-fam-001",
        decision_id="test-decision-001",
        candidate_id="test-candidate-001",
        city="NYC",
        target_date="2026-06-01",
        range_label="70-80°F",
        direction="buy_yes",
        recorded_at="2026-05-17T00:00:00Z",
        meta={"source": "test_lineage_join_keys"},
    )

    row = conn.execute(
        "SELECT decision_id FROM selection_hypothesis_fact WHERE hypothesis_id=?",
        ("test-hyp-001",),
    ).fetchone()
    assert row is not None, "Row must exist after write"
    assert row["decision_id"] is not None, (
        "F2: selection_hypothesis_fact.decision_id must not be NULL after write"
    )
    assert row["decision_id"] == "test-decision-001"
    conn.close()


# ---------------------------------------------------------------------------
# F25 antibody: pre-snapshot EdgeDecision DSI is sentinel (non-NULL, non-empty)
# ---------------------------------------------------------------------------

_SENTINEL_RE = re.compile(r"^<pre_snapshot:.+>$")


def test_pre_snapshot_dsi_sentinel_is_non_null_non_empty():
    """F25: _PRE_SNAPSHOT_DSI_SENTINEL must match the expected sentinel pattern."""
    from src.engine.evaluator import _PRE_SNAPSHOT_DSI_SENTINEL

    assert _PRE_SNAPSHOT_DSI_SENTINEL is not None
    assert _PRE_SNAPSHOT_DSI_SENTINEL != ""
    assert _SENTINEL_RE.match(_PRE_SNAPSHOT_DSI_SENTINEL), (
        f"Sentinel {_PRE_SNAPSHOT_DSI_SENTINEL!r} does not match expected pattern"
    )


def test_make_rejection_decision_stamps_sentinel():
    """F25: _make_rejection_decision must stamp DSI sentinel on returned EdgeDecision."""
    from src.engine.evaluator import _PRE_SNAPSHOT_DSI_SENTINEL, _make_rejection_decision

    decision = _make_rejection_decision(
        rejection_stage="TEST_STAGE",
        rejection_reasons=["test reason"],
        selected_method="test_method",
        applied_validations=["v1"],
    )

    assert decision.should_trade is False
    assert decision.decision_id, "decision_id must be non-empty"
    assert decision.decision_snapshot_id == _PRE_SNAPSHOT_DSI_SENTINEL, (
        f"Expected sentinel, got {decision.decision_snapshot_id!r}"
    )
    assert _SENTINEL_RE.match(decision.decision_snapshot_id)
