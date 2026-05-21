# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T3 acceptance criteria
"""Tests for SettlementCaptureVerifier — 3-valued coherence verdict."""
from __future__ import annotations

import sqlite3

import pytest

from src.contracts.settlement_capture_verifier import (
    SettlementCaptureVerifier,
    VerificationResult,
    check_pre_promotion_gate,
)


# ---------------------------------------------------------------------------
# Helper — in-memory DB with the verifications table
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE settlement_capture_verifications (
            verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL
                CHECK (temperature_metric IN ('high', 'low')),
            fact_known_time TEXT,
            source_published_time TEXT,
            venue_resolved_time TEXT,
            redeemed_time TEXT,
            coherence_verdict TEXT NOT NULL
                CHECK (coherence_verdict IN ('COHERENT', 'INCOHERENT', 'INCOMPLETE')),
            incoherence_reason TEXT,
            evidence_tier TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            UNIQUE(city, target_date, temperature_metric)
        )
    """)
    return conn


_VERIFIER = SettlementCaptureVerifier()


# ---------------------------------------------------------------------------
# T3: 3-valued verdict logic
# ---------------------------------------------------------------------------

class TestComputeVerdict:
    def test_coherent_all_four_in_order(self):
        verdict, reason = SettlementCaptureVerifier.compute_verdict(
            fact_known_time="2026-07-01T10:00:00Z",
            source_published_time="2026-07-01T12:00:00Z",
            venue_resolved_time="2026-07-01T14:00:00Z",
            redeemed_time="2026-07-01T16:00:00Z",
        )
        assert verdict == "COHERENT"
        assert reason is None

    def test_incoherent_venue_before_source(self):
        verdict, reason = SettlementCaptureVerifier.compute_verdict(
            fact_known_time="2026-07-01T10:00:00Z",
            source_published_time="2026-07-01T15:00:00Z",
            venue_resolved_time="2026-07-01T13:00:00Z",  # BEFORE source
            redeemed_time="2026-07-01T16:00:00Z",
        )
        assert verdict == "INCOHERENT"
        assert reason is not None
        assert "venue_resolved" in reason
        assert "source_published" in reason

    def test_incomplete_missing_one(self):
        verdict, reason = SettlementCaptureVerifier.compute_verdict(
            fact_known_time="2026-07-01T10:00:00Z",
            source_published_time="2026-07-01T12:00:00Z",
            venue_resolved_time=None,
            redeemed_time=None,
        )
        assert verdict == "INCOMPLETE"
        assert reason is None

    def test_incomplete_all_none(self):
        verdict, reason = SettlementCaptureVerifier.compute_verdict(None, None, None, None)
        assert verdict == "INCOMPLETE"

    def test_incoherent_fact_after_source(self):
        verdict, reason = SettlementCaptureVerifier.compute_verdict(
            fact_known_time="2026-07-01T14:00:00Z",  # AFTER source
            source_published_time="2026-07-01T12:00:00Z",
            venue_resolved_time="2026-07-01T15:00:00Z",
            redeemed_time="2026-07-01T16:00:00Z",
        )
        assert verdict == "INCOHERENT"
        assert "fact_known" in (reason or "")


class TestVerifyMethod:
    def test_verify_dict_coherent(self):
        result = _VERIFIER.verify({
            "city": "Chicago",
            "target_date": "2026-07-04",
            "temperature_metric": "high",
            "fact_known_time": "2026-07-04T10:00:00Z",
            "source_published_time": "2026-07-04T12:00:00Z",
            "venue_resolved_time": "2026-07-04T14:00:00Z",
            "redeemed_time": "2026-07-04T16:00:00Z",
        })
        assert result.coherence_verdict == "COHERENT"
        assert result.city == "Chicago"
        assert isinstance(result, VerificationResult)

    def test_verify_dict_incoherent(self):
        result = _VERIFIER.verify({
            "city": "Chicago",
            "target_date": "2026-07-04",
            "temperature_metric": "high",
            "fact_known_time": "2026-07-04T10:00:00Z",
            "source_published_time": "2026-07-04T15:00:00Z",
            "venue_resolved_time": "2026-07-04T13:00:00Z",  # before source
            "redeemed_time": "2026-07-04T16:00:00Z",
        })
        assert result.coherence_verdict == "INCOHERENT"
        assert result.incoherence_reason is not None

    def test_verify_dict_incomplete(self):
        result = _VERIFIER.verify({
            "city": "Dallas",
            "target_date": "2026-07-05",
            "temperature_metric": "low",
            "fact_known_time": "2026-07-05T10:00:00Z",
            "source_published_time": "2026-07-05T12:00:00Z",
        })
        assert result.coherence_verdict == "INCOMPLETE"


class TestWriteResult:
    def test_write_coherent(self):
        conn = _make_conn()
        result = _VERIFIER.verify({
            "city": "Chicago",
            "target_date": "2026-07-04",
            "temperature_metric": "high",
            "fact_known_time": "2026-07-04T10:00:00Z",
            "source_published_time": "2026-07-04T12:00:00Z",
            "venue_resolved_time": "2026-07-04T14:00:00Z",
            "redeemed_time": "2026-07-04T16:00:00Z",
        })
        _VERIFIER.write_result(result, conn=conn)
        row = conn.execute(
            "SELECT coherence_verdict FROM settlement_capture_verifications WHERE city='Chicago'"
        ).fetchone()
        assert row is not None
        assert row[0] == "COHERENT"

    def test_write_no_raw_commit(self):
        """write_result with caller-conn must not commit the connection.
        After a write, we can roll back and the row disappears — proving no commit happened.
        """
        conn = _make_conn()
        conn.execute("BEGIN")
        result = _VERIFIER.verify({
            "city": "Phoenix",
            "target_date": "2026-08-01",
            "temperature_metric": "high",
            "fact_known_time": "2026-08-01T10:00:00Z",
            "source_published_time": "2026-08-01T12:00:00Z",
            "venue_resolved_time": "2026-08-01T14:00:00Z",
            "redeemed_time": "2026-08-01T16:00:00Z",
        })
        _VERIFIER.write_result(result, conn=conn)
        conn.execute("ROLLBACK")
        row = conn.execute(
            "SELECT * FROM settlement_capture_verifications WHERE city='Phoenix'"
        ).fetchone()
        assert row is None, "write_result with conn= must not self-commit"

    def test_idempotent_upsert(self):
        conn = _make_conn()
        base = {
            "city": "Miami",
            "target_date": "2026-07-10",
            "temperature_metric": "high",
            "fact_known_time": "2026-07-10T10:00:00Z",
            "source_published_time": "2026-07-10T12:00:00Z",
            "venue_resolved_time": "2026-07-10T14:00:00Z",
            "redeemed_time": "2026-07-10T16:00:00Z",
        }
        r1 = _VERIFIER.verify(base)
        _VERIFIER.write_result(r1, conn=conn)
        conn.commit()
        _VERIFIER.write_result(r1, conn=conn)
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM settlement_capture_verifications WHERE city='Miami'"
        ).fetchone()[0]
        assert count == 1


class TestPrePromotionGate:
    def test_gate_below_threshold(self):
        conn = _make_conn()
        result = check_pre_promotion_gate("Chicago", "high", conn=conn, threshold=3)
        assert result is False

    def test_gate_meets_threshold(self):
        conn = _make_conn()
        for i in range(3):
            r = _VERIFIER.verify({
                "city": "Chicago",
                "target_date": f"2026-07-{i + 1:02d}",
                "temperature_metric": "high",
                "fact_known_time": "2026-07-01T10:00:00Z",
                "source_published_time": "2026-07-01T12:00:00Z",
                "venue_resolved_time": "2026-07-01T14:00:00Z",
                "redeemed_time": "2026-07-01T16:00:00Z",
            })
            _VERIFIER.write_result(r, conn=conn)
        conn.commit()
        result = check_pre_promotion_gate("Chicago", "high", conn=conn, threshold=3)
        assert result is True
