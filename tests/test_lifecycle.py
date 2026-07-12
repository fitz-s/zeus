# Created: 2025-10-01
# Lifecycle: created=2025-10-01; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Exit-trigger + harvester lifecycle regression tests — covers
#          position exit detection, harvest_settlement default-HIGH routing
#          through calibration_pairs after C5 (2026-04-24), and p_raw
#          skip behavior when ensemble signal is absent.
# Reuse: Referenced by regression suite; last touched 2026-05-08 for Wave28
#        (HIGH→v2 route). Apply v2 schema in test fixtures when asserting
#        post-harvest pair rows.
# Last reused/audited: 2026-06-20
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md
"""Tests for exit triggers and harvester."""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.engine import monitor_refresh
# Wave 3 (2026-06-02): evaluate_exit_triggers deleted (dead twin). TestExitTriggers
#   repointed to Position.evaluate_exit (the one live path).
from src.execution.harvester import harvest_settlement
from src.state.portfolio import Position, PortfolioState, ExitContext
from src.state.db import get_connection, init_schema
from src.config import City


def _call_exit(
    pos: Position,
    fresh_prob: float,
    current_market_price: float,
    *,
    hours_to_settlement: float = 72.0,
    best_bid: float | None = None,
    divergence_score: float = 0.0,
    market_velocity_1h: float = 0.0,
    whale_toxicity: bool | None = None,
    market_vig: float | None = None,
    entry_ci: tuple[float, float] | None = None,
    current_ci: tuple[float, float] | None = None,
    entry_posterior: float | None = None,
):
    """Thin wrapper: call the one live exit path."""
    ctx = ExitContext(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid if best_bid is not None else current_market_price,
        hours_to_settlement=hours_to_settlement,
        position_state="active",
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
        whale_toxicity=whale_toxicity,
        market_vig=market_vig,
        entry_ci=entry_ci,
        current_ci=current_ci,
        entry_posterior=entry_posterior,
    )
    return pos.evaluate_exit(ctx)


NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1", market_id="m1", city="NYC",
        cluster="US-Northeast", target_date="2026-01-15",
        bin_label="39-40", direction="buy_yes",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60,
        edge=0.20, entered_at="2026-01-12T00:00:00Z",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def test_legacy_edli_forecast_high_buy_no_strategy_label_repairs_at_runtime():
    from src.state.portfolio import _runtime_strategy_key_from_projection_row

    row = {
        "position_id": "legacy-high",
        "strategy_key": "settlement_capture",
        "entry_method": "ens_member_counting",
        "direction": "buy_no",
        "temperature_metric": "high",
        "target_date": "2999-01-01",
    }

    assert _runtime_strategy_key_from_projection_row(row) == "opening_inertia"


def test_legacy_edli_forecast_low_buy_no_strategy_label_repairs_at_runtime():
    from src.state.portfolio import _runtime_strategy_key_from_projection_row

    row = {
        "position_id": "legacy-low",
        "strategy_key": "settlement_capture",
        "entry_method": "ens_member_counting",
        "direction": "buy_no",
        "temperature_metric": "low",
        "target_date": "2999-01-01",
    }

    assert _runtime_strategy_key_from_projection_row(row) == "opening_inertia"


def test_legacy_edli_same_day_high_buy_no_strategy_label_repairs_at_runtime():
    from src.state.portfolio import _runtime_strategy_key_from_projection_row

    row = {
        "position_id": "legacy-sameday-high",
        "strategy_key": "settlement_capture",
        "entry_method": "ens_member_counting",
        "direction": "buy_no",
        "temperature_metric": "high",
        "target_date": datetime.now(timezone.utc).date().isoformat(),
    }

    assert _runtime_strategy_key_from_projection_row(row) == "opening_inertia"


def test_repaired_opening_inertia_position_does_not_emit_review_fact():
    from src.state.portfolio import _invalid_strategy_review_fact_from_position

    pos = _make_position(
        trade_id="legacy-low",
        city="Tokyo",
        target_date="2999-01-01",
        temperature_metric="low",
        direction="buy_no",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        entry_method="ens_member_counting",
        no_token_id="no-token-low",
        condition_id="condition-low",
        shares=9.0,
        entry_price=0.97,
        cost_basis_usd=8.73,
    )

    assert _invalid_strategy_review_fact_from_position(pos) is None


def test_terminal_invalid_legacy_position_does_not_emit_review_fact():
    from src.state.portfolio import _invalid_strategy_review_fact_from_position

    pos = _make_position(
        trade_id="terminal-low",
        city="Tokyo",
        target_date="2026-06-08",
        temperature_metric="low",
        direction="buy_no",
        strategy_key="settlement_capture",
        strategy="settlement_capture",
        state="settled",
        chain_state="synced",
        no_token_id="no-token-low",
    )

    assert _invalid_strategy_review_fact_from_position(pos) is None


def test_entry_proof_accepts_actionable_provenance_without_receipt_row():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        receipt_json=None,
        actionable_payload_json=json.dumps(
            {
                "strategy_key": "opening_inertia",
                "q_source": "emos",
                "opportunity_book": {
                    "selected_candidate_id": "c1",
                    "actual_receipt_selected_candidate_id": "c1",
                },
            }
        ),
        calibration_payload_json=json.dumps({"authority": "EMOS", "n_samples": 20}),
    )

    assert rejection is None


