# Created: 2026-04-23
# Last reused/audited: 2026-05-07
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T4.2-Phase1 exit-side DecisionEvidence audit-only symmetry check, D4 MITIGATED gate); Wave26 canonical position event env authority

"""T4.2-Phase1 exit-side DecisionEvidence audit antibodies.

Phase1 ships the audit-only D4 symmetry check. The
DecisionEvidence.assert_symmetric_with invocation fires on the three
statistically-asymmetric exit triggers (EDGE_REVERSAL / BUY_NO_EDGE_EXIT /
BUY_NO_NEAR_EXIT) when a position has a captured entry envelope (written
by T4.1b). On EvidenceAsymmetryError the audit LOGS a structured JSON
warning (aggregated over 7 days as ``audit_log_false_positive_rate``) and
the exit proceeds. Phase2 (T4.2-Phase2) will flip the try/except to
hard-fail weak exits once the 7-day rate satisfies the ≤ 0.05 gate.

Coverage:
- load_entry_evidence read path from position_events.payload_json
  decision_evidence_envelope sidecar (landed by T4.1b)
- None-return on legacy (no envelope key), backfill (reason sentinel
  only), malformed JSON, unknown contract version, non-ENTRY_ORDER_POSTED
  event types
- Canonical D4 asymmetry detection: weak exit (2 cycles, no FDR) vs
  strong entry (5000-bootstrap CI + BH-FDR) raises EvidenceAsymmetryError
  with messages naming both the sample_size gap and the FDR gap
- Symmetric strong exit does not raise
- Phase1 exit-evidence construction satisfies DecisionEvidence
  __post_init__ invariants
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.contracts.decision_evidence import (
    DECISION_EVIDENCE_CONTRACT_VERSION,
    DecisionEvidence,
    EvidenceAsymmetryError,
)
from src.state.decision_chain import load_entry_evidence
from src.state.portfolio import Position


def _runtime_position(
    *,
    trade_id: str = "rt-pos-1",
    state: str = "pending_tracked",
    chain_state: str = "local_only",
) -> Position:
    return Position(
        trade_id=trade_id,
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
        day0_entered_at="",
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
        exit_state="",
    )


def _setup_conn() -> sqlite3.Connection:
    from src.state.ledger import apply_architecture_kernel_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _raw_insert_entry_posted(conn: sqlite3.Connection, trade_id: str, payload_json: str) -> None:
    """Direct INSERT into position_events bypassing the builder — used
    ONLY by the defense-in-depth tests that seed corrupted / future-version
    payloads which the builder would never produce. Matches the column
    set + NOT NULL constraints per architecture/2026_04_02_architecture_kernel.sql."""
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            decision_id, snapshot_id, order_id, command_id, caused_by,
            idempotency_key, venue_status, source_module, env, payload_json
        ) VALUES (?, ?, 1, 2, 'ENTRY_ORDER_POSTED', ?, 'pending_entry',
                  'pending_entry', 'center_buy', 'dec-raw', NULL, NULL,
                  NULL, NULL, ?, NULL, 'src.test.raw', 'live', ?)
        """,
        (
            f"{trade_id}:entry_order_posted",
            trade_id,
            "2026-04-23T00:00:00Z",
            f"{trade_id}:entry_order_posted",
            payload_json,
        ),
    )
    conn.commit()


