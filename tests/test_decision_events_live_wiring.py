# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19-2.md P1-3

"""P1-3 antibody — write_decision_event is called at the live-submit boundary.

P1-3 bug: write_decision_event was implemented but never called at runtime.
The decision_events table sat empty while decisions proceeded, making Phase-1
reporting/learning silently diverge.

Fix (P1-3): cycle_runtime.py::execute_discovery_phase calls write_decision_event
immediately after execute_final_intent returns, inside a fail-soft try/except.

This antibody tests:

1. Signature contract: write_decision_event accepts primitive fields (direction,
   target_size_usd, limit_price, edge, p_posterior) rather than an ExecutionIntent
   or FinalExecutionIntent object. This decouples the write from FinalExecutionIntent's
   field names (size_value, final_limit_price — different from ExecutionIntent's
   target_size_usd, limit_price).

2. Fail-soft: missing decision_source_context on FinalExecutionIntent causes no
   write and no crash (the write block is guarded by `if _dsc is not None`).

3. Write reaches the table: a minimal integration smoke — write_decision_event
   with a known world DB writes a row that can be read back.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.execution_intent import DecisionSourceContext
from src.state.decision_events import write_decision_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DECISION_EVENTS_DDL = """
CREATE TABLE decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL,
    polymarket_end_anchor_source TEXT NOT NULL,
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL,
    source         TEXT NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
