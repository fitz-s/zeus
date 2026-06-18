# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/archive/2026-Q2/task_2026-05-17_post_karachi_remediation/PR_I5_WEB3_WIRE.md §7 risk #4
#                  ("adapter constructor gap: PolymarketV2Adapter() at main.py:226 passes no args;
#                   signer_key required — this must be fixed first")
#                  + Wave-3 Batch A retry brief 2026-05-18 (PR-I.5.b adapter signer_key wire)
"""Antibody: redeem submitter cycle MUST resolve credentials before adapter ctor.

Three antibodies, mirroring `feedback_one_failed_test_is_not_a_diagnosis` (one
failure observation is not a diagnosis — exercise distinct branches):

  1. happy path (live mode + creds available) →
     `PolymarketV2Adapter` is constructed with non-empty `signer_key` AND
     `funder_address`, mirroring the entry-adapter wire in
     `polymarket_client._ensure_v2_adapter`.
  2. live mode + missing creds → cycle raises BEFORE `submit_redeem` is ever
     called and BEFORE the trade DB is touched.
  3. non-live mode → cycle returns cleanly without resolving credentials,
     without constructing the adapter, and without opening a DB connection.

Anti-drift hazard this defends against: silent parameter divergence between
the entry adapter and the redeem adapter. They MUST share the same keychain
path (`resolve_polymarket_credentials`) — if a future refactor splits them,
this test should fail loudly.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Updated 2026-06-12 (operator law 2026-06-10 — redeem submission FORBIDDEN):
# the redeem-submitter cycle is now an UNCONDITIONAL no-op. It returns BEFORE
# resolving credentials, constructing an adapter, or calling submit_redeem,
# because redeem_submission_allowed() is always False. Antibodies 1 and 2
# previously pinned the (now dead-by-law) credential-wiring / fail-closed
# submitter machinery; they now pin that the cycle never reaches it.
# ---------------------------------------------------------------------------
def test_redeem_submitter_adapter_has_credentials_in_live_mode(monkeypatch):
    """In live mode, redeem submission is forbidden before credential resolution."""
    from src.execution import post_trade_capital as main_mod  # P4: redeem cycle lifted here

    adapter_constructed = {"count": 0}
    creds_resolved = {"count": 0}

    class _FakeAdapter:  # pragma: no cover - must never be constructed
        def __init__(self, **kwargs):
            adapter_constructed["count"] += 1

    def _spy_resolve():  # pragma: no cover - must never be called
        creds_resolved["count"] += 1
        return {"private_key": "0xprivkey-test", "funder_address": "0xfunder-test"}

    monkeypatch.setattr(main_mod, "get_mode", lambda: "live")
    with patch(
        "src.data.polymarket_client.resolve_polymarket_credentials",
        side_effect=_spy_resolve,
    ), patch(
        "src.venue.polymarket_v2_adapter.PolymarketV2Adapter", _FakeAdapter
    ):
        main_mod._redeem_submitter_cycle()

    assert adapter_constructed["count"] == 0, (
        "PolymarketV2Adapter must not be constructed; redeem submission is forbidden"
    )
    assert creds_resolved["count"] == 0, (
        "the cycle must calm-skip before resolving credentials"
    )


# ---------------------------------------------------------------------------
# Antibody 2 — fail-closed: live mode + missing creds + work exists → raise
#              before adapter construction and before submit_redeem.
#
# Codex P2 fix (PR #145): creds resolution moved to after empty-row check.
# The prior assertion "DB must NOT be opened" was over-specified; a read-only
# SELECT is not a write side effect. The real invariant is: no adapter
# constructed and no submit_redeem called when creds missing AND work exists.
# ---------------------------------------------------------------------------
def test_redeem_submitter_fails_closed_when_creds_missing(monkeypatch):
    """Missing creds are irrelevant because redeem submission calm-skips first."""
    from src.execution import post_trade_capital as main_mod  # P4: redeem cycle lifted here

    adapter_constructed = {"count": 0}
    submit_called = {"count": 0}

    class _SpyAdapter:  # pragma: no cover
        def __init__(self, **kwargs):
            adapter_constructed["count"] += 1

    def _spy_submit(*args, **kwargs):  # pragma: no cover
        submit_called["count"] += 1

    monkeypatch.setattr(main_mod, "get_mode", lambda: "live")
    with patch(
        "src.data.polymarket_client.resolve_polymarket_credentials",
        side_effect=RuntimeError("Cannot resolve Polymarket credentials: keychain"),
    ), patch(
        "src.venue.polymarket_v2_adapter.PolymarketV2Adapter", _SpyAdapter
    ), patch(
        "src.execution.settlement_commands.submit_redeem", side_effect=_spy_submit
    ), patch(
        "src.data.dual_run_lock.acquire_lock",
    ) as mock_lock:
        mock_lock.return_value.__enter__.return_value = True
        mock_lock.return_value.__exit__.return_value = False

        result = main_mod._redeem_submitter_cycle()

    assert result is None
    assert adapter_constructed["count"] == 0, (
        "PolymarketV2Adapter must NOT be constructed (submission forbidden)"
    )
    assert submit_called["count"] == 0, (
        "submit_redeem must NOT be called (submission forbidden)"
    )


# ---------------------------------------------------------------------------
# Antibody 3 — non-live mode skips cleanly (no creds resolution, no DB).
# ---------------------------------------------------------------------------
def test_redeem_submitter_skips_in_non_live_mode(monkeypatch):
    """In non-live mode (paper/dry-run), the cycle returns cleanly without
    resolving credentials, constructing the adapter, or opening the DB.
    """
    from src.execution import post_trade_capital as main_mod  # P4: redeem cycle lifted here

    creds_calls = {"count": 0}
    db_calls = {"count": 0}

    def _spy_resolve():  # pragma: no cover
        creds_calls["count"] += 1
        raise AssertionError(
            "credentials must NOT be resolved in non-live mode"
        )

    def _spy_db(*args, **kwargs):  # pragma: no cover
        db_calls["count"] += 1
        raise AssertionError("trade DB must NOT be opened in non-live mode")

    monkeypatch.setattr(main_mod, "get_mode", lambda: "paper")
    with patch(
        "src.data.polymarket_client.resolve_polymarket_credentials",
        side_effect=_spy_resolve,
    ), patch(
        "src.state.db.get_trade_connection", side_effect=_spy_db
    ):
        # No raise; no DB; no creds resolution. Just a clean return.
        result = main_mod._redeem_submitter_cycle()

    assert result is None
    assert creds_calls["count"] == 0
    assert db_calls["count"] == 0


# ---------------------------------------------------------------------------
# Antibody 4 — live mode + missing creds + NO rows → clean return, no raise.
#
# Antibody for the Codex P2 fix (PR #145): credential resolution deferred
# until after the empty-row check. An idle daemon with no submittable rows
# must NOT mark the scheduler job FAILED even when Keychain is unavailable.
# ---------------------------------------------------------------------------
def test_redeem_submitter_idle_no_rows_no_raise_even_when_creds_missing(monkeypatch):
    """In live mode with no submittable rows, missing credentials must NOT
    cause a RuntimeError. The cycle returns cleanly after the empty-row check.
    """
    from src.execution import post_trade_capital as main_mod  # P4: redeem cycle lifted here

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = []  # no work

    monkeypatch.setattr(main_mod, "get_mode", lambda: "live")
    with patch(
        "src.data.polymarket_client.resolve_polymarket_credentials",
        side_effect=RuntimeError("Cannot resolve Polymarket credentials: keychain"),
    ), patch(
        "src.state.db.get_trade_connection", return_value=fake_conn
    ), patch(
        "src.data.dual_run_lock.acquire_lock",
    ) as mock_lock:
        mock_lock.return_value.__enter__.return_value = True
        mock_lock.return_value.__exit__.return_value = False

        # Must return cleanly — no raise.
        result = main_mod._redeem_submitter_cycle()

    assert result is None, (
        "Idle cycle with no rows must return None, not raise on missing creds"
    )
