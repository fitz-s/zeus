# Created: 2026-04-17
# Last reused/audited: 2026-05-08
# Lifecycle: created=2026-04-17; last_reviewed=2026-05-08; last_reused=2026-05-08
# Authority basis: object-meaning invariance Wave26 rescue audit compatibility; PR90 gitleaks fixture hygiene.
# Purpose: Guard rescue_events_v2 schema and chain reconciliation dual-write audit compatibility.
# Reuse: Run after rescue_events_v2 schema, chain reconciliation rescue, or rescue audit compatibility changes.
"""B063: rescue_events_v2 audit table tests.

Verifies the new rescue_events_v2 DDL, log_rescue_event helper, and
chain_reconciliation._emit_rescue_event dual-write integration.

Design:
  - temperature_metric MUST be binary ('high' | 'low') per SD-1.
  - Provenance ambiguity rides on the `authority` column per SD-H —
    VERIFIED when Position was fully materialized, UNVERIFIED when the
    Position's temperature_metric fell back to a default.
  - Table is exempt from the DT#1 commit_then_export choke point; it
    is the authoritative audit record and must be durable before the
    cycle acknowledges the rescue outcome.
"""
from __future__ import annotations

import sqlite3
import unittest


class TestRescueEventsV2Schema(unittest.TestCase):
    """Schema-level checks for rescue_events_v2."""

    def _apply_schema(self) -> sqlite3.Connection:
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_v2_schema(conn)
        return conn

    def test_table_is_created_by_apply_v2_schema(self) -> None:
        conn = self._apply_schema()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='rescue_events_v2'"
        ).fetchone()
        self.assertIsNotNone(row, "rescue_events_v2 table was not created")

    def test_temperature_metric_is_binary(self) -> None:
        """SD-1 invariant: CHECK (temperature_metric IN ('high','low'))."""
        conn = self._apply_schema()
        # 'high' accepted
        conn.execute(
            "INSERT INTO rescue_events_v2 "
            "(trade_id, temperature_metric, chain_state, reason, occurred_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("trade-h", "high", "PENDING_ENTRY", "test", "2026-04-17T00:00:00Z"),
        )
        # 'low' accepted
        conn.execute(
            "INSERT INTO rescue_events_v2 "
            "(trade_id, temperature_metric, chain_state, reason, occurred_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("trade-l", "low", "PENDING_ENTRY", "test", "2026-04-17T00:00:00Z"),
        )
        # 'unknown' REJECTED at the DB layer
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rescue_events_v2 "
                "(trade_id, temperature_metric, chain_state, reason, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("trade-u", "unknown", "PENDING_ENTRY", "test", "2026-04-17T00:00:00Z"),
            )

    def test_authority_is_tri_state(self) -> None:
        conn = self._apply_schema()
        for auth in ("VERIFIED", "UNVERIFIED", "RECONSTRUCTED"):
            conn.execute(
                "INSERT INTO rescue_events_v2 "
                "(trade_id, temperature_metric, authority, chain_state, reason, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"trade-{auth}", "high", auth, "PENDING_ENTRY", "test", f"2026-04-17T0{ord(auth[0])%10}:00:00Z"),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rescue_events_v2 "
                "(trade_id, temperature_metric, authority, chain_state, reason, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("trade-bogus", "high", "BOGUS", "PENDING_ENTRY", "test", "2026-04-17T00:00:00Z"),
            )

    def test_causality_status_enum(self) -> None:
        conn = self._apply_schema()
        for status in (
            "OK",
            "N/A_CAUSAL_DAY_ALREADY_STARTED",
            "N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON",
            "REJECTED_BOUNDARY_AMBIGUOUS",
            "UNKNOWN",
        ):
            conn.execute(
                "INSERT INTO rescue_events_v2 "
                "(trade_id, temperature_metric, causality_status, chain_state, reason, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"trade-{hash(status) & 0xFFFF}", "high", status, "PENDING_ENTRY", "test", f"2026-04-17T{abs(hash(status)) % 24:02d}:00:00Z"),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rescue_events_v2 "
                "(trade_id, temperature_metric, causality_status, chain_state, reason, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("trade-bogus-cs", "high", "MADE_UP", "PENDING_ENTRY", "test", "2026-04-17T23:30:00Z"),
            )