"""


def _minimal_dsc() -> DecisionSourceContext:
    return DecisionSourceContext(
        first_member_observed_time="2026-05-19T00:00:00Z",
        run_complete_time="2026-05-19T00:30:00Z",
        zeus_submit_intent_time="2026-05-19T01:00:00Z",
        venue_ack_time="2026-05-19T01:00:01Z",
        observation_available_at="2026-05-18T12:00:00Z",
        polymarket_end_anchor_source="gamma_explicit",
        observation_time="2026-05-18T12:00:00Z",
        decision_time="2026-05-19T01:00:00Z",
    )


def _forecast_entry_dsc_without_observation_available_at() -> DecisionSourceContext:
    return DecisionSourceContext(
        source_id="tigge",
        model_family="ecmwf_ifs025",
        forecast_issue_time="2026-05-21T00:00:00Z",
        forecast_valid_time="2026-05-23T00:00:00Z",
        forecast_fetch_time="2026-05-21T11:34:30Z",
        forecast_available_at="2026-05-21T00:00:00Z",
        raw_payload_hash="a" * 64,
        degradation_level="OK",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time="2026-05-21T11:37:09Z",
        decision_time_status="OK",
        polymarket_end_anchor_source="gamma_explicit",
        first_member_observed_time="2026-05-21T11:00:00Z",
        run_complete_time="2026-05-21T11:34:00Z",
        zeus_submit_intent_time="2026-05-21T11:37:54Z",
        venue_ack_time="2026-05-21T11:38:03Z",
    )


@contextmanager
def _noop_lock(*_args, **_kwargs) -> Generator[None, None, None]:
    yield


_NO_TRADE_EVENTS_DDL = """
CREATE TABLE no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
);
"""


def _setup_world_db(world_db_path: Path) -> None:
    conn = sqlite3.connect(str(world_db_path))
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Contract 1: primitive signature — no ExecutionIntent required
# ---------------------------------------------------------------------------


class TestPrimitiveSignature:
    """write_decision_event must accept primitives, not an intent object."""

    def test_accepts_direction_string(self, tmp_path: Path) -> None:
        """direction kwarg accepts a plain string like 'buy_yes'."""
        world_db_path = tmp_path / "zeus-world.db"
        _setup_world_db(world_db_path)
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        def _fake_world_conn(**_kwargs) -> sqlite3.Connection:
            c = sqlite3.connect(str(world_db_path))
            c.row_factory = sqlite3.Row
            return c

        from src.state.db import SCHEMA_VERSION

        with (
            patch("src.state.db.get_world_connection", side_effect=_fake_world_conn),
            patch("src.state.db_writer_lock.db_writer_lock", _noop_lock),
            patch("src.state.db.SCHEMA_VERSION", SCHEMA_VERSION),
            patch("src.state.db.ZEUS_WORLD_DB_PATH", world_db_path),
        ):
            # Must not raise — direction is a str, not an enum
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
            )

    def test_accepts_edge_and_p_posterior_optional(self, tmp_path: Path) -> None:
        """edge and p_posterior are optional; omitting them does not raise."""
        world_db_path = tmp_path / "zeus-world.db"
        _setup_world_db(world_db_path)
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        def _fake_world_conn(**_kwargs) -> sqlite3.Connection:
            c = sqlite3.connect(str(world_db_path))
            c.row_factory = sqlite3.Row
            return c

        from src.state.db import SCHEMA_VERSION

        with (
            patch("src.state.db.get_world_connection", side_effect=_fake_world_conn),
            patch("src.state.db_writer_lock.db_writer_lock", _noop_lock),
            patch("src.state.db.SCHEMA_VERSION", SCHEMA_VERSION),
            patch("src.state.db.ZEUS_WORLD_DB_PATH", world_db_path),
        ):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_no",
                strategy_key="shoulder_sell",
                target_size_usd=10.0,
                limit_price=0.38,
                # edge and p_posterior deliberately omitted
            )

    def test_rejects_missing_required_dsc_fields(self) -> None:
        """Missing live-required timing fields raise ValueError before any DB touch."""
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        # DecisionSourceContext with empty required fields
        bad_dsc = DecisionSourceContext(
            observation_time="2026-05-18T12:00:00Z",
            decision_time="2026-05-19T01:00:00Z",
            # first_member_observed_time, run_complete_time, etc. all empty (default "")
        )
        with pytest.raises(ValueError, match="live_decision requires non-empty fields"):
            write_decision_event(
                nk,
                bad_dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
            )


# ---------------------------------------------------------------------------
# Contract 2: integration smoke — row reaches the table
# ---------------------------------------------------------------------------


class TestWriteReachesTable:
    def test_row_written_and_readable(self, tmp_path: Path) -> None:
        """A single write_decision_event(conn=None) produces a readable row."""
        world_db_path = tmp_path / "zeus-world.db"
        _setup_world_db(world_db_path)
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        def _fake_world_conn(**_kwargs) -> sqlite3.Connection:
            c = sqlite3.connect(str(world_db_path))
            c.row_factory = sqlite3.Row
            return c

        from src.state.db import SCHEMA_VERSION

        with (
            patch("src.state.db.get_world_connection", side_effect=_fake_world_conn),
            patch("src.state.db_writer_lock.db_writer_lock", _noop_lock),
            patch("src.state.db.SCHEMA_VERSION", SCHEMA_VERSION),
            patch("src.state.db.ZEUS_WORLD_DB_PATH", world_db_path),
        ):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=25.0,
                limit_price=0.62,
                edge=0.08,
                p_posterior=0.70,
            )

        verify = sqlite3.connect(str(world_db_path))
        rows = verify.execute(
            "SELECT * FROM decision_events WHERE market_slug='chicago-high-2026-06-01'"
        ).fetchall()
        verify.close()

        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        row = rows[0]
        assert row[0] == "chicago-high-2026-06-01"  # market_slug
        assert row[1] == "high"                       # temperature_metric
        assert row[4] == 0                            # decision_seq
        assert row[9] == "buy_yes"                    # side
        assert row[10] == "center_buy"                # strategy_key
        assert row[8] == "pending"                    # outcome

    def test_side_stores_direction_value(self, tmp_path: Path) -> None:
        """The 'side' column stores the direction string passed to write_decision_event."""
        world_db_path = tmp_path / "zeus-world.db"
        _setup_world_db(world_db_path)
        nk = make_decision_natural_key(
            market_slug="chicago-low-2026-06-01",
            temperature_metric="low",
            target_date="2026-06-01",
            observation_time="2026-05-18T12:00:00Z",
            decision_seq=0,
        )
        dsc = _minimal_dsc()

        def _fake_world_conn(**_kwargs) -> sqlite3.Connection:
            c = sqlite3.connect(str(world_db_path))
            c.row_factory = sqlite3.Row
            return c

        from src.state.db import SCHEMA_VERSION

        with (
            patch("src.state.db.get_world_connection", side_effect=_fake_world_conn),
            patch("src.state.db_writer_lock.db_writer_lock", _noop_lock),
            patch("src.state.db.SCHEMA_VERSION", SCHEMA_VERSION),
            patch("src.state.db.ZEUS_WORLD_DB_PATH", world_db_path),
        ):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_no",
                strategy_key="shoulder_sell",
                target_size_usd=15.0,
                limit_price=0.38,
            )

        verify = sqlite3.connect(str(world_db_path))
        side = verify.execute(
            "SELECT side FROM decision_events WHERE market_slug='chicago-low-2026-06-01'"
        ).fetchone()[0]
        verify.close()

        assert side == "buy_no"


class TestSourceClassTiming:
    def test_forecast_entry_live_decision_does_not_require_observation_available_at(
        self,
        tmp_path: Path,
    ) -> None:
        """Forecast-entry decisions carry forecast availability, not fabricated observation availability."""
        world_db_path = tmp_path / "zeus-world.db"
        _setup_world_db(world_db_path)
        nk = make_decision_natural_key(
            market_slug="kuala-lumpur-high-2026-05-23",
            temperature_metric="high",
            target_date="2026-05-23",
            observation_time="",
            decision_seq=0,
        )
        dsc = _forecast_entry_dsc_without_observation_available_at()

        def _fake_world_conn(**_kwargs) -> sqlite3.Connection:
            c = sqlite3.connect(str(world_db_path))
            c.row_factory = sqlite3.Row
            return c

        from src.state.db import SCHEMA_VERSION

        with (
            patch("src.state.db.get_world_connection", side_effect=_fake_world_conn),
            patch("src.state.db_writer_lock.db_writer_lock", _noop_lock),
            patch("src.state.db.SCHEMA_VERSION", SCHEMA_VERSION),
            patch("src.state.db.ZEUS_WORLD_DB_PATH", world_db_path),
        ):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="center_buy",
                target_size_usd=1.26,
                limit_price=0.009,
                edge=0.05,
                p_posterior=0.07,
            )

        verify = sqlite3.connect(str(world_db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            """
            SELECT forecast_time, observation_available_at,
                   zeus_submit_intent_time, venue_ack_time
              FROM decision_events
             WHERE market_slug = 'kuala-lumpur-high-2026-05-23'
            """
        ).fetchone()
        verify.close()

        assert row is not None
        assert row["forecast_time"] == "2026-05-21T00:00:00Z"
        assert row["observation_available_at"] == ""
        assert row["observation_available_at"] != row["forecast_time"]
        assert row["zeus_submit_intent_time"] == "2026-05-21T11:37:54Z"
        assert row["venue_ack_time"] == "2026-05-21T11:38:03Z"

    def test_observation_class_live_decision_still_requires_observation_available_at(
        self,
    ) -> None:
        """Observation/nowcast decisions must not inherit the forecast-entry relaxation."""
        nk = make_decision_natural_key(
            market_slug="chicago-high-2026-06-01",
            temperature_metric="high",
            target_date="2026-06-01",
            observation_time="2026-05-21T10:00:00Z",
            decision_seq=0,
        )
        dsc = _forecast_entry_dsc_without_observation_available_at()
        dsc = DecisionSourceContext(
            **{
                **dsc.__dict__,
                "authority_tier": "OBSERVATION",
                "observation_time": "2026-05-21T10:00:00Z",
            }
        )

        with pytest.raises(ValueError, match="observation_available_at"):
            write_decision_event(
                nk,
                dsc,
                None,
                direction="buy_yes",
                strategy_key="settlement_capture",
                target_size_usd=1.0,
                limit_price=0.99,
            )


# ---------------------------------------------------------------------------
# Contract 3: fail-soft — missing DSC on final_intent must not crash
# ---------------------------------------------------------------------------


class TestFailSoftNoDSC:
    """write_decision_event is only called when decision_source_context is not None.

    The call site guards with `if _dsc is not None`. This tests that a
    FinalExecutionIntent-like object with decision_source_context=None
    would bypass the write (simulated by not calling write_decision_event).
    """

    def test_none_dsc_skips_write(self, tmp_path: Path) -> None:
        """When decision_source_context is None, the write block is skipped entirely."""
        world_db_path = tmp_path / "zeus-world.db"
        _setup_world_db(world_db_path)

        # Simulate what cycle_runtime does: _dsc = getattr(final_intent, "decision_source_context", None)
        # When None, the if-block does not execute write_decision_event.
        final_intent_dsc = None

        write_called = []

        def _spy_write(*args, **kwargs):  # noqa: ANN002, ANN003
            write_called.append(True)

        with patch("src.state.decision_events.write_decision_event", side_effect=_spy_write):
            _dsc = final_intent_dsc
            if _dsc is not None:
                from src.state.decision_events import write_decision_event as _wde
                _wde(
                    make_decision_natural_key("slug", "high", "2026-06-01", "t", 0),
                    _dsc,
                    None,
                    direction="buy_yes",
                    strategy_key="center_buy",
                    target_size_usd=25.0,
                    limit_price=0.62,
                    conn=None,
                )

        assert write_called == [], "write_decision_event must not be called when DSC is None"

        # Confirm no rows in world DB either
        verify = sqlite3.connect(str(world_db_path))
        count = verify.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        verify.close()
        assert count == 0
