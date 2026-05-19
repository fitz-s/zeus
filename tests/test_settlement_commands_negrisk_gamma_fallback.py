# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: .omc/plans/2026-05-19-redeem-snapshot-gamma-fallback.md
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — submit_redeem() falls back to live Gamma authority when
#          world.executable_market_snapshots has no cache row for the market.
# Reuse: Run when modifying submit_redeem snapshot lookup, the Gamma fallback
#        helper, or _OPERATOR_REVIEW_ERRORCODES.
"""Antibody tests: live Gamma authority fallback for missing snapshot rows.

Root cause (2026-05-19 alpha-loss): in-flight Karachi-class redeem positions
were entered before the `capture_executable_market_snapshot` side-effect path
existed. `world.executable_market_snapshots` held 0 rows for those condition_ids,
so `submit_redeem` failed closed at `REDEEM_NEGRISK_FACT_MISSING` and latched
`REDEEM_OPERATOR_REQUIRED` indefinitely (errorCode not in autonomous-retry
allowlist).

Fix: when the snapshot table has no row, `submit_redeem` calls
`_fetch_neg_risk_from_gamma_for_submitter(condition_id)` which queries the
canonical public CLOB authority. If Gamma returns `neg_risk` + token IDs, the
submitter proceeds with the live authority data. If Gamma also fails, the
existing `REDEEM_NEGRISK_FACT_MISSING` fail-closed path is preserved.

Antibody contracts (sed-flip verifiable):
  G1: parser-level — Gamma JSON → {neg_risk, yes_token_id, no_token_id}.
  G2: submitter with empty snapshot + Gamma reachable → adapter.redeem called
      with neg_risk from Gamma (sed-flip: remove the fallback call → RED).
  G3: submitter with empty snapshot + Gamma unreachable → existing fail-closed
      REDEEM_NEGRISK_FACT_MISSING path preserved.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.state.db import init_schema


NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_CONDITION_ID = "0xkarachi" + "b" * 56


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def plain_trade_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


@pytest.fixture()
def empty_world_db_path():
    """Real-file world DB whose executable_market_snapshots table has 0 rows.
    Reproduces the Karachi failure mode (legacy positions entered before the
    snapshot side-effect path existed)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    world_conn = sqlite3.connect(path)
    world_conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          condition_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    world_conn.commit()
    world_conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeAdapter:
    def __init__(self):
        self.calls = []

    def redeem(self, condition_id, *, index_sets=None, neg_risk=False, amount_per_slot=None, **_kw):
        self.calls.append({
            "condition_id": condition_id,
            "index_sets": index_sets,
            "neg_risk": neg_risk,
            "amount_per_slot": amount_per_slot,
        })
        return {"success": True, "tx_hash": "0xdeadbeef" + "0" * 56}