class TestLogRescueEventHelper(unittest.TestCase):
    """Unit tests for src.state.db.log_rescue_event."""

    def _conn(self) -> sqlite3.Connection:
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_v2_schema(conn)
        return conn

    def test_writes_row_with_verified_authority(self) -> None:
        from src.state.db import log_rescue_event
        conn = self._conn()
        log_rescue_event(
            conn,
            trade_id="t-001",
            position_id="t-001",
            chain_state="ACTIVE",
            reason="chain_reconciliation_rescue",
            occurred_at="2026-04-17T10:00:00Z",
            temperature_metric="low",
            causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED",
            authority="VERIFIED",
            authority_source="position_materialized",
        )
        conn.commit()
        row = conn.execute("SELECT * FROM rescue_events_v2 WHERE trade_id='t-001'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["temperature_metric"], "low")
        self.assertEqual(row["causality_status"], "N/A_CAUSAL_DAY_ALREADY_STARTED")
        self.assertEqual(row["authority"], "VERIFIED")
        self.assertEqual(row["authority_source"], "position_materialized")

    def test_invalid_metric_is_skipped_not_raised(self) -> None:
        """SD-1 enforcement at the Python-side helper.

        The helper must NOT allow an out-of-domain temperature_metric
        to reach the CHECK constraint (which would raise IntegrityError
        and break the caller). Instead it logs an error and returns
        without writing.
        """
        from src.state.db import log_rescue_event
        conn = self._conn()
        log_rescue_event(
            conn,
            trade_id="t-skip",
            chain_state="ACTIVE",
            reason="test",
            occurred_at="2026-04-17T10:00:00Z",
            temperature_metric="unknown",  # out-of-domain
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM rescue_events_v2 WHERE trade_id='t-skip'"
        ).fetchone()["c"]
        self.assertEqual(count, 0, "out-of-domain metric must not produce a row")

    def test_conn_none_is_noop(self) -> None:
        from src.state.db import log_rescue_event
        # Should not raise.
        log_rescue_event(
            None,
            trade_id="t-none",
            chain_state="ACTIVE",
            reason="test",
            occurred_at="2026-04-17T10:00:00Z",
            temperature_metric="high",
        )

    def test_missing_table_is_logged_not_raised(self) -> None:
        """Legacy DBs without apply_v2_schema applied must not crash the caller."""
        from src.state.db import log_rescue_event
        conn = sqlite3.connect(":memory:")
        # Deliberately NOT applying v2 schema.
        # Must not raise.
        log_rescue_event(
            conn,
            trade_id="t-legacy",
            chain_state="ACTIVE",
            reason="test",
            occurred_at="2026-04-17T10:00:00Z",
            temperature_metric="high",
        )

    def test_duplicate_trade_occurred_at_is_idempotent(self) -> None:
        """UNIQUE(trade_id, occurred_at) constraint produces IntegrityError,
        which the helper swallows at INFO level (legitimate retry)."""
        from src.state.db import log_rescue_event
        conn = self._conn()
        for _ in range(2):
            log_rescue_event(
                conn,
                trade_id="t-dup",
                chain_state="ACTIVE",
                reason="test",
                occurred_at="2026-04-17T10:00:00Z",
                temperature_metric="high",
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM rescue_events_v2 WHERE trade_id='t-dup'"
        ).fetchone()["c"]
        self.assertEqual(count, 1, "duplicate insert must be a no-op")


class TestEmitRescueEventIntegration(unittest.TestCase):
    """Integration: `_emit_rescue_event` dual-writes CHAIN_RESCUE_AUDIT
    (to position_events) AND rescue_events_v2 row with correct
    provenance authority based on the Position's temperature_metric.

    Keeps the original position_events write intact (legacy consumers
    like test_live_safety_invariants continue to see CHAIN_RESCUE_AUDIT)
    and adds the new rescue_events_v2 row with the typed metadata.
    """

    def _make_conn_with_position_events(self) -> sqlite3.Connection:
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        apply_v2_schema(conn)
        # Minimal position_events table — just enough for the CHAIN_RESCUE_AUDIT
        # INSERT to succeed; matches the real schema's required columns.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL,
                sequence_no INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload TEXT,
                source_module TEXT
            )
            """
        )
        conn.commit()
        return conn

    def _make_canonical_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        return conn

    def test_canonical_position_events_schema_does_not_suppress_rescue_v2(self) -> None:
        """Canonical position_events no longer has the legacy payload/event
        shape; a skipped legacy CHAIN_RESCUE_AUDIT write must not suppress
        the structured rescue_events_v2 authority row.
        """
        from src.engine.lifecycle_events import build_entry_canonical_write
        from src.state.chain_reconciliation import ChainPosition, reconcile
        from src.state.db import append_many_and_project
        from src.state.portfolio import Position, PortfolioState

        conn = self._make_canonical_conn()
        position = Position(
            trade_id="t-canonical-rescue-v2",
            market_id="m-canonical-rescue-v2",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-04-17",
            bin_label="39-40°F",
            direction="buy_yes",
            unit="F",
            env="live",
            size_usd=10.0,
            entry_price=0.40,
            p_posterior=0.61,
            decision_snapshot_id="snap-canonical-rescue-v2",
            strategy_key="center_buy",
            strategy="center_buy",
            edge_source="center_buy",
            order_id="ord-canonical-rescue-v2",
            entry_order_id="ord-canonical-rescue-v2",
            order_status="pending",
            order_posted_at="2026-04-17T10:00:00Z",
            state="pending_tracked",
            chain_state="local_only",
            token_id="t1",
        )
        events, projection = build_entry_canonical_write(
            position,
            decision_id="dec-canonical-rescue-v2",
            source_module="tests.test_b063_rescue_events_v2",
        )
        append_many_and_project(conn, events, projection)

        stats = reconcile(
            PortfolioState(positions=[position]),
            [
                ChainPosition(
                    token_id="t1",
                    size=25.0,
                    avg_price=0.44,
                    cost=11.0,
                    condition_id="cond-canonical-rescue-v2",
                )
            ],
            conn=conn,
        )

        self.assertEqual(stats["rescued_pending"], 1)
        rescue_row = conn.execute(
            """
            SELECT trade_id, authority
            FROM rescue_events_v2
            WHERE trade_id = ? AND authority = ?
            """,
            ("t-canonical-rescue-v2", "VERIFIED"),
        ).fetchone()
        self.assertIsNotNone(rescue_row)
        legacy_count = conn.execute(
            "SELECT COUNT(*) AS c FROM position_events WHERE event_type = ?",
            ("CHAIN_RESCUE_AUDIT",),
        ).fetchone()["c"]
        self.assertEqual(legacy_count, 0)

    def test_verified_when_position_has_valid_metric(self) -> None:
        """Position materialized through MetricIdentity spine → VERIFIED.

        This test uses the shared `resolve_rescue_authority` helper that
        the live `_emit_rescue_event` closure also imports, so logic drift
        between prod and test is impossible (B063 P1 critic fix).
        """
        from types import SimpleNamespace
        from src.state.chain_reconciliation import resolve_rescue_authority
        from src.state.db import log_rescue_event

        conn = self._make_conn_with_position_events()
        position = SimpleNamespace(
            trade_id="t-verified",
            temperature_metric="low",
            chain_state="PENDING_ENTRY",
            shares=10.0,
            entry_price=0.42,
        )
        metric, authority, authority_source = resolve_rescue_authority(position)
        log_rescue_event(
            conn,
            trade_id=position.trade_id,
            chain_state=str(position.chain_state),
            reason="chain_reconciliation_rescue",
            occurred_at="2026-04-17T11:00:00Z",
            temperature_metric=metric,
            causality_status="UNKNOWN",
            authority=authority,
            authority_source=authority_source,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM rescue_events_v2 WHERE trade_id='t-verified'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["temperature_metric"], "low")
        self.assertEqual(row["authority"], "VERIFIED")
        self.assertEqual(row["authority_source"], "position_materialized")

    def test_unverified_when_position_missing_metric(self) -> None:
        """Quarantine placeholder or JSON-reconstructed Position with a
        missing or out-of-domain temperature_metric → UNVERIFIED + high
        fallback, per SD-1 (binary) + SD-H (provenance on authority).

        Uses the shared `resolve_rescue_authority` helper — same rule as prod.
        """
        from types import SimpleNamespace
        from src.state.chain_reconciliation import resolve_rescue_authority
        from src.state.db import log_rescue_event

        conn = self._make_conn_with_position_events()
        position = SimpleNamespace(
            trade_id="t-unverified",
            temperature_metric=None,  # missing
            chain_state="PENDING_ENTRY",
        )
        metric, authority, authority_source = resolve_rescue_authority(position)
        log_rescue_event(
            conn,
            trade_id=position.trade_id,
            chain_state=str(position.chain_state),
            reason="chain_reconciliation_rescue",
            occurred_at="2026-04-17T12:00:00Z",
            temperature_metric=metric,
            causality_status="UNKNOWN",
            authority=authority,
            authority_source=authority_source,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM rescue_events_v2 WHERE trade_id='t-unverified'"
        ).fetchone()
        self.assertIsNotNone(row)
        # SD-1: concrete high tag, not tri-state.
        self.assertEqual(row["temperature_metric"], "high")
        # SD-H: authority carries the provenance doubt.
        self.assertEqual(row["authority"], "UNVERIFIED")
        self.assertIn("position_missing_metric", row["authority_source"])


class TestResolveRescueAuthority(unittest.TestCase):
    """Unit tests for the shared `resolve_rescue_authority` helper.

    This helper is the single source of truth for the authority rule;
    logic bugs here propagate to every rescue_events_v2 row, so it has
    its own focused coverage (B063 P1 critic fix).
    """

    def _pos(self, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(**kwargs)

    def test_high_metric_is_verified(self) -> None:
        from src.state.chain_reconciliation import resolve_rescue_authority
        self.assertEqual(
            resolve_rescue_authority(self._pos(temperature_metric="high")),
            ("high", "VERIFIED", "position_materialized"),
        )

    def test_low_metric_is_verified(self) -> None:
        from src.state.chain_reconciliation import resolve_rescue_authority
        self.assertEqual(
            resolve_rescue_authority(self._pos(temperature_metric="low")),
            ("low", "VERIFIED", "position_materialized"),
        )

    def test_none_metric_falls_back_unverified(self) -> None:
        from src.state.chain_reconciliation import resolve_rescue_authority
        metric, authority, source = resolve_rescue_authority(
            self._pos(temperature_metric=None)
        )
        self.assertEqual(metric, "high")
        self.assertEqual(authority, "UNVERIFIED")
        self.assertIn("position_missing_metric", source)
        self.assertIn("None", source)

    def test_empty_string_metric_falls_back_unverified(self) -> None:
        """Edge case critic raised: `""` is not in {"high","low"} so must UNVERIFIED."""
        from src.state.chain_reconciliation import resolve_rescue_authority
        metric, authority, source = resolve_rescue_authority(
            self._pos(temperature_metric="")
        )
        self.assertEqual(metric, "high")
        self.assertEqual(authority, "UNVERIFIED")

    def test_missing_attribute_falls_back_unverified(self) -> None:
        """Position object with no temperature_metric attribute at all."""
        from src.state.chain_reconciliation import resolve_rescue_authority
        metric, authority, source = resolve_rescue_authority(self._pos(trade_id="t"))
        self.assertEqual(metric, "high")
        self.assertEqual(authority, "UNVERIFIED")

    def test_unknown_metric_value_falls_back_unverified(self) -> None:
        """Hypothetical out-of-domain value must not be VERIFIED."""
        from src.state.chain_reconciliation import resolve_rescue_authority
        metric, authority, source = resolve_rescue_authority(
            self._pos(temperature_metric="unknown")
        )
        self.assertEqual(metric, "high")
        self.assertEqual(authority, "UNVERIFIED")
        self.assertIn("'unknown'", source)


if __name__ == "__main__":
    unittest.main()
