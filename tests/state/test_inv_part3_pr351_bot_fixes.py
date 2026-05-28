# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-refactor-part2-findings.md
#   + Part-3 audit (PR #352) — Copilot review bugs on PR #350/#351.
"""Antibody regressions for the Part-3 audit / Copilot review bugs.

Each test FAILS on the pre-fix code and PASSES after the PR #352 fix.

  bot #4 (PR #350, lifecycle_events.projection_updated_at): a later chain
         ABSENCE observation must advance updated_at even when an older
         positive chain_verified_at exists. First-non-empty ordering lost it.
  bot #6 (PR #351, build_venue_position_observed_canonical_write): the durable
         projection must carry recovery_authority=balance_only even when the
         runtime Position never set the attribute, so the position_current row
         matches the event payload.
  bot #7 (PR #351, projection.upsert_position_current): the ON CONFLICT update
         path must refresh the authority columns; the rescue path UPDATEs an
         existing pending-entry row, so INSERT-only authority leaves them stale.
  bot #5 (PR #351, harvester.maybe_write_learning_pair): the per-position
         authority gate must read position_current from the TRADES DB, not the
         forecasts connection it is handed.
"""

from __future__ import annotations

import sqlite3
import types

import pytest

from src.engine.lifecycle_events import (
    build_venue_position_observed_canonical_write,
    projection_updated_at,
)
from src.state.projection import (
    CANONICAL_POSITION_CURRENT_COLUMNS,
    ordered_values,
    upsert_position_current,
)


class _Pos:
    """Minimal runtime-Position stub for builder/projection tests."""

    def __init__(self, **kw):
        self.trade_id = "t-1"
        self.market_id = "m-1"
        self.city = "London"
        self.cluster = "eu"
        self.target_date = "2026-06-01"
        self.bin_label = "ge_20"
        self.direction = "yes"
        self.unit = "C"
        self.size_usd = 10.0
        self.shares = 20.0
        self.cost_basis_usd = 9.0
        self.entry_price = 0.45
        self.p_posterior = 0.6
        self.decision_snapshot_id = "snap-1"
        self.entry_method = "limit"
        self.strategy_key = "s-1"
        self.chain_state = "synced"
        self.token_id = "tok-1"
        self.condition_id = "cond-1"
        self.order_id = "o-1"
        self.order_status = "filled"
        self.state = "entered"
        self.exit_state = ""
        self.entered_at = "2026-05-27T10:00:00+00:00"
        self.env = "test"
        for k, v in kw.items():
            setattr(self, k, v)


# ---------- bot #4 — projection_updated_at uses most-recent, not priority ----------

def test_later_absence_advances_updated_at_over_older_verification() -> None:
    pos = _Pos(
        chain_verified_at="2026-05-27T10:00:00+00:00",
        last_chain_absence_observed_at="2026-05-27T18:00:00+00:00",
    )
    # The absence is newer; updated_at must reflect it.
    assert projection_updated_at(pos) == "2026-05-27T18:00:00+00:00"


def test_older_absence_does_not_override_newer_verification() -> None:
    pos = _Pos(
        chain_verified_at="2026-05-27T18:00:00+00:00",
        last_chain_absence_observed_at="2026-05-27T10:00:00+00:00",
    )
    assert projection_updated_at(pos) == "2026-05-27T18:00:00+00:00"


# ---------- bot #6 — rescue projection carries balance_only authority ----------

def test_venue_position_observed_projection_forces_balance_only_authority() -> None:
    # Position deliberately has NO recovery_authority / fill_authority attrs.
    pos = _Pos(chain_state="chain_only", fill_authority="", chain_verified_at="2026-05-27T11:00:00+00:00")
    # Force ACTIVE phase so the builder accepts it.
    pos.state = "entered"
    events, projection = build_venue_position_observed_canonical_write(pos, venue_observed_at="2026-05-27T12:00:00Z", sequence_no=4)
    assert projection["recovery_authority"] == "balance_only"
    assert projection["fill_authority"] == "venue_position_observed"
    # And the event payload agrees with the durable projection.
    import json

    payload = json.loads(events[0]["payload_json"])
    assert payload["recovery_authority"] == "balance_only"