def test_entry_proof_accepts_live_decision_audit_as_real_submit_authority():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        decision_audit_json=json.dumps(
            {
                "strategy_key": "opening_inertia",
                "q_source": "emos",
                "opportunity_book": {
                    "selected_candidate_id": "c1",
                    "actual_receipt_selected_candidate_id": "c1",
                },
            }
        ),
        receipt_json=None,
        actionable_payload_json=None,
        calibration_payload_json=None,
    )

    assert rejection is None


def test_edli_entry_proof_query_does_not_freeze_legacy_pre_audit_fill():
    from src.state.portfolio import _query_edli_entry_proof_review_reasons

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE venue_commands (
            decision_id TEXT,
            venue_order_id TEXT,
            intent_kind TEXT,
            token_id TEXT,
            updated_at TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute("CREATE TABLE world.edli_no_submit_receipts (event_id TEXT, token_id TEXT, created_at TEXT, receipt_json TEXT)")
    conn.execute("CREATE TABLE world.decision_certificates (certificate_type TEXT, semantic_key TEXT, payload_json TEXT, created_at TEXT)")
    conn.execute(
        """
        CREATE TABLE world.edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            payload_json TEXT,
            occurred_at TEXT
        )
        """
    )
    event_id = "edli_evt_legacy"
    token_id = "tok-legacy"
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, 'ENTRY', ?, ?, ?)",
        (
            f"edli_exec_cmd:{event_id}:intent-legacy:{token_id}:buy_no",
            "order-legacy",
            token_id,
            "2026-06-07T01:02:00+00:00",
            "2026-06-07T01:01:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO world.edli_live_order_events VALUES (?, 1, 'DecisionProofAccepted', ?, ?)",
        (
            f"{event_id}:intent-legacy",
            json.dumps({"event_id": event_id, "final_intent_id": "intent-legacy"}),
            "2026-06-07T01:01:00+00:00",
        ),
    )

    reasons = _query_edli_entry_proof_review_reasons(
        conn,
        [
            {
                "trade_id": "edli-legacy",
                "phase": "active",
                "entry_method": "ens_member_counting",
                "no_token_id": token_id,
                "order_id": "order-legacy",
            }
        ],
    )

    assert reasons == {}


def test_edli_entry_proof_query_requires_decision_audit_after_hotfix():
    from src.state.portfolio import _query_edli_entry_proof_review_reasons

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE venue_commands (
            decision_id TEXT,
            venue_order_id TEXT,
            intent_kind TEXT,
            token_id TEXT,
            updated_at TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute("CREATE TABLE world.edli_no_submit_receipts (event_id TEXT, token_id TEXT, created_at TEXT, receipt_json TEXT)")
    conn.execute("CREATE TABLE world.decision_certificates (certificate_type TEXT, semantic_key TEXT, payload_json TEXT, created_at TEXT)")
    conn.execute(
        """
        CREATE TABLE world.edli_live_order_events (
            aggregate_id TEXT,
            event_sequence INTEGER,
            event_type TEXT,
            payload_json TEXT,
            occurred_at TEXT
        )
        """
    )
    event_id = "edli_evt_new"
    token_id = "tok-new"
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, 'ENTRY', ?, ?, ?)",
        (
            f"edli_exec_cmd:{event_id}:intent-new:{token_id}:buy_no",
            "order-new",
            token_id,
            "2026-06-07T03:02:00+00:00",
            "2026-06-07T03:01:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO world.edli_live_order_events VALUES (?, 1, 'DecisionProofAccepted', ?, ?)",
        (
            f"{event_id}:intent-new",
            json.dumps({"event_id": event_id, "final_intent_id": "intent-new"}),
            "2026-06-07T03:01:00+00:00",
        ),
    )

    reasons = _query_edli_entry_proof_review_reasons(
        conn,
        [
            {
                "trade_id": "edli-new",
                "phase": "active",
                "entry_method": "ens_member_counting",
                "no_token_id": token_id,
                "order_id": "order-new",
            }
        ],
    )

    assert reasons == {"edli-new": "EDLI_ENTRY_DECISION_AUDIT_MISSING"}


def test_entry_proof_rejects_missing_actionable_provenance_without_receipt_row():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        receipt_json=None,
        actionable_payload_json=json.dumps({"strategy_key": "opening_inertia"}),
        calibration_payload_json=json.dumps({"authority": "EMOS", "n_samples": 20}),
    )

    assert rejection == "EDLI_ENTRY_ACTIONABLE_Q_SOURCE_MISSING"


def test_entry_proof_rejects_missing_family_selection_authority():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        receipt_json=json.dumps({"q_source": "emos"}),
        actionable_payload_json=json.dumps(
            {
                "strategy_key": "opening_inertia",
                "q_source": "emos",
                "opportunity_book": {"selected_candidate_id": "c1"},
            }
        ),
        calibration_payload_json=json.dumps({"authority": "EMOS", "n_samples": 20}),
    )

    assert rejection == "EDLI_ENTRY_OPPORTUNITY_BOOK_MISSING"


def test_entry_proof_rejects_identity_fallback_calibration():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        receipt_json=json.dumps(
            {
                "q_source": "emos",
                "opportunity_book": {
                    "selected_candidate_id": "c1",
                    "actual_receipt_selected_candidate_id": "c1",
                },
            }
        ),
        actionable_payload_json=json.dumps(
            {
                "strategy_key": "opening_inertia",
                "q_source": "emos",
                "opportunity_book": {"selected_candidate_id": "c1"},
            }
        ),
        calibration_payload_json=json.dumps(
            {"authority": "IDENTITY_FALLBACK_NO_PLATT_BUCKET", "n_samples": 0}
        ),
    )

    assert rejection == "EDLI_ENTRY_CALIBRATION_IDENTITY_FALLBACK"