def _raw_insert_position_open_intent(conn: sqlite3.Connection, trade_id: str, payload_json: str) -> None:
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            decision_id, snapshot_id, order_id, command_id, caused_by,
            idempotency_key, venue_status, source_module, env, payload_json
        ) VALUES (?, ?, 1, 1, 'POSITION_OPEN_INTENT', ?, NULL,
                  'pending_entry', 'center_buy', 'dec-raw', NULL, NULL,
                  NULL, NULL, ?, NULL, 'src.test.raw', 'live', ?)
        """,
        (
            f"{trade_id}:position_open_intent",
            trade_id,
            "2026-04-23T00:00:00Z",
            f"{trade_id}:position_open_intent",
            payload_json,
        ),
    )
    conn.commit()


def _seed_entry_event(
    conn: sqlite3.Connection,
    trade_id: str,
    *,
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> None:
    """Seed ENTRY_ORDER_POSTED via the canonical builder — exercises the
    same T4.1b write path as production.
    """
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.ledger import append_many_and_project

    events, projection = build_entry_canonical_write(
        _runtime_position(trade_id=trade_id, state="entered", chain_state="unknown"),
        decision_id="dec-1",
        source_module="src.test",
        decision_evidence=decision_evidence,
        decision_evidence_reason=decision_evidence_reason,
    )
    append_many_and_project(conn, events, projection)


def _entry_evidence() -> DecisionEvidence:
    return DecisionEvidence(
        evidence_type="entry",
        statistical_method="bootstrap_ci_bh_fdr",
        sample_size=5000,
        confidence_level=0.10,
        fdr_corrected=True,
        consecutive_confirmations=1,
    )


def _exit_evidence_weak() -> DecisionEvidence:
    """The canonical D4 asymmetry — 2 cycles, no FDR."""
    return DecisionEvidence(
        evidence_type="exit",
        statistical_method="consecutive_confirmation",
        sample_size=2,
        confidence_level=1.0,
        fdr_corrected=False,
        consecutive_confirmations=2,
    )


class TestLoadEntryEvidenceReadPath:
    """load_entry_evidence round-trips through the canonical write path."""

    def test_returns_evidence_when_envelope_present(self):
        conn = _setup_conn()
        entry = _entry_evidence()
        _seed_entry_event(conn, "t-1", decision_evidence=entry)
        loaded = load_entry_evidence(conn, "t-1")
        assert loaded == entry

    def test_returns_none_when_no_event(self):
        conn = _setup_conn()
        assert load_entry_evidence(conn, "t-nonexistent") is None

    def test_returns_none_when_backfill_reason_only(self):
        """Legacy-backfill path (T4.1b exit_lifecycle scope addition) emits
        reason sentinel instead of envelope. Audit must skip cleanly."""
        conn = _setup_conn()
        _seed_entry_event(
            conn, "t-backfill",
            decision_evidence_reason="backfill_legacy_position",
        )
        assert load_entry_evidence(conn, "t-backfill") is None

    def test_returns_none_when_neither_key_present(self):
        """Pre-T4.1b entry events have no evidence key at all."""
        conn = _setup_conn()
        _seed_entry_event(conn, "t-legacy")
        assert load_entry_evidence(conn, "t-legacy") is None

    def test_returns_none_when_envelope_malformed_json(self):
        """DB corruption / partial write: envelope string is non-parseable JSON.
        Raw INSERT bypasses the builder to construct the rare but possible
        corrupted state; append-only trigger blocks UPDATE so INSERT is the
        only way to simulate this."""
        conn = _setup_conn()
        corrupted_payload = json.dumps(
            {"decision_evidence_envelope": "{not valid json"}
        )
        _raw_insert_entry_posted(conn, "t-malformed", corrupted_payload)
        assert load_entry_evidence(conn, "t-malformed") is None

    def test_returns_none_when_envelope_unknown_version(self):
        """Future-version rollback: payload was written by a newer runtime
        and older runtime is reading it back. from_json rejects unknown
        version; audit must skip, not crash."""
        conn = _setup_conn()
        future_envelope = json.dumps({"contract_version": 99, "fields": {}})
        future_payload = json.dumps({"decision_evidence_envelope": future_envelope})
        _raw_insert_entry_posted(conn, "t-futurev", future_payload)
        assert load_entry_evidence(conn, "t-futurev") is None

    def test_position_open_intent_envelope_is_ignored(self):
        """POSITION_OPEN_INTENT must never carry the envelope per T4.1b
        design. Even if a future edit (or misconfiguration) attaches one
        there, the read path consults ONLY ENTRY_ORDER_POSTED. Seed an
        OPEN_INTENT smuggling the envelope; expect load_entry_evidence to
        return None because no ENTRY_ORDER_POSTED event exists at all."""
        conn = _setup_conn()
        smuggled_payload = json.dumps(
            {"decision_evidence_envelope": _entry_evidence().to_json()}
        )
        _raw_insert_position_open_intent(conn, "t-intent", smuggled_payload)
        assert load_entry_evidence(conn, "t-intent") is None


class TestD4AsymmetryDetection:
    """Canonical D4 signal: weak exit vs strong entry raises."""

    def test_asymmetric_burden_flags_sample_size_and_fdr(self):
        entry = _entry_evidence()
        exit_weak = _exit_evidence_weak()
        with pytest.raises(EvidenceAsymmetryError) as excinfo:
            exit_weak.assert_symmetric_with(entry)
        msg = str(excinfo.value)
        assert "sample_size" in msg
        assert "FDR" in msg

    def test_matching_symmetric_burden_does_not_raise(self):
        entry = _entry_evidence()
        exit_strong = DecisionEvidence(
            evidence_type="exit",
            statistical_method="bootstrap_ci_bh_fdr",
            sample_size=5000,
            confidence_level=0.10,
            fdr_corrected=True,
            consecutive_confirmations=2,
        )
        exit_strong.assert_symmetric_with(entry)

    def test_phase1_weak_exit_construction_is_contract_valid(self):
        ev = _exit_evidence_weak()
        assert ev.evidence_type == "exit"
        assert ev.sample_size == 2
        assert ev.fdr_corrected is False
        assert ev.consecutive_confirmations == 2
        # to_json / from_json round-trip survives the weak-burden shape.
        assert DecisionEvidence.from_json(ev.to_json()) == ev


class TestLoadEntryEvidenceSourceIsLatest:
    """When multiple ENTRY_ORDER_POSTED events exist (retry / re-emit),
    load_entry_evidence honors the sequence_no ordering and returns the
    earliest one's evidence. Retries with the same decision_id carry
    identical envelopes (T4.1b idempotency), so either is equivalent."""

    def test_single_entry_event_returns_evidence(self):
        conn = _setup_conn()
        entry = _entry_evidence()
        _seed_entry_event(conn, "t-single", decision_evidence=entry)
        assert load_entry_evidence(conn, "t-single") == entry