def _insert_command(conn, condition_id: str) -> str:
    from src.execution.settlement_commands import request_redeem
    cmd_id = request_redeem(
        condition_id,
        "USDC",
        market_id=condition_id,
        token_amounts={"yes-token-gamma": "1.5"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.commit()
    return cmd_id


def _make_gamma_response(*, neg_risk: bool, yes_token: str, no_token: str):
    """Build a fake httpx Response matching Polymarket /markets/{id} shape."""
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "neg_risk": neg_risk,
                "tokens": [
                    {"outcome": "Yes", "token_id": yes_token},
                    {"outcome": "No", "token_id": no_token},
                ],
            }

    return _Resp()


# ---------------------------------------------------------------------------
# G1: parser-level test
# ---------------------------------------------------------------------------

def test_g1_fetch_neg_risk_from_gamma_extracts_tokens_and_neg_risk():
    """G1: Gamma payload → dict with neg_risk + yes_token_id + no_token_id."""
    from src.execution.settlement_commands import _fetch_neg_risk_from_gamma_for_submitter

    with patch("httpx.get", return_value=_make_gamma_response(
        neg_risk=True,
        yes_token="0xyes_token_id_abcd",
        no_token="0xno_token_id_efgh",
    )):
        result = _fetch_neg_risk_from_gamma_for_submitter(_CONDITION_ID)

    assert result is not None, (
        "G1 FAIL: helper returned None despite a well-formed Gamma response."
    )
    assert result["neg_risk"] is True
    assert result["yes_token_id"] == "0xyes_token_id_abcd"
    assert result["no_token_id"] == "0xno_token_id_efgh"


def test_g1b_fetch_neg_risk_from_gamma_returns_none_on_http_error():
    """G1b: any httpx exception → helper returns None (caller fails closed)."""
    import httpx
    from src.execution.settlement_commands import _fetch_neg_risk_from_gamma_for_submitter

    with patch("httpx.get", side_effect=httpx.ConnectError("transient network failure")):
        result = _fetch_neg_risk_from_gamma_for_submitter(_CONDITION_ID)

    assert result is None, (
        "G1b FAIL: helper returned %r on httpx error; must return None so "
        "caller falls back to REDEEM_NEGRISK_FACT_MISSING fail-closed."
        % (result,)
    )


# ---------------------------------------------------------------------------
# G2: submitter with empty snapshot + Gamma reachable → adapter called
# ---------------------------------------------------------------------------

def test_g2_submitter_falls_back_to_gamma_when_snapshot_missing(
    plain_trade_conn, empty_world_db_path, monkeypatch
):
    """G2: with an EMPTY snapshot table (Karachi failure mode), the submitter
    must consult live Gamma authority and proceed with the negRisk redeem
    instead of latching REDEEM_NEGRISK_FACT_MISSING.

    Sed-flip: remove the `_gamma_row = _fetch_neg_risk_from_gamma_for_submitter`
    call inside submit_redeem → adapter.calls stays empty, antibody RED.
    """
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod

    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(
            allow_redemption=True, block_reason=None, state="LIVE_ENABLED"
        ),
    )
    monkeypatch.setattr(
        "src.execution.settlement_commands.require_pusd_redemption_allowed",
        lambda fx: fx,
    )

    # Point the submitter's ATTACH path at our empty-snapshot world DB.
    monkeypatch.setattr(
        sc, "ZEUS_WORLD_DB_PATH", pathlib.Path(empty_world_db_path), raising=False
    )
    orig_world = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(empty_world_db_path)

    try:
        cmd_id = _insert_command(plain_trade_conn, _CONDITION_ID)
        adapter = _FakeAdapter()
        ledger = SimpleNamespace()

        # Gamma authority responds with neg_risk=True + token IDs.
        with patch("httpx.get", return_value=_make_gamma_response(
            neg_risk=True,
            yes_token="0xyes_gamma_token",
            no_token="0xno_gamma_token",
        )):
            sc.submit_redeem(
                cmd_id,
                adapter,
                ledger,
                conn=plain_trade_conn,
            )

        assert adapter.calls, (
            "G2 FAIL: adapter.redeem was never called. Snapshot was empty AND "
            "Gamma fallback was reachable — submitter should have used the "
            "live authority data and proceeded. Karachi failure mode is not closed."
        )
        assert adapter.calls[0]["neg_risk"] is True, (
            "G2 FAIL: adapter called but neg_risk=%r; expected True from Gamma payload."
            % (adapter.calls[0]["neg_risk"],)
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_world


# ---------------------------------------------------------------------------
# G3: submitter with empty snapshot + Gamma unreachable → fail-closed preserved
# ---------------------------------------------------------------------------

def test_g3_submitter_fails_closed_when_both_snapshot_and_gamma_unavailable(
    plain_trade_conn, empty_world_db_path, monkeypatch
):
    """G3: when BOTH snapshot AND Gamma are unavailable, the existing
    REDEEM_NEGRISK_FACT_MISSING fail-closed path must still fire — the Gamma
    fallback does NOT weaken the fail-closed posture, only relaxes the
    inputs that can satisfy it."""
    import httpx
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod

    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(
            allow_redemption=True, block_reason=None, state="LIVE_ENABLED"
        ),
    )
    monkeypatch.setattr(
        "src.execution.settlement_commands.require_pusd_redemption_allowed",
        lambda fx: fx,
    )

    monkeypatch.setattr(
        sc, "ZEUS_WORLD_DB_PATH", pathlib.Path(empty_world_db_path), raising=False
    )
    orig_world = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(empty_world_db_path)

    try:
        cmd_id = _insert_command(plain_trade_conn, _CONDITION_ID)
        adapter = _FakeAdapter()
        ledger = SimpleNamespace()

        with patch("httpx.get", side_effect=httpx.ConnectError("simulated transient outage")):
            result = sc.submit_redeem(
                cmd_id,
                adapter,
                ledger,
                conn=plain_trade_conn,
            )

        assert not adapter.calls, (
            "G3 FAIL: adapter.redeem was called despite both snapshot AND Gamma "
            "being unavailable. The fail-closed guarantee has been weakened — "
            "topology.yaml:4193 forbids guessing neg_risk."
        )
        # The result should reflect OPERATOR_REQUIRED with REDEEM_NEGRISK_FACT_MISSING.
        # Different return shapes are acceptable as long as it does not call adapter.
        row = plain_trade_conn.execute(
            "SELECT state, error_payload FROM settlement_commands WHERE command_id = ?",
            (cmd_id,),
        ).fetchone()
        assert row is not None
        assert "REDEEM_OPERATOR_REQUIRED" in str(row["state"]), (
            f"G3 FAIL: row.state={row['state']!r}, expected REDEEM_OPERATOR_REQUIRED."
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_world