def test_entry_proof_accepts_insufficient_data_empty_calibration_sample():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        receipt_json=json.dumps(
            {
                "q_source": "qkernel_spine",
                "opportunity_book": {
                    "selected_candidate_id": "c1",
                    "actual_receipt_selected_candidate_id": "c1",
                },
            }
        ),
        actionable_payload_json=json.dumps(
            {
                "strategy_key": "opening_inertia",
                "q_source": "qkernel_spine",
                "opportunity_book": {"selected_candidate_id": "c1"},
            }
        ),
        calibration_payload_json=json.dumps(
            {
                "authority": "FUSED_BOOTSTRAP_CALIBRATION",
                "coverage_status": "INSUFFICIENT_DATA",
                "n_samples": 0,
            }
        ),
    )

    assert rejection is None


def test_entry_proof_accepts_day0_observation_hard_fact_empty_sample():
    from src.state.portfolio import _entry_proof_rejection_from_evidence

    rejection = _entry_proof_rejection_from_evidence(
        receipt_json=json.dumps(
            {
                "q_source": "day0_remaining_day",
                "opportunity_book": {
                    "selected_candidate_id": "c1",
                    "actual_receipt_selected_candidate_id": "c1",
                },
            }
        ),
        actionable_payload_json=json.dumps(
            {
                "strategy_key": "center_buy",
                "q_source": "day0_remaining_day",
                "opportunity_book": {"selected_candidate_id": "c1"},
            }
        ),
        calibration_payload_json=json.dumps(
            {
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
                "n_samples": 0,
            }
        ),
    )

    assert rejection is None


def test_invalid_entry_proof_emits_blocking_review_fact_for_repaired_high_position():
    from src.state.portfolio import _invalid_entry_proof_review_fact_from_position

    pos = _make_position(
        trade_id="edli-high-invalid-proof",
        city="Helsinki",
        target_date="2999-01-01",
        temperature_metric="high",
        direction="buy_no",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        entry_method="ens_member_counting",
        no_token_id="no-token-high",
        condition_id="condition-high",
        shares=5.0,
        entry_price=0.69,
        cost_basis_usd=3.45,
    )

    fact = _invalid_entry_proof_review_fact_from_position(
        pos,
        reason="EDLI_ENTRY_CALIBRATION_IDENTITY_FALLBACK",
    )

    assert fact is not None
    assert fact.token_id == "no-token-high"
    assert fact.blocks_entry is False
    assert fact.blocks_position_management is True


def test_chain_reconciliation_phantom_void_persists_canonical_projection(tmp_path):
    """Relationship: Chain > Portfolio voids must persist to canonical DB truth."""

    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import query_position_events

    conn = get_connection(tmp_path / "chain_phantom_void.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="phantom-db-1",
        state="holding",
        chain_state="synced",
        token_id="tok-phantom",
        no_token_id="tok-phantom-no",
        shares=6.0,
        cost_basis_usd=1.86,
        size_usd=1.86,
        entry_price=0.31,
        entered_at="2026-05-18T12:00:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric
        ) VALUES (
            ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-phantom",
            "ens_member_counting",
            pos.strategy_key,
            "opening_inertia",
            "opening_hunt",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-phantom",
            "order-phantom",
            "filled",
            pos.entered_at,
            "high",
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["voided"] == 1
    assert row["phase"] == "voided"
    assert [event["event_type"] for event in events] == ["ADMIN_VOIDED"]
    assert events[0]["source"] == "src.state.chain_reconciliation"
    assert events[0]["details"]["reason"] == "PHANTOM_NOT_ON_CHAIN"


def test_chain_reconciliation_does_not_void_chain_observed_aggregate_lot():
    """A lot already carrying the token aggregate chain observation is not phantom.

    Regression for 2026-06-17 Shenzhen: an older buy_no lot was corrected to the
    aggregate NO-token balance, then a newer same-token lot appeared in the
    runtime portfolio. LIFO aggregate allocation saw the corrected older lot as
    too large and voided it even though the token was present on chain.
    """

    from src.state.chain_reconciliation import ChainPosition, reconcile

    token = "tok-no-aggregate"
    older = _make_position(
        trade_id="older-aggregate-lot",
        state="holding",
        chain_state="synced",
        direction="buy_no",
        token_id="tok-yes",
        no_token_id=token,
        shares=31.5,
        chain_shares=31.5,
        chain_verified_at="2026-06-17T16:26:14+00:00",
        entered_at="2026-06-17T16:21:58+00:00",
        order_id="older-order",
    )
    newer = _make_position(
        trade_id="newer-lot",
        state="holding",
        chain_state="synced",
        direction="buy_no",
        token_id="tok-yes",
        no_token_id=token,
        shares=15.5,
        entered_at="2026-06-17T16:23:12+00:00",
        order_id="newer-order",
    )

    portfolio = PortfolioState(positions=[older, newer])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id=token, size=31.5, avg_price=0.74, condition_id="cond-no")],
    )

    assert stats["voided"] == 0
    assert stats["skipped_aggregate_allocation_existing_chain_observation"] == 1
    assert older.state == "holding"
    assert newer.state == "holding"