# ---------- bot #7 — upsert conflict path refreshes authority columns ----------

def _mk_projection(position_id: str, *, fill_authority, recovery_authority) -> dict:
    base = {col: None for col in CANONICAL_POSITION_CURRENT_COLUMNS}
    base.update(
        position_id=position_id,
        phase="active",
        trade_id=position_id,
        market_id="m",
        city="London",
        cluster="eu",
        target_date="2026-06-01",
        bin_label="b",
        direction="yes",
        unit="C",
        size_usd=1.0,
        shares=1.0,
        cost_basis_usd=1.0,
        entry_price=0.5,
        p_posterior=0.5,
        decision_snapshot_id="snap",
        entry_method="limit",
        strategy_key="s",
        chain_state="synced",
        token_id="tok-7",
        condition_id="cond-7",
        order_id="o",
        order_status="filled",
        updated_at="2026-05-27T10:00:00+00:00",
        temperature_metric="high",
        fill_authority=fill_authority,
        recovery_authority=recovery_authority,
        chain_shares=2.0,
        chain_seen_at="2026-05-27T10:00:00+00:00",
        chain_absence_at=None,
    )
    return base


def _fresh_position_current(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "pc.db"))
    cols_sql = ", ".join(f"{c} TEXT" if c not in {
        "size_usd", "shares", "cost_basis_usd", "entry_price", "p_posterior", "chain_shares",
    } else f"{c} REAL" for c in CANONICAL_POSITION_CURRENT_COLUMNS)
    conn.execute(f"CREATE TABLE position_current ({cols_sql}, PRIMARY KEY(position_id))")
    return conn


def test_upsert_conflict_refreshes_fill_authority(tmp_path) -> None:
    conn = _fresh_position_current(tmp_path)
    # First INSERT — verified.
    upsert_position_current(conn, _mk_projection(
        "p-7", fill_authority="venue_confirmed_full", recovery_authority=None))
    # Same position_id, now degraded recovery — must UPDATE the authority columns.
    upsert_position_current(conn, _mk_projection(
        "p-7", fill_authority="venue_position_observed", recovery_authority="balance_only"))
    row = conn.execute(
        "SELECT fill_authority, recovery_authority FROM position_current WHERE position_id='p-7'"
    ).fetchone()
    assert row == ("venue_position_observed", "balance_only")


# ---------- bot #5 — harvester gate reads the trades DB, not forecasts conn ----------

def test_learning_writer_gate_uses_trades_connection(monkeypatch) -> None:
    """maybe_write_learning_pair must route the position_current authority join
    through a trades-DB connection, never the forecasts conn it is handed."""
    import src.execution.harvester as harvester

    # Make all upstream gates pass so we reach the per-position gate.
    monkeypatch.setattr(harvester, "_context_training_allowed", lambda ctx: True)
    monkeypatch.setattr(harvester, "_causality_allows_learning", lambda s: True)
    monkeypatch.setattr(harvester, "_is_training_forecast_source", lambda v: True)

    used = {"trades_conn": False}

    class _TradesConn:
        def execute(self, *a, **k):
            used["trades_conn"] = True
            raise sqlite3.OperationalError("sentinel — gate must fail closed here")

        def close(self):
            pass

    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only", lambda: _TradesConn()
    )

    forecasts_conn = object()  # would explode if .execute were called on it
    written = harvester.maybe_write_learning_pair(
        forecasts_conn,
        types.SimpleNamespace(name="London"),
        "2026-06-01",
        "ge_20",
        ["ge_20", "lt_20"],
        {
            "forecast_model_id": "ens_v3",
            "snapshot_training_allowed": True,
            "snapshot_causality_status": "OK",
            "decision_snapshot_id": "snap-1",
        },
        "high",
    )
    assert used["trades_conn"] is True
    assert written == 0  # fail-closed via the trades-conn sentinel, never touched forecasts conn