def test_chain_reconciliation_confirmed_absent_position_quarantines_not_voids(tmp_path):
    """Relationship: confirmed fills missing on-chain require attribution, not
    void — T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
    PHASE LAW): the position keeps its TRUE phase/chain_state; the dispute is
    tracked by an open ReviewWorkItem (CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT),
    never a quarantine scar."""

    from src.state.chain_reconciliation import (
        ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON,
        ChainPosition,
        reconcile,
    )
    from src.state.db import query_position_events
    from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL

    conn = get_connection(tmp_path / "chain_confirmed_absence.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="confirmed-absent-1",
        state="day0_window",
        chain_state="synced",
        direction="buy_no",
        token_id="tok-confirmed-yes",
        no_token_id="tok-confirmed-no",
        # T5: the position keeps its TRUE (open) phase now — Fix B's
        # condition_id-required-on-open-phases guard applies, matching the
        # condition_id already seeded on the position_current row below.
        condition_id="cond-confirmed-absent",
        shares=5.06,
        chain_shares=5.0599,
        cost_basis_usd=3.795,
        size_usd=3.795,
        entry_price=0.75,
        entered_at="2026-06-20T02:44:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
        order_status="filled",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares, chain_seen_at
        ) VALUES (
            ?, 'day0_window', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-confirmed-absent",
            "ens_member_counting",
            pos.strategy_key,
            "opening_inertia",
            "day0_capture",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-confirmed-absent",
            "order-confirmed-absent",
            "filled",
            pos.entered_at,
            "low",
            pos.fill_authority,
            pos.chain_shares,
            "2026-06-20T02:45:00+00:00",
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state, shares, chain_shares FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["voided"] == 0
    assert stats["confirmed_fill_chain_absence_conflict_preserved"] == 1
    # T5: TRUE phase/chain_state preserved (never overwritten to a quarantine
    # scar) — the dispute lives in an open ReviewWorkItem instead.
    assert row["phase"] == "day0_window"
    assert row["chain_state"] == "synced"
    assert row["shares"] == pos.shares
    assert row["chain_shares"] == pos.chain_shares
    assert [event["event_type"] for event in events] == ["REVIEW_REQUIRED"]
    assert events[0]["details"]["reason"] == ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON
    assert events[0]["details"]["held_token_id"] == "tok-confirmed-no"
    assert events[0]["details"]["no_token_id"] == "tok-confirmed-no"


def test_chain_reconciliation_recent_positive_observation_defers_confirmed_absence(tmp_path):
    """A transient missing token must not quarantine recently chain-observed exposure."""

    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import query_position_events
    from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL

    conn = get_connection(tmp_path / "chain_confirmed_absence_recent_positive.db")
    init_schema(conn)
    observed_at = datetime.now(timezone.utc).isoformat()

    pos = _make_position(
        trade_id="confirmed-absent-recent-positive",
        state="day0_window",
        chain_state="synced",
        direction="buy_no",
        token_id="tok-recent-yes",
        no_token_id="tok-recent-no",
        shares=5.06,
        chain_shares=5.06,
        cost_basis_usd=3.795,
        size_usd=3.795,
        entry_price=0.75,
        entered_at="2026-06-20T02:44:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
        order_status="filled",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares, chain_seen_at
        ) VALUES (
            ?, 'day0_window', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-confirmed-absent-recent-positive",
            "ens_member_counting",
            pos.strategy_key,
            "opening_inertia",
            "day0_capture",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-confirmed-absent-recent-positive",
            "order-confirmed-absent-recent-positive",
            "filled",
            pos.entered_at,
            "low",
            pos.fill_authority,
            pos.chain_shares,
            observed_at,
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state, shares, chain_shares FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["voided"] == 0
    assert stats.get("confirmed_chain_absence_quarantined", 0) == 0
    assert stats["confirmed_chain_absence_recent_positive_deferred"] == 1
    assert row["phase"] == "day0_window"
    assert row["chain_state"] == "synced"
    assert row["shares"] == pos.shares
    assert row["chain_shares"] == pos.chain_shares
    assert events == []


def test_chain_reconciliation_uses_canonical_recent_positive_when_runtime_chain_shares_missing(tmp_path):
    """Canonical chain evidence must veto absence quarantine even if runtime cache is thin."""

    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import query_position_events
    from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL

    conn = get_connection(tmp_path / "chain_confirmed_absence_canonical_recent.db")
    init_schema(conn)
    observed_at = datetime.now(timezone.utc).isoformat()

    pos = _make_position(
        trade_id="confirmed-absent-canonical-recent",
        state="day0_window",
        chain_state="synced",
        direction="buy_yes",
        token_id="tok-canonical-recent-yes",
        no_token_id="tok-canonical-recent-no",
        shares=12.7,
        chain_shares=0.0,
        cost_basis_usd=0.889,
        size_usd=0.889,
        entry_price=0.07,
        entered_at="2026-06-29T12:52:00+00:00",
        strategy_key="center_buy",
        strategy="center_buy",
        env="live",
        unit="C",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
        order_status="partial",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares, chain_seen_at
        ) VALUES (
            ?, 'day0_window', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-confirmed-absent-canonical-recent",
            "qkernel_spine",
            pos.strategy_key,
            "center_buy",
            "forecast_redecision",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-confirmed-absent-canonical-recent",
            "order-confirmed-absent-canonical-recent",
            "partial",
            pos.entered_at,
            "high",
            pos.fill_authority,
            12.7,
            observed_at,
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state, shares, chain_shares FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["voided"] == 0
    assert stats.get("confirmed_chain_absence_quarantined", 0) == 0
    assert stats["confirmed_chain_absence_recent_positive_deferred"] == 1
    assert row["phase"] == "day0_window"
    assert row["chain_state"] == "synced"
    assert row["shares"] == pos.shares
    assert row["chain_shares"] == pytest.approx(12.7)
    assert events == []


def test_chain_reconciliation_venue_partial_fill_fact_prevents_phantom_void(tmp_path):
    """A live partial fill is real exposure even before local fill authority
    catches up — T5: TRUE phase/chain_state preserved, dispute tracked by an
    open ReviewWorkItem instead of a quarantine scar."""

    from src.state.chain_reconciliation import (
        ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON,
        ChainPosition,
        reconcile,
    )
    from src.state.db import query_position_events

    conn = get_connection(tmp_path / "venue_partial_fill_fact_not_phantom.db")
    init_schema(conn)
    observed_at = datetime.now(timezone.utc).isoformat()

    pos = _make_position(
        trade_id="venue-partial-fill-position",
        state="holding",
        chain_state="synced",
        direction="buy_yes",
        token_id="tok-partial-fill-yes",
        no_token_id="tok-partial-fill-no",
        # T5: the position keeps its TRUE (open) phase now — Fix B's
        # condition_id-required-on-open-phases guard applies, matching the
        # condition_id already seeded on the position_current row below.
        condition_id="cond-venue-partial-fill",
        shares=85.17,
        chain_shares=0.0,
        cost_basis_usd=4.3436,
        size_usd=4.3436,
        entry_price=0.051,
        entered_at="2026-06-29T11:16:00+00:00",
        strategy_key="center_buy",
        strategy="center_buy",
        env="live",
        unit="F",
        order_id="0xvenuepartialfill",
        order_status="partial",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares
        ) VALUES (
            ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-venue-partial-fill",
            "qkernel_spine",
            pos.strategy_key,
            "center_buy",
            "forecast_redecision",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-venue-partial-fill",
            pos.order_id,
            "partial",
            pos.entered_at,
            "high",
            "none",
            0.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', ?, ?, ?, 'CANCELLED', ?, ?)
        """,
        (
            "cmd-venue-partial-fill",
            "snap-venue-partial-fill",
            "env-venue-partial-fill",
            pos.trade_id,
            "decision-venue-partial-fill",
            "idem-venue-partial-fill",
            pos.market_id,
            pos.token_id,
            135.89,
            pos.entry_price,
            pos.order_id,
            observed_at,
            observed_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, state, filled_size, fill_price,
            source, observed_at, venue_timestamp, local_sequence, raw_payload_hash,
            raw_payload_json
        ) VALUES (?, ?, ?, 'MATCHED', ?, ?, 'WS_USER', ?, ?, 1, ?, '{}')
        """,
        (
            "venue-trade-partial-fill",
            pos.order_id,
            "cmd-venue-partial-fill",
            "85.17",
            "0.051",
            observed_at,
            observed_at,
            "hash-venue-partial-fill",
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state, shares FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["voided"] == 0
    assert stats["confirmed_fill_chain_absence_conflict_preserved"] == 1
    assert row["phase"] == "active"
    assert row["chain_state"] == "synced"
    assert row["shares"] == pos.shares
    assert [event["event_type"] for event in events] == ["REVIEW_REQUIRED"]
    assert events[0]["details"]["reason"] == ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON


def test_chain_reconciliation_restores_false_phantom_void_with_positive_exposure(tmp_path):
    """A false PHANTOM void with positive exposure must be restored to its
    TRUE (active) phase — T5 REPLACEMENT PHASE LAW, never a quarantine scar;
    tracked by an open TERMINAL_RESTORE_EXPOSURE ReviewWorkItem."""

    from src.state.chain_reconciliation import (
        ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON,
        ChainPosition,
        reconcile,
    )
    from src.state.db import query_position_events
    from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL

    conn = get_connection(tmp_path / "false_phantom_void_positive_exposure.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="false-phantom-positive",
        state="voided",
        chain_state="synced",
        direction="buy_yes",
        token_id="tok-false-phantom-yes",
        no_token_id="tok-false-phantom-no",
        # T5: restored to its TRUE (open) phase now — Fix B's condition_id-
        # required-on-open-phases guard applies, matching the condition_id
        # already seeded on the position_current row below.
        condition_id="cond-false-phantom",
        shares=85.17,
        chain_shares=85.17,
        cost_basis_usd=4.3436,
        size_usd=4.3436,
        entry_price=0.051,
        entered_at="2026-06-29T11:16:00+00:00",
        strategy_key="center_buy",
        strategy="center_buy",
        env="live",
        unit="F",
        order_id="0xfalsephantom",
        order_status="partial",
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        entry_fill_verified=True,
        exit_reason="PHANTOM_NOT_ON_CHAIN",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares, chain_seen_at, exit_reason
        ) VALUES (
            ?, 'voided', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-false-phantom",
            "qkernel_spine",
            pos.strategy_key,
            "center_buy",
            "forecast_redecision",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-false-phantom",
            pos.order_id,
            pos.order_status,
            pos.entered_at,
            "high",
            pos.fill_authority,
            pos.chain_shares,
            pos.entered_at,
            pos.exit_reason,
        ),
    )
    conn.commit()

    stats = reconcile(
        PortfolioState(positions=[pos]),
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5)],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state, shares, chain_shares FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["false_phantom_void_positive_exposure_restored"] == 1
    assert row["phase"] == "active"
    assert row["chain_state"] == "synced"
    assert row["shares"] == pytest.approx(85.17)
    assert row["chain_shares"] == pytest.approx(85.17)
    assert [event["event_type"] for event in events] == ["REVIEW_REQUIRED"]
    assert events[0]["details"]["reason"] == ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON


def test_chain_reconciliation_restores_terminal_no_fill_void_when_chain_holds_token(tmp_path):
    """A terminal no-fill projection is not final if chain later proves
    exposure — T5 REPLACEMENT PHASE LAW: restored to TRUE (active) phase,
    never a quarantine scar; tracked by an open TERMINAL_RESTORE_EXPOSURE
    ReviewWorkItem."""

    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import query_position_events
    from src.state.portfolio import FILL_AUTHORITY_VENUE_POSITION_OBSERVED

    conn = get_connection(tmp_path / "terminal_no_fill_chain_positive.db")
    init_schema(conn)
    pos = _make_position(
        trade_id="terminal-no-fill-positive-chain",
        state="voided",
        chain_state="local_only",
        direction="buy_no",
        token_id="tok-terminal-yes",
        no_token_id="tok-terminal-no",
        shares=10.7,
        chain_shares=0.0,
        cost_basis_usd=5.80,
        size_usd=5.80,
        entry_price=0.54,
        entered_at="2026-06-29T18:00:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
        order_id="0xterminalnofill",
        order_status="canceled",
        fill_authority="",
        entry_fill_verified=False,
        exit_reason="venue_terminal_no_fill",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares, chain_seen_at, exit_reason
        ) VALUES (
            ?, 'voided', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-terminal-no-fill",
            "qkernel_spine",
            pos.strategy_key,
            "opening_inertia",
            "forecast_redecision",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-terminal-no-fill",
            pos.order_id,
            pos.order_status,
            pos.entered_at,
            "high",
            pos.fill_authority,
            pos.chain_shares,
            "",
            pos.exit_reason,
        ),
    )
    conn.commit()

    stats = reconcile(
        PortfolioState(positions=[pos]),
        [
            ChainPosition(
                token_id="tok-terminal-no",
                size=10.7,
                avg_price=0.54,
                cost=5.80,
                condition_id="cond-terminal-no-fill",
            )
        ],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        """
        SELECT phase, chain_state, shares, chain_shares, fill_authority, exit_reason
          FROM position_current
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["terminal_chain_exposure_restored"] == 1
    assert row["phase"] == "active"
    assert row["chain_state"] == "synced"
    assert row["shares"] == pytest.approx(10.7)
    assert row["chain_shares"] == pytest.approx(10.7)
    assert row["fill_authority"] == FILL_AUTHORITY_VENUE_POSITION_OBSERVED
    assert row["exit_reason"] is None
    assert [event["event_type"] for event in events] == ["REVIEW_REQUIRED"]
    assert events[0]["details"]["reason"] == "chain_held_after_terminal_projection"


def test_chain_reconciliation_phantom_void_allows_legacy_unknown_phase_before(tmp_path):
    """Relationship: legacy runtime states can still be canonically voided."""

    from src.state.chain_reconciliation import ChainPosition, reconcile

    conn = get_connection(tmp_path / "chain_phantom_void_unknown_phase.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="phantom-unknown-phase",
        state="holding",
        chain_state="synced",
        token_id="tok-legacy-phantom",
        no_token_id="tok-legacy-phantom-no",
        shares=2.0,
        cost_basis_usd=0.6,
        size_usd=0.6,
        entry_price=0.3,
        entered_at="2026-05-18T12:00:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
    )
    pos.state = "quarantine_size_mismatch"
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric
        ) VALUES (
            ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-legacy-phantom",
            "ens_member_counting",
            pos.strategy_key,
            "opening_inertia",
            "opening_hunt",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-legacy-phantom",
            "order-legacy-phantom",
            "filled",
            pos.entered_at,
            "high",
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    row = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    event = conn.execute(
        "SELECT event_type, phase_before, phase_after FROM position_events WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    conn.close()

    assert stats["voided"] == 1
    assert row["phase"] == "voided"
    assert dict(event) == {
        "event_type": "ADMIN_VOIDED",
        "phase_before": None,
        "phase_after": "voided",
    }


class TestExitTriggers:
    """Wave 3 (2026-06-02): all tests repointed from evaluate_exit_triggers
    (dead twin, deleted) to Position.evaluate_exit (the one live path).
    entry_price=0.40, p_posterior=0.60; use hours_to_settlement=72.0 unless
    testing near-settlement behavior (near_settlement_hours()=48).
    """

    def test_settlement_imminent(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, hours_to_settlement=0.5)
        assert decision.should_exit
        assert decision.trigger == "SETTLEMENT_IMMINENT"

    def test_settlement_imminent_confirmed_win_holds(self):
        pos = _make_position()
        decision = _call_exit(
            pos,
            0.99,
            0.998,
            best_bid=0.998,
            hours_to_settlement=0.5,
            divergence_score=0.40,
            market_velocity_1h=-0.20,
        )
        assert not decision.should_exit
        assert "near_settlement_confirmed_win_hold" in decision.applied_validations
        assert decision.trigger != "MODEL_DIVERGENCE_PANIC"

    def test_whale_toxicity(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, whale_toxicity=True)
        assert decision.should_exit
        assert decision.trigger == "WHALE_TOXICITY"

    def test_soft_divergence_requires_adverse_velocity_confirmation(self):
        """Soft divergence (0.20) without adverse velocity does not panic."""
        pos = _make_position()
        decision = _call_exit(
            pos, 0.20, 0.40, divergence_score=0.20, market_velocity_1h=0.0,
        )
        assert not decision.should_exit or decision.trigger != "MODEL_DIVERGENCE_PANIC"

    def test_hard_divergence_without_current_exit_evidence_does_not_panic(self):
        """Hard divergence alone no longer fires the removed model-divergence panic."""
        pos = _make_position()
        decision = _call_exit(
            pos, 0.20, 0.40, divergence_score=0.31, market_velocity_1h=0.0,
        )

        assert not decision.should_exit
        assert decision.trigger != "MODEL_DIVERGENCE_PANIC"

    def test_edge_reversal_needs_two_confirmations(self):
        """CLAUDE.md §4.2: EDGE_REVERSAL needs 2 confirmations, 1st doesn't trigger.

        buy_yes: fresh_prob=0.30 < market=0.40 → forward_edge=-0.10 (negative).
        CI_OVERLAP_HOLD gate: entry_ci_width=0 (default), so width/2=0 →
        ci_lo=ci_hi=entry_price → only fires when fresh_prob==entry_price exactly.
        """
        pos = _make_position()
        # First check: edge reversed but only 1 confirmation
        decision = _call_exit(pos, 0.30, 0.40)
        assert not decision.should_exit  # Should NOT trigger on first reversal

        # Second check: confirmed reversal
        decision = _call_exit(pos, 0.30, 0.40)
        assert decision.should_exit
        assert decision.trigger == "EDGE_REVERSAL"

    def test_ci_separated_but_positive_held_edge_holds(self):
        """CI separation alone is not a sell signal while held-side edge remains positive."""
        pos = _make_position(
            direction="buy_no",
            p_posterior=0.999999999,
            entry_price=0.949,
            shares=10.0,
            cost_basis_usd=9.49,
            size_usd=9.49,
        )

        decision = _call_exit(
            pos,
            fresh_prob=0.9992713626795082,
            current_market_price=0.8609807447600297,
            entry_ci=(0.999999999, 0.999999999),
            current_ci=(0.9992713626795082, 0.9992713626795082),
            entry_posterior=0.999999999,
        )

        assert not decision.should_exit
        assert decision.trigger == "CI_SEPARATED_POSITIVE_EDGE_HOLD"

    def test_ci_separated_shenzhen_light_negative_edge_holds(self):
        """Shenzhen 2026-06-19 regression: a lightly negative buy-NO edge is
        not enough to liquidate a still-high-probability held bin."""
        pos = _make_position(
            direction="buy_no",
            p_posterior=0.871650896043244,
            entry_price=0.74,
            entry_ci_width=0.02,
            shares=60.0,
            shares_filled=60.0,
            filled_cost_basis_usd=44.4,
            cost_basis_usd=44.4,
            size_usd=44.4,
        )

        decision = _call_exit(
            pos,
            fresh_prob=0.846733041380824,
            current_market_price=0.85,
            best_bid=0.85,
            entry_ci=(0.86, 0.88),
            current_ci=(0.84, 0.85),
            entry_posterior=0.871650896043244,
        )

        assert not decision.should_exit
        assert decision.trigger == "CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD"
        assert "ci_separated_edge_within_threshold_hold" in decision.applied_validations

    def test_buy_yes_ev_gate_hold_when_bid_below_posterior(self):
        """When best_bid is below current posterior after exit costs, exit is blocked.

        Wave 3: direct observable-behavior test. No monkeypatching needed.
        Position has neg_edge_count=1 (pre-set); next negative cycle would
        normally exit but EV gate blocks it.
        """
        pos = _make_position(p_posterior=0.60, entry_price=0.50)
        pos.neg_edge_count = 1
        # fresh_prob=0.10, market=0.55 -> edge=-0.45 (deeply negative, would exit).
        # best_bid is still too low versus current held-side value after exit costs.
        decision = _call_exit(pos, 0.10, 0.55, best_bid=0.01)
        assert not decision.should_exit  # EV gate blocks

    def test_buy_yes_ev_gate_exits_when_bid_above_posterior(self):
        """When best_bid >= p_posterior (sell EV >= hold EV), exit fires.

        Wave 3: complement of EV-gate-hold test. Fresh posterior has degraded
        (0.10) but market is generous (0.65 bid > posterior). Rational to exit.
        """
        pos = _make_position(p_posterior=0.60, entry_price=0.50)
        pos.neg_edge_count = 1
        # best_bid=0.65 > p_posterior=0.10 → sell value exceeds hold EV → EXIT
        decision = _call_exit(pos, 0.10, 0.65, best_bid=0.65)
        assert decision.should_exit
        assert decision.trigger == "EDGE_REVERSAL"

    def test_stale_probability_authority_blocks_edge_exit(self):
        """Stale fresh_prob (fresh_prob_is_fresh=False) → EVIDENCE_UNAVAILABLE, no exit.

        Wave 3: ExitContext.fresh_prob_is_fresh=False triggers EVIDENCE_UNAVAILABLE hold.
        """
        pos = _make_position(entry_price=0.12, p_posterior=0.90)
        pos.neg_edge_count = 1
        ctx = ExitContext(
            fresh_prob=0.05,
            fresh_prob_is_fresh=False,  # stale — not authority
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            hours_to_settlement=72.0,
            position_state="active",
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit

    def test_flash_crash_panic_fires_with_adverse_velocity(self):
        """Sustained deep adverse velocity triggers FLASH_CRASH_PANIC.

        Wave 3: live path requires probability authority (fresh_prob_is_fresh=True).
        Flash crash is the live panic path after the model-divergence panic branch
        was removed; bare divergence does not preempt it.
        """
        pos = _make_position()
        _call_exit(pos, 0.60, 0.40, market_velocity_1h=-0.45)
        decision = _call_exit(pos, 0.60, 0.40, market_velocity_1h=-0.45)
        assert decision.should_exit
        assert decision.trigger == "FLASH_CRASH_PANIC"

    def test_vig_extreme_fires_with_probability_authority(self):
        """Market-vig extreme (>1.08) triggers VIG_EXTREME exit."""
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, market_vig=1.10)
        assert decision.should_exit
        assert decision.trigger == "VIG_EXTREME"

    def test_edge_reversal_resets_on_recovery(self):
        """If edge recovers between checks, counter resets."""
        pos = _make_position()
        _call_exit(pos, 0.30, 0.40)  # neg → count=1
        _call_exit(pos, 0.60, 0.40)  # pos → count=0
        decision = _call_exit(pos, 0.30, 0.40)  # neg → count=1 again
        assert not decision.should_exit  # Only 1st confirmation after reset

    def test_no_exit_when_edge_healthy(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40)
        assert not decision.should_exit

    def test_vig_extreme(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, market_vig=1.10)
        assert decision.should_exit
        assert decision.trigger == "VIG_EXTREME"


class TestMonitorWhaleToxicity:
    class _BookClob:
        def __init__(self, books):
            self.books = books

        def get_best_bid_ask(self, token_id):
            return self.books[token_id]

    @staticmethod
    def _siblings():
        return [
            {"market_id": "m-below", "range_low": 37, "range_high": 38, "token_id": "yes-below"},
            {"market_id": "m1", "range_low": 39, "range_high": 40, "token_id": "yes-held"},
            {"market_id": "m-above", "range_low": 41, "range_high": 42, "token_id": "yes-above"},
        ]

    @staticmethod
    def _conn_with_prior(tmp_path, token_id: str, price: float, now: datetime):
        conn = get_connection(tmp_path / "whale.db")
        init_schema(conn)
        conn.execute(
            """
            INSERT INTO token_price_log
                (token_id, price, timestamp)
            VALUES (?, ?, ?)
            """,
            (token_id, price, (now - timedelta(hours=2)).isoformat()),
        )
        conn.commit()
        return conn

    def test_orderbook_adjacent_pressure_flags_buy_yes_whale_toxicity(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.40, now)
        clob = self._BookClob({
            "yes-above": (0.50, 0.52, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is True
        assert "whale_toxicity_available:adjacent_orderbook_pressure" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_returns_false_when_clear(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.42, now)
        clob = self._BookClob({
            "yes-above": (0.44, 0.46, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is False
        assert "whale_toxicity_available:clear" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_stays_unknown_without_verified_scan(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.40, now)
        clob = self._BookClob({
            "yes-above": (0.60, 0.62, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "STALE")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is None
        assert "whale_toxicity_unavailable:market_scan_not_verified" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_is_not_applicable_to_buy_no(self):
        pos = _make_position(direction="buy_no", no_token_id="no-held")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            None,
            None,
            pos,
            held_best_bid=None,
            held_best_ask=None,
        )

        assert result is False
        assert "whale_toxicity_not_applicable:buy_no" in pos.applied_validations


class TestHarvester:
    def test_harvest_creates_pairs(self, tmp_path):
        """Post-C5 (2026-04-24): harvest_settlement default-HIGH path now
        writes to calibration_pairs (previously legacy calibration_pairs).
        """
        from src.state.schema.v2_schema import apply_canonical_schema

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        apply_canonical_schema(conn)

        bin_labels = ["32 or below", "33-34", "35-36", "37-38", "39-40",
                      "41-42", "43-44", "45-46", "47-48", "49-50", "51 or higher"]
        p_raw = [0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.03, 0.01, 0.005, 0.005]

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=bin_labels,
            p_raw_vector=p_raw,
            lead_days=3.0,
            forecast_issue_time="2026-01-12T00:00:00Z",
            forecast_model_id="test_lifecycle_v1",
        )
        conn.commit()

        assert count == 11

        # Post-C5: HIGH default routes to calibration_pairs.
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM calibration_pairs GROUP BY outcome"
        ).fetchall()
        outcome_counts = {r[0]: r[1] for r in rows}
        assert outcome_counts[1] == 1
        assert outcome_counts[0] == 10

        conn.close()

    def test_harvest_skips_missing_p_raw(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=["39-40", "41-42"],
            p_raw_vector=None,
        )

        assert count == 0  # No P_raw → no pairs created
        conn.close()
